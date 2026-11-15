"""动作注册表：manifest + 实现体，版本不可变、内容寻址、软删除、引用反查。

Implements: DR-10, DR-13, DR-16（INV-01：ActionVersion 不可变）
"""
from __future__ import annotations

import builtins
import difflib
import hashlib
import json
import sqlite3
from typing import Any

from wfp.contracts.manifest import IMPL_EXT, capability_badges, validate_manifest
from wfp.errors import Conflict, ContractError, NotFound
from wfp.paths import DataPaths
from wfp.store.db import Store, utcnow


def semver(v: str) -> tuple[int, int, int]:
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ActionRegistry:
    def __init__(self, store: Store, paths: DataPaths) -> None:
        self.store = store
        self.paths = paths

    # ---------- 写 ----------
    def create(self, manifest: dict[str, Any], impl: str = "", *, actor: str = "ui") -> dict[str, Any]:
        validate_manifest(manifest)
        aid, version = manifest["id"], manifest["version"]
        exists = self.store.read(lambda c: c.execute(
            "SELECT id, deleted FROM action WHERE id=?", (aid,)).fetchone())
        if exists:
            raise Conflict(f"动作已存在：{aid}（新增版本请用 PUT）", path="id")
        impl_rel, impl_sha = self._store_impl(manifest["runtime"], impl)
        now = utcnow()

        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO action(id,name,category,tags,latest_version,enabled,deleted,created_at,updated_at)"
                " VALUES (?,?,?,?,?,1,0,?,?)",
                (aid, manifest["name"], manifest.get("category"),
                 json.dumps(manifest.get("tags", []), ensure_ascii=False), version, now, now),
            )
            self._insert_version(conn, manifest, impl_rel, impl_sha, now)

        self.store.write(_w)
        self.store.audit(actor, "action.create", aid, {"version": version}, after_sha256=impl_sha)
        return self.get(aid)

    def add_version(self, action_id: str, manifest: dict[str, Any], impl: str = "",
                    *, actor: str = "ui") -> dict[str, Any]:
        """编辑 = 新版本（DR-10/16）；旧版本字节不变（INV-01）。"""
        validate_manifest(manifest)
        if manifest["id"] != action_id:
            raise ContractError("manifest.id 与路径中的动作 id 不一致", path="id")
        current = self.get(action_id)
        if semver(manifest["version"]) <= semver(current["latest_version"]):
            raise Conflict(
                f"新版本必须大于当前版本 {current['latest_version']}", path="version")
        if self.store.read(lambda c: c.execute(
                "SELECT 1 FROM action_version WHERE action_id=? AND version=?",
                (action_id, manifest["version"])).fetchone()):
            raise Conflict(f"版本已存在且不可变：{action_id}@{manifest['version']}", path="version")
        impl_rel, impl_sha = self._store_impl(manifest["runtime"], impl)
        now = utcnow()

        def _w(conn: sqlite3.Connection) -> None:
            self._insert_version(conn, manifest, impl_rel, impl_sha, now)
            conn.execute(
                "UPDATE action SET name=?,category=?,tags=?,latest_version=?,updated_at=? WHERE id=?",
                (manifest["name"], manifest.get("category"),
                 json.dumps(manifest.get("tags", []), ensure_ascii=False),
                 manifest["version"], now, action_id),
            )

        self.store.write(_w)
        self.store.audit(actor, "action.version", action_id, {"version": manifest["version"]},
                           after_sha256=impl_sha)
        return self.get(action_id)

    def soft_delete(self, action_id: str, *, actor: str = "ui") -> dict[str, Any]:
        self.get(action_id)
        refs = self.references(action_id)
        if refs:
            raise Conflict(f"动作被 {len(refs)} 个工作项引用，拒绝删除", details={"references": refs})

        def _w(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE action SET deleted=1, updated_at=? WHERE id=?", (utcnow(), action_id))
        self.store.write(_w)
        self.store.audit(actor, "action.delete", action_id)
        return {"id": action_id, "deleted": True}

    def set_enabled(self, action_id: str, enabled: bool, *, actor: str = "ui") -> dict[str, Any]:
        self.get(action_id)
        self.store.write(lambda c: c.execute(
            "UPDATE action SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, utcnow(), action_id)))
        self.store.audit(actor, "action.enable" if enabled else "action.disable", action_id)
        return self.get(action_id)

    def duplicate(self, action_id: str, new_id: str, *, actor: str = "ui") -> dict[str, Any]:
        """复制为新动作（DR-13）：共享实现体哈希，不共享可变状态。"""
        src = self.get_version(action_id, self.get(action_id)["latest_version"])
        manifest = dict(src["manifest"])
        manifest["id"] = new_id
        manifest["version"] = "1.0.0"
        manifest["name"] = f"{manifest['name']} (副本)"
        return self.create(manifest, src["impl"], actor=actor)

    # ---------- 读 ----------
    def list(self, *, query: str = "", category: str | None = None, tag: str | None = None,
             include_deleted: bool = False) -> builtins.list[dict[str, Any]]:
        sql = "SELECT * FROM action"
        clauses, args = [], []
        if not include_deleted:
            clauses.append("deleted=0")
        if category:
            clauses.append("category=?"); args.append(category)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY category, id"
        rows = self.store.read(lambda c: c.execute(sql, args).fetchall())
        out = []
        for r in rows:
            item = self._row_to_action(r)
            if query:
                hay = f"{item['id']} {item['name']} {item.get('description','')} {' '.join(item['tags'])}".lower()
                if query.lower() not in hay:
                    continue
            if tag and tag not in item["tags"]:
                continue
            out.append(item)
        return out

    def get(self, action_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        row = self.store.read(lambda c: c.execute(
            "SELECT * FROM action WHERE id=?", (action_id,)).fetchone())
        if row is None or (row["deleted"] and not include_deleted):
            raise NotFound(f"动作不存在：{action_id}")
        return self._row_to_action(row)

    def versions(self, action_id: str) -> builtins.list[dict[str, Any]]:
        self.get(action_id, include_deleted=True)
        rows = self.store.read(lambda c: c.execute(
            "SELECT version,runtime,impl_sha256,created_at FROM action_version"
            " WHERE action_id=? ORDER BY created_at", (action_id,)).fetchall())
        return [dict(r) for r in rows]

    def get_version(self, action_id: str, version: str) -> dict[str, Any]:
        row = self.store.read(lambda c: c.execute(
            "SELECT * FROM action_version WHERE action_id=? AND version=?",
            (action_id, version)).fetchone())
        if row is None:
            raise NotFound(f"动作版本不存在：{action_id}@{version}")
        manifest = json.loads(row["manifest_json"])
        impl_path = self.paths.root / row["impl_path"]
        impl = impl_path.read_text(encoding="utf-8") if impl_path.exists() else ""
        return {
            "action_id": action_id, "version": version, "manifest": manifest,
            "impl": impl, "impl_sha256": row["impl_sha256"], "runtime": row["runtime"],
            "created_at": row["created_at"], "badges": capability_badges(manifest),
        }

    def diff(self, action_id: str, from_version: str, to_version: str) -> dict[str, Any]:
        """实现体行级 diff（DR-03 查看窗口）。"""
        a = self.get_version(action_id, from_version)
        b = self.get_version(action_id, to_version)
        lines = list(difflib.unified_diff(
            a["impl"].splitlines(), b["impl"].splitlines(),
            fromfile=f"{action_id}@{from_version}", tofile=f"{action_id}@{to_version}", lineterm=""))
        return {"action_id": action_id, "from": from_version, "to": to_version,
                "changed": a["impl_sha256"] != b["impl_sha256"], "diff": "\n".join(lines)}

    def references(self, action_id: str) -> builtins.list[dict[str, Any]]:
        """被哪些工作项引用（DR-13）。M0 起可用，M1 起有数据。"""
        rows = self.store.read(lambda c: c.execute(
            "SELECT pipeline_id, version, deps_json FROM pipeline_version").fetchall())
        refs = []
        for r in rows:
            for dep in json.loads(r["deps_json"] or "[]"):
                if dep.get("action_id") == action_id:
                    refs.append({"pipeline_id": r["pipeline_id"], "pipeline_version": r["version"],
                                 "action_version": dep.get("version")})
        return refs

    # ---------- 内部 ----------
    def _store_impl(self, runtime: str, impl: str) -> tuple[str, str]:
        sha = sha256_text(impl)
        path = self.paths.impl_path(sha, IMPL_EXT.get(runtime, ".txt"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(impl, encoding="utf-8")
        return str(path.relative_to(self.paths.root)), sha

    def _insert_version(self, conn: sqlite3.Connection, manifest: dict[str, Any],
                        impl_rel: str, impl_sha: str, now: str) -> None:
        caps = manifest["capabilities"]
        reqs = manifest.get("requirements") or []
        conn.execute(
            "INSERT INTO action_version(action_id,version,manifest_json,runtime,impl_path,impl_sha256,"
            "cap_dry_run,cap_idempotent,cap_compensate,needs_admin,needs_network,side_effects,"
            "req_fingerprint,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (manifest["id"], manifest["version"], json.dumps(manifest, ensure_ascii=False),
             manifest["runtime"], impl_rel, impl_sha,
             caps["dry_run"], caps["idempotent"], caps["compensate"],
             1 if caps["needs_admin"] else 0, 1 if caps["needs_network"] else 0, caps["side_effects"],
             sha256_text("\n".join(sorted(reqs))) if reqs else None, now),
        )

    def _row_to_action(self, r: sqlite3.Row) -> dict[str, Any]:
        latest = self.store.read(lambda c: c.execute(
            "SELECT manifest_json FROM action_version WHERE action_id=? AND version=?",
            (r["id"], r["latest_version"])).fetchone())
        manifest = json.loads(latest["manifest_json"]) if latest else {}
        return {
            "id": r["id"], "name": r["name"], "category": r["category"],
            "tags": json.loads(r["tags"] or "[]"), "latest_version": r["latest_version"],
            "enabled": bool(r["enabled"]), "deleted": bool(r["deleted"]),
            "description": manifest.get("description", ""), "runtime": manifest.get("runtime"),
            "badges": capability_badges(manifest),
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
