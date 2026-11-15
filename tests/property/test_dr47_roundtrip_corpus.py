"""G3 阻塞门禁：≥200 份语料的 round-trip 无损属性测试（DR-47）。

Implements: DR-45, DR-46, DR-47
"""
from __future__ import annotations

import pytest

from tests.property.corpus import CORPUS, build
from wfp.sync.patch import YamlDoc


def test_dr47_corpus_size_and_byte_identical_roundtrip() -> None:
    assert len(CORPUS) >= 200, f"G3 门禁语料不足：{len(CORPUS)}"
    for idx, text in enumerate(CORPUS):
        doc = YamlDoc(text)
        assert doc.dump() == text, f"语料 #{idx} round-trip 丢失字节（注释/键序/引号/换行/锚点）"


def test_dr47_crlf_chinese_anchor_and_long_lines_preserved() -> None:
    text = build(quote="single", crlf=True, anchor=True, comment="block",
                 blanks=2, long_value=True, style="wide", trailing=True)
    doc = YamlDoc(text)
    out = doc.dump()
    assert out == text
    assert "\r\n" in out and "中文" in out and "*base" in out
    assert len(max(out.splitlines(), key=len)) >= 300


def test_dr47_undo_returns_original_bytes() -> None:
    text = build(crlf=True, anchor=True, comment="inline")
    doc = YamlDoc(text)
    from wfp.sync.patch import GraphDelta
    for i in range(20):
        doc.apply_delta(GraphDelta("update_param", step_id="s2", field="value", value=f"v{i}"))
    assert doc.text != text
    assert doc.undo(20) == text


def test_dr47_hypothesis_shrinks_minimal_counterexample() -> None:
    pytest.importorskip("hypothesis")
    from hypothesis import given, settings
    from hypothesis import strategies as st

    from wfp.sync.patch import GraphDelta

    @settings(max_examples=25, deadline=None)
    @given(st.sampled_from(CORPUS))
    def _property(text: str) -> None:
        doc = YamlDoc(text)
        assert doc.dump() == text
        out = doc.apply_delta(GraphDelta("update_param", step_id="s2", field="value", value="changed"))
        assert "changed" in out
        assert doc.project().nodes[1]["id"] == "s2"

    _property()
