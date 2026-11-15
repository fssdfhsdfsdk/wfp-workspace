"""动作契约校验（入库前强制）。

Implements: DR-11, DR-17（并支撑 DR-14 的错误定位约定）
规范文件：schemas/action-manifest.schema.json（随包分发，DR-07/73）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from wfp.errors import ContractError

CAPABILITY_FIELDS = ("dry_run", "idempotent", "compensate", "needs_admin", "needs_network", "side_effects")
RUNTIME_ENTRY_KEY = {
    "builtin": "module",
    "python": "script",
    "powershell": "command",
    "cmd": "command",
    "remote": "operation",
}
IMPL_EXT = {"builtin": ".py", "python": ".py", "powershell": ".ps1", "cmd": ".cmd", "remote": ".txt"}


def _schema_dir() -> Path:
    repo = Path(__file__).resolve().parents[2] / "schemas"
    if repo.is_dir():
        return repo
    return Path(__file__).resolve().parent.parent / "_schemas"  # wheel 内置


@lru_cache(maxsize=4)
def load_schema(name: str) -> dict[str, Any]:
    path = _schema_dir() / name
    if not path.exists():
        raise ContractError(f"契约文件缺失：{path}", details={"schema": name})
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_path(err: Any) -> str:
    parts = [str(p) for p in err.absolute_path]
    return ".".join(parts) if parts else "<root>"


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """校验 manifest；失败抛 ContractError（含字段级路径清单）。

    DR-11：schema 合法性；DR-17：能力声明强制；DR-12：runtime 与 entry 一致性。
    """
    if not isinstance(manifest, dict):
        raise ContractError("manifest 必须是对象", path="<root>")

    schema = load_schema("action-manifest.schema.json")
    errors: list[dict[str, str]] = []
    for err in sorted(Draft202012Validator(schema).iter_errors(manifest), key=_fmt_path):
        errors.append({"path": _fmt_path(err), "message": err.message})

    # 语义检查 1：runtime 与 entry 必须匹配（DR-12）
    runtime = manifest.get("runtime")
    entry = manifest.get("entry") or {}
    if runtime in RUNTIME_ENTRY_KEY and isinstance(entry, dict):
        need = RUNTIME_ENTRY_KEY[runtime]
        if need not in entry:
            errors.append({"path": f"entry.{need}",
                           "message": f"runtime={runtime} 需要 entry.{need}"})

    # 语义检查 2：params 必须是合法的 JSON Schema（DR-14）
    params = manifest.get("params")
    if isinstance(params, dict):
        try:
            Draft202012Validator.check_schema(params)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": "params", "message": f"不是合法的 JSON Schema：{exc}"})

    # 语义检查 3：能力声明齐备（DR-17，schema 已约束，此处给出更友好提示）
    caps = manifest.get("capabilities")
    if isinstance(caps, dict):
        for f in CAPABILITY_FIELDS:
            if f not in caps:
                errors.append({"path": f"capabilities.{f}", "message": "能力声明缺失，拒绝入库"})

    if errors:
        uniq = {(e["path"], e["message"]): e for e in errors}
        detail = list(uniq.values())
        raise ContractError(
            f"manifest 不满足契约（{len(detail)} 项）：{detail[0]['path']} {detail[0]['message']}",
            path=detail[0]["path"], details=detail,
        )
    return manifest


def capability_badges(manifest: dict[str, Any]) -> dict[str, Any]:
    """UI 能力徽章（DR-17/41）。"""
    caps = manifest.get("capabilities", {})
    return {
        "dry_run": caps.get("dry_run"),
        "idempotent": caps.get("idempotent"),
        "compensate": caps.get("compensate"),
        "needs_admin": bool(caps.get("needs_admin")),
        "needs_network": bool(caps.get("needs_network")),
        "side_effects": caps.get("side_effects"),
    }
