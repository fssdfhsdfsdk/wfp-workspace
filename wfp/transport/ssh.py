"""SSH transport（paramiko + SFTP），Linux 目标的 M2 主通道。

Implements: DR-54, DR-55, DR-56, DR-57, DR-58, DR-59
可选依赖 paramiko：未安装时给出 WFP-E-CAP-MISSING，而不是静默失败。
"""
from __future__ import annotations

import hashlib
import posixpath
import uuid
from pathlib import Path
from typing import Any

from wfp.transport.base import (
    CapabilityError,
    ExecResult,
    RemoteStat,
    TransferReceipt,
    TransportError,
    WriteReceipt,
)


class SSHTransport:
    kind = "ssh"

    def __init__(self, node: dict[str, Any], *, password: str | None = None,
                 key_filename: str | None = None, connect_timeout: float = 10.0) -> None:
        try:
            import paramiko  # type: ignore
        except ImportError as exc:  # pragma: no cover - 取决于可选依赖
            raise CapabilityError("SSH transport 需要 paramiko（pip install paramiko）") from exc
        self.node = node
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.client.connect(
                hostname=node["host"], port=int(node.get("port", 22)), username=node["username"],
                password=password, key_filename=key_filename, timeout=connect_timeout,
                banner_timeout=connect_timeout, auth_timeout=connect_timeout,
                allow_agent=node.get("auth_kind") == "agent", look_for_keys=False,
            )
            self.client.get_transport().set_keepalive(15)
            self.sftp = self.client.open_sftp()
        except Exception as exc:  # noqa: BLE001
            self.client.close()
            raise TransportError(f"WFP-E-CONNECT：{exc}") from exc
        self.caps = {"exec": True, "sftp": True, "hash": True, "atomic_write": True,
                     "sudo": True, "cancel": True}
        self.backups: dict[str, bytes] = {}

    def exec(self, command: str, *, timeout: float = 30.0, sudo: bool = False,
             cancel: Any | None = None) -> ExecResult:
        cmd = f"sudo -n {command}" if sudo else command
        try:
            _, stdout, stderr = self.client.exec_command(cmd, timeout=timeout, get_pty=False)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            return ExecResult(out, err, stdout.channel.recv_exit_status())
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"WFP-E-TIMEOUT：{exc}") from exc

    def put(self, local: Any, remote: str, *, mode: int | None = None) -> TransferReceipt:
        raw = local if isinstance(local, bytes) else Path(local).read_bytes()
        parent = posixpath.dirname(remote)
        if parent:
            self.exec(f"mkdir -p {parent}")
        with self.sftp.open(remote, "wb") as fh:
            fh.write(raw)
        if mode is not None:
            self.sftp.chmod(remote, mode)
        return TransferReceipt(remote, hashlib.sha256(raw).hexdigest(), len(raw))

    def get(self, remote: str, local: Any | None = None) -> bytes:
        with self.sftp.open(remote, "rb") as fh:
            raw = fh.read()
        if local is not None:
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            Path(local).write_bytes(raw)
        return raw

    def stat(self, remote: str) -> RemoteStat | None:
        try:
            st = self.sftp.stat(remote)
        except OSError:
            return None
        return RemoteStat(remote, st.st_size, st.st_mtime or 0.0, bool(st.st_mode & 0o40000))

    def hash(self, remote: str, *, algo: str = "sha256") -> str | None:
        if "windows" in self.node.get("os_kind", ""):
            out = self.exec(f"powershell -NoProfile -Command \"(Get-FileHash '{remote}' -Algorithm SHA256).Hash\"")
            return out.stdout.strip().lower() or None
        out = self.exec(f"sha256sum {remote}")
        return out.stdout.split()[0] if out.exit_code == 0 and out.stdout.strip() else None

    def atomic_write(self, remote: str, data: bytes, *, backup: bool = True) -> WriteReceipt:
        want = hashlib.sha256(data).hexdigest()
        old: bytes | None = None
        bak: str | None = None
        if backup:
            try:
                old = self.get(remote)
                bak = f"{remote}.wfp-bak-{uuid.uuid4().hex[:6]}"
                self.put(old, bak)
                self.backups[remote] = old
            except TransportError:
                old = None
        tmp = f"{remote}.wfp-tmp-{uuid.uuid4().hex[:6]}"
        self.put(data, tmp)
        if self.hash(tmp) != want:
            self.exec(f"rm -f {tmp}")
            raise TransportError("WFP-E-TRANSFER_HASH：临时文件哈希不符")
        self.exec(f"mv -f {tmp} {remote}")
        if self.hash(remote) != want:
            if old is not None:
                self.put(old, remote)
            return WriteReceipt(remote, want, len(data), bak, rolled_back=True)
        return WriteReceipt(remote, want, len(data), bak)

    def close(self) -> None:
        try:
            self.sftp.close()
        finally:
            self.client.close()


def ssh_from_node(node: dict[str, Any], secret_resolver: Any) -> SSHTransport:
    """凭据经 SecretResolver 注入，绝不拼接进命令行（DR-53）。"""
    password = None
    ref = node.get("credential_ref") or node.get("credential_id")
    if ref:
        password = secret_resolver.get(ref).reveal()
    return SSHTransport(node, password=password)


__all__ = ["SSHTransport", "ssh_from_node"]
