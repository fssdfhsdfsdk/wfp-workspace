"""MemoryStore：进程内 SQLite 存储替身，供无磁盘/无网络集成测试注入。

Implements: DR-71, DR-75
接口与 Store 的 read/write/audit/get_setting/set_setting/current_version 兼容，
但只持有一个 :memory: 连接，避免 WAL 多连接各自独立的问题。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from wfp.store.db import _statements, utcnow

T = TypeVar("T")
HERE = Path(__file__).parent


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        for stmt in _statements((HERE / "schema_v1.sql").read_text(encoding="utf-8")):
            self.conn.execute(stmt)
        self.conn.execute("INSERT INTO schema_version(version,applied_at) VALUES (1,?)", (utcnow(),))
        self.conn.commit()

    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        with self._lock:
            return fn(self.conn)

    def write(self, fn: Callable[[sqlite3.Connection], T], *, timeout: float = 5.0) -> T:
        with self._lock, self.conn:
            return fn(self.conn)

    def current_version(self) -> int:
        return self.read(lambda c: int(c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]))

    def audit(self, actor: str, action: str, target: str | None = None,
              detail: dict[str, Any] | None = None) -> None:
        def _w(c: sqlite3.Connection) -> None:
            c.execute("INSERT INTO audit(ts,actor,action,target,detail_json) VALUES (?,?,?,?,?)",
                      (utcnow(), actor, action, target, json.dumps(detail or {}, ensure_ascii=False)))
        self.write(_w)

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.read(lambda c: c.execute("SELECT value_json FROM setting WHERE key=?", (key,)).fetchone())
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.write(lambda c: c.execute(
            "INSERT INTO setting(key,value_json,updated_at) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), utcnow())))

    def close(self) -> None:
        with self._lock:
            self.conn.close()


__all__ = ["MemoryStore"]
