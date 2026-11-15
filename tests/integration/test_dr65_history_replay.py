"""历史/报表/审计/保留/配额/AI 四道闸与诊断包。

Implements: DR-65, DR-66, DR-67, DR-68, DR-69, DR-77, DR-78, DR-79,
DR-80, DR-81, DR-82, DR-83
"""
from __future__ import annotations

import json

from tests.conftest import LOCAL_PIPELINE
from wfp.ai import AIDraftService
from wfp.engine.journal import EventWriter
from wfp.reports import run_report, summary
from wfp.security.redactor import Redactor


def _seed(env) -> None:
    (env.tmp / "conf").mkdir(exist_ok=True)
    (env.tmp / "conf" / "app.json").write_text('{"env": "dev"}\n', encoding="utf-8")


def _run(env):
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    return env.runner.apply(ref, {}, yes=True)


def test_dr65_history_and_dr66_reports_md_html_csv(env) -> None:
    _seed(env)
    result = _run(env)
    md, media, name = run_report(env.paths, env.runner.runs, result["id"], fmt="md")
    assert "WFP 运行报表" in md and media.startswith("text/markdown") and name.endswith(".md")
    html, _, _ = run_report(env.paths, env.runner.runs, result["id"], fmt="html")
    assert "<table>" in html and result["id"] in html
    csv, _, _ = run_report(env.paths, env.runner.runs, result["id"], fmt="csv")
    assert csv.startswith("\ufeff") and "run_id" in csv
    s = summary(env.paths, env.runner.runs)
    assert s["runs"] >= 1 and s["per_run"][0]["steps"] == 3


def test_dr68_audit_covers_confirm_and_run_finish(env) -> None:
    _seed(env)
    _run(env)
    rows = env.store.read(lambda c: c.execute("SELECT action, detail_json FROM audit").fetchall())
    actions = {r["action"] for r in rows}
    assert {"run.apply.confirm", "run.finished", "action.create"} <= actions
    assert any("plan_sha" in (r["detail_json"] or "") for r in rows)


def test_dr69_retention_keeps_recent_runs(env) -> None:
    _seed(env)
    result = _run(env)
    out = env.runner.runs.cleanup(days=0)
    assert out["count"] == 0, "最近 run 的证据必须保留"
    assert (env.paths.logs / result["id"] / "events.ndjson").exists()
    rows = env.store.read(lambda c: c.execute(
        "SELECT action FROM audit WHERE action='maintenance.cleanup'").fetchall())
    assert rows


def test_dr77_log_quota_truncates_without_crashing(env) -> None:
    events = EventWriter(env.paths, "r-quota", Redactor(), quota=256)
    for i in range(20):
        events.emit("step.stdout", {"text": "x" * 200, "i": i})
    events.close()
    lines = [json.loads(x) for x in (env.paths.logs / "r-quota" / "events.ndjson").read_text().splitlines()]
    assert any(e["type"] == "log.quota_exceeded" for e in lines)
    assert len(lines) < 20


def test_dr80_81_82_83_ai_draft_four_gates(env) -> None:
    service = AIDraftService(env.store, env.runner._action_exists)
    assert service.enabled is False
    service.set_enabled(True)
    draft = service.draft("解压压缩包，修改 json 配置，然后上传到远程节点")
    assert draft["status"] in ("draft", "draft_invalid")
    assert draft["gates"] == ["schema", "lint", "human_accept", "plan_confirm"]
    assert "config.edit@1.0.0" in draft["yaml"]
    assert "10.0.0.8" not in service.redact("连接 10.0.0.8")
    accepted = service.accept(draft["id"])
    assert accepted["status"] == "active"
    rows = env.store.read(lambda c: c.execute("SELECT action FROM audit WHERE actor='ai'").fetchall())
    assert {r["action"] for r in rows} >= {"ai.draft", "ai.accept"}
    service.set_enabled(False)
    assert service.enabled is False
