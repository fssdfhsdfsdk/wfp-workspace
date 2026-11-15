"""导出/导入、完整性校验、清理与诊断包。

Implements: DR-05, DR-08, DR-52, DR-69, DR-73, DR-78, DR-79
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

from wfp.paths import DataPaths
from wfp.store.db import Store, utcnow


def export_all(paths: DataPaths, store: Store, out: Path, *,
               with_credentials: bool = False, with_logs: bool = True) -> Path:
    """默认不含凭据密文（DR-05/52）；zip 含 DB、真源 YAML、动作契约与证据。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    # SQLite backup API 生成一致性快照：只 copy wfp.db 会漏掉 WAL 中的已提交表（DR-05/71）
    tmp_db = store.backup(tag="export")
    if tmp_db is None:
        tmp_db = paths.backups / f"export-{uuid.uuid4().hex[:8]}.db"
        shutil.copy2(paths.db, tmp_db)
    if not with_credentials:
        conn = sqlite3.connect(tmp_db)
        conn.execute("DELETE FROM credential")
        conn.commit()
        conn.close()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp_db, "wfp.db")
        z.writestr("manifest.json", json.dumps({
            "v": 1, "exported_at": utcnow(), "schema_version": store.current_version(),
            "with_credentials": with_credentials,
        }, ensure_ascii=False, indent=2))
        for base in (paths.pipelines, paths.artifacts, *( [paths.logs] if with_logs else [])):
            if not base.exists():
                continue
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    z.write(f, str(f.relative_to(paths.root)))
        rows = store.read(lambda c: c.execute(
            "SELECT action_id,version,manifest_json,impl_path,impl_sha256 FROM action_version").fetchall())
        z.writestr("actions/manifests.json", json.dumps([dict(r) for r in rows],
                                                        ensure_ascii=False, indent=2))
        for r in rows:
            p = paths.root / r["impl_path"]
            if p.exists():
                z.write(p, str(p.relative_to(paths.root)))
    tmp_db.unlink(missing_ok=True)
    store.audit("cli", "maintenance.export", str(out), {"with_credentials": with_credentials})
    return out


def import_all(paths: DataPaths, store: Store, archive: Path) -> dict[str, Any]:
    paths.ensure()
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        for n in names:
            if n.startswith("/") or ".." in Path(n).parts:
                raise ValueError(f"导入包含非法路径：{n}")
            if n == "wfp.db":
                backup = paths.backups / f"pre-import-{utcnow().replace(':', '')}.db"
                if paths.db.exists():
                    shutil.copy2(paths.db, backup)
                # 原子替换：调用方可能仍持有旧 DB 连接，避免关闭时把旧快照写回（DR-05/71）
                tmp = paths.db.with_name(paths.db.name + ".import-tmp")
                tmp.write_bytes(z.read(n))
                for suffix in ("-wal", "-shm"):
                    paths.db.with_name(paths.db.name + suffix).unlink(missing_ok=True)
                os.replace(tmp, paths.db)
            else:
                target = paths.root / n
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(n))
    # 替换 DB 文件后原连接仍指向旧 inode，审计用短连接重新打开（DR-68/71）
    fresh = Store(paths.db)
    try:
        fresh.migrate()
        fresh.audit("cli", "maintenance.import", str(archive))
    finally:
        fresh.close()
    return {"ok": True, "files": len(names), "home": str(paths.root),
            "credentials_note": "导出默认不含凭据，请重新录入（DR-05）"}


def verify(paths: DataPaths, store: Store) -> dict[str, Any]:
    from wfp.store.runs import RunStore
    rs = RunStore(store, paths)
    bad = rs.verify_integrity()
    integrity = store.read(lambda c: c.execute("PRAGMA integrity_check").fetchone()[0])
    return {"ok": not bad and integrity == "ok", "sqlite_integrity": integrity,
            "artifacts_checked": len(store.read(lambda c: c.execute(
                "SELECT id FROM artifact").fetchall())), "problems": bad}


def diagnostics(paths: DataPaths, store: Store, run_id: str | None = None) -> Path:
    """DR-78：一键诊断包，配置脱敏 + 事件 + journal + 环境信息，绝不含凭据明文。"""
    out = paths.backups / f"wfp-diag-{uuid.uuid4().hex[:8]}.zip"
    from wfp.doctor import run_doctor
    from wfp.store.runs import RunStore
    rs = RunStore(store, paths)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doctor.json", json.dumps(run_doctor(paths, store=store, verify=True),
                                             ensure_ascii=False, indent=2))
        z.writestr("environment.json", json.dumps({
            "platform": sys.platform, "python": sys.version, "wfp_home": str(paths.root),
            "schema_version": store.current_version(), "generated_at": utcnow(),
        }, ensure_ascii=False, indent=2))
        z.writestr("settings.redacted.json", json.dumps({
            "note": "配置已脱敏；凭据表永不导出（DR-52/78)"
        }, ensure_ascii=False))
        z.writestr("audit.csv", _audit_csv(store))
        targets = [run_id] if run_id else [r["id"] for r in rs.list(limit=10)]
        for rid in targets:
            events = paths.logs / rid / "events.ndjson"
            if events.exists():
                z.write(events, f"logs/{rid}/events.ndjson")
            z.writestr(f"journal/{rid}.json", json.dumps(rs.journal_for(rid), ensure_ascii=False,
                                                          indent=2, default=str))
    store.audit("cli", "maintenance.diagnostics", str(out))
    return out


def run_evidence_zip(paths: DataPaths, store: Store, run_id: str) -> Path:
    """整 run 证据打包 zip，含清单与 sha256（DR-63/78/79）。"""
    from wfp.store.runs import RunStore
    rs = RunStore(store, paths)
    artifacts = rs.artifacts(run_id)
    out = paths.backups / f"{run_id}-evidence.zip"
    manifest: list[dict[str, Any]] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for a in artifacts:
            p = paths.root / a["path"]
            if p.exists():
                z.write(p, a["path"])
                manifest.append({k: a.get(k) for k in
                                 ("id", "kind", "path", "sha256", "bytes", "source_target")})
        events = paths.logs / run_id / "events.ndjson"
        if events.exists():
            z.write(events, f"logs/{run_id}/events.ndjson")
        z.writestr("manifest.json", json.dumps({"v": 1, "run_id": run_id,
                                                "artifacts": manifest}, ensure_ascii=False, indent=2))
    store.audit("cli", "maintenance.evidence_zip", run_id, {"count": len(manifest)})
    return out


def _audit_csv(store: Store) -> str:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["seq", "ts", "actor", "action", "target", "before_sha256", "after_sha256"])
    rows = store.read(lambda c: c.execute(
        "SELECT seq,ts,actor,action,target,before_sha256,after_sha256 FROM audit ORDER BY seq").fetchall())
    for r in rows:
        w.writerow(list(r))
    return buf.getvalue()


__all__ = ["diagnostics", "export_all", "import_all", "run_evidence_zip", "verify"]
