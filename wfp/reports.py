"""运行摘要与跨 run 报表（md / html / csv）。

Implements: DR-65, DR-66, DR-78
"""
from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter
from typing import Any

from wfp.paths import DataPaths
from wfp.store.runs import RunStore

MEDIA = {"md": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8",
         "csv": "text/csv; charset=utf-8"}


def _stats(run: dict[str, Any], steps: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    status = Counter(s["status"] for s in steps)
    total = len(steps) or 1
    failed = [s for s in steps if s["status"] == "failed"]
    return {
        "run_id": run["id"], "pipeline": run["pipeline_id"], "version": run["pipeline_version"],
        "status": run["status"], "phase": run["phase"], "trigger": run["trigger"],
        "started_at": run["started_at"], "finished_at": run["finished_at"],
        "error_code": run["error_code"], "steps": len(steps),
        "succeeded": status.get("succeeded", 0), "failed": len(failed),
        "skipped_idempotent": status.get("skipped_idempotent", 0),
        "skipped_condition": status.get("skipped_condition", 0),
        "retries": sum(max(0, int(s.get("attempt") or 1) - 1) for s in steps),
        "skip_rate": round(100 * status.get("skipped_idempotent", 0) / total, 1),
        "artifacts": len(artifacts),
        "failure_top": [s["step_id"] for s in failed[:5]],
        "duration_ms": _duration(run),
    }


def _duration(run: dict[str, Any]) -> int:
    from datetime import datetime
    try:
        a = datetime.fromisoformat(run["started_at"])
        b = datetime.fromisoformat(run["finished_at"]) if run.get("finished_at") else datetime.now().astimezone()
        return int((b - a).total_seconds() * 1000)
    except (ValueError, TypeError):
        return 0


def run_report(paths: DataPaths, run_store: RunStore, run_id: str, *, fmt: str = "md") -> tuple[str, str, str]:
    """返回 (内容, media_type, 文件名)。"""
    run = run_store.get(run_id)
    steps, artifacts = run_store.steps(run_id), run_store.artifacts(run_id)
    st = _stats(run, steps, artifacts)
    name = f"wfp-report-{run_id}.{fmt}"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(st.keys()))
        w.writeheader()
        w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                    for k, v in st.items()})
        return "\ufeff" + buf.getvalue(), MEDIA["csv"], name  # UTF-8 BOM（Excel）
    if fmt == "html":
        rows = "".join(f"<tr><td>{html.escape(str(s['step_id']))}</td>"
                       f"<td>{html.escape(str(s['status']))}</td>"
                       f"<td>{html.escape(str(s.get('attempt') or 1))}</td></tr>" for s in steps)
        doc = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
               "<title>WFP Run Report</title><style>body{font-family:system-ui;margin:2rem}"
               "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.4rem .7rem}"
               "</style><h1>WFP 运行报表</h1>"
               f"<p>Run <code>{html.escape(run_id)}</code> · 状态 <b>{html.escape(run['status'])}</b></p>"
               "<h2>摘要</h2><pre>" + html.escape(json.dumps(st, ensure_ascii=False, indent=2)) + "</pre>"
               "<h2>步骤</h2><table><tr><th>步骤</th><th>状态</th><th>attempt</th></tr>"
               + rows + "</table></html>")
        return doc, MEDIA["html"], name
    lines = [f"# WFP 运行报表 `{run_id}`", "",
             f"- 工作项：`{st['pipeline']}@{st['version']}`", f"- 状态：**{st['status']}**",
             f"- 阶段：{st['phase']}｜触发：{st['trigger']}",
             f"- 步骤：{st['steps']}（成功 {st['succeeded']} / 失败 {st['failed']} / 幂等跳过 {st['skipped_idempotent']}）",
             f"- 幂等跳过率：{st['skip_rate']}%｜重试：{st['retries']}｜证据：{st['artifacts']}",
             f"- 耗时：{st['duration_ms']}ms", "",
             "| 步骤 | 动作 | 状态 | attempt | 跳过原因 |", "| --- | --- | --- | --- | --- |"]
    for s in steps:
        lines.append(f"| {s['step_id']} | {s['action_id']}@{s['action_version']} | {s['status']} | "
                     f"{s.get('attempt') or 1} | {s.get('skip_reason') or '-'} |")
    if artifacts:
        lines += ["", "## 证据", *[f"- `{a['path']}`（{a['kind']}, sha256={a['sha256'][:12]}…）"
                                  for a in artifacts[:20]]]
    return "\n".join(lines) + "\n", MEDIA["md"], name


def summary(paths: DataPaths, run_store: RunStore) -> dict[str, Any]:
    runs = run_store.list()
    counts = Counter(r["status"] for r in runs)
    return {"runs": len(runs), "by_status": dict(counts),
            "success_rate": round(100 * counts.get("succeeded", 0) / max(1, len(runs)), 1),
            "per_run": [_stats(r, run_store.steps(r["id"]), run_store.artifacts(r["id"]))
                        for r in runs[:50]]}


__all__ = ["run_report", "summary"]
