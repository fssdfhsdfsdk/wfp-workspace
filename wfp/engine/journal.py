"""NDJSON 事件流、证据与备份写入器。

Implements: DR-33, DR-38, DR-61, DR-63, DR-77, DR-78, DR-79
日志落盘优先、不写 SQLite 行（ADR-05）；证据入 artifacts 表并记 sha256。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from wfp.paths import DataPaths
from wfp.security.redactor import Redactor
from wfp.store.db import Store, utcnow

DEFAULT_LOG_QUOTA = 200 * 1024 * 1024       # DR-77 单 run 200MB
DEFAULT_ACTION_QUOTA = 100 * 1024 * 1024    # DR-77 单动作 100MB


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EventWriter:
    """结构化事件（版本化 NDJSON），支持 SSE tail 与配额截断。"""

    def __init__(self, paths: DataPaths, run_id: str, redactor: Redactor,
                 *, quota: int = DEFAULT_LOG_QUOTA, fsync: bool = False) -> None:
        self.path = paths.logs / run_id / "events.ndjson"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor
        self.quota = quota
        self.bytes_written = 0
        self.fsync = fsync
        self.seq = 0
        self._quota_warned = False
        self._fh = self.path.open("a", encoding="utf-8")
        self.run_id = run_id

    def emit(self, event_type: str, payload: dict[str, Any] | None = None, *,
             step: str | None = None, attempt: int | None = None) -> dict[str, Any]:
        self.seq += 1
        event = {
            "v": 1, "seq": self.seq, "ts": utcnow(), "run": self.run_id,
            "step": step, "attempt": attempt, "type": event_type,
            "payload": self.redactor.scrub(payload or {}),
        }
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        size = len(line.encode())
        if self.bytes_written + size > self.quota:
            if not self._quota_warned:
                self._quota_warned = True
                event["payload"] = {"message": "日志配额超限，后续输出被截断（DR-77）"}
                event["type"] = "log.quota_exceeded"
                line = json.dumps(event, ensure_ascii=False) + "\n"
                self._fh.write(line)
                self._fh.flush()
            return event
        self._fh.write(line)
        self._fh.flush()
        if self.fsync:
            import os
            os.fsync(self._fh.fileno())
        self.bytes_written += size
        return event

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    @staticmethod
    def tail(path: Path, after_seq: int = 0) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("seq", 0) > after_seq:
                out.append(ev)
        return out


class ArtifactWriter:
    """证据写入 + 前置备份 + 哈希校验还原（DR-33/34/63/79）。"""

    def __init__(self, store: Store, paths: DataPaths, run_id: str, redactor: Redactor,
                 events: EventWriter) -> None:
        self.store, self.paths, self.run_id = store, paths, run_id
        self.redactor, self.events = redactor, events
        self.dir = paths.artifacts / run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def put(self, kind: str, name: str, data: bytes | str, *, step_id: str | None = None,
            source_target: str | None = None) -> dict[str, Any]:
        raw = data.encode() if isinstance(data, str) else data
        safe = name.replace("/", "_").replace("\\", "_")
        path = self.dir / safe
        path.write_bytes(raw)
        sha, aid = sha256_bytes(raw), f"art-{uuid.uuid4().hex[:12]}"
        rel = str(path.relative_to(self.paths.root))
        self.store.write(lambda c: c.execute(
            "INSERT INTO artifact(id,run_id,step_run_id,kind,path,sha256,bytes,source_target,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, self.run_id, step_id, kind, rel, sha, len(raw), source_target, utcnow())))
        info = {"id": aid, "kind": kind, "path": rel, "sha256": sha, "bytes": len(raw),
                "source_target": source_target}
        self.events.emit("artifact.created", info, step=step_id)
        return info

    def backup_local(self, target: Path, *, step_id: str | None = None) -> dict[str, Any] | None:
        if not target.exists() or target.is_dir():
            return None
        raw = target.read_bytes()
        info = self.put("backup", f"backup-{target.name}-{uuid.uuid4().hex[:6]}{target.suffix}",
                        raw, step_id=step_id, source_target=str(target))
        receipt = {"kind": "local", "target": str(target), "sha256": info["sha256"],
                   "artifact": info["path"], "artifact_id": info["id"]}
        self.events.emit("backup.created", receipt, step=step_id)
        return receipt

    def backup_path(self, target: Path, *, step_id: str | None = None) -> dict[str, Any] | None:
        """备份文件/目录：目录先打成 zip 再内容寻址（DR-33 删除前必须备份）。"""
        import shutil
        import tempfile
        if not target.exists():
            return None
        if target.is_dir():
            with tempfile.TemporaryDirectory() as td:
                archive = shutil.make_archive(str(Path(td) / "backup"), "zip", target)
                raw = Path(archive).read_bytes()
        else:
            raw = target.read_bytes()
        info = self.put("backup", f"backup-{target.name}-{uuid.uuid4().hex[:6]}.wfpbak",
                        raw, step_id=step_id, source_target=str(target))
        receipt = {"kind": "dir" if target.is_dir() else "local", "target": str(target),
                   "sha256": info["sha256"], "artifact": info["path"], "artifact_id": info["id"],
                   "is_dir": target.is_dir(), "step_id": step_id}
        self.events.emit("backup.created", receipt, step=step_id)
        return receipt

    def backup_bytes(self, name: str, raw: bytes, *, target: str,
                     step_id: str | None = None) -> dict[str, Any]:
        info = self.put("backup", f"backup-{name}-{uuid.uuid4().hex[:6]}", raw,
                        step_id=step_id, source_target=target)
        receipt = {"kind": "bytes", "target": target, "sha256": info["sha256"],
                   "artifact": info["path"], "artifact_id": info["id"]}
        self.events.emit("backup.created", receipt, step=step_id)
        return receipt

    def restore(self, receipt: dict[str, Any]) -> bool:
        """还原备份并校验哈希；不符返回 False（DR-79 拒绝并保留证据）。"""
        rel = receipt.get("artifact")
        target = Path(receipt.get("target", ""))
        if not rel or not target.is_absolute():
            return False
        path = self.paths.root / rel
        if not path.exists():
            return False
        raw = path.read_bytes()
        if sha256_bytes(raw) != receipt.get("sha256"):
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        if receipt.get("is_dir"):
            import io
            import shutil
            import zipfile
            if target.exists():
                shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                z.extractall(target)
        else:
            target.write_bytes(raw)
        self.events.emit("backup.restored", {"target": str(target), "sha256": receipt["sha256"]},
                         step=receipt.get("step_id"))
        return True

    def diff(self, name: str, before: str, after: str, *, step_id: str | None = None) -> dict[str, Any]:
        import difflib
        text = "\n".join(difflib.unified_diff(
            before.splitlines(), after.splitlines(), fromfile="before", tofile="after", lineterm=""))
        unique = f"diff-{name}-{uuid.uuid4().hex[:8]}.txt"
        return self.put("diff", unique, self.redactor.scrub_text(text),
                        step_id=step_id, source_target=name)


__all__ = ["ArtifactWriter", "EventWriter", "sha256_bytes", "DEFAULT_ACTION_QUOTA"]
