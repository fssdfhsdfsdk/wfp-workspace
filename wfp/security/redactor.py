"""凭据最小暴露与全链路脱敏。

Implements: DR-52, DR-53, DR-68, DR-78
Redactor 在事件、证据、异常/审计三处强制过滤（含 URL/base64 变体）。
"""
from __future__ import annotations

import base64
import json
import urllib.parse
from typing import Any


class SecretStr:
    """凭据引用对象：str() 恒为 ***，仅注入点可 reveal()（DR-53）。"""

    __slots__ = ("_value", "ref")

    def __init__(self, value: str, ref: str = "") -> None:
        self._value = value
        self.ref = ref

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "***"

    def __repr__(self) -> str:
        return "SecretStr(***)"

    def __bool__(self) -> bool:
        return bool(self._value)


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, value: str | SecretStr) -> None:
        plain = value.reveal() if isinstance(value, SecretStr) else str(value)
        if plain and plain != "***":
            self._secrets.add(plain)

    @property
    def variants(self) -> list[str]:
        out: list[str] = []
        for s in self._secrets:
            out.extend([s, urllib.parse.quote(s, safe=""), urllib.parse.quote_plus(s, safe="")])
            try:
                out.append(base64.b64encode(s.encode()).decode())
                out.append(base64.urlsafe_b64encode(s.encode()).decode().rstrip("="))
            except Exception:  # noqa: BLE001
                pass
        return sorted({v for v in out if v}, key=len, reverse=True)

    def scrub_text(self, text: str) -> str:
        for v in self.variants:
            text = text.replace(v, "***")
        return text

    def scrub(self, obj: Any) -> Any:
        if isinstance(obj, SecretStr):
            self.add(obj)
            return "***"
        if isinstance(obj, str):
            return self.scrub_text(obj)
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.scrub(v) for v in obj]
        if isinstance(obj, (int, float, bool)) or obj is None:
            return obj
        return self.scrub_text(str(obj))

    def dumps(self, obj: Any) -> str:
        return json.dumps(self.scrub(obj), ensure_ascii=False, default=str)


__all__ = ["Redactor", "SecretStr"]
