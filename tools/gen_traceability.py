#!/usr/bin/env python3
"""WFP 追溯矩阵生成与校验工具（CI 门禁工具）。

Implements: DR-74, DR-75 ；支撑 IR-25 的两条关系：
  * 满足关系  Input Requirements -> Derived Requirements
  * 合格性关系 Derived Requirements -> Qualification Cases

单一真源：requirements-spec.md。生成物禁止手工编辑：
  - traceability.csv          机器可读双矩阵
  - traceability.md           人可读双矩阵 + 覆盖统计
  - requirements-spec.md §7   （BEGIN/END GENERATED 标记之间）
  - qualification-strategy.md（BEGIN/END GENERATED 标记之间）

用法：
  python tools/gen_traceability.py             # 生成
  python tools/gen_traceability.py --check     # 只校验（CI 用，违规退出码 1）
  python tools/gen_traceability.py --check-code # 追加统计代码/测试对 DR 的引用覆盖率

仅依赖标准库，可在离线环境运行（DR-73）。
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "requirements-spec.md"
QUAL = ROOT / "qualification-strategy.md"

PRIORITIES = {"Must", "Should", "Could"}
METHODS = {"T": "测试", "A": "分析", "I": "检查/评审", "D": "演示"}
MILESTONES = [f"M{i}" for i in range(6)]
GATES = [f"G{i}" for i in range(6)]

DR_HEAD = re.compile(r"^####\s+(DR-\d{2})\s+(.+?)\s*$", re.M)
IR_ROW = re.compile(r"^\|\s*(IR-\d{2})\s*\|\s*(.+?)\s*\|", re.M)
RA_ROW = re.compile(r"^\|\s*(RA-\d{2})\s*\|\s*(.+?)\s*\|", re.M)


@dataclass
class DR:
    id: str
    title: str
    statement: str = ""
    acceptance: str = ""
    sources_raw: str = ""
    irs: list[str] = field(default_factory=list)
    ras: list[str] = field(default_factory=list)
    adrs: list[str] = field(default_factory=list)
    mds: list[str] = field(default_factory=list)
    priority: str = ""
    milestone: str = ""
    qc_id: str = ""
    method: str = ""
    level: str = ""
    gate: str = ""
    evidence: str = ""
    deprecated: bool = False

    @property
    def num(self) -> int:
        return int(self.id.split("-")[1])


def _section(text: str, start_marker: str, end_marker: str) -> str:
    """截取章节，避免把 §7 自动生成矩阵误当作需求定义源。"""
    i = text.find(start_marker)
    j = text.find(end_marker, i + 1) if i >= 0 else -1
    return text[i:j] if i >= 0 and j > i else ""


def parse_spec(text: str) -> tuple[dict[str, str], dict[str, str], list[DR]]:
    ir_sec = _section(text, "## 2. 输入需求", "## 3. 输入需求分析")
    ra_sec = _section(text, "## 3. 输入需求分析", "## 4. 模型")
    irs = {m.group(1): m.group(2) for m in IR_ROW.finditer(ir_sec)}
    ras = {m.group(1): m.group(2) for m in RA_ROW.finditer(ra_sec)}

    heads = list(DR_HEAD.finditer(text))
    drs: list[DR] = []
    for i, m in enumerate(heads):
        start = m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[start:end]
        # 只取到下一个二级/三级标题为止，避免吞掉后续章节
        nxt = re.search(r"^#{2,3}\s", body, re.M)
        if nxt:
            body = body[: nxt.start()]
        dr = DR(id=m.group(1), title=m.group(2))
        dr.deprecated = "[DEPRECATED]" in dr.title

        def grab(label: str, body: str = body) -> str:
            mm = re.search(rf"^-\s*{label}：(.*)$", body, re.M)
            return mm.group(1).strip() if mm else ""

        dr.statement = grab("陈述")
        dr.acceptance = grab("验收")
        dr.sources_raw = grab("来源")
        dr.irs = sorted(set(re.findall(r"IR-\d{2}", dr.sources_raw)))
        dr.ras = sorted(set(re.findall(r"RA-\d{2}", dr.sources_raw)))
        dr.adrs = sorted(set(re.findall(r"ADR-\d{2}", dr.sources_raw)))
        dr.mds = sorted(set(re.findall(r"MD-\d{2}", dr.sources_raw)))

        pm = re.search(r"^-\s*优先级：(\w+)｜里程碑：(M\d)", body, re.M)
        if pm:
            dr.priority, dr.milestone = pm.group(1), pm.group(2)

        qline = grab("合格性")
        parts = [p.strip() for p in qline.split("｜")]
        if parts:
            dr.qc_id = parts[0]
        if len(parts) > 1:
            mm = re.match(r"([TAID])·([^（]+)(（.*）)?", parts[1])
            if mm:
                dr.method, dr.level = mm.group(1), mm.group(2).strip()
                dr.level += (mm.group(3) or "")
        if len(parts) > 2:
            gm = re.search(r"G\d", parts[2])
            dr.gate = gm.group(0) if gm else ""
        if len(parts) > 3:
            dr.evidence = parts[3]
        drs.append(dr)
    return irs, ras, drs


def validate(irs: dict[str, str], ras: dict[str, str], drs: list[DR]) -> list[str]:
    errs: list[str] = []
    seen: dict[str, int] = defaultdict(int)
    for dr in drs:
        seen[dr.id] += 1
    for k, v in seen.items():
        if v > 1:
            errs.append(f"{k}: 编号重复 {v} 次（编号只增不改，§9.2）")

    covered_ir: set[str] = set()
    for dr in drs:
        tag = f"{dr.id}"
        if dr.deprecated:
            continue
        for f, val in (("陈述", dr.statement), ("验收", dr.acceptance), ("来源", dr.sources_raw)):
            if not val:
                errs.append(f"{tag}: 缺少「{f}」字段")
        if dr.priority not in PRIORITIES:
            errs.append(f"{tag}: 优先级非法 '{dr.priority}'")
        if dr.milestone not in MILESTONES:
            errs.append(f"{tag}: 里程碑非法 '{dr.milestone}'")
        if dr.qc_id != f"QC-{dr.num:02d}":
            errs.append(f"{tag}: 合格性用例编号应为 QC-{dr.num:02d}，实际 '{dr.qc_id}'")
        if dr.method not in METHODS:
            errs.append(f"{tag}: 合格性方法非法 '{dr.method}'（应为 T/A/I/D）")
        if dr.gate not in GATES:
            errs.append(f"{tag}: 门禁非法 '{dr.gate}'")
        elif dr.milestone in MILESTONES and GATES.index(dr.gate) < MILESTONES.index(dr.milestone):
            errs.append(f"{tag}: 门禁 {dr.gate} 早于里程碑 {dr.milestone}")
        if not dr.evidence:
            errs.append(f"{tag}: 缺少证据路径（测试文件或评审/演示记录）")
        if not dr.irs and not (dr.adrs or dr.mds):
            errs.append(f"{tag}: 既无 IR 来源也无 ADR/MD 架构派生依据（满足关系断裂）")
        for ir in dr.irs:
            if ir not in irs:
                errs.append(f"{tag}: 引用了不存在的 {ir}")
            covered_ir.add(ir)
        for ra in dr.ras:
            if ra not in ras:
                errs.append(f"{tag}: 引用了不存在的 {ra}")
        if dr.priority == "Must" and dr.method == "D" and "例外" not in dr.level:
            errs.append(f"{tag}: Must 需求不得仅以演示结案（需 T/A/I，或在方法中标注「例外」）")
        if dr.method == "T" and not dr.evidence.startswith("tests/"):
            errs.append(f"{tag}: 方法为 T 但证据不是 tests/ 下的测试文件")

    for ir in sorted(irs):
        if ir not in covered_ir:
            errs.append(f"{ir}: 未被任何 DR 满足（G-REQ-1 覆盖门禁失败）")

    nums = sorted(d.num for d in drs)
    if nums and nums != list(range(1, max(nums) + 1)):
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        missing_ids = [f"DR-{n:02d}" for n in missing]
        errs.append(f"DR 编号不连续，缺号：{missing_ids}（废弃需保留编号并标 DEPRECATED）")
    return errs


def build_matrices(irs: dict[str, str], drs: list[DR]) -> tuple[str, str, str]:
    active = [d for d in drs if not d.deprecated]

    # 7.1 满足关系
    by_ir: dict[str, list[DR]] = defaultdict(list)
    arch: list[DR] = []
    for d in active:
        if d.irs:
            for ir in d.irs:
                by_ir[ir].append(d)
        else:
            arch.append(d)
    sat = io.StringIO()
    sat.write("| 输入需求 | 客户输入（摘要） | 满足它的派生需求 | 数量 |\n| --- | --- | --- | --- |\n")
    for ir in sorted(irs):
        ds = sorted(by_ir.get(ir, []), key=lambda x: x.num)
        sat.write(f"| {ir} | {irs[ir][:46]} | {', '.join(d.id for d in ds) or '**未覆盖**'} | {len(ds)} |\n")
    if arch:
        sat.write(f"\n**架构派生（无直接 IR，依据 ADR/MD）**：{', '.join(d.id for d in arch)}\n")

    # 7.2 合格性关系
    qual = io.StringIO()
    qual.write("| DR | 需求标题 | 优先级 | 里程碑 | QC | 方法 | 门禁 | 证据 |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n")
    for d in sorted(active, key=lambda x: x.num):
        qual.write(
            f"| {d.id} | {d.title} | {d.priority} | {d.milestone} | {d.qc_id} | "
            f"{d.method}·{d.level} | {d.gate} | `{d.evidence}` |\n"
        )

    # 7.3 统计
    st = io.StringIO()
    st.write(f"- 输入需求 IR：**{len(irs)}** 条，覆盖率 **{len(by_ir)}/{len(irs)}**\n")
    st.write(f"- 派生需求 DR：**{len(active)}** 条（编号 DR-01…DR-{max(d.num for d in active):02d}）\n")
    st.write(f"- 合格性用例 QC：**{len(active)}** 条（1:1 绑定，无悬空）\n")
    prio = defaultdict(int)
    for d in active:
        prio[d.priority] += 1
    st.write(f"- 优先级分布：Must **{prio['Must']}** / Should **{prio['Should']}** / Could **{prio['Could']}**\n")
    meth = defaultdict(int)
    for d in active:
        meth[d.method] += 1
    st.write("- 合格性方法分布：" + " / ".join(f"{k}({METHODS[k]}) **{meth[k]}**" for k in "TAID" if meth[k]) + "\n")
    ms = defaultdict(list)
    for d in active:
        ms[d.milestone].append(d.id)
    st.write("\n| 里程碑 | DR 数 | 门禁 | DR 清单 |\n| --- | --- | --- | --- |\n")
    for m in MILESTONES:
        if ms[m]:
            ids = sorted(ms[m], key=lambda x: int(x.split('-')[1]))
            st.write(f"| {m} | {len(ids)} | G{m[1]} | {', '.join(ids)} |\n")
    return sat.getvalue(), qual.getvalue(), st.getvalue()


def replace_block(text: str, name: str, content: str) -> str:
    pat = re.compile(
        rf"(<!-- BEGIN GENERATED: {name} -->)(.*?)(<!-- END GENERATED: {name} -->)", re.S
    )
    if not pat.search(text):
        return text
    return pat.sub(lambda m: m.group(1) + "\n" + content.rstrip() + "\n" + m.group(3), text)


def write_csv(drs: list[DR], path: Path) -> None:
    cols = [
        "dr_id", "title", "priority", "milestone", "sources_ir", "risks", "arch_basis",
        "qc_id", "method", "level", "gate", "evidence", "code_refs", "test_refs", "status",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for d in sorted(drs, key=lambda x: x.num):
            w.writerow({
                "dr_id": d.id, "title": d.title, "priority": d.priority, "milestone": d.milestone,
                "sources_ir": ";".join(d.irs), "risks": ";".join(d.ras),
                "arch_basis": ";".join(d.adrs + d.mds), "qc_id": d.qc_id,
                "method": d.method, "level": d.level, "gate": d.gate, "evidence": d.evidence,
                "code_refs": "", "test_refs": "", "status": "deprecated" if d.deprecated else "specified",
            })


def scan_code_refs(drs: list[DR]) -> dict[str, dict[str, list[str]]]:
    """扫描仓库中对 DR 的实现引用与测试引用（DR-74 机器校验）。"""
    refs: dict[str, dict[str, list[str]]] = {d.id: {"code": [], "test": []} for d in drs}
    # wfp/web/dist 是随包分发的离线前端源码（不是构建中间产物），需纳入引用扫描
    skip = {".git", "node_modules", "__pycache__", ".venv", "build", "docs"}
    for p in ROOT.rglob("*"):
        if not p.is_file() or any(s in p.parts for s in skip):
            continue
        if p.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx", ".yaml", ".yml", ".toml"}:
            continue
        if p.name == Path(__file__).name:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(ROOT))
        for dr_id in set(re.findall(r"DR-\d{2}", txt)):
            if dr_id in refs:
                refs[dr_id]["code"].append(rel)
        # 测试函数命名形如 test_dr45_...（下划线是词字符，前后均不能用 \b）
        for num in set(re.findall(r"dr(\d{2})(?!\d)", txt)):
            dr_id = f"DR-{num}"
            if dr_id in refs:
                refs[dr_id]["test"].append(rel)
    return refs


def main() -> int:
    ap = argparse.ArgumentParser(description="WFP 追溯矩阵生成与校验")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件（CI 门禁）")
    ap.add_argument("--check-code", action="store_true", help="追加统计代码/测试对 DR 的引用")
    args = ap.parse_args()

    text = SPEC.read_text(encoding="utf-8")
    irs, ras, drs = parse_spec(text)
    errs = validate(irs, ras, drs)

    print(f"解析：IR={len(irs)}  RA={len(ras)}  DR={len(drs)}")
    if errs:
        print(f"\n校验失败（{len(errs)} 项）：")
        for e in errs:
            print("  ✗", e)
    else:
        print("校验通过：编号唯一连续 / IR 全覆盖 / DR↔QC 1:1 / 门禁与里程碑一致 / Must 方法合规")

    if args.check_code:
        refs = scan_code_refs(drs)
        implemented = [d for d in drs if refs[d.id]["code"] or refs[d.id]["test"]]
        print(f"\n代码/测试引用：{len(implemented)}/{len(drs)} 条 DR 已被引用（实现阶段门禁：Must 类 DR 应有测试引用）")
        for d in sorted(drs, key=lambda x: x.num):
            r = refs[d.id]
            if r["code"] or r["test"]:
                print(f"  {d.id}: code={len(r['code'])} test={len(r['test'])}")

    if args.check:
        return 1 if errs else 0
    if errs:
        print("\n存在校验错误，未生成产物。请先修正 requirements-spec.md。")
        return 1

    sat, qual, stats = build_matrices(irs, drs)
    write_csv(drs, ROOT / "traceability.csv")

    md = io.StringIO()
    md.write("# WFP 追溯矩阵（自动生成，勿手工编辑）\n\n")
    md.write("> 由 `python tools/gen_traceability.py` 从 `requirements-spec.md` 生成。\n")
    md.write("> 满足关系（IR→DR）与合格性关系（DR→QC）的唯一机器可读副本为 `traceability.csv`。\n\n")
    md.write("## 1. 满足关系：输入需求 → 派生需求\n\n" + sat + "\n")
    md.write("## 2. 合格性关系：派生需求 → 合格性用例\n\n" + qual + "\n")
    md.write("## 3. 覆盖统计\n\n" + stats)
    (ROOT / "traceability.md").write_text(md.getvalue(), encoding="utf-8")

    new_spec = replace_block(text, "satisfaction", sat)
    new_spec = replace_block(new_spec, "qualification", qual)
    new_spec = replace_block(new_spec, "stats", stats)
    SPEC.write_text(new_spec, encoding="utf-8")

    if QUAL.exists():
        q = QUAL.read_text(encoding="utf-8")
        q = replace_block(q, "qc_matrix", qual)
        q = replace_block(q, "qc_stats", stats)
        QUAL.write_text(q, encoding="utf-8")

    print("\n已生成：traceability.csv / traceability.md，并回填 requirements-spec.md §7"
          + ("、qualification-strategy.md" if QUAL.exists() else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
