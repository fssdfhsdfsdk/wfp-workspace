"""结构化配置修改（json / yaml / ini），保留注释与键序。

Implements: DR-19, DR-30, DR-31, DR-33, DR-55, DR-32
yaml 用 ruamel round-trip 保注释；plan 只读计算 diff（ADR-11）。
"""
from __future__ import annotations

import configparser
import io
import json
import re
from pathlib import Path
from typing import Any

from wfp.contracts.yamlio import dump_rt, make_rt
from wfp.errors import ContractError


def detect_format(path: str | Path, fmt: str = "auto") -> str:
    if fmt != "auto":
        return fmt
    suffix = Path(path).suffix.lower()
    return {".json": "json", ".yaml": "yaml", ".yml": "yaml",
            ".ini": "ini", ".cfg": "ini", ".conf": "ini"}.get(suffix, "json")


def _set_path(root: Any, key: str, value: Any, op: str = "set") -> None:
    parts = str(key).lstrip("$.").split(".")
    cur = root
    for part in parts[:-1]:
        if isinstance(cur, dict):
            cur = cur.setdefault(part, {})
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            raise ContractError(f"键路径不可写：{key}", path=key)
    last = parts[-1]
    if op == "delete":
        if isinstance(cur, dict):
            cur.pop(last, None)
        return
    if op == "merge" and isinstance(cur.get(last), dict) and isinstance(value, dict):
        cur[last].update(value)
        return
    if isinstance(cur, dict):
        cur[last] = value
    elif isinstance(cur, list) and last.isdigit():
        cur[int(last)] = value
    else:
        raise ContractError(f"键路径不可写：{key}", path=key)


def _get_path(root: Any, key: str) -> Any:
    cur = root
    for part in str(key).lstrip("$.").split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def apply_edits(text: str, fmt: str, edits: list[dict[str, Any]]) -> tuple[str, bool, dict[str, Any]]:
    """返回 (新文本, 是否变化, 期望态)；纯函数，绝不写文件（DR-30）。"""
    desired = {str(e["key"]): e.get("value") for e in edits if e.get("op", "set") != "delete"}
    if fmt == "json":
        data = json.loads(text or "{}") if text.strip() else {}
        for e in edits:
            _set_path(data, e["key"], e.get("value"), e.get("op", "set"))
        indent = 4 if re.search(r'^\s{4}"', text, re.M) else 2
        new = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
        return new, new.strip() != text.strip(), desired
    if fmt in ("yaml", "yml"):
        y = make_rt(text or "{}\n")
        doc = y.load(text or "{}\n")
        for e in edits:
            _set_path(doc, e["key"], e.get("value"), e.get("op", "set"))
        new = dump_rt(y, doc, text or "{}\n")
        return new, new.strip() != text.strip(), desired
    if fmt in ("ini", "cfg", "conf"):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str  # type: ignore[method-assign,assignment]  # 保留大小写
        parser.read_string(text or "[DEFAULT]\n")
        for e in edits:
            key = str(e["key"])
            section, _, option = key.partition(".")
            if not option:
                section, option = "DEFAULT", section
            if not parser.has_section(section) and section != "DEFAULT":
                parser.add_section(section)
            if e.get("op", "set") == "delete":
                parser.remove_option(section, option)
            else:
                parser.set(section, option, str(e.get("value")))
        buf = io.StringIO()
        parser.write(buf)
        new = buf.getvalue()
        return new, new.strip() != text.strip(), desired
    raise ContractError(f"不支持的配置格式：{fmt}", path="format")


def expected_matches(text: str, fmt: str, edits: list[dict[str, Any]]) -> bool:
    """DR-32 期望态探测：所有 key 已等于目标值则跳过。"""
    try:
        _, changed, _ = apply_edits(text, fmt, edits)
        return not changed
    except Exception:  # noqa: BLE001 - 解析失败视为不满足，交由 apply 报错
        return False


def line_diff(before: str, after: str, *, name: str = "config") -> str:
    import difflib
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                          fromfile=f"{name} (before)", tofile=f"{name} (after)",
                                          lineterm=""))


def restore_newlines(text: str, original: str) -> str:
    if original.count("\r\n") > original.count("\n") / 2:
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text


__all__ = ["apply_edits", "detect_format", "expected_matches", "line_diff", "restore_newlines"]
