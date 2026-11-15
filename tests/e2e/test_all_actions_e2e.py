"""动作库全部内置动作端到端验证：批量契约 + 逐个真实 plan/apply（离线 FakeTransport）。

Implements: DR-11, DR-17, DR-18, DR-19, DR-30, DR-31, DR-41, DR-75
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from wfp.actions.manifests import BUILTIN_MANIFESTS
from wfp.actions.verify import SAFE_PARAMS
from wfp.api.app import create_app
from wfp.contracts.yamlio import safe_dump

TOKEN = "verify-token"
HEADERS = {"X-WFP-Token": TOKEN, "Host": "127.0.0.1:8765"}


@pytest.fixture
def web(env):
    app = create_app(env.paths, token=TOKEN, port=8765)
    with TestClient(app) as client:
        yield client, app, env
    app.state.wfp["store"].close()


def _node(env) -> None:
    env.runner.nodes.add({"id": "prod", "name": "验证节点", "host": "10.0.0.8",
                          "username": "root", "os_kind": "ssh-linux",
                          "auth_kind": "agent", "default_workdir": "/opt"}, actor="pytest")
    env.runner.nodes.set_current("prod", actor="pytest")


def _seed(ws, env) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "probe.txt").write_text("hello\n", encoding="utf-8")
    (ws / "probe.json").write_text('{"a": 0}\n', encoding="utf-8")
    (ws / "trash.txt").write_text("trash\n", encoding="utf-8")
    (ws / "src_dir").mkdir(exist_ok=True)
    (ws / "src_dir" / "a.txt").write_text("a", encoding="utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("conf/app.json", json.dumps({"a": 0}))
    (ws / "app.zip").write_bytes(buf.getvalue())
    remote = env.transport.root / "opt"
    remote.mkdir(parents=True, exist_ok=True)
    (remote / "probe.txt").write_text("hello\n", encoding="utf-8")
    (remote / "app.json").write_text('{"a": 0}\n', encoding="utf-8")


def test_verify_all_endpoint_covers_every_builtin_action(web) -> None:
    client, _, _ = web
    r = client.post("/api/actions/verify-all", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(SAFE_PARAMS) == set(BUILTIN_MANIFESTS)
    assert body["total"] == len(BUILTIN_MANIFESTS) == 16
    failed = [i for i in body["items"] if not i["ok"]]
    assert not failed, failed
    assert body["passed"] == body["total"]


@pytest.mark.parametrize("aid", sorted(BUILTIN_MANIFESTS))
def test_each_builtin_action_plans_and_applies(env, aid: str) -> None:
    ws = env.tmp / f"ws-{aid.replace('.', '-')}"
    _seed(ws, env)
    manifest = BUILTIN_MANIFESTS[aid]
    doc = {
        "apiVersion": "wfp/v1",
        "meta": {"id": f"e2e-{aid.replace('.', '-')}", "name": f"E2E {aid}",
                 "version": "1.0.0", "workdir": str(ws)},
        "steps": [{"id": "s1", "uses": f"{aid}@1.0.0", "with": SAFE_PARAMS[aid]}],
    }
    if manifest["runtime"] == "remote":
        _node(env)
        doc["defaults"] = {"node": "current"}
    ref = env.tmp / f"e2e-{aid.replace('.', '-')}.yaml"
    ref.write_text(safe_dump(doc), encoding="utf-8")

    report = env.runner.plan(str(ref), {})
    assert report["items"] and report["items"][0]["action_ref"] == f"{aid}@1.0.0"
    result = env.runner.apply(str(ref), {}, yes=True)
    assert result["status"] == "succeeded", (aid, result)
    assert result["steps"] and result["steps"][0]["status"] in ("succeeded", "skipped_idempotent"), (aid, result["steps"])
