"""执行引擎共享数据结构（依赖注入点，DR-75）。

Implements: DR-12, DR-23, DR-24, DR-30, DR-31, DR-33, DR-34, DR-38, DR-75
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wfp.engine.cancel import CancelToken


@dataclass
class PlanResult:
    """只读 plan 的产物；不得产生任何副作用（DR-30/31）。"""
    targets: list[str] = field(default_factory=list)
    diff: str = ""
    risks: list[str] = field(default_factory=list)
    unpredictable: bool = False
    desired_state: dict[str, Any] = field(default_factory=dict)
    backup_targets: list[dict[str, Any]] = field(default_factory=list)
    no_backup_reason: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApplyResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    changed: bool = True
    receipt: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StepOutcome:
    status: str = "succeeded"
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    attempt: int = 1


@dataclass
class ActionContext:
    """动作上下文：适配器唯一可见的世界（DR-12/23/24/53/75）。"""
    params: dict[str, Any]
    workspace: Path
    run_id: str
    step_id: str
    iteration: int
    emit: Callable[[str, dict[str, Any]], None]
    artifact: Any                 # ArtifactWriter
    cancel: CancelToken
    deadline: float
    transport: Any | None         # Transport
    secrets: Any                  # SecretResolver
    node: dict[str, Any] | None
    redactor: Any                 # Redactor
    manifest: dict[str, Any]
    action_id: str
    action_version: str
    registry: Any | None = None
    allow_outside: bool = False
    timeout: float = 300.0

    def local_path(self, value: str, *, allow_outside: bool | None = None) -> Path:
        """DR-24：相对路径基于 workspace，默认禁止越界。"""
        from wfp.engine.expr import resolve_local_path
        flag = self.allow_outside if allow_outside is None else allow_outside
        return resolve_local_path(value, self.workspace, flag)

    def remote_path(self, value: str) -> str:
        """DR-24：远端路径显式 remote: 前缀或 node 上下文。"""
        if value.startswith("remote:"):
            return value[len("remote:"):]
        if value.startswith("node:"):
            return value[len("node:"):]
        if not value.startswith("/") and self.node:
            base = (self.node.get("default_workdir") or "/").rstrip("/")
            return f"{base}/{value}"
        return value

    def check_cancel(self) -> None:
        self.cancel.check()

    def emit_log(self, text: str, stream: str = "stdout") -> None:
        if text:
            self.emit(f"step.{stream}", {"text": text})

    def require_transport(self) -> Any:
        if self.transport is None:
            raise RuntimeError("WFP-E-TRANSPORT: 未配置远程通道")
        return self.transport


@dataclass
class PlanItem:
    step_id: str
    action_ref: str
    status: str
    targets: list[str] = field(default_factory=list)
    diff: str = ""
    risks: list[str] = field(default_factory=list)
    unpredictable: bool = False
    needs_admin: bool = False
    needs_network: bool = False
    compensate: str = "none"
    backups: list[str] = field(default_factory=list)
    no_backup_reason: str | None = None


__all__ = ["ActionContext", "ApplyResult", "PlanItem", "PlanResult", "StepOutcome"]
