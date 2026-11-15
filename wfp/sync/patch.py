"""双视图同步：ruamel round-trip + 最小 patch + 布局分离。

Implements: DR-16, DR-43, DR-44, DR-45, DR-46, DR-47, DR-48
铁律：YAML 文本唯一真源；任何图编辑只触及 steps[i] 及其字段；布局永不进 YAML。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wfp.contracts.yamlio import CommentedMap, CommentedSeq, dump_rt, make_rt
from wfp.errors import Conflict, ContractError


class YamlParseError(ContractError):
    pass


@dataclass
class GraphDelta:
    kind: str                       # update_param|insert_step|remove_step|move_step|wrap_container|update_uses
    step_id: str | None = None
    field: str | None = None
    value: Any = None
    after_step_id: str | None = None
    new_step: dict[str, Any] | None = None
    src: int | None = None
    dst: int | None = None
    expr: str | None = None
    container: str = "when"


@dataclass
class Patch:
    path: list[Any]
    op: str                         # replace|add|remove|move
    value: Any = None


@dataclass
class GraphModel:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    index: dict[str, str] = field(default_factory=dict)


STEP_KEY_ORDER = ("id", "name", "uses", "with", "when", "loop", "timeout_seconds",
                  "retry", "on_error", "compensate", "register", "node")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class YamlDoc:
    """真源包装：原始文本 + ruamel round-trip 树 + 冲突检测 sha。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.text_sha = _sha(text)
        self.yaml = make_rt(text)
        try:
            # ruamel 在直接解析 CRLF 文本时会吞掉别名后的空行；先归一化为 LF，
            # dump 后再由 dump_rt 还原主导换行符（DR-45/47 字节级保真）。
            self.doc = self.yaml.load(text.replace("\r\n", "\n"))
        except Exception as exc:  # noqa: BLE001
            raise YamlParseError(f"YAML 语法错误：{exc}") from exc
        if not isinstance(self.doc, dict) or "steps" not in self.doc:
            raise YamlParseError("工作项缺少 steps 列表")
        self.history: list[str] = []

    @classmethod
    def load(cls, path: str | Path) -> YamlDoc:
        return cls(Path(path).read_text(encoding="utf-8-sig"))

    def dump(self) -> str:
        return dump_rt(self.yaml, self.doc, self.text)

    @property
    def steps(self) -> Any:
        return self.doc["steps"]

    def index_of(self, step_id: str) -> int:
        for i, st in enumerate(self.steps):
            if isinstance(st, dict) and str(st.get("id")) == step_id:
                return i
        raise ContractError(f"图节点不存在：{step_id}", path="steps")

    def project(self) -> GraphModel:
        """DR-45：YAML → 图（只读投影，解析失败不清空画布由调用方处理）。"""
        model = GraphModel()
        for i, st in enumerate(self.steps):
            if not isinstance(st, dict):
                continue
            sid = str(st.get("id") or f"steps[{i}]")
            model.nodes.append({"id": sid, "index": i, "label": st.get("name") or sid,
                                "uses": str(st.get("uses") or ""),
                                "with": st.get("with") or {},
                                "when": st.get("when"), "loop": st.get("loop"),
                                "compensate": (st.get("compensate") or {}).get("mode")
                                if isinstance(st.get("compensate"), dict) else st.get("compensate")})
            model.index[sid] = f"steps[{i}]"
        for a, b in zip(model.nodes, model.nodes[1:], strict=False):
            model.edges.append({"source": a["id"], "target": b["id"]})
        return model

    def graph_edit_to_patch(self, delta: GraphDelta) -> list[Patch]:
        """DR-45 §9.2：翻译为仅触及 steps[i] 的最小 patch；无变化不产 patch。"""
        steps = self.steps
        patches: list[Patch] = []
        if delta.kind == "update_param":
            if not delta.step_id or not delta.field:
                raise ContractError("update_param 需要 step_id 与 field")
            i = self.index_of(delta.step_id)
            # 表单字段可能在 with 内，也可能是 step 级字段（name/when/...），二者都只触及 steps[i]
            step_level = delta.field in STEP_KEY_ORDER
            path: list[Any] = ["steps", i, delta.field] if step_level else [
                "steps", i, "with", delta.field]
            current = steps[i].get(delta.field) if step_level else (
                steps[i].get("with", {}).get(delta.field)
                if isinstance(steps[i].get("with"), dict) else None)
            if current != delta.value:
                patches.append(Patch(path, "replace", delta.value))
        elif delta.kind in ("update_uses", "wrap_container"):
            i = self.index_of(delta.step_id or "")
            key = "uses" if delta.kind == "update_uses" else delta.container
            new_value = delta.value if delta.kind == "update_uses" else delta.expr
            if steps[i].get(key) != new_value:
                patches.append(Patch(["steps", i, key], "replace", new_value))
        elif delta.kind == "insert_step":
            idx = self.index_of(delta.after_step_id) + 1 if delta.after_step_id else len(steps)
            patches.append(Patch(["steps", idx], "add", delta.new_step or {}))
        elif delta.kind == "remove_step":
            patches.append(Patch(["steps", self.index_of(delta.step_id or "")], "remove"))
        elif delta.kind == "move_step":
            src = delta.src if delta.src is not None else self.index_of(delta.step_id or "")
            dst = delta.dst if delta.dst is not None else 0
            if src != dst:
                patches.append(Patch(["steps", src], "move", dst))
        else:
            raise ContractError(f"未知图操作：{delta.kind}")
        for p in patches:
            if not (len(p.path) >= 2 and p.path[0] == "steps" and isinstance(p.path[1], int)):
                raise ContractError("最小 patch 铁律：只允许触及 steps[i]", path=str(p.path))
        return patches

    def apply_delta(self, delta: GraphDelta, *, expected_sha: str | None = None) -> str:
        """应用 patch 并回写；冲突时以真源为准（DR-45 §9.4）。"""
        if expected_sha and expected_sha != self.text_sha:
            raise Conflict("YAML 真源已被其他视图修改，本次图编辑放弃（DR-45）")
        patches = self.graph_edit_to_patch(delta)
        if not patches:
            return self.text            # 回声抑制：无变化不产 patch、不二次写回
        self.history.append(self.text)
        for p in patches:
            self._apply_patch(p)
        self.text = self.dump()
        self.text_sha = _sha(self.text)
        return self.text

    def _apply_patch(self, p: Patch) -> None:
        steps = self.steps
        i = p.path[1]
        if p.op == "add":
            steps.insert(i, _commented_step(p.value))
        elif p.op == "remove":
            _shift_comments(steps, i)
            del steps[i]
        elif p.op == "move":
            dst = int(p.value)
            item = steps[i]
            _shift_comments(steps, i)
            del steps[i]
            if dst > i:
                dst -= 1
            steps.insert(dst, item)
        elif p.op == "replace":
            st = steps[i]
            if len(p.path) == 4 and p.path[2] == "with":
                if "with" not in st or not isinstance(st["with"], dict):
                    st["with"] = CommentedMap()
                st["with"][p.path[3]] = p.value
            else:
                st[p.path[2]] = p.value

    def undo(self, steps: int = 1) -> str:
        """DR-44/47：撤销 N 步后回到初始文本（字节级）。"""
        for _ in range(min(steps, len(self.history))):
            self.text = self.history.pop()
            self.yaml = make_rt(self.text)
            self.doc = self.yaml.load(self.text.replace("\r\n", "\n"))
            self.text_sha = _sha(self.text)
        return self.text


def _commented_step(data: dict[str, Any]) -> Any:
    """新增节点键序按 schema 规范序，沿用 round-trip 容器类型（DR-45）。"""
    cm = CommentedMap()
    for key in STEP_KEY_ORDER:
        if key in data:
            value = data[key]
            if isinstance(value, dict):
                sub = CommentedMap()
                sub.update(value)
                cm[key] = sub
            elif isinstance(value, list):
                seq = CommentedSeq()
                seq.extend(value)
                cm[key] = seq
            else:
                cm[key] = value
    for key, value in data.items():
        if key not in cm:
            cm[key] = value
    return cm


def _shift_comments(seq: Any, index: int) -> None:
    """删除/移动 step 时搬运 comment attribute，避免注释悬挂（DR-45 写回规则 5/6）。"""
    ca = getattr(seq, "ca", None)
    if ca is None or not hasattr(ca, "items"):
        return
    items = ca.items
    items.pop(index, None)
    for key in sorted(k for k in items if k > index):
        items[key - 1] = items.pop(key)


def project_text(text: str) -> GraphModel:
    return YamlDoc(text).project()


def apply_deltas(text: str, deltas: list[GraphDelta]) -> tuple[str, YamlDoc]:
    doc = YamlDoc(text)
    for d in deltas:
        doc.apply_delta(d)
    return doc.text, doc


# ---------- 布局分离（DR-46） ----------
def load_layout(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_layout(path: str | Path, layout: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")


def prune_layout(layout: dict[str, Any], step_ids: set[str]) -> tuple[dict[str, Any], list[str]]:
    positions = layout.get("positions") or {}
    removed = [k for k in positions if k not in step_ids]
    clean = {**layout, "positions": {k: v for k, v in positions.items() if k in step_ids},
             "collapsed": [x for x in (layout.get("collapsed") or []) if x in step_ids],
             "groups": {k: [x for x in v if x in step_ids]
                        for k, v in (layout.get("groups") or {}).items()}}
    return clean, removed


def layout_step_ids(text: str) -> set[str]:
    return {n["id"] for n in project_text(text).nodes}


__all__ = ["GraphDelta", "GraphModel", "Patch", "YamlDoc", "YamlParseError", "apply_deltas",
           "load_layout", "layout_step_ids", "prune_layout", "project_text", "save_layout"]
