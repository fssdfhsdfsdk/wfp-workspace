"""G3 无损门禁语料：216 份 YAML，覆盖注释/锚点/引号/CRLF/中文/长行/空行/缩进变体。

Implements: DR-45, DR-47
"""
from __future__ import annotations

import io
from itertools import product

from ruamel.yaml import YAML

COMMENT_STYLES = ("head", "inline", "block")
QUOTES = ("double", "single", "plain")
BLANKS = (0, 1, 2)
STYLES = ("compact", "wide")   # 2 空格紧凑 / ruamel 宽缩进（映射 4、序列 key 6）
NEWLINES = ("\n", "\r\n")
ANCHORS = (False, True)
LONG = (False, True)
TRAILING = (True, False)


def _wide(text: str) -> str:
    """用 ruamel 自身规范化输出构造第二种缩进风格，保证二次 round-trip 稳定。"""
    y = YAML()
    y.preserve_quotes = True
    y.width = 100_000
    y.indent(mapping=4, sequence=6, offset=2)
    doc = y.load(text)
    buf = io.StringIO()
    y.dump(doc, buf)
    return buf.getvalue()


def build(*, style: str = "compact", quote: str = "double", crlf: bool = False,
          anchor: bool = False, comment: str = "head", blanks: int = 0,
          long_value: bool = False, trailing: bool = True) -> str:
    indent = 2
    sp = " " * indent
    name = {"double": '"演示：中文/emoji 👋"', "single": "'演示：中文/emoji 👋'",
            "plain": "演示配置"}[quote]
    path = {"double": '"conf/app.json"', "single": "'conf/app.json'",
            "plain": "conf/app.json"}[quote]
    lines: list[str] = []
    if comment == "head":
        lines += ["# 头部注释：G3 语料", "# 第二行注释"]
    elif comment == "block":
        lines += ["# ---- 块注释开始 ----", "# 说明：键序与引号不得被改写", "# ---- 块注释结束 ----"]
    lines += ["apiVersion: wfp/v1", "", "meta:", f"{sp}id: demo", f"{sp}name: {name}",
              f"{sp}version: 1.0.0"]
    if comment == "inline":
        lines[-1] += "  # 行尾注释：版本固定"
    lines += ["", "vars:"]
    if anchor:
        lines += [f"{sp}base: &base /opt/app", f"{sp}alias: *base"]
    else:
        lines += [f"{sp}app_dir: /opt/app"]
    lines += [""] * (1 + blanks)
    item_sp = " " * (indent + 2)
    child_sp = " " * (indent + 4)
    lines += ["steps:"]
    for idx in (1, 2, 3):
        lines += [f"{sp}- id: s{idx}",
                  f"{item_sp}uses: config.edit@1.0.0",
                  f"{item_sp}with:",
                  f"{child_sp}path: {path}  # 行尾注释 {idx}" if comment == "inline"
                  else f"{child_sp}path: {path}",
                  f"{child_sp}format: json",
                  f"{child_sp}value: {'x' * 300 if long_value else f'v{idx}'}"]
    text = "\n".join(lines) + "\n"
    if style == "wide":
        text = _wide(text)
    if not trailing:
        text = text.rstrip("\r\n")
    return text.replace("\n", "\r\n") if crlf else text


def build_corpus() -> list[str]:
    variants = product(COMMENT_STYLES, QUOTES, BLANKS, STYLES, NEWLINES, ANCHORS, LONG, TRAILING)
    docs: list[str] = []
    for comment, quote, blanks, style, newline, anchor, long_value, trailing in variants:
        docs.append(build(style=style, quote=quote, crlf=newline == "\r\n", anchor=anchor,
                          comment=comment, blanks=blanks, long_value=long_value, trailing=trailing))
    return docs


CORPUS = build_corpus()
assert len(CORPUS) >= 200, f"G3 阻塞门禁要求语料 >=200，实际 {len(CORPUS)}"

__all__ = ["CORPUS", "build", "build_corpus"]
