# WFP（Workflow Pipeline Platform）— 通用流水线平台

**状态**：M0–M4 可运行版本已落地（2026-11-15），前端按 `原始需求.md` 重建为 React + React Flow + Monaco + rjsf｜规格仍以 `requirements-spec.md` 为唯一真源
**运行形态**：Windows 本地运行（PowerShell，Python 3.11+）｜后端 + 本地离线 Web UI（默认回环 `127.0.0.1:8765` + 本机 token）｜单机单用户
**范围**：YAML 真源、动作库、plan/apply/compensate、远程 SSH/FakeTransport、双视图最小 patch、审计与报表

---

## 1. 五分钟上手（Windows / PowerShell）

> 以下命令在仓库根目录的 **PowerShell**（Windows PowerShell 5.1 或 PowerShell 7）中执行，Python ≥ 3.11。
> 本地运行提供两种隔离环境，**二选一**：`uv`（推荐）或标准库 `venv` + `pip`；二者都在项目内生成 `.venv\`，不污染全局 Python。
> Windows PowerShell 5.1 不支持 `&&`，示例一律分行书写。

**环境准备 A：uv（推荐，自动管理 Python 与依赖）**

```powershell
winget install --id=astral-sh.uv -e          # 或 irm https://astral.dev/uv/install.ps1 | iex
uv python install 3.11                       # uv 自带 Python，无需系统预装
uv sync --extra dev --extra remote           # 创建 .venv 并按 pyproject.toml 可编辑安装 wfp
```

- 仓库已随附 `uv.lock`（`uv lock` 可刷新），`uv sync` 按锁文件安装；`uv run <命令>` 会自动同步并在 `.venv` 内执行，无需激活。
- 全局安装（DR-04）：`uv tool install .`，等价 `pipx install .`，均含离线前端产物。

**环境准备 B：标准库 venv + pip**

```powershell
# 如未安装系统 Python 3.11+：winget install --id=Python.Python.3.11 -e
py -3.11 -m venv .venv                       # 或 python -m venv .venv
.\.venv\Scripts\Activate.ps1                 # CMD：.\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev,remote]"
```

- 若 PowerShell 禁止运行脚本，先执行 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`（仅当前会话生效）；也可不激活，直接调用 `.\.venv\Scripts\wfp.exe`。
- 方案 B 复用下文所有命令时，去掉 `uv run ` 前缀即可（需先激活 `.venv`）。

完全离线的 Windows 环境仍可走 wheelhouse 安装（DR-73）：`.\scripts\install-offline.ps1 -Wheelhouse .\wheelhouse`，该脚本使用 `pip --no-index`，不访问外网；上述 uv / pip venv 路径用于在线本地开发与运行。

**启动**

```powershell
$env:WFP_HOME = Join-Path $PWD ".wfp-home"  # 可选：所有可变状态集中在单一数据目录
uv run wfp config init                       # 生成 <WFP_HOME>\config.toml 启动配置
uv run wfp config show                       # 查看当前生效配置
uv run wfp doctor                            # 环境自诊断（含配置文件与端口检查）
uv run wfp serve --open                      # 启动平台，控制台打印本机 token
```

Windows 运行提示：

- 默认仅监听 `127.0.0.1:8765`，无需管理员权限；`lan = true` 时需设置 `$env:WFP_PASSWORD` 并放行 Windows 防火墙。
- 端口被占用：改用 `uv run wfp serve --port 9000`，或 `netstat -ano | findstr :8765` 定位占用进程。
- 控制台中文乱码：`[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`。

CLI 等价入口（无需打开浏览器）：

```powershell
uv run wfp action list                                       # 16 个内置动作（随启动自动入库）
uv run wfp pipeline import examples/pipeline.remote-yaml-sample.yaml
uv run wfp node add prod 10.0.0.8 --user root
uv run wfp node use prod
uv run wfp plan deploy-sample --input env=prod --json       # 干跑：零副作用，输出 PlanReport
uv run wfp run deploy-sample --input env=prod --yes        # 执行：确认闸 + 前置备份 + journal
$runId = "<run_id>"                                         # 从 wfp run 输出或 wfp history 获取后替换
uv run wfp run resume $runId                                # 崩溃/失败后续跑
uv run wfp report $runId --fmt md                           # Markdown / HTML / CSV 报表
uv run wfp export --out wfp-backup.zip                      # 默认不含凭据
uv run wfp verify                                           # 完整性校验
uv run wfp diagnostics                                      # 一键诊断包
```

退出码契约（DR-09）：`0` 成功、`2` 参数/校验错、`3` 执行失败、`4` 已取消、`5` 需人工介入。

### 1.1 启动配置（`config.toml`）

模板见 `config.example.toml`；首次 `wfp serve` 也会在 `<数据目录>\config.toml` 自动生成。配置**不含凭据**，本机 token 仍存于 `session.json`（POSIX 0600 / Windows 仅当前用户 ACL）。

PowerShell 中环境变量按会话设置（用 `[Environment]::SetEnvironmentVariable(..., "User")` 可永久生效）：

```powershell
$env:WFP_HOME = "C:\WFP\data"                # 数据目录
$env:WFP_CONFIG = "C:\WFP\config.toml"      # 指定配置文件
$env:WFP_PASSWORD = "..."                   # 非回环监听时必需
```

```toml
[server]
port = 8765                 # 监听端口
host = "127.0.0.1"         # 默认仅回环；非回环需 lan = true + --password
lan = false
open_browser = false
log_level = "info"

[storage]
data_dir = ""              # 留空 = WFP_HOME / 系统默认；也可写绝对路径
db_file = ""               # 留空 = <data_dir>/wfp.db；可写文件名或绝对路径
retention_days = 30        # 证据与日志保留天数
retention_max_bytes = 5368709120
log_quota_mb = 200         # 单 run 日志上限

[engine]
default_timeout_seconds = 300

[features]
ai_enabled = false
current_node = ""          # 启动时的"当前目标节点"初值
```

查找与优先级：

- 配置文件：`--config <path>` / `$env:WFP_CONFIG` > `<WFP_HOME>\config.toml`（`wfp config init` 可生成）。
- 数据目录：`--home` > `$env:WFP_HOME` > 配置里的 `data_dir` > 系统默认（Windows `$env:LOCALAPPDATA\WFP`；POSIX `~/.local/share/WFP`）。
- 单项值：CLI 参数（如 `wfp serve --port 9000`）> 配置文件 > 默认值；`wfp config show --json` 可查看最终生效值，REST 侧对应 `GET /api/config`（不含 token）。
- `lan = true` 时必须提供 `--password` 或环境变量 `$env:WFP_PASSWORD`，否则拒绝启动（DR-72）。

### 1.2 前端工程（React + React Flow + Monaco + rjsf）

`原始需求.md` L87-88 明确要求该技术栈且"前端需要功能完善"，因此前端已按此重建，不再是原生 JS 骨架。

运行平台**不需要** Node（离线产物已随包分发）；仅重建前端时需要 Node.js 22：

```powershell
winget install --id=OpenJS.NodeJS.LTS -e   # 可选，仅重建前端需要
cd frontend
npm ci                                    # 依赖锁定于 frontend/package-lock.json
npm run build                             # 产物写入 wfp/web/dist，随 Python 包离线分发
cd ..
```

- `frontend/src/App.tsx`：7 页 hash 路由、顶栏当前节点、AI 草案入口、快捷键、Toast。
- `frontend/src/pages/Actions.tsx`：分类/搜索、CRUD、启停/复制、Monaco 实现体编辑、版本 diff、契约套件、单动作试跑。
- `frontend/src/pages/Pipelines.tsx`：rjsf inputs 表单、干跑 → PlanReport（diff/风险/不可补偿）→ 确认闸 → 执行、复制/导出/删除二次确认。
- `frontend/src/pages/Editor.tsx`：React Flow 图（拖拽插入、连线改序、多选删除、dagre 自动布局、when/loop 容器、撤销重做）⇄ Monaco YAML（300ms 防抖、lint 标记点击跳转）双栏；rjsf 参数表单只产出 `steps[i]` 最小 patch；布局写 `pipeline.layout.json`。
- `frontend/src/StepParamsModal.tsx`：点击画布动作节点即打开参数编辑器，展示**可用参数列表**（manifest.params）、**修改默认参数**、增删**任意参数**（值支持 JSON），保存只替换 `steps[i].with`（DR-14/27/48）。
- 工作项页「拖拽新建」→ 编排页无 `id` 时进入草稿模式：拖入动作按落点决定步骤顺序、连线/拖动改序、点击节点编辑参数，点「保存工作项」由后端 `POST /api/pipelines/draft` 生成并校验 YAML 真源，再写回布局。
- `frontend/src/pages/Runs.tsx`：步骤树（循环/attempt）、SSE 日志（级别/关键字/步骤过滤、跟随暂停、ANSI、NDJSON 下载）、输出查看窗口、证据 diff/zip、取消/暂停/续跑/补偿/单步重试、md/html/csv 报表。
- `frontend/src/pages/Nodes.tsx` / `History.tsx` / `Settings.tsx` / `AIDraft.tsx`：节点与凭据 CRUD、历史筛选与"相同输入重跑"、设置与备份恢复、AI 四道闸。

Vite 构建产物在 `wfp/web/dist/`，由 FastAPI `StaticFiles` 托管 `/assets/*`，**运行期不请求任何 CDN**（DR-07）。`npm run build` 还会生成兼容入口 `/app.js`、`/style.css`。

### 1.3 Windows 路径与 workspace（DR-24）

相对路径基于 workspace 根（默认工作项 YAML 所在目录）。填 `C:\Users\Administrator\Downloads\my_dev` 这类绝对路径时，若工作项在 D 盘，会因**跨盘符被判定越界**（旧版本误报「路径无法解析」，见 `bug-tracks.md` BUG-004）。两种做法：

- 编排页「路径设置」→ 把 **workspace 根**设为 `C:/Users/Administrator/Downloads/my_dev`，相对路径即基于它；
- 或勾选「允许访问 workspace 之外的绝对路径」（写入 `meta.allow_outside_workspace: true`）。

拖拽新建工作项时，草稿表单同样提供这两项。YAML 里写 Windows 路径建议用正斜杠 `C:/Users/...` 或单引号，避免双引号标量中的 `\U` 被当成转义。

## 2. 已实现的工程能力

| 层 | 交付内容 | 主要 DR |
| --- | --- | --- |
| 契约 | `schemas/*.schema.json`、manifest/pipeline 校验、16 个内置动作契约、能力徽章、契约一致性套件（8 项） | 10,11,14,17,18,19,48 |
| 存储 | SQLite WAL + 单写队列 + 前向迁移/自动备份、只追加审计触发器、内容寻址实现体、工作项版本不可变 | 08,10,16,28,68,71,79 |
| 引擎 | 受限 AST 表达式 + Jinja 风格模板沙箱、作用域链、路径越界防护、lint（≥12 规则）、顺序/分支/有界循环/子调用、重试与错误策略 | 20–29 |
| 三阶段 | `plan` 只读零副作用 + PlanReport/diff、`apply` 确认闸/前置备份/幂等键、`compensate` 逆序还原 + 人工清单、崩溃续跑 | 30–34,37,49 |
| 动作 | archive / fs / config(json·yaml·ini) / net / assert / script / remote 八类；python·powershell·cmd 子进程（进程树 kill、stdin 关闭、venv 指纹） | 12,15,19,35,36 |
| 远程 | `Transport` 抽象、paramiko SSH、离线 `FakeTransport`（12 类故障注入）、节点/当前节点快照、凭据加密（DPAPI/keyring/AES-GCM/标准库兜底）、全链路脱敏、原子替换 + hash 校验 | 50–59,75,84 |
| 双视图 | `ruamel.yaml` round-trip、只触及 `steps[i]` 的最小 patch、冲突检测/撤销/回声抑制、布局文件与语义分离 | 43–48 |
| 平台 | FastAPI REST + SSE（Last-Event-ID 续传）、token/Origin/Host 校验、NDJSON 事件 + artifacts、历史/报表/设置/保留/诊断包、可选 AI 草案四道闸 | 01–07,60–69,72,77,78,80–83 |
| 前端 | React + React Flow + Monaco + rjsf + dagre，Vite 离线构建；7 页完整交互、拖拽编排、双视图最小 patch、SSE 日志/证据/报表 | 40–48,60–63,67,80–83 |

四个客户真实场景 `E2E-01…04` 全部由离线集成测试覆盖（`tests/e2e/test_e2e_scenarios.py`）：
部署流水线（10 步）、远端 YAML 注释保真、下载文件、上传文件（二次 hash 幂等跳过）。

## 3. 验证

在 Windows PowerShell 中（uv 方案，无需激活 `.venv`）：

```powershell
uv run python -m pytest                              # 单元 / 集成 / 属性 / E2E / 性能；真实靶机用例默认跳过
uv run python -m pytest -m remote                    # 需要真实 SSH / WinRM 靶机时显式执行
uv run ruff check wfp tools tests                    # DR-74 静态门禁
uv run mypy wfp                                      # DR-74 静态门禁
cd frontend
npm ci
npm run build                                        # 重建离线前端产物（CI 在 Ubuntu 上亦执行）
cd ..
uv run python tools/gen_traceability.py --check      # IR→DR→QC 追溯矩阵校验
uv run python tools/gen_traceability.py --check-code # 代码/测试对 84 条 DR 的引用覆盖
uv run python tools/validate_examples.py --strict    # 示例夹具契约校验
```

pip venv 方案：先 `.\.venv\Scripts\Activate.ps1`，再去掉上面每条命令的 `uv run ` 前缀执行即可。

G3 阻塞门禁：864 份 YAML 语料（注释/锚点/别名/引号/CRLF/中文/长行/空行/两种缩进）做字节级 round-trip 属性测试，随机图操作后非目标区域字节不变、撤销 20 步回到原文（`tests/property/`）。

## 4. 目录

```
wfp/
├─ config.py              启动配置（TOML）解析、校验、生成与优先级
├─ cli.py / api/          CLI 命令族、REST + SSE、离线 SPA 静态托管
├─ contracts/             动作与工作项契约、lint、YAML 读写
├─ engine/                表达式、状态机三阶段、journal/证据、取消、幂等
├─ actions/               内置动作实现、契约种子、8 项契约套件
├─ runtimes/              builtin / python / powershell / cmd / remote 适配器
├─ transport/             SSH + FakeTransport + WinRM 可选适配点
├─ store/                 SQLite、迁移、工作项/运行/节点/内存替身
├─ security/              凭据加密、SecretStr、Redactor
├─ sync/                  round-trip 最小 patch、布局分离
└─ web/dist/              React/Vite 离线构建产物（index.html + assets/，无 CDN）
frontend/                   React + Vite + TS 前端源码（npm run build → wfp/web/dist）
tests/{unit,integration,property,e2e,perf,remote}/   tools/   schemas/   examples/
config.example.toml        启动配置模板（本地 config.toml 不入库）
.venv/                      uv sync / py -m venv 生成的本地虚拟环境（不入库）
uv.lock                     uv 解析的可复现锁文件（uv lock 刷新，建议入库）
frontend/package-lock.json  npm ci 的前端依赖锁定
```

## 5. 需求与过程文档

| 文件 | 说明 |
| --- | --- |
| `requirements-spec.md` | 需求唯一真源：25 IR、18 RA、8 MD、84 DR、4 个 E2E、变更控制 |
| `architecture-design.md` | 12 条 ADR、组件/进程/数据/API/安全/性能设计 |
| `implementation-plan.md` | M0–M5 WBS、G0–G5 门禁、风险与评审 |
| `qualification-strategy.md` | 84 条 QC 用例与证据归档规范 |
| `traceability.md` / `.csv` | 自动生成的双追溯矩阵（勿手工编辑） |
| `bug-tracks.md` | 开发过程 bug 追踪（描述 / 背景与上下文 / 分析过程 / 根因 / 解决方法 / 时间） |

## 6. 已知限制（相对计划基线）

- Windows 开始菜单快捷方式、真实 WinRM 靶机适配（DR-84）、10 万行日志的 Web Worker 级虚拟滚动仍属后续增强；当前输出查看窗口以 20 万字符上限做保护（DR-77）。
- runner 在 API 进程内以受控线程执行；架构设计的独立 `wfp-runner` 进程 + Job Object/killpg 全量进程树语义在 POSIX 已覆盖，Windows Job Object 需 Windows runner 验收。
- AI 草案默认关闭，仅内置本地规则生成器；远端模型 provider 需显式配置。
- OQ-01…OQ-08 仍按 `requirements-spec.md` §10 的默认假设执行；客户签署后需重跑追溯校验。
