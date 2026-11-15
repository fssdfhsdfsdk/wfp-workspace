"""WinRM transport（可选增强 DR-84）。

Implements: DR-54, DR-84
pypsrp 不可用或能力缺失时显式抛 WFP-E-CAP-MISSING，不静默降级；
无 Windows 靶机时以 FakeTransport 的契约测试结案（OQ-02 决议）。
"""
from __future__ import annotations

from typing import Any

from wfp.transport.base import CapabilityError


class WinRMTransport:
    kind = "winrm"

    def __init__(self, node: dict[str, Any], *, password: str | None = None) -> None:
        try:
            import pypsrp  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise CapabilityError(
                "WFP-E-CAP-MISSING：WinRM 需要 pypsrp，且当前交付以 Linux SSH 为主（DR-84 可选）"
            ) from exc
        raise CapabilityError("WFP-E-CAP-MISSING：WinRM 真机适配器尚未启用（可用 FakeTransport 做契约测试）")


__all__ = ["WinRMTransport"]
