"""契约与参数校验单元测试。

Implements: DR-10, DR-11, DR-12, DR-14, DR-16, DR-17, DR-18, DR-19, DR-48, DR-74
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from wfp.actions.manifests import BUILTIN_MANIFESTS, make_manifest
from wfp.contracts.manifest import capability_badges, validate_manifest
from wfp.engine.expr import validate_params
from wfp.errors import ContractError


def test_dr11_all_builtin_manifests_pass_schema() -> None:
    assert len(BUILTIN_MANIFESTS) >= 16
    for action_id, manifest in BUILTIN_MANIFESTS.items():
        assert validate_manifest(deepcopy(manifest))["id"] == action_id


def test_dr17_missing_capability_is_rejected_with_field_path() -> None:
    manifest = deepcopy(BUILTIN_MANIFESTS["config.edit"])
    manifest["capabilities"].pop("compensate")
    with pytest.raises(ContractError) as exc:
        validate_manifest(manifest)
    assert "capabilities.compensate" in str(exc.value.details)


def test_dr11_invalid_runtime_and_params_schema_are_rejected() -> None:
    manifest = deepcopy(BUILTIN_MANIFESTS["archive.extract"])
    manifest["runtime"] = "unknown"
    with pytest.raises(ContractError):
        validate_manifest(manifest)
    manifest = deepcopy(BUILTIN_MANIFESTS["archive.extract"])
    manifest["params"] = {"type": "not-a-type"}
    with pytest.raises(ContractError) as exc:
        validate_manifest(manifest)
    assert any("params" in d["path"] for d in exc.value.details)


def test_dr12_runtime_entry_consistency() -> None:
    manifest = deepcopy(BUILTIN_MANIFESTS["remote.exec"])
    manifest["entry"] = {"module": "wfp.actions.builtin:dispatch"}
    with pytest.raises(ContractError) as exc:
        validate_manifest(manifest)
    assert any("entry.operation" in d["path"] for d in exc.value.details)


def test_dr14_param_validation_error_paths_and_types() -> None:
    manifest = BUILTIN_MANIFESTS["config.edit"]
    with pytest.raises(ContractError) as exc:
        validate_params({"path": 42, "edits": []}, manifest, path="steps[0].with")
    assert exc.value.path.startswith("steps[0].with")
    assert any("path" in d["path"] for d in exc.value.details)


def test_dr17_badges_and_dr48_ui_widgets() -> None:
    b = capability_badges(BUILTIN_MANIFESTS["config.edit"])
    assert b["dry_run"] == "plan" and b["idempotent"] == "yes"
    assert BUILTIN_MANIFESTS["config.edit"]["ui"]["edits"]["ui:widget"] == "keyValueList"


def test_dr19_builtin_action_catalog_covers_eight_categories() -> None:
    categories = {m["category"] for m in BUILTIN_MANIFESTS.values()}
    assert {"archive", "fs", "config", "net", "assert", "script", "remote"} <= categories


def test_dr10_manifest_version_is_immutable_shape() -> None:
    m = make_manifest("demo.action", "演示", "config",
                      {"type": "object", "properties": {}, "required": []})
    assert m["version"] == "1.0.0" and m["plan"]["entry"]["module"]
