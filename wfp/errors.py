"""统一错误码与异常。Implements: DR-14, DR-29（错误可定位到字段路径）。"""
from __future__ import annotations

from typing import Any


class WfpError(Exception):
    code = "WFP-E-INTERNAL"
    http_status = 500

    def __init__(self, message: str, *, path: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path:
            d["path"] = self.path
        if self.details is not None:
            d["details"] = self.details
        return d


class ContractError(WfpError):
    """manifest / 参数不满足契约（DR-11/14/17）。"""
    code = "WFP-E-CONTRACT"
    http_status = 422


class NotFound(WfpError):
    code = "WFP-E-NOT-FOUND"
    http_status = 404


class Conflict(WfpError):
    code = "WFP-E-CONFLICT"
    http_status = 409


class MigrationError(WfpError):
    code = "WFP-E-MIGRATION"
    http_status = 500


class AuthError(WfpError):
    code = "WFP-E-AUTH"
    http_status = 401


class OriginError(WfpError):
    code = "WFP-E-ORIGIN"
    http_status = 403


class ActionError(WfpError):
    """动作执行失败（非零退出 / 断言失败），DR-26/19。"""
    code = "WFP-E-ACTION"
    http_status = 500


class ConfirmRequired(Conflict):
    """apply 确认闸（DR-49）。"""
    code = "WFP-E-CONFIRM-REQUIRED"

    def __init__(self, message: str = "需先查看 PlanReport 并确认风险后再执行", **kw: Any) -> None:
        super().__init__(message, **kw)


class NodeUnset(WfpError):
    code = "WFP-E-NODE-UNSET"
    http_status = 422
