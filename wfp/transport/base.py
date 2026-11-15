"""Transport 抽象接口与错误分类。

Implements: DR-54, DR-55, DR-56, DR-57, DR-58, DR-59, DR-75, DR-84
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from wfp.errors import WfpError


class TransportError(WfpError):
    code = "WFP-E-TRANSPORT"
    http_status = 502


class CapabilityError(TransportError):
    code = "WFP-E-CAP-MISSING"
    http_status = 422


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dataclass
class RemoteStat:
    path: str
    size: int = 0
    mtime: float = 0.0
    is_dir: bool = False


@dataclass
class TransferReceipt:
    path: str
    sha256: str = ""
    bytes: int = 0
    skipped: bool = False


@dataclass
class WriteReceipt:
    path: str
    sha256: str
    bytes: int
    backup_path: str | None = None
    rolled_back: bool = False


class Transport(Protocol):
    kind: str
    caps: dict[str, bool]

    def exec(self, command: str, *, timeout: float = 30.0, sudo: bool = False,
             cancel: Any | None = None) -> ExecResult: ...
    def put(self, local: Any, remote: str, *, mode: int | None = None) -> TransferReceipt: ...
    def get(self, remote: str, local: Any | None = None) -> bytes: ...
    def stat(self, remote: str) -> RemoteStat | None: ...
    def hash(self, remote: str, *, algo: str = "sha256") -> str | None: ...
    def atomic_write(self, remote: str, data: bytes, *, backup: bool = True) -> WriteReceipt: ...
    def close(self) -> None: ...


# DR-75：FakeTransport 可注入的 12 类故障
FAULT_KINDS = (
    "connect", "auth", "dns", "timeout", "disconnect", "permission",
    "partial_transfer", "hash_mismatch", "rename", "exec", "slow", "cancel",
)


def classify_transport_error(exc: Exception) -> str:
    from wfp.store.nodes import _classify
    code = _classify(str(exc))
    return code if code != "WFP-E-CONNECT" else "WFP-E-TRANSPORT"


__all__ = ["CapabilityError", "ExecResult", "FAULT_KINDS", "RemoteStat", "TransferReceipt",
           "Transport", "TransportError", "WriteReceipt", "classify_transport_error"]
