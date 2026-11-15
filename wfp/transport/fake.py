"""FakeTransport：离线可注入故障的本地文件系统传输实现。

Implements: DR-54, DR-55, DR-56, DR-58, DR-59, DR-75, DR-84
把"远端绝对路径"映射到一个本地目录，使 E2E 场景无需真实靶机即可完整验证。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from wfp.engine.cancel import Cancelled
from wfp.transport.base import ExecResult, RemoteStat, TransferReceipt, TransportError, WriteReceipt


class FakeTransport:
    kind = "fake"

    def __init__(self, root: str | Path, *, faults: dict[str, Any] | None = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.faults = dict(faults or {})
        self.closed = False
        self.exec_log: list[str] = []
        self.caps = {"exec": True, "sftp": True, "hash": True, "atomic_write": True,
                     "sudo": True, "cancel": True}
        self.backups: dict[str, bytes] = {}
        if self.faults.get("connect"):
            raise TransportError("WFP-E-REFUSED：模拟连接被拒绝", details={"fault": "connect"})
        if self.faults.get("dns"):
            raise TransportError("WFP-E-DNS：模拟 DNS 解析失败", details={"fault": "dns"})
        if self.faults.get("auth"):
            raise TransportError("WFP-E-AUTH：模拟认证失败", details={"fault": "auth"})

    # ---------- 故障注入 ----------
    def fault(self, kind: str, enabled: bool = True) -> None:
        self.faults[kind] = enabled

    def _check(self, op: str) -> None:
        if self.closed:
            raise TransportError("WFP-E-DISCONNECT：通道已关闭")
        if self.faults.get("disconnect"):
            self.closed = True
            raise TransportError("WFP-E-DISCONNECT：模拟通道断开")
        if self.faults.get("timeout"):
            raise TransportError("WFP-E-TIMEOUT：模拟连接超时")
        if self.faults.get("cancel"):
            raise Cancelled("WFP-E-CANCEL：模拟远端取消")
        if self.faults.get("permission") and op in ("exec", "put", "get", "atomic_write"):
            raise TransportError("WFP-E-PRIV：模拟权限不足")
        if self.faults.get(op):
            raise TransportError(f"WFP-E-{op.upper()}：模拟 {op} 故障")
        if self.faults.get("slow"):
            time.sleep(0.05)

    # ---------- 路径 ----------
    def _p(self, remote: str) -> Path:
        candidate = (self.root / str(remote).lstrip("/")).resolve()
        if os.path.commonpath([str(self.root), str(candidate)]) != str(self.root):
            raise TransportError(f"WFP-E-PATH：远端路径越界 {remote}")
        return candidate

    @staticmethod
    def _sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    # ---------- 接口 ----------
    def exec(self, command: str, *, timeout: float = 30.0, sudo: bool = False,
             cancel: Any | None = None) -> ExecResult:
        self._check("exec")
        cmd = command.strip()
        self.exec_log.append(cmd)
        if sudo and self.faults.get("sudo"):
            return ExecResult("", "WFP-E-PRIV: sudo 需要口令", 1)
        cmd = re.sub(r"^sudo\s+-n\s+", "", cmd)
        if " || " in cmd:  # 支持 E2E-01 的 test || mkdir 惯用写法
            left, right = cmd.split(" || ", 1)
            first = self.exec(left, timeout=timeout, cancel=cancel)
            return first if first.exit_code == 0 else self.exec(right, timeout=timeout, cancel=cancel)
        if " && " in cmd:
            left, right = cmd.split(" && ", 1)
            first = self.exec(left, timeout=timeout, cancel=cancel)
            return self.exec(right, timeout=timeout, cancel=cancel) if first.exit_code == 0 else first
        if cmd.startswith("exit "):
            return ExecResult("", "", int(cmd.split()[1]))
        m = re.match(r"sleep\s+(\d+(?:\.\d+)?)", cmd)
        if m:
            waited = min(float(m.group(1)), max(0.01, timeout))
            if cancel is not None and cancel.wait(min(waited, 0.5)):
                raise Cancelled("远端 sleep 已取消")
            time.sleep(min(waited, 0.5))
            return ExecResult("", "", 0)
        if cmd.startswith("test -d "):
            return ExecResult("", "", 0 if self._p(cmd[8:]).is_dir() else 1)
        if cmd.startswith("test -s "):
            p = self._p(cmd[8:])
            return ExecResult("", "", 0 if p.is_file() and p.stat().st_size else 1)
        if cmd.startswith("mkdir -p "):
            self._p(cmd[9:]).mkdir(parents=True, exist_ok=True)
            return ExecResult("", "", 0)
        if cmd.startswith("sha256sum "):
            p = self._p(cmd[10:].split()[0])
            if not p.is_file():
                return ExecResult("", "no such file", 1)
            return ExecResult(f"{self._sha(p.read_bytes())}  {p.name}\n", "", 0)
        if cmd.startswith("cat "):
            p = self._p(cmd[4:].split()[0])
            return ExecResult(p.read_text(errors="replace") if p.is_file() else "", "", 0 if p.is_file() else 1)
        if cmd.startswith("echo "):
            return ExecResult(cmd[5:].strip().strip("'\"") + "\n", "", 0)
        if cmd.startswith("mv "):
            parts = cmd.split()
            if len(parts) >= 3:
                src, dst = self._p(parts[1]), self._p(parts[2])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            return ExecResult("", "", 0)
        if cmd.startswith("cp "):
            parts = cmd.split()
            if len(parts) >= 3:
                src, dst = self._p(parts[1]), self._p(parts[2])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            return ExecResult("", "", 0)
        if cmd.startswith("rm -f "):
            self._p(cmd[6:]).unlink(missing_ok=True)
            return ExecResult("", "", 0)
        if cmd == "false":
            return ExecResult("", "", 1)
        if cmd in ("true", "whoami"):
            return ExecResult("root\n" if cmd == "whoami" else "", "", 0)
        if cmd.startswith(("systemctl ", "curl ", "python3 ", "powershell ", "Get-FileHash")):
            return ExecResult(f"fake-ok: {cmd}\n", "", 0)
        return ExecResult(f"fake: {cmd}\n", "", 0)

    def put(self, local: Any, remote: str, *, mode: int | None = None) -> TransferReceipt:
        self._check("put")
        raw = local if isinstance(local, bytes) else Path(local).read_bytes()
        if self.faults.get("partial_transfer"):
            raw = raw[: max(0, len(raw) // 2)]
            self._write(remote, raw)
            raise TransportError("WFP-E-TRANSFER_HASH：模拟半传输")
        self._write(remote, raw)
        return TransferReceipt(remote, self._sha(raw), len(raw))

    def _write(self, remote: str, raw: bytes) -> Path:
        p = self._p(remote)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p

    def get(self, remote: str, local: Any | None = None) -> bytes:
        self._check("get")
        p = self._p(remote)
        if not p.is_file():
            raise TransportError(f"WFP-E-NOT-FOUND：远端文件不存在 {remote}")
        raw = p.read_bytes()
        if local is not None:
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            Path(local).write_bytes(raw)
        return raw

    def stat(self, remote: str) -> RemoteStat | None:
        self._check("stat")
        p = self._p(remote)
        if not p.exists():
            return None
        return RemoteStat(remote, p.stat().st_size, p.stat().st_mtime, p.is_dir())

    def hash(self, remote: str, *, algo: str = "sha256") -> str | None:
        self._check("hash")
        p = self._p(remote)
        if not p.is_file():
            return None
        raw = p.read_bytes()
        digest = hashlib.md5(raw).hexdigest() if algo == "md5" else self._sha(raw)
        if self.faults.get("hash_mismatch"):
            return "0" * 64
        return digest

    def atomic_write(self, remote: str, data: bytes, *, backup: bool = True) -> WriteReceipt:
        """DR-55：备份 → tmp → rename → hash 校验 → 失败回滚。"""
        self._check("atomic_write")
        p = self._p(remote)
        old = p.read_bytes() if p.is_file() else None
        bak: str | None = None
        if backup and old is not None:
            bak = f"{remote}.wfp-bak-{uuid.uuid4().hex[:6]}"
            self._write(bak, old)
            self.backups[remote] = old
        tmp = f"{remote}.wfp-tmp-{uuid.uuid4().hex[:6]}"
        self._write(tmp, data)
        if self._sha(self._p(tmp).read_bytes()) != self._sha(data):
            self._p(tmp).unlink(missing_ok=True)
            raise TransportError("WFP-E-TRANSFER_HASH：临时文件哈希不符，已清理")
        if self.faults.get("rename"):
            self._p(tmp).unlink(missing_ok=True)
            raise TransportError("WFP-E-RENAME：原子替换前注入故障，远端保持原样")
        os.replace(self._p(tmp), p)
        if self.hash(remote) != self._sha(data):
            if old is not None:
                p.write_bytes(old)
            return WriteReceipt(remote, self._sha(data), len(data), bak, rolled_back=True)
        return WriteReceipt(remote, self._sha(data), len(data), bak)

    def rollback(self, remote: str) -> bool:
        if remote not in self.backups:
            return False
        self._p(remote).parent.mkdir(parents=True, exist_ok=True)
        self._p(remote).write_bytes(self.backups[remote])
        return True

    def close(self) -> None:
        self.closed = True


__all__ = ["FakeTransport"]
