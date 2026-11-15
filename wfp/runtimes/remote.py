"""远程动作：exec / upload / download / config_edit。

Implements: DR-19, DR-30, DR-31, DR-32, DR-33, DR-34, DR-53, DR-54, DR-55, DR-56, DR-57, DR-59
plan 阶段仅只读 get / 只读命令；apply 走备份→tmp→原子替换→hash 校验事务。
"""
from __future__ import annotations

import fnmatch
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wfp.actions.config import apply_edits, detect_format, line_diff, restore_newlines
from wfp.engine.journal import sha256_bytes
from wfp.engine.types import ActionContext, ApplyResult, PlanResult
from wfp.errors import ActionError, ContractError
from wfp.transport.base import TransportError


def _tp(ctx: ActionContext) -> Any:
    return ctx.require_transport()


def _remote(ctx: ActionContext, key: str = "path") -> str:
    v = ctx.params.get(key)
    if not v:
        raise ContractError(f"缺少参数：{key}", path=key)
    return ctx.remote_path(str(v))


def _local_files(ctx: ActionContext, local: str) -> list[Path]:
    p = ctx.local_path(local.rstrip("/"))
    if p.is_dir():
        return sorted(x for x in p.rglob("*") if x.is_file())
    return [p]


def _upload_plan_list(ctx: ActionContext) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    exclude = ctx.params.get("exclude") or []
    for item in ctx.params.get("items") or []:
        local = str(item["local"])
        remote = ctx.remote_path(str(item["remote"]))
        recursive = bool(item.get("recursive")) or local.endswith("/")
        base = ctx.local_path(local.rstrip("/"))
        if recursive and base.is_dir():
            remote_dir = remote if remote.endswith("/") else remote.rstrip("/") + "/"
            for src in sorted(base.rglob("*")):
                if not src.is_file():
                    continue
                rel = src.relative_to(base).as_posix()
                if any(fnmatch.fnmatch(rel, pat) for pat in exclude):
                    continue
                out.append((src, remote_dir + rel))
        else:
            out.append((base, remote))
    return out


# ---------------- plan（只读） ----------------
def plan_exec(ctx: ActionContext) -> PlanResult:
    command = str(ctx.params.get("command", ""))
    readonly = bool(ctx.params.get("readonly"))
    r = PlanResult(targets=[f"{ctx.node['host'] if ctx.node else 'node'}: {command}"] if ctx.node else [command],
                   no_backup_reason="远程命令副作用不可预先备份；补偿走人工清单（DR-33/34）",
                   desired_state={"remote_exec": command, "idempotent": False})
    if readonly:
        try:
            out = _tp(ctx).exec(command, timeout=min(ctx.timeout, 30), cancel=ctx.cancel)
            r.info = {"exit_code": out.exit_code, "stdout": out.stdout}
        except TransportError as exc:
            r.risks.append(str(exc))
    else:
        r.unpredictable = True
        r.risks.append("通用远程命令无法保证零副作用干跑，plan 不执行实现体（DR-30）")
    if ctx.params.get("sudo"):
        r.risks.append("需要提权（sudo -n / 管理员会话），apply 前校验权限（DR-57）")
    return r


def plan_upload(ctx: ActionContext) -> PlanResult:
    pairs = _upload_plan_list(ctx)
    r = PlanResult(targets=[remote for _, remote in pairs],
                   backup_targets=[{"kind": "remote", "path": remote} for _, remote in pairs],
                   desired_state={"upload": [{"path": remote, "sha256": sha256_bytes(src.read_bytes())
                                              if src.is_file() else None}
                                             for src, remote in pairs]})
    missing = [str(src) for src, _ in pairs if not src.is_file()]
    if missing:
        r.risks.append(f"以下本地文件暂不存在（可能由前序步骤生成）：{missing[:3]}")
    r.diff = "\n".join(f"put {src} -> {remote}" for src, remote in pairs[:50])
    return r


def plan_download(ctx: ActionContext) -> PlanResult:
    remote = _remote(ctx, "remote")
    local = ctx.local_path(str(ctx.params.get("local", "")))
    r = PlanResult(targets=[remote, str(local)], backup_targets=[],
                   desired_state={"path": str(local), "remote": remote,
                                  "sha256": _tp(ctx).hash(remote)})
    return r


def plan_config_edit(ctx: ActionContext) -> PlanResult:
    remote = _remote(ctx)
    fmt = detect_format(remote, str(ctx.params.get("format", "auto")))
    edits = list(ctx.params.get("edits") or [])
    r = PlanResult(targets=[f"node:{remote}"],
                   backup_targets=[{"kind": "remote", "path": remote}],
                   desired_state={"remote": remote, "format": fmt, "edits": edits},
                   no_backup_reason=None)
    try:
        raw = _tp(ctx).get(remote)
        original = raw.decode("utf-8-sig")
        new, changed, desired = apply_edits(original, fmt, edits)
        r.diff = line_diff(original, new, name=Path(remote).name)
        r.desired_state["sha256"] = sha256_bytes(new.encode())
        r.info.update(changed=changed, desired=desired)
    except (TransportError, UnicodeDecodeError) as exc:
        r.risks.append(f"远端文件暂不可读（可能由前序步骤生成）：{exc}")
    return r


PLAN: dict[str, Callable[[ActionContext], PlanResult]] = {
    "exec": plan_exec, "upload": plan_upload,
    "download": plan_download, "config_edit": plan_config_edit,
}


def dispatch_plan(ctx: ActionContext) -> PlanResult:
    op = ctx.manifest["entry"].get("operation", "exec")
    return PLAN[op](ctx)


# ---------------- apply ----------------
def apply_exec(ctx: ActionContext) -> ApplyResult:
    tp = _tp(ctx)
    out = tp.exec(str(ctx.params.get("command", "")), timeout=ctx.timeout,
                  sudo=bool(ctx.params.get("sudo")), cancel=ctx.cancel)
    ctx.emit_log(out.stdout, "stdout")
    ctx.emit_log(out.stderr, "stderr")
    expect = int(ctx.params.get("expect_exit", 0))
    if out.exit_code != expect:
        raise ActionError(f"远程命令返回 {out.exit_code}（期望 {expect}）：{out.stderr.strip()}")
    return ApplyResult(outputs={"exit_code": out.exit_code, "stdout": out.stdout,
                                "stderr": out.stderr},
                       receipt={"kind": "remote_exec", "manual": True})


def apply_upload(ctx: ActionContext) -> ApplyResult:
    tp = _tp(ctx)
    pairs = _upload_plan_list(ctx)
    uploaded, skipped, backups = [], [], []
    for idx, (src, remote) in enumerate(pairs, 1):
        ctx.check_cancel()
        if not src.is_file():
            raise ActionError(f"本地文件不存在：{src}")
        raw = src.read_bytes()
        want = sha256_bytes(raw)
        old_hash = tp.hash(remote)
        if old_hash == want:
            skipped.append(remote)
            ctx.emit("step.skipped", {"reason": "hash_match", "path": remote})
            continue
        if old_hash is not None:
            old = tp.get(remote)
            backups.append(ctx.artifact.backup_bytes(Path(remote).name, old, target=remote,
                                                      step_id=ctx.step_id))
        tp.put(raw, remote)
        ctx.emit("transfer.progress", {"index": idx, "total": len(pairs), "path": remote,
                                       "bytes": len(raw), "percent": round(100 * idx / max(1, len(pairs)), 1)})
        if tp.hash(remote) != want:
            raise ActionError(f"WFP-E-TRANSFER_HASH：上传后哈希不符 {remote}")
        uploaded.append(remote)
    return ApplyResult(outputs={"uploaded": uploaded, "skipped": skipped,
                                "count": len(uploaded)},
                       changed=bool(uploaded),
                       receipt={"kind": "remote", "backups": backups, "uploaded": uploaded})


def apply_download(ctx: ActionContext) -> ApplyResult:
    tp = _tp(ctx)
    remote = _remote(ctx, "remote")
    local = ctx.local_path(str(ctx.params.get("local", "")))
    raw = tp.get(remote)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(raw)
    digest = sha256_bytes(raw)
    if tp.hash(remote) not in (None, digest):
        raise ActionError(f"WFP-E-TRANSFER_HASH：下载后哈希不符 {remote}")
    ctx.emit("transfer.progress", {"path": remote, "bytes": len(raw), "percent": 100})
    return ApplyResult(outputs={"local": str(local), "sha256": digest, "bytes": len(raw)})


def apply_config_edit(ctx: ActionContext) -> ApplyResult:
    tp = _tp(ctx)
    remote = _remote(ctx)
    fmt = detect_format(remote, str(ctx.params.get("format", "auto")))
    edits = list(ctx.params.get("edits") or [])
    raw = tp.get(remote)
    original = raw.decode("utf-8-sig")
    new, changed, _ = apply_edits(original, fmt, edits)
    new = restore_newlines(new, original)
    ctx.artifact.diff(Path(remote).name, original, new, step_id=ctx.step_id)
    receipt_info = ctx.artifact.backup_bytes(Path(remote).name, raw, target=remote,
                                              step_id=ctx.step_id)
    want = sha256_bytes(new.encode("utf-8"))
    if not changed:
        return ApplyResult(outputs={"changed": False, "new_sha256": want}, changed=False,
                           receipt={"kind": "remote", "backup": receipt_info, "remote": remote})
    write = tp.atomic_write(remote, new.encode("utf-8"), backup=True)
    if write.rolled_back or tp.hash(remote) != want:
        raise ActionError(f"WFP-E-TRANSFER_HASH：远端配置写入校验失败，已回滚 {remote}")
    return ApplyResult(outputs={"changed": True, "new_sha256": want, "backup_path": write.backup_path},
                       receipt={"kind": "remote", "backup": receipt_info, "remote": remote,
                                "remote_backup_path": write.backup_path})


APPLY: dict[str, Callable[[ActionContext], ApplyResult]] = {
    "exec": apply_exec, "upload": apply_upload,
    "download": apply_download, "config_edit": apply_config_edit,
}


def dispatch(ctx: ActionContext) -> ApplyResult:
    ctx.check_cancel()
    return APPLY[ctx.manifest["entry"].get("operation", "exec")](ctx)


def dispatch_compensate(ctx: ActionContext, receipt: dict[str, Any]) -> bool:
    """远端补偿：用备份字节原子还原并校验哈希（DR-34/55/79）。"""
    tp = _tp(ctx)
    remote = receipt.get("remote")
    backup = receipt.get("backup") or {}
    if not remote or not backup.get("artifact"):
        return False
    path = ctx.artifact.paths.root / backup["artifact"]
    if not path.exists():
        return False
    raw = path.read_bytes()
    if sha256_bytes(raw) != backup.get("sha256"):
        return False
    write = tp.atomic_write(remote, raw, backup=False)
    return not write.rolled_back and tp.hash(remote) == sha256_bytes(raw)


__all__ = ["dispatch", "dispatch_compensate", "dispatch_plan"]
