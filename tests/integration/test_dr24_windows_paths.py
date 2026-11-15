"""DR-24：Windows 跨盘符路径的错误分类、workspace 根与越界开关的视图配置。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import LOCAL_PIPELINE
from wfp.api.app import create_app
from wfp.engine import expr
from wfp.engine.expr import PathError, resolve_local_path

TOKEN = "path-token"
HEADERS = {"X-WFP-Token": TOKEN, "Host": "127.0.0.1:8765"}
WINDOWS_DIR = "C:/Users/Administrator/Downloads/my_dev"


@pytest.fixture
def web(env):
    app = create_app(env.paths, token=TOKEN, port=8765)
    with TestClient(app) as client:
        yield client, app, env
    app.state.wfp["store"].close()


def test_bug004_cross_drive_path_reports_outside_not_unresolvable(monkeypatch) -> None:
    # Windows 上 os.path.commonpath 遇跨盘符会抛 ValueError，旧实现误报"路径无法解析"。
    def boom(_paths):
        raise ValueError("Paths are on different drives")

    monkeypatch.setattr(expr.os.path, "commonpath", boom)
    with pytest.raises(PathError) as exc:
        resolve_local_path("/etc/hosts", Path("/tmp/wfp-workspace"))
    assert "路径越界" in str(exc.value)
    assert "路径无法解析" not in str(exc.value)
    assert "allow_outside_workspace" in str(exc.value)
    # 显式允许后不再拦截
    assert resolve_local_path("/etc/hosts", Path("/tmp/wfp-workspace"), allow_outside=True)


def test_dr24_meta_endpoint_sets_workspace_and_outside_flag(web) -> None:
    client, _, _ = web
    assert client.post("/api/pipelines", headers=HEADERS, json={"yaml": LOCAL_PIPELINE}).status_code == 200
    r = client.put("/api/pipelines/local-demo/meta", headers=HEADERS,
                   json={"workdir": WINDOWS_DIR, "allow_outside_workspace": True})
    assert r.status_code == 200, r.text
    text = client.get("/api/pipelines/local-demo/yaml", headers=HEADERS).json()["text"]
    assert WINDOWS_DIR in text and "allow_outside_workspace: true" in text
    assert "inputs:" in text and "default: prod" in text    # 非 meta 区域保留
    meta = client.get("/api/pipelines/local-demo", headers=HEADERS).json()["meta"]
    assert meta["workdir"] == WINDOWS_DIR and meta["allow_outside_workspace"] is True

    r2 = client.put("/api/pipelines/local-demo/meta", headers=HEADERS,
                    json={"workdir": "", "allow_outside_workspace": False})
    assert r2.status_code == 200, r2.text
    text2 = client.get("/api/pipelines/local-demo/yaml", headers=HEADERS).json()["text"]
    assert "workdir:" not in text2 and "allow_outside_workspace" not in text2


def test_dr24_draft_accepts_workspace_and_outside_flag(web) -> None:
    client, _, _ = web
    r = client.post("/api/pipelines/draft", headers=HEADERS,
                    json={"id": "win-path-demo", "workdir": WINDOWS_DIR,
                          "allow_outside_workspace": True,
                          "steps": [{"id": "s1", "uses": "archive.pack@1.0.0", "with": {}}]})
    assert r.status_code == 200, r.text
    text = client.get("/api/pipelines/win-path-demo/yaml", headers=HEADERS).json()["text"]
    assert WINDOWS_DIR in text and "allow_outside_workspace: true" in text


def test_dr24_bundle_exposes_path_settings(web) -> None:
    client, _, _ = web
    bundle = client.get("/app.js").text
    for marker in ("路径设置", "workspace 根", "允许访问 workspace 之外"):
        assert marker in bundle, marker
