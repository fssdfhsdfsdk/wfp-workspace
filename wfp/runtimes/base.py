"""统一 runtime 适配器接口 prepare/plan/apply/compensate/cancel。

Implements: DR-12, DR-15, DR-17, DR-18, DR-30, DR-34, DR-35, DR-36
ADR-02：统一契约，不统一实现；无 plan 入口者绝不执行（ADR-11）。
"""
from __future__ import annotations

from typing import Any

from wfp.engine.types import ActionContext, ApplyResult, PlanResult
from wfp.errors import ActionError


class RuntimeAdapter:
    def __init__(self, manifest: dict[str, Any], registry: Any | None = None) -> None:
        self.manifest = manifest
        self.registry = registry
        self.runtime = manifest["runtime"]
        self.caps = manifest["capabilities"]
        self.handle: ProcessHandleLike | None = None

    def prepare(self, ctx: ActionContext) -> None:
        return None

    def plan(self, ctx: ActionContext) -> PlanResult:
        if self.caps.get("dry_run") != "plan":
            return PlanResult(unpredictable=True, targets=[],
                              risks=["动作未声明 dry_run=plan，plan 阶段不执行实现体（DR-30）"],
                              no_backup_reason="无 plan 入口，apply 前无法预列备份目标（DR-33）")
        if self.runtime == "remote":
            from wfp.runtimes.remote import dispatch_plan
            return dispatch_plan(ctx)
        if self.runtime == "builtin":
            from wfp.actions.builtin import dispatch_plan as builtin_plan
            return builtin_plan(ctx)
        # 脚本 runtime 若声明可干跑，只能给出"不可预测"占位，绝不半执行
        return PlanResult(unpredictable=True, targets=[],
                          risks=["脚本动作无法证明只读，plan 不执行（ADR-11）"])

    def apply(self, ctx: ActionContext) -> ApplyResult:
        if self.runtime == "builtin":
            from wfp.actions.builtin import dispatch as builtin_apply
            return builtin_apply(ctx)
        if self.runtime == "remote":
            from wfp.runtimes.remote import dispatch as remote_apply
            return remote_apply(ctx)
        from wfp.runtimes.script import run_script
        return run_script(ctx)

    def compensate(self, ctx: ActionContext, receipt: dict[str, Any]) -> bool:
        if self.runtime == "remote":
            from wfp.runtimes.remote import dispatch_compensate
            return dispatch_compensate(ctx, receipt)
        if self.runtime == "builtin":
            from wfp.actions.builtin import dispatch_compensate as builtin_comp
            return builtin_comp(ctx, receipt)
        return False

    def cancel(self, handle: Any | None = None) -> None:
        from wfp.engine.cancel import ProcessHandle
        target = handle or self.handle
        if isinstance(target, ProcessHandle):
            target.terminate_tree()


class ProcessHandleLike:  # 仅用于类型标注占位（DR-74 全量类型标注）
    pass


def adapter_for(manifest: dict[str, Any], registry: Any | None = None) -> RuntimeAdapter:
    if manifest.get("runtime") not in {"builtin", "python", "powershell", "cmd", "remote"}:
        raise ActionError(f"未知 runtime：{manifest.get('runtime')}")
    return RuntimeAdapter(manifest, registry)


__all__ = ["RuntimeAdapter", "adapter_for"]
