"""数据目录与可迁移性。Implements: DR-05, DR-67。

所有可变状态集中在单一数据目录（默认 %LOCALAPPDATA%\\WFP，可由 WFP_HOME 覆盖）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "WFP_HOME"


def default_home() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "WFP"
    return Path.home() / ".local" / "share" / "WFP"


@dataclass(frozen=True)
class DataPaths:
    root: Path
    db_file: str = "wfp.db"        # 可由 config.toml 的 storage.db_file 覆盖（DR-05/67）

    @classmethod
    def resolve(cls, home: str | Path | None = None, *, db_file: str | None = None) -> DataPaths:
        root = Path(home).expanduser().resolve() if home else default_home()
        return cls(root=root, db_file=db_file or "wfp.db")

    @property
    def db(self) -> Path:
        raw = Path(self.db_file).expanduser()
        return raw if raw.is_absolute() else self.root / raw
    @property
    def config(self) -> Path: return self.root / "config.toml"
    @property
    def session(self) -> Path: return self.root / "session.json"
    @property
    def impl(self) -> Path: return self.root / "impl"
    @property
    def logs(self) -> Path: return self.root / "logs"
    @property
    def artifacts(self) -> Path: return self.root / "artifacts"
    @property
    def venvs(self) -> Path: return self.root / "venvs"
    @property
    def keys(self) -> Path: return self.root / "keys"
    @property
    def backups(self) -> Path: return self.root / "backups"
    @property
    def pipelines(self) -> Path: return self.root / "pipelines"

    def ensure(self) -> DataPaths:
        for p in (self.root, self.impl, self.logs, self.artifacts, self.venvs,
                  self.keys, self.backups, self.pipelines):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def impl_path(self, sha256: str, ext: str = ".py") -> Path:
        """内容寻址存储实现体（DR-10）。"""
        return self.impl / sha256[:2] / sha256[2:4] / f"{sha256}{ext}"

    def writable(self) -> bool:
        try:
            probe = self.root / ".wfp-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False
