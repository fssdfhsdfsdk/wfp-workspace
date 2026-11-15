"""工作项持久化：YAML 真源、版本不可变、导入导出、布局分离。

Implements: DR-05, DR-16, DR-20, DR-28, DR-45, DR-46, DR-65, DR-68
"""
from __future__ import annotations

import builtins
import json
import zipfile
from pathlib import Path
from typing import Any

from wfp.contracts.pipeline import PipelineDoc, dependencies, validate_pipeline
from wfp.contracts.yamlio import safe_load
from wfp.errors import Conflict, NotFound
from wfp.paths import DataPaths
from wfp.store.db import Store, utcnow


def _bump(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 3 and all(x.isdigit() for x in parts):
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return f"{version}.1"


class PipelineStore:
    def __init__(self, store: Store, paths: DataPaths) -> None:
        self.store, self.paths = store, paths

    # ---------- 写 ----------
    def create(self, text: str, *, actor: str = "ui", source: Path | None = None) -> dict[str, Any]:
        doc = validate_pipeline(safe_load(text))
        pid, version = doc["meta"]["id"], str(doc["meta"].get("version", "1.0.0"))
        if self.store.read(lambda c: c.execute("SELECT 1 FROM pipeline WHERE id=?", (pid,)).fetchone()):
            raise Conflict(f"工作项已存在：{pid}（保存请生成新版本）", path="meta.id")
        return self._persist(pid, version, text, doc, actor, "pipeline.create")

    def save_yaml(self, pipeline_id: str, text: str, *, if_match: str | None = None,
                  actor: str = "ui") -> dict[str, Any]:
        """全文保存（If-Match sha 乐观并发）；编辑产生新版本（INV-01）。"""
        current = self.get(pipeline_id)
        if if_match and if_match != current.sha256:
            raise Conflict("YAML 已被其他窗口修改（If-Match 不匹配），请以真源为准", path="If-Match")
        doc = validate_pipeline(safe_load(text))
        if doc["meta"]["id"] != pipeline_id:
            raise Conflict("meta.id 不可修改", path="meta.id")
        version = _bump(current.version)
        doc["meta"]["version"] = version
        # 用 round-trip 文档树原地改 meta.version，避免字符串替换误伤或整树重排丢注释（DR-45/47）
        from wfp.sync.patch import YamlDoc
        ydoc = YamlDoc(text)
        ydoc.doc["meta"]["version"] = version
        text = ydoc.dump()
        return self._persist(pipeline_id, version, text, doc, actor, "pipeline.version", update=True)

    def _persist(self, pid: str, version: str, text: str, doc: dict[str, Any], actor: str,
                 event: str, *, update: bool = False) -> dict[str, Any]:
        import hashlib
        d = self.paths.pipelines / pid / version
        d.mkdir(parents=True, exist_ok=True)
        yaml_path = d / "pipeline.yaml"
        yaml_path.write_text(text, encoding="utf-8")
        sha = hashlib.sha256(text.encode()).hexdigest()
        deps = dependencies(doc)
        now = utcnow()
        layout = d / "pipeline.layout.json"
        if not layout.exists():
            layout.write_text("{}\n", encoding="utf-8")

        def _w(c: Any) -> None:
            if update:
                c.execute("UPDATE pipeline SET name=?,latest_version=?,status='active',updated_at=? WHERE id=?",
                          (doc["meta"]["name"], version, now, pid))
            else:
                c.execute("INSERT INTO pipeline(id,name,latest_version,status,deleted,created_at,updated_at)"
                          " VALUES (?,?,?,'active',0,?,?)",
                          (pid, doc["meta"]["name"], version, now, now))
            c.execute("INSERT INTO pipeline_version(pipeline_id,version,yaml_path,yaml_sha256,layout_path,"
                      "inputs_schema_json,step_count,deps_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (pid, version, str(yaml_path.relative_to(self.paths.root)), sha,
                       str(layout.relative_to(self.paths.root)),
                       json.dumps(doc.get("inputs") or {}, ensure_ascii=False),
                       len(doc.get("steps") or []), json.dumps(deps, ensure_ascii=False), now))

        self.store.write(_w)
        self.store.audit(actor, event, pid, {"version": version}, after_sha256=sha)
        return {"id": pid, "version": version, "sha256": sha, "step_count": len(doc.get("steps") or [])}

    def delete(self, pipeline_id: str, *, actor: str = "ui") -> dict[str, Any]:
        self.get(pipeline_id)
        self.store.write(lambda c: c.execute(
            "UPDATE pipeline SET deleted=1,updated_at=? WHERE id=?", (utcnow(), pipeline_id)))
        self.store.audit(actor, "pipeline.delete", pipeline_id)
        return {"id": pipeline_id, "deleted": True}

    def duplicate(self, pipeline_id: str, new_id: str, *, actor: str = "ui") -> dict[str, Any]:
        p = self.get(pipeline_id)
        text = p.text.replace(f"id: {pipeline_id}", f"id: {new_id}", 1)
        return self.create(text, actor=actor)

    # ---------- 读 ----------
    def list(self, *, include_deleted: bool = False) -> builtins.list[dict[str, Any]]:
        rows = self.store.read(lambda c: c.execute(
            "SELECT * FROM pipeline" + ("" if include_deleted else " WHERE deleted=0") + " ORDER BY id").fetchall())
        return [dict(r) for r in rows]

    def get(self, ref: str, *, version: str | None = None) -> PipelineDoc:
        """ref 可以是工作项 id，也可以是 YAML 文件路径。"""
        candidate = Path(ref)
        if candidate.is_file():
            return PipelineDoc.from_file(candidate)
        row = self.store.read(lambda c: c.execute(
            "SELECT * FROM pipeline WHERE id=?", (ref,)).fetchone())
        if row is None or (row["deleted"] and not include_deleted_default()):
            raise NotFound(f"工作项不存在：{ref}")
        ver = version or row["latest_version"]
        vrow = self.store.read(lambda c: c.execute(
            "SELECT * FROM pipeline_version WHERE pipeline_id=? AND version=?", (ref, ver)).fetchone())
        if vrow is None:
            raise NotFound(f"工作项版本不存在：{ref}@{ver}")
        path = self.paths.root / vrow["yaml_path"]
        return PipelineDoc.from_text(path.read_text(encoding="utf-8"), source=path)

    def versions(self, pipeline_id: str) -> builtins.list[dict[str, Any]]:
        rows = self.store.read(lambda c: c.execute(
            "SELECT version,yaml_sha256,step_count,created_at FROM pipeline_version"
            " WHERE pipeline_id=? ORDER BY created_at", (pipeline_id,)).fetchall())
        return [dict(r) for r in rows]

    def yaml_text(self, pipeline_id: str, *, version: str | None = None) -> tuple[str, str]:
        p = self.get(pipeline_id, version=version)
        return p.text, p.sha256

    def yaml_file(self, pipeline_id: str, *, version: str | None = None) -> Path:
        """返回指定版本的真源文件路径（用于以固定版本复现/续跑，DR-16/65）。"""
        p = self.get(pipeline_id)
        ver = version or p.version
        row = self.store.read(lambda c: c.execute(
            "SELECT yaml_path FROM pipeline_version WHERE pipeline_id=? AND version=?",
            (pipeline_id, ver)).fetchone())
        if row is None:
            raise NotFound(f"工作项版本不存在：{pipeline_id}@{ver}")
        return self.paths.root / row["yaml_path"]

    # ---------- 布局（DR-46） ----------
    def layout_path(self, pipeline_id: str) -> Path:
        p = self.get(pipeline_id)
        vrow = self.store.read(lambda c: c.execute(
            "SELECT layout_path FROM pipeline_version WHERE pipeline_id=? AND version=?",
            (pipeline_id, p.version)).fetchone())
        return self.paths.root / (vrow["layout_path"] if vrow else
                                  f"pipelines/{pipeline_id}/{p.version}/pipeline.layout.json")

    def get_layout(self, pipeline_id: str) -> dict[str, Any]:
        path = self.layout_path(pipeline_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def put_layout(self, pipeline_id: str, layout: dict[str, Any]) -> dict[str, Any]:
        path = self.layout_path(pipeline_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
        return layout

    # ---------- 导入导出（DR-05/28） ----------
    def export_zip(self, pipeline_id: str, out: Path, *, with_actions: bool = False) -> Path:
        p = self.get(pipeline_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"pipelines/{pipeline_id}/pipeline.yaml", p.text)
            try:
                z.writestr(f"pipelines/{pipeline_id}/pipeline.layout.json",
                           json.dumps(self.get_layout(pipeline_id), ensure_ascii=False))
            except NotFound:
                pass
            z.writestr(f"pipelines/{pipeline_id}/deps.json",
                       json.dumps(dependencies(p.doc), ensure_ascii=False))
            if with_actions:
                z.writestr("actions.json", json.dumps(
                    self.store.read(lambda c: c.execute(
                        "SELECT action_id,version,manifest_json,impl_path FROM action_version").fetchall()
                    ) and [], ensure_ascii=False))
        return out

    def import_zip(self, archive: Path, *, actor: str = "ui") -> dict[str, Any]:
        with zipfile.ZipFile(archive) as z:
            names = [n for n in z.namelist() if n.endswith("pipeline.yaml")]
            if not names:
                raise Conflict("导入包中缺少 pipeline.yaml")
            text = z.read(names[0]).decode("utf-8")
            doc = safe_load(text)
            deps = dependencies(doc)
            missing = [f"{d['action_id']}@{d['version']}" for d in deps
                       if not self.store.read(lambda c, dep=d: c.execute(  # type: ignore[misc]
                           "SELECT 1 FROM action_version WHERE action_id=? AND version=?",
                           (dep["action_id"], dep["version"])).fetchone())]
            result = self.create(text, actor=actor)
            result["missing_actions"] = missing
            for n in z.namelist():
                if n.endswith("pipeline.layout.json"):
                    self.put_layout(result["id"], json.loads(z.read(n).decode("utf-8")))
            return result


def include_deleted_default() -> bool:
    return False


__all__ = ["PipelineStore"]
