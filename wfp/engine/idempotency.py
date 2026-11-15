"""期望态探测与幂等键（ADR-12）。

Implements: DR-32, DR-37, DR-38, DR-65
idempotency_key = sha256(action_ref | resolved_params | target | desired_state_fingerprint)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from wfp.actions.config import expected_matches
from wfp.engine.journal import sha256_bytes
from wfp.engine.types import ActionContext
from wfp.security.redactor import SecretStr


def _stable(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return "***"
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def make_key(action_ref: str, params: dict[str, Any], desired: dict[str, Any]) -> str:
    payload = {"action": action_ref, "params": _stable(params), "desired": _stable(desired)}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def probe(ctx: ActionContext, desired: dict[str, Any]) -> bool:
    """只读探测期望态是否已满足；任一明确条件不满足即 False。"""
    if not desired or desired.get("idempotent") is False or "assertion" in desired or "remote_exec" in desired:
        return False
    checks: list[bool] = []
    transport = ctx.transport

    if "edits" in desired and desired.get("path"):
        path = Path(desired["path"])
        fmt = desired.get("format", "auto")
        checks.append(path.is_file() and expected_matches(
            path.read_text(encoding="utf-8-sig"), fmt, desired["edits"]))
    elif "path" in desired and desired["path"]:
        path = Path(desired["path"])
        if desired.get("sha256"):
            checks.append(path.is_file() and sha256_bytes(path.read_bytes()) == desired["sha256"])
        elif "exists" in desired:
            checks.append(path.exists())
        elif desired.get("remote"):
            checks.append(transport is not None and
                          transport.hash(str(desired["remote"])) == desired.get("sha256"))

    if "dir" in desired and desired["dir"]:
        d = Path(desired["dir"])
        checks.append(d.is_dir() and any(x.is_file() for x in d.rglob("*")))

    if "upload" in desired:
        items = desired["upload"]
        if transport is None or not items:
            checks.append(False)
        else:
            checks.append(all(bool(item.get("sha256")) and
                              transport.hash(item["path"]) == item["sha256"] for item in items))

    if "remote" in desired and desired.get("sha256") and desired.get("format"):
        if transport is None:
            checks.append(False)
        else:
            try:
                raw = transport.get(desired["remote"])
                checks.append(expected_matches(raw.decode("utf-8-sig"), desired["format"],
                                               desired.get("edits") or []))
            except Exception:  # noqa: BLE001
                checks.append(False)

    return bool(checks) and all(checks)


__all__ = ["make_key", "probe"]
