"""YAML 读写统一入口。

Implements: DR-20, DR-45, DR-47, DR-73
安全加载用 ruamel.yaml(typ=safe)；round-trip 编辑用 ruamel.yaml(typ=rt) 保留注释/键序/引号。
ruamel 缺失时降级到 PyYAML（安全加载），保证离线环境仍可执行工作项。
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

HAVE_RUAMEL = True
try:  # pragma: no cover - 依赖存在与否由环境决定
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
except ImportError:  # pragma: no cover
    HAVE_RUAMEL = False
    YAML = Any  # type: ignore[assignment,misc]
    CommentedMap = dict  # type: ignore[assignment,misc]
    CommentedSeq = list  # type: ignore[assignment,misc]
    import yaml as _pyyaml  # type: ignore[import-untyped]

SCHEMA_NAMES = ("action-manifest.schema.json", "pipeline.schema.json")


def safe_load(text: str) -> dict[str, Any]:
    """解析 YAML 为普通 dict（DR-20）。"""
    if HAVE_RUAMEL:
        return YAML(typ="safe").load(text) or {}
    return _pyyaml.safe_load(text) or {}  # type: ignore[no-any-return]


def safe_load_file(path: str | Path) -> dict[str, Any]:
    return safe_load(Path(path).read_text(encoding="utf-8-sig"))


def safe_dump(data: Any) -> str:
    if HAVE_RUAMEL:
        y = YAML(typ="safe")
        y.default_flow_style = False
        buf = io.StringIO()
        y.dump(data, buf)
        return buf.getvalue()
    return _pyyaml.safe_dump(data, allow_unicode=True, sort_keys=False)  # type: ignore[no-any-return]


def detect_indents(text: str) -> tuple[int, int, int]:
    """从原文推断缩进：返回 (mapping, sequence, offset)（DR-45/47）。"""
    lines = text.splitlines()
    mapping = 2
    for line in lines:
        m = re.match(r"^( +)\S", line)
        if m:
            mapping = len(m.group(1))
            break
    # 从 "- id: ..." 行推断：dash 缩进 + dash 后空格数 => 首个 key 的列号。
    # ruamel 的 sequence 参数等于该 key 列号（标准 2 空格 -> 4；宽缩进 -> 6）。
    seq_line = re.search(r"^( *)-( +)\S", text, re.M)
    offset = 2
    sequence = mapping + offset
    if seq_line:
        key_indent = len(seq_line.group(1)) + 1 + len(seq_line.group(2))
        sequence = max(mapping, key_indent)
    return mapping, sequence, offset


def make_rt(text: str) -> Any:
    """构造保留注释/引号/宽度的 round-trip YAML 实例。"""
    if not HAVE_RUAMEL:
        raise RuntimeError("round-trip 编辑需要 ruamel.yaml（pip install ruamel.yaml）")
    y = YAML()
    y.preserve_quotes = True
    y.width = max(4096, max((len(ln) for ln in text.splitlines()), default=80) + 1)
    mapping, sequence, offset = detect_indents(text)
    y.indent(mapping=mapping, sequence=sequence, offset=offset)
    return y


def dump_rt(yaml_obj: Any, doc: Any, original_text: str) -> str:
    """就地 dump，还原主导换行符与末尾换行（DR-45/47）。"""
    buf = io.StringIO()
    yaml_obj.dump(doc, buf)
    out = buf.getvalue()
    crlf = original_text.count("\r\n") > original_text.count("\n") / 2
    if crlf:
        out = out.replace("\r\n", "\n").replace("\n", "\r\n")
    if original_text and not original_text.endswith("\n") and out.endswith(("\n", "\r\n")):
        out = out.rstrip("\r\n")
    return out


__all__ = ["HAVE_RUAMEL", "CommentedMap", "CommentedSeq", "safe_load", "safe_load_file",
           "safe_dump", "make_rt", "dump_rt", "detect_indents"]
