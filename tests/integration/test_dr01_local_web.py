"""本地 Web 平台：REST/SSE 契约、token/Origin 安全边界、7 页离线 SPA。

Implements: DR-01, DR-02, DR-03, DR-07, DR-09, DR-13, DR-28, DR-40, DR-41,
DR-42, DR-43, DR-45, DR-46, DR-49, DR-50, DR-51, DR-52, DR-60, DR-61, DR-63,
DR-65, DR-66, DR-67, DR-68, DR-69, DR-72, DR-77, DR-78, DR-80, DR-82, DR-83
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import LOCAL_PIPELINE
from wfp.api.app import create_app
from wfp.engine.journal import EventWriter

TOKEN = "test-token"
HEADERS = {"X-WFP-Token": TOKEN, "Host": "127.0.0.1:8765"}


@pytest.fixture
def client(env):
    app = create_app(env.paths, token=TOKEN, port=8765)
    with TestClient(app) as c:
        yield c
    app.state.wfp["store"].close()


def _pipeline_text(env) -> str:
    return LOCAL_PIPELINE.replace("meta:\n  id: local-demo", f"meta:\n  id: local-demo\n  workdir: {env.tmp}")


def _seed(env) -> None:
    (env.tmp / "conf").mkdir(exist_ok=True)
    (env.tmp / "conf" / "app.json").write_text('{"env": "dev"}\n', encoding="utf-8")


def test_dr01_health_and_dr07_offline_spa(client) -> None:
    assert client.get("/api/health").json()["ok"] is True
    html = client.get("/").text
    for label in ("动作库", "工作项", "编排", "运行面板", "节点与凭据", "历史与报表", "设置"):
        assert label in html
    assert TOKEN in html and "cdn" not in html.lower()
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_dr72_token_and_origin_guards(client) -> None:
    assert client.get("/api/actions", headers={"Host": "127.0.0.1:8765"}).status_code == 401
    assert client.get("/api/actions", headers={"X-WFP-Token": TOKEN,
                                               "Host": "evil.example"}).status_code == 403
    r = client.post("/api/nodes", headers={**HEADERS, "Origin": "http://evil.example"},
                    json={"id": "x", "host": "h", "username": "u"})
    assert r.status_code == 403
    assert client.get("/api/actions", headers=HEADERS).status_code == 200


def test_dr13_action_library_and_contract_suite(client) -> None:
    actions = client.get("/api/actions", headers=HEADERS).json()
    assert len(actions) >= 16
    suite = client.post("/api/actions/config.edit/test", headers=HEADERS).json()
    assert suite["ok"] is True
    detail = client.get("/api/actions/config.edit/versions/1.0.0", headers=HEADERS).json()
    assert detail["manifest"]["capabilities"]["dry_run"] == "plan"


def test_dr28_45_46_pipeline_yaml_patch_graph_layout(client, env) -> None:
    r = client.post("/api/pipelines", headers=HEADERS, json={"yaml": _pipeline_text(env)})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    data = client.get(f"/api/pipelines/{pid}/yaml", headers=HEADERS).json()
    assert "conf/app.json" in data["text"]
    patch = client.post(f"/api/pipelines/{pid}/patch", headers=HEADERS, json={
        "sha256": data["sha256"],
        "deltas": [{"kind": "update_param", "step_id": "s1", "field": "dest", "value": "work/changed"}],
    })
    assert patch.status_code == 200, patch.text
    assert "work/changed" in patch.json()["text"]
    graph = client.get(f"/api/pipelines/{pid}/graph", headers=HEADERS).json()
    assert len(graph["nodes"]) == 3 and len(graph["edges"]) == 2
    layout = client.put(f"/api/pipelines/{pid}/layout", headers=HEADERS,
                        json={"positions": {"s1": {"x": 1}, "ghost": {"x": 9}}})
    assert "ghost" in layout.json()["removed_orphans"]
    assert client.post(f"/api/pipelines/{pid}/lint", headers=HEADERS).status_code == 200


def test_dr49_plan_confirm_apply_via_rest_and_dr61_sse_tail(client, env) -> None:
    _seed(env)
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": _pipeline_text(env)}).status_code == 200
    created = client.post("/api/runs", headers=HEADERS,
                          json={"pipeline": "local-demo", "inputs": {}, "phase": "plan"})
    task_id = created.json()["task_id"]
    report = None
    for _ in range(100):
        t = client.get(f"/api/tasks/{task_id}", headers=HEADERS).json()
        if t["status"] == "done":
            report = t["result"]
            break
        assert t["status"] != "error", t
    assert report and report["confirm_required"] is True
    bad = client.post("/api/runs", headers=HEADERS,
                      json={"pipeline": "local-demo", "inputs": {}, "phase": "apply"})
    assert bad.status_code == 409, "未先 plan 不得 apply（DR-49）"
    applied = client.post(f"/api/runs/{report['run_id']}/apply", headers=HEADERS,
                          json={"ack_risks": True})
    task = applied.json()["task_id"]
    result = None
    for _ in range(150):
        t = client.get(f"/api/tasks/{task}", headers=HEADERS).json()
        if t["status"] == "done":
            result = t["result"]
            break
        assert t["status"] != "error", t
    assert result and result["status"] == "succeeded", result
    run = client.get(f"/api/runs/{result['id']}", headers=HEADERS).json()
    assert len(run["steps"]) == 3 and run["artifacts"]
    events_path = env.paths.logs / result["id"] / "events.ndjson"
    all_events = EventWriter.tail(events_path, 0)
    assert client.get(f"/api/runs/{result['id']}/report?fmt=md", headers=HEADERS).status_code == 200
    assert EventWriter.tail(events_path, all_events[-1]["seq"]) == [], "Last-Event-ID 续传不重复"


def test_dr50_52_67_68_nodes_credentials_settings_audit(client) -> None:
    node = client.post("/api/nodes", headers=HEADERS,
                       json={"id": "prod", "name": "生产", "host": "10.0.0.8",
                             "username": "root", "os_kind": "ssh-linux"}).json()
    assert node["host"] == "10.0.0.8"
    assert client.put("/api/context/current-node", headers=HEADERS,
                      json={"node_id": "prod"}).json()["current_node"] == "prod"
    cred = client.post("/api/credentials", headers=HEADERS,
                       json={"label": "prod.root", "value": "SUPER-SECRET"}).json()
    assert "SUPER-SECRET" not in json.dumps(cred)
    assert all("SUPER-SECRET" not in json.dumps(c)
               for c in client.get("/api/credentials", headers=HEADERS).json())
    client.put("/api/settings", headers=HEADERS, json={"port": 8765})
    audit = client.get("/api/audit", headers=HEADERS).json()
    assert any(a["action"] == "settings.update" for a in audit)
    assert client.get("/api/maintenance/verify", headers=HEADERS).json()["ok"] is True
    assert client.get("/api/maintenance/diagnostics", headers=HEADERS).status_code == 200
    assert client.get("/api/history/summary", headers=HEADERS).status_code == 200
    assert client.post("/api/ai/draft", headers=HEADERS,
                       json={"description": "解压并上传"}).status_code == 404
