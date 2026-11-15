#!/usr/bin/env python3
"""校验示例夹具是否符合契约 Schema（M0 起的 CI 前置检查）。

Implements: DR-11, DR-14, DR-20（契约优先原则的落地检查）

依赖：pyyaml + jsonschema（离线环境用 wheelhouse 安装）。
若依赖缺失则跳过并给出提示（退出码 0），以免阻塞纯文档阶段的 CI。

用法：
  python tools/validate_examples.py
  python tools/validate_examples.py --strict   # 依赖缺失时视为失败
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    try:
        import yaml  # type: ignore
        from jsonschema import Draft202012Validator  # type: ignore
    except ImportError:
        msg = "跳过：缺少 pyyaml / jsonschema（pip install pyyaml jsonschema）"
        print(msg)
        return 1 if args.strict else 0

    manifest_schema = json.loads((ROOT / "schemas/action-manifest.schema.json").read_text(encoding="utf-8"))
    pipeline_schema = json.loads((ROOT / "schemas/pipeline.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(pipeline_schema)

    failures = 0
    jobs = [
        (sorted((ROOT / "examples/actions").glob("*.yaml")), manifest_schema, "manifest"),
        (sorted((ROOT / "examples").glob("pipeline.*.yaml")), pipeline_schema, "pipeline"),
    ]
    for files, schema, kind in jobs:
        v = Draft202012Validator(schema)
        for f in files:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
            rel = f.relative_to(ROOT)
            if errs:
                failures += 1
                print(f"✗ {rel} ({kind})")
                for e in errs[:5]:
                    path = "/".join(str(p) for p in e.path) or "<root>"
                    print(f"    {path}: {e.message}")
            else:
                print(f"✓ {rel} ({kind})")
    print(f"\n合计失败 {failures} 个")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
