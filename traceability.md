# WFP 追溯矩阵（自动生成，勿手工编辑）

> 由 `python tools/gen_traceability.py` 从 `requirements-spec.md` 生成。
> 满足关系（IR→DR）与合格性关系（DR→QC）的唯一机器可读副本为 `traceability.csv`。

## 1. 满足关系：输入需求 → 派生需求

| 输入需求 | 客户输入（摘要） | 满足它的派生需求 | 数量 |
| --- | --- | --- | --- |
| IR-01 | 可视化管理平台：创建可复用基本动作 → 拖拽组合成自定义工作项（固定或带配置）→ 一键执行 | DR-01, DR-13, DR-14, DR-20, DR-23, DR-27, DR-28, DR-31, DR-40, DR-42, DR-44, DR-48, DR-49 | 13 |
| IR-02 | Windows 基本动作：解压缩、修改配置文件、上传、下载、校验、本地执行脚本、远程执行、删 | DR-19 | 1 |
| IR-03 | 【本地执行脚本】查看窗口？编辑窗口？ | DR-03, DR-36, DR-62 | 3 |
| IR-04 | 建立本地库；安装到全局，可以被使用 | DR-04, DR-05, DR-06, DR-09 | 4 |
| IR-05 | 设计评估：拖拽过于麻烦，可视化查看是优点；核心是逻辑本身，"描述逻辑 + AI 转换"更快 | DR-45, DR-67, DR-80, DR-81, DR-82, DR-83 | 6 |
| IR-06 | 基本动作 = 单元执行动作；是否每个动作都能抽象为一次脚本调用？ | DR-10, DR-11, DR-12, DR-18 | 4 |
| IR-07 | 工作项 = 串联/组合/分支/循环 组合多个基本动作 | DR-20, DR-21, DR-22, DR-25, DR-26, DR-44 | 6 |
| IR-08 | 配置管理：添加远程节点（ip/用户名/密码/描述）；选择"当前目标节点" | DR-23, DR-50, DR-51, DR-52, DR-58 | 5 |
| IR-09 | 动作管理：增删改；动作 = manifest + 实现体；编辑 python 脚本/命令/参 | DR-03, DR-08, DR-10, DR-11, DR-13, DR-14, DR-16, DR-41, DR-48, DR-71, DR-79 | 11 |
| IR-10 | 多 runtime 表：builtin / python / powershell / cm | DR-11, DR-12, DR-15, DR-17, DR-18, DR-35, DR-36, DR-54, DR-57, DR-58, DR-59, DR-84 | 12 |
| IR-11 | 远程如何实现？用 Fabric？ | DR-12, DR-54, DR-57, DR-84 | 4 |
| IR-12 | 幂等/干跑/补偿三阶段：plan 零副作用、apply 前置备份、compensate 步骤 | DR-17, DR-18, DR-29, DR-30, DR-31, DR-32, DR-33, DR-34, DR-37, DR-38, DR-49, DR-53, DR-55, DR-63, DR-64, DR-65, DR-68, DR-79, DR-82 | 19 |
| IR-13 | 工作项管理：增删改 + 能组合基本动作 | DR-20, DR-22, DR-27, DR-28, DR-29, DR-42 | 6 |
| IR-14 | 脚本执行的麻烦点：缺少入口 | DR-04, DR-09, DR-41, DR-76 | 4 |
| IR-15 | 重复例 1：移动压缩包→解压→检测配置存在（相对路径，异常报错退出）→改 json→连接当前 | DR-19, DR-24, DR-26, DR-51, DR-55, DR-56 | 6 |
| IR-16 | 重复例 2：连接当前目标节点 → 修改远程 yaml | DR-19, DR-24, DR-51, DR-55 | 4 |
| IR-17 | 重复例 3：下载某个文件 | DR-19, DR-54, DR-56 | 3 |
| IR-18 | 重复例 4：上传某个文件 | DR-19, DR-54, DR-56 | 3 |
| IR-19 | 技术栈：React + React Flow + Monaco + rjsf；Python  | DR-07, DR-45, DR-48, DR-70, DR-73 | 5 |
| IR-20 | 页面：动作库 / 工作项列表 / 编排（图⇄YAML双栏）/ 运行面板（步骤树+日志流+证据 | DR-38, DR-40, DR-41, DR-42, DR-43, DR-44, DR-60, DR-61, DR-62, DR-63, DR-64, DR-65, DR-66, DR-67, DR-69, DR-70, DR-77, DR-78 | 18 |
| IR-21 | 双视图同步（DR-45）：ruamel round-trip 保注释；图编辑产出最小 pat | DR-43, DR-45, DR-46, DR-47 | 4 |
| IR-22 | 运行形态：Python + 本地 Web 平台；执行环境默认 Windows 本机 | DR-01, DR-06, DR-07, DR-35, DR-36, DR-72, DR-73 | 7 |
| IR-23 | 使用规模与凭据安全：单机单用户 | DR-02, DR-05, DR-23, DR-39, DR-52, DR-53, DR-68, DR-72, DR-83 | 9 |
| IR-24 | 交付：需求规格 + 架构设计与实施计划；开发记录 md；`requirements-spec | DR-74, DR-75, DR-76, DR-78 | 4 |
| IR-25 | 流程动作模型（RE 过程：确认→风险→建模→派生→合格性→团队一致→满足关系→合格性关系） | DR-74, DR-75 | 2 |

## 2. 合格性关系：派生需求 → 合格性用例

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

## 3. 覆盖统计

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
