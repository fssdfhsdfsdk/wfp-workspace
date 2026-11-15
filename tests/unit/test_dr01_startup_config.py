"""启动配置文件（端口/数据目录/保留策略）解析、优先级与接入测试。

Implements: DR-01, DR-05, DR-06, DR-67, DR-69, DR-72, DR-73, DR-76, DR-77
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wfp.api.app import create_app
from wfp.cli import main as cli_main
from wfp.cli import services
from wfp.config import EXAMPLE_TOML, Config
from wfp.doctor import run_doctor
from wfp.errors import ContractError

ROOT = Path(__file__).resolve().parents[2]


def _write_config(tmp: Path, text: str) -> Path:
    p = tmp / "custom.toml"
    p.write_text(text, encoding="utf-8")
    return p


def _cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ, WFP_HOME=str(home))
    return subprocess.run([sys.executable, "-m", "wfp.cli", *args], cwd=ROOT,
                          env=e, capture_output=True, text=True, check=False)


def test_dr01_example_template_and_toml_roundtrip(tmp_path: Path) -> None:
    cfg = Config.default()
    assert cfg.port == 8765 and cfg.host == "127.0.0.1" and cfg.lan is False
    assert cfg.retention_days == 30 and cfg.log_quota_mb == 200
    assert tomllib.loads(EXAMPLE_TOML)["server"]["port"] == 8765
    p = _write_config(tmp_path, cfg.to_toml())
    loaded = Config.load(p)
    assert loaded.exists is True and loaded.port == cfg.port
    assert loaded.retention_max_bytes == cfg.retention_max_bytes
    assert loaded.db_file == "" and cfg.public_dict()["db_path"].endswith("wfp.db")
    assert "token" not in cfg.public_dict() and "password" not in cfg.public_dict()


def test_dr05_config_data_dir_selects_storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WFP_HOME", raising=False)
    monkeypatch.delenv("WFP_CONFIG", raising=False)
    target = tmp_path / "custom-data"
    p = _write_config(tmp_path, f'[storage]\ndata_dir = "{target}"\n')
    cfg = Config.resolve(config_path=p)
    assert cfg.require_paths().root == target.resolve()
    assert cfg.public_dict()["data_dir_resolved"] == str(target.resolve())


def test_dr05_config_db_file_overrides_database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WFP_HOME", raising=False)
    monkeypatch.delenv("WFP_CONFIG", raising=False)
    target = tmp_path / "custom-data"
    p = _write_config(tmp_path, f'[storage]\ndata_dir = "{target}"\ndb_file = "pipeline.db"\n')
    cfg = Config.resolve(config_path=p)
    assert cfg.require_paths().db == (target / "pipeline.db").resolve()
    assert cfg.public_dict()["db_path"].endswith("pipeline.db")
    absolute = _write_config(tmp_path, f'[storage]\ndb_file = "{target / "absolute.db"}"\n')
    assert Config.resolve(config_path=absolute).require_paths().db == (target / "absolute.db").resolve()


def test_dr05_wfp_home_overrides_config_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "env-home"
    monkeypatch.setenv("WFP_HOME", str(home))
    p = _write_config(tmp_path, '[storage]\ndata_dir = "/tmp/should-not-win"\n')
    cfg = Config.resolve(config_path=p)
    assert cfg.require_paths().root == home.resolve()


def test_dr01_cli_overrides_and_validation(tmp_path: Path) -> None:
    p = _write_config(tmp_path, "[server]\nport = 9123\n")
    cfg = Config.resolve(config_path=p, overrides={"port": 9200, "log_level": "debug"})
    assert cfg.port == 9200 and cfg.log_level == "debug"
    assert Config.resolve(config_path=p).port == 9123
    with pytest.raises(ContractError):
        Config.resolve(config_path=p, overrides={"unknown_key": 1})


def test_dr72_non_loopback_host_requires_lan(tmp_path: Path) -> None:
    bad = _write_config(tmp_path, '[server]\nhost = "0.0.0.0"\nlan = false\n')
    with pytest.raises(ContractError) as exc:
        Config.load(bad)
    assert "回环" in exc.value.message
    good = _write_config(tmp_path, '[server]\nhost = "0.0.0.0"\nlan = true\n')
    assert Config.load(good).lan is True


def test_dr01_invalid_config_is_rejected(tmp_path: Path) -> None:
    for text in ("[server\n", "[misc]\nx = 1\n", "[server]\nports = 1\n",
                 "[server]\nport = 0\n", "[server]\nport = \"abc\"\n"):
        with pytest.raises(ContractError):
            Config.load(_write_config(tmp_path, text))


def test_dr76_cli_config_show_and_init(tmp_path: Path) -> None:
    home = tmp_path / "home"
    p = _write_config(tmp_path, "[server]\nport = 9130\n[storage]\nretention_days = 7\n")
    show = _cli(home, "--config", str(p), "config", "show", "--json")
    assert show.returncode == 0, show.stderr
    data = json.loads(show.stdout)
    assert data["port"] == 9130 and data["retention_days"] == 7
    assert data["config_file"] == str(p) and data["data_dir_resolved"] == str(home.resolve())

    out = tmp_path / "generated.toml"
    init = _cli(home, "config", "init", "--out", str(out))
    assert init.returncode == 0 and out.exists()
    assert tomllib.loads(out.read_text(encoding="utf-8"))["server"]["port"] == 8765
    again = _cli(home, "config", "init", "--out", str(out))
    assert again.returncode == 0 and "未覆盖" in again.stdout


def test_dr01_serve_uses_config_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import webbrowser

    import uvicorn

    home = tmp_path / "serve-home"
    monkeypatch.setenv("WFP_HOME", str(home))
    p = _write_config(tmp_path, "[server]\nport = 9131\nhost = \"127.0.0.1\"\n"
                                "log_level = \"warning\"\nopen_browser = true\n")
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)
        captured["app"] = app

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(webbrowser, "open", lambda url: captured.update(url=url))

    assert cli_main(["--config", str(p), "serve"]) == 0
    assert captured["port"] == 9131 and captured["host"] == "127.0.0.1"
    assert captured["log_level"] == "warning"
    assert captured["url"] == "http://127.0.0.1:9131/"
    app = captured["app"]
    assert app.state.wfp["port"] == 9131 and app.state.wfp["config"].port == 9131
    app.state.wfp["store"].close()


def test_dr01_cli_port_overrides_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)
        captured["app"] = app

    monkeypatch.setattr(uvicorn, "run", fake_run)
    home = tmp_path / "home2"
    monkeypatch.setenv("WFP_HOME", str(home))
    p = _write_config(tmp_path, "[server]\nport = 9132\n")
    assert cli_main(["--config", str(p), "serve", "--port", "9140"]) == 0
    assert captured["port"] == 9140
    app = captured["app"]
    assert app.state.wfp["port"] == 9140
    app.state.wfp["store"].close()


def test_dr01_api_config_endpoint_is_read_only_and_secret_free(env, tmp_path: Path) -> None:
    cfg = Config.default(env.paths)
    cfg.port = 9133
    cfg.retention_days = 3
    app = create_app(env.paths, config=cfg, token="cfg-token")
    headers = {"X-WFP-Token": "cfg-token", "Host": "127.0.0.1:9133"}
    try:
        with TestClient(app) as client:
            r = client.get("/api/config", headers=headers)
            assert r.status_code == 200
            data = r.json()
            assert data["port"] == 9133 and data["retention_days"] == 3
            assert "cfg-token" not in r.text and "ciphertext" not in r.text
            assert client.get("/api/health").json()["port"] == 9133
    finally:
        app.state.wfp["store"].close()


def test_dr67_config_feeds_runner_and_cleanup(env) -> None:
    cfg = Config.default(env.paths)
    cfg.log_quota_mb = 1
    cfg.default_timeout_seconds = 42
    cfg.retention_days = 2
    cfg.retention_max_bytes = 1024 ** 3
    with services(cfg) as svc:
        assert svc["runner"].log_quota == 1024 ** 2
        assert svc["runner"].default_timeout == 42
        assert svc["config"].retention_days == 2
        assert svc["config"].retention_max_bytes == 1024 ** 3


def test_dr06_doctor_reports_config_file_and_port(env) -> None:
    result = run_doctor(env.paths, port=9134, config_path=env.paths.config)
    assert result["port"] == 9134
    assert result["config_file"] == str(env.paths.config)
    assert "config" in {c["name"] for c in result["checks"]}
