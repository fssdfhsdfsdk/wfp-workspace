"""【工作项-复杂工作#01】11 步端到端验收（TDD）。

移动压缩包 → 解压到当前目录 → 断言配置存在 → 改本地 JSON → 执行动作库中编辑加入的
Python 脚本 → 连接当前目标节点 → 远程命令1 → 上传文件夹+2 个相对路径文件 →
改远端 JSON → 远程命令2 → 远程命令3。

同时验收：命令/脚本异常即失败；日志流能看到 Python 脚本与远程命令的 stdout。
"""
from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

from wfp.actions.manifests import BUILTIN_MANIFESTS
from wfp.engine.journal import EventWriter

PYTHON_IMPL = '''"""动作库中编辑加入的 Python 基本动作。"""
import json
import os
import sys

params = json.loads(os.environ.get("WFP_PARAMS", "{}"))
print("python-script-ok label=" + str(params.get("label", "")))
if params.get("fail"):
    print("python-script-failed", file=sys.stderr)
    sys.exit(3)
print("WFP_OUTPUT::" + json.dumps({"marker": "py-ok", "label": params.get("label", "")}))
'''

PIPELINE = """apiVersion: wfp/v1
meta:
  id: complex-01
  name: 复杂工作#01
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
        - { key: env, value: "{{ inputs.env }}" }
  - id: s5
    uses: custom.python.report@1.0.0
    with: { label: complex-01, args: [] }
    register: py
  - id: s6
    uses: remote.exec@1.0.0
    with: { command: whoami, readonly: true }
  - id: s7
    uses: remote.exec@1.0.0
    with: { command: "test -d /opt/app/current || mkdir -p /opt/app/current" }
  - id: s8
    uses: remote.upload@1.0.0
    with:
      items:
        - { local: folder/, remote: /opt/app/current/folder/, recursive: true }
        - { local: payload1.txt, remote: /opt/app/current/payload1.txt }
        - { local: payload2.txt, remote: /opt/app/current/payload2.txt }
      exclude: ["**/*.pyc"]
  - id: s9
    uses: remote.config_edit@1.0.0
    with:
      path: /opt/app/current/conf/app.json
      format: json
      edits:
        - { key: env, value: "{{ py.marker }}" }
  - id: s10
    uses: remote.exec@1.0.0
    with: { command: "systemctl restart app", sudo: true }
  - id: s11
    uses: remote.exec@1.0.0
    with: { command: "curl -fsS http://127.0.0.1:8080/health", readonly: true }
"""


def _node(env) -> None:
    env.runner.nodes.add({"id": "prod", "name": "生产节点", "host": "10.0.0.8",
                          "username": "root", "os_kind": "ssh-linux",
                          "auth_kind": "agent", "default_workdir": "/opt/app"}, actor="pytest")
    env.runner.nodes.set_current("prod", actor="pytest")


def _python_action(env) -> None:
    manifest = deepcopy(BUILTIN_MANIFESTS["script.local"])
    manifest["id"] = "custom.python.report"
    manifest["name"] = "自定义 Python 报告"
    manifest["params"]["properties"]["label"] = {"type": "string"}
    manifest["params"]["properties"]["fail"] = {"type": "boolean"}
    env.runner.actions.create(manifest, PYTHON_IMPL, actor="pytest")


def _fixtures(env) -> None:
    build = env.tmp / "build"
    build.mkdir(exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("conf/app.json", json.dumps({"env": "dev"}))
        z.writestr("README.md", "demo")
    (build / "app.zip").write_bytes(buf.getvalue())
    folder = env.tmp / "folder"
    (folder / "sub").mkdir(parents=True, exist_ok=True)
    (folder / "a.txt").write_text("a\n", encoding="utf-8")
    (folder / "sub" / "b.txt").write_text("b\n", encoding="utf-8")
    (env.tmp / "payload1.txt").write_text("payload-1\n", encoding="utf-8")
    (env.tmp / "payload2.txt").write_text("payload-2\n", encoding="utf-8")
    env.transport._write("/opt/app/current/conf/app.json", b'{"env": "dev"}\n')


def _events(env, run_id: str) -> list[dict]:
    return EventWriter.tail(env.paths.logs / run_id / "events.ndjson")


def test_complex_work_01_all_steps_succeed(env) -> None:
    _fixtures(env)
    _node(env)
    _python_action(env)
    ref = env.pipeline("complex-01.yaml", PIPELINE)

    report = env.runner.plan(ref, {"env": "prod"})
    assert len(report["items"]) == 11, [i["step_id"] for i in report["items"]]

    result = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert result["status"] == "succeeded", (result.get("error_code"), result["steps"])
    assert len(result["steps"]) == 11
    assert all(s["status"] == "succeeded" for s in result["steps"]), result["steps"]

    # 1~4：压缩包搬运/解压/断言/改 JSON
    assert (env.tmp / "work/app.zip").is_file()
    local_cfg = json.loads((env.tmp / "work/app/conf/app.json").read_text())
    assert local_cfg["env"] == "prod"

    # 6~8：连接当前节点、三条远程命令、上传文件夹与 2 个文件
    assert "whoami" in env.transport.exec_log
    assert any("systemctl restart app" in c for c in env.transport.exec_log)
    assert any("curl -fsS" in c for c in env.transport.exec_log)
    remote_root = env.transport.root / "opt/app/current"
    for rel in ("folder/a.txt", "folder/sub/b.txt", "payload1.txt", "payload2.txt"):
        assert (remote_root / rel).is_file(), rel

    # 9：远端 JSON 被改，且用到了 Python 脚本 register 的输出
    remote_cfg = json.loads((remote_root / "conf/app.json").read_text())
    assert remote_cfg["env"] == "py-ok", remote_cfg

    # 日志流：Python 脚本与远程命令的 stdout 都能看到
    stdout = "\n".join(str(e["payload"].get("text", "")) for e in _events(env, result["id"])
                       if e["type"] == "step.stdout")
    assert "python-script-ok label=complex-01" in stdout, stdout
    assert "root" in stdout, stdout                       # whoami 的远端输出
    assert "fake-ok: systemctl restart app" in stdout, stdout
    assert "fake-ok: curl -fsS" in stdout, stdout
    assert "b'" not in stdout                             # 不能是 bytes 转义串


def test_complex_work_01_remote_command_failure_aborts(env) -> None:
    _fixtures(env)
    _node(env)
    _python_action(env)
    broken = PIPELINE.replace("command: \"systemctl restart app\"", "command: \"false\"")
    ref = env.pipeline("complex-01-fail.yaml", broken)
    result = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert result["status"] == "failed", result["steps"]
    # readonly 的命令3 在 plan 阶段会做只读探测（DR-30），因此 exec_log 可能已含它；
    # 关键断言：apply 阶段失败后没有为 s11 生成任何步骤记录。
    assert not any(s["step_id"] == "s11" for s in result["steps"]), "失败后不应继续执行命令3"


def test_complex_work_01_python_script_failure_aborts(env) -> None:
    _fixtures(env)
    _node(env)
    _python_action(env)
    broken = PIPELINE.replace("with: { label: complex-01, args: [] }",
                              "with: { label: complex-01, args: [], fail: true }")
    ref = env.pipeline("complex-01-pyfail.yaml", broken)
    result = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert result["status"] == "failed", result["steps"]
    assert "3" in str(result.get("error_code") or ""), result.get("error_code")


def test_complex_work_01_missing_config_aborts(env) -> None:
    _fixtures(env)
    _node(env)
    _python_action(env)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("README.md", "no config")
    (env.tmp / "build/app.zip").write_bytes(buf.getvalue())
    ref = env.pipeline("complex-01-nocfg.yaml", PIPELINE)
    result = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert result["status"] == "failed", result["steps"]
    assert not (env.tmp / "work/app/conf/app.json").exists()


def test_log_view_renders_script_and_remote_stdout_as_text() -> None:
    src = Path(__file__).resolve().parents[2] / "frontend/src/pages/Runs.tsx"
    text = src.read_text(encoding="utf-8")
    assert "step.stdout" in text and "p.text" in text
