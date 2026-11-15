"""作用域链、模板沙箱与受限表达式单元测试。

Implements: DR-23, DR-24, DR-25, DR-29, DR-53
"""
from __future__ import annotations

import pytest

from wfp.engine.expr import ExprError, Scope, eval_expr, eval_template, render_value, resolve_local_path
from wfp.errors import ContractError
from wfp.security.redactor import Redactor, SecretStr


def test_dr23_scope_priority_outputs_over_inputs_over_vars() -> None:
    s = Scope(inputs={"x": "input", "only": 1}, variables={"x": "var"},
              outputs={"x": "output"}, node={"x": "node"})
    assert s.resolve("x") == "output"
    assert s.resolve("only") == 1
    assert eval_expr("inputs.x == 'input' and vars.x == 'var'", s) is True
    child = s.push(item={"name": "a"}, index=2)
    assert eval_expr("item.name == 'a' and index == 2", child) is True
    s.register("cfg", {"new_sha256": "abc"})
    assert eval_expr("cfg.new_sha256", s) == "abc"


def test_dr23_template_filters_and_secret_never_render_plaintext() -> None:
    s = Scope(inputs={"package": "build/app-1.4.2.zip", "env": "prod"}, variables={"dir": "/opt/app"})
    assert eval_template("inputs.package | basename", s) == "app-1.4.2.zip"
    assert render_value("{{ inputs.env }}", s) == "prod"
    assert render_value("{{ vars.dir }}/current", s) == "/opt/app/current"
    resolver = lambda ref: SecretStr("P@ssw0rd", ref)  # noqa: E731
    value = render_value("{{ secret('node.prod.root') }}", s, secret_fn=resolver)
    assert isinstance(value, SecretStr) and str(value) == "***"
    redactor = Redactor()
    redactor.add(value)
    assert "P@ssw0rd" not in redactor.scrub("password=P@ssw0rd")


def test_dr25_expr_rejects_import_attribute_traversal_and_comprehension() -> None:
    s = Scope(variables={"a": 2})
    for expr in ("__import__('os').system('echo hacked')", "().__class__",
                 "[x for x in range(3)]", "vars.a.__class__"):
        with pytest.raises((ExprError, ContractError)):
            eval_expr(expr, s)
    assert eval_expr("len('abc') + vars.a * 2 == 7", s) is True
    assert eval_expr("regex_match('app-1.4.2.zip', '\\\\.zip$')", s) is True


def test_dr25_expression_timeout_budget() -> None:
    s = Scope(variables={})
    assert eval_expr("1 + 2 + 3", s) == 6
    with pytest.raises(ExprError):
        eval_expr(" + ".join(["1"] * 1500), s)


def test_dr24_absolute_and_relative_paths_are_workspace_bound() -> None:
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        assert resolve_local_path("conf/app.json", base) == base / "conf/app.json"
        assert resolve_local_path(str(base / "conf/app.json"), base) == base / "conf/app.json"
        with pytest.raises(ContractError):
            resolve_local_path("../outside", base)
