"""BUG-007/008 回归：archive.pack 幂等误判、dest 目录语义与压缩包层级。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wfp.api.app import create_app
from wfp.engine.journal import EventWriter

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


def _pipeline(client, pid: str, src: Path, dest: Path, extra: str = "") -> None:
    yaml_text = f"""apiVersion: wfp/v1
meta:
  id: {pid}
  name: {pid}
  version: 1.0.0
  allow_outside_workspace: true
steps:
  - id: s1
    uses: archive.pack@1.0.0
    with:
      src: {src}
      dest: {dest}
{extra}"""
    r = client.post("/api/pipelines", headers=HEADERS, json={"yaml": yaml_text})
    assert r.status_code == 200, r.text


def _direct(client, pid: str) -> dict:
    t = client.post("/api/runs/direct", headers=HEADERS,
                    json={"pipeline": pid, "inputs": {}, "ack_risks": True})
    assert t.status_code == 200, t.text
    return _poll(client, t.json()["task_id"])


def test_pack_never_skips_and_writes_into_dest_directory(web, env) -> None:
    client, _, _ = web
    src = env.tmp / "my_dev"
    src.mkdir()
    (src / "hello.txt").write_text("hello", encoding="utf-8")
    dest_dir = env.tmp / "workspace"
    dest_dir.mkdir()               # 用户场景：dest 是已存在的目录
    out = dest_dir / "my_dev.zip"  # 压缩包应落在目录内，而不是上一层

    _pipeline(client, "pack-no-skip", src, dest_dir)

    planned = client.post("/api/runs", headers=HEADERS,
                          json={"pipeline": "pack-no-skip", "inputs": {}, "phase": "plan"})
    report = _poll(client, planned.json()["task_id"])
    assert str(out) in report["items"][0]["targets"], report["items"][0]
    assert not out.exists()        # 干跑零副作用

    r = _direct(client, "pack-no-skip")
    assert r["status"] == "succeeded", r
    assert [s["status"] for s in r["steps"]] == ["succeeded"], r["steps"]
    assert out.is_file()
    assert "my_dev/hello.txt" in zipfile.ZipFile(out).namelist()   # 保留源目录名一层

    # 日志明确打印输出路径，且不再出现第二条空 payload 的 step.started
    events = EventWriter.tail(env.paths.logs / r["id"] / "events.ndjson")
    started = [e for e in events if e["type"] == "step.started"]
    assert len(started) == 1 and started[0]["payload"]["info"]["output"] == str(out)
    assert started[0]["payload"]["info"]["archive_root"] == "my_dev/"
    assert any(e["type"] == "step.attempt" for e in events)

    # 第二次仍真正打包（DR-32 跳过清单不含 archive.pack）
    r2 = _direct(client, "pack-no-skip")
    assert r2["status"] == "succeeded" and out.is_file()


def test_pack_dest_suffix_and_include_root_switch(web, env) -> None:
    client, _, _ = web
    src = env.tmp / "src_dir"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")

    # 显式 .zip 文件名：直接输出到该文件
    explicit = env.tmp / "explicit.zip"
    _pipeline(client, "pack-explicit", src, explicit)
    assert _direct(client, "pack-explicit")["status"] == "succeeded"
    assert explicit.is_file()

    # 不存在的基名：补 .zip（兼容旧行为）
    base = env.tmp / "basename"
    _pipeline(client, "pack-base", src, base)
    assert _direct(client, "pack-base")["status"] == "succeeded"
    assert Path(str(base) + ".zip").is_file()

    # include_root: false → 包内不含顶层目录名
    flat_dir = env.tmp / "flat"
    flat_dir.mkdir()
    out = flat_dir / "src_dir.zip"
    _pipeline(client, "pack-flat", src, flat_dir, "      include_root: false\n")
    assert _direct(client, "pack-flat")["status"] == "succeeded"
    names = zipfile.ZipFile(out).namelist()
    assert "a.txt" in names and not any(n.startswith("src_dir/") for n in names)


def test_builtin_contract_sync_upgrades_existing_manifest(web) -> None:
    client, app, _ = web
    import json

    from wfp.actions.manifests import BUILTIN_MANIFESTS, seed_builtin_actions

    reg = app.state.wfp["actions"]
    old = json.loads(json.dumps(BUILTIN_MANIFESTS["archive.pack"]))
    old["params"]["properties"].pop("include_root", None)
    reg.store.write(lambda c: c.execute(
        "UPDATE action_version SET manifest_json=? WHERE action_id=? AND version=?",
        (json.dumps(old, ensure_ascii=False), "archive.pack", "1.0.0")))
    assert "include_root" not in reg.get_version("archive.pack", "1.0.0")["manifest"]["params"]["properties"]

    seed_builtin_actions(reg)      # 模拟老库下次启动
    m = reg.get_version("archive.pack", "1.0.0")["manifest"]
    assert "include_root" in m["params"]["properties"]
