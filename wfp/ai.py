"""AI 草案生成器（可选、默认关闭、四道闸）。

Implements: DR-80, DR-81, DR-82, DR-83
生成 → schema 校验 → lint → 人工接受 → plan 确认闸；draft 不可 apply。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from wfp.contracts.pipeline import lint_pipeline, validate_pipeline
from wfp.contracts.yamlio import safe_dump, safe_load
from wfp.errors import Conflict, ContractError
from wfp.store.db import Store

RULES: list[tuple[str, str, dict[str, Any]]] = [
    (r"解压|压缩包|zip|tar", "archive.extract@1.0.0",
     {"archive": "{{ inputs.package }}", "dest": "work/app"}),
    (r"校验|存在|检查", "assert.file_exists@1.0.0", {"path": "work/app/conf/app.json"}),
    (r"修改.*(json|配置)|编辑.*配置", "config.edit@1.0.0",
     {"path": "work/app/conf/app.json", "format": "json",
      "edits": [{"key": "env", "value": "{{ inputs.env }}"}]}),
    (r"上传|发布", "remote.upload@1.0.0",
     {"items": [{"local": "work/app/", "remote": "/opt/app/current/", "recursive": True}]}),
    (r"下载", "remote.download@1.0.0",
     {"remote": "/opt/app/current/conf/app.json", "local": "work/downloaded.json"}),
    (r"远程|远端|ssh|yaml", "remote.config_edit@1.0.0",
     {"path": "/opt/app/conf/app.yaml", "format": "yaml",
      "edits": [{"key": "log.level", "value": "{{ inputs.log_level }}"}]}),
]


class AIDraftService:
    def __init__(self, store: Store, action_exists: Any) -> None:
        self.store = store
        self.action_exists = action_exists

    @property
    def enabled(self) -> bool:
        return bool(self.store.get_setting("ai.enabled", False))

    def set_enabled(self, value: bool) -> None:
        self.store.set_setting("ai.enabled", bool(value))
        self.store.audit("cli", "ai.toggle", None, {"enabled": bool(value)})

    @staticmethod
    def redact(text: str) -> str:
        """DR-83：主机名/IP/凭据样式脱敏后再作为提示词。"""
        text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<host>", text)
        text = re.sub(r"(?i)(password|token|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=***", text)
        return text

    def draft(self, description: str) -> dict[str, Any]:
        if not self.enabled:
            raise Conflict("AI 默认关闭（DR-83）")
        prompt = self.redact(description)
        steps: list[dict[str, Any]] = []
        for idx, (pattern, ref, params) in enumerate(RULES, 1):
            if re.search(pattern, description, re.I):
                aid, ver = ref.split("@")
                if not self.action_exists(aid, ver):
                    continue
                steps.append({"id": f"ai{idx}", "uses": ref, "with": params})
        if not steps:
            raise ContractError("描述未匹配到库内动作；AI 不得臆造动作（DR-80）", path="description")
        doc: dict[str, Any] = {
            "apiVersion": "wfp/v1",
            "meta": {"id": f"ai-draft-{hashlib.sha1(prompt.encode()).hexdigest()[:8]}",
                     "name": "AI 草案", "version": "1.0.0"},
            "inputs": {"type": "object", "properties": {
                "package": {"type": "string", "default": "build/app.zip"},
                "env": {"enum": ["dev", "staging", "prod"], "default": "staging"},
                "log_level": {"enum": ["debug", "info", "warn", "error"], "default": "info"},
            }, "required": []},
            "steps": steps,
        }
        text = safe_dump(doc)
        issues: list[dict[str, Any]] = []
        for _ in range(2):  # DR-81：最多 2 轮自动修正
            try:
                validate_pipeline(safe_load(text))
                from wfp.contracts.pipeline import PipelineDoc
                issues = [i.as_dict() for i in lint_pipeline(
                    PipelineDoc.from_text(text), action_exists=self.action_exists)]
                break
            except ContractError as exc:
                issues = [{"code": "WFP-E-SCHEMA", "level": "error", "message": str(exc)}]
                doc.setdefault("inputs", {})["required"] = []
                text = safe_dump(doc)
        status = "draft" if not any(i["level"] == "error" for i in issues) else "draft_invalid"
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        self.store.audit("ai", "ai.draft", doc["meta"]["id"],
                         {"prompt_sha256": digest, "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "issues": len(issues)})
        return {"id": doc["meta"]["id"], "yaml": text, "status": status, "issues": issues,
                "gates": ["schema", "lint", "human_accept", "plan_confirm"]}

    def accept(self, pipeline_id: str) -> dict[str, Any]:
        self.store.set_setting(f"ai.accepted.{pipeline_id}", True)
        self.store.audit("ai", "ai.accept", pipeline_id)
        return {"id": pipeline_id, "status": "active"}


__all__ = ["AIDraftService"]
