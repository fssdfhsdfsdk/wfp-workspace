"""环境自诊断 wfp doctor。

Implements: DR-01, DR-04, DR-05, DR-06, DR-07, DR-08, DR-52, DR-67, DR-73, DR-76, DR-79
"""
from __future__ import annotations

import importlib.util
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

from wfp.paths import DataPaths
from wfp.store.db import Store


def _check(name: str, ok: bool, code: str, detail: str, suggestion: str = "") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "code": code, "detail": detail,
            "suggestion": suggestion if not ok else ""}


def run_doctor(paths: DataPaths, *, store: Store | None = None, verify: bool = False,
               port: int = 8765, config_path: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_check("python", sys.version_info >= (3, 11), "WFP-D-PYTHON",
                         f"{sys.version.split()[0]}（要求 >=3.11）", "升级到 Python 3.11+"))
    checks.append(_check("data_home", paths.writable(), "WFP-D-HOME",
                         str(paths.root), "检查 WFP_HOME 权限或选择可写目录"))
    checks.append(_check("config", config_path is None or config_path.is_file(), "WFP-D-CONFIG",
                         str(config_path or paths.config),
                         "运行 wfp config init 生成启动配置（端口/数据目录/保留策略）"))
    free = shutil.disk_usage(paths.root if paths.root.exists() else Path.home()).free
    checks.append(_check("disk", free >= 500 * 1024 ** 2, "WFP-D-DISK",
                         f"可用 {free // 1024 ** 2} MB", "清理磁盘或调整数据目录"))
    probe = socket.socket()
    probe.settimeout(0.2)
    port_free = probe.connect_ex(("127.0.0.1", port)) != 0
    probe.close()
    checks.append(_check("port", port_free, "WFP-D-PORT", f"127.0.0.1:{port}",
                         "修改 config.toml 的 server.port 或用 wfp serve --port <其他端口>"))
    checks.append(_check("powershell", sys.platform != "win32" or
                         shutil.which("powershell") is not None, "WFP-D-PS",
                         "Windows PowerShell" if sys.platform == "win32" else "POSIX CI 跳过",
                         "安装 PowerShell 5.1/7.x"))
    checks.append(_check("schemas", all((_schema_dir() / n).exists() for n in
                                        ("action-manifest.schema.json", "pipeline.schema.json")),
                         "WFP-D-SCHEMA", "动作/工作项契约", "重新安装 wfp 包"))
    checks.append(_check("credential_backend",
                         sys.platform == "win32" or importlib.util.find_spec("keyring") is not None
                         or importlib.util.find_spec("cryptography") is not None,
                         "WFP-D-CRED", "DPAPI / keyring / AES-GCM", "pip install cryptography keyring"))
    checks.append(_check("ssh", importlib.util.find_spec("paramiko") is not None,
                         "WFP-D-SSH", "paramiko", "pip install paramiko（离线用 wheelhouse）"))
    checks.append(_check("ruamel", importlib.util.find_spec("ruamel") is not None,
                         "WFP-D-RUAMEL", "ruamel.yaml", "pip install ruamel.yaml"))
    if verify and store is not None:
        from wfp.store.runs import RunStore
        bad = RunStore(store, paths).verify_integrity()
        checks.append(_check("integrity", not bad, "WFP-D-VERIFY",
                             f"{len(bad)} 项异常", "检查 artifacts 缺失或哈希不符的证据"))
    if store is not None:
        checks.append(_check("database", paths.db.exists() and store.current_version() >= 1,
                             "WFP-D-DB", f"schema v{store.current_version()}", "运行 wfp doctor 或重新初始化"))
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "version": sys.version.split()[0], "home": str(paths.root),
            "config_file": str(config_path or paths.config), "port": port,
            "checks": checks, "failed": [c["code"] for c in checks if not c["ok"]]}


def _schema_dir() -> Path:
    from wfp.contracts.manifest import _schema_dir as _dir
    return _dir()


__all__ = ["run_doctor"]
