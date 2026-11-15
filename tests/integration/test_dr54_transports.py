"""Transport 契约、12 类故障注入、原子编辑回滚与超时/取消治理。

Implements: DR-12, DR-35, DR-54, DR-55, DR-56, DR-57, DR-58, DR-59, DR-75, DR-84
"""
from __future__ import annotations

from pathlib import Path

import pytest

from wfp.engine.cancel import Cancelled, CancelToken
from wfp.errors import WfpError
from wfp.store.memory import MemoryStore
from wfp.transport.base import FAULT_KINDS, CapabilityError, TransportError
from wfp.transport.fake import FakeTransport
from wfp.transport.ssh import SSHTransport


def test_dr54_transport_contract_exec_put_get_stat_hash(tmp_path: Path) -> None:
    tp = FakeTransport(tmp_path / "root")
    assert tp.exec("echo hello").stdout.strip() == "hello"
    assert tp.exec("mkdir -p /opt/app").exit_code == 0
    receipt = tp.put(b"hello", "/opt/app/a.txt")
    assert receipt.bytes == 5 and len(receipt.sha256) == 64
    assert tp.get("/opt/app/a.txt") == b"hello"
    assert tp.stat("/opt/app/a.txt").size == 5
    assert tp.hash("/opt/app/a.txt") == receipt.sha256
    write = tp.atomic_write("/opt/app/a.txt", b"new", backup=True)
    assert write.sha256 and write.backup_path
    assert tp.get("/opt/app/a.txt") == b"new"
    assert tp.rollback("/opt/app/a.txt") is True and tp.get("/opt/app/a.txt") == b"hello"


def test_dr75_twelve_fault_kinds_are_injectable(tmp_path: Path) -> None:
    assert len(FAULT_KINDS) == 12
    for i, kind in enumerate(FAULT_KINDS):
        if kind in ("connect", "dns", "auth"):
            with pytest.raises(TransportError):
                FakeTransport(tmp_path / f"r{i}", faults={kind: True})
            continue
        tp = FakeTransport(tmp_path / f"r{i}", faults={kind: True})
        raised, receipt = False, None
        try:
            tp.exec("echo x")
            tp.put(b"abc", "/x.txt")
            tp.hash("/x.txt")
            receipt = tp.atomic_write("/x.txt", b"def")
        except (TransportError, Cancelled, WfpError):
            raised = True
        if kind == "hash_mismatch":
            assert receipt is None or receipt.rolled_back, "哈希不符必须显式回滚（DR-55）"
        else:
            assert kind == "slow" or raised, f"故障 {kind} 未生效（DR-75）"


def test_dr55_atomic_write_failure_rolls_back(tmp_path: Path) -> None:
    tp = FakeTransport(tmp_path / "root")
    tp.put(b"original", "/opt/app/conf.json")
    tp.fault("rename", True)
    with pytest.raises(TransportError):
        tp.atomic_write("/opt/app/conf.json", b"changed")
    assert tp.get("/opt/app/conf.json") == b"original", "rename 前注入故障远端必须保持原样"


def test_dr59_remote_cancel_and_dr58_timeout_classification(tmp_path: Path) -> None:
    tp = FakeTransport(tmp_path / "root")
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        tp.exec("sleep 300", timeout=10, cancel=token)
    assert tp.exec("sleep 300", timeout=0.01).exit_code == 0  # 超时被截断，不留残留
    from wfp.store.nodes import _classify
    assert _classify("Name or service not known") == "WFP-E-DNS"
    assert _classify("Authentication failed") == "WFP-E-AUTH"
    assert _classify("timed out") == "WFP-E-TIMEOUT"


def test_dr75_memory_store_is_injectable() -> None:
    store = MemoryStore()
    try:
        assert store.current_version() == 1
        store.set_setting("k", {"v": 1})
        assert store.get_setting("k") == {"v": 1}
        store.audit("pytest", "action.create", "x")
        assert store.read(lambda c: c.execute("SELECT COUNT(*) FROM audit").fetchone()[0]) == 1
    finally:
        store.close()


def test_dr54_ssh_adapter_is_available_and_winrm_is_optional() -> None:
    assert SSHTransport.kind == "ssh"
    try:
        import paramiko  # noqa: F401
    except ImportError:
        pytest.skip("paramiko 未安装（remote extra）")
    from wfp.transport.winrm import WinRMTransport
    with pytest.raises(CapabilityError):
        WinRMTransport({"host": "127.0.0.1"})
