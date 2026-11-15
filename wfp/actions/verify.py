"""动作库批量端到端验证（视图「验证全部」按钮的后端）。

每个动作依次走：manifest 契约 8 项（含真实 adapter.plan 零副作用探针）
→ 样例参数渲染 → JSON Schema 参数校验。产物为零副作用的验证报告，不写用户 workspace。
Implements: DR-11, DR-17, DR-18, DR-19, DR-41, DR-75
"""
from __future__ import annotations

from typing import Any

from wfp.actions.suite import run_contract_suite
from wfp.engine.expr import validate_params

# 每个内置动作的最小可用参数（离线、无需外部网络/靶机）
SAFE_PARAMS: dict[str, dict[str, Any]] = {
    "archive.extract": {"archive": "app.zip", "dest": "out", "overwrite": True},
    "archive.pack": {"src": "src_dir", "dest": "backup", "format": "zip"},
    "fs.copy": {"src": "probe.txt", "dest": "copy.txt"},
    "fs.move": {"src": "probe.txt", "dest": "moved.txt"},
    "fs.delete": {"path": "trash.txt", "recursive": True},
    "config.edit": {"path": "probe.json", "format": "json",
                    "edits": [{"key": "a", "value": 1}], "create_if_missing": True},
    "net.upload": {"src": "probe.txt", "dest": "uploaded.txt"},
    "net.download": {"src": "probe.txt", "dest": "downloaded.txt"},
    "assert.file_exists": {"path": "probe.txt"},
    "assert.checksum": {"path": "probe.txt", "expect_sha256": ""},
    "assert.json_schema": {"path": "probe.json", "schema": {"type": "object"}},
    "script.local": {"args": ["hello"], "env": {}},
    "remote.exec": {"command": "echo ok", "readonly": True},
    "remote.upload": {"items": [{"local": "probe.txt", "remote": "/opt/probe.txt"}]},
    "remote.download": {"remote": "/opt/probe.txt", "local": "dl.txt"},
    "remote.config_edit": {"path": "/opt/app.json", "format": "json",
                           "edits": [{"key": "a", "value": 1}]},
}


def verify_all_actions(actions: Any) -> dict[str, Any]:
    """遍历动作库全部动作，返回逐项验证结果与汇总。"""
    items: list[dict[str, Any]] = []
    for a in actions.list():
        aid, ver = a["id"], a["latest_version"]
        item: dict[str, Any] = {"action_id": aid, "version": ver, "ok": False,
                                "passed": 0, "total": 0, "checks": [], "detail": ""}
        try:
            suite = run_contract_suite(actions, aid, ver)
            item["passed"], item["total"] = suite["passed"], suite["total"]
            item["checks"] = suite["checks"]
            manifest = actions.get_version(aid, ver)["manifest"]
            params = SAFE_PARAMS.get(aid)
            if params is None:
                raise ValueError(f"缺少 {aid} 的验证样例参数")
            validate_params(params, manifest)
            item["params"] = params
            item["ok"] = bool(suite["ok"])
            item["detail"] = ("契约套件 + 参数校验 + plan 零副作用探针通过" if suite["ok"]
                              else "契约套件存在失败项")
        except Exception as exc:  # noqa: BLE001 - 单动作失败不影响其余动作
            item["detail"] = str(exc)
        items.append(item)
    return {"total": len(items), "passed": sum(1 for i in items if i["ok"]), "items": items}


__all__ = ["SAFE_PARAMS", "verify_all_actions"]
