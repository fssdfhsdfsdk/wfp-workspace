"""数据库迁移、只追加审计与可靠性单元测试（集成层）。

Implements: DR-08, DR-68, DR-71, DR-79
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wfp.errors import MigrationError
from wfp.paths import DataPaths
from wfp.store.db import Store


def test_dr08_failed_migration_rolls_back_and_keeps_backup(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "home").ensure()
    store = Store(paths.db)
    try:
        assert store.migrate() == [1]
        assert store.current_version() == 1
        store.audit("pytest", "action.create", "a")
        with pytest.raises(MigrationError):
            store.migrate([(1, "schema_v1.sql"), (2, "CREATE TABLE broken(;")])
        assert store.current_version() == 1, "失败迁移必须整体回滚（DR-08）"
        assert store.read(lambda c: c.execute("SELECT COUNT(*) FROM audit").fetchone()[0]) == 1
        backups = list((paths.db.parent / "backups").glob("wfp-*.db"))
        assert backups, "迁移前必须自动备份 DB"
    finally:
        store.close()


def test_dr68_audit_is_append_only(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "home").ensure()
    store = Store(paths.db)
    try:
        store.migrate()
        store.audit("cli", "settings.update", "k")
        with pytest.raises(sqlite3.IntegrityError):
            store.write(lambda c: c.execute("UPDATE audit SET actor='hacker'"))
        with pytest.raises(sqlite3.IntegrityError):
            store.write(lambda c: c.execute("DELETE FROM audit"))
    finally:
        store.close()


def test_dr71_wal_short_transactions_and_concurrent_reads(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "home").ensure()
    store = Store(paths.db)
    try:
        store.migrate()
        mode = store.read(lambda c: c.execute("PRAGMA journal_mode").fetchone()[0])
        assert str(mode).lower() == "wal"
        for i in range(50):
            store.set_setting(f"k{i}", i)
        assert store.get_setting("k49") == 49
        assert store.read(lambda c: c.execute("SELECT COUNT(*) FROM setting").fetchone()[0]) == 50
    finally:
        store.close()
