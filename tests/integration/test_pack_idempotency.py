"""BUG-007 回归：archive.pack 因 dest 基名已存在被误判幂等，run 成功却无压缩包。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wfp.api.app import create_app

TOKEN = "pack-token"
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


def test_pack_never_skips_and_really_writes_archive(web, env) -> None:
    client, _, _ = web
    src = env.tmp / "my_dev"
    src.mkdir()
    (src / "hello.txt").write_text("hello", encoding="utf-8")
    dest_base = env.tmp / "workspace"
    dest_base.mkdir()          # 用户场景：dest 同名目录本来就存在
    out = Path(str(dest_base) + ".zip")

    yaml_text = f"""apiVersion: wfp/v1
meta:
  id: pack-no-skip
  name: pack-no-skip
  version: 1.0.0
  allow_outside_workspace: true
steps:
  - id: s1
    uses: archive.pack@1.0.0
    with:
      src: {src}
      dest: {dest_base}
"""
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": yaml_text}).status_code == 200

    # 干跑：目标应指向真实输出 workspace.zip，且零副作用
    planned = client.post("/api/runs", headers=HEADERS,
                          json={"pipeline": "pack-no-skip", "inputs": {}, "phase": "plan"})
    report = _poll(client, planned.json()["task_id"])
    assert str(out) in report["items"][0]["targets"], report["items"][0]
    assert not out.exists()

    # 第一次直接运行：不能因 workspace 目录存在而 skipped_idempotent
    t = client.post("/api/runs/direct", headers=HEADERS,
                    json={"pipeline": "pack-no-skip", "inputs": {}, "ack_risks": True})
    assert t.status_code == 200, t.text
    r = _poll(client, t.json()["task_id"])
    assert r["status"] == "succeeded", r
    assert [s["status"] for s in r["steps"]] == ["succeeded"], r["steps"]
    assert out.is_file()
    assert "hello.txt" in zipfile.ZipFile(out).namelist()

    # 第二次：仍真正打包（DR-32 的跳过清单不含 archive.pack）
    t2 = client.post("/api/runs/direct", headers=HEADERS,
                     json={"pipeline": "pack-no-skip", "inputs": {}, "ack_risks": True})
    r2 = _poll(client, t2.json()["task_id"])
    assert r2["status"] == "succeeded", r2
    assert [s["status"] for s in r2["steps"]] == ["succeeded"], r2["steps"]
    assert out.is_file()
