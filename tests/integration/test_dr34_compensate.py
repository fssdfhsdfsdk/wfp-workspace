"""补偿、崩溃续跑、重试、分支循环、进程树取消与运行控制。

Implements: DR-21, DR-22, DR-26, DR-32, DR-34, DR-35, DR-36, DR-37, DR-39,
DR-64, DR-71, DR-75, DR-77
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from wfp.actions.manifests import make_manifest
from wfp.engine.cancel import ProcessHandle, popen_kwargs


def _seed(env) -> None:
    (env.tmp / "conf").mkdir(exist_ok=True)
    (env.tmp / "conf" / "app.json").write_text('{"env": "dev"}\n', encoding="utf-8")


COMPENSATE_PIPELINE = """apiVersion: wfp/v1
meta: { id: compensate-demo, name: 补偿演示, version: 1.0.0 }
steps:
  - id: s1
    uses: config.edit@1.0.0
    with:
      path: conf/app.json
      format: json
      edits:
        - key: env
          value: prod
  - id: s2
    uses: assert.file_exists@1.0.0
    on_error: compensate
    with: { path: missing.json }
"""


def test_dr34_compensate_restores_hash_and_lists_manual_items(env) -> None:
    _seed(env)
    before = (env.tmp / "conf" / "app.json").read_bytes()
    ref = env.pipeline("comp.yaml", COMPENSATE_PIPELINE)
    result = env.runner.apply(ref, {}, yes=True)
    assert result["status"] in ("compensated", "needs_attention"), result["error_code"]
    assert (env.tmp / "conf" / "app.json").read_bytes() == before, "补偿必须还原到 apply 前字节"
    events = (env.paths.logs / result["id"] / "events.ndjson").read_text(encoding="utf-8")
    assert "backup.restored" in events
    assert "attention.required" in events, "不可逆步骤必须进入人工清单（DR-34）"


def test_dr37_resume_replays_from_last_consistent_point(env) -> None:
    _seed(env)
    text = COMPENSATE_PIPELINE.replace("on_error: compensate", "on_error: fail")
    text = text.replace("meta: { id: compensate-demo, name: 补偿演示, version: 1.0.0 }",
                        f"meta:\n  id: compensate-demo\n  name: 补偿演示\n  version: 1.0.0\n  workdir: {env.tmp}")
    env.runner.pipelines.create(text)
    first = env.runner.apply("compensate-demo", {}, yes=True)
    assert first["status"] == "failed"
    assert any(s["status"] == "succeeded" for s in first["steps"])
    (env.tmp / "missing.json").write_text("now exists", encoding="utf-8")
    resumed = env.runner.resume(first["id"])
    assert resumed["id"] == first["id"]
    assert resumed["status"] == "succeeded", resumed["error_code"]
    steps = {s["step_id"]: s["status"] for s in resumed["steps"]}
    assert steps["s1"] == "succeeded" and steps["s2"] == "succeeded"


def test_dr26_retry_creates_independent_attempts(env) -> None:
    manifest = make_manifest("script.flaky", "会失败两次的脚本", "script",
                             {"type": "object", "properties": {
                                 "args": {"type": "array", "items": {"type": "string"}}},
                              "required": []},
                             runtime="python", dry_run="none", idempotent="no",
                             compensate="manual")
    impl = '''import json, os, pathlib
params = json.loads(os.environ.get("WFP_PARAMS", "{}"))
counter = pathlib.Path(params["args"][0])
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
if n < 3:
    raise SystemExit(1)
print("WFP_OUTPUT::" + json.dumps({"ok": True, "attempt": n}))
'''
    env.runner.actions.create(manifest, impl, actor="pytest")
    pipeline = """apiVersion: wfp/v1
meta: { id: retry-demo, name: 重试演示, version: 1.0.0 }
steps:
  - id: s1
    uses: script.flaky@1.0.0
    with: { args: [counter.txt] }
    retry: { attempts: 2, delay_seconds: 0 }
"""
    ref = env.pipeline("retry.yaml", pipeline)
    result = env.runner.apply(ref, {}, yes=True)
    assert result["status"] == "succeeded", result["error_code"]
    assert result["steps"][0]["attempt"] == 3
    assert (env.tmp / "counter.txt").read_text() == "3"


def test_dr21_branch_and_bounded_loop(env) -> None:
    _seed(env)
    pipeline = """apiVersion: wfp/v1
meta: { id: loop-demo, name: 分支循环, version: 1.0.0 }
inputs:
  type: object
  properties:
    flag: { type: boolean, default: true }
steps:
  - id: s1
    uses: assert.file_exists@1.0.0
    when: "inputs.flag == false"
    with: { path: conf/app.json }
  - id: s2
    uses: assert.file_exists@1.0.0
    loop: { for_each: [a, b, c], max_iterations: 3 }
    with: { path: conf/app.json }
"""
    ref = env.pipeline("loop.yaml", pipeline)
    result = env.runner.apply(ref, {}, yes=True)
    assert result["status"] == "succeeded", result["error_code"]
    statuses = [(s["step_id"], s["iteration"], s["status"]) for s in result["steps"]]
    assert ("s1", 0, "skipped_condition") in statuses
    assert sum(1 for s in statuses if s[0] == "s2") == 3
    assert sorted(s[1] for s in statuses if s[0] == "s2") == [0, 1, 2]


def test_dr21_while_loop_over_limit_fails(env) -> None:
    pipeline = """apiVersion: wfp/v1
meta: { id: while-demo, name: 有界循环, version: 1.0.0 }
steps:
  - id: s1
    uses: assert.file_exists@1.0.0
    loop: { while: "index < 999", max_iterations: 3 }
    with: { path: conf/app.json }
"""
    ref = env.pipeline("while.yaml", pipeline)
    with pytest.raises(Exception) as exc:
        env.runner.plan(ref, {})
    assert "max_iterations" in str(exc.value)


def test_dr35_process_tree_termination() -> None:
    code = ("import subprocess, time; "
            "subprocess.Popen(['sleep', '30']); time.sleep(30)")
    proc = subprocess.Popen([sys.executable, "-c", code], **popen_kwargs())
    handle = ProcessHandle(proc)
    handle.terminate_tree(grace=0.2, force_after=1.0)
    assert not handle.alive, "取消后进程树必须被回收（DR-35）"


def test_dr64_run_control_writes_audit(env) -> None:
    from tests.conftest import LOCAL_PIPELINE
    _seed(env)
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    plan = env.runner.plan(ref, {})
    assert env.runner.pause(plan["run_id"])["status"] == "paused"
    assert env.runner.continue_run(plan["run_id"])["status"] == "running"
    assert env.runner.cancel(plan["run_id"])["status"] == "cancelling"
    actions = [r["action"] for r in env.store.read(
        lambda c: c.execute("SELECT action FROM audit").fetchall())]
    assert {"run.pause", "run.resume", "run.cancel"} <= set(actions)
