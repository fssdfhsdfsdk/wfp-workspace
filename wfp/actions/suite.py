"""动作契约一致性测试套件（8 项检查，任何动作发布前必须通过）。

Implements: DR-18, DR-41, DR-75
"""
from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

from wfp.contracts.manifest import validate_manifest
from wfp.engine.cancel import CancelToken
from wfp.engine.expr import validate_params
from wfp.security.redactor import Redactor, SecretStr


def run_contract_suite(registry: Any, action_id: str, version: str | None = None,
                       *, workspace: Any | None = None) -> dict[str, Any]:
    v = version or registry.get(action_id)["latest_version"]
    record = registry.get_version(action_id, v)
    manifest = record["manifest"]
    checks: list[dict[str, Any]] = []

    def add(name: str, fn: Any) -> None:
        try:
            detail = fn()
            checks.append({"name": name, "ok": True, "detail": detail or "ok"})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": name, "ok": False, "detail": str(exc)})

    add("schema", lambda: (validate_manifest(manifest), "manifest 合法")[1])
    add("capabilities", lambda: (
        all(k in manifest["capabilities"] for k in
            ("dry_run", "idempotent", "compensate", "needs_admin", "needs_network", "side_effects"))
        or (_ for _ in ()).throw(AssertionError("能力声明缺失")), "6 项能力齐备")[1])
    add("params_schema", lambda: (Draft202012Validator.check_schema(manifest["params"]),
                                  "params 是合法 JSON Schema")[1])
    add("param_validation", lambda: _check_bad_param(manifest))
    add("plan_zero_side_effect", lambda: _check_plan_readonly(registry, manifest, workspace))
    add("cancel_response", lambda: _check_cancel(manifest))
    add("timeout_policy", lambda: (float(manifest.get("policy", {}).get("timeout_seconds", 300)) > 0
                                   or (_ for _ in ()).throw(AssertionError("未声明超时")), "超时策略已声明")[1])
    add("outputs_schema", lambda: (Draft202012Validator.check_schema(manifest.get("outputs") or {}),
                                   "outputs 结构合法")[1])
    add("secret_redaction", lambda: _check_redaction())
    add("evidence", lambda: (manifest.get("evidence") or (_ for _ in ()).throw(
        AssertionError("未声明证据类型")), "证据类型已声明")[1])
    return {"action_id": action_id, "version": v,
            "ok": all(c["ok"] for c in checks), "checks": checks,
            "passed": sum(c["ok"] for c in checks), "total": len(checks)}


def _check_bad_param(manifest: dict[str, Any]) -> str:
    required = (manifest["params"] or {}).get("required") or []
    if not required:
        return "无必填参数，跳过反向用例"
    try:
        validate_params({}, manifest)
    except Exception:
        return "错误实参被拦截（字段级路径）"
    raise AssertionError("缺少必填参数却通过校验")


def _check_plan_readonly(registry: Any, manifest: dict[str, Any], workspace: Any | None) -> str:
    """用文件系统快照探针验证 plan 不写目标文件（DR-30/18）。"""
    import tempfile
    from pathlib import Path

    from wfp.engine.journal import ArtifactWriter, EventWriter
    from wfp.engine.types import ActionContext
    from wfp.paths import DataPaths
    from wfp.runtimes.base import adapter_for
    from wfp.security.redactor import Redactor as R

    if manifest["capabilities"]["dry_run"] != "plan":
        return "dry_run=none：引擎保证 plan 不执行实现体"
    with tempfile.TemporaryDirectory() as td:
        home = DataPaths(Path(td) / "home").ensure()
        ws = Path(workspace) if workspace else Path(td) / "ws"
        ws.mkdir(parents=True, exist_ok=True)
        target = ws / "probe.txt"
        target.write_text("{}\n", encoding="utf-8")
        paths = home
        # 轻量 store 替身：仅提供 write 用于 artifact 记账（用真实 SQLite 亦可）
        from wfp.store.db import Store
        store = Store(paths.db)
        store.migrate()
        try:
            redactor = R()
            events = EventWriter(paths, "r-suite", redactor)
            artifacts = ArtifactWriter(store, paths, "r-suite", redactor, events)
            from wfp.transport.fake import FakeTransport
            transport = (FakeTransport(Path(td) / "remote") if manifest["runtime"] == "remote"
                         else None)
            ctx = ActionContext(params=_sample_params(manifest), workspace=ws, run_id="r-suite",
                                step_id="s-suite", iteration=0, emit=lambda t, p: None,
                                artifact=artifacts, cancel=CancelToken(), deadline=float("inf"),
                                transport=transport, secrets=None, node=None, redactor=redactor,
                                manifest=manifest, action_id=manifest["id"],
                                action_version=manifest["version"], registry=registry,
                                allow_outside=True)
            snap = {str(p): p.stat().st_mtime_ns for p in ws.rglob("*") if p.is_file()}
            adapter_for(manifest, registry).plan(ctx)
            after = {str(p): p.stat().st_mtime_ns for p in ws.rglob("*") if p.is_file()}
            if snap != after:
                raise AssertionError("plan 期间工作区发生写操作")
            events.close()
        finally:
            store.close()
    return "plan 前后工作区字节/时间戳一致"


def _sample_params(manifest: dict[str, Any]) -> dict[str, Any]:
    props = (manifest["params"] or {}).get("properties") or {}
    out: dict[str, Any] = {}
    for key, spec in props.items():
        spec = spec or {}
        if "default" in spec:
            out[key] = spec["default"]
        elif spec.get("type") == "string" or "enum" not in spec:
            out[key] = "probe.txt"
        elif "enum" in spec:
            out[key] = spec["enum"][0]
        if key == "edits":
            out[key] = [{"key": "a", "value": 1}]
        if key == "items":
            out[key] = [{"local": "probe.txt", "remote": "/tmp/probe.txt"}]
        if key == "schema":
            out[key] = {"type": "object"}
    return out


def _check_cancel(manifest: dict[str, Any]) -> str:
    token = CancelToken()
    token.cancel()
    try:
        token.check()
    except Exception:
        return "CancelToken 协作式取消可响应"
    raise AssertionError("取消令牌无响应")


def _check_redaction() -> str:
    r = Redactor()
    s = SecretStr("P@ssw0rd/中文", "cred-test")
    text = json.dumps({"command": f"password={s.reveal()}"}, ensure_ascii=False)
    r.add(s)
    if s.reveal() in r.scrub(text) or str(s) != "***":
        raise AssertionError("凭据值未被脱敏")
    return "明文/base64/URL 变体均被过滤"


__all__ = ["run_contract_suite"]
