"""本地 Web 平台：FastAPI REST + SSE + 静态 SPA（回环 + token + Origin 校验）。

Implements: DR-01, DR-02, DR-03, DR-04, DR-05, DR-07, DR-09, DR-13, DR-28, DR-40,
DR-41, DR-42, DR-43, DR-45, DR-46, DR-49, DR-50, DR-51, DR-52, DR-60, DR-61,
DR-63, DR-64, DR-65, DR-66, DR-67, DR-68, DR-72, DR-77, DR-80, DR-82, DR-83
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from wfp import __version__
from wfp.actions.registry import ActionRegistry
from wfp.actions.suite import run_contract_suite
from wfp.ai import AIDraftService
from wfp.config import Config
from wfp.contracts.pipeline import lint_pipeline
from wfp.contracts.yamlio import safe_dump
from wfp.doctor import run_doctor
from wfp.engine.journal import EventWriter
from wfp.engine.phases import Runner
from wfp.errors import Conflict, ContractError, NotFound, WfpError
from wfp.maintenance import diagnostics, export_all, import_all, run_evidence_zip, verify
from wfp.paths import DataPaths
from wfp.reports import run_report, summary
from wfp.security.credentials import CredentialService
from wfp.store.db import Store
from wfp.store.nodes import NodeStore
from wfp.store.pipelines import PipelineStore
from wfp.store.runs import RunStore
from wfp.sync.patch import GraphDelta, YamlDoc, prune_layout

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def sse_frame(ev: dict[str, Any]) -> str:
    """DR-61：SSE 帧只发 id/data，不发 event: 命名事件。

    EventSource.onmessage 仅接收默认 message 类型；若发送 `event: run.started` 这类命名事件，
    浏览器会分发给对应 addEventListener 而未注册时直接丢弃，日志面板将永远显示"等待事件…"。
    事件类型已在 data.type 中，Last-Event-ID 续传由 id: 提供。
    """
    seq = int(ev["seq"])
    return f"id: {seq}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"


class RunManager:
    """排队 + 后台执行；apply 全局互斥由 Runner 内部锁保证（DR-02/39）。"""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.tasks: dict[str, dict[str, Any]] = {}
        self.plans: dict[str, dict[str, Any]] = {}

    def submit(self, kind: str, fn: Callable[[], Any]) -> str:
        tid = f"t-{uuid.uuid4().hex[:10]}"
        self.tasks[tid] = {"id": tid, "kind": kind, "status": "queued"}

        def work() -> None:
            self.tasks[tid]["status"] = "running"
            try:
                result = fn()
                self.tasks[tid].update(status="done", result=result)
                if kind == "plan" and isinstance(result, dict):
                    self.plans[result["run_id"]] = result
            except Exception as exc:  # noqa: BLE001
                self.tasks[tid].update(status="error", error={
                    "code": getattr(exc, "code", "WFP-E-INTERNAL"), "message": str(exc),
                    "details": getattr(exc, "details", None)})
        threading.Thread(target=work, name=f"wfp-{kind}-{tid}", daemon=True).start()
        return tid

    def get(self, task_id: str) -> dict[str, Any]:
        if task_id not in self.tasks:
            raise NotFound(f"任务不存在：{task_id}")
        return self.tasks[task_id]


def create_app(paths: DataPaths | None = None, *, token: str | None = None,
               lan: bool | None = None, port: int | None = None,
               open_browser: bool | None = None, log_level: str | None = None,
               config: Config | None = None) -> FastAPI:
    """启动 API；显式参数优先于 config.toml（DR-01/67/72）。"""
    cfg = config or Config.default(paths)
    paths = (paths or cfg.require_paths()).ensure()
    lan_enabled = cfg.lan if lan is None else lan
    server_port = cfg.port if port is None else port
    open_on_start = cfg.open_browser if open_browser is None else open_browser
    server_log_level = cfg.log_level if log_level is None else log_level
    if not lan_enabled and cfg.host not in {"127.0.0.1", "localhost", "::1"}:
        raise WfpError("默认仅允许回环监听；非回环 host 需显式 lan=true（DR-72）")
    store = Store(paths.db)
    store.migrate()
    runner = Runner(paths, store, actor="ui", config=cfg)
    actions = ActionRegistry(store, paths)
    pipelines = PipelineStore(store, paths)
    runs = RunStore(store, paths)
    credentials = CredentialService(store, paths)
    nodes = NodeStore(store, paths, credentials)
    manager = RunManager(runner)
    ai = AIDraftService(store, runner._action_exists)
    # 配置里的功能开关作为 setting 初值；用户后续在设置页修改优先（DR-67）
    if store.get_setting("ai.enabled") is None:
        store.set_setting("ai.enabled", cfg.ai_enabled)
    if cfg.current_node and nodes.current_id is None:
        try:
            nodes.set_current(cfg.current_node, actor="config")
        except NotFound:
            pass

    session_file = paths.session
    if token:
        sess_token = token
    elif session_file.exists():
        sess_token = json.loads(session_file.read_text(encoding="utf-8"))["token"]
    else:
        sess_token = secrets.token_urlsafe(24)
    session_file.write_text(json.dumps({"token": sess_token}, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(session_file, 0o600)
    except OSError:
        pass

    app = FastAPI(title="WFP", version=__version__, docs_url="/api/docs", redoc_url=None)
    # Vite/Monaco 产物离线托管：/assets/* 由本地文件系统提供，无任何 CDN（DR-07）
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")
    app.state.wfp = {"paths": paths, "store": store, "runner": runner, "token": sess_token,
                     "manager": manager, "lan": lan_enabled, "port": server_port,
                     "log_level": server_log_level, "config": cfg, "actions": actions,
                     "pipelines": pipelines, "runs": runs, "nodes": nodes,
                     "credentials": credentials, "ai": ai, "open_browser": open_on_start}

    def persisted_plan_report(run_id: str) -> dict[str, Any] | None:
        """刷新页面后仍可从 artifacts 恢复 PlanReport（修复 plan→apply 依赖内存字典的缺陷）。"""
        cached = manager.plans.get(run_id)
        if cached:
            return cached
        try:
            rec = runs.get(run_id)
            rel = rec.get("plan_report_path")
            if not rel:
                return None
            p = paths.root / rel
            if not p.is_file():
                return None
            report = json.loads(p.read_text(encoding="utf-8"))
            manager.plans[run_id] = report
            return report
        except (NotFound, OSError, json.JSONDecodeError):
            return None

    @app.exception_handler(WfpError)
    async def wfp_error(_: Request, exc: WfpError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            host = (request.headers.get("host") or "").split(":")[0]
            origin = request.headers.get("origin")
            allowed_hosts = {"127.0.0.1", "localhost", "::1"}
            if not lan_enabled and host not in allowed_hosts:
                return JSONResponse({"code": "WFP-E-ORIGIN", "message": "仅允许回环访问（DR-72）"},
                                    status_code=403)
            if origin and not any(origin.startswith(f"http://{h}:{server_port}") or
                                  origin == f"http://{h}" for h in allowed_hosts):
                return JSONResponse({"code": "WFP-E-ORIGIN", "message": "Origin 校验失败（DR-72）"},
                                    status_code=403)
            supplied = request.headers.get("x-wfp-token") or request.query_params.get("token")
            if supplied != sess_token:
                return JSONResponse({"code": "WFP-E-AUTH", "message": "缺少或错误的本机 token"},
                                    status_code=401)
        return await call_next(request)

    # ---------- 基础 ----------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "schema_version": store.current_version(),
                "home": str(paths.root), "port": server_port,
                "config_file": str(cfg.source) if cfg.source else ""}

    @app.get("/api/config")
    def config_view() -> dict[str, Any]:
        """生效启动配置（只读；永不含 token/口令/凭据，DR-52/72）。"""
        return cfg.public_dict()

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str) -> dict[str, Any]:
        return manager.get(task_id)

    # ---------- 动作库（DR-03/10/13/18/41） ----------
    @app.get("/api/actions")
    def action_list(query: str = "", category: str | None = None, tag: str | None = None) -> Any:
        return actions.list(query=query, category=category, tag=tag)

    @app.post("/api/actions")
    async def action_create(request: Request) -> Any:
        body = await request.json()
        return actions.create(body.get("manifest") or {}, body.get("impl", ""))

    @app.get("/api/actions/{action_id}")
    def action_get(action_id: str) -> Any:
        return actions.get(action_id)

    @app.put("/api/actions/{action_id}")
    async def action_update(action_id: str, request: Request) -> Any:
        body = await request.json()
        return actions.add_version(action_id, body.get("manifest") or {}, body.get("impl", ""))

    @app.delete("/api/actions/{action_id}")
    def action_delete(action_id: str) -> Any:
        return actions.soft_delete(action_id)

    @app.get("/api/actions/{action_id}/versions")
    def action_versions(action_id: str) -> Any:
        return actions.versions(action_id)

    @app.get("/api/actions/{action_id}/versions/{version}")
    def action_version(action_id: str, version: str) -> Any:
        return actions.get_version(action_id, version)

    @app.get("/api/actions/{action_id}/diff")
    def action_diff(action_id: str, from_version: str, to_version: str) -> Any:
        result = actions.diff(action_id, from_version, to_version)
        result["before"] = actions.get_version(action_id, from_version)["impl"]
        result["after"] = actions.get_version(action_id, to_version)["impl"]
        return result

    @app.get("/api/actions/{action_id}/references")
    def action_refs(action_id: str) -> Any:
        return actions.references(action_id)

    @app.post("/api/actions/{action_id}/test")
    def action_test(action_id: str, version: str | None = None) -> Any:
        return run_contract_suite(actions, action_id, version)

    @app.post("/api/actions/{action_id}/enable")
    async def action_enable(action_id: str, request: Request) -> Any:
        body = await request.json()
        return actions.set_enabled(action_id, bool(body.get("enabled")), actor="ui")

    @app.post("/api/actions/{action_id}/duplicate")
    async def action_duplicate(action_id: str, request: Request) -> Any:
        body = await request.json()
        return actions.duplicate(action_id, str(body.get("new_id")), actor="ui")

    @app.post("/api/actions/{action_id}/trial")
    async def action_trial(action_id: str, request: Request) -> Any:
        """单动作试跑：用 manifest.params 生成一步工作项，plan 零副作用 / apply 走确认闸（DR-41）。"""
        body = await request.json()
        a = actions.get(action_id)
        version = a["latest_version"]
        params = body.get("params") or {}
        phase = str(body.get("phase") or "plan")
        trials = paths.root / "trials"
        trials.mkdir(parents=True, exist_ok=True)
        doc: dict[str, Any] = {
            "apiVersion": "wfp/v1",
            "meta": {"id": f"trial-{action_id}".replace(".", "-"), "name": f"试跑 {action_id}",
                     "version": "1.0.0", "workdir": str(trials)},
            "steps": [{"id": "trial", "uses": f"{action_id}@{version}", "with": params}],
        }
        if a.get("runtime") == "remote" and nodes.current_id:
            doc["defaults"] = {"node": "current"}
        ref = trials / f"trial-{uuid.uuid4().hex[:8]}.yaml"
        ref.write_text(safe_dump(doc), encoding="utf-8")
        if phase == "apply":
            return runner.apply(str(ref), {}, yes=True, trigger="ui")
        return runner.plan(str(ref), {}, trigger="ui")

    # ---------- 工作项与双视图（DR-28/43/45/46/48） ----------
    @app.get("/api/pipelines")
    def pipeline_list() -> Any:
        return pipelines.list()

    @app.post("/api/pipelines")
    async def pipeline_create(request: Request) -> Any:
        body = await request.json()
        text = body.get("yaml") or ""
        if body.get("path"):
            text = Path(body["path"]).read_text(encoding="utf-8")
        return pipelines.create(text)

    @app.post("/api/pipelines/draft")
    async def pipeline_draft(request: Request) -> Any:
        """DR-44：从编排视图的拖拽草稿创建工作项；YAML 真源仍由后端生成并校验。"""
        body = await request.json()
        pid = str(body.get("id") or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", pid):
            raise ContractError("工作项 ID 必须小写字母开头，仅含小写字母/数字/下划线/连字符",
                                path="meta.id")
        raw_steps = body.get("steps") or []
        if not raw_steps:
            raise ContractError("请至少拖入一个动作再保存", path="steps")
        steps: list[dict[str, Any]] = []
        for i, s in enumerate(raw_steps):
            if not isinstance(s, dict) or not s.get("uses"):
                raise ContractError(f"steps[{i}] 缺少 uses", path=f"steps[{i}]")
            step: dict[str, Any] = {"id": str(s.get("id") or f"s{i + 1}"),
                                    "uses": str(s["uses"]), "with": s.get("with") or {}}
            for key in ("name", "when", "node", "timeout_seconds"):
                if s.get(key) not in (None, ""):
                    step[key] = s[key]
            if isinstance(s.get("loop"), dict):
                step["loop"] = s["loop"]
            steps.append(step)
        meta: dict[str, Any] = {"id": pid, "name": str(body.get("name") or pid), "version": "1.0.0"}
        if body.get("workdir"):
            meta["workdir"] = str(body["workdir"])
        if body.get("allow_outside_workspace"):
            meta["allow_outside_workspace"] = True
        doc: dict[str, Any] = {
            "apiVersion": "wfp/v1", "meta": meta,
            "inputs": body.get("inputs") or {"type": "object", "properties": {}},
            "steps": steps,
        }
        return pipelines.create(safe_dump(doc))

    @app.get("/api/pipelines/{pipeline_id}")
    def pipeline_get(pipeline_id: str) -> Any:
        p = pipelines.get(pipeline_id)
        return {"id": pipeline_id, "version": p.version, "sha256": p.sha256,
                "steps": len(p.steps), "inputs": p.inputs_schema, "meta": p.meta}

    @app.put("/api/pipelines/{pipeline_id}/meta")
    async def pipeline_meta_put(pipeline_id: str, request: Request) -> Any:
        """DR-24：视图设置 workspace 根 / 越界开关；改 meta 并产生新版本，注释保真。"""
        body = await request.json()
        text, sha = pipelines.yaml_text(pipeline_id)
        doc = YamlDoc(text)
        meta = doc.doc.get("meta")
        if not isinstance(meta, dict):
            raise ContractError("工作项缺少 meta", path="meta")
        if "workdir" in body:
            wd = str(body.get("workdir") or "").strip()
            if wd:
                meta["workdir"] = wd
            else:
                meta.pop("workdir", None)
        if "allow_outside_workspace" in body:
            if bool(body["allow_outside_workspace"]):
                meta["allow_outside_workspace"] = True
            else:
                meta.pop("allow_outside_workspace", None)
        return pipelines.save_yaml(pipeline_id, doc.dump(), if_match=sha)

    @app.delete("/api/pipelines/{pipeline_id}")
    def pipeline_delete(pipeline_id: str) -> Any:
        return pipelines.delete(pipeline_id)

    @app.get("/api/pipelines/{pipeline_id}/yaml")
    def pipeline_yaml(pipeline_id: str) -> Any:
        text, sha = pipelines.yaml_text(pipeline_id)
        return {"text": text, "sha256": sha}

    @app.put("/api/pipelines/{pipeline_id}/yaml")
    async def pipeline_yaml_put(pipeline_id: str, request: Request) -> Any:
        body = await request.json()
        return pipelines.save_yaml(pipeline_id, body.get("yaml", ""),
                                  if_match=request.headers.get("if-match"))

    @app.post("/api/pipelines/{pipeline_id}/patch")
    async def pipeline_patch(pipeline_id: str, request: Request) -> Any:
        """DR-45：图/表单编辑的最小 patch 通道（仅 steps[i]）。"""
        body = await request.json()
        text, sha = pipelines.yaml_text(pipeline_id)
        doc = YamlDoc(text)
        deltas = [GraphDelta(**d) for d in body.get("deltas", [])]
        expected = body.get("sha256") or sha
        for d in deltas:
            doc.apply_delta(d, expected_sha=expected)
            expected = doc.text_sha   # 同一批 patch 内逐个推进，避免第二个 delta 误判冲突（DR-45）
        result = pipelines.save_yaml(pipeline_id, doc.text, if_match=sha)
        result["text"] = doc.text
        return result

    @app.get("/api/pipelines/{pipeline_id}/graph")
    def pipeline_graph(pipeline_id: str) -> Any:
        from wfp.sync.patch import project_text
        text, sha = pipelines.yaml_text(pipeline_id)
        model = project_text(text)
        return {"sha256": sha, "nodes": model.nodes, "edges": model.edges, "index": model.index}

    @app.get("/api/pipelines/{pipeline_id}/layout")
    def layout_get(pipeline_id: str) -> Any:
        return pipelines.get_layout(pipeline_id)

    @app.put("/api/pipelines/{pipeline_id}/layout")
    async def layout_put(pipeline_id: str, request: Request) -> Any:
        layout = await request.json()
        text, _ = pipelines.yaml_text(pipeline_id)
        from wfp.sync.patch import layout_step_ids
        clean, removed = prune_layout(layout, layout_step_ids(text))
        return {"layout": pipelines.put_layout(pipeline_id, clean), "removed_orphans": removed}

    @app.post("/api/pipelines/{pipeline_id}/lint")
    def pipeline_lint(pipeline_id: str) -> Any:
        p = pipelines.get(pipeline_id)
        issues = lint_pipeline(p, action_exists=runner._action_exists,
                              current_node=nodes.current_id)
        return {"issues": [i.as_dict() for i in issues]}

    @app.get("/api/pipelines/{pipeline_id}/export")
    def pipeline_export(pipeline_id: str, out: str | None = None) -> Any:
        dest = Path(out) if out else paths.backups / f"{pipeline_id}.zip"
        return {"path": str(pipelines.export_zip(pipeline_id, dest))}

    @app.get("/api/pipelines/{pipeline_id}/download")
    def pipeline_download(pipeline_id: str) -> FileResponse:
        dest = paths.backups / f"{pipeline_id}.zip"
        pipelines.export_zip(pipeline_id, dest)
        return FileResponse(dest, filename=f"{pipeline_id}.zip", media_type="application/zip")

    @app.post("/api/pipelines/{pipeline_id}/duplicate")
    async def pipeline_duplicate(pipeline_id: str, request: Request) -> Any:
        body = await request.json()
        return pipelines.duplicate(pipeline_id, str(body.get("new_id")), actor="ui")

    # ---------- 运行（DR-49/60/61/63/64） ----------
    @app.post("/api/runs")
    async def run_create(request: Request) -> Any:
        body = await request.json()
        ref = body.get("pipeline") or body.get("ref")
        inputs = body.get("inputs") or {}
        phase = body.get("phase", "plan")
        if phase == "plan":
            tid = manager.submit("plan", lambda: runner.plan(ref, inputs, trigger="ui"))
        else:
            report = persisted_plan_report(body.get("plan_run_id") or "")
            if not report:
                raise Conflict("apply 前必须先 plan 并确认 PlanReport（DR-49）")
            confirm = {"plan_sha": report["sha256"], "ack_risks": bool(body.get("ack_risks")),
                       "actor": "ui", "at": body.get("at")}
            tid = manager.submit("apply", lambda: runner.apply(
                ref, inputs, confirm=confirm, trigger="ui", plan_report=report))
        return {"task_id": tid, "status": "queued", "phase": phase}

    @app.post("/api/runs/direct")
    async def run_direct(request: Request) -> Any:
        """DR-31/49：直接运行 = 内部先 plan 再 apply（yes 确认闸），一次提交完成。"""
        body = await request.json()
        ref = body.get("pipeline") or body.get("ref")
        inputs = body.get("inputs") or {}
        if not body.get("ack_risks"):
            raise ContractError("直接运行必须显式确认风险（ack_risks=true）", path="ack_risks")
        tid = manager.submit("apply", lambda: runner.apply(ref, inputs, yes=True, trigger="ui"))
        return {"task_id": tid, "status": "queued", "phase": "apply"}

    @app.get("/api/runs")
    def run_list(pipeline_id: str | None = None, status: str | None = None) -> Any:
        return runs.list(pipeline_id=pipeline_id, status=status)

    @app.get("/api/runs/{run_id}")
    def run_get(run_id: str) -> Any:
        return runner.run_status(run_id)

    @app.post("/api/runs/{run_id}/apply")
    async def run_apply(run_id: str, request: Request) -> Any:
        body = await request.json()
        report = persisted_plan_report(run_id)
        if not report:
            raise Conflict("未找到该 run 的 PlanReport（DR-49）")
        rec = runs.get(run_id)
        confirm = {"plan_sha": report["sha256"], "ack_risks": bool(body.get("ack_risks")),
                   "actor": "ui"}
        tid = manager.submit("apply", lambda: runner.apply(
            rec["pipeline_id"], rec["inputs"], confirm=confirm, trigger="ui", plan_report=report))
        return {"task_id": tid, "status": "queued"}

    @app.post("/api/runs/{run_id}/cancel")
    def run_cancel(run_id: str) -> Any:
        return runner.cancel(run_id)

    @app.post("/api/runs/{run_id}/pause")
    def run_pause(run_id: str) -> Any:
        return runner.pause(run_id)

    @app.post("/api/runs/{run_id}/resume")
    def run_resume(run_id: str, action: str = "continue") -> Any:
        return runner.resume(run_id) if action == "resume" else runner.continue_run(run_id)

    @app.post("/api/runs/{run_id}/compensate")
    def run_compensate(run_id: str) -> Any:
        return runner.compensate(run_id)

    @app.post("/api/runs/{run_id}/steps/{step_id}/retry")
    def step_retry(run_id: str, step_id: str) -> Any:
        return runner.retry_step(run_id, step_id)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> StreamingResponse:
        after = int(request.headers.get("last-event-id") or request.query_params.get("last_event_id") or 0)
        path = paths.logs / run_id / "events.ndjson"

        async def stream() -> Any:
            nonlocal after
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    break
                found = False
                for ev in EventWriter.tail(path, after):
                    found = True
                    after = int(ev["seq"])
                    yield sse_frame(ev)
                if found:
                    idle = 0.0
                else:
                    idle += 0.25
                    if idle > 2:
                        yield ": keepalive\n\n"
                        idle = 0.0
                await asyncio.sleep(0.25)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/artifacts")
    def artifacts(run_id: str) -> Any:
        return runs.artifacts(run_id)

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
    def artifact_download(run_id: str, artifact_id: str) -> FileResponse:
        for a in runs.artifacts(run_id):
            if a["id"] == artifact_id:
                p = paths.root / a["path"]
                if p.exists():
                    return FileResponse(p, filename=p.name)
        raise NotFound(f"证据不存在：{artifact_id}")

    @app.get("/api/runs/{run_id}/events.ndjson")
    def events_download(run_id: str) -> FileResponse:
        p = paths.logs / run_id / "events.ndjson"
        if not p.is_file():
            raise NotFound(f"事件流不存在：{run_id}")
        return FileResponse(p, filename=f"{run_id}-events.ndjson", media_type="application/x-ndjson")

    @app.get("/api/runs/{run_id}/evidence.zip")
    def evidence_download(run_id: str) -> FileResponse:
        out = run_evidence_zip(paths, store, run_id)
        return FileResponse(out, filename=out.name, media_type="application/zip")

    @app.post("/api/runs/{run_id}/replay")
    def run_replay(run_id: str) -> Any:
        """以相同输入 + 固化版本重跑（DR-65）。"""
        rec = runs.get(run_id)
        try:
            ref = str(pipelines.yaml_file(rec["pipeline_id"], version=rec["pipeline_version"]))
        except NotFound:
            ref = str(rec["pipeline_id"])
        return runner.apply(ref, rec["inputs"], yes=True, trigger="ui")

    @app.get("/api/runs/{run_id}/report")
    def report(run_id: str, fmt: str = "md") -> Response:
        text, media, name = run_report(paths, runs, run_id, fmt=fmt)
        return Response(text, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---------- 节点 / 凭据 / 设置 / 审计（DR-50/51/52/67/68） ----------
    @app.get("/api/nodes")
    def node_list() -> Any:
        return nodes.list()

    @app.post("/api/nodes")
    async def node_add(request: Request) -> Any:
        return nodes.add(await request.json())

    @app.put("/api/nodes/{node_id}")
    async def node_update(node_id: str, request: Request) -> Any:
        return nodes.update(node_id, await request.json())

    @app.delete("/api/nodes/{node_id}")
    def node_del(node_id: str) -> Any:
        return nodes.delete(node_id)

    @app.post("/api/nodes/{node_id}/probe")
    def node_probe(node_id: str) -> Any:
        return nodes.probe(node_id)

    @app.get("/api/context/current-node")
    def current_node() -> Any:
        return {"current_node": nodes.current_id}

    @app.put("/api/context/current-node")
    async def set_current_node(request: Request) -> Any:
        return nodes.set_current((await request.json()).get("node_id"))

    @app.get("/api/credentials")
    def credential_list() -> Any:
        return credentials.list()

    @app.post("/api/credentials")
    async def credential_add(request: Request) -> Any:
        body = await request.json()
        return credentials.put(body.get("label", "credential"), body.get("value", ""),
                               kind=body.get("kind", "password"))

    @app.delete("/api/credentials/{credential_id}")
    def credential_del(credential_id: str) -> Any:
        credentials.delete(credential_id)
        return {"id": credential_id, "deleted": True}

    @app.get("/api/settings")
    def settings_get() -> Any:
        rows = store.read(lambda c: c.execute("SELECT key,value_json,updated_at FROM setting").fetchall())
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    @app.put("/api/settings")
    async def settings_put(request: Request) -> Any:
        body = await request.json()
        for k, v in body.items():
            store.set_setting(k, v)
        store.audit("ui", "settings.update", None, {"keys": list(body)})
        return {"ok": True, "keys": list(body)}

    @app.get("/api/audit")
    def audit_list(limit: int = 200) -> Any:
        rows = store.read(lambda c: c.execute(
            "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall())
        return [dict(r) for r in rows]

    @app.get("/api/history/summary")
    def history_summary() -> Any:
        return summary(paths, runs)

    # ---------- 维护与可选 AI（DR-05/69/78/79/80/83） ----------
    @app.post("/api/maintenance/export")
    async def maintenance_export(request: Request) -> Any:
        body = await request.json()
        out = Path(body.get("out") or paths.backups / "wfp-export.zip")
        return {"path": str(export_all(paths, store, out,
                                       with_credentials=bool(body.get("with_credentials"))))}

    @app.post("/api/maintenance/import")
    async def maintenance_import(request: Request) -> Any:
        body = await request.json()
        return import_all(paths, store, Path(body["path"]))

    @app.get("/api/maintenance/verify")
    def maintenance_verify() -> Any:
        return verify(paths, store)

    @app.post("/api/maintenance/cleanup")
    async def maintenance_cleanup(request: Request) -> Any:
        body = await request.json()
        return runs.cleanup(days=int(body.get("days", cfg.retention_days)),
                            max_bytes=int(body.get("max_bytes", cfg.retention_max_bytes)))

    @app.get("/api/maintenance/diagnostics")
    def maintenance_diagnostics(run_id: str | None = None) -> Any:
        return {"path": str(diagnostics(paths, store, run_id))}

    @app.get("/api/maintenance/doctor")
    def maintenance_doctor() -> Any:
        return run_doctor(paths, store=store, port=server_port, config_path=cfg.source)

    @app.get("/api/maintenance/export/download")
    def maintenance_export_download() -> FileResponse:
        out = export_all(paths, store, paths.backups / "wfp-export.zip")
        return FileResponse(out, filename="wfp-backup.zip", media_type="application/zip")

    @app.get("/api/maintenance/diagnostics/download")
    def maintenance_diagnostics_download(run_id: str | None = None) -> FileResponse:
        out = diagnostics(paths, store, run_id)
        return FileResponse(out, filename=out.name, media_type="application/zip")

    @app.post("/api/maintenance/import/upload")
    async def maintenance_import_upload(file: UploadFile = File(...)) -> Any:  # noqa: B008 - FastAPI 惯用写法
        import shutil
        temp = paths.backups / f"upload-{uuid.uuid4().hex[:8]}.zip"
        with temp.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        return import_all(paths, store, temp)

    @app.post("/api/ai/accept")
    def ai_accept(id: str) -> Any:
        return ai.accept(id)

    @app.post("/api/ai/draft")
    async def ai_draft(request: Request) -> Any:
        if not ai.enabled:
            return JSONResponse({"code": "WFP-E-AI-DISABLED", "message": "AI 默认关闭（DR-83）"},
                                status_code=404)
        body = await request.json()
        return ai.draft(body.get("description", ""))

    # ---------- 静态 SPA（DR-07/40） ----------
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (DIST / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__WFP_TOKEN__", sess_token))

    @app.get("/app.js")
    def app_js() -> FileResponse:
        return FileResponse(DIST / "app.js", media_type="application/javascript")

    @app.get("/style.css")
    def app_css() -> FileResponse:
        return FileResponse(DIST / "style.css", media_type="text/css")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    return app


__all__ = ["RunManager", "create_app"]
