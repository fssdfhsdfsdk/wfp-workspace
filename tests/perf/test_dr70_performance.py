"""性能预算基准（DR-70）：500 步解析+lint、builtin 开销、日志吞吐。

Implements: DR-19, DR-29, DR-30, DR-38, DR-61, DR-70, DR-77
"""
from __future__ import annotations

import time

from wfp.contracts.pipeline import PipelineDoc, lint_pipeline
from wfp.engine.journal import EventWriter
from wfp.security.redactor import Redactor


def _big_pipeline(n: int = 500) -> str:
    steps = "\n".join(
        f"  - id: s{i}\n    uses: assert.file_exists@1.0.0\n    with: {{ path: conf/app.json }}"
        for i in range(n))
    return f"apiVersion: wfp/v1\nmeta:\n  id: perf\n  name: 性能\n  version: 1.0.0\nsteps:\n{steps}\n"


def test_dr70_parse_and_lint_500_steps_under_500ms() -> None:
    text = _big_pipeline(500)
    started = time.monotonic()
    doc = PipelineDoc.from_text(text)
    issues = lint_pipeline(doc, action_exists=lambda a, v: True, current_node=None)
    elapsed = time.monotonic() - started
    assert len(doc.steps) == 500
    assert not any(i.level == "error" for i in issues)
    assert elapsed < 0.5, f"500 步解析+lint 耗时 {elapsed:.3f}s（预算 500ms）"


def test_dr70_builtin_plan_overhead_under_20ms(env) -> None:
    import tempfile
    from pathlib import Path

    from wfp.actions.builtin import dispatch_plan
    from wfp.engine.cancel import CancelToken
    from wfp.engine.types import ActionContext
    from wfp.paths import DataPaths
    from wfp.store.db import Store

    (env.tmp / "conf").mkdir(exist_ok=True)
    target = env.tmp / "conf" / "app.json"
    target.write_text('{"env": "dev"}\n', encoding="utf-8")
    manifest = env.runner.actions.get_version("assert.file_exists", "1.0.0")["manifest"]
    with tempfile.TemporaryDirectory() as td:
        paths = DataPaths(Path(td)).ensure()
        store = Store(paths.db)
        store.migrate()
        try:
            from wfp.engine.journal import ArtifactWriter
            r = Redactor()
            events = EventWriter(paths, "r-perf", r)
            artifacts = ArtifactWriter(store, paths, "r-perf", r, events)
            ctx = ActionContext(params={"path": str(target)}, workspace=env.tmp, run_id="r-perf",
                                step_id="s", iteration=0, emit=lambda t, p: None,
                                artifact=artifacts, cancel=CancelToken(), deadline=float("inf"),
                                transport=None, secrets=None, node=None, redactor=r,
                                manifest=manifest, action_id="assert.file_exists",
                                action_version="1.0.0", allow_outside=True)
            started = time.monotonic()
            for _ in range(1000):
                dispatch_plan(ctx)
            per_call = (time.monotonic() - started) / 1000
            assert per_call < 0.02, f"builtin 单次开销 {per_call * 1000:.2f}ms"
            events.close()
        finally:
            store.close()


def test_dr61_log_5000_lines_are_all_persisted(env) -> None:
    events = EventWriter(env.paths, "r-throughput", Redactor())
    started = time.monotonic()
    for i in range(5000):
        events.emit("step.stdout", {"text": f"line {i}"})
    events.close()
    elapsed = time.monotonic() - started
    path = env.paths.logs / "r-throughput" / "events.ndjson"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5000
    assert EventWriter.tail(path, 4990)[-1]["seq"] == 5000
    assert elapsed < 5.0, f"5000 行落盘耗时 {elapsed:.2f}s"
