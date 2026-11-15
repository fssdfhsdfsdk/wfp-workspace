"""Run / StepRun / journal / artifact / 锁 的数据访问层。

Implements: DR-32, DR-37, DR-38, DR-39, DR-65, DR-66, DR-68, DR-69, DR-71, DR-79
"""
from __future__ import annotations

import builtins
import json
import os
import time
from typing import Any

from wfp.paths import DataPaths
from wfp.store.db import Store, utcnow

RUN_TERMINAL = {"succeeded", "succeeded_with_warnings", "failed", "cancelled", "compensated",
                "needs_attention", "planned", "plan_failed", "interrupted"}


class RunStore:
    def __init__(self, store: Store, paths: DataPaths) -> None:
        self.store, self.paths = store, paths

    # ---------- run ----------
    def create(self, run_id: str, *, pipeline_id: str, version: str, phase: str,
               trigger: str, inputs: dict[str, Any], node_snapshot: dict[str, Any] | None,
               action_versions: dict[str, str]) -> None:
        self.store.write(lambda c: c.execute(
            "INSERT INTO run(id,pipeline_id,pipeline_version,phase,status,trigger,inputs_json,"
            "node_snapshot_json,action_versions_json,runner_pid,heartbeat_at,started_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, pipeline_id, version, phase, "created", trigger,
             json.dumps(inputs, ensure_ascii=False, default=str),
             json.dumps(node_snapshot or {}, ensure_ascii=False, default=str),
             json.dumps(action_versions, ensure_ascii=False), os.getpid(), utcnow(), utcnow())))

    def set_status(self, run_id: str, status: str, *, error_code: str | None = None,
                   plan_report_path: str | None = None, confirm: dict[str, Any] | None = None,
                   finished: bool = False) -> None:
        def _w(c: Any) -> None:
            c.execute("UPDATE run SET status=?,error_code=COALESCE(?,error_code),"
                      "plan_report_path=COALESCE(?,plan_report_path),confirm_json=COALESCE(?,confirm_json),"
                      "heartbeat_at=?,finished_at=CASE WHEN ? THEN ? ELSE finished_at END WHERE id=?",
                      (status, error_code, plan_report_path,
                       json.dumps(confirm, ensure_ascii=False) if confirm else None,
                       utcnow(), 1 if finished else 0, utcnow() if finished else None, run_id))
        self.store.write(_w)

    def heartbeat(self, run_id: str) -> None:
        self.store.write(lambda c: c.execute(
            "UPDATE run SET heartbeat_at=? WHERE id=?", (utcnow(), run_id)))

    def get(self, run_id: str) -> dict[str, Any]:
        row = self.store.read(lambda c: c.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone())
        if row is None:
            from wfp.errors import NotFound
            raise NotFound(f"运行不存在：{run_id}")
        d = dict(row)
        for k in ("inputs_json", "node_snapshot_json", "action_versions_json", "confirm_json"):
            d[k.replace("_json", "")] = json.loads(d.pop(k) or ("{}" if k != "confirm_json" else "{}"))
        return d

    def list(self, *, pipeline_id: str | None = None, status: str | None = None,
             limit: int = 200) -> builtins.list[dict[str, Any]]:
        sql: str = "SELECT * FROM run WHERE 1=1"
        args: list[Any] = []
        if pipeline_id:
            sql += " AND pipeline_id=?"
            args.append(pipeline_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        rows = self.store.read(lambda c: c.execute(sql, args).fetchall())
        return [dict(r) for r in rows]

    def delete(self, run_id: str, *, actor: str = "ui") -> dict[str, Any]:
        """运行历史删除：仅允许终态 run；级联清理 DB 行与日志/证据文件（DR-65/69）。"""
        import shutil

        from wfp.errors import Conflict

        rec = self.get(run_id)
        if rec["status"] not in RUN_TERMINAL:
            raise Conflict(f"运行尚未结束（{rec['status']}），请等待完成或取消后再删除", path=run_id)

        def _w(c: Any) -> None:
            c.execute("DELETE FROM step_run WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM artifact WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM journal WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM singleton_lock WHERE holder_run=?", (run_id,))
            c.execute("DELETE FROM run WHERE id=?", (run_id,))
        self.store.write(_w)
        for d in (self.paths.logs / run_id, self.paths.artifacts / run_id):
            shutil.rmtree(d, ignore_errors=True)
        self.store.audit(actor, "run.delete", run_id, {"status": rec["status"]})
        return {"id": run_id, "deleted": True}

    def scan_interrupted(self) -> builtins.list[str]:
        """DR-37/39：崩溃后未完成的 run 标 interrupted，并释放陈旧锁。"""
        rows = self.store.read(lambda c: c.execute(
            "SELECT id,runner_pid,heartbeat_at FROM run WHERE status IN ('created','planning','running','cancelling','paused')").fetchall())
        out = []
        for r in rows:
            out.append(r["id"])
            self.set_status(r["id"], "interrupted", error_code="WFP-E-INTERRUPTED", finished=True)
        sql = ("DELETE FROM singleton_lock WHERE holder_run IN ({})".format(
            ",".join("?" for _ in out)) if out else "DELETE FROM singleton_lock WHERE 1=0")
        self.store.write(lambda c: c.execute(sql, out))
        return out

    # ---------- step_run ----------
    def add_step(self, step_id: str, *, run_id: str, step_key: str, iteration: int, attempt: int,
                 action_id: str, action_version: str, status: str,
                 skip_reason: str | None = None, idempotency_key: str | None = None,
                 resolved_sha: str | None = None, outputs: dict[str, Any] | None = None,
                 exit_code: int | None = None) -> None:
        self.store.write(lambda c: c.execute(
            "INSERT OR REPLACE INTO step_run(id,run_id,step_id,iteration,attempt,action_id,action_version,"
            "status,skip_reason,idempotency_key,resolved_params_sha256,exit_code,outputs_json,started_at,finished_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (step_id, run_id, step_key, iteration, attempt, action_id, action_version, status,
             skip_reason, idempotency_key, resolved_sha, exit_code,
             json.dumps(outputs or {}, ensure_ascii=False, default=str),
             utcnow(), utcnow() if status in {"succeeded", "failed", "skipped_condition",
                                              "skipped_idempotent", "cancelled"} else None)))

    def steps(self, run_id: str) -> builtins.list[dict[str, Any]]:
        rows = self.store.read(lambda c: c.execute(
            "SELECT * FROM step_run WHERE run_id=? ORDER BY id", (run_id,)).fetchall())
        return [dict(r) for r in rows]

    # ---------- journal（只追加，DR-38/79） ----------
    def journal(self, run_id: str, event: str, payload: dict[str, Any],
                step_run_id: str | None = None) -> None:
        self.store.write(lambda c: c.execute(
            "INSERT INTO journal(run_id,step_run_id,event,payload_json,ts) VALUES (?,?,?,?,?)",
            (run_id, step_run_id, event, json.dumps(payload, ensure_ascii=False, default=str), utcnow())))

    def journal_for(self, run_id: str, *, event: str | None = None) -> builtins.list[dict[str, Any]]:
        rows = self.store.read(lambda c: c.execute(
            "SELECT * FROM journal WHERE run_id=?" + (" AND event=?" if event else "") + " ORDER BY seq",
            (run_id, event) if event else (run_id,)).fetchall())
        return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]

    def artifacts(self, run_id: str) -> builtins.list[dict[str, Any]]:
        return [dict(r) for r in self.store.read(lambda c: c.execute(
            "SELECT * FROM artifact WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall())]

    def verify_integrity(self) -> builtins.list[dict[str, Any]]:
        """DR-79：孤儿记录 / 缺失文件 / 哈希不符。"""
        from wfp.engine.journal import sha256_bytes
        bad = []
        rows = self.store.read(lambda c: c.execute("SELECT * FROM artifact").fetchall())
        for r in rows:
            path = self.paths.root / r["path"]
            if not path.exists():
                bad.append({"artifact": r["id"], "problem": "missing_file", "path": r["path"]})
            elif sha256_bytes(path.read_bytes()) != r["sha256"]:
                bad.append({"artifact": r["id"], "problem": "hash_mismatch", "path": r["path"]})
        return bad

    def cleanup(self, *, days: int = 30, max_bytes: int = 5 * 1024 ** 3) -> dict[str, Any]:
        """DR-69：按天数+容量双阈值列出并清理（保留最近 run）。"""
        import shutil
        cutoff = time.time() - days * 86400
        logs, arts = self.paths.logs, self.paths.artifacts
        removed: list[str] = []
        recent = {r["id"] for r in self.list(limit=5)}
        size = 0
        for base in (logs, arts):
            for child in base.iterdir() if base.exists() else []:
                size += sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        for base in (logs, arts):
            for child in base.iterdir() if base.exists() else []:
                if child.name in recent:
                    continue
                oldest = min((f.stat().st_mtime for f in child.rglob("*") if f.is_file()), default=time.time())
                if oldest < cutoff or size > max_bytes:
                    shutil.rmtree(child, ignore_errors=True)
                    removed.append(str(child))
        self.store.audit("system", "maintenance.cleanup", None, {"removed": removed})
        return {"removed": removed, "count": len(removed)}

    # ---------- 互斥锁（DR-02/39） ----------
    def acquire_lock(self, name: str, run_id: str) -> bool:
        now = utcnow()

        def _w(c: Any) -> bool:
            row = c.execute("SELECT * FROM singleton_lock WHERE name=?", (name,)).fetchone()
            if row:
                hb = row["heartbeat_at"] or ""
                # 锁自愈：PID 不存在或心跳超过 30s
                try:
                    stale = hb and (time.time() - _parse_ts(hb) > 30)
                except Exception:  # noqa: BLE001
                    stale = True
                if row["holder_pid"] and _pid_alive(int(row["holder_pid"])) and not stale:
                    return False
                c.execute("DELETE FROM singleton_lock WHERE name=?", (name,))
            c.execute("INSERT INTO singleton_lock(name,holder_pid,holder_run,acquired_at,heartbeat_at)"
                      " VALUES (?,?,?,?,?)", (name, os.getpid(), run_id, now, now))
            return True
        return self.store.write(_w)

    def lock_holder(self, name: str) -> str | None:
        row = self.store.read(lambda c: c.execute(
            "SELECT holder_run FROM singleton_lock WHERE name=?", (name,)).fetchone())
        return row["holder_run"] if row else None

    def release_lock(self, name: str, run_id: str) -> None:
        self.store.write(lambda c: c.execute(
            "DELETE FROM singleton_lock WHERE name=? AND holder_run=?", (name, run_id)))


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_ts(ts: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return time.time()


__all__ = ["RUN_TERMINAL", "RunStore"]
