# WFP 开发 Bug 追踪

> 每个开发过程中的 bug 按固定字段记录，编号 `BUG-NNN` 递增追加。
> 字段：描述 / 背景与上下文 / 分析过程 / 根因 / 解决方法 / 时间。

## BUG-001 · `POST /api/pipelines/{id}/patch` 返回 500（ruamel ParserError）

描述：
编排视图拖入动作时 `POST /api/pipelines/test/patch` 返回 500，日志为
`ruamel.yaml.parser.ParserError: while parsing a block collection ... expected <block end>, but found '?'`；
紧接着 `GET /api/pipelines/test/yaml` 返回 200，说明真源未被写坏。

背景与上下文：
Windows 本地运行（`.venv` + uv）。工作项 `test` 由视图拖拽草稿 `POST /api/pipelines/draft` 创建，
其 YAML 由 `safe_dump` 生成，`steps` 序列的 dash 顶格：

```yaml
steps:
- id: s1
  uses: archive.pack@1.0.0
  with: {}
```

分析过程：
1. 堆栈定位：`app.py:pipeline_patch` → `PipelineStore.save_yaml` → `validate_pipeline(safe_load(text))` → `YAML(typ="safe").load`，异常在解析而非写入阶段，故原件完好。
2. 最小复现：对顶格序列文本执行 `YamlDoc.apply_delta(GraphDelta(kind="insert_step", index=0, ...))`，dump 结果为

```yaml
steps:
  - id: s3jhxf
  uses: archive.pack@1.0.0
  with: {}
```

`uses` 与 dash 同为 2 空格缩进，被解析器当成显式键 `?`，YAML 非法。
3. `detect_indents(text)` 对该文本返回 `(mapping=2, sequence=2, offset=2)`；
   `make_rt()` 用它调用 `y.indent(mapping=2, sequence=2, offset=2)`，ruamel 在 `sequence <= offset + 2`
   的组合下把序列项后续键排到与 dash 同一列。
4. 触发源是 `safe_dump` 未显式设置缩进（默认顶格序列），与 round-trip 的缩进约定不一致；
   放大原因是 `YamlDoc.dump()` 没有自检，ParserError 直接冒泡成 500。

根因：
`detect_indents()` 把 `offset` 固定为 2，未从原文 dash 缩进推断；对顶格序列（dash 缩进 0）
得到 `sequence=2, offset=2`，导致 round-trip dump 产出非法缩进。同时 `safe_dump` 与
`make_rt` 缩进风格不统一，`dump` 缺少解析自检与错误转换。

解决方法：
1. `wfp/contracts/yamlio.py::detect_indents`：取所有 `- ` 序列行中最浅层的一项，
   `offset = dash 前导空格数`（顶格 0 / 标准 2 / 宽缩进 4），`sequence = 首个 key 的列号`，
   于是顶格序列得到 `(2, 2, 0)`、标准序列得到 `(2, 4, 2)`，两种原文都能按原风格合法 dump。
2. `wfp/contracts/yamlio.py::safe_dump`：显式 `y.indent(mapping=2, sequence=4, offset=2)`，
   让新建工作项/草案与 round-trip 缩进约定一致，从源头消除不一致。
3. `wfp/sync/patch.py::YamlDoc.dump`：dump 后用 `safe_load` 自检；失败则回退到标准缩进重新 dump，
   仍失败抛 `YamlParseError`（ContractError → 4xx），杜绝再次出现 500。
4. 回归用例：`tests/integration/test_dr44_drag_params.py::test_bug001_patch_on_top_level_sequence_produces_parseable_yaml`，
   覆盖顶格序列 patch 与 draft 新格式两种路径。

时间：
2026-11-15

## BUG-002 · 同一批 patch 的第二个 delta 误判冲突（DR-45）

描述：
表单一次提交多个字段 / 画布一次删除多个节点时，`POST /api/pipelines/{id}/patch` 对第二个 delta
抛出 `Conflict`，提示"YAML 真源已被其他视图修改"。

背景与上下文：
`pipeline_patch` 用请求里的 `sha256` 作为 `expected_sha` 校验每一个 `GraphDelta`；
`YamlDoc.apply_delta` 每应用一个 delta 都会重算 `text_sha`。

分析过程：
单 delta 正常；两个 delta 时第一个已改变 `doc.text_sha`，第二个仍拿初始 sha 比较，必然不等。

根因：
批次内未推进 `expected_sha`，把"同一请求的连续编辑"误判成"并发修改"。

解决方法：
`wfp/api/app.py::pipeline_patch` 在循环内每应用一个 delta 后令 `expected = doc.text_sha`；
回归用例 `test_dr45_multi_delta_patch_updates_expected_sha_between_deltas`。

时间：
2026-11-15

## BUG-003 · 拖拽落点只影响布局，不改变步骤顺序（DR-44）

描述：
把动作拖到画布中间位置后，YAML 里新步骤仍追加在 `steps` 末尾，视觉位置与语义顺序不一致。

背景与上下文：
`Editor.insertStep` 把落点坐标写入 `pipeline.layout.json`，但 `insert_step` delta 只带 `new_step`；
后端 `graph_edit_to_patch` 默认 `idx = len(steps)`。

分析过程：
布局文件与语义真源分离（DR-46 设计如此），但前端没有把落点转换成 `steps` 序号，
后端虽已有 `after_step_id` 字段，前端未传且无法表达"插入到第 0 位"。

根因：
缺少"落点 → 插入序号"的翻译，`insert_step` 缺少显式 `index` 语义。

解决方法：
`GraphDelta` 增加 `index`，后端按 `index` 插入；前端 `dropIndex()` 依据 layout 中各节点 y 坐标
计算插入序号，草稿模式与已有工作项统一走该逻辑；回归用例见
`tests/integration/test_dr44_drag_params.py::test_dr48_set_with_and_indexed_insert`。

时间：
2026-11-15

## BUG-004 · Windows 跨盘符路径被误报"路径无法解析"（DR-24）

描述：
在动作参数里填 `C:\Users\Administrator\Downloads\my_dev` 时报错
`路径无法解析：C:\Users\Administrator\Downloads\my_dev`（WFP-L020），路径实际存在且合法。

背景与上下文：
Windows 本地运行，工作项位于 D 盘（如 `D:\myProject\wfp-workspace`），
用户填的是 C 盘绝对路径。DR-24 规定相对路径基于 workspace 根、默认禁止越界，
越界需 `meta.allow_outside_workspace: true` 显式开启。

分析过程：
1. 全局检索该文案，唯一定位 `wfp/engine/expr.py::resolve_local_path`。
2. 该函数用 `os.path.commonpath([str(base), str(target)])` 判断是否越界；
   Windows 上两个路径分属 C: 与 D: 时，标准库直接抛 `ValueError: Paths are on different drives`。
3. 该 `ValueError` 被捕获后包装成 `PathError("路径无法解析：...")`，
   把"越界"误报成"无法解析"，且没有给出任何可操作的解决办法。
4. 进一步检查发现视图侧没有配置 `meta.workdir` / `allow_outside_workspace` 的入口，
   用户只能手写 YAML，和"通过视图操作"的诉求冲突。

根因：
用 `commonpath` 做跨盘符比较本身会抛异常，异常分类错误；
同时缺少视图化的 workspace 根与越界开关配置。

解决方法：
1. `resolve_local_path` 改用 `Path.is_relative_to(base)` 判定，跨盘符/UNC 返回 False 即越界；
   任何 `ValueError/OSError` 也统一归为"路径越界"，消息里给出两种解决办法，不再出现"路径无法解析"。
2. 新增 `PUT /api/pipelines/{id}/meta`：以 round-trip 方式设置/清除
   `meta.workdir` 与 `meta.allow_outside_workspace`，产生新版本且保留注释。
3. `POST /api/pipelines/draft` 支持同两个字段；编排页新增「路径设置」弹窗与草稿表单的
   workspace 根输入 / 允许越界复选框。
4. 回归用例 `tests/integration/test_dr24_windows_paths.py`（4 个），
   含 monkeypatch 模拟 `commonpath` 抛 ValueError 的错误分类验证。

时间：
2026-11-15

## BUG-005 · 「运行」看起来只有干跑，第二阶段执行入口不醒目

描述：
点击工作项「运行」后只出现干跑/PlanReport，用户以为平台无法真正执行动作。
复现用例为 `archive.pack@1.0.0`（`src: C:\Users\...\my_dev`、`dest: C:\Users\...\workspace`，
`meta.allow_outside_workspace: true`）。

背景与上下文：
规格 DR-30/31/49 规定 `plan` 零副作用 → 人工确认闸 → `apply` 才真正执行，
UI 也已实现 `plan()` / `apply()` 两个函数与 `POST /api/runs/{run_id}/apply` 端点。

分析过程：
1. 用用户原 YAML（路径替换为临时目录）走完整 REST 链路：
   `POST /api/runs phase=plan` 返回 PlanReport 且磁盘无输出；
   `POST /api/runs/{run_id}/apply {"ack_risks": true}` 返回 `succeeded` 并生成 `workspace.zip`。
   说明后端执行能力正常，不是"无法运行"。
2. 检查 `Pipelines.tsx`：按钮文案为「干跑（零副作用）」，PlanReport 出现后
   「确认执行」位于可滚动 Modal 底部；`confirm_required && !ack` 时按钮仍可点，只弹 toast 后 return。
3. 因此用户容易停在第一步，或点了按钮却没反应，形成"只有干跑"的观感。

根因：
两阶段流程缺少显式步骤编号，第二阶段确认区不常驻可见；
未勾选确认时按钮未禁用，反馈只有一条容易被忽略的 toast。属交互缺陷，非执行缺陷。

解决方法：
1. 输入表单增加"运行分两步"提示，提交按钮改为「① 干跑预览（零副作用）」；
   toast 明确写出"仅点干跑不会改变系统状态，需点 ②"。
2. PlanReport 后新增 sticky `.run-footer`：显示「② 确认执行」说明，
   按钮改为「② 确认执行（真正运行）」；`confirm_required && !ack` 时按钮 disabled 并显示原因。
3. 回归用例 `tests/integration/test_run_two_step_flow.py`：验证干跑零副作用、
   确认执行真正落盘，以及 bundle 含两阶段文案。
4. 另给出 CLI 一步到位方式 `uv run wfp run <id> --yes`（仍经过确认闸）。
5. 后续按用户要求进一步拆成「直接运行」「干跑」两个独立入口：
   新增 `POST /api/runs/direct`（服务端内部 plan → apply，强制 `ack_risks`），
   「干跑」按钮只生成 PlanReport、不再提供执行按钮。

时间：
2026-11-15

## BUG-006 · 直接运行压缩文件夹后「持续未完成」、SSE 无事件、控制台无限轮询

描述：
点「直接运行」压缩文件夹后，运行面板一直显示"等待事件…（SSE，Last-Event-ID 续传）"，
控制台每 1.5 秒刷一条 `GET /api/runs/r-... 200 OK`，用户判断任务持续未完成。

背景与上下文：
Windows 本地运行，`archive.pack@1.0.0` 打包目录；`POST /api/runs/direct` 后已跳转到运行面板
（说明 `/api/tasks/{id}` 轮询已结束，apply 任务其实返回了）。SSE 连接 200 但前端收不到任何事件。

分析过程：
1. 用 `tests/integration/test_run_two_step_flow.py` 验证直接运行链路：task 返回 `succeeded`、
   压缩包生成、`events.ndjson` 含 `run.finished` —— 后端执行本身已完成。
2. `wfp/api/app.py::run_events` 的 SSE 帧为
   `id: <seq>\nevent: <type>\ndata: <json>\n\n`；浏览器 `EventSource.onmessage` 只接收
   默认 `message` 事件，命名事件需 `addEventListener(type, ...)`，未注册即被丢弃，
   于是日志面板永远空白。
3. `frontend/src/pages/Runs.tsx` 的 `setInterval(load, 1500)` 没有终态判断，
   run 已 `succeeded` 仍持续轮询，控制台刷屏进一步造成"未完成"错觉。
4. 顺带排查 `wfp/actions/builtin.py::_apply_pack`：若 `dest` 位于 `src` 目录内，
   `shutil.make_archive` 会把正在写入的压缩包打进自身，文件无限膨胀、任务真正永不结束，
   是"压缩文件夹"场景的潜在硬卡死路径。

根因：
SSE 帧的事件类型分发与前端监听方式不匹配；前端轮询缺少终态退出；
打包输出落在源目录内缺少防护。三者叠加造成"任务一直没完成"的观感与风险。

解决方法：
1. 新增 `wfp/api/app.py::sse_frame()`：只发 `id:` + `data:`（事件类型已在 `data.type`），
   不再发 `event:`，`Last-Event-ID` 续传仍由 `id:` 保证。
2. `Runs.tsx`：终态（`finished_at` 或终态状态集合）后 `clearInterval`；
   标题显示「已完成 + 完成时间」/「进行中…」；日志行由只显示 `HH:MM:SS` 改为完整日期时间。
3. `_apply_pack`：输出路径若 `is_relative_to(src)` 则抛 `ContractError`
   「打包输出不能位于源目录内（自包含递归）」，动作记为 failed，不再无限打包。
4. 控制台日志增强：`wfp/cli.py::_uvicorn_log_config` 给 uvicorn 访问/错误日志加
   `%(asctime)s`（`%Y-%m-%d %H:%M:%S`），启动 print 也带时间戳。
5. 回归用例 `tests/integration/test_sse_console_log.py`（4 个）与
   `test_run_two_step_flow.py` 追加完成事件断言。

时间：
2026-11-15

## BUG-007 · 压缩任务显示成功却没有压缩包（幂等误判）

描述：
`archive.pack@1.0.0` 直接运行返回 `run.finished {"status":"succeeded","steps":1,"duration_ms":2}`，
但用户在 Windows 上找不到任何压缩包。事件流里第 2 条是
`step.skipped {"reason":"idempotent","key":"98aecb9b..."}`，说明步骤被跳过、零写操作。

背景与上下文：
工作项 YAML：`src: C:\Users\Administrator\Downloads\my_dev`、
`dest: C:\Users\Administrator\Downloads\workspace`、`meta.allow_outside_workspace: true`。
日志时间 2026-11-16 00:00:54；`duration_ms: 2` 与 `steps: 1` 都指向"根本没执行打包"。

分析过程：
1. `wfp/engine/phases.py::_execute` 在 `caps["idempotent"] == "yes"` 且 `probe()` 为真时
   记 `skipped_idempotent` 并 `continue`；聚合后 run 仍是 `succeeded`，所以 UI 显示成功。
2. `wfp/actions/builtin.py::_plan_pack` 返回
   `desired_state={"path": str(dest), "exists": True}`；但 `shutil.make_archive` 的实际输出是
   `dest` 补后缀后的 `workspace.zip`，而不是 `workspace` 本身。
3. `wfp/engine/idempotency.py::probe` 对 `exists` 只做 `Path(desired["path"]).exists()`。
   `C:\Users\Administrator\Downloads\workspace` 作为目录本来就存在，于是一次次判定
   "期望态已满足" → 永远跳过，压缩包从未生成。
4. 规格 DR-32 的二次 apply 跳过清单是 `config.edit / fs.copy / archive.extract / net.upload`，
   **不含 archive.pack**；且打包内容随源目录变化，用"目标存在"做幂等本就不成立。

根因：
plan 的期望态指向压缩包**基名**而非真实输出文件，配合宽松的 `exists` 探测，
对不该幂等跳过的 `archive.pack` 误判为已完成。

解决方法：
1. 新增 `_pack_output()`：统一计算真实输出路径（去掉 `.zip`/`.tar.gz` 后缀再按 format 补回），
   plan 与 apply 共用，消除两处路径语义不一致。
2. `_plan_pack`：`targets` / `backup_targets` 指向真实输出文件；
   `desired_state={"idempotent": False, "path": str(out)}` 显式禁用幂等跳过，
   每次运行都重新打包并覆盖输出（符合 DR-32 跳过清单）。
3. `_apply_pack` 复用 helper，保留上一轮加的"输出落在源目录内"自包含递归防护。
4. 回归用例 `tests/integration/test_pack_idempotency.py`：模拟 `dest` 同名目录已存在，
   验证干跑零副作用且 targets 指向 `workspace.zip`，两次直接运行均真正生成可打开的 zip。

时间：
2026-11-16

## BUG-008 · 压缩包落在 dest 的上一层、包内少一层目录、日志出现重复空 step.started

描述：
`archive.pack` 的 `dest` 填 `C:\Users\Administrator\Downloads\workspace`（该目录已存在），
日志显示目标为 `C:\Users\Administrator\Downloads\workspace.zip`，压缩包出现在 Downloads
而不是 workspace 目录内；解压后内容也少了 `my_dev/` 这一层。另外日志里有第二条
`step.started`，`runtime / 参数 / 目标 / 工作目录` 全是 `-`。

背景与上下文：
Windows，日志 2026-11-16 00:22:29；`run.started` 工作目录是数据目录
`D:\myProject\wfp-workspace\.wfp-home\pipelines\test\1.0.5`，src/dest 均为 C 盘绝对路径。

分析过程：
1. `wfp/actions/builtin.py::_pack_output` 把 `dest` 一律当"压缩包基名"补后缀，
   完全没有处理"dest 是已存在目录"的情况，于是输出落在 dest 的父目录（上一层）。
2. `shutil.make_archive(base, fmt, src)` 用 `src` 作 root_dir，压缩包内是源目录的**内容**，
   不含 `my_dev/` 顶层目录，用户解压后感觉"在上一层级"。
3. `wfp/engine/phases.py::_run_with_retry` 也 emit `step.started`（payload 仅 attempt/action），
   与 `_execute` 新增的完整 `step.started` 重名，前端按完整字段格式化时第二条全显示 `-`。

根因：
`dest` 语义单一且不符合"压缩文件夹"直觉；压缩包内根层级缺失；事件名复用造成日志混淆。

解决方法：
1. `dest` 三分支语义：已存在目录 → 输出 `<dest>/<src.name>.zip`；以 `.zip`/`.tar.gz` 结尾
   → 直接作为压缩包文件（后缀决定 format）；其余 → 基名补后缀（兼容旧行为）。
2. `archive.pack` manifest 新增 `include_root`（默认 true），
   `make_archive(root_dir=src.parent, base_dir=src.name)` 保留顶层目录；显式 `false` 可恢复旧行为。
3. `_plan_pack` 的 `info` 带 `output` / `archive_root`，`step.started` payload 带上 `info`，
   前端日志行打印「输出 / 包内根」，可直接看到压缩包真实位置。
4. `_run_with_retry` 事件改名 `step.attempt`，前端单独渲染「第 N 次尝试」，消除重复空 `step.started`。
5. 回归 `tests/integration/test_pack_idempotency.py`：目录语义、显式 .zip、基名、
   `include_root: false`、日志 output/archive_root 与 `step.attempt`。
6. 老数据库里的内置 `archive.pack@1.0.0` manifest 没有 `include_root` 字段：
   `seed_builtin_actions` 启动时同步系统内置契约（仅内置动作、不动实现体字节），
   避免旧库上显式传 `include_root: false` 被 `validate_params` 拒绝；回归用例
   `test_builtin_contract_sync_upgrades_existing_manifest`。

时间：
2026-11-16

## BUG-009 · 日志流把脚本/远程命令 stdout 显示成 JSON 包裹且折叠换行

描述：
按 TDD 编写【工作项-复杂工作#01】11 步端到端用例时发现：后端已把 Python 脚本与远程命令的
stdout 写入 `step.stdout` 事件，但运行面板显示成 `{"text":"..."}` 且多行输出被压成一行，
不满足"日志流可以显示 python 脚本的输出、远程命令的输出"的验收。

背景与上下文：
用户要求的复杂工作项包含动作库中编辑加入的 Python 脚本、3 条远程命令、上传文件夹与 2 个文件，
并要求命令/脚本异常即失败。先写测试再跑（TDD），5 个用例首次运行 2 个失败。

分析过程：
1. `wfp/runtimes/script.py::_run_python/_run_shell` 与 `wfp/runtimes/remote.py::apply_exec`
   都调用 `ctx.emit_log(out, "stdout")`；NDJSON 中 `step.stdout.payload.text` 是纯文本，
   测试断言「含 python-script-ok / root / fake-ok: systemctl / fake-ok: curl 且无 b' 转义」通过
   → 后端无缺陷。
2. `frontend/src/pages/Runs.tsx::payloadLine` 对 `step.stdout` 走默认 `JSON.stringify(p)`，
   显示成 JSON 包裹；`frontend/src/styles.css` 的 `.log .line` 未设 `white-space`，换行被折叠。
3. 另两处是测试夹具写法问题而非产品缺陷：`command: false` 被 YAML 解析成布尔导致
   `validate_params` 拒绝（应为 `"false"`）；`readonly` 的命令3 在 plan 阶段会做只读探测（DR-30），
   `FakeTransport.exec_log` 会提前出现它，断言应改为「apply 阶段未生成 s11 步骤记录」。

根因：
日志渲染层缺少对 stdout/stderr 事件的专门格式化与多行样式；TDD 用例的两处断言/夹具不够精确。

解决方法：
1. `payloadLine` 对 `step.stdout` / `step.stderr` 直接返回 `p.text`；
   日志行加 `stdout-line` class，CSS `white-space: pre-wrap` + 等宽字体，保留脚本/命令原始换行。
2. 修正测试夹具：`command: "false"`，失败断言改为基于 `result["steps"]` 中是否出现 s11。
3. 新增 `tests/e2e/test_complex_work_01.py` 5 个用例：11 步正向全绿、远程命令异常失败、
   Python 脚本退出码 3 失败、配置缺失导致 assert 失败、日志视图源码断言；
   同时验证 `register` 作用域（`{{ py.marker }}` 写入远端 JSON）、上传文件夹与 2 个相对路径文件。

时间：
2026-11-16
