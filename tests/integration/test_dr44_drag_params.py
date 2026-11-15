"""DR-44/48：视图拖拽草稿创建工作项、点击节点编辑任意/默认参数、按落点插入。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import LOCAL_PIPELINE
from wfp.api.app import create_app
from wfp.contracts.yamlio import safe_load

TOKEN = "drag-params-token"
HEADERS = {"X-WFP-Token": TOKEN, "Host": "127.0.0.1:8765"}


@pytest.fixture
def web(env):
    app = create_app(env.paths, token=TOKEN, port=8765)
    with TestClient(app) as client:
        yield client, app, env
    app.state.wfp["store"].close()


def test_dr44_draft_creates_pipeline_from_dragged_steps(web) -> None:
    client, _, _ = web
    steps = [
        {"id": "s1", "uses": "assert.file_exists@1.0.0", "with": {"path": "README.md"}},
        {"id": "s2", "uses": "config.edit@1.0.0",
         "with": {"format": "json", "custom_flag": True}},
    ]
    r = client.post("/api/pipelines/draft", headers=HEADERS,
                    json={"id": "drag-demo", "name": "拖拽新建", "steps": steps})
    assert r.status_code == 200, r.text
    assert r.json()["step_count"] == 2
    graph = client.get("/api/pipelines/drag-demo/graph", headers=HEADERS).json()
    assert [n["id"] for n in graph["nodes"]] == ["s1", "s2"]
    assert graph["nodes"][1]["with"]["custom_flag"] is True       # 任意参数
    yaml_text = client.get("/api/pipelines/drag-demo/yaml", headers=HEADERS).json()["text"]
    assert "custom_flag: true" in yaml_text and "id: drag-demo" in yaml_text
    assert client.post("/api/pipelines/draft", headers=HEADERS,
                       json={"id": "Bad_ID", "steps": steps}).status_code >= 400
    assert client.post("/api/pipelines/draft", headers=HEADERS,
                       json={"id": "empty-demo", "steps": []}).status_code >= 400


def test_dr48_set_with_and_indexed_insert(web) -> None:
    client, _, _ = web
    created = client.post("/api/pipelines", headers=HEADERS, json={"yaml": LOCAL_PIPELINE})
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    sha = client.get(f"/api/pipelines/{pid}/yaml", headers=HEADERS).json()["sha256"]
    first = client.get(f"/api/pipelines/{pid}/graph", headers=HEADERS).json()["nodes"][0]["id"]

    # 点击节点编辑参数：可用参数覆盖 + 任意参数 + 默认值修改，一次只触及 steps[i].with
    params = {"path": "conf/app.json", "format": "yaml", "flag": False, "extra": [1, 2]}
    r = client.post(f"/api/pipelines/{pid}/patch", headers=HEADERS,
                    json={"sha256": sha, "deltas": [{"kind": "set_with", "step_id": first, "value": params}]})
    assert r.status_code == 200, r.text
    graph = client.get(f"/api/pipelines/{pid}/graph", headers=HEADERS).json()
    assert graph["nodes"][0]["with"] == params
    text = client.get(f"/api/pipelines/{pid}/yaml", headers=HEADERS).json()["text"]
    assert "inputs:" in text and "default: prod" in text   # 非目标区域保留（DR-45）
    assert "flag: false" in text and "format: yaml" in text

    # 拖拽落点：插入到指定序号而非永远追加到末尾
    sha = r.json()["sha256"]
    r2 = client.post(f"/api/pipelines/{pid}/patch", headers=HEADERS,
                     json={"sha256": sha, "deltas": [{"kind": "insert_step", "index": 0,
                                                      "new_step": {"id": "s0", "uses": "assert.file_exists@1.0.0",
                                                                   "with": {}}}]})
    assert r2.status_code == 200, r2.text
    nodes = client.get(f"/api/pipelines/{pid}/graph", headers=HEADERS).json()["nodes"]
    assert nodes[0]["id"] == "s0"


def test_dr44_bundle_exposes_drag_and_params_ui(web) -> None:
    client, _, _ = web
    bundle = client.get("/app.js").text
    for marker in ("拖拽新建", "保存工作项", "可用参数列表", "任意参数", "编辑动作参数",
                   "编辑参数", "单击选中 · 双击节点编辑参数", "单击选中 / 双击编辑"):
        assert marker in bundle, marker


def test_bug001_patch_on_top_level_sequence_produces_parseable_yaml(web) -> None:
    """BUG-001 回归：steps 序列 dash 顶格时，图编辑不得产出键与 dash 同列的非法 YAML。"""
    client, _, _ = web
    yaml_text = ("apiVersion: wfp/v1\nmeta:\n  id: indent-demo\n  name: 缩进回归\n"
                 "  version: 1.0.0\nsteps:\n- id: s1\n  uses: archive.pack@1.0.0\n  with: {}\n")
    created = client.post("/api/pipelines", headers=HEADERS, json={"yaml": yaml_text})
    assert created.status_code == 200, created.text
    sha = client.get("/api/pipelines/indent-demo/yaml", headers=HEADERS).json()["sha256"]
    r = client.post("/api/pipelines/indent-demo/patch", headers=HEADERS,
                    json={"sha256": sha, "deltas": [{"kind": "insert_step", "index": 0,
                                                     "new_step": {"id": "s0", "uses": "assert.file_exists@1.0.0",
                                                                  "with": {}}}]})
    assert r.status_code == 200, r.text
    assert safe_load(r.json()["text"])["steps"][0]["id"] == "s0"

    # draft 新产物使用标准缩进，且同样可被 round-trip 编辑
    r2 = client.post("/api/pipelines/draft", headers=HEADERS,
                     json={"id": "indent-draft", "steps": [{"id": "s1", "uses": "archive.pack@1.0.0", "with": {}}]})
    assert r2.status_code == 200, r2.text
    text = client.get("/api/pipelines/indent-draft/yaml", headers=HEADERS).json()["text"]
    assert "\n  - id: s1\n" in text
    assert safe_load(text)["meta"]["id"] == "indent-draft"


def test_editor_single_click_selects_and_double_click_edits() -> None:
    root = Path(__file__).resolve().parents[2]
    editor = (root / "frontend/src/pages/Editor.tsx").read_text(encoding="utf-8")
    canvas = (root / "frontend/src/GraphCanvas.tsx").read_text(encoding="utf-8")
    assert "onNodeClick={(_, node) => onSelect(node.id)}" in canvas        # 单击仅选中
    assert "onNodeDoubleClick={(_, node) => onEdit(node.id)}" in canvas    # 双击才编辑
    assert "zoomOnDoubleClick={false}" in canvas                            # 双击不误触缩放
    assert "onEdit={(sid: string) => selectStep(sid, true)}" in editor
    assert "if (openEditor) setParamsOpen(true)" in editor
    assert "编辑参数" in canvas
