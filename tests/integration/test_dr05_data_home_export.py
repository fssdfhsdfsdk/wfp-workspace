"""数据目录、导出/导入、环境自诊断与凭据加密。

Implements: DR-04, DR-05, DR-06, DR-07, DR-52, DR-53, DR-68, DR-73, DR-78, DR-79, DR-83, DR-84
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tests.conftest import LOCAL_PIPELINE
from wfp.doctor import run_doctor
from wfp.maintenance import diagnostics, export_all, import_all, verify
from wfp.paths import DataPaths
from wfp.security.redactor import SecretStr
from wfp.store.db import Store
from wfp.store.pipelines import PipelineStore
from wfp.transport.base import CapabilityError


def _seed_config(env) -> None:
    conf = env.tmp / "conf"
    conf.mkdir(exist_ok=True)
    (conf / "app.json").write_text('{"env": "dev"}\n', encoding="utf-8")


def test_dr05_wfp_home_is_the_only_writable_root(env) -> None:
    assert env.paths.root.is_dir()
    assert env.paths.db.exists()
    assert str(env.paths.root).startswith(str(env.tmp))
    assert DataPaths.resolve().root == env.paths.root


def test_dr52_credentials_never_plaintext_at_rest(env) -> None:
    plain = "P@ssw0rd/中文-secret"
    rec = env.runner.credentials.put("prod.root", plain)
    assert "value" not in rec and plain not in json.dumps(rec)
    assert plain not in env.paths.db.read_bytes().decode("utf-8", "ignore")
    assert env.runner.credentials.reveal(rec["id"]) == plain
    listed = env.runner.credentials.list()
    assert all("ciphertext" not in c and plain not in json.dumps(c) for c in listed)
    secret = SecretStr(plain, "prod.root")
    assert str(secret) == "***"


def test_dr05_export_excludes_credentials_and_import_restores(env, tmp_path: Path) -> None:
    plain = "SUPER-SECRET-PLAINTEXT"
    env.runner.credentials.put("prod.root", plain)
    ps = PipelineStore(env.store, env.paths)
    ps.create(LOCAL_PIPELINE)
    out = tmp_path / "backup.zip"
    export_all(env.paths, env.store, out)
    raw = b"".join(zipfile.ZipFile(out).read(n) for n in zipfile.ZipFile(out).namelist())
    assert plain.encode() not in raw, "默认导出不含凭据明文（DR-05/52）"
    with zipfile.ZipFile(out) as z:
        conn_bytes = z.read("wfp.db")
    assert b"SUPER-SECRET-PLAINTEXT" not in conn_bytes

    dest = DataPaths(tmp_path / "imported").ensure()
    store2 = Store(dest.db)
    result = import_all(dest, store2, out)
    store2.close()
    assert result["ok"] is True
    check = Store(dest.db)
    try:
        check.migrate()
        assert PipelineStore(check, dest).list(), "导入后工作项可用"
        assert not check.read(lambda c: c.execute("SELECT id FROM credential").fetchall())
    finally:
        check.close()


def test_dr06_doctor_structured_report_and_dr78_diagnostics(env) -> None:
    report = run_doctor(env.paths, store=env.store, verify=True, port=8765)
    assert {"python", "data_home", "disk", "port", "schemas", "credential_backend",
            "ssh", "ruamel", "integrity", "database"} <= {c["name"] for c in report["checks"]}
    assert all("suggestion" in c for c in report["checks"])
    path = diagnostics(env.paths, env.store, None)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert "doctor.json" in names and "environment.json" in names and "audit.csv" in names


def test_dr79_integrity_detects_corrupted_artifact(env) -> None:
    _seed_config(env)
    ref = env.pipeline("local.yaml", LOCAL_PIPELINE)
    result = env.runner.apply(ref, {}, yes=True)
    assert result["status"] == "succeeded"
    art = env.runner.runs.artifacts(result["id"])[0]
    (env.paths.root / art["path"]).write_bytes(b"corrupted")
    problems = verify(env.paths, env.store)
    assert not problems["ok"]
    assert any(p["problem"] == "hash_mismatch" for p in problems["problems"])


def test_dr84_winrm_optional_adapter_reports_capability_gap(env) -> None:
    from wfp.transport.winrm import WinRMTransport
    with pytest.raises(CapabilityError):
        WinRMTransport({"host": "127.0.0.1", "os_kind": "winrm"})


def test_dr83_ai_defaults_off_and_redacts_prompt(env) -> None:
    from wfp.ai import AIDraftService
    service = AIDraftService(env.store, env.runner._action_exists)
    assert service.enabled is False
    assert "<host>" in service.redact("连接 10.0.0.8 并 password=abc123")
    from wfp.errors import Conflict
    with pytest.raises(Conflict):
        service.draft("解压并上传")
