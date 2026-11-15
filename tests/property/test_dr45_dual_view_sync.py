"""双视图最小 patch 属性测试：只触及 steps[i]、无抖动循环、冲突以真源为准。

Implements: DR-16, DR-43, DR-44, DR-45, DR-46, DR-48
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

import pytest

from tests.property.corpus import CORPUS
from wfp.errors import Conflict
from wfp.sync.patch import GraphDelta, YamlDoc, apply_deltas, layout_step_ids, project_text, prune_layout

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "pipeline.deploy-sample.yaml"


def _block(lines: list[str], step_id: str) -> tuple[int, int]:
    start = next(i for i, line in enumerate(lines) if re.search(rf"-\s+id:\s*{step_id}\b", line))
    end = next((i for i in range(start + 1, len(lines))
                if re.search(r"-\s+id:\s*\w+", lines[i])), len(lines))
    return start, end


def _changed_original_lines(before: str, after: str) -> list[int]:
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    out: list[int] = []
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag != "equal":
            out.extend(range(i1, i2))
    return out


def test_dr45_update_param_changes_only_target_step_region() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    for step_id in ("s1", "s4", "s9"):
        doc = YamlDoc(text)
        out = doc.apply_delta(GraphDelta("update_param", step_id=step_id, field="name",
                                         value="改名后的步骤"))
        start, end = _block(text.splitlines(), step_id)
        changed = _changed_original_lines(text, out)
        assert changed, "应产生一次最小 patch"
        assert all(start <= i < end for i in changed), (step_id, changed, start, end)
        assert out.splitlines()[:start] == text.splitlines()[:start], "非 steps 区域必须字节级不变"


def test_dr45_random_graph_ops_and_undo_are_byte_stable() -> None:
    import random
    rng = random.Random(20261115)
    text = EXAMPLE.read_text(encoding="utf-8")
    doc = YamlDoc(text)
    header = text.splitlines()[:10]
    for i in range(20):
        step_id = rng.choice(["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10"])
        doc.apply_delta(GraphDelta("update_param", step_id=step_id, field="name", value=f"n{i}"))
    assert doc.text.splitlines()[:10] == header
    assert doc.undo(20) == text, "撤销 20 步必须回到初始文本（DR-44/47）"


def test_dr45_no_fixed_point_drift() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    doc = YamlDoc(text)
    delta = GraphDelta("update_param", step_id="s1", field="dest", value="work/final.zip")
    first = doc.apply_delta(delta)
    second = doc.apply_delta(delta)
    assert first == second, "同一编辑不得产生二次 patch（回声抑制，DR-45）"
    model1, model2 = project_text(first), project_text(second)
    assert [n["id"] for n in model1.nodes] == [n["id"] for n in model2.nodes]


def test_dr45_conflict_prefers_yaml_source() -> None:
    doc = YamlDoc(EXAMPLE.read_text(encoding="utf-8"))
    with pytest.raises(Conflict):
        doc.apply_delta(GraphDelta("update_param", step_id="s1", field="dest", value="x"),
                        expected_sha="0" * 64)
    assert doc.text == EXAMPLE.read_text(encoding="utf-8")


def test_dr45_insert_remove_move_preserve_comments() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    deltas = [
        GraphDelta("insert_step", after_step_id="s1",
                   new_step={"id": "snew", "uses": "assert.file_exists@1.0.0",
                             "with": {"path": "work/app/conf/app.json"}}),
        GraphDelta("move_step", src=10, dst=2),
        GraphDelta("remove_step", step_id="snew"),
    ]
    doc = YamlDoc(text)
    for d in deltas:
        doc.apply_delta(d)
    assert "# 关键校验：目标配置文件不存在则报错退出（客户明确要求）" in doc.text
    assert "# ============================================================" in doc.text
    out, final = apply_deltas(text, [GraphDelta("update_uses", step_id="s1",
                                                value="fs.move@1.0.1")])
    assert "fs.move@1.0.1" in out and final.project().nodes[0]["uses"] == "fs.move@1.0.1"


def test_dr46_layout_is_semantically_irrelevant() -> None:
    text = CORPUS[0]
    ids = layout_step_ids(text)
    assert {"s1", "s2", "s3"} <= ids
    clean, removed = prune_layout({"positions": {"s1": {"x": 1}, "ghost": {"x": 9}},
                                   "collapsed": ["s1", "ghost"], "groups": {"g": ["s1", "ghost"]},
                                   "viewport": {"zoom": 1}}, ids)
    assert removed == ["ghost"]
    assert "ghost" not in clean["positions"] and clean["viewport"]["zoom"] == 1
    assert "pipeline.layout.json" not in text
