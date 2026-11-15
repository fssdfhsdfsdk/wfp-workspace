"""BUG-006 回归：SSE 命名事件丢失 / 无限轮询 / 控制台时间戳 / 打包自包含递归。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wfp.api.app import create_app, sse_frame
from wfp.cli import _uvicorn_log_config

TOKEN = "sse-token"
HEADERS = {"X-WFP-Token": TOKEN, "Host": "127.0.0.1:8765"}


@pytest.fixture
def web(env):
    app = create_app(env.paths, token=TOKEN, port=8765)
    with TestClient(app) as client:
        yield client, app, env
    app.state.wfp["store"].close()


def test_sse_frame_is_default_message_event() -> None:
    ev = {"v": 1, "seq": 7, "ts": "2026-11-15T21:00:00Z", "run": "r-1",
          "type": "run.finished", "payload": {"status": "succeeded"}}
    frame = sse_frame(ev)
    assert frame.startswith("id: 7\n")
    assert "event:" not in frame          # 命名事件会被 onmessage 丢弃
    assert "data: " in frame
    assert json.loads(frame.split("data: ", 1)[1].strip())["type"] == "run.finished"


def test_uvicorn_console_log_has_datetime() -> None:
    cfg = _uvicorn_log_config("info")
    for name in ("default", "access"):
        assert "%(asctime)s" in cfg["formatters"][name]["fmt"]
        assert cfg["formatters"][name]["datefmt"] == "%Y-%m-%d %H:%M:%S"
    assert cfg["loggers"]["uvicorn.access"]["handlers"] == ["access"]


def test_frontend_stops_polling_and_shows_full_timestamp() -> None:
    root = Path(__file__).resolve().parents[2]
    runs = (root / "frontend/src/pages/Runs.tsx").read_text(encoding="utf-8")
    assert "clearInterval(timer)" in runs
    assert "finished_at" in runs
    assert "stamp(ev.ts)" in runs
    assert "replace('T', ' ')" in runs


def test_pack_output_inside_source_is_rejected(web, env) -> None:
    client, _, _ = web
    src = env.tmp / "my_dev"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")
    yaml_text = f"""apiVersion: wfp/v1
meta:
  id: pack-loop
  name: pack-loop
  version: 1.0.0
  allow_outside_workspace: true
steps:
  - id: s1
    uses: archive.pack@1.0.0
    with:
      src: {src}
      dest: {src / 'backup.zip'}
"""
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": yaml_text}).status_code == 200
    t = client.post("/api/runs/direct", headers=HEADERS,
                    json={"pipeline": "pack-loop", "inputs": {}, "ack_risks": True})
    assert t.status_code == 200, t.text
    result = None
    for _ in range(200):
        s = client.get(f"/api/tasks/{t.json()['task_id']}", headers=HEADERS).json()
        if s["status"] in ("done", "error"):
            result = s
            break
    # 动作级失败会被 _execute 记为 run failed 并正常返回（不冒泡到 task error）
    assert result is not None and result["status"] == "done", result
    body = result["result"]
    assert body["status"] == "failed", body
    assert "自包含递归" in str(body.get("error_code", "")), body
    assert not (src / "backup.zip").exists()
