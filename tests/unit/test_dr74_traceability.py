"""可维护性门禁：DR 引用规范与追溯矩阵机器校验。

Implements: DR-08, DR-74, DR-75
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "gen_traceability.py"), *args],
                          cwd=ROOT, capture_output=True, text=True, check=False)


def test_dr74_traceability_check_passes() -> None:
    r = _tool("--check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DR=84" in r.stdout and "校验通过" in r.stdout


def test_dr74_code_and_test_reference_coverage() -> None:
    r = _tool("--check-code")
    m = re.search(r"代码/测试引用：(\d+)/84", r.stdout)
    assert m, r.stdout
    assert int(m.group(1)) == 84, f"DR 引用覆盖率不足：{m.group(1)}/84"
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_traceability as g
    drs = g.parse_spec((ROOT / "requirements-spec.md").read_text(encoding="utf-8"))[2]
    refs = g.scan_code_refs(drs)
    assert all(refs[d.id]["code"] for d in drs)
    assert all(refs[d.id]["test"] for d in drs), "每条 DR 必须有测试引用（DR-74）"
    names = " ".join(p.read_text(encoding="utf-8")
                     for p in (ROOT / "tests").rglob("test_*.py"))
    assert len(set(re.findall(r"test_dr(\d{2})", names))) >= 20


def test_dr74_every_module_docstring_mentions_dr() -> None:
    modules = sorted((ROOT / "wfp").rglob("*.py"))
    missing = [str(p.relative_to(ROOT)) for p in modules
               if p.name != "__init__.py" and "DR-" not in p.read_text(encoding="utf-8")]
    assert not missing, f"模块缺少 DR 引用：{missing}"
