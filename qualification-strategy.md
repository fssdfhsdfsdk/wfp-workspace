# WFP 合格性策略（Qualification Strategy）

> 对应过程动作："Generating a qualification strategy from the input requirements via the analysis and modelling information"
> 与 "Establishing the qualification relationship between derived requirements and the relevant qualification strategy"。
> 需求真源：`requirements-spec.md`。本文的 QC 矩阵由 `tools/gen_traceability.py` 自动生成，**勿手工编辑标记区块**。
> 版本 v1.0 ｜ 2026-11-15

---

## 1. 策略原则

| 原则 | 内容 |
| --- | --- |
| P-1 全覆盖 | 每条 DR 恰好绑定 1 条 QC（`QC-xx` 与 `DR-xx` 同号），无悬空、无遗漏（工具校验） |
| P-2 方法匹配风险 | 副作用/安全/保真类 → 必须自动化测试（T）；人因/文档类 → 演示（D）且需标注例外 |
| P-3 优先级决定严格度 | Must 不得仅以演示结案；Should 可 T 或 D；Could 可 D/I |
| P-4 门禁绑定 | 每条 QC 归属唯一门禁 G0…G5；门禁未过不得进入下一里程碑 |
| P-5 证据可复现 | 每条 QC 必须留下"命令 + 环境 + 种子 + 结果"，归档 `docs/gates/Gx/` |
| P-6 客户场景优先 | 四个 E2E 场景（IR-15…IR-18）是终验主线，任何门禁不得替代它们 |
| P-7 阻塞门禁 | **G3 的 QC-47（round-trip 无损）为客户指定阻塞项**（IR-21 / DR-47），失败禁止发布 |

## 2. 合格性方法定义

| 代码 | 方法 | 适用 | 证据形式 |
| --- | --- | --- | --- |
| **T** | 测试（单元/集成/属性/E2E/性能） | 可判定的行为、算法、契约、安全、性能 | JUnit XML + 覆盖率 + 日志/种子 |
| **A** | 分析（建模/计算/静态论证） | 容量、复杂度、成本、理论边界 | 分析文档 + 计算表 |
| **I** | 检查/评审（代码或文档审查） | 规范符合性、引用规范、不可测约定 | 评审记录 + 检查清单 + 工具输出 |
| **D** | 演示（人工操作走查） | 人因体验、上手成本、视觉呈现 | 录屏/截图 + 步骤脚本 + 签署 |

**测试层与标记**：`tests/unit`、`tests/integration`、`tests/property`、`tests/e2e`、`tests/perf`、`tests/remote`（`@remote` 可跳过，需真机）。
命名规范：文件 `test_dr<编号>_<slug>.py`，函数 `test_dr<编号>_<行为>`（DR-74）。

## 3. 门禁定义与出口条件

| 门禁 | 里程碑 | 出口条件（全部满足） | 阻塞项 | 证据目录 |
| --- | --- | --- | --- | --- |
| G0 | M0 | 归属 QC 全绿；`--check` 通过；安装/启动/安全/迁移可用 | QC-72（本机安全）、QC-11/17（契约） | `docs/gates/G0/` |
| G1 | M1 | 归属 QC 全绿；plan 零副作用探针通过；取消 10s 无残留；日志压测不丢 | QC-30、QC-35、QC-61 | `docs/gates/G1/` |
| G2 | M2 | 归属 QC 全绿；**E2E-01/02 在真机通过**；凭据 grep 零命中 | QC-53、QC-55、E2E-01 | `docs/gates/G2/` |
| **G3** | **M3** | 归属 QC 全绿；**≥200 语料 × 4 属性无损**；布局删除不改语义 | **QC-47（客户指定）**、QC-45 | `docs/gates/G3/` |
| G4 | M4 | 归属 QC 全绿；二次 apply 全跳过；补偿后哈希还原；崩溃续跑；性能达标 | QC-32、QC-34、QC-70 | `docs/gates/G4/` |
| G5 | M5 | 归属 QC 全绿；离线安装通过；30 分钟上手演示通过 | QC-73、QC-76 | `docs/gates/G5/` |

**降级规则**：仅当客户书面同意，方可对 G3 采取"图只读 + YAML 编辑"降级（见实施计划 R-01）；QC-47 本身不可豁免，只能缩小图编辑能力面后重跑。

## 4. 关键 QC 的测试设计要点

### QC-30 plan 零副作用（DR-30，最高风险）
- 探针：apply 前后对 workspace 与远端目录做 `路径→(mtime,size,sha256)` 快照；plan 执行后快照必须完全相等。
- 进程探针：`dry_run: none` 的动作在 plan 期间不得产生任何子进程（psutil 计数）。
- 反例注入：故意写一个"plan 里偷偷写文件"的动作，契约套件必须拒绝其发布（联动 QC-18）。

### QC-35 进程树取消（DR-35）
- 构造 `parent → child → grandchild` 三层进程，全部 sleep。
- Windows：Job Object 关闭后枚举进程快照断言三者消失；POSIX：killpg 同理。
- 时限断言：3s 内温和终止信号发出，10s 内全部消失；超时路径同样验证。

### QC-45 / QC-47 双视图与无损（DR-45/46/47，G3 阻塞）
- 语料 ≥200：注释（行首/行尾/块）、锚点别名、引号风格、CRLF/LF、中文/emoji、长行、空行、缩进变体、流式集合。
- 属性 1：`dump(load(t)) == t`；属性 2：随机图操作序列后非目标区域字节不变；属性 3：撤销 N 步回到原文；属性 4：投影不动点稳定。
- 失败输出最小反例（Hypothesis shrinking）并归档；反例进入回归语料库。

### QC-53 凭据脱敏（DR-53）
- 用含特殊字符（`"'$&<>%` 与中文）的密码执行远程动作。
- 断言：日志/事件/证据/审计/异常堆栈中，明文、URL 编码、base64 三种形态 grep 命中数均为 0；进程命令行不含密码。

### QC-32 / QC-34 幂等与补偿（DR-32/34）
- 幂等：同一工作项连续两次 apply，第二次可幂等步骤 100% `skipped_idempotent` 且零写操作；篡改一个目标文件后第三次仅修复该项。
- 补偿：在 E2E-01 第 8 步注入失败 → 补偿 → 本地与远端全部目标哈希回到 apply 前；不可逆步骤出现在人工清单。

### QC-70 性能（DR-70）
- 基准机固定（8C/16G/SSD，Windows 11）；每项指标 5 次取中位数；退化 >20% CI 告警。

## 5. 证据与归档规范

```
docs/gates/G3/
├─ junit.xml              # 测试结果（含 DR 编号的测试名）
├─ coverage.xml           # 覆盖率
├─ property-seeds.txt     # Hypothesis 种子与语料清单哈希
├─ counterexamples/       # 失败最小反例（若有）
├─ perf.json              # 性能指标（G4）
├─ demo-notes.md          # 演示步骤、截图/录屏链接、观察结论
└─ signoff.md             # 门禁签署：QA + 客户代表（G2/G3/G5 需客户参与）
```

## 6. 客户参与点

| 门禁 | 客户参与 | 形式 |
| --- | --- | --- |
| G2 | 必须 | 在客户靶机上跑 E2E-01/02，客户确认结果 |
| G3 | 必须 | 双视图保真演示：客户自带含注释的真实 YAML 做 round-trip |
| G5 | 必须 | 新客户 30 分钟上手演示（DR-76）+ 四场景终验 |

---

## 7. QC 全量矩阵（自动生成）

<!-- BEGIN GENERATED: qc_matrix -->
| DR | 需求标题 | 优先级 | 里程碑 | QC | 方法 | 门禁 | 证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DR-01 | 本地 Web 平台形态 | Must | M0 | QC-01 | T·集成 | G0 | `tests/integration/test_dr01_local_web.py` |
| DR-02 | 单机单用户模型 | Must | M0 | QC-02 | T·集成 | G0 | `tests/integration/test_dr02_single_user_lock.py` |
| DR-03 | 实现体查看/编辑窗口 | Must | M0 | QC-03 | T·E2E | G0 | `tests/e2e/test_dr03_impl_editor.py` |
| DR-04 | 全局安装与统一入口 | Must | M0 | QC-04 | T·E2E | G5 | `tests/e2e/test_dr04_global_install.py` |
| DR-05 | 数据目录与可迁移性 | Must | M0 | QC-05 | T·集成 | G0 | `tests/integration/test_dr05_data_home_export.py` |
| DR-06 | 环境自诊断 | Should | M0 | QC-06 | T·集成 | G0 | `tests/integration/test_dr06_doctor.py` |
| DR-07 | 前端离线分发 | Must | M0 | QC-07 | T·E2E | G0 | `tests/e2e/test_dr07_offline_ui.py` |
| DR-08 | 数据库版本与迁移 | Must | M0 | QC-08 | T·集成 | G0 | `tests/integration/test_dr08_migrations.py` |
| DR-09 | CLI 与 UI 能力等价 | Must | M1 | QC-09 | T·E2E | G1 | `tests/e2e/test_dr09_cli_parity.py` |
| DR-10 | 动作 = manifest + 实现体（版本化持久化） | Must | M0 | QC-10 | T·集成 | G0 | `tests/integration/test_dr10_action_versions.py` |
| DR-11 | manifest 规范（JSON Schema 2020-12） | Must | M0 | QC-11 | T·单元 | G0 | `tests/unit/test_dr11_manifest_schema.py` |
| DR-12 | runtime 适配器统一接口 | Must | M1 | QC-12 | T·集成 | G2 | `tests/integration/test_dr12_runtime_adapters.py` |
| DR-13 | 动作库 CRUD 与组织 | Must | M0 | QC-13 | T·集成 | G0 | `tests/integration/test_dr13_action_crud.py` |
| DR-14 | 参数契约校验（入库 + 运行前） | Must | M1 | QC-14 | T·单元 | G1 | `tests/unit/test_dr14_param_validation.py` |
| DR-15 | Python 依赖隔离 | Must | M1 | QC-15 | T·集成 | G1 | `tests/integration/test_dr15_venv_isolation.py` |
| DR-16 | 动作版本引用与固定 | Must | M1 | QC-16 | T·集成 | G1 | `tests/integration/test_dr16_version_pinning.py` |
| DR-17 | 能力声明强制 | Must | M0 | QC-17 | T·单元 | G0 | `tests/unit/test_dr17_capabilities_required.py` |
| DR-18 | 动作契约一致性测试套件 | Must | M1 | QC-18 | T·集成 | G1 | `tests/integration/test_dr18_contract_suite.py` |
| DR-19 | 内置动作集（覆盖 IR-02 八类） | Must | M1 | QC-19 | T·集成 | G2 | `tests/integration/test_dr19_builtin_actions.py` |
| DR-20 | 工作项声明式 Schema | Must | M1 | QC-20 | T·单元 | G1 | `tests/unit/test_dr20_pipeline_schema.py` |
| DR-21 | 分支与有界循环 | Must | M1 | QC-21 | T·集成 | G1 | `tests/integration/test_dr21_branch_loop.py` |
| DR-22 | 子工作项调用 | Should | M4 | QC-22 | T·集成 | G4 | `tests/integration/test_dr22_subpipeline.py` |
| DR-23 | 变量作用域与模板求值 | Must | M1 | QC-23 | T·单元 | G1 | `tests/unit/test_dr23_scope_template.py` |
| DR-24 | 路径语义与越界防护 | Must | M1 | QC-24 | T·集成 | G1 | `tests/integration/test_dr24_path_resolution.py` |
| DR-25 | 表达式安全求值 | Must | M1 | QC-25 | T·单元 | G1 | `tests/unit/test_dr25_safe_expr.py` |
| DR-26 | 步骤级错误策略与重试 | Must | M1 | QC-26 | T·集成 | G1 | `tests/integration/test_dr26_retry_on_error.py` |
| DR-27 | 工作项输入表单 | Must | M1 | QC-27 | T·集成 | G3 | `tests/integration/test_dr27_inputs_form.py` |
| DR-28 | 工作项 CRUD 与导入导出 | Must | M1 | QC-28 | T·集成 | G1 | `tests/integration/test_dr28_pipeline_crud.py` |
| DR-29 | 静态校验（lint） | Must | M1 | QC-29 | T·单元 | G1 | `tests/unit/test_dr29_lint_rules.py` |
| DR-30 | 三阶段执行 API | Must | M1 | QC-30 | T·集成 | G1 | `tests/integration/test_dr30_plan_zero_side_effect.py` |
| DR-31 | PlanReport 与 diff 预览 | Must | M1 | QC-31 | T·集成 | G1 | `tests/integration/test_dr31_plan_report.py` |
| DR-32 | 幂等判定与跳过 | Must | M4 | QC-32 | T·集成 | G4 | `tests/integration/test_dr32_idempotency.py` |
| DR-33 | apply 前置备份 | Must | M1 | QC-33 | T·集成 | G1 | `tests/integration/test_dr33_pre_backup.py` |
| DR-34 | 补偿（逆序回退） | Must | M4 | QC-34 | T·E2E | G4 | `tests/e2e/test_dr34_compensate.py` |
| DR-35 | 进程隔离与进程树取消 | Must | M1 | QC-35 | T·集成 | G1 | `tests/integration/test_dr35_process_tree_kill.py` |
| DR-36 | 无窗口执行与可选可见控制台 | Must | M1 | QC-36 | T·集成 | G1 | `tests/integration/test_dr36_console_mode.py` |
| DR-37 | 崩溃恢复与续跑 | Should | M4 | QC-37 | T·E2E | G4 | `tests/e2e/test_dr37_crash_resume.py` |
| DR-38 | 结构化事件与证据留存 | Must | M1 | QC-38 | T·集成 | G1 | `tests/integration/test_dr38_events_artifacts.py` |
| DR-39 | 并发互斥与目标节点串行 | Must | M1 | QC-39 | T·集成 | G1 | `tests/integration/test_dr39_mutex.py` |
| DR-40 | 页面信息架构 | Must | M0 | QC-40 | T·E2E | G3 | `tests/e2e/test_dr40_navigation.py` |
| DR-41 | 动作库页 | Must | M3 | QC-41 | T·E2E | G3 | `tests/e2e/test_dr41_action_library.py` |
| DR-42 | 工作项列表页 | Must | M1 | QC-42 | T·E2E | G3 | `tests/e2e/test_dr42_pipeline_list.py` |
| DR-43 | 编排页双栏布局 | Must | M3 | QC-43 | T·E2E | G3 | `tests/e2e/test_dr43_editor_layout.py` |
| DR-44 | 图编辑能力 | Must | M3 | QC-44 | T·E2E | G3 | `tests/e2e/test_dr44_graph_editing.py` |
| DR-45 | 双视图最小 patch 同步（客户指定编号） | Must | M3 | QC-45 | T·属性 | G3 | `tests/property/test_dr45_dual_view_sync.py` |
| DR-46 | 布局与语义分离 | Must | M3 | QC-46 | T·集成 | G3 | `tests/integration/test_dr46_layout_separation.py` |
| DR-47 | round-trip 无损门禁（M3 阻塞门禁） | Must | M3 | QC-47 | T·属性 | G3 | `tests/property/test_dr47_roundtrip_corpus.py` |
| DR-48 | manifest 驱动的参数表单 | Must | M3 | QC-48 | T·E2E | G3 | `tests/e2e/test_dr48_rjsf_forms.py` |
| DR-49 | 执行前干跑确认闸 | Must | M1 | QC-49 | T·集成 | G3 | `tests/integration/test_dr49_confirm_gate.py` |
| DR-50 | 节点管理 | Must | M2 | QC-50 | T·集成 | G2 | `tests/integration/test_dr50_nodes.py` |
| DR-51 | 当前目标节点与运行快照 | Must | M2 | QC-51 | T·集成 | G2 | `tests/integration/test_dr51_current_node_snapshot.py` |
| DR-52 | 凭据加密存储 | Must | M2 | QC-52 | T·集成 | G2 | `tests/integration/test_dr52_credential_encryption.py` |
| DR-53 | 凭据最小暴露与全链路脱敏 | Must | M2 | QC-53 | T·集成 | G2 | `tests/integration/test_dr53_secret_redaction.py` |
| DR-54 | 远程传输适配器 | Must | M2 | QC-54 | T·集成 | G2 | `tests/integration/test_dr54_transports.py` |
| DR-55 | 远程配置文件编辑（原子 + 可回滚） | Must | M2 | QC-55 | T·集成 | G2 | `tests/integration/test_dr55_remote_config_edit.py` |
| DR-56 | 远程目录与多文件上传/下载 | Must | M2 | QC-56 | T·集成 | G2 | `tests/integration/test_dr56_transfer.py` |
| DR-57 | 远程提权 | Should | M2 | QC-57 | T·集成 | G2 | `tests/integration/test_dr57_elevation.py` |
| DR-58 | 连接测试与超时治理 | Must | M2 | QC-58 | T·集成 | G2 | `tests/integration/test_dr58_connection_timeout.py` |
| DR-59 | 远端取消与残留清理 | Must | M2 | QC-59 | T·集成 | G2 | `tests/integration/test_dr59_remote_cancel.py` |
| DR-60 | 运行面板步骤树 | Must | M1 | QC-60 | T·E2E | G3 | `tests/e2e/test_dr60_step_tree.py` |
| DR-61 | 实时日志流 | Must | M1 | QC-61 | T·集成 | G1 | `tests/integration/test_dr61_log_stream.py` |
| DR-62 | 输出查看窗口 | Should | M3 | QC-62 | D·演示 | G3 | `docs/acceptance/QC-62-output-viewer.md` |
| DR-63 | 证据浏览 | Must | M1 | QC-63 | T·E2E | G3 | `tests/e2e/test_dr63_evidence.py` |
| DR-64 | 运行控制 | Must | M1 | QC-64 | T·集成 | G4 | `tests/integration/test_dr64_run_control.py` |
| DR-65 | 运行历史与复现 | Must | M4 | QC-65 | T·集成 | G4 | `tests/integration/test_dr65_history_replay.py` |
| DR-66 | 报表与导出 | Should | M4 | QC-66 | T·集成 | G4 | `tests/integration/test_dr66_reports.py` |
| DR-67 | 设置页 | Must | M4 | QC-67 | T·集成 | G4 | `tests/integration/test_dr67_settings.py` |
| DR-68 | 审计日志（只追加） | Must | M2 | QC-68 | T·集成 | G4 | `tests/integration/test_dr68_audit.py` |
| DR-69 | 保留与清理 | Should | M4 | QC-69 | T·集成 | G4 | `tests/integration/test_dr69_retention.py` |
| DR-70 | 性能指标 | Must | M4 | QC-70 | T·性能 | G4 | `tests/perf/test_dr70_performance.py` |
| DR-71 | 可靠性与故障隔离 | Must | M1 | QC-71 | T·集成 | G1 | `tests/integration/test_dr71_reliability.py` |
| DR-72 | 本机安全边界 | Must | M0 | QC-72 | T·集成 | G0 | `tests/integration/test_dr72_local_security.py` |
| DR-73 | 离线与可移植 | Must | M5 | QC-73 | T·E2E | G5 | `tests/e2e/test_dr73_offline_install.py` |
| DR-74 | 可维护性与 DR 引用规范 | Must | M0 | QC-74 | I·检查 | G0 | `tools/gen_traceability.py` |
| DR-75 | 可测试性设计 | Must | M1 | QC-75 | T·集成 | G1 | `tests/integration/test_dr75_testability.py` |
| DR-76 | 文档与内置帮助 | Must | M5 | QC-76 | D·演示（人因例外） | G5 | `docs/acceptance/QC-76-onboarding.md` |
| DR-77 | 资源配额与保护 | Should | M4 | QC-77 | T·集成 | G4 | `tests/integration/test_dr77_quotas.py` |
| DR-78 | 可观测与诊断包 | Should | M4 | QC-78 | T·集成 | G4 | `tests/integration/test_dr78_diagnostics.py` |
| DR-79 | 数据完整性与校验 | Should | M4 | QC-79 | T·集成 | G4 | `tests/integration/test_dr79_integrity.py` |
| DR-80 | 自然语言生成工作项草案 | Should | M5 | QC-80 | D·演示 | G5 | `docs/acceptance/QC-80-ai-draft.md` |
| DR-81 | 双闸校验与自动修正 | Should | M5 | QC-81 | T·集成 | G5 | `tests/integration/test_dr81_ai_validation.py` |
| DR-82 | 草案不可直接执行 | Should | M5 | QC-82 | T·集成 | G5 | `tests/integration/test_dr82_ai_draft_gate.py` |
| DR-83 | 可插拔、可关闭、可脱敏 | Should | M5 | QC-83 | T·集成 | G5 | `tests/integration/test_dr83_ai_privacy.py` |
| DR-84 | WinRM 传输适配器（Windows 目标，可选） | Should | M5 | QC-84 | T·集成 | G5 | `tests/integration/test_dr84_winrm_transport.py` |
<!-- END GENERATED: qc_matrix -->

## 8. 覆盖统计（自动生成）

<!-- BEGIN GENERATED: qc_stats -->
- 输入需求 IR：**25** 条，覆盖率 **25/25**
- 派生需求 DR：**84** 条（编号 DR-01…DR-84）
- 合格性用例 QC：**84** 条（1:1 绑定，无悬空）
- 优先级分布：Must **69** / Should **15** / Could **0**
- 合格性方法分布：T(测试) **80** / I(检查/评审) **1** / D(演示) **3**

| 里程碑 | DR 数 | 门禁 | DR 清单 |
| --- | --- | --- | --- |
| M0 | 15 | G0 | DR-01, DR-02, DR-03, DR-04, DR-05, DR-06, DR-07, DR-08, DR-10, DR-11, DR-13, DR-17, DR-40, DR-72, DR-74 |
| M1 | 31 | G1 | DR-09, DR-12, DR-14, DR-15, DR-16, DR-18, DR-19, DR-20, DR-21, DR-23, DR-24, DR-25, DR-26, DR-27, DR-28, DR-29, DR-30, DR-31, DR-33, DR-35, DR-36, DR-38, DR-39, DR-42, DR-49, DR-60, DR-61, DR-63, DR-64, DR-71, DR-75 |
| M2 | 11 | G2 | DR-50, DR-51, DR-52, DR-53, DR-54, DR-55, DR-56, DR-57, DR-58, DR-59, DR-68 |
| M3 | 8 | G3 | DR-41, DR-43, DR-44, DR-45, DR-46, DR-47, DR-48, DR-62 |
| M4 | 12 | G4 | DR-22, DR-32, DR-34, DR-37, DR-65, DR-66, DR-67, DR-69, DR-70, DR-77, DR-78, DR-79 |
| M5 | 7 | G5 | DR-73, DR-76, DR-80, DR-81, DR-82, DR-83, DR-84 |
<!-- END GENERATED: qc_stats -->
