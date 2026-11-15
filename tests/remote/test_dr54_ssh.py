"""真实 SSH 靶机用例（默认跳过；`pytest -m remote` 显式执行）。

Implements: DR-54, DR-57, DR-58, DR-59, DR-73, DR-84
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.remote


def test_dr54_real_ssh_target_contract() -> None:
    pytest.skip("需要真实 Linux SSH 靶机；离线验收由 FakeTransport 契约测试覆盖（DR-75）")


def test_dr84_real_winrm_target_contract() -> None:
    pytest.skip("需要 Windows/WinRM 靶机；可选增强 DR-84，以 FakeTransport 契约测试结案")
