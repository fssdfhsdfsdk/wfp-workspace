"""执行引擎：plan / apply / compensate / resume 与运行控制。

Implements: DR-01, DR-02, DR-09, DR-12, DR-14, DR-16, DR-18, DR-20, DR-21, DR-22,
DR-23, DR-24, DR-25, DR-26, DR-27, DR-28, DR-29, DR-30, DR-31, DR-32, DR-33, DR-34,
DR-35, DR-37, DR-38, DR-39, DR-49, DR-50, DR-51, DR-53, DR-61, DR-63, DR-64, DR-65,
DR-67, DR-68, DR-69, DR-71, DR-75, DR-77, DR-79, DR-82
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wfp.actions.registry import ActionRegistry
from wfp.contracts.pipeline import PipelineDoc, lint_errors, lint_pipeline, parse_ref, validate_inputs
from wfp.engine.cancel import Cancelled, CancelToken
from wfp.engine.expr import ExprError, Scope, eval_expr, render_params, render_value
from wfp.engine.idempotency import make_key, probe
from wfp.engine.journal import ArtifactWriter, EventWriter, sha256_bytes
from wfp.engine.types import ActionContext, ApplyResult, PlanItem, PlanResult, StepOutcome
from wfp.errors import ConfirmRequired, Conflict, ContractError, NodeUnset, NotFound, WfpError
from wfp.paths import DataPaths
from wfp.runtimes.base import adapter_for
from wfp.security.credentials import CredentialService, SecretResolver
from wfp.security.redactor import Redactor
from wfp.store.db import Store, utcnow
from wfp.store.nodes import NodeStore
from wfp.store.pipelines import PipelineStore
from wfp.store.runs import RunStore
from wfp.transport.fake import FakeTransport

MAX_LOOP = 1000


@dataclass
class RunCtx:
    run_id: str
    doc: PipelineDoc
    inputs: dict[str, Any]
    node_snapshot: dict[str, Any] | None
    events: EventWriter
    artifacts: ArtifactWriter
    redactor: Redactor
    cancel: CancelToken
    transport: Any | None
    scope: Scope
    pause: threading.Event
    lock_name: str | None = None
    warnings: list[str] = field(default_factory=list)
    manual: list[dict[str, Any]] = field(default_factory=list)
    status: str = "running"
    error: str | None = None
    started: float = field(default_factory=time.monotonic)


class Runner:
    """单进程内 runner；API 进程通过后台线程 spawn 它（DR-71 故障隔离由调用方负责）。"""

    def __init__(self, paths: DataPaths, store: Store, *, actor: str = "cli",
                 transport: Any | None = None, transport_factory: Any | None = None,
                 nodes: NodeStore | None = None, credentials: CredentialService | None = None,
                 config: Any | None = None) -> None:
        self.paths = paths.ensure()
        self.store = store
        self.actor = actor
        self.config = config                       # 启动配置（DR-67/69/77）
        self.log_quota = int(getattr(config, "log_quota_mb", 200)) * 1024 ** 2
        self.default_timeout = float(getattr(config, "default_timeout_seconds", 300))
        self.actions = ActionRegistry(store, paths)
        self.pipelines = PipelineStore(store, paths)
        self.runs = RunStore(store, paths)
        self.credentials = credentials or CredentialService(store, paths)
        self.nodes = nodes or NodeStore(store, paths, self.credentials)
        self._transport = transport
        self._transport_factory = transport_factory
        self._active: dict[str, RunCtx] = {}
        from wfp.actions.manifests import seed_builtin_actions
        seed_builtin_actions(self.actions, actor="system")

    # ---------- 基础设施 ----------
    def _action_exists(self, action_id: str, version: str) -> bool:
        return bool(self.store.read(lambda c: c.execute(
            "SELECT 1 FROM action_version WHERE action_id=? AND version=?",
            (action_id, version)).fetchone()))

    def _lint(self, doc: PipelineDoc, *, strict: bool = True) -> list[dict[str, Any]]:
        issues = lint_pipeline(
            doc, action_exists=self._action_exists, current_node=self.nodes.current_id,
            resolve_pipeline=lambda pid, ver: self.pipelines.get(pid, version=ver))
        errs = lint_errors(issues)
        if errs and strict:
            raise ContractError(f"lint 发现 {len(errs)} 个错误：{errs[0]['code']} {errs[0]['message']}",
                                path=errs[0]["path"], details=errs)
        return [i.as_dict() for i in issues]

    def _transport_for(self, snapshot: dict[str, Any], redactor: Redactor) -> Any | None:
        if self._transport is not None:
            return self._transport
        fake = os.environ.get("WFP_FAKE_REMOTE")
        if fake:
            root = Path(fake) / snapshot.get("host", "node").replace(":", "_")
            return FakeTransport(root)
        if self._transport_factory:
            return self._transport_factory(snapshot)
        if snapshot.get("os_kind", "ssh-linux").startswith("ssh"):
            from wfp.transport.ssh import ssh_from_node
            resolver = SecretResolver(self.credentials, redactor)
            return ssh_from_node(snapshot, resolver)
        from wfp.transport.winrm import WinRMTransport
        resolver = SecretResolver(self.credentials, redactor)
        return WinRMTransport(snapshot, password=resolver.get(
            snapshot.get("credential_ref") or "").reveal() if snapshot.get("credential_ref") else None)

    def _node_snapshot(self, doc: PipelineDoc) -> dict[str, Any] | None:
        remote_used = any(str(s.get("uses", "")).startswith("remote.") or
                          str(s.get("node", "")) for s in doc.steps)
        node_ref = doc.defaults.get("node")
        if not node_ref:
            node_ref = next((s.get("node") for s in doc.steps if s.get("node")), None)
        if not node_ref and not remote_used:
            return None
        try:
            return self.nodes.snapshot(node_ref)
        except WfpError as exc:
            raise NodeUnset(str(exc)) from exc

    def _scope(self, doc: PipelineDoc, inputs: dict[str, Any], node: dict[str, Any] | None,
               *, placeholders: bool = False) -> Scope:
        base = Scope(inputs=inputs, variables={}, node=node, outputs={})
        variables = render_value(doc.vars, base) if doc.vars else {}
        base.layers[3]["vars"] = variables
        if placeholders:
            for st in doc.steps:
                if st.get("register"):
                    base.register(str(st["register"]), {"new_sha256": "<plan>", "sha256": "<plan>",
                                                        "dest": "<plan>"})
        return base

    def _ctx(self, run: RunCtx, step: dict[str, Any], params: dict[str, Any],
             manifest: dict[str, Any], action_id: str, version: str,
             iteration: int, *, phase: str = "apply", step_run_id: str | None = None) -> ActionContext:
        timeout = float(step.get("timeout_seconds") or
                        doc_default(run.doc, "timeout_seconds", self.default_timeout))

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            run.events.emit(event_type, payload, step=step["id"])

        return ActionContext(
            params=params, workspace=run.doc.workspace, run_id=run.run_id,
            step_id=step_run_id or f"{run.run_id}-{step['id']}-{iteration}",
            iteration=iteration,
            emit=emit,
            artifact=run.artifacts, cancel=run.cancel, deadline=time.monotonic() + timeout,
            transport=run.transport, secrets=SecretResolver(self.credentials, run.redactor),
            node=run.node_snapshot, redactor=run.redactor, manifest=manifest,
            action_id=action_id, action_version=version, registry=self.actions,
            allow_outside=run.doc.allow_outside, timeout=timeout,
        )

    @staticmethod
    def _iter_steps(doc: PipelineDoc, scope: Scope) -> Iterator[tuple[dict[str, Any], Scope, int, str | None]]:
        """DR-20/21：顺序即列表顺序；when 跳过；for_each/while 有界循环。"""
        for step in doc.steps:
            when = step.get("when")
            if when:
                try:
                    if not eval_expr(str(when), scope):
                        yield step, scope, 0, "skipped_condition"
                        continue
                except ExprError as exc:
                    raise ContractError(f"条件表达式非法：{exc}", path=f"steps[{step['id']}].when") from exc
            loop = step.get("loop")
            if not loop:
                yield step, scope, 0, None
                continue
            limit = min(int(loop.get("max_iterations", MAX_LOOP)), MAX_LOOP)
            if "for_each" in loop:
                spec = loop["for_each"]
                items = eval_expr(spec, scope) if isinstance(spec, str) else spec
                if not isinstance(items, (list, tuple)):
                    raise ContractError("for_each 必须求值为列表", path=f"steps[{step['id']}].loop.for_each")
                if len(items) > limit:
                    raise ContractError(f"循环超过上限 {limit}", path=f"steps[{step['id']}].loop")
                for idx, item in enumerate(items):
                    yield step, scope.push(item=item, index=idx), idx, None
            else:
                cond = str(loop["while"])
                for idx in range(limit):
                    child = scope.push(item=None, index=idx)
                    if not eval_expr(cond, child):
                        break
                    yield step, child, idx, None
                else:
                    raise ContractError(f"while 达到 max_iterations={limit} 仍未结束",
                                        path=f"steps[{step['id']}].loop.while")

    def _new_run_ctx(self, run_id: str, doc: PipelineDoc, inputs: dict[str, Any],
                     node: dict[str, Any] | None, *, placeholders: bool = False) -> RunCtx:
        redactor = Redactor()
        events = EventWriter(self.paths, run_id, redactor, quota=self.log_quota)
        artifacts = ArtifactWriter(self.store, self.paths, run_id, redactor, events)
        transport = self._transport_for(node, redactor) if node else None
        return RunCtx(run_id=run_id, doc=doc, inputs=inputs, node_snapshot=node,
                      events=events, artifacts=artifacts, redactor=redactor,
                      cancel=CancelToken(), transport=transport,
                      scope=self._scope(doc, inputs, node, placeholders=placeholders),
                      pause=threading.Event())

    def _finish_ctx(self, run: RunCtx) -> None:
        run.events.close()
        # 注入的共享 transport 由调用方持有（DR-75），不得在 plan/apply 结束时关闭
        if run.transport is not None and run.transport is not self._transport:
            try:
                run.transport.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------- plan（DR-30/31） ----------
    def plan(self, ref: str, inputs: dict[str, Any] | None = None, *,
             trigger: str = "cli", strict_lint: bool = True) -> dict[str, Any]:
        doc = self.pipelines.get(ref)
        merged = validate_inputs(doc.doc, inputs or {})
        issues = self._lint(doc, strict=strict_lint)
        node = self._node_snapshot(doc)
        run_id = f"r-{uuid.uuid4().hex[:12]}"
        self.runs.create(run_id, pipeline_id=doc.pipeline_id or Path(ref).stem,
                         version=doc.version, phase="plan", trigger=trigger,
                         inputs=merged, node_snapshot=node,
                         action_versions={d["action_id"]: d["version"] for d in
                                          _deps(doc)})
        self.runs.set_status(run_id, "planning")
        run = self._new_run_ctx(run_id, doc, merged, node, placeholders=True)
        self._active[run_id] = run
        items: list[dict[str, Any]] = []
        try:
            run.events.emit("run.started", {"phase": "plan", "pipeline": doc.pipeline_id,
                                            "version": doc.version, "workdir": str(doc.workspace),
                                            "inputs": run.redactor.scrub(merged),
                                            "inputs_sha256": sha256_bytes(
                json.dumps(merged, ensure_ascii=False, sort_keys=True, default=str).encode())})
            for step, scope, iteration, skip in self._iter_steps(doc, run.scope):
                self.runs.heartbeat(run_id)
                if skip:
                    item = PlanItem(step["id"], step["uses"], "skipped_condition")
                    items.append(item.__dict__)
                    run.events.emit("step.skipped", {"reason": skip}, step=step["id"])
                    continue
                kind, aid, ver = parse_ref(str(step["uses"]))
                if kind == "pipeline":  # DR-22：子工作项在加载期展开为步骤树
                    items.append(PlanItem(step["id"], step["uses"], "subpipeline",
                                          targets=[f"pipeline:{aid}@{ver}"]).__dict__)
                    continue
                manifest = self.actions.get_version(aid, ver)["manifest"]
                adapter = adapter_for(manifest, self.actions)
                resolver = SecretResolver(self.credentials, run.redactor)
                params = render_params(step.get("with") or {}, manifest, scope,
                                       secret_fn=resolver.get)
                from wfp.engine.expr import validate_params
                validate_params(params, manifest, path=f"steps[{step['id']}].with")
                ctx = self._ctx(run, step, params, manifest, aid, ver, iteration, phase="plan")
                result = adapter.plan(ctx)
                caps = manifest["capabilities"]
                item = PlanItem(
                    step_id=step["id"], action_ref=step["uses"], status="planned",
                    targets=result.targets, diff=result.diff, risks=result.risks,
                    unpredictable=result.unpredictable or caps["dry_run"] == "none",
                    needs_admin=bool(caps["needs_admin"]), needs_network=bool(caps["needs_network"]),
                    compensate=caps["compensate"],
                    backups=[str(b.get("path")) for b in result.backup_targets],
                    no_backup_reason=result.no_backup_reason or (
                        None if result.backup_targets else "目标对象不存在或由前序步骤创建（DR-33）"),
                )
                items.append(item.__dict__)
                run.events.emit("step.finished", {"status": "planned", "targets": result.targets,
                                                  "diff": result.diff[:4000]}, step=step["id"])
            risks = [r for i in items for r in i.get("risks", [])]
            confirm_required = any(i.get("unpredictable") or i.get("compensate") in ("none", "manual")
                                   for i in items)
            report: dict[str, Any] = {
                "v": 1, "run_id": run_id, "phase": "plan", "pipeline": doc.pipeline_id,
                "pipeline_version": doc.version, "inputs": merged,
                "node_snapshot": node, "items": items, "risks": risks,
                "confirm_required": confirm_required, "lint": issues,
                "generated_at": utcnow(),
            }
            report["sha256"] = sha256_bytes(json.dumps(report, ensure_ascii=False,
                                                       sort_keys=True, default=str).encode())
            info = run.artifacts.put("report", "plan-report.json",
                                     json.dumps(report, ensure_ascii=False, indent=2, default=str))
            run.artifacts.put("report", "plan-report.md", self._plan_markdown(report))
            self.runs.set_status(run_id, "planned", plan_report_path=info["path"], finished=True)
            run.events.emit("plan.report", {"path": info["path"], "sha256": report["sha256"]})
            run.events.emit("run.finished", {"status": "planned", "steps": len(items)})
            self.store.audit(self.actor, "run.plan", run_id, {"pipeline": doc.pipeline_id})
            return report
        except Exception as exc:  # noqa: BLE001
            self.runs.set_status(run_id, "plan_failed", error_code=getattr(exc, "code", "WFP-E-PLAN"),
                                 finished=True)
            run.events.emit("run.finished", {"status": "plan_failed", "error": str(exc)})
            raise
        finally:
            self._active.pop(run_id, None)
            self._finish_ctx(run)

    @staticmethod
    def _plan_markdown(report: dict[str, Any]) -> str:
        lines = [f"# WFP PlanReport `{report['run_id']}`", "",
                 f"- 工作项：`{report['pipeline']}@{report['pipeline_version']}`",
                 f"- 确认闸：{'需要' if report['confirm_required'] else '不需要'}",
                 f"- 报告哈希：`{report['sha256']}`", "",
                 "| # | 步骤 | 动作 | 状态 | 目标 | 风险 |", "| --- | --- | --- | --- | --- | --- |"]
        for i, it in enumerate(report["items"], 1):
            lines.append(f"| {i} | {it['step_id']} | {it['action_ref']} | {it['status']} | "
                         f"{', '.join(it.get('targets', []))[:120]} | "
                         f"{'; '.join(it.get('risks', []))[:120]} |")
        if report["risks"]:
            lines += ["", "## 风险", *[f"- {r}" for r in report["risks"]]]
        return "\n".join(lines) + "\n"

    # ---------- apply（DR-33/49/26） ----------
    def apply(self, ref: str, inputs: dict[str, Any] | None = None, *,
              confirm: dict[str, Any] | None = None, yes: bool = False,
              trigger: str = "cli", resume_run_id: str | None = None,
              confirm_step: str | None = None, only_step: str | None = None,
              plan_report: dict[str, Any] | None = None) -> dict[str, Any]:
        doc = self.pipelines.get(ref)
        merged = validate_inputs(doc.doc, inputs or {})
        self._lint(doc)
        # DR-49：先 plan，再确认闸（UI 可传入已展示过的 PlanReport）
        plan_report = plan_report or self.plan(ref, merged, trigger=trigger)
        if yes:
            confirm = {"plan_sha": plan_report["sha256"], "ack_risks": True,
                       "actor": self.actor, "auto": True, "at": utcnow()}
        elif confirm is None:
            raise ConfirmRequired(details={"plan_report": plan_report})
        elif confirm.get("plan_sha") != plan_report["sha256"]:
            raise ConfirmRequired("PlanReport 已变化，请重新干跑并确认",
                                  details={"expected": plan_report["sha256"]})
        elif plan_report["confirm_required"] and not confirm.get("ack_risks"):
            raise ConfirmRequired("存在不可补偿/不可预测步骤，需显式勾选确认（DR-49）")

        run_id = resume_run_id or f"r-{uuid.uuid4().hex[:12]}"
        if not self.runs.acquire_lock("apply", run_id):
            raise Conflict(f"已有 apply 正在运行：{self.runs.lock_holder('apply')}（DR-02）")
        existing = None
        run: RunCtx | None = None
        try:
            if resume_run_id:
                existing = self.runs.get(resume_run_id)
                self.runs.set_status(run_id, "running", confirm=confirm)
            else:
                node = self._node_snapshot(doc)
                self.runs.create(run_id, pipeline_id=doc.pipeline_id or Path(ref).stem,
                                 version=doc.version, phase="apply", trigger=trigger,
                                 inputs=merged, node_snapshot=node,
                                 action_versions={d["action_id"]: d["version"] for d in _deps(doc)})
                self.runs.set_status(run_id, "running", confirm=confirm)
            run = self._new_run_ctx(run_id, doc, merged,
                                    existing["node_snapshot"] if existing else node)
            run.lock_name = "apply"
            self._active[run_id] = run
            self.store.audit(self.actor, "run.apply.confirm", run_id,
                             {"plan_sha": plan_report["sha256"], "auto": bool(confirm.get("auto"))})
        except BaseException:
            self.runs.release_lock("apply", run_id)
            if run is not None:
                self._finish_ctx(run)
            raise
        assert run is not None

        # DR-37 resume：重建作用域与已完成步骤
        skip: set[tuple[str, int]] = set()
        if existing:
            for sr in self.runs.steps(run_id):
                if sr["status"] == "succeeded" and not (confirm_step and sr["step_id"] == confirm_step):
                    skip.add((sr["step_id"], sr["iteration"]))
                    try:
                        run.scope.register(sr["step_id"], json.loads(sr["outputs_json"] or "{}"))
                    except json.JSONDecodeError:
                        pass
        run.events.emit("run.started", {"phase": "apply", "resume": bool(existing),
                                        "pipeline": doc.pipeline_id, "version": doc.version,
                                        "workdir": str(doc.workspace),
                                        "inputs": run.redactor.scrub(merged),
                                        "plan_sha": plan_report["sha256"]})
        try:
            self._execute(run, doc, plan_report, skip=skip, only_step=only_step)
        finally:
            self.runs.release_lock("apply", run_id)
            self._active.pop(run_id, None)
            self._finish_ctx(run)
        return self.run_status(run_id)

    def _execute(self, run: RunCtx, doc: PipelineDoc, plan_report: dict[str, Any], *,
                 skip: set[tuple[str, int]] | None = None, only_step: str | None = None) -> None:
        skip = skip or set()
        abort = False
        for step, scope, iteration, condition_skip in self._iter_steps(doc, run.scope):
            if only_step and step["id"] != only_step:
                continue
            if run.cancel.cancelled:
                run.status, run.error = "cancelled", "WFP-E-CANCELLED"
                break
            self.runs.heartbeat(run.run_id)
            if run.pause.is_set():
                self.runs.set_status(run.run_id, "paused")
                run.pause.wait()
                self.runs.set_status(run.run_id, "running")
            sid = step["id"]
            step_run_id = f"{run.run_id}-{sid}-{iteration}"
            if (sid, iteration) in skip:
                run.events.emit("step.skipped", {"reason": "resume_replay", "step": sid}, step=sid)
                continue
            if condition_skip:
                self.runs.add_step(step_run_id, run_id=run.run_id, step_key=sid, iteration=iteration,
                                   attempt=1, action_id="-", action_version="-",
                                   status="skipped_condition", skip_reason=condition_skip)
                run.events.emit("step.skipped", {"reason": condition_skip}, step=sid)
                continue
            kind, aid, ver = parse_ref(str(step["uses"]))
            if kind == "pipeline":  # DR-22：加载期展开子工作项
                child = self.pipelines.get(aid, version=ver)
                self._execute(run, child, plan_report, skip=skip)
                continue
            try:
                manifest = self.actions.get_version(aid, ver)["manifest"]
                adapter = adapter_for(manifest, self.actions)
                resolver = SecretResolver(self.credentials, run.redactor)
                params = render_params(step.get("with") or {}, manifest, scope,
                                       secret_fn=resolver.get)
                from wfp.engine.expr import validate_params
                validate_params(params, manifest, path=f"steps[{sid}].with")
                ctx = self._ctx(run, step, params, manifest, aid, ver, iteration,
                                phase="apply", step_run_id=step_run_id)
                planned = adapter.plan(ctx)
                caps = manifest["capabilities"]
                t0 = time.monotonic()
                entry = manifest.get("entry") or {}
                command = str(params.get("command") or "")
                if not command and params.get("args"):
                    command = "python <impl> " + " ".join(str(a) for a in params["args"])
                if not command:
                    command = str(entry.get("command") or entry.get("module")
                                  or entry.get("operation") or entry.get("script") or "")
                # 结构化执行上下文：命令 / 入口 / 参数 / 目标 / 工作目录，便于直观定位
                run.events.emit("step.started", {
                    "action": f"{aid}@{ver}", "runtime": manifest.get("runtime"),
                    "entry": str(entry.get("module") or entry.get("operation")
                                 or entry.get("script") or ""),
                    "command": command, "params": run.redactor.scrub(params),
                    "targets": planned.targets, "workdir": str(run.doc.workspace),
                }, step=sid)
                key = make_key(f"{aid}@{ver}", params, planned.desired_state)
                if caps["idempotent"] in ("yes", "conditional") and probe(ctx, planned.desired_state):
                    self.runs.add_step(step_run_id, run_id=run.run_id, step_key=sid,
                                       iteration=iteration, attempt=1, action_id=aid,
                                       action_version=ver, status="skipped_idempotent",
                                       skip_reason="desired_state_satisfied", idempotency_key=key)
                    self.runs.journal(run.run_id, "skipped_idempotent",
                                      {"step": sid, "key": key}, step_run_id)
                    run.events.emit("step.skipped", {
                        "reason": "idempotent", "key": key,
                        "note": "期望态已满足，本次未执行任何写操作（DR-32）"}, step=sid)
                    continue

                # DR-33：apply 前置备份
                receipts = self._backup(ctx, planned, caps)
                self.runs.journal(run.run_id, "pre_apply", {
                    "step": sid, "action": f"{aid}@{ver}", "params": run.redactor.scrub(params),
                    "params_sha256": sha256_bytes(json.dumps(params, ensure_ascii=False,
                                                             sort_keys=True, default=str).encode()),
                    "capabilities": caps, "idempotency_key": key, "receipts": receipts,
                }, step_run_id)
                outcome = self._run_with_retry(run, ctx, adapter, step, step_run_id, aid, ver, key)
                if outcome.status != "succeeded":
                    policy = step.get("on_error") or doc.defaults.get("on_error") or "fail"
                    run.events.emit("step.finished", {"status": "failed", "error": outcome.error}, step=sid)
                    self.runs.add_step(step_run_id, run_id=run.run_id, step_key=sid,
                                       iteration=iteration, attempt=outcome.attempt, action_id=aid,
                                       action_version=ver, status="failed", idempotency_key=key)
                    self.runs.journal(run.run_id, "post_apply",
                                      {"step": sid, "status": "failed", "error": outcome.error}, step_run_id)
                    if policy == "continue":
                        run.warnings.append(f"{sid}: {outcome.error}")
                        continue
                    if policy == "compensate":
                        self._compensate(run)
                        run.status = "compensated" if run.status != "needs_attention" else run.status
                        abort = True
                        break
                    run.status, run.error, abort = "failed", outcome.error, True
                    break
                self.runs.journal(run.run_id, "post_apply", {
                    "step": sid, "status": "succeeded", "outputs": run.redactor.scrub(outcome.outputs),
                    "receipts": receipts,
                }, step_run_id)
                self.runs.add_step(step_run_id, run_id=run.run_id, step_key=sid, iteration=iteration,
                                   attempt=outcome.attempt, action_id=aid, action_version=ver,
                                   status="succeeded", idempotency_key=key,
                                   resolved_sha=sha256_bytes(json.dumps(params, ensure_ascii=False,
                                                                       sort_keys=True, default=str).encode()),
                                   outputs=outcome.outputs, exit_code=0)
                if step.get("register"):
                    scope.register(str(step["register"]), outcome.outputs)
                run.events.emit("step.finished", {
                    "status": "succeeded", "action": f"{aid}@{ver}",
                    "outputs": run.redactor.scrub(outcome.outputs),
                    "result": run.redactor.scrub(outcome.outputs),
                    "receipts": receipts, "attempt": outcome.attempt,
                    "duration_ms": round((time.monotonic() - t0) * 1000)}, step=sid)
            except Cancelled:
                run.status, run.error, abort = "cancelled", "WFP-E-CANCELLED", True
                break
            except WfpError as exc:
                run.status, run.error, abort = "failed", f"{exc.code}: {exc.message}", True
                run.events.emit("step.finished", {"status": "failed", "error": run.error}, step=sid)
                break
            except Exception as exc:  # noqa: BLE001 - 兜底并保证状态落库
                run.status, run.error, abort = "failed", f"WFP-E-ACTION: {exc}", True
                run.events.emit("step.finished", {"status": "failed", "error": run.error}, step=sid)
                break

        if run.status == "running":
            run.status = "succeeded_with_warnings" if run.warnings else "succeeded"
        if run.status == "failed" and not abort:
            run.status = "failed"
        self.runs.set_status(run.run_id, run.status, error_code=run.error, finished=True)
        summary = {"status": run.status, "steps": len(self.runs.steps(run.run_id)),
                   "warnings": run.warnings, "duration_ms": round((time.monotonic() - run.started) * 1000),
                   "manual": run.manual}
        run.artifacts.put("report", "run-summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        run.events.emit("run.finished", summary)
        self.store.audit(self.actor, "run.finished", run.run_id, summary)

    def _backup(self, ctx: ActionContext, planned: PlanResult, caps: dict[str, Any]) -> list[dict[str, Any]]:
        """DR-33/INV-03：有副作用步骤必须备份或声明 no_backup_reason。"""
        receipts: list[dict[str, Any]] = []
        if caps["side_effects"] == "none":
            return receipts
        for target in planned.backup_targets:
            if target.get("kind") == "local":
                receipt = ctx.artifact.backup_path(Path(target["path"]), step_id=ctx.step_id)
                if receipt:
                    receipts.append(receipt)
            elif target.get("kind") == "remote" and ctx.transport:
                try:
                    raw = ctx.transport.get(target["path"])
                    receipt = ctx.artifact.backup_bytes(Path(target["path"]).name, raw,
                                                        target=target["path"], step_id=ctx.step_id)
                    receipt.update({"kind": "remote", "remote": target["path"],
                                    "backup": dict(receipt)})
                    receipts.append(receipt)
                except Exception:  # noqa: BLE001 - 远端尚不存在时由 plan 风险清单说明
                    continue
        if not receipts and not planned.no_backup_reason:
            planned.no_backup_reason = "目标对象不存在或由前序步骤创建（DR-33）"
        for r in receipts:
            r["step_id"] = ctx.step_id
            r["capabilities"] = caps
            r["params"] = ctx.redactor.scrub(ctx.params)
        return receipts

    def _run_with_retry(self, run: RunCtx, ctx: ActionContext, adapter: Any,
                        step: dict[str, Any], step_run_id: str, aid: str, ver: str,
                        key: str) -> StepOutcome:
        """DR-26：重试产生独立 attempt；超时/取消由适配器与 CancelToken 保证。"""
        retry = step.get("retry") or doc_default(run.doc, "retry", {}) or {}
        attempts = 1 + int(retry.get("attempts", 0))
        delay = float(retry.get("delay_seconds", 0))
        backoff = str(retry.get("backoff", "fixed"))
        last_error = ""
        for attempt in range(1, attempts + 1):
            run.cancel.check()
            run.events.emit("step.started", {"attempt": attempt, "action": f"{aid}@{ver}"},
                            step=step["id"], attempt=attempt)
            ctx.deadline = time.monotonic() + ctx.timeout
            try:
                result: ApplyResult = adapter.apply(ctx)
                return StepOutcome("succeeded", result.outputs, attempt=attempt)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = f"{getattr(exc, 'code', 'WFP-E-ACTION')}: {exc}"
                run.events.emit("step.retry" if attempt < attempts else "step.timeout",
                                {"attempt": attempt, "error": run.redactor.scrub_text(last_error)},
                                step=step["id"], attempt=attempt)
                if attempt < attempts and delay:
                    run.cancel.wait(delay * (2 ** (attempt - 1) if backoff == "exponential" else 1))
        return StepOutcome("failed", error=last_error, attempt=attempts)

    # ---------- compensate（DR-34/79） ----------
    def compensate(self, run_id: str) -> dict[str, Any]:
        record = self.runs.get(run_id)
        doc = self.pipelines.get(record["pipeline_id"], version=record["pipeline_version"])
        run = self._new_run_ctx(run_id, doc, record["inputs"], record["node_snapshot"])
        self._active[run_id] = run
        run.events.emit("run.started", {"phase": "compensate"})
        try:
            manual = self._compensate(run)
            self.runs.set_status(run_id, run.status, error_code=run.error, finished=True)
            run.events.emit("run.finished", {"status": run.status, "manual": manual})
            self.store.audit(self.actor, "run.compensate", run_id, {"status": run.status})
            return {"run_id": run_id, "status": run.status, "manual": manual}
        finally:
            self._active.pop(run_id, None)
            self._finish_ctx(run)

    def _compensate(self, run: RunCtx) -> list[dict[str, Any]]:
        """逆序读取 journal 的 pre_apply，逐个还原并校验哈希（DR-34/79）。"""
        entries = self.runs.journal_for(run.run_id, event="pre_apply")
        for entry in reversed(entries):
            payload = entry["payload"]
            caps = payload.get("capabilities") or {}
            step_id = payload.get("step") or "?"
            if caps.get("compensate") in ("manual", "none"):
                run.manual.append({"step": step_id, "target": payload.get("action"),
                                   "reason": "不可逆步骤需人工处理（DR-34）"})
                continue
            for receipt in payload.get("receipts") or []:
                ok = False
                if receipt.get("kind") == "remote":
                    ctx = self._ctx(run, {"id": step_id, "uses": payload.get("action", "remote.exec@1.0.0")},
                                    receipt.get("params") or {}, {}, "remote.exec", "1.0.0", 0,
                                    step_run_id=entry["step_run_id"])
                    from wfp.runtimes.remote import dispatch_compensate
                    ok = dispatch_compensate(ctx, receipt)
                else:
                    try:
                        aid = str(payload.get("action", "")).split("@")[0]
                        manifest = self.actions.get_version(aid, str(payload.get("action", "@1.0.0")).split("@")[-1])["manifest"]
                        ctx = self._ctx(run, {"id": step_id, "uses": payload["action"]},
                                        receipt.get("params") or {}, manifest, aid, manifest["version"], 0,
                                        step_run_id=entry["step_run_id"])
                        ok = adapter_for(manifest, self.actions).compensate(ctx, receipt)
                    except (NotFound, KeyError, IndexError):
                        ok = False
                    if not ok and receipt.get("artifact"):
                        ok = run.artifacts.restore(receipt)
                if ok:
                    run.events.emit("backup.restored", {"target": receipt.get("target"),
                                                        "step": step_id}, step=step_id)
                else:
                    run.status = "needs_attention"
                    run.manual.append({"step": step_id, "target": receipt.get("target"),
                                       "reason": "补偿失败，保留全部证据（DR-34）"})
        if run.manual:
            run.events.emit("attention.required", {"items": run.manual})
        if run.status != "needs_attention":
            run.status = "compensated"
        return run.manual

    # ---------- 运行控制（DR-37/64） ----------
    def resume(self, run_id: str, *, confirm_step: str | None = None,
               yes: bool = True) -> dict[str, Any]:
        self.runs.scan_interrupted()
        rec = self.runs.get(run_id)
        if rec["status"] not in {"interrupted", "failed", "cancelled", "paused"}:
            raise Conflict(f"run {rec['status']} 不允许续跑（DR-37）")
        ref = self._pinned_ref(rec)
        return self.apply(ref, rec["inputs"], yes=yes, resume_run_id=run_id,
                          confirm_step=confirm_step, trigger="cli")

    def _pinned_ref(self, rec: dict[str, Any]) -> str:
        """优先用 run 固化的 pipeline_version 真源文件，避免库升级后行为漂移（INV-02/DR-65）。"""
        try:
            return str(self.pipelines.yaml_file(rec["pipeline_id"],
                                                version=rec["pipeline_version"]))
        except NotFound:
            return str(rec["pipeline_id"])

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self._active.get(run_id)
        if run:
            run.cancel.cancel()
            run.events.emit("step.cancel", {"step": "current"})
        self.runs.set_status(run_id, "cancelling")
        self.store.audit(self.actor, "run.cancel", run_id)
        return {"run_id": run_id, "status": "cancelling"}

    def pause(self, run_id: str) -> dict[str, Any]:
        run = self._active.get(run_id)
        if run:
            run.pause.set()
        self.runs.set_status(run_id, "paused")
        self.store.audit(self.actor, "run.pause", run_id)
        return {"run_id": run_id, "status": "paused"}

    def continue_run(self, run_id: str) -> dict[str, Any]:
        run = self._active.get(run_id)
        if run:
            run.pause.clear()
        self.runs.set_status(run_id, "running")
        self.store.audit(self.actor, "run.resume", run_id)
        return {"run_id": run_id, "status": "running"}

    def retry_step(self, run_id: str, step_id: str) -> dict[str, Any]:
        rec = self.runs.get(run_id)
        return self.apply(self._pinned_ref(rec), rec["inputs"], yes=True, resume_run_id=run_id,
                          confirm_step=step_id, only_step=step_id, trigger="cli")

    def run_status(self, run_id: str) -> dict[str, Any]:
        rec = self.runs.get(run_id)
        rec["steps"] = self.runs.steps(run_id)
        rec["events"] = len(EventWriter.tail(self.paths.logs / run_id / "events.ndjson"))
        rec["artifacts"] = self.runs.artifacts(run_id)
        return rec


def doc_default(doc: PipelineDoc, key: str, fallback: Any) -> Any:
    return doc.defaults.get(key, fallback)


def _deps(doc: PipelineDoc) -> list[dict[str, str]]:
    from wfp.contracts.pipeline import dependencies
    return dependencies(doc.doc)


__all__ = ["Runner"]
