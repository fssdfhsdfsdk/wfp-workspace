"""内置动作实现（进程内，真·零副作用 plan + 结构化 diff）。

Implements: DR-12, DR-18, DR-19, DR-24, DR-30, DR-31, DR-32, DR-33, DR-34, DR-38, DR-63
契约见 wfp/actions/manifests.py；所有副作用只在 apply 路径，plan 只读（ADR-02/11）。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from wfp.actions.config import apply_edits, detect_format, expected_matches, line_diff, restore_newlines
from wfp.engine.journal import sha256_bytes
from wfp.engine.types import ActionContext, ApplyResult, PlanResult
from wfp.errors import ActionError, ContractError


def _sha(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def _local(ctx: ActionContext, key: str) -> Path:
    value = ctx.params.get(key)
    if not value:
        raise ContractError(f"缺少参数：{key}", path=key)
    return ctx.local_path(str(value))


def _targets(ctx: ActionContext) -> list[str]:
    out = []
    for key in ("path", "src", "dest", "archive", "local"):
        v = ctx.params.get(key)
        if isinstance(v, str) and v and key != "remote":
            out.append(str(ctx.local_path(v)))
    return out


def _backup_targets(ctx: ActionContext, keys: tuple[str, ...] = ("dest", "path", "src")) -> list[dict[str, Any]]:
    out = []
    for key in keys:
        v = ctx.params.get(key)
        if not isinstance(v, str) or not v:
            continue
        p = ctx.local_path(v)
        if p.exists():
            out.append({"kind": "local", "path": str(p), "is_dir": p.is_dir()})
    return out


# ---------------- plan（只读） ----------------
def _plan_generic(ctx: ActionContext) -> PlanResult:
    return PlanResult(targets=_targets(ctx), backup_targets=_backup_targets(ctx))


def _plan_fs_copy(ctx: ActionContext) -> PlanResult:
    src, dest = _local(ctx, "src"), _local(ctx, "dest")
    r = PlanResult(targets=[str(src), str(dest)], backup_targets=_backup_targets(ctx, ("dest",)),
                   desired_state={"path": str(dest), "sha256": _sha(src)})
    r.diff = f"copy {src} -> {dest}\n"
    return r


def _plan_fs_move(ctx: ActionContext) -> PlanResult:
    src, dest = _local(ctx, "src"), _local(ctx, "dest")
    r = PlanResult(targets=[str(src), str(dest)], backup_targets=_backup_targets(ctx, ("src", "dest")),
                   desired_state={"path": str(dest), "sha256": _sha(src), "src": str(src)})
    r.diff = f"move {src} -> {dest}\n"
    return r


def _plan_archive(ctx: ActionContext) -> PlanResult:
    archive, dest = _local(ctx, "archive"), _local(ctx, "dest")
    r = PlanResult(targets=[str(archive), str(dest)], backup_targets=_backup_targets(ctx, ("dest",)),
                   desired_state={"dir": str(dest), "archive_sha256": _sha(archive)})
    if archive.is_file():
        try:
            r.info["members"] = _archive_members(archive)
            r.diff = "\n".join(f"+ {m}" for m in r.info["members"][:50])
        except (ActionError, OSError) as exc:
            r.risks.append(f"压缩包格式暂不可识别（可能由前序步骤生成）：{exc}")
    else:
        r.risks.append(f"压缩包暂不存在（可能在本次运行前序步骤生成）：{archive}")
    return r


def _plan_pack(ctx: ActionContext) -> PlanResult:
    src, dest = _local(ctx, "src"), _local(ctx, "dest")
    return PlanResult(targets=[str(src), str(dest)], backup_targets=_backup_targets(ctx, ("dest",)),
                      desired_state={"path": str(dest), "exists": True})


def _plan_config(ctx: ActionContext) -> PlanResult:
    path = _local(ctx, "path")
    fmt = detect_format(path, str(ctx.params.get("format", "auto")))
    edits = list(ctx.params.get("edits") or [])
    original = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    if not path.is_file() and not ctx.params.get("create_if_missing"):
        r = PlanResult(targets=[str(path)], backup_targets=[],
                       desired_state={"path": str(path), "format": fmt, "edits": edits})
        r.risks.append(f"配置文件暂不存在（可能由前序步骤生成）：{path}")
        return r
    new, changed, desired = apply_edits(original, fmt, edits)
    r = PlanResult(targets=[str(path)], diff=line_diff(original, new, name=path.name),
                   backup_targets=_backup_targets(ctx, ("path",)),
                   desired_state={"path": str(path), "format": fmt, "edits": edits,
                                  "sha256": sha256_bytes(new.encode())})
    r.info.update(changed=changed, desired=desired)
    return r


def _plan_assert(ctx: ActionContext) -> PlanResult:
    r = PlanResult(targets=_targets(ctx), desired_state={"assertion": ctx.action_id})
    r.info["check"] = f"{ctx.action_id}: {ctx.params}"
    return r


def _plan_net(ctx: ActionContext) -> PlanResult:
    src = ctx.params.get("src") or ctx.params.get("url")
    dest = _local(ctx, "dest") if ctx.params.get("dest") else None
    return PlanResult(targets=[str(src), str(dest) if dest else ""],
                      backup_targets=_backup_targets(ctx, ("dest",)),
                      desired_state={"path": str(dest) if dest else None})


PLAN: dict[str, Callable[[ActionContext], PlanResult]] = {
    "archive.extract": _plan_archive, "archive.pack": _plan_pack,
    "fs.copy": _plan_fs_copy, "fs.move": _plan_fs_move, "fs.delete": _plan_generic,
    "config.edit": _plan_config,
    "net.upload": _plan_net, "net.download": _plan_net,
    "assert.file_exists": _plan_assert, "assert.checksum": _plan_assert,
    "assert.json_schema": _plan_assert,
}


def dispatch_plan(ctx: ActionContext) -> PlanResult:
    return PLAN.get(ctx.action_id, _plan_generic)(ctx)


# ---------------- apply ----------------
def _archive_members(archive: Path) -> list[str]:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            return [n for n in z.namelist() if not n.endswith("/")]
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            return [m.name for m in t.getmembers() if m.isfile()]
    raise ActionError(f"不支持的压缩包格式：{archive}")


def _apply_extract(ctx: ActionContext) -> ApplyResult:
    archive, dest = _local(ctx, "archive"), _local(ctx, "dest")
    if not archive.is_file():
        raise ActionError(f"压缩包不存在：{archive}")
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    overwrite = bool(ctx.params.get("overwrite"))
    include = ctx.params.get("include") or ["**/*"]
    exclude = ctx.params.get("exclude") or []
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                name = member.filename
                if member.is_dir() or not _selected(name, include, exclude):
                    continue
                target = (dest / name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ActionError(f"zip-slip 拒绝：{name}")
                if target.exists() and not overwrite:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(member))
                written.append(name)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            for tar_member in t.getmembers():
                if not tar_member.isfile() or not _selected(tar_member.name, include, exclude):
                    continue
                target = (dest / tar_member.name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ActionError(f"tar-slip 拒绝：{tar_member.name}")
                fh = t.extractfile(tar_member)
                if fh is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fh.read())
                written.append(tar_member.name)
    else:
        raise ActionError(f"不支持的压缩包格式：{archive}")
    return ApplyResult(outputs={"extracted_files": written, "count": len(written)},
                       changed=bool(written))


def _selected(name: str, include: list[str], exclude: list[str]) -> bool:
    return (any(fnmatch.fnmatch(name, p) or p == "**/*" for p in include)
            and not any(fnmatch.fnmatch(name, p) for p in exclude))


def _apply_pack(ctx: ActionContext) -> ApplyResult:
    src, dest = _local(ctx, "src"), _local(ctx, "dest")
    fmt = ctx.params.get("format", "zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    base = str(dest)
    for suffix in (".zip", ".tar.gz"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    out = shutil.make_archive(base, "gztar" if fmt == "tar.gz" else "zip", src)
    return ApplyResult(outputs={"archive": out, "sha256": sha256_bytes(Path(out).read_bytes())})


def _apply_copy(ctx: ActionContext) -> ApplyResult:
    src, dest = _local(ctx, "src"), _local(ctx, "dest")
    if not src.exists():
        raise ActionError(f"源文件不存在：{src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    return ApplyResult(outputs={"dest": str(dest), "sha256": _sha(dest)})


def _apply_move(ctx: ActionContext) -> ApplyResult:
    src, dest = _local(ctx, "src"), _local(ctx, "dest")
    if not src.exists():
        if dest.exists():  # 幂等重放：已完成
            return ApplyResult(outputs={"dest": str(dest), "sha256": _sha(dest)}, changed=False)
        raise ActionError(f"源路径不存在：{src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return ApplyResult(outputs={"dest": str(dest), "sha256": _sha(dest), "src": str(src)})


def _apply_delete(ctx: ActionContext) -> ApplyResult:
    path = _local(ctx, "path")
    if not path.exists():
        return ApplyResult(outputs={"deleted": False}, changed=False)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return ApplyResult(outputs={"deleted": True, "path": str(path)})


def _apply_config(ctx: ActionContext) -> ApplyResult:
    path = _local(ctx, "path")
    fmt = detect_format(path, str(ctx.params.get("format", "auto")))
    edits = list(ctx.params.get("edits") or [])
    original = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    if not path.is_file() and not ctx.params.get("create_if_missing"):
        raise ActionError(f"配置文件不存在：{path}")
    new, changed, _ = apply_edits(original, fmt, edits)
    new = restore_newlines(new, original)
    if changed:
        ctx.artifact.diff(path.name, original, new, step_id=ctx.step_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    digest = sha256_bytes(new.encode())
    return ApplyResult(outputs={"changed": changed, "new_sha256": digest,
                                "diff_artifact": f"diff-{path.name}"},
                       changed=changed)


def _apply_net(ctx: ActionContext) -> ApplyResult:
    src = str(ctx.params.get("src") or ctx.params.get("url") or "")
    dest = _local(ctx, "dest") if ctx.params.get("dest") else None
    if not dest:
        raise ContractError("缺少 dest 参数", path="dest")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.startswith(("http://", "https://")):
        urllib.request.urlretrieve(src, dest)
    else:
        source = ctx.local_path(src)
        if source.is_dir():
            shutil.copytree(source, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(source, dest)
    return ApplyResult(outputs={"dest": str(dest), "sha256": _sha(dest)})


def _apply_assert_exists(ctx: ActionContext) -> ApplyResult:
    path = _local(ctx, "path")
    if not path.exists():
        raise ActionError(f"WFP-E-ASSERT：配置文件不存在，立即终止（IR-15）：{path}")
    return ApplyResult(outputs={"exists": True, "path": str(path)}, changed=False)


def _apply_assert_checksum(ctx: ActionContext) -> ApplyResult:
    path = _local(ctx, "path")
    expect = str(ctx.params.get("expect_sha256") or "")
    algo = str(ctx.params.get("algo", "sha256"))
    if not path.is_file():
        raise ActionError(f"待校验文件不存在：{path}")
    raw = path.read_bytes()
    actual = hashlib.md5(raw).hexdigest() if algo == "md5" else sha256_bytes(raw)
    if expect and expect != actual:
        raise ActionError(f"校验失败：期望 {expect}，实际 {actual}")
    return ApplyResult(outputs={"sha256": actual}, changed=False)


def _apply_assert_schema(ctx: ActionContext) -> ApplyResult:
    path = _local(ctx, "path")
    data = json.loads(path.read_text(encoding="utf-8"))
    errs = list(Draft202012Validator(ctx.params.get("schema") or {}).iter_errors(data))
    if errs:
        raise ActionError(f"JSON Schema 校验失败：{errs[0].message}")
    return ApplyResult(outputs={"valid": True}, changed=False)


def _apply_script(ctx: ActionContext) -> ApplyResult:
    from wfp.runtimes.script import run_script
    return run_script(ctx)


APPLY: dict[str, Callable[[ActionContext], ApplyResult]] = {
    "archive.extract": _apply_extract, "archive.pack": _apply_pack,
    "fs.copy": _apply_copy, "fs.move": _apply_move, "fs.delete": _apply_delete,
    "config.edit": _apply_config,
    "net.upload": _apply_net, "net.download": _apply_net,
    "assert.file_exists": _apply_assert_exists, "assert.checksum": _apply_assert_checksum,
    "assert.json_schema": _apply_assert_schema, "script.local": _apply_script,
}


def dispatch(ctx: ActionContext) -> ApplyResult:
    fn = APPLY.get(ctx.action_id)
    if fn is None:
        raise ActionError(f"未实现的内置动作：{ctx.action_id}")
    ctx.check_cancel()
    return fn(ctx)


# ---------------- compensate（DR-34） ----------------
def _compensate_move(ctx: ActionContext, receipt: dict[str, Any]) -> bool:
    src = ctx.local_path(str(ctx.params.get("src", "")))
    dest = ctx.local_path(str(ctx.params.get("dest", "")))
    if dest.exists() and not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(src))
        return True
    return False


COMPENSATE: dict[str, Callable[[ActionContext, dict[str, Any]], bool]] = {
    "fs.move": _compensate_move,
}


def dispatch_compensate(ctx: ActionContext, receipt: dict[str, Any]) -> bool:
    fn = COMPENSATE.get(ctx.action_id)
    return bool(fn(ctx, receipt)) if fn else False


__all__ = ["dispatch", "dispatch_compensate", "dispatch_plan", "expected_matches"]
