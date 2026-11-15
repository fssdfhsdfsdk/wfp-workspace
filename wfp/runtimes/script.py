"""python / powershell / cmd 子进程适配器（统一入口契约）。

Implements: DR-12, DR-14, DR-15, DR-35, DR-36, DR-53, DR-77
- python：WFP_PARAMS 环境变量注入 + stdout 末行 WFP_OUTPUT::{json}（IR-14 缺入口）
- 子进程进程组 + 进程树 kill；stdin 关闭；Windows 无窗口
- requirements 指纹隔离 venv（支持离线 wheelhouse）
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from wfp.engine.cancel import Cancelled, ProcessHandle, popen_kwargs
from wfp.engine.types import ActionContext, ApplyResult
from wfp.errors import ActionError
from wfp.security.redactor import SecretStr

OUTPUT_MARKER = "WFP_OUTPUT::"


def _jsonable(value: Any, *, reveal: bool = False) -> Any:
    if isinstance(value, SecretStr):
        return value.reveal() if reveal else "***"
    if isinstance(value, dict):
        return {k: _jsonable(v, reveal=reveal) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, reveal=reveal) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def venv_python(ctx: ActionContext, requirements: list[str]) -> str:
    """DR-15：按依赖指纹复用 venv；离线优先 wheelhouse。"""
    if not requirements:
        return sys.executable
    fingerprint = hashlib.sha256("\n".join(sorted(requirements)).encode()).hexdigest()[:16]
    venv_dir = ctx.artifact.paths.venvs / fingerprint
    py = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if py.exists():
        return str(py)
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wheelhouse = ctx.artifact.paths.root / "wheelhouse"
    cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--quiet"]
    if wheelhouse.is_dir():
        cmd += ["--no-index", "--find-links", str(wheelhouse)]
    cmd += requirements
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ctx.artifact.events.emit("venv.created", {"fingerprint": fingerprint, "requirements": requirements})
    return str(py)


def _run_python(ctx: ActionContext, impl: str) -> ApplyResult:
    interpreter = venv_python(ctx, list(ctx.manifest.get("requirements") or []))
    with tempfile.TemporaryDirectory(prefix="wfp-script-") as td:
        script = Path(td) / "script.py"
        script.write_text(impl, encoding="utf-8")
        env = dict(os.environ)
        # DR-53：明文只经 env 注入，绝不进命令行；日志侧由 Redactor 过滤
        env["WFP_PARAMS"] = json.dumps(_jsonable(ctx.params, reveal=True), ensure_ascii=False)
        env["WFP_RUN_ID"], env["WFP_STEP_ID"] = ctx.run_id, ctx.step_id
        argv = [interpreter, str(script), *[str(a) for a in ctx.params.get("args", [])]]
        proc = subprocess.Popen(argv, env=env, cwd=str(ctx.workspace),
                                **popen_kwargs(console=ctx.manifest.get("policy", {}).get("console", "hidden")))
        handle = ProcessHandle(proc)
        def _watch_cancel() -> None:
            if ctx.cancel.wait(ctx.timeout):
                handle.terminate_tree()
        threading.Thread(target=_watch_cancel, daemon=True).start()
        try:
            out, err = proc.communicate(timeout=ctx.timeout)
        except subprocess.TimeoutExpired as exc:
            handle.terminate_tree()
            out, err = proc.communicate()
            raise ActionError(f"WFP-E-TIMEOUT：脚本超时 {ctx.timeout}s") from exc
        if ctx.cancel.cancelled:
            raise Cancelled("脚本已取消")
        ctx.emit_log(out[-100_000:], "stdout")
        ctx.emit_log(err[-100_000:], "stderr")
        if proc.returncode != 0:
            raise ActionError(f"脚本退出码 {proc.returncode}：{err.strip()[:500]}")
        outputs: dict[str, Any] = {"stdout": out.strip()}
        for line in reversed(out.splitlines()):
            if line.startswith(OUTPUT_MARKER):
                try:
                    parsed = json.loads(line[len(OUTPUT_MARKER):])
                    if isinstance(parsed, dict):
                        outputs.update(parsed)
                except json.JSONDecodeError:
                    pass
                break
        return ApplyResult(outputs=outputs, receipt={"kind": "script", "manual": True})


def _run_shell(ctx: ActionContext, impl: str) -> ApplyResult:
    runtime = ctx.manifest["runtime"]
    if sys.platform == "win32":  # pragma: no cover - Windows runner
        argv = (["powershell", "-NonInteractive", "-NoProfile", "-Command", impl]
                if runtime == "powershell" else ["cmd", "/c", impl])
    else:
        argv = ["/bin/sh", "-c", impl]
    env = dict(os.environ)
    env["WFP_PARAMS"] = json.dumps(_jsonable(ctx.params, reveal=True), ensure_ascii=False)
    proc = subprocess.Popen(argv, env=env, cwd=str(ctx.workspace),
                            **popen_kwargs(console=ctx.manifest.get("policy", {}).get("console", "hidden")))
    handle = ProcessHandle(proc)
    try:
        out, err = proc.communicate(timeout=ctx.timeout)
    except subprocess.TimeoutExpired as exc:
        handle.terminate_tree()
        raise ActionError(f"WFP-E-TIMEOUT：{runtime} 超时") from exc
    if ctx.cancel.cancelled:
        raise Cancelled("脚本已取消")
    ctx.emit_log(out[-100_000:], "stdout")
    ctx.emit_log(err[-100_000:], "stderr")
    if proc.returncode != 0:
        raise ActionError(f"{runtime} 退出码 {proc.returncode}：{err.strip()[:500]}")
    outputs: dict[str, Any] = {"stdout": out.strip()}
    for line in reversed(out.splitlines()):
        if line.startswith(OUTPUT_MARKER):
            try:
                parsed = json.loads(line[len(OUTPUT_MARKER):])
                if isinstance(parsed, dict):
                    outputs.update(parsed)
            except json.JSONDecodeError:
                pass
            break
    return ApplyResult(outputs=outputs, receipt={"kind": "script", "manual": True})


def run_script(ctx: ActionContext) -> ApplyResult:
    impl = ""
    if ctx.registry is not None:
        impl = ctx.registry.get_version(ctx.action_id, ctx.action_version)["impl"]
    if not impl.strip():
        raise ActionError("动作实现体为空（DR-10：动作 = manifest + impl）")
    return _run_python(ctx, impl) if ctx.manifest["runtime"] == "python" else _run_shell(ctx, impl)


__all__ = ["OUTPUT_MARKER", "run_script", "venv_python"]
