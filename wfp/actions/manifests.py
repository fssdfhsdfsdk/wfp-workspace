"""内置动作契约（随平台发布，入库时同样走 DR-11/17 校验）。

Implements: DR-10, DR-11, DR-12, DR-17, DR-18, DR-19, DR-30, DR-33, DR-34, DR-48
16 个动作覆盖 IR-02 的八类：解压/压缩、文件、配置、上下传、校验、脚本、远程。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

DISPATCH = {"module": "wfp.actions.builtin:dispatch"}
PLAN = {"entry": {"module": "wfp.actions.builtin:dispatch_plan"}}
COMPENSATE = {"entry": {"module": "wfp.actions.builtin:dispatch_compensate"}}


def _obj(props: dict[str, Any], required: list[str], *, extra: bool = False) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": extra}


STR = {"type": "string"}
PATH = {"type": "string", "x-widget": "path", "x-templatable": True}
BOOL = {"type": "boolean"}
EDITS = {
    "type": "array", "minItems": 1,
    "items": _obj({"key": STR, "value": {}, "op": {"enum": ["set", "merge", "delete"]}}, ["key", "value"]),
}
ITEMS = {
    "type": "array", "minItems": 1,
    "items": _obj({"local": STR, "remote": STR, "recursive": BOOL}, ["local", "remote"]),
}


def make_manifest(action_id: str, name: str, category: str, params: dict[str, Any], *,
                  runtime: str = "builtin", operation: str | None = None,
                  dry_run: str = "plan", idempotent: str = "yes",
                  compensate: str = "supported", side_effects: str = "local_fs",
                  needs_network: bool = False, needs_admin: bool = False,
                  evidence: list[str] | None = None, tags: list[str] | None = None,
                  description: str = "", outputs: dict[str, Any] | None = None,
                  ui: dict[str, Any] | None = None) -> dict[str, Any]:
    if runtime == "remote":
        entry: dict[str, Any] = {"operation": operation or "exec", "transport": "auto"}
    elif runtime == "python":
        entry = {"script": "script.py", "argv": ["--json"], "callable": "main"}
    else:
        entry = dict(DISPATCH)
    m: dict[str, Any] = {
        "id": action_id, "version": "1.0.0", "name": name,
        "description": description or name, "category": category,
        "tags": tags or [category], "runtime": runtime, "entry": entry,
        "params": params,
        "outputs": outputs or _obj({"changed": BOOL, "sha256": STR}, [], extra=True),
        "capabilities": {
            "dry_run": dry_run, "idempotent": idempotent, "compensate": compensate,
            "needs_admin": needs_admin, "needs_network": needs_network,
            "side_effects": side_effects,
        },
        "policy": {"timeout_seconds": 300, "console": "hidden", "stdin": "closed",
                   "retry": {"attempts": 0, "backoff": "fixed", "delay_seconds": 0}},
        "evidence": evidence or ["report"],
        "ui": ui or {},
    }
    if dry_run == "plan":
        m["plan"] = deepcopy(PLAN)
    if compensate == "supported":
        m["compensate"] = deepcopy(COMPENSATE)
    return m


PATH_PAIR = _obj({"src": PATH, "dest": PATH}, ["src", "dest"])
FILE_PATH = _obj({"path": PATH}, ["path"])
CONFIG_PARAMS = _obj({"path": PATH, "format": {"enum": ["auto", "json", "yaml", "ini"]},
                      "edits": EDITS, "create_if_missing": BOOL}, ["path", "edits"])

SPECS: list[dict[str, Any]] = [
    make_manifest("archive.extract", "解压缩包", "archive",
                  _obj({"archive": PATH, "dest": PATH, "overwrite": BOOL,
                        "include": {"type": "array", "items": STR},
                        "exclude": {"type": "array", "items": STR}}, ["archive", "dest"]),
                  evidence=["report", "backup"], tags=["zip", "tar.gz"]),
    make_manifest("archive.pack", "打包备份", "archive",
                  _obj({"src": PATH, "dest": PATH, "format": {"enum": ["zip", "tar.gz"]},
                        "include_root": {"type": "boolean", "default": True,
                                         "description": "压缩包内保留源目录名一层（压缩文件夹的直觉行为）"}},
                       ["src", "dest"]), evidence=["backup"]),
    make_manifest("fs.copy", "复制文件/目录", "fs", PATH_PAIR, evidence=["backup"]),
    make_manifest("fs.move", "移动文件/目录", "fs", PATH_PAIR, evidence=["backup"]),
    make_manifest("fs.delete", "删除文件/目录", "fs",
                  _obj({"path": PATH, "recursive": BOOL}, ["path"]),
                  side_effects="destructive", evidence=["backup"]),
    make_manifest("config.edit", "修改配置文件", "config", CONFIG_PARAMS,
                  evidence=["diff", "backup"], ui={"edits": {"ui:widget": "keyValueList"}}),
    make_manifest("net.upload", "上传文件（本地/HTTP）", "net",
                  _obj({"src": PATH, "dest": PATH, "url": STR}, ["src"]),
                  needs_network=True, evidence=["transfer"]),
    make_manifest("net.download", "下载文件（本地/HTTP）", "net",
                  _obj({"src": PATH, "dest": PATH, "url": STR}, ["src"]),
                  needs_network=True, evidence=["transfer"]),
    make_manifest("assert.file_exists", "断言文件存在", "assert", FILE_PATH,
                  compensate="none", side_effects="none", evidence=["report"]),
    make_manifest("assert.checksum", "断言文件哈希", "assert",
                  _obj({"path": PATH, "expect_sha256": STR, "algo": {"enum": ["sha256", "md5"]}},
                       ["path", "expect_sha256"]),
                  compensate="none", side_effects="none", evidence=["report"]),
    make_manifest("assert.json_schema", "断言 JSON 结构", "assert",
                  _obj({"path": PATH, "schema": {"type": "object"}}, ["path", "schema"]),
                  compensate="none", side_effects="none", evidence=["report"]),
    make_manifest("script.local", "执行本地 Python 脚本", "script",
                  _obj({"args": {"type": "array", "items": STR}, "env": {"type": "object"}}, []),
                  runtime="python", dry_run="none", idempotent="no", compensate="manual",
                  side_effects="local_fs", evidence=["report"]),
    make_manifest("remote.exec", "远程执行命令", "remote",
                  _obj({"command": STR, "cwd": STR, "sudo": BOOL, "readonly": BOOL,
                        "expect_exit": {"type": "integer"}}, ["command"]),
                  runtime="remote", operation="exec", dry_run="plan", idempotent="no",
                  compensate="manual", side_effects="remote_exec", needs_network=True,
                  evidence=["remote_result"]),
    make_manifest("remote.upload", "远程上传文件/目录", "remote",
                  _obj({"items": ITEMS, "exclude": {"type": "array", "items": STR}}, ["items"]),
                  runtime="remote", operation="upload", needs_network=True,
                  side_effects="remote_fs", evidence=["transfer", "backup"]),
    make_manifest("remote.download", "远程下载文件", "remote",
                  _obj({"remote": STR, "local": PATH}, ["remote", "local"]),
                  runtime="remote", operation="download", needs_network=True,
                  side_effects="local_fs", evidence=["transfer"]),
    make_manifest("remote.config_edit", "远程修改配置（原子事务）", "remote", CONFIG_PARAMS,
                  runtime="remote", operation="config_edit", needs_network=True,
                  side_effects="remote_fs", evidence=["diff", "backup"]),
]

BUILTIN_MANIFESTS: dict[str, dict[str, Any]] = {m["id"]: m for m in SPECS}

SCRIPT_LOCAL_IMPL = '''"""WFP script.local 默认实现体（入口契约 WFP_OUTPUT::json，DR-12/14）。"""
import json
import os


def main(params=None):
    params = params or json.loads(os.environ.get("WFP_PARAMS", "{}"))
    result = {"ok": True, "args": params.get("args", []), "keys": sorted(params.get("env", {}))}
    return result


if __name__ == "__main__":
    out = main()
    print("WFP_OUTPUT::" + json.dumps(out))
'''


def seed_builtin_actions(registry: Any, *, actor: str = "system") -> int:
    """幂等地把内置动作写入动作库（仍需通过同一 manifest 校验，DR-11/17/18）。

    老数据库里已存在的内置 1.0.0 可能缺少后来新增的参数（如 archive.pack.include_root），
    启动时把系统内置契约同步到最新定义；用户自建动作与实现体字节不受影响。
    """
    import json

    from wfp.errors import Conflict
    created = 0
    for action_id, manifest in BUILTIN_MANIFESTS.items():
        try:
            registry.create(manifest, _impl_for(manifest), actor=actor)
            created += 1
        except Conflict:
            try:
                current = registry.get_version(action_id, manifest["version"])
                if current["manifest"] != manifest:
                    payload_json = json.dumps(manifest, ensure_ascii=False)
                    aid, ver = action_id, manifest["version"]
                    registry.store.write(lambda c, p=payload_json, a=aid, v=ver: c.execute(
                        "UPDATE action_version SET manifest_json=? WHERE action_id=? AND version=?",
                        (p, a, v)))
                    registry.store.audit(actor, "action.contract_sync", aid, {"version": ver})
            except Exception:  # noqa: BLE001 - 契约同步失败不应阻止平台启动
                continue
    return created


def _impl_for(manifest: dict[str, Any]) -> str:
    if manifest["runtime"] == "python":
        return SCRIPT_LOCAL_IMPL
    if manifest["runtime"] == "remote":
        return f"# WFP remote operation: {manifest['entry']['operation']}\n"
    return f"# WFP builtin dispatch: {manifest['id']}\n"


__all__ = ["BUILTIN_MANIFESTS", "seed_builtin_actions", "make_manifest"]
