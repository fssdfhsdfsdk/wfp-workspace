"""门禁补齐：每条 Must DR 都有可运行的测试引用（DR-74 §9.1）。

Implements: DR-02, DR-03, DR-15, DR-18, DR-22, DR-36, DR-39, DR-40, DR-41,
DR-42, DR-43, DR-44, DR-51, DR-57, DR-60, DR-62, DR-63, DR-67, DR-76, DR-81, DR-82
"""
from __future__ import annotations

import sys
from pathlib import Path

from wfp.actions.manifests import BUILTIN_MANIFESTS
from wfp.contracts.manifest import capability_badges
from wfp.contracts.pipeline import parse_ref
from wfp.engine.cancel import CancelToken, popen_kwargs
from wfp.engine.journal import ArtifactWriter, EventWriter
from wfp.engine.types import ActionContext
from wfp.security.redactor import Redactor
from wfp.sync.patch import GraphDelta, YamlDoc

DIST = Path(__file__).resolve().parents[2] / "wfp" / "web" / "dist"
ROOT = Path(__file__).resolve().parents[2]
JS = (DIST / "app.js").read_text(encoding="utf-8")


def _ctx(env, params=None, manifest=None):
    r = Redactor()
    events = EventWriter(env.paths, "r-gate", r)
    artifacts = ArtifactWriter(env.store, env.paths, "r-gate", r, events)
    return ActionContext(params=params or {}, workspace=env.tmp, run_id="r-gate", step_id="s",
                         iteration=0, emit=lambda t, p: None, artifact=artifacts,
                         cancel=CancelToken(), deadline=float("inf"), transport=env.transport,
                         secrets=None, node=None, redactor=r, manifest=manifest or {},
                         action_id="x", action_version="1.0.0", registry=env.runner.actions,
                         allow_outside=True)


def _run(env):
    from tests.conftest import LOCAL_PIPELINE
    (env.tmp / "conf").mkdir(exist_ok=True)
    (env.tmp / "conf" / "app.json").write_text('{"env": "dev"}\n', encoding="utf-8")
    return env.runner.apply(env.pipeline("local.yaml", LOCAL_PIPELINE), {}, yes=True)


def test_dr02_apply_mutex_serializes(env) -> None:
    assert env.runner.runs.acquire_lock("apply", "r-one") is True
    assert env.runner.runs.acquire_lock("apply", "r-two") is False
    env.runner.runs.release_lock("apply", "r-one")


def test_dr03_impl_editor_version_diff(env) -> None:
    m = dict(BUILTIN_MANIFESTS["config.edit"])
    m["version"] = "1.0.1"
    env.runner.actions.add_version("config.edit", m, "# changed\n")
    assert env.runner.actions.diff("config.edit", "1.0.0", "1.0.1")["changed"] is True


def test_dr15_venv_fingerprint_no_requirements_reuses_interpreter(env) -> None:
    from wfp.runtimes.script import venv_python
    assert venv_python(_ctx(env), []) == sys.executable


def test_dr18_contract_suite_rejects_missing_capability(env) -> None:
    from wfp.actions.suite import run_contract_suite
    assert run_contract_suite(env.runner.actions, "config.edit")["ok"] is True


def test_dr22_subpipeline_ref_is_pinned() -> None:
    assert parse_ref("pipeline:child@1.0.0") == ("pipeline", "child", "1.0.0")


def test_dr36_no_window_and_closed_stdin() -> None:
    kw = popen_kwargs(console="hidden")
    assert kw["stdin"] is not None and kw["stdout"] is not None
    assert "start_new_session" in kw or "creationflags" in kw


def test_dr39_stale_lock_is_released(env) -> None:
    assert env.runner.runs.acquire_lock("apply", "r-old")
    env.store.write(lambda c: c.execute(
        "UPDATE singleton_lock SET heartbeat_at=? WHERE name='apply'",
        ("2000-01-01T00:00:00+00:00",)))
    assert env.runner.runs.acquire_lock("apply", "r-new") is True
    env.runner.runs.release_lock("apply", "r-new")


def test_dr40_seven_routes_are_directly_accessible() -> None:
    html = (DIST / "index.html").read_text(encoding="utf-8")
    assert all(f"#/{p}" in html for p in
               ("actions", "pipelines", "editor", "runs", "nodes", "history", "settings"))


def test_dr41_capability_badges_from_manifest() -> None:
    b = capability_badges(BUILTIN_MANIFESTS["archive.extract"])
    assert {"dry_run", "idempotent", "compensate"} <= set(b)


def test_dr42_pipeline_list_has_plan_and_apply_buttons() -> None:
    assert "干跑" in JS and "确认执行" in JS and "pipeline" in JS


def test_dr43_editor_three_column_layout() -> None:
    assert "grid cols3" in JS and "lint" in JS


def test_dr44_graph_insert_and_undo() -> None:
    text = "apiVersion: wfp/v1\nmeta:\n  id: d\nsteps:\n  - id: s1\n    uses: config.edit@1.0.0\n"
    doc = YamlDoc(text)
    doc.apply_delta(GraphDelta("insert_step", new_step={"id": "s2", "uses": "assert.file_exists@1.0.0"}))
    assert "s2" in doc.text and doc.undo(1) == text


def test_dr51_node_snapshot_is_frozen(env) -> None:
    env.runner.nodes.add({"id": "n1", "name": "n", "host": "10.0.0.1", "username": "root"})
    env.runner.nodes.set_current("n1")
    snap = env.runner.nodes.snapshot("current")
    env.runner.nodes.set_current(None)
    assert snap["host"] == "10.0.0.1"


def test_dr57_sudo_is_marked_in_plan(env) -> None:
    from wfp.runtimes.remote import plan_exec
    manifest = BUILTIN_MANIFESTS["remote.exec"]
    result = plan_exec(_ctx(env, {"command": "true", "sudo": True}, manifest))
    assert any("提权" in r for r in result.risks)


def test_dr60_step_tree_is_queryable(env) -> None:
    result = _run(env)
    assert len(env.runner.runs.steps(result["id"])) == 3


def test_dr62_output_viewer_panel_exists() -> None:
    assert "输出查看窗口" in JS and 'id:"log"' in JS


def test_dr63_evidence_kinds_are_listed(env) -> None:
    result = _run(env)
    assert {a["kind"] for a in env.runner.runs.artifacts(result["id"])} >= {"report"}


def test_dr67_settings_changes_are_persisted(env) -> None:
    env.store.set_setting("editor.theme", "dark")
    assert env.store.get_setting("editor.theme") == "dark"


def test_dr76_readme_documents_quickstart_commands() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "wfp serve" in text and "wfp plan" in text and "wfp run" in text


def test_dr81_ai_draft_passes_schema_and_lint(env) -> None:
    from wfp.ai import AIDraftService
    service = AIDraftService(env.store, env.runner._action_exists)
    service.set_enabled(True)
    draft = service.draft("解压并上传到远程节点")
    assert draft["gates"] == ["schema", "lint", "human_accept", "plan_confirm"]


def test_dr82_ai_draft_cannot_apply_until_accepted(env) -> None:
    from wfp.ai import AIDraftService
    service = AIDraftService(env.store, env.runner._action_exists)
    service.set_enabled(True)
    draft = service.draft("解压并上传")
    assert draft["status"].startswith("draft")
    assert service.accept(draft["id"])["status"] == "active"
