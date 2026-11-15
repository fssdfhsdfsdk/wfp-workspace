"""运行日志增强与运行历史删除的回归用例。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wfp.api.app import create_app
from wfp.engine.journal import EventWriter
from wfp.store.runs import RunStore

TOKEN = "observe-token"
HEADERS = {"X-WFP-Token": TOKEN, "Host": "127.0.0.1:8765"}


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


def _pack_pipeline(web, env) -> str:
    client, _, _ = web
    src = env.tmp / "my_dev"
    src.mkdir(exist_ok=True)
    (src / "a.txt").write_text("a", encoding="utf-8")
    dest = env.tmp / "backup"
    yaml_text = f"""apiVersion: wfp/v1
meta:
  id: observe-demo
  name: observe-demo
  version: 1.0.0
  allow_outside_workspace: true
steps:
  - id: s1
    uses: archive.pack@1.0.0
    with:
      src: {src}
      dest: {dest}
"""
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": yaml_text}).status_code == 200
    return str(dest) + ".zip"


def test_step_events_print_command_params_and_result(web, env) -> None:
    client, _, _ = web
    _pack_pipeline(web, env)
    t = client.post("/api/runs/direct", headers=HEADERS,
                    json={"pipeline": "observe-demo", "inputs": {}, "ack_risks": True})
    assert t.status_code == 200
    result = _poll(client, t.json()["task_id"])
    assert result["status"] == "succeeded", result
    events = EventWriter.tail(env.paths.logs / result["id"] / "events.ndjson")

    run_started = next(e for e in events if e["type"] == "run.started")
    assert run_started["payload"]["pipeline"] == "observe-demo"
    assert "inputs" in run_started["payload"] and "workdir" in run_started["payload"]

    started = next(e for e in events if e["type"] == "step.started")
    p = started["payload"]
    assert p["action"] == "archive.pack@1.0.0"
    assert p["params"]["src"] and p["params"]["dest"]
    assert p["command"] and p["entry"] and p["targets"] and p["workdir"]

    finished = next(e for e in events if e["type"] == "step.finished")
    f = finished["payload"]
    assert f["status"] == "succeeded" and "outputs" in f
    assert "archive" in f["outputs"] and "duration_ms" in f and "attempt" in f


def test_delete_run_cascades_records_and_files(web, env) -> None:
    client, _, _ = web
    archive = _pack_pipeline(web, env)
    t = client.post("/api/runs/direct", headers=HEADERS,
                    json={"pipeline": "observe-demo", "inputs": {}, "ack_risks": True})
    rid = _poll(client, t.json()["task_id"])["id"]
    assert Path(archive).is_file()
    assert (env.paths.logs / rid / "events.ndjson").is_file()
    assert client.get(f"/api/runs/{rid}", headers=HEADERS).status_code == 200

    r = client.delete(f"/api/runs/{rid}", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": rid, "deleted": True}
    assert client.get(f"/api/runs/{rid}", headers=HEADERS).status_code >= 400
    assert not (env.paths.logs / rid).exists()
    assert not (env.paths.artifacts / rid).exists()
    assert rid not in [x["id"] for x in client.get("/api/runs", headers=HEADERS).json()]


def test_delete_rejects_non_terminal_run(web, env) -> None:
    client, _, _ = web
    store = RunStore(env.store, env.paths)
    store.create("r-running", pipeline_id="x", version="1.0.0", phase="apply",
                 trigger="pytest", inputs={}, node_snapshot=None, action_versions={})
    store.set_status("r-running", "running")
    r = client.delete("/api/runs/r-running", headers=HEADERS)
    assert r.status_code >= 400, r.text
    assert store.get("r-running")["status"] == "running"


def test_bundle_exposes_verify_logs_and_delete(web) -> None:
    client, _, _ = web
    bundle = client.get("/app.js").text
    for marker in ("验证全部动作", "命令/入口", "点击展开完整事件 JSON", "删除运行记录"):
        assert marker in bundle, marker
