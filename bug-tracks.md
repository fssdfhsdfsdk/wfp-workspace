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
