"""作用域、模板与受限表达式求值器。

Implements: DR-23, DR-24, DR-25, DR-29, DR-53
- 作用域优先级：step.outputs > inputs > node > vars > env(白名单)
- 模板使用 {{ ... }} + 白名单过滤器，绝不使用 eval/exec
- when/while 使用 ast 白名单求值器，单次 50ms 截止（DR-25）
"""
from __future__ import annotations

import ast
import json
import operator
import os
import re
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wfp.errors import ContractError, WfpError

TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}", re.S)
ENV_ALLOWLIST = ("WFP_HOME", "WFP_WORKSPACE", "PATH", "TEMP", "TMP", "USERPROFILE", "HOME", "OS")


class ExprError(WfpError):
    code = "WFP-L021"
    http_status = 422


class PathError(ContractError):
    code = "WFP-L020"
    http_status = 422


class Scope:
    """按优先级叠加的作用域链（DR-23）。每层是命名空间 dict。"""

    def __init__(self, *, inputs: dict[str, Any] | None = None, variables: dict[str, Any] | None = None,
                 node: dict[str, Any] | None = None, outputs: dict[str, Any] | None = None,
                 env: dict[str, str] | None = None) -> None:
        env_map = {k: os.environ.get(k, "") for k in ENV_ALLOWLIST}
        env_map.update(env or {})
        self.layers: list[dict[str, Any]] = [
            outputs or {},
            {"inputs": inputs or {}},
            {"node": node or {}},
            {"vars": variables or {}},
            {"env": env_map},
        ]

    def resolve(self, name: str) -> Any:
        # YAML 风格的小写布尔/空值（DR-25 白名单常量）
        if name in ("true", "false", "null", "none"):
            return {"true": True, "false": False, "null": None, "none": None}[name]
        for layer in self.layers:
            if name in layer:
                return layer[name]
        # 允许裸变量名按作用域优先级解析（DR-23）
        for layer in self.layers:
            for value in layer.values():
                if isinstance(value, dict) and name in value:
                    return value[name]
        raise ExprError(f"未定义变量：{name}")

    def push(self, **namespaces: Any) -> Scope:
        child = Scope()
        child.layers = [dict(namespaces), *[dict(x) for x in self.layers]]
        return child

    def register(self, key: str, outputs: dict[str, Any]) -> None:
        self.layers[0][key] = outputs

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for layer in reversed(self.layers):
            out.update(layer)
        return out


def resolve_local_path(value: str, workspace: Path, allow_outside: bool = False) -> Path:
    """DR-24：相对路径基于 workspace；默认禁止越界（WFP-L020）。"""
    base = Path(workspace).resolve()
    raw = Path(value)
    target = (raw if raw.is_absolute() else base / raw).resolve()
    if not allow_outside:
        try:
            inside = target.is_relative_to(base)
        except (ValueError, OSError) as exc:
            # Windows 跨盘符 / UNC 与本地路径不可比较时仍属越界，
            # 不能误报成"路径无法解析"（commonpath 会抛 ValueError）。
            raise PathError(f"路径越界：{value} → {target}（与 workspace 不在同一路径根；"
                            "请把 meta.workdir 设为该目录，或开启 allow_outside_workspace: true）") from exc
        if not inside:
            raise PathError(f"路径越界：{value} → {target}（需 allow_outside_workspace: true，"
                            "或把 meta.workdir 设为该目录）")
    return target


_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_CMPOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not,
}
_UNARYOPS: dict[type, Callable[[Any], Any]] = {
    ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Attribute, ast.Subscript,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.List, ast.Tuple,
    ast.Dict, ast.IfExp, ast.Slice, ast.keyword, ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Is, ast.IsNot,
)


def _json_get(obj: Any, path: str, fallback: Any = None) -> Any:
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return fallback
    return cur


class SafeEvaluator:
    """白名单 AST 求值器（DR-25）：无属性穿越、无 import、无推导式。"""

    def __init__(self, scope: Scope, *, secret_fn: Callable[[str], Any] | None = None,
                 timeout: float = 0.05) -> None:
        self.scope = scope
        self.secret_fn = secret_fn
        self.deadline = time.monotonic() + timeout
        self.steps = 0
        self.funcs: dict[str, Callable[..., Any]] = {
            "len": len, "str": str, "int": int, "float": float, "bool": bool,
            "abs": abs, "min": min, "max": max, "round": round, "sorted": sorted,
            "contains": lambda a, b: b in a,
            "startswith": lambda a, b: str(a).startswith(str(b)),
            "endswith": lambda a, b: str(a).endswith(str(b)),
            "regex_match": lambda a, p: bool(re.search(str(p), str(a))),
            "json_get": _json_get,
            "default": lambda v, d=None: d if v in (None, "", [], {}) else v,
            "basename": lambda p: Path(str(p)).name,
            "dirname": lambda p: str(Path(str(p)).parent),
            "joinpath": lambda *parts: str(Path(*[str(p) for p in parts])),
            "to_json": lambda v: json.dumps(v, ensure_ascii=False),
            "quote": lambda v: urllib.parse.quote(str(v), safe=""),
            "secret": self._secret,
        }

    def _secret(self, ref: str) -> Any:
        if self.secret_fn is None:
            raise ExprError("当前上下文不允许解析凭据引用")
        return self.secret_fn(str(ref))

    def _tick(self) -> None:
        self.steps += 1
        if self.steps > 2000 or time.monotonic() > self.deadline:
            raise ExprError("表达式求值超时（50ms 上限）")

    def evaluate(self, expression: str) -> Any:
        expr = expression.strip()
        if not expr or len(expr) > 2000:
            raise ExprError("表达式为空或过长")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise ExprError(f"表达式语法错误：{exc.msg}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, _NODES):
                raise ExprError(f"表达式含禁止语法：{type(node).__name__}")
            if isinstance(node, ast.Attribute) and (
                node.attr.startswith("_") or isinstance(node.value, ast.Call)
            ):
                raise ExprError("禁止属性穿越")
        return self._eval(tree.body)

    def _eval(self, node: ast.AST) -> Any:
        self._tick()
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.scope.resolve(node.id)
        if isinstance(node, ast.Attribute):
            base = self._eval(node.value)
            if isinstance(base, dict):
                return base.get(node.attr)
            raise ExprError(f"禁止访问属性：{node.attr}")
        if isinstance(node, ast.Subscript):
            base = self._eval(node.value)
            key = self._eval(node.slice)
            try:
                return base[key]
            except (KeyError, IndexError, TypeError) as exc:
                raise ExprError(f"下标不可用：{key!r}") from exc
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self._eval(x) for x in node.elts]
        if isinstance(node, ast.Dict):
            return {self._eval(k): self._eval(v) for k, v in zip(node.keys, node.values, strict=False)
                    if k is not None and v is not None}
        if isinstance(node, ast.BinOp):
            return _BINOPS[type(node.op)](self._eval(node.left), self._eval(node.right))
        if isinstance(node, ast.UnaryOp):
            return _UNARYOPS[type(node.op)](self._eval(node.operand))
        if isinstance(node, ast.BoolOp):
            values = [self._eval(x) for x in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self._eval(node.left)
            for op, right_node in zip(node.ops, node.comparators, strict=False):
                right = self._eval(right_node)
                if not _CMPOPS[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body) if self._eval(node.test) else self._eval(node.orelse)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in self.funcs:
                raise ExprError("禁止调用非白名单函数")
            args = [self._eval(a) for a in node.args]
            kwargs = {kw.arg: self._eval(kw.value) for kw in node.keywords if kw.arg}
            return self.funcs[node.func.id](*args, **kwargs)
        raise ExprError(f"不支持的节点：{type(node).__name__}")


def eval_expr(expression: str, scope: Scope, *, secret_fn: Callable[[str], Any] | None = None) -> Any:
    return SafeEvaluator(scope, secret_fn=secret_fn).evaluate(expression)


def _split_filters(text: str) -> list[str]:
    parts, buf, quote, depth = [], "", "", 0
    for ch in text:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
            buf += ch
        elif ch in "([{":
            depth += 1
            buf += ch
        elif ch in ")]}":
            depth -= 1
            buf += ch
        elif ch == "|" and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    parts.append(buf.strip())
    return parts


def eval_template(expression: str, scope: Scope, *, secret_fn: Callable[[str], Any] | None = None) -> Any:
    parts = _split_filters(expression)
    ev = SafeEvaluator(scope, secret_fn=secret_fn)
    value = ev.evaluate(parts[0])
    for flt in parts[1:]:
        name = flt.split("(", 1)[0].strip()
        args_expr = flt[len(name):].strip()
        args: list[Any] = []
        if args_expr.startswith("(") and args_expr.endswith(")"):
            inner = args_expr[1:-1].strip()
            if inner:
                args = [ev.evaluate(p) for p in _split_filters(inner.replace(",", "|"))]
        value = ev.funcs[name](value, *args) if name in ev.funcs else value
    return value


def render_value(value: Any, scope: Scope, *, schema: dict[str, Any] | None = None,
                 secret_fn: Callable[[str], Any] | None = None, path: str = "with") -> Any:
    """递归渲染模板；字段可声明 x-templatable: false 禁用（DR-23）。"""
    if isinstance(value, dict):
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        return {
            k: render_value(v, scope, schema=props.get(k) if isinstance(props.get(k), dict) else None,
                            secret_fn=secret_fn, path=f"{path}.{k}")
            for k, v in value.items()
        }
    if isinstance(value, list):
        item_schema = (schema or {}).get("items") if isinstance(schema, dict) else None
        return [render_value(v, scope, schema=item_schema, secret_fn=secret_fn, path=path)
                for v in value]
    if not isinstance(value, str) or "{{" not in value:
        return value
    if isinstance(schema, dict) and schema.get("x-templatable") is False:
        return value

    def _sub(m: re.Match[str]) -> str:
        obj = eval_template(m.group(1), scope, secret_fn=secret_fn)
        return "***" if hasattr(obj, "reveal") else str(obj)

    stripped = TEMPLATE_RE.sub(_sub, value)
    # 整个字符串就是单个模板时保留对象类型（凭据引用不转成字符串，DR-53）
    single = TEMPLATE_RE.fullmatch(value.strip())
    if single:
        return eval_template(single.group(1), scope, secret_fn=secret_fn)
    return stripped


def render_params(params: dict[str, Any], manifest: dict[str, Any], scope: Scope,
                  *, secret_fn: Callable[[str], Any] | None = None) -> dict[str, Any]:
    return render_value(params, scope, schema=manifest.get("params", {}), secret_fn=secret_fn)


def validate_params(params: dict[str, Any], manifest: dict[str, Any], *, path: str = "with") -> None:
    """DR-14：运行前用 manifest.params 的 JSON Schema 校验，错误定位到字段。"""
    from jsonschema import Draft202012Validator

    from wfp.security.redactor import SecretStr

    def for_schema(value: Any) -> Any:
        if isinstance(value, SecretStr):
            return "***"          # 凭据引用按字符串校验，但明文不进入校验器/错误体
        if isinstance(value, dict):
            return {k: for_schema(v) for k, v in value.items()}
        if isinstance(value, list):
            return [for_schema(v) for v in value]
        return value

    schema = manifest.get("params") or {}
    v = Draft202012Validator(schema)
    errs = sorted(v.iter_errors(for_schema(params)), key=lambda e: [str(p) for p in e.absolute_path])
    if errs:
        e = errs[0]
        where = ".".join(str(p) for p in e.absolute_path)
        raise ContractError(
            f"参数不符合契约：{e.message}",
            path=f"{path}.{where}" if where else path,
            details=[{"path": f"{path}.{'.'.join(str(p) for p in x.absolute_path)}",
                      "message": x.message} for x in errs],
        )


__all__ = ["ExprError", "PathError", "Scope", "SafeEvaluator", "eval_expr", "eval_template",
           "render_params", "render_value", "validate_params", "resolve_local_path"]
