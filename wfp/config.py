"""WFP 启动配置（TOML）：端口、数据目录、保留策略与功能开关。

Implements: DR-01, DR-05, DR-06, DR-07, DR-67, DR-69, DR-72, DR-73, DR-76
配置文件默认位于 <数据目录>/config.toml；架构设计 §3.3 明确"config.toml 不含凭据"。
优先级：CLI 参数 > WFP_CONFIG/--config 指定文件 > 内置默认值；
数据目录：--home > WFP_HOME > 配置里的 data_dir > 系统默认。
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wfp.errors import ContractError
from wfp.paths import DataPaths, default_home

ENV_CONFIG = "WFP_CONFIG"
ENV_HOME = "WFP_HOME"

SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "server": ("port", "host", "lan", "open_browser", "log_level"),
    "storage": ("data_dir", "db_file", "retention_days", "retention_max_bytes", "log_quota_mb"),
    "engine": ("default_timeout_seconds",),
    "features": ("ai_enabled", "current_node"),
}
PUBLIC_FIELDS = tuple(k for keys in SECTION_FIELDS.values() for k in keys)
BOOL_FIELDS = {"lan", "open_browser", "ai_enabled"}
INT_FIELDS = {"port", "retention_days", "retention_max_bytes", "log_quota_mb"}
NUM_FIELDS = {"default_timeout_seconds"}
STR_FIELDS = set(PUBLIC_FIELDS) - BOOL_FIELDS - INT_FIELDS - NUM_FIELDS
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOG_LEVELS = {"critical", "error", "warning", "info", "debug", "trace"}

EXAMPLE_TOML = '''# WFP 启动配置（无凭据；本机 token 存于数据目录 session.json）
# 生成模板：wfp config init     查看生效值：wfp config show
# 查找顺序：--config / WFP_CONFIG 指定文件 > <WFP_HOME>/config.toml > 内置默认值
# 优先级：CLI 参数 > 配置文件 > 默认值；数据目录：--home > WFP_HOME > data_dir > 系统默认

[server]
port = 8765
host = "127.0.0.1"
lan = false
open_browser = false
log_level = "info"

[storage]
# 留空表示使用 WFP_HOME 或系统默认数据目录；也可写绝对路径
data_dir = ""
# 留空表示 <data_dir>/wfp.db；可写文件名或绝对路径
db_file = ""
retention_days = 30
retention_max_bytes = 5368709120
log_quota_mb = 200

[engine]
default_timeout_seconds = 300

[features]
ai_enabled = false
current_node = ""
'''


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


@dataclass
class Config:
    """启动配置；所有字段都能被 CLI 同名参数覆盖（DR-67/76）。"""
    port: int = 8765
    host: str = "127.0.0.1"
    lan: bool = False
    open_browser: bool = False
    log_level: str = "info"
    data_dir: str = ""
    db_file: str = ""
    retention_days: int = 30
    retention_max_bytes: int = 5 * 1024 ** 3
    log_quota_mb: int = 200
    default_timeout_seconds: float = 300
    ai_enabled: bool = False
    current_node: str = ""
    source: Path | None = None            # 实际读取/生成的配置文件
    paths: DataPaths | None = None        # 生效数据目录
    exists: bool = False                  # 读取时配置文件是否已存在

    # ---------- 校验 ----------
    def validate(self) -> Config:
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ContractError("server.port 必须是 1..65535 的整数", path="server.port")
        if self.host not in LOOPBACK_HOSTS and not self.lan:
            raise ContractError("默认仅允许回环监听；非回环 host 需显式 server.lan=true（DR-72）",
                                path="server.host")
        if self.log_level not in LOG_LEVELS:
            raise ContractError(f"server.log_level 非法：{self.log_level}", path="server.log_level")
        if self.retention_days < 0 or self.retention_max_bytes <= 0 or self.log_quota_mb <= 0:
            raise ContractError("storage 的保留天数/容量/日志配额必须为正数", path="storage")
        if self.default_timeout_seconds <= 0:
            raise ContractError("engine.default_timeout_seconds 必须为正数", path="engine")
        return self

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, source: Path | None = None,
                     exists: bool = True) -> Config:
        flat: dict[str, Any] = {}
        extra_sections = sorted(set(data) - set(SECTION_FIELDS))
        if extra_sections:
            raise ContractError(f"未知配置节：[{extra_sections[0]}]", path=extra_sections[0])
        for section, keys in SECTION_FIELDS.items():
            chunk = data.get(section, {})
            if not isinstance(chunk, dict):
                raise ContractError(f"配置节 [{section}] 必须是表", path=section)
            unknown = sorted(set(chunk) - set(keys))
            if unknown:
                raise ContractError(f"未知配置项：{section}.{unknown[0]}", path=f"{section}.{unknown[0]}")
            flat.update(chunk)
        for key, value in flat.items():
            if key in BOOL_FIELDS and not isinstance(value, bool):
                raise ContractError(f"{key} 必须是布尔值", path=key)
            if key in INT_FIELDS and (not isinstance(value, int) or isinstance(value, bool)):
                raise ContractError(f"{key} 必须是整数", path=key)
            if key in NUM_FIELDS and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise ContractError(f"{key} 必须是数值", path=key)
            if key in STR_FIELDS and not isinstance(value, str):
                raise ContractError(f"{key} 必须是字符串", path=key)
        cfg = cls(**flat)
        cfg.source, cfg.exists = source, exists
        return cfg.validate()

    @classmethod
    def default(cls, paths: DataPaths | None = None) -> Config:
        return cls(paths=paths or DataPaths.resolve()).validate()

    # ---------- 读写 ----------
    @classmethod
    def load(cls, path: str | Path) -> Config:
        p = Path(path).expanduser()
        if not p.is_file():
            return cls(source=p, exists=False).validate()
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ContractError(f"配置文件 TOML 解析失败：{exc}", path=str(p)) from exc
        return cls.from_mapping(data, source=p, exists=True)

    def to_toml(self) -> str:
        """按生效值生成带注释的 TOML（不写回用户已有配置，除非显式 init --force）。"""
        lines = [
            "# WFP 启动配置（由 wfp config init 生成；不含凭据）",
            "# 查看生效值：wfp config show；指定文件：--config / WFP_CONFIG",
            "",
            "[server]",
            f"port = {_scalar(self.port)}",
            f"host = {_scalar(self.host)}",
            f"lan = {_scalar(self.lan)}",
            f"open_browser = {_scalar(self.open_browser)}",
            f"log_level = {_scalar(self.log_level)}",
            "",
            "[storage]",
            "# 留空表示使用 WFP_HOME 或系统默认数据目录",
            f"data_dir = {_scalar(self.data_dir)}",
            "# 留空表示 <data_dir>/wfp.db；可写文件名或绝对路径",
            f"db_file = {_scalar(self.db_file)}",
            f"retention_days = {_scalar(self.retention_days)}",
            f"retention_max_bytes = {_scalar(self.retention_max_bytes)}",
            f"log_quota_mb = {_scalar(self.log_quota_mb)}",
            "",
            "[engine]",
            f"default_timeout_seconds = {_scalar(self.default_timeout_seconds)}",
            "",
            "[features]",
            f"ai_enabled = {_scalar(self.ai_enabled)}",
            f"current_node = {_scalar(self.current_node)}",
            "",
        ]
        return "\n".join(lines)

    def write(self, path: str | Path | None = None, *, force: bool = False) -> Path:
        dest = Path(path).expanduser() if path else (self.source or self.require_paths().config)
        if dest.exists() and not force:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.to_toml(), encoding="utf-8")
        self.source, self.exists = dest, True
        return dest

    def ensure_file(self) -> Path:
        """首次启动自动生成 <数据目录>/config.toml（DR-05/76）。"""
        return self.write()

    # ---------- 解析与覆盖 ----------
    @classmethod
    def resolve(cls, *, config_path: str | Path | None = None, home: str | Path | None = None,
                overrides: dict[str, Any] | None = None) -> Config:
        env_home = os.environ.get(ENV_HOME)
        explicit = config_path or os.environ.get(ENV_CONFIG)
        if explicit:
            source = Path(explicit).expanduser()
        else:
            base = Path(home) if home else (Path(env_home) if env_home else default_home())
            source = base.expanduser() / "config.toml"
        cfg = cls.load(source)
        if home:
            root = Path(home)
        elif env_home:
            root = Path(env_home)
        elif cfg.data_dir:
            root = Path(cfg.data_dir)
        else:
            root = default_home()
        cfg.paths = DataPaths.resolve(root, db_file=cfg.db_file or None)
        if overrides:
            cfg.apply(overrides)
        return cfg.validate()

    def apply(self, overrides: dict[str, Any]) -> Config:
        """只覆盖非 None 且字段名合法的值；CLI 参数优先级最高。"""
        for key, value in overrides.items():
            if value is None:
                continue
            if key not in PUBLIC_FIELDS:
                raise ContractError(f"未知配置项：{key}", path=key)
            if key in BOOL_FIELDS and not isinstance(value, bool):
                raise ContractError(f"{key} 必须是布尔值", path=key)
            setattr(self, key, value)
        return self.validate()

    def require_paths(self) -> DataPaths:
        if self.paths is None:
            self.paths = DataPaths.resolve(self.data_dir or None, db_file=self.db_file or None)
        return self.paths

    def public_dict(self) -> dict[str, Any]:
        """API/CLI 展示用：不含 token/口令/凭据（DR-52/72）。"""
        data: dict[str, Any] = {k: getattr(self, k) for k in PUBLIC_FIELDS}
        data["data_dir_resolved"] = str(self.require_paths().root)
        data["db_path"] = str(self.require_paths().db)
        data["config_file"] = str(self.source) if self.source else ""
        data["config_file_exists"] = self.exists
        data["note"] = "token 存于 session.json；口令/凭据不写入本配置文件"
        return data


__all__ = ["ENV_CONFIG", "ENV_HOME", "EXAMPLE_TOML", "Config"]
