"""用户问题回归：工作项「运行」是干跑 → 确认执行两阶段，第二步必须真正落盘。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wfp.api.app import create_app

TOKEN = "run-flow-token"
HEADERS = {"X-WFP-Token": TOKEN, "Host": "127.0.0.1:8765"}

USER_YAML = """apiVersion: wfp/v1
inputs:
  properties: {}
  type: object
meta:
  id: test
  name: test
  version: 1.0.5
  allow_outside_workspace: true
steps:
- id: s3jhxf
  uses: archive.pack@1.0.0
  with:
    src: C:\\Users\\Administrator\\Downloads\\my_dev
    dest: C:\\Users\\Administrator\\Downloads\\workspace
"""


@pytest.fixture
def web(env):
    app = create_app(env.paths, token=TOKEN, port=8765)
    with TestClient(app) as client:
        yield client, app, env
    app.state.wfp["store"].close()


def _poll(client: TestClient, task_id: str) -> dict:
    for _ in range(200):
        t = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        if t["status"] == "done":
            return t["result"]
        assert t["status"] != "error", t
    raise AssertionError("任务超时")


def test_run_plan_then_apply_really_writes_archive(web, env) -> None:
    client, _, _ = web
    src = env.tmp / "my_dev"
    src.mkdir()
    (src / "hello.txt").write_text("hello", encoding="utf-8")
    dest_base = env.tmp / "workspace"
    yaml_text = (USER_YAML
                 .replace("C:\\Users\\Administrator\\Downloads\\my_dev", str(src))
                 .replace("C:\\Users\\Administrator\\Downloads\\workspace", str(dest_base)))
    created = client.post("/api/pipelines", headers=HEADERS, json={"yaml": yaml_text})
    assert created.status_code == 200, created.text

    # 第 1 步：UI「干跑」= POST /api/runs phase=plan，零副作用
    planned = client.post("/api/runs", headers=HEADERS,
                          json={"pipeline": "test", "inputs": {}, "phase": "plan"})
    assert planned.status_code == 200, planned.text
    report = _poll(client, planned.json()["task_id"])
    assert report["items"] and report["run_id"]
    assert not Path(f"{dest_base}.zip").exists()      # 干跑确实没写文件

    # 第 2 步：UI「确认执行」= POST /api/runs/{run_id}/apply
    applied = client.post(f"/api/runs/{report['run_id']}/apply", headers=HEADERS,
                          json={"ack_risks": True})
    assert applied.status_code == 200, applied.text
    result = _poll(client, applied.json()["task_id"])
    assert result["status"] == "succeeded", result
    assert Path(f"{dest_base}.zip").is_file()         # 真正落盘


def test_run_bundle_labels_the_two_entries(web) -> None:
    client, _, _ = web
    bundle = client.get("/app.js").text
    for marker in ("直接运行", "干跑", "我已确认跳过干跑预览", "仅生成 PlanReport"):
        assert marker in bundle, marker


def test_direct_run_requires_ack_and_really_executes(web, env) -> None:
    client, _, _ = web
    src = env.tmp / "my_dev"
    src.mkdir()
    (src / "hello.txt").write_text("hello", encoding="utf-8")
    dest_base = env.tmp / "workspace"
    yaml_text = (USER_YAML
                 .replace("C:\\Users\\Administrator\\Downloads\\my_dev", str(src))
                 .replace("C:\\Users\\Administrator\\Downloads\\workspace", str(dest_base)))
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": yaml_text}).status_code == 200

    # 未确认必须被拒（DR-49 人工确认闸）
    bad = client.post("/api/runs/direct", headers=HEADERS,
                      json={"pipeline": "test", "inputs": {}})
    assert bad.status_code >= 400, bad.text
    assert not Path(f"{dest_base}.zip").exists()

    # 一次提交内部完成 plan + apply，真正落盘
    ok = client.post("/api/runs/direct", headers=HEADERS,
                     json={"pipeline": "test", "inputs": {}, "ack_risks": True})
    assert ok.status_code == 200, ok.text
    result = _poll(client, ok.json()["task_id"])
    assert result["status"] == "succeeded", result
    assert Path(f"{dest_base}.zip").is_file()
