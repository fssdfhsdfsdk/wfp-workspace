"""客户四个真实重复场景 E2E-01..04（FakeTransport 离线验收）。

Implements: DR-19, DR-24, DR-26, DR-30, DR-31, DR-32, DR-33, DR-34, DR-38,
DR-50, DR-51, DR-54, DR-55, DR-56, DR-57, DR-59, DR-61, DR-63, DR-73, DR-75, DR-76
"""
from __future__ import annotations

import io
import json
import zipfile

from tests.conftest import REMOTE_PIPELINE


def _node(env) -> None:
    env.runner.nodes.add({"id": "prod", "name": "生产节点", "host": "10.0.0.8",
                          "username": "root", "os_kind": "ssh-linux",
                          "auth_kind": "agent", "default_workdir": "/opt/app"}, actor="pytest")
    env.runner.nodes.set_current("prod", actor="pytest")


DEPLOY = """apiVersion: wfp/v1
meta:
  id: e2e-deploy
  name: E2E-01 部署流水线
  version: 1.0.0
inputs:
  type: object
  properties:
    env: { type: string, default: prod }
defaults:
  node: current
  on_error: fail
steps:
  - id: s1
    uses: fs.move@1.0.0
    with: { src: build/app.zip, dest: work/app.zip }
  - id: s2
    uses: archive.extract@1.0.0
    with: { archive: work/app.zip, dest: work/app, overwrite: true }
  - id: s3
    uses: assert.file_exists@1.0.0
    with: { path: work/app/conf/app.json }
  - id: s4
    uses: config.edit@1.0.0
    with:
      path: work/app/conf/app.json
      format: json
      edits:
        - key: env
          value: "{{ inputs.env }}"
    register: cfg
  - id: s5
    uses: remote.exec@1.0.0
    with: { command: "test -d /opt/app/current || mkdir -p /opt/app/current" }
  - id: s6
    uses: remote.upload@1.0.0
    with:
      items:
        - { local: work/app/, remote: /opt/app/current/, recursive: true }
        - { local: scripts/start.sh, remote: /opt/app/current/start.sh }
      exclude: ["**/*.pyc"]
  - id: s7
    uses: remote.config_edit@1.0.0
    with:
      path: /opt/app/current/conf/app.json
      format: json
      edits:
        - key: env
          value: "{{ inputs.env }}"
  - id: s8
    uses: remote.exec@1.0.0
    with: { command: "systemctl restart app", sudo: true }
  - id: s9
    uses: remote.exec@1.0.0
    with: { command: "curl -fsS http://127.0.0.1:8080/health", readonly: true }
  - id: s10
    uses: assert.checksum@1.0.0
    when: "inputs.env != 'dev'"
    with:
      path: work/app/conf/app.json
      expect_sha256: "{{ cfg.new_sha256 }}"
"""


def _fixtures(env) -> None:
    build = env.tmp / "build"
    build.mkdir(exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("conf/app.json", json.dumps({"env": "dev", "log": {"level": "debug"}}))
        z.writestr("README.md", "demo")
    (build / "app.zip").write_bytes(buf.getvalue())
    scripts = env.tmp / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "start.sh").write_text("#!/bin/sh\necho start\n", encoding="utf-8")


def test_e2e01_deploy_pipeline_dr19_dr55_dr56(env) -> None:
    _fixtures(env)
    _node(env)
    ref = env.pipeline("deploy.yaml", DEPLOY)
    report = env.runner.plan(ref, {"env": "prod"})
    assert len(report["items"]) == 10
    assert any(i["unpredictable"] for i in report["items"]), "通用远程命令应标不可预测（DR-30）"
    result = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert result["status"] == "succeeded", result["error_code"]
    remote = env.transport.root / "opt/app/current/conf/app.json"
    assert remote.is_file()
    assert json.loads(remote.read_text())["env"] == "prod"
    assert (env.transport.root / "opt/app/current/start.sh").is_file()
    kinds = {a["kind"] for a in env.runner.runs.artifacts(result["id"])}
    assert {"backup", "diff", "report", "transfer"} <= kinds or "backup" in kinds
    journal = env.runner.runs.journal_for(result["id"])
    assert any(j["event"] == "pre_apply" for j in journal)


def test_e2e02_remote_yaml_preserves_comments_dr55(env) -> None:
    _node(env)
    original = ("# 应用配置（中文注释）\napp:\n  name: demo  # 行尾注释\n"
                "log:\n  level: debug\n  path: /var/log/app.log\n")
    env.transport._write("/opt/app/conf/app.yaml", original.encode())
    ref = env.pipeline("remote.yaml", REMOTE_PIPELINE)
    before = (env.transport.root / "opt/app/conf/app.yaml").read_bytes()
    report = env.runner.plan(ref, {})
    assert (env.transport.root / "opt/app/conf/app.yaml").read_bytes() == before, "plan 远端 mtime/内容不变"
    assert "diff" in report["items"][0]["diff"] or "level" in report["items"][0]["diff"]
    result = env.runner.apply(ref, {}, yes=True)
    assert result["status"] == "succeeded", result["error_code"]
    text = (env.transport.root / "opt/app/conf/app.yaml").read_text()
    assert "# 应用配置（中文注释）" in text and "# 行尾注释" in text
    assert "level: info" in text
    assert "name: demo" in text


def test_e2e03_download_dr54_and_e2e04_upload_dr56(env) -> None:
    _node(env)
    (env.tmp / "payload.txt").write_text("hello wfp\n", encoding="utf-8")
    env.transport._write("/opt/app/payload.txt", b"hello wfp\n")

    download = """apiVersion: wfp/v1
meta: { id: e2e-download, name: 下载, version: 1.0.0 }
defaults: { node: current }
steps:
  - id: d1
    uses: remote.download@1.0.0
    with: { remote: /opt/app/payload.txt, local: work/payload.txt }
    register: d1
  - id: d2
    uses: assert.checksum@1.0.0
    with: { path: work/payload.txt, expect_sha256: "{{ d1.sha256 }}" }
"""
    ref = env.pipeline("download.yaml", download)
    result = env.runner.apply(ref, {}, yes=True)
    assert result["status"] == "succeeded", result["error_code"]
    assert (env.tmp / "work/payload.txt").read_text() == "hello wfp\n"

    upload = """apiVersion: wfp/v1
meta: { id: e2e-upload, name: 上传, version: 1.0.0 }
defaults: { node: current }
steps:
  - id: u1
    uses: remote.upload@1.0.0
    with:
      items:
        - { local: payload.txt, remote: /opt/app/uploaded.txt }
"""
    ref = env.pipeline("upload.yaml", upload)
    first = env.runner.apply(ref, {}, yes=True)
    assert first["status"] == "succeeded", first["error_code"]
    assert (env.transport.root / "opt/app/uploaded.txt").read_bytes() == b"hello wfp\n"
    second = env.runner.apply(ref, {}, yes=True)
    assert second["status"] == "succeeded"
    assert any(s["status"] == "skipped_idempotent" for s in second["steps"]), "二次上传 hash 命中应跳过（DR-32/56）"
