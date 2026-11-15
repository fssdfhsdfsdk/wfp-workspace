"""WFP 测试夹具：临时 WFP_HOME + 内存/文件 FakeTransport，无网络依赖（DR-75）。

覆盖分层：tests/unit、tests/integration、tests/property、tests/e2e、tests/perf、tests/remote。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from wfp.engine.phases import Runner
from wfp.paths import DataPaths
from wfp.store.db import Store
from wfp.transport.fake import FakeTransport


@dataclass
class Env:
    tmp: Path
    paths: DataPaths
    store: Store
    runner: Runner
    transport: FakeTransport

    def pipeline(self, name: str, text: str) -> str:
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    home = tmp_path / "home"
    monkeypatch.setenv("WFP_HOME", str(home))
    monkeypatch.setenv("WFP_FAKE_REMOTE", str(tmp_path / "fake-remote"))
    paths = DataPaths.resolve().ensure()
    store = Store(paths.db)
    store.migrate()
    transport = FakeTransport(tmp_path / "remote-root")
    runner = Runner(paths, store, actor="pytest", transport=transport)
    e = Env(tmp_path, paths, store, runner, transport)
    try:
        yield e
    finally:
        e.close()


LOCAL_PIPELINE = """apiVersion: wfp/v1
meta:
  id: local-demo
  name: 本地演示
  version: 1.0.0
inputs:
  type: object
  properties:
    env: { type: string, default: prod }
steps:
  - id: s1
    uses: config.edit@1.0.0
    with:
      path: conf/app.json
      format: json
      edits:
        - key: env
          value: "{{ inputs.env }}"
    register: cfg
  - id: s2
    uses: assert.file_exists@1.0.0
    with:
      path: conf/app.json
  - id: s3
    uses: config.edit@1.0.0
    when: "inputs.env != 'dev'"
    with:
      path: conf/app.json
      format: json
      edits:
        - key: server.port
          value: 8080
"""

REMOTE_PIPELINE = """apiVersion: wfp/v1
meta:
  id: remote-demo
  name: 远端演示
  version: 1.0.0
defaults:
  node: current
steps:
  - id: r1
    uses: remote.config_edit@1.0.0
    with:
      path: /opt/app/conf/app.yaml
      format: yaml
      edits:
        - key: log.level
          value: info
  - id: r2
    uses: remote.exec@1.0.0
    with:
      command: "test -s /opt/app/conf/app.yaml"
      readonly: true
"""
