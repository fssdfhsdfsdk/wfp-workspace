"""wfp 全局 CLI：serve / doctor / action / pipeline / plan / run / node / 维护。

Implements: DR-01, DR-04, DR-05, DR-06, DR-09, DR-13, DR-28, DR-30, DR-31, DR-34,
DR-37, DR-41, DR-49, DR-64, DR-66, DR-69, DR-72, DR-73, DR-76
退出码契约（DR-09）：0 成功 / 2 参数或校验错 / 3 执行失败 / 4 已取消 / 5 需人工介入。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from wfp import __version__
from wfp.config import Config
from wfp.contracts.yamlio import safe_load
from wfp.doctor import run_doctor
from wfp.engine.phases import Runner
from wfp.errors import ConfirmRequired, ContractError, WfpError
from wfp.maintenance import diagnostics, export_all, import_all, verify
from wfp.reports import run_report
from wfp.security.credentials import CredentialService
from wfp.store.db import Store
from wfp.store.nodes import NodeStore
from wfp.store.pipelines import PipelineStore
from wfp.store.runs import RunStore


@contextmanager
def services(setting: str | Config | None = None) -> Iterator[dict[str, Any]]:
    """打开服务上下文；setting 可为数据目录或已解析的启动配置。"""
    cfg = setting if isinstance(setting, Config) else Config.resolve(home=setting)
    paths = cfg.require_paths().ensure()
    store = Store(paths.db)
    store.migrate()
    runner = Runner(paths, store, actor="cli", config=cfg)
    try:
        yield {"paths": paths, "store": store, "runner": runner, "config": cfg,
               "actions": runner.actions, "pipelines": PipelineStore(store, paths),
               "runs": RunStore(store, paths), "nodes": NodeStore(store, paths, runner.credentials),
               "credentials": runner.credentials}
    finally:
        store.close()


def cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """把 CLI 显式参数转成配置覆盖；None 表示"未指定，沿用配置文件"。"""
    out: dict[str, Any] = {}
    if args.cmd in ("serve", "doctor") and getattr(args, "port", None) is not None:
        out["port"] = args.port
    if args.cmd == "serve":
        for key in ("host", "log_level"):
            if getattr(args, key, None) is not None:
                out[key] = getattr(args, key)
        if getattr(args, "open", None):
            out["open_browser"] = True
        if getattr(args, "lan", None):
            out["lan"] = True
    if args.cmd == "cleanup" and getattr(args, "days", None) is not None:
        out["retention_days"] = args.days
    return out


def _inputs(args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if getattr(args, "inputs_file", None):
        text = Path(args.inputs_file).read_text(encoding="utf-8")
        out.update(json.loads(text) if text.lstrip().startswith("{") else safe_load(text))
    for item in getattr(args, "input", None) or []:
        if "=" not in item:
            raise ContractError(f"--input 需为 k=v：{item}")
        key, raw = item.split("=", 1)
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


def _print(data: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif isinstance(data, dict):
        for k, v in data.items():
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v}")
    else:
        print(data)


def _exit_for(status: str) -> int:
    return {"failed": 3, "plan_failed": 3, "cancelled": 4,
            "needs_attention": 5}.get(status, 0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wfp", description="WFP 通用流水线平台（YAML 真源 + 本地 Web + CLI）")
    p.add_argument("--home", help="数据目录（优先级最高：--home > WFP_HOME > config.toml data_dir）")
    p.add_argument("--config", help="启动配置文件（默认 <数据目录>/config.toml，可用 WFP_CONFIG 指定）")
    p.add_argument("--version", action="version", version=f"wfp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _config_arg(sp: argparse.ArgumentParser) -> None:
        # SUPPRESS：允许 `wfp serve --config x`，且不会覆盖 `wfp --config x serve`
        sp.add_argument("--config", default=argparse.SUPPRESS,
                        help="启动配置文件（同全局 --config）")

    serve = sub.add_parser("serve", help="启动本机 Web 平台")
    serve.add_argument("--port", type=int, default=None, help="覆盖 config.toml 的 server.port")
    serve.add_argument("--host", default=None, help="覆盖 server.host")
    serve.add_argument("--log-level", default=None,
                       choices=["critical", "error", "warning", "info", "debug", "trace"])
    serve.add_argument("--open", action="store_true", default=None)
    serve.add_argument("--lan", action="store_true", default=None)
    serve.add_argument("--password", help="lan=true 时必须设置访问口令（也可用 WFP_PASSWORD）")
    serve.add_argument("--token", help="固定本机 token（默认随机生成并保存）")
    _config_arg(serve)

    doctor = sub.add_parser("doctor", help="环境自诊断")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--verify", action="store_true")
    doctor.add_argument("--port", type=int, default=None, help="覆盖 server.port")
    _config_arg(doctor)

    cfg_cmd = sub.add_parser("config", help="启动配置（config.toml）")
    csp = cfg_cmd.add_subparsers(dest="op", required=True)
    cshow = csp.add_parser("show", help="打印生效配置")
    cshow.add_argument("--json", action="store_true")
    _config_arg(cshow)
    cinit = csp.add_parser("init", help="生成配置模板")
    cinit.add_argument("--out", help="输出路径（默认 <数据目录>/config.toml）")
    cinit.add_argument("--force", action="store_true", help="覆盖已有配置文件")
    _config_arg(cinit)

    action = sub.add_parser("action", help="动作库")
    asp = action.add_subparsers(dest="op", required=True)
    asp.add_parser("list").add_argument("--query", default="")
    show = asp.add_parser("show"); show.add_argument("id")
    add = asp.add_parser("add"); add.add_argument("manifest"); add.add_argument("--impl")
    test = asp.add_parser("test"); test.add_argument("id"); test.add_argument("--version")
    ver = asp.add_parser("versions"); ver.add_argument("id")
    diff = asp.add_parser("diff"); diff.add_argument("id"); diff.add_argument("from_version"); diff.add_argument("to_version")
    dup = asp.add_parser("duplicate"); dup.add_argument("id"); dup.add_argument("new_id")
    dele = asp.add_parser("delete"); dele.add_argument("id")

    pipe = sub.add_parser("pipeline", help="工作项")
    psp = pipe.add_subparsers(dest="op", required=True)
    psp.add_parser("list")
    imp = psp.add_parser("import"); imp.add_argument("path")
    sho = psp.add_parser("show"); sho.add_argument("id")
    lint = psp.add_parser("lint"); lint.add_argument("ref")
    yaml = psp.add_parser("yaml"); yaml.add_argument("id")
    exp = psp.add_parser("export"); exp.add_argument("id"); exp.add_argument("--out")
    dele = psp.add_parser("delete"); dele.add_argument("id")
    graph = psp.add_parser("graph"); graph.add_argument("id")

    for name, help_text in (("plan", "干跑（零副作用）"), ("run", "执行工作项")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("ref", nargs="?", help="工作项 id 或 YAML 路径")
        cmd.add_argument("--input", action="append", default=[])
        cmd.add_argument("--inputs-file")
        cmd.add_argument("--node")
        cmd.add_argument("--json", action="store_true")
        if name == "run":
            cmd.add_argument("--yes", action="store_true", help="跳过交互确认（仍写审计，DR-49）")
            cmd.add_argument("--confirm-plan", help="使用指定 plan run 的报告哈希做确认闸")

    node = sub.add_parser("node", help="远程节点")
    nsp = node.add_subparsers(dest="op", required=True)
    nsp.add_parser("list")
    nadd = nsp.add_parser("add"); nadd.add_argument("id"); nadd.add_argument("host")
    nadd.add_argument("--name"); nadd.add_argument("--user", default="root")
    nadd.add_argument("--port", type=int, default=22); nadd.add_argument("--os", default="ssh-linux")
    nadd.add_argument("--credential"); nadd.add_argument("--workdir", default="/")
    use = nsp.add_parser("use"); use.add_argument("id")
    probe = nsp.add_parser("probe"); probe.add_argument("id")
    ndel = nsp.add_parser("delete"); ndel.add_argument("id")

    cred = sub.add_parser("credential", help="凭据（永不返回明文）")
    csp = cred.add_subparsers(dest="op", required=True)
    csp.add_parser("list")
    cadd = csp.add_parser("add"); cadd.add_argument("label")
    cadd.add_argument("value", nargs="?", help="省略则从安全提示符读取（避免明文进命令行，DR-53）")
    cdel = csp.add_parser("delete"); cdel.add_argument("id")

    exp = sub.add_parser("export", help="导出全库（默认不含凭据）")
    exp.add_argument("--out", required=True); exp.add_argument("--with-credentials", action="store_true")
    imp = sub.add_parser("import", help="导入全库"); imp.add_argument("path")
    sub.add_parser("verify", help="完整性校验")
    rep = sub.add_parser("report", help="导出运行报表"); rep.add_argument("run_id")
    rep.add_argument("--fmt", choices=["md", "html", "csv"], default="md")
    rep.add_argument("--out")
    clean = sub.add_parser("cleanup", help="按保留策略清理")
    clean.add_argument("--days", type=int, default=None, help="覆盖 storage.retention_days")
    diag = sub.add_parser("diagnostics", help="一键诊断包"); diag.add_argument("--run")

    ai = sub.add_parser("ai", help="AI 草案（默认关闭）")
    ais = ai.add_subparsers(dest="op", required=True)
    toggle = ais.add_parser("toggle"); toggle.add_argument("value", choices=["on", "off"])
    draft = ais.add_parser("draft"); draft.add_argument("description")
    accept = ais.add_parser("accept"); accept.add_argument("id")
    return p


def _run_command(args: argparse.Namespace, svc: dict[str, Any], *, is_plan: bool) -> int:
    runner: Runner = svc["runner"]
    if not args.ref:
        raise ContractError("缺少工作项 ref")
    if args.node:
        runner.nodes.set_current(args.node, actor="cli")
    data = _inputs(args)
    if is_plan:
        report = runner.plan(args.ref, data, trigger="cli")
        _print(report if args.json else {
            "run_id": report["run_id"], "status": "planned",
            "steps": len(report["items"]), "confirm_required": report["confirm_required"],
            "sha256": report["sha256"], "risks": len(report["risks"])}, args.json)
        return 0
    confirm = None
    plan_report = None
    if args.confirm_plan:
        rec = svc["runs"].get(args.confirm_plan)
        if not rec.get("plan_report_path"):
            raise ContractError(f"run {args.confirm_plan} 不是有效的 plan 结果")
        plan_report = json.loads((svc["paths"].root / rec["plan_report_path"]).read_text(encoding="utf-8"))
        confirm = {"plan_sha": plan_report["sha256"], "ack_risks": True, "actor": "cli"}
    result = runner.apply(args.ref, data, confirm=confirm, yes=args.yes,
                          plan_report=plan_report)
    _print(result if args.json else {
        "run_id": result["id"], "status": result["status"], "phase": result["phase"],
        "error_code": result["error_code"], "steps": len(result["steps"])}, args.json)
    return _exit_for(result["status"])


def dispatch(args: argparse.Namespace, svc: dict[str, Any]) -> int:
    cmd = args.cmd
    cfg: Config = svc["config"]
    if cmd == "serve":
        password = args.password or os.environ.get("WFP_PASSWORD")
        if cfg.lan and not password:
            raise ContractError("lan=true 时必须设置 --password 或 WFP_PASSWORD（DR-72）")
        import uvicorn

        from wfp.api.app import create_app
        cfg.ensure_file()
        app = create_app(cfg.require_paths(), config=cfg,
                         token=args.token or (password if cfg.lan else None))
        url = f"http://127.0.0.1:{cfg.port}/"
        print(f"WFP 已启动：{url}  config={cfg.source}  data_dir={cfg.require_paths().root}")
        print(f"本机 token={app.state.wfp['token']}（也存于 session.json）")
        if cfg.open_browser:
            webbrowser.open(url)
        uvicorn.run(app, host="0.0.0.0" if cfg.lan else cfg.host, port=cfg.port,
                    log_level=cfg.log_level)
        return 0
    if cmd == "doctor":
        result = run_doctor(svc["paths"], store=svc["store"], verify=args.verify,
                            port=cfg.port, config_path=cfg.source)
        _print(result, args.json)
        if not args.json:
            for c in result["checks"]:
                print(f"  {'OK ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}"
                      + (f" → {c['suggestion']}" if c["suggestion"] else ""))
        return 0 if result["ok"] else 3
    if cmd == "action":
        reg = svc["actions"]
        if args.op == "list":
            _print(reg.list(query=args.query))
        elif args.op == "show":
            _print(reg.get(args.id))
        elif args.op == "add":
            manifest = safe_load(Path(args.manifest).read_text(encoding="utf-8"))
            impl = Path(args.impl).read_text(encoding="utf-8") if args.impl else ""
            _print(reg.create(manifest, impl, actor="cli"))
        elif args.op == "test":
            from wfp.actions.suite import run_contract_suite
            result = run_contract_suite(reg, args.id, args.version)
            _print(result)
            return 0 if result["ok"] else 3
        elif args.op == "versions":
            _print(reg.versions(args.id))
        elif args.op == "diff":
            print(reg.diff(args.id, args.from_version, args.to_version)["diff"])
        elif args.op == "duplicate":
            _print(reg.duplicate(args.id, args.new_id, actor="cli"))
        elif args.op == "delete":
            _print(reg.soft_delete(args.id, actor="cli"))
        return 0
    if cmd == "pipeline":
        ps: PipelineStore = svc["pipelines"]
        if args.op == "list":
            _print(ps.list())
        elif args.op == "import":
            if args.path.endswith((".yaml", ".yml")):
                text = Path(args.path).read_text(encoding="utf-8")
                _print(ps.create(text, actor="cli"))
            else:
                _print(ps.import_zip(Path(args.path), actor="cli"))
        elif args.op == "show":
            p = ps.get(args.id)
            _print({"id": args.id, "version": p.version, "sha256": p.sha256,
                    "steps": len(p.steps), "inputs": p.inputs_schema})
        elif args.op == "lint":
            from wfp.contracts.pipeline import lint_pipeline
            issues = lint_pipeline(svc["pipelines"].get(args.ref),
                                   action_exists=svc["runner"]._action_exists,
                                   current_node=svc["nodes"].current_id)
            _print([i.as_dict() for i in issues])
            return 0 if not any(i.level == "error" for i in issues) else 2
        elif args.op == "yaml":
            print(ps.yaml_text(args.id)[0])
        elif args.op == "export":
            _print({"path": str(ps.export_zip(args.id, Path(args.out or f"{args.id}.zip")))})
        elif args.op == "delete":
            _print(ps.delete(args.id, actor="cli"))
        elif args.op == "graph":
            from wfp.sync.patch import project_text
            text, sha = ps.yaml_text(args.id)
            model = project_text(text)
            _print({"sha256": sha, "nodes": model.nodes, "edges": model.edges})
        return 0
    if cmd == "plan":
        return _run_command(args, svc, is_plan=True)
    if cmd == "run":
        return _run_command(args, svc, is_plan=False)
    if cmd == "node":
        ns: NodeStore = svc["nodes"]
        if args.op == "list":
            _print(ns.list())
        elif args.op == "add":
            _print(ns.add({"id": args.id, "name": args.name or args.id, "host": args.host,
                           "port": args.port, "username": args.user, "os_kind": args.os,
                           "credential_id": args.credential, "default_workdir": args.workdir},
                          actor="cli"))
        elif args.op == "use":
            _print(ns.set_current(args.id, actor="cli"))
        elif args.op == "probe":
            _print(ns.probe(args.id))
        elif args.op == "delete":
            _print(ns.delete(args.id, actor="cli"))
        return 0
    if cmd == "credential":
        cs: CredentialService = svc["credentials"]
        if args.op == "list":
            _print(cs.list())
        elif args.op == "add":
            import getpass
            value = args.value if args.value is not None else getpass.getpass("凭据值（不回显）：")
            _print(cs.put(args.label, value, actor="cli"))
        elif args.op == "delete":
            cs.delete(args.id, actor="cli")
            print("deleted")
        return 0
    if cmd == "export":
        _print({"path": str(export_all(svc["paths"], svc["store"], Path(args.out),
                                       with_credentials=args.with_credentials))})
        return 0
    if cmd == "import":
        _print(import_all(svc["paths"], svc["store"], Path(args.path)))
        return 0
    if cmd == "verify":
        result = verify(svc["paths"], svc["store"])
        _print(result)
        return 0 if result["ok"] else 3
    if cmd == "report":
        text, _, name = run_report(svc["paths"], svc["runs"], args.run_id, fmt=args.fmt)
        dest = Path(args.out or name)
        dest.write_text(text, encoding="utf-8")
        print(dest)
        return 0
    if cmd == "cleanup":
        _print(svc["runs"].cleanup(days=cfg.retention_days,
                                   max_bytes=cfg.retention_max_bytes))
        return 0
    if cmd == "config":
        if args.op == "show":
            _print(cfg.public_dict(), args.json)
            if not args.json:
                print(f"config_file: {cfg.source}")
                print(f"data_dir: {cfg.require_paths().root}")
        elif args.op == "init":
            dest = Path(args.out) if args.out else (cfg.source or cfg.require_paths().config)
            existed = dest.exists()
            written = cfg.write(dest, force=args.force)
            print(f"配置模板：{written}" + ("（已存在，未覆盖；用 --force 覆盖）"
                                          if existed and not args.force else ""))
        return 0
    if cmd == "diagnostics":
        print(diagnostics(svc["paths"], svc["store"], args.run))
        return 0
    if cmd == "ai":
        from wfp.ai import AIDraftService
        service = AIDraftService(svc["store"], svc["runner"]._action_exists)
        if args.op == "toggle":
            service.set_enabled(args.value == "on")
            print(f"AI {'已启用' if service.enabled else '已关闭'}")
        elif args.op == "draft":
            _print(service.draft(args.description))
        elif args.op == "accept":
            _print(service.accept(args.id))
        return 0
    raise ContractError(f"未知命令：{cmd}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = Config.resolve(config_path=getattr(args, "config", None), home=args.home,
                             overrides=cli_overrides(args))
        with services(cfg) as svc:
            return dispatch(args, svc)
    except ConfirmRequired as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        print("请先执行 wfp plan <ref> 查看 PlanReport，再用 --yes 或 UI 勾选确认（DR-49）", file=sys.stderr)
        return 2
    except WfpError as exc:
        print(f"{exc.code}: {exc.message}" + (f" [{exc.path}]" if exc.path else ""), file=sys.stderr)
        return 2 if isinstance(exc, ContractError) or exc.http_status in (401, 403, 404, 409, 422) else 3
    except KeyboardInterrupt:
        return 4
    except Exception as exc:  # noqa: BLE001
        print(f"WFP-E-INTERNAL: {exc}", file=sys.stderr)
        return 3


__all__ = ["build_parser", "dispatch", "main", "services"]

if __name__ == "__main__":
    raise SystemExit(main())
