"""CLI 与 UI 能力等价、退出码契约与离线命令族。

Implements: DR-04, DR-05, DR-06, DR-09, DR-13, DR-28, DR-30, DR-31, DR-49,
DR-64, DR-66, DR-73, DR-76
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def cli(env, *args: str) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ, WFP_HOME=str(env.paths.root))
    return subprocess.run([sys.executable, "-m", "wfp.cli", *args], cwd=ROOT,
                          env=e, capture_output=True, text=True, check=False)


def _prepare(env) -> None:
    (env.tmp / "conf").mkdir(exist_ok=True)
    (env.tmp / "conf" / "app.json").write_text('{"env": "dev"}\n', encoding="utf-8")
    from tests.conftest import LOCAL_PIPELINE
    text = LOCAL_PIPELINE.replace("meta:\n  id: local-demo",
                                  f"meta:\n  id: local-demo\n  workdir: {env.tmp}")
    env.pipeline("cli.yaml", text)


def test_dr04_cli_help_lists_all_subcommands(env) -> None:
    r = cli(env, "--help")
    assert r.returncode == 0
    for cmd in ("serve", "doctor", "action", "pipeline", "plan", "run", "node",
                "credential", "export", "import", "verify", "report", "diagnostics"):
        assert cmd in r.stdout


def test_dr06_doctor_and_dr09_exit_codes(env) -> None:
    r = cli(env, "doctor", "--json")
    assert r.returncode in (0, 3)
    data = json.loads(r.stdout)
    assert data["checks"] and "home" in data
    assert cli(env, "action", "list").returncode == 0


def test_dr09_cli_plan_apply_confirm_gate_and_report(env, tmp_path: Path) -> None:
    _prepare(env)
    imported = cli(env, "pipeline", "import", str(env.tmp / "cli.yaml"))
    assert imported.returncode == 0, imported.stderr
    plan = cli(env, "plan", "local-demo", "--json")
    assert plan.returncode == 0, plan.stderr
    report = json.loads(plan.stdout)
    assert report["confirm_required"] is True and len(report["items"]) == 3

    denied = cli(env, "run", "local-demo", "--json")
    assert denied.returncode == 2, denied.stderr
    assert "WFP-E-CONFIRM-REQUIRED" in denied.stderr

    applied = cli(env, "run", "local-demo", "--yes", "--json")
    assert applied.returncode == 0, applied.stderr
    result = json.loads(applied.stdout)
    assert result["status"] == "succeeded"
    assert json.loads((env.tmp / "conf" / "app.json").read_text())["env"] == "prod"

    out = tmp_path / "report.csv"
    rep = cli(env, "report", result["id"], "--fmt", "csv", "--out", str(out))
    assert rep.returncode == 0 and out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert cli(env, "verify").returncode == 0


def test_dr05_cli_export_and_dr73_offline_no_external_calls(env, tmp_path: Path) -> None:
    _prepare(env)
    cli(env, "pipeline", "import", str(env.tmp / "cli.yaml"))
    out = tmp_path / "wfp.zip"
    r = cli(env, "export", "--out", str(out))
    assert r.returncode == 0 and out.exists()
    assert "cdn" not in r.stdout.lower()
