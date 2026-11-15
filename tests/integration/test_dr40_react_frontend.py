"""重建后的 React 前端静态产物、完整 REST 交互与"刷新后 apply"缺陷回归。

Implements: DR-03, DR-07, DR-13, DR-28, DR-40, DR-41, DR-42, DR-43, DR-45,
DR-46, DR-49, DR-50, DR-62, DR-63, DR-65, DR-67, DR-78, DR-80, DR-82
"""
from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from tests.conftest import LOCAL_PIPELINE
from wfp.api.app import create_app

TOKEN = "frontend-token"
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


def _pipeline(env) -> str:
    (env.tmp / "conf").mkdir(exist_ok=True)
    (env.tmp / "conf" / "app.json").write_text('{"env": "dev"}\n', encoding="utf-8")
    return LOCAL_PIPELINE.replace("meta:\n  id: local-demo",
                                  f"meta:\n  id: local-demo\n  workdir: {env.tmp}")


def test_dr40_react_bundle_is_offline_and_routes_are_direct(web) -> None:
    client, _, _ = web
    html = client.get("/").text
    assert TOKEN in html and "__WFP_TOKEN__" not in html
    assert "cdn" not in html.lower()
    for p in ("actions", "pipelines", "editor", "runs", "nodes", "history", "settings"):
        assert f"#/{p}" in html
    js = re.search(r'src="(/assets/index-[^"]+\.js)"', html)
    css = re.search(r'href="(/assets/index-[^"]+\.css)"', html)
    assert js and client.get(js.group(1)).status_code == 200
    assert css and client.get(css.group(1)).status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    bundle = client.get("/app.js").text
    for marker in ("React Flow", "Monaco", "rjsf", "grid cols3", "输出查看窗口", "干跑", "确认执行"):
        assert marker in bundle, marker


def test_dr41_action_enable_duplicate_diff_and_trial(web) -> None:
    client, _, _ = web
    assert client.post("/api/actions/config.edit/enable", headers=HEADERS, json={"enabled": False}).status_code == 200
    assert client.get("/api/actions/config.edit", headers=HEADERS).json()["enabled"] is False
    assert client.post("/api/actions/config.edit/enable", headers=HEADERS, json={"enabled": True}).status_code == 200
    dup = client.post("/api/actions/config.edit/duplicate", headers=HEADERS, json={"new_id": "config.copy"})
    assert dup.status_code == 200, dup.text
    d = client.get("/api/actions/config.edit/diff?from_version=1.0.0&to_version=1.0.0", headers=HEADERS).json()
    assert "before" in d and "after" in d and "diff" in d
    trial = client.post("/api/actions/assert.file_exists/trial", headers=HEADERS,
                        json={"params": {"path": "README.md"}, "phase": "plan"})
    assert trial.status_code == 200, trial.text
    assert trial.json()["items"][0]["action_ref"] == "assert.file_exists@1.0.0"


def test_dr28_pipeline_duplicate_download_and_graph_params(web) -> None:
    client, _, env = web
    created = client.post("/api/pipelines", headers=HEADERS, json={"yaml": _pipeline(env)})
    assert created.status_code == 200, created.text
    dup = client.post("/api/pipelines/local-demo/duplicate", headers=HEADERS,
                      json={"new_id": "local-demo-copy"})
    assert dup.status_code == 200, dup.text
    z = client.get("/api/pipelines/local-demo/download", headers=HEADERS)
    assert z.status_code == 200 and z.content[:2] == b"PK"
    graph = client.get("/api/pipelines/local-demo/graph", headers=HEADERS).json()
    assert graph["nodes"][0]["with"]["path"] == "conf/app.json"


def test_dr50_node_edit_and_dr67_maintenance_downloads(web) -> None:
    client, _, _ = web
    client.post("/api/nodes", headers=HEADERS,
                json={"id": "n1", "name": "节点1", "host": "10.0.0.1", "username": "root",
                      "description": "旧描述"})
    updated = client.put("/api/nodes/n1", headers=HEADERS, json={"description": "新描述"})
    assert updated.status_code == 200 and updated.json()["description"] == "新描述"
    assert client.get("/api/maintenance/doctor", headers=HEADERS).json()["checks"]
    for path in ("/api/maintenance/export/download", "/api/maintenance/diagnostics/download"):
        r = client.get(path, headers=HEADERS)
        assert r.status_code == 200 and r.content[:2] == b"PK", path
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"v": 1}))
    up = client.post("/api/maintenance/import/upload", headers=HEADERS,
                     files={"file": ("minimal.zip", buf.getvalue(), "application/zip")})
    assert up.status_code == 200, up.text
    assert client.post("/api/ai/accept?id=draft-x", headers=HEADERS).json()["status"] == "active"


def test_dr49_apply_survives_page_refresh_and_dr63_evidence(web) -> None:
    client, app, env = web
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": _pipeline(env)}).status_code == 200
    created = client.post("/api/runs", headers=HEADERS,
                          json={"pipeline": "local-demo", "inputs": {}, "phase": "plan"})
    report = _poll(client, created.json()["task_id"])
    # 模拟浏览器刷新 / 新进程：内存中的 PlanReport 缓存全部丢失
    app.state.wfp["manager"].plans.clear()
    applied = client.post(f"/api/runs/{report['run_id']}/apply", headers=HEADERS,
                          json={"ack_risks": True})
    assert applied.status_code == 200, applied.text
    result = _poll(client, applied.json()["task_id"])
    assert result["status"] == "succeeded", result
    rid = result["id"]
    events = client.get(f"/api/runs/{rid}/events.ndjson", headers=HEADERS)
    assert events.status_code == 200 and b"run.finished" in events.content
    evidence = client.get(f"/api/runs/{rid}/evidence.zip", headers=HEADERS)
    assert evidence.status_code == 200 and evidence.content[:2] == b"PK"
    replay = client.post(f"/api/runs/{rid}/replay", headers=HEADERS)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "succeeded"


def test_dr45_multi_delta_patch_updates_expected_sha_between_deltas(web) -> None:
    client, _, env = web
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": _pipeline(env)}).status_code == 200
    yaml = client.get("/api/pipelines/local-demo/yaml", headers=HEADERS).json()
    r = client.post("/api/pipelines/local-demo/patch", headers=HEADERS, json={
        "sha256": yaml["sha256"],
        "deltas": [
            {"kind": "update_param", "step_id": "s1", "field": "name", "value": "改名一"},
            {"kind": "update_param", "step_id": "s2", "field": "name", "value": "改名二"},
        ],
    })
    assert r.status_code == 200, r.text
    assert "改名一" in r.json()["text"] and "改名二" in r.json()["text"]


def test_dr45_save_yaml_preserves_comments_without_version_line(env) -> None:
    from wfp.store.pipelines import PipelineStore
    text = """# 头部注释
apiVersion: wfp/v1
meta:
  id: no-version
  name: 无版本字段
steps:
  - id: s1
    uses: assert.file_exists@1.0.0
    with:
      path: a.txt  # 行尾注释
"""
    ps = PipelineStore(env.store, env.paths)
    ps.create(text, actor="pytest")
    saved = ps.save_yaml("no-version", text, if_match=ps.yaml_text("no-version")[1], actor="pytest")
    new_text, _ = ps.yaml_text("no-version")
    assert saved["version"] == "1.0.1"
    assert "# 头部注释" in new_text and "# 行尾注释" in new_text
    assert "version: 1.0.1" in new_text
