"""工作项契约：解析、Schema 校验、静态 lint。

Implements: DR-20, DR-21, DR-22, DR-24, DR-25, DR-26, DR-27, DR-28, DR-29, DR-51
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from wfp.contracts.manifest import load_schema
from wfp.contracts.yamlio import safe_load
from wfp.errors import ContractError

STEP_REF = re.compile(r"^([a-z][a-z0-9_.]*)@(\d+\.\d+\.\d+)$")
PIPE_REF = re.compile(r"^pipeline:([a-z][a-z0-9_-]*)@(\d+\.\d+\.\d+)$")


@dataclass
class LintIssue:
    code: str
    level: str                  # error | warning
    path: str
    message: str
    line: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "level": self.level, "path": self.path,
                "message": self.message, "line": self.line}


@dataclass
class PipelineDoc:
    text: str
    doc: dict[str, Any]
    source: Path | None = None
    pipeline_id: str = ""
    version: str = "1.0.0"
    workspace: Path = field(default_factory=Path.cwd)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def steps(self) -> list[dict[str, Any]]:
        return list(self.doc.get("steps") or [])

    @property
    def inputs_schema(self) -> dict[str, Any]:
        return self.doc.get("inputs") or {}

    @property
    def meta(self) -> dict[str, Any]:
        return self.doc.get("meta") or {}

    @property
    def defaults(self) -> dict[str, Any]:
        return self.doc.get("defaults") or {}

    @property
    def vars(self) -> dict[str, Any]:
        return self.doc.get("vars") or {}

    @property
    def allow_outside(self) -> bool:
        return bool(self.meta.get("allow_outside_workspace"))

    @classmethod
    def from_text(cls, text: str, *, source: Path | None = None,
                  workspace: Path | None = None) -> PipelineDoc:
        doc = safe_load(text)
        validate_pipeline(doc)
        meta = doc.get("meta") or {}
        if meta.get("workdir"):
            raw = Path(meta["workdir"])
            ws = raw.resolve() if raw.is_absolute() else (
                (source.parent / raw).resolve() if source else raw.resolve())
        else:
            ws = source.parent.resolve() if source else Path.cwd()
        return cls(text=text, doc=doc, source=source, pipeline_id=meta.get("id", ""),
                   version=str(meta.get("version", "1.0.0")), workspace=ws)

    @classmethod
    def from_file(cls, path: str | Path) -> PipelineDoc:
        p = Path(path).resolve()
        return cls.from_text(p.read_text(encoding="utf-8-sig"), source=p)


def _fmt(err: Any) -> str:
    return ".".join(str(p) for p in err.absolute_path) or "<root>"


def validate_pipeline(doc: dict[str, Any]) -> dict[str, Any]:
    """DR-20：schema + 语义（重复 step id、uses 格式、无界循环）。"""
    if not isinstance(doc, dict):
        raise ContractError("工作项 YAML 顶层必须是对象", path="<root>")
    schema = load_schema("pipeline.schema.json")
    errs = [{"path": _fmt(e), "message": e.message}
            for e in Draft202012Validator(schema).iter_errors(doc)]
    steps = doc.get("steps") or []
    seen: set[str] = set()
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            continue
        sid = str(st.get("id", ""))
        if sid in seen:
            errs.append({"path": f"steps[{i}].id", "message": "重复 step id（DR-20）"})
        seen.add(sid)
        uses = str(st.get("uses", ""))
        if uses and not (STEP_REF.match(uses) or PIPE_REF.match(uses)):
            errs.append({"path": f"steps[{i}].uses", "message": "必须精确引用 action@x.y.z 或 pipeline:id@x.y.z（DR-16）"})
    if errs:
        raise ContractError(f"工作项不合法（{len(errs)} 项）：{errs[0]['path']} {errs[0]['message']}",
                            path=errs[0]["path"], details=errs)
    return doc


def parse_ref(uses: str) -> tuple[str, str, str]:
    """返回 (kind, id, version)。"""
    m = STEP_REF.match(uses)
    if m:
        return "action", m.group(1), m.group(2)
    m = PIPE_REF.match(uses)
    if m:
        return "pipeline", m.group(1), m.group(2)
    raise ContractError(f"非法动作引用：{uses}", path="uses")


def dependencies(doc: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for st in doc.get("steps") or []:
        uses = str(st.get("uses", ""))
        if STEP_REF.match(uses):
            kind, aid, ver = parse_ref(uses)
            out.append({"action_id": aid, "version": ver})
    return out


def validate_inputs(doc: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """DR-27：UI 与 CLI 共用同一 JSON Schema 校验，并填充默认值。"""
    schema = doc.get("inputs") or {"type": "object"}
    merged: dict[str, Any] = {}
    for key, prop in (schema.get("properties") or {}).items():
        if isinstance(prop, dict) and "default" in prop:
            merged[key] = prop["default"]
    merged.update(inputs)
    errs = sorted(Draft202012Validator(schema).iter_errors(merged), key=_fmt)
    if errs:
        e = errs[0]
        raise ContractError(f"运行输入不合法：{e.message}", path=f"inputs.{_fmt(e)}",
                            details=[{"path": _fmt(x), "message": x.message} for x in errs])
    return merged


def _line_of(text: str, needle: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return 0


def lint_pipeline(p: PipelineDoc, *, action_exists: Callable[[str, str], bool] | None = None,
                  current_node: str | None = None,
                  resolve_pipeline: Callable[[str, str], PipelineDoc] | None = None,
                  chain: tuple[str, ...] = ()) -> list[LintIssue]:
    """≥12 条静态规则，错误阻止 apply（DR-29）。"""
    issues: list[LintIssue] = []
    doc, text, steps = p.doc, p.text, p.steps
    known = set((doc.get("inputs") or {}).get("properties", {}))
    known |= set(p.vars)
    outputs: set[str] = set()
    ids = [str(s.get("id", "")) for s in steps]

    def add(code: str, level: str, path: str, message: str, needle: str = "") -> None:
        issues.append(LintIssue(code, level, path, message, _line_of(text, needle) if needle else 0))

    if "parallel:" in text:
        add("WFP-L013", "warning", "steps", "本轮不支持并行，parallel 关键字被忽略", "parallel:")
    for i, st in enumerate(steps):
        sid = str(st.get("id", ""))
        uses = str(st.get("uses", ""))
        where = f"steps[{i}]"
        if ids.count(sid) > 1:
            add("WFP-L002", "error", f"{where}.id", f"重复 step id：{sid}", f"id: {sid}")
        if not (STEP_REF.match(uses) or PIPE_REF.match(uses)):
            add("WFP-L003", "error", f"{where}.uses", f"非法引用：{uses}", f"uses: {uses}")
        elif uses.startswith("pipeline:"):
            _, pid, _ = parse_ref(uses)
            if pid in chain or pid == p.pipeline_id:
                add("WFP-L012", "error", f"{where}.uses", "子工作项循环引用", f"uses: {uses}")
            elif resolve_pipeline:
                try:
                    child = resolve_pipeline(pid, parse_ref(uses)[2])
                    issues.extend(lint_pipeline(child, action_exists=action_exists,
                                                current_node=current_node,
                                                resolve_pipeline=resolve_pipeline,
                                                chain=(*chain, p.pipeline_id)))
                except Exception as exc:  # noqa: BLE001
                    add("WFP-L007", "error", f"{where}.uses", f"子工作项不可加载：{exc}", f"uses: {uses}")
        elif action_exists and not action_exists(*parse_ref(uses)[1:]):
            add("WFP-L001", "error", f"{where}.uses", f"未知动作或版本：{uses}", f"uses: {uses}")

        # 模板变量：仅白名单命名空间 + 已注册 outputs
        with_dump = json.dumps(st.get("with") or {}, ensure_ascii=False)
        for m in re.finditer(r"\{\{\s*([\w.]+)", with_dump):
            ref = m.group(1)
            root = ref.split(".")[0]
            if root in {"inputs", "vars", "node", "env", "item", "index"}:
                continue
            if root in outputs or root in known:
                continue
            add("WFP-L005", "error", f"{where}.with", f"未定义变量：{ref}", sid)

        loop = st.get("loop") or {}
        if loop.get("while") and "max_iterations" not in loop:
            add("WFP-L006", "warning", f"{where}.loop", "while 循环缺少 max_iterations（按默认 1000 处理）", sid)
        if st.get("when") in ("false", "False", "0"):
            add("WFP-L011", "warning", f"{where}.when", "恒假条件，步骤不可达", sid)
        if re.search(r"['\"]\.\./", with_dump) and not p.allow_outside:
            add("WFP-L008", "error", f"{where}.with", "相对路径越界（../），需显式 allow_outside_workspace", sid)
        if st.get("compensate", {}).get("mode") == "manual" or (
            uses.startswith("remote.exec") and st.get("on_error") == "compensate"
        ):
            add("WFP-L009", "warning", f"{where}.compensate", "该步骤不可自动补偿，apply 前需人工确认", sid)
        if st.get("register"):
            outputs.add(str(st["register"]))
        if "path" in (st.get("with") or {}) and str(st["with"]["path"]).startswith("remote:") and not current_node:
            add("WFP-L014", "error", f"{where}.with", "使用 remote: 路径但未选择当前目标节点", sid)

    inputs_schema = doc.get("inputs") or {}
    for req in inputs_schema.get("required", []):
        if "default" not in (inputs_schema.get("properties", {}).get(req) or {}):
            add("WFP-L010", "warning", f"inputs.{req}", f"必填输入 {req} 无默认值，运行时必须提供", req)
    if p.defaults.get("node") == "current" and not current_node:
        add("WFP-L014", "error", "defaults.node", "使用 node: current 但未设置当前目标节点（WFP-E-NODE-UNSET）",
            "node: current")
    risky = [str(s.get("id")) for s in steps if str(s.get("uses", "")).startswith("fs.delete")]
    if risky and not any(str(s.get("compensate", {}).get("mode")) == "auto" for s in steps):
        add("WFP-L015", "warning", "steps", "破坏性步骤缺少自动补偿声明，apply 前需确认", risky[0])
    return issues


def lint_errors(issues: list[LintIssue]) -> list[dict[str, Any]]:
    return [i.as_dict() for i in issues if i.level == "error"]


__all__ = ["LintIssue", "PipelineDoc", "dependencies", "lint_errors", "lint_pipeline",
           "parse_ref", "validate_inputs", "validate_pipeline"]
