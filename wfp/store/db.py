"""SQLite 存储：WAL + 单写队列 + 前向迁移 + 迁移前自动备份。

Implements: DR-08, DR-71（并支撑 DR-10/68 的持久化）
设计依据：architecture-design.md §10、ADR-05。
"""
from __future__ import annotations

import json
import queue
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from wfp.errors import MigrationError

T = TypeVar("T")
HERE = Path(__file__).parent

# (版本号, SQL 文件名或内联 SQL)。只增不改（DR-08）。
MIGRATIONS: list[tuple[int, str]] = [(1, "schema_v1.sql")]


def utcnow() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="milliseconds")


def _statements(sql: str) -> Iterator[str]:
    """按完整语句切分（正确处理触发器 BEGIN...END 内的分号）。"""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt:
                yield stmt
            buf = ""
    if buf.strip():
        yield buf.strip()


class Store:
    """单写队列 + 多读连接（WAL）。写事务必须短（DR-71）。"""

    def __init__(self, db_path: Path, *, backup_keep: int = 5) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_keep = backup_keep
        self._local = threading.local()
        self._jobs: queue.Queue[tuple[Callable[[sqlite3.Connection], Any], Any]] = queue.Queue()
        self._closed = threading.Event()
        self._writer = threading.Thread(target=self._writer_loop, name="wfp-db-writer", daemon=True)
        self._writer.start()
        self.write_queue_depth = 0

    # ---------- 连接 ----------
    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            # 另一连接已把库切到 WAL；此处无需再次获取排他锁（DR-71 避免 locked）
            pass
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _reader(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
        return conn

    def _writer_loop(self) -> None:
        conn = self._new_conn()
        while not self._closed.is_set():
            try:
                fn, fut = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                with conn:  # 单事务：成功提交，异常回滚
                    result = fn(conn)
                fut["result"] = result
            except BaseException as exc:  # noqa: BLE001 - 透传给调用方
                fut["error"] = exc
            finally:
                fut["done"].set()
                self._jobs.task_done()
        conn.close()

    # ---------- 读写 API ----------
    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return fn(self._reader())

    def write(self, fn: Callable[[sqlite3.Connection], T], *, timeout: float = 15.0) -> T:
        fut: dict[str, Any] = {"done": threading.Event()}
        self._jobs.put((fn, fut))
        if not fut["done"].wait(timeout):
            raise TimeoutError("写队列超时（DR-71：事务应 <50ms）")
        if "error" in fut:
            raise fut["error"]
        return fut["result"]  # type: ignore[no-any-return]

    def close(self) -> None:
        self._closed.set()
        self._writer.join(timeout=3)
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------- 迁移 ----------
    def current_version(self) -> int:
        def _q(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if not row:
                return 0
            v = conn.execute("SELECT COALESCE(MAX(version),0) AS v FROM schema_version").fetchone()
            return int(v["v"])
        return self.read(_q)

    def backup(self, tag: str = "pre-migrate") -> Path | None:
        if not self.db_path.exists() or self.current_version() == 0:
            return None
        dest_dir = self.db_path.parent / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"wfp-{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        src = self._reader()
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
        self._prune_backups(dest_dir)
        return dest

    def _prune_backups(self, dest_dir: Path) -> None:
        files = sorted(dest_dir.glob("wfp-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[self.backup_keep:]:
            old.unlink(missing_ok=True)

    def migrate(self, migrations: list[tuple[int, str]] | None = None) -> list[int]:
        """前向迁移；迁移前自动备份；失败回滚并拒绝启动（DR-08）。"""
        migs = sorted(migrations if migrations is not None else MIGRATIONS)
        current = self.current_version()
        pending = [(v, s) for v, s in migs if v > current]
        if not pending:
            return []
        backup_path = self.backup()
        applied: list[int] = []

        def _apply(conn: sqlite3.Connection) -> None:
            for version, src in pending:
                sql = (HERE / src).read_text(encoding="utf-8") if src.endswith(".sql") else src
                try:
                    for stmt in _statements(sql):
                        conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO schema_version(version, applied_at) VALUES (?,?)",
                        (version, utcnow()),
                    )
                except sqlite3.Error as exc:
                    raise MigrationError(
                        f"迁移到 v{version} 失败：{exc}；数据库已回滚"
                        + (f"，备份：{backup_path}" if backup_path else ""),
                        details={"version": version, "backup": str(backup_path) if backup_path else None},
                    ) from exc
                applied.append(version)

        try:
            self.write(_apply)
        except MigrationError:
            raise
        return applied

    # ---------- 审计（DR-68，只追加） ----------
    def audit(self, actor: str, action: str, target: str | None = None,
              detail: dict[str, Any] | None = None,
              before_sha256: str | None = None, after_sha256: str | None = None) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO audit(ts,actor,action,target,before_sha256,after_sha256,detail_json)"
                " VALUES (?,?,?,?,?,?,?)",
                (utcnow(), actor, action, target, before_sha256, after_sha256,
                 json.dumps(detail or {}, ensure_ascii=False)),
            )
        self.write(_w)

    # ---------- 设置 ----------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.read(lambda c: c.execute(
            "SELECT value_json FROM setting WHERE key=?", (key,)).fetchone())
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO setting(key,value_json,updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
                " updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), utcnow()),
            )
        self.write(_w)


@contextmanager
def open_store(paths: Any) -> Iterator[Store]:
    store = Store(paths.db)
    try:
        store.migrate()
        yield store
    finally:
        store.close()


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0, interval: float = 0.02) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


__all__ = ["Store", "MIGRATIONS", "open_store", "utcnow", "wait_until", "shutil"]
