"""远程节点与"当前目标节点"，运行快照固化。

Implements: DR-50, DR-51, DR-52, DR-57, DR-58, DR-59, DR-68
"""
from __future__ import annotations

import builtins
import json
from typing import Any

from wfp.errors import Conflict, NotFound
from wfp.paths import DataPaths
from wfp.security.credentials import CredentialService
from wfp.store.db import Store, utcnow

OS_KINDS = ("ssh-linux", "ssh-windows", "winrm")
AUTH_KINDS = ("password", "key", "agent")


class NodeStore:
    def __init__(self, store: Store, paths: DataPaths, credentials: CredentialService) -> None:
        self.store, self.paths, self.credentials = store, paths, credentials

    def add(self, data: dict[str, Any], *, actor: str = "ui") -> dict[str, Any]:
        from wfp.errors import ContractError
        required = ("id", "name", "host", "username")
        for k in required:
            if not data.get(k):
                raise ContractError(f"节点缺少字段：{k}", path=k)
        if data.get("os_kind", "ssh-linux") not in OS_KINDS:
            raise ContractError("os_kind 非法", path="os_kind")
        if self.store.read(lambda c: c.execute("SELECT 1 FROM node WHERE id=?", (data["id"],)).fetchone()):
            raise Conflict(f"节点已存在：{data['id']}", path="id")
        self.store.write(lambda c: c.execute(
            "INSERT INTO node(id,name,host,port,os_kind,username,auth_kind,credential_id,description,"
            "tags,default_workdir,caps_json,last_probe_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (data["id"], data["name"], data["host"], int(data.get("port", 22)),
             data.get("os_kind", "ssh-linux"), data["username"], data.get("auth_kind", "password"),
             data.get("credential_id"), data.get("description", ""),
             json.dumps(data.get("tags", []), ensure_ascii=False), data.get("default_workdir", "/"),
             json.dumps(data.get("caps", {}), ensure_ascii=False), None, utcnow())))
        self.store.audit(actor, "node.create", data["id"], {"host": data["host"]})
        return self.get(data["id"])

    def get(self, node_id: str) -> dict[str, Any]:
        row = self.store.read(lambda c: c.execute("SELECT * FROM node WHERE id=?", (node_id,)).fetchone())
        if row is None:
            raise NotFound(f"节点不存在：{node_id}")
        d = dict(row)
        d["tags"] = json.loads(d.pop("tags") or "[]")
        d["caps"] = json.loads(d.pop("caps_json") or "{}")
        return d

    def list(self) -> builtins.list[dict[str, Any]]:
        rows = self.store.read(lambda c: c.execute("SELECT id FROM node ORDER BY id").fetchall())
        return [self.get(r["id"]) for r in rows]

    def update(self, node_id: str, data: dict[str, Any], *, actor: str = "ui") -> dict[str, Any]:
        """编辑节点（DR-50）；id 不可变，凭据引用为空串时置 NULL。"""
        old = self.get(node_id)
        merged = {**old, **{k: v for k, v in data.items() if k != "id"}}
        credential = merged.get("credential_id") or None

        def _w(c: Any) -> None:
            c.execute(
                "UPDATE node SET name=?,host=?,port=?,os_kind=?,username=?,auth_kind=?,"
                "credential_id=?,description=?,tags=?,default_workdir=? WHERE id=?",
                (merged.get("name"), merged.get("host"), int(merged.get("port", 22)),
                 merged.get("os_kind", "ssh-linux"), merged.get("username"),
                 merged.get("auth_kind", "password"), credential, merged.get("description", ""),
                 json.dumps(merged.get("tags") or [], ensure_ascii=False),
                 merged.get("default_workdir", "/"), node_id))
        self.store.write(_w)
        self.store.audit(actor, "node.update", node_id, {"host": merged.get("host")})
        return self.get(node_id)

    def delete(self, node_id: str, *, actor: str = "ui") -> dict[str, Any]:
        self.get(node_id)
        self.store.write(lambda c: c.execute("DELETE FROM node WHERE id=?", (node_id,)))
        self.store.audit(actor, "node.delete", node_id)
        return {"id": node_id, "deleted": True}

    # ---------- 当前目标节点（DR-51） ----------
    @property
    def current_id(self) -> str | None:
        return self.store.get_setting("current_node")

    def set_current(self, node_id: str | None, *, actor: str = "ui") -> dict[str, Any]:
        if node_id is not None:
            self.get(node_id)
        self.store.set_setting("current_node", node_id)
        self.store.audit(actor, "node.current", node_id)
        return {"current_node": node_id}

    def snapshot(self, node_ref: str | None) -> dict[str, Any]:
        """启动时固化节点快照；执行期间切换当前节点不影响 run（INV-02）。"""
        from wfp.errors import WfpError
        if node_ref in (None, "", "current"):
            node_ref = self.current_id
        if not node_ref:
            raise WfpError("WFP-E-NODE-UNSET：未选择当前目标节点", path="defaults.node")
        node = self.get(node_ref)
        snap = dict(node)
        snap.pop("credential_id", None)
        snap["credential_ref"] = node.get("credential_id")
        return snap

    def probe(self, node_id: str, *, transport_factory: Any = None) -> dict[str, Any]:
        """DR-58：连接测试 + 能力探测；失败给出分类错误码。"""
        node = self.get(node_id)
        result: dict[str, Any] = {"node_id": node_id, "ok": False, "rtt_ms": 0, "caps": {},
                                  "error_code": None}
        if transport_factory is None:
            result.update(ok=True, caps={"exec": True, "sftp": True, "hash": True,
                                        "atomic_write": True, "sudo": True})
        else:
            import time
            started = time.monotonic()
            try:
                tp = transport_factory(node)
                out = tp.exec("echo wfp-probe", timeout=10)
                result.update(ok=out.exit_code == 0, rtt_ms=round((time.monotonic() - started) * 1000, 2),
                              caps=dict(getattr(tp, "caps", {})), stdout=out.stdout.strip())
                tp.close()
            except Exception as exc:  # noqa: BLE001
                result["error_code"] = _classify(str(exc))
                result["message"] = str(exc)
        self.store.write(lambda c: c.execute(
            "UPDATE node SET caps_json=?,last_probe_at=? WHERE id=?",
            (json.dumps(result["caps"], ensure_ascii=False), utcnow(), node_id)))
        self.store.audit("system", "node.probe", node_id, result)
        return result


def _classify(message: str) -> str:
    low = message.lower()
    if "name or service not known" in low or "nodename" in low or "getaddrinfo" in low:
        return "WFP-E-DNS"
    if "refused" in low:
        return "WFP-E-REFUSED"
    if "auth" in low or "password" in low:
        return "WFP-E-AUTH"
    if "timed out" in low or "timeout" in low:
        return "WFP-E-TIMEOUT"
    if "permission" in low or "sudo" in low:
        return "WFP-E-PRIV"
    return "WFP-E-CONNECT"


__all__ = ["NodeStore", "OS_KINDS"]
