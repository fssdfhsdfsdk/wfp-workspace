"""凭据加密存储与解析（DPAPI / keyring / AES-GCM / 标准库兜底）。

Implements: DR-05, DR-50, DR-52, DR-53, DR-68, DR-83
INV-05：ciphertext 永不出库到 API 响应/日志/导出包；明文仅在内存。
"""
from __future__ import annotations

import builtins
import hashlib
import hmac
import json
import os
import secrets
import sys
import uuid
from typing import Any

from wfp.errors import NotFound, WfpError
from wfp.paths import DataPaths
from wfp.security.redactor import Redactor, SecretStr
from wfp.store.db import Store, utcnow


class CredentialError(WfpError):
    code = "WFP-E-CREDENTIAL"
    http_status = 500


def _dpapi_protect(data: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - 仅 Windows 验收环境
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        src = Blob(len(data), (ctypes.c_byte * len(data)).from_buffer_copy(data))
        dst = Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
            ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst))
        if not ok:
            return None
        out = bytes(bytearray(dst.pbData[:dst.cbData]))
        ctypes.windll.kernel32.LocalFree(dst.pbData)  # type: ignore[attr-defined]
        return b"DPAPI" + out
    except Exception:  # noqa: BLE001
        return None


def _dpapi_unprotect(blob: bytes) -> bytes | None:  # pragma: no cover - Windows only
    try:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = blob[5:]
        src = Blob(len(raw), (ctypes.c_byte * len(raw)).from_buffer_copy(raw))
        dst = Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
            ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst))
        if not ok:
            return None
        out = bytes(bytearray(dst.pbData[:dst.cbData]))
        ctypes.windll.kernel32.LocalFree(dst.pbData)  # type: ignore[attr-defined]
        return out
    except Exception:  # noqa: BLE001
        return None


class _Aead:
    """AES-GCM（cryptography 可用）或 scrypt keystream+HMAC 兜底，均不明文落盘。"""

    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths
        self.mode = "aesgcm"
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            self.mode = "scrypt"

    def _key(self) -> bytes:
        try:
            import keyring  # type: ignore
            raw = keyring.get_password("wfp", "master")
            if not raw:
                raw = secrets.token_hex(32)
                keyring.set_password("wfp", "master", raw)
            if len(raw) >= 64 and all(c in "0123456789abcdef" for c in raw):
                return bytes.fromhex(raw)
        except Exception:  # noqa: BLE001 - 无可用 keyring 后端时退回本地密钥文件
            pass
        self.paths.keys.mkdir(parents=True, exist_ok=True)
        key_file = self.paths.keys / "master.key"
        if not key_file.exists():
            key_file.write_bytes(secrets.token_bytes(32))
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
        return key_file.read_bytes()

    def seal(self, plain: str) -> tuple[bytes, str]:
        nonce = secrets.token_bytes(12)
        key = self._key()
        if self.mode == "aesgcm":
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return b"AGCM" + nonce + AESGCM(key).encrypt(nonce, plain.encode(), b"wfp"), "aesgcm"
        ks = hashlib.scrypt(key, salt=nonce, n=2 ** 14, r=8, p=1, dklen=len(plain.encode()) + 32)
        body = bytes(a ^ b for a, b in zip(plain.encode(), ks[:-32], strict=False))
        tag = hmac.new(ks[-32:], body, hashlib.sha256).digest()
        return b"SCRY" + nonce + tag + body, "scrypt"

    def open(self, blob: bytes) -> str:
        magic, payload = blob[:4], blob[4:]
        key = self._key()
        if magic == b"AGCM":
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM(key).decrypt(payload[:12], payload[12:], b"wfp").decode()
        if magic == b"SCRY":
            nonce, tag, body = payload[:12], payload[12:44], payload[44:]
            ks = hashlib.scrypt(key, salt=nonce, n=2 ** 14, r=8, p=1, dklen=len(body) + 32)
            if not hmac.compare_digest(tag, hmac.new(ks[-32:], body, hashlib.sha256).digest()):
                raise CredentialError("凭据校验失败（密钥不匹配或数据被篡改）")
            return bytes(a ^ b for a, b in zip(body, ks[:-32], strict=False)).decode()
        raise CredentialError("未知凭据密文格式")


class CredentialService:
    def __init__(self, store: Store, paths: DataPaths) -> None:
        self.store = store
        self.paths = paths
        self.aead = _Aead(paths)

    def put(self, label: str, plaintext: str, *, kind: str = "password", actor: str = "ui") -> dict[str, Any]:
        if not plaintext:
            raise CredentialError("凭据明文不能为空")
        blob = _dpapi_protect(plaintext.encode())
        scheme = "dpapi"
        if blob is None:
            blob, scheme = self.aead.seal(plaintext)
        cid = f"cred-{uuid.uuid4().hex[:12]}"
        meta = json.dumps({"length": len(plaintext)}, ensure_ascii=False)
        self.store.write(lambda c: c.execute(
            "INSERT INTO credential(id,kind,label,scheme,ciphertext,meta_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, kind, label, scheme, blob, meta, utcnow())))
        self.store.audit(actor, "credential.create", cid, {"label": label, "scheme": scheme})
        return {"id": cid, "label": label, "kind": kind, "scheme": scheme}

    def reveal(self, credential_id: str, *, actor: str = "run") -> str:
        row = self.store.read(lambda c: c.execute(
            "SELECT scheme,ciphertext FROM credential WHERE id=?", (credential_id,)).fetchone())
        if row is None:
            raise NotFound(f"凭据不存在：{credential_id}")
        blob = bytes(row["ciphertext"])
        if row["scheme"] == "dpapi":
            plain = _dpapi_unprotect(blob)
            if plain is None:
                raise CredentialError("DPAPI 解密失败：请确认使用创建凭据的同一 Windows 用户")
            value = plain.decode()
        else:
            value = self.aead.open(blob)
        self.store.audit(actor, "credential.read", credential_id)
        return value

    def list(self) -> builtins.list[dict[str, Any]]:
        rows = self.store.read(lambda c: c.execute(
            "SELECT id,kind,label,scheme,created_at FROM credential ORDER BY created_at").fetchall())
        return [dict(r) for r in rows]

    def delete(self, credential_id: str, *, actor: str = "ui") -> None:
        self.store.write(lambda c: c.execute("DELETE FROM credential WHERE id=?", (credential_id,)))
        self.store.audit(actor, "credential.delete", credential_id)


class SecretResolver:
    """secret('<ref>') 的解析入口；返回值立即注册到 Redactor（DR-53）。"""

    def __init__(self, credentials: CredentialService, redactor: Redactor) -> None:
        self.credentials = credentials
        self.redactor = redactor

    def get(self, ref: str) -> SecretStr:
        cid = ref
        if not cid.startswith("cred-"):
            rows = self.credentials.list()
            match = next((r for r in rows if r["label"] == ref or r["id"] == ref), None)
            if match is None:
                raise NotFound(f"凭据引用不存在：{ref}")
            cid = match["id"]
        value = self.credentials.reveal(cid)
        self.redactor.add(value)
        return SecretStr(value, ref)


__all__ = ["CredentialError", "CredentialService", "SecretResolver"]
