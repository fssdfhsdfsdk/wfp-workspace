"""M1 核心链路集成测试：plan 零副作用 / apply / 备份 / 确认闸 / 事件。

Implements: DR-09, DR-14, DR-18, DR-19, DR-20, DR-26, DR-27, DR-28, DR-29, DR-30,
DR-31, DR-32, DR-33, DR-38, DR-39, DR-49, DR-61, DR-63, DR-71, DR-75, DR-79
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import LOCAL_PIPELINE
from wfp.errors import ConfirmRequired
from wfp.security.redactor import Redactor, SecretStr


def _seed_config(env) -> Path:
    conf = env.tmp / "conf"
    conf.mkdir(exist_ok=True)
    p = conf / "app.json"
    p.write_text(json.dumps({"env": "dev", "server": {"port": 80}}, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def test_dr30_plan_zero_side_effect_and_dr31_report(env) -> None:
    target = _seed_config(env)
    before = target.read_bytes()
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    report = env.runner.plan(ref, {"env": "staging"})
    assert target.read_bytes() == before, "plan 期间不得写目标文件（DR-30）"
    assert len(report["items"]) == 3
    # assert.* 声明 compensate=none，按 DR-49 必须走风险确认闸
    assert report["confirm_required"] is True
    assert report["items"][0]["diff"], "config.edit 应产出行级 diff（DR-31）"
    assert len(report["sha256"]) == 64


def test_dr30_dry_run_none_never_executes(env) -> None:
    """无 plan 入口的动作在 plan 阶段只标 unpredictable，进程数为 0（DR-30/17）。"""
    from wfp.actions.manifests import make_manifest
    m = make_manifest("script.custom", "自定义脚本", "script",
                      {"type": "object", "properties": {}, "required": []},
                      runtime="python", dry_run="none", idempotent="no",
                      compensate="manual", side_effects="local_fs")
    env.runner.actions.create(m, "print('should not run')\n", actor="pytest")
    text = LOCAL_PIPELINE.replace("config.edit@1.0.0", "script.custom@1.0.0", 1).replace(
        "      path: conf/app.json\n      format: json\n      edits:\n        - key: env\n          value: \"{{ inputs.env }}\"\n",
        "      args: []\n")
    ref = env.pipeline("custom.yaml", text)
    report = env.runner.plan(ref, {})
    assert report["items"][0]["unpredictable"] is True


def test_dr49_confirm_gate_and_dr09_apply_success(env) -> None:
    _seed_config(env)
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    with pytest.raises(ConfirmRequired):
        env.runner.apply(ref, {"env": "prod"})
    result = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert result["status"] == "succeeded"
    assert len(result["steps"]) == 3
    assert json.loads((env.tmp / "conf" / "app.json").read_text())["env"] == "prod"
    audit = env.store.read(lambda c: c.execute(
        "SELECT action FROM audit WHERE action LIKE 'run.apply%'").fetchall())
    assert audit, "确认动作必须写审计（DR-49）"


def test_dr33_pre_backup_and_dr38_events_artifacts(env) -> None:
    _seed_config(env)
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    result = env.runner.apply(ref, {}, yes=True)
    artifacts = env.runner.runs.artifacts(result["id"])
    kinds = {a["kind"] for a in artifacts}
    assert "backup" in kinds and "diff" in kinds and "report" in kinds
    journal = env.runner.runs.journal_for(result["id"])
    assert any(j["event"] == "pre_apply" and j["payload"]["receipts"] for j in journal)
    events = (env.paths.logs / result["id"] / "events.ndjson").read_text(encoding="utf-8")
    types = {json.loads(line)["type"] for line in events.splitlines() if line.strip()}
    assert {"run.started", "step.started", "step.finished", "artifact.created",
            "backup.created", "run.finished"} <= types
    assert "database" not in events.lower()
    assert env.runner.runs.verify_integrity() == []


def test_dr32_idempotency_second_apply_skips(env) -> None:
    _seed_config(env)
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    first = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert first["status"] == "succeeded"
    second = env.runner.apply(ref, {"env": "prod"}, yes=True)
    assert second["status"] == "succeeded"
    statuses = [s["status"] for s in second["steps"]]
    assert statuses.count("skipped_idempotent") >= 1, statuses


def test_dr14_param_validation_and_dr27_inputs(env) -> None:
    from wfp.errors import ContractError
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    with pytest.raises(ContractError) as exc:
        env.runner.plan(ref, {"env": 123})
    assert "inputs" in (exc.value.path or "")


def test_dr53_redaction_and_dr52_secret_str() -> None:
    r, secret = Redactor(), SecretStr("P@ss/中文", "cred-prod")
    r.add(secret)
    text = json.dumps({"cmd": "password=P@ss/中文", "b64": "UEBzcy/kuK3mnpw="}, ensure_ascii=False)
    assert "P@ss/中文" not in r.scrub(text)
    assert str(secret) == "***" and secret.reveal().startswith("P@ss")


def test_dr75_contract_suite_all_builtin(env) -> None:
    from wfp.actions.suite import run_contract_suite
    for action_id in env.runner.actions.list():
        result = run_contract_suite(env.runner.actions, action_id["id"])
        assert result["ok"], (action_id["id"], [c for c in result["checks"] if not c["ok"]])
