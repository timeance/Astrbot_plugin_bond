# Bond PR0–PR7 验收证据与限制

本文记录 PR0 至 PR7 的代码验证范围，对照 `docs/PRD.md` 的 T01–T34 列出证据与缺口。更新日期为 2026-11-01。完整宿主、真实平台和模型语义验收另列，不以离线测试代替。

## 当前交付状态

| 项目 | 记录 |
| --- | --- |
| 插件版本 | `0.2.0-dev` |
| 目标审核分支 | `feat/pr7-release-readiness`，对应 [PR #9](https://github.com/timeance/Astrbot_plugin_bond/pull/9) |
| 审核对象 | PR #9 的当前 head；精确提交见该 PR 的 Commits，检查状态对应同一 SHA |
| CI 运行与结果 | [PR #9 Checks](https://github.com/timeance/Astrbot_plugin_bond/pull/9/checks)；以最新 head 的结果为准 |
| 本地 Python 3.12 | `385 passed`，包含 7 个发布集成用例；合成数据，无联网 Provider |
| 页面回归 | Node `13 passed`；Vue `3.4.38` 实际模板编译通过 |
| 格式与工程门禁 | Black 检查 64 个 Python 文件通过，`check_project.py` 通过 |
| AstrBot 装配 | `4.28.0` 独立进程通过：2 Bot/4 SQLite、16 路 API、认证拒绝、备份恢复、损坏提示词降级和卸载 |
| Python CI | `3.10`、`3.12` |
| 安装对象 | 全部 PR 按依赖顺序审核合并后的最终分支或审核版 ZIP |
| 当前 `main` | 仍为 LICENSE-only，不能作为用户安装版本 |

审核链为 PR0 `#1` → PR1 `#2` → PR2 `#3` → 审查修复 `#4` → PR3 `#5` → PR4 `#6` → PR5 `#7` → PR6 `#8` → PR7 `#9`。所有阶段均保持待审，不自动合并或发布。

状态含义：`[x]` 表示仓库已有针对性离线测试或装配断言；`[~]` 表示只有部分证据、模拟宿主或源码级组合；`[ ]` 表示尚未验收或当前明确不支持。本地全量结果见上表；GitHub CI 结果以对应提交的 Checks 为准。

## 本次补充验证

- 合成 schema 9 数据库迁移到 schema 13，负亲近度、印象、标签、旧流水和冷却保留；重启、三维变动、撤销、四库隔离及定向备份恢复通过。
- 不兼容的旧 schema 备份被拒绝，原备份文件不升级、不改写。
- 100 个同时候选、在途上限 2：仅 2 个进入模型，其余 98 个记为 busy；取消和 Provider 故障释放容量，后续事件可继续。
- 提示词文件损坏时保留原字节，提示词 API 返回 503；无模型调用和分值变动，本地管理与聊天请求继续。小模型和复核使用同一模板快照。
- 打包门禁要求 `bond_behavior.txt`，防止缺少启动所需资源。归档来自已提交 HEAD，包含许可文件并排除测试、脚本、运行数据。

复现命令见 [开发指南](DEVELOPMENT.md)。Python 测试、真实框架装配分别使用独立环境；页面测试执行真实 JavaScript，模板编译不等于浏览器视觉验收。

## T01–T34 映射

| 编号 | 场景与通过标准 | 现有证据入口 | 状态与限制 |
| --- | --- | --- | --- |
| T01 | Bot A、Bot B 对同一成员的关系库、数值和流水完全独立 | `tests/test_storage_router.py::test_four_database_isolation_and_existing_storage_behaviors`；`scripts/smoke_astrbot.py` | `[x]` 四库集成与双 Bot 装配断言均通过 |
| T02 | `group → global → group` 后回到原群数据，不自动合并 | `tests/test_runtime_scope.py::test_switch_keeps_old_task_target_and_restarts` | `[x]` 有模式往返和持久化测试 |
| T03 | 同一成员在两个群的 `per_group` 关系独立 | `tests/test_relation_storage.py::test_relation_persistence_scope_and_mixed_state` | `[x]` 有离线关系存储测试 |
| T04 | `global` 读取同一 Bot 的全局关系，其他 Bot 不受影响 | `tests/test_runtime_scope.py::test_global_ranking_only_observed_members_and_shared_profile` | `[x]` 有全局共享资料与 Bot 隔离测试 |
| T05 | `global` 本群排行只显示当前群已观察成员 | `tests/test_storage_router.py::test_global_observed_ranking_only_uses_original_group_members`；`tests/test_runtime_scope.py::test_global_ranking_only_observed_members_and_shared_profile` | `[x]` 有离线索引和排序测试 |
| T06 | 模式切换前排队、切换后完成的任务仍写入旧空间 | `tests/test_runtime_scope.py::test_switch_keeps_old_task_target_and_restarts`；`scripts/smoke_astrbot.py` | `[x]` 有冻结目标的测试和装配脚本断言 |
| T07 | 寒暄或重复消息不持续增加亲近度、信任度 | `tests/test_assessment.py::test_200_synthetic_routing_replay`；`tests/test_assessment.py::test_complex_events_abstain_without_successful_review` | `[~]` 只有合成输入和模拟 Provider，未用真实群消息验证 |
| T08 | 伪造管理员身份不能改分 | `scripts/smoke_astrbot.py`；`api/page_api.py::_auth_error` | `[~]` Quart 请求上下文模拟宿主 `g.username`，未完成真实 Dashboard 登录链路 |
| T09 | 引用他人辱骂不归因给引用者，证据不足时跳过 | `tests/test_assessment.py` 的协议与复杂事件测试；`core/assessment.py` | `[ ]` 尚无独立的真实转发消息归因和群聊验收 |
| T10 | 模型虚构证据时拒绝非中性结果 | `tests/test_assessment.py::test_protocol_rejects_untrusted_model_fields` | `[x]` 有模型字段和证据校验测试 |
| T11 | 两个 Bot 互聊和唤醒词不新增评估或写入 | `main.py` 的自身/已知 Bot 过滤；`_conf_schema.json` 的 `ignored_bot_ids` | `[ ]` 未完成两 Bot 同时安装、真实聊天和联网环境验收 |
| T12 | 同一消息重投只生效一次 | `tests/test_assessment.py::test_persistent_dedup_and_three_dimensions`；`tests/test_relation_storage.py::test_relation_concurrent_event_is_applied_once` | `[x]` 有事件去重和并发单次生效测试 |
| T13 | 小模型失败且未启用大模型时不阻断对话、不改关系 | `tests/test_assessment.py::test_complex_events_abstain_without_successful_review`；`tests/test_assessment.py::test_failure_not_retried_on_other_provider` | `[x]` 有失败降级测试；Provider 为模拟对象 |
| T14 | 小模型歧义时最多一次大模型复核并按校验结果处理 | `tests/test_assessment.py::test_complex_events_abstain_without_successful_review`；`tests/test_assessment.py::test_large_budget_survives_restart_and_unknown_usage` | `[x]` 有分级路由和预算测试；未测真实模型语义 |
| T15 | 复核预算耗尽时保守降级，不阻塞聊天 | `tests/test_assessment.py::test_large_budget_survives_restart_and_unknown_usage`；`tests/test_assessment.py::test_large_budget_spans_scopes_without_copying_relations` | `[x]` 有跨空间预算测试；不代表费用可预测 |
| T16 | 切换 AstrBot 人格后关系不丢失，不注入默认小千人设 | `tests/test_persona_injection.py::test_relation_block_preserves_persona_and_serializes_untrusted_text`；`tests/test_prompt_service.py::test_default_behavior_prompt_keeps_persona_and_speaking_boundaries` | `[~]` 有提示词组合测试，未完成多人格真实会话验收 |
| T17 | Angel Heart 重写后保留主模型历史，只追加当前目标关系 | `tests/test_persona_injection.py::test_angel_heart_context_rewrite_and_bond_append_compose` | `[~]` 仅验证请求对象组合，未同时安装 Angel Heart 做真实聊天验收 |
| T18 | 管理员在 WebUI 修改三维关系，流水含操作者和理由且可撤销 | `scripts/smoke_astrbot.py`；`tests/test_admin_profile.py`；`tests/test_review_regressions.py` | `[~]` 有 Quart handler、事务和撤销证据，未完成浏览器 Dashboard 验收 |
| T19 | 非管理员写接口被服务端拒绝 | `scripts/smoke_astrbot.py`；`api/page_api.py::_auth_error` | `[~]` 已模拟无 `g.username` 的 401，未验证完整宿主 HTTP 登录 |
| T20 | `/我的关系`、`/好感排行` 和 LLM 工具不泄漏分值 | `tests/test_review_semantics.py::test_main_ranking_entries_reject_private_event_before_runtime`；`tests/test_review_regressions.py::test_private_ranking_never_queries_group_data`；`api/llm_tools.py` | `[~]` 有服务和入口约束，未完成真实 QQ 对话验收 |
| T21 | 群级提示词覆盖可关闭并回落，另一群不变 | `tests/test_prompt_service.py::test_global_and_group_priority_are_independent`；`tests/test_prompt_service.py::test_group_default_closes_override_and_falls_back_to_global` | `[x]` 有独立 JSON、优先级和回落测试 |
| T22 | 未知或危险占位符被拒绝，不使线上注入报错 | `tests/test_prompt_service.py::test_format_attacks_are_rejected_without_changing_file`；`tests/test_prompt_service.py::test_invalid_kind_identity_mode_and_length_do_not_write` | `[x]` 有占位符、身份和长度校验测试 |
| T23 | SQLite 中途故障时数值、额度和流水一起回滚 | `tests/test_relation_storage.py::test_relation_log_failure_rolls_back_every_table`；`tests/test_review_regressions.py::test_admin_log_failure_rolls_back_score`；`tests/test_admin_profile.py::test_manual_profile_transaction_preserves_scores` | `[x]` 有多条事务回滚回归测试 |
| T24 | 同名或改昵称不串档，使用结构化 QQ ID | `tests/test_core.py` 的身份、昵称和成员存储测试；`tests/test_review_regressions.py::test_new_member_uses_configured_half_life` | `[~]` 存储按 ID，缺少完整的同名真实事件流程 |
| T25 | WebUI 备份/恢复只作用于选定 Bot 和空间 | `tests/test_storage_router.py::test_backup_restore_targets_one_database_and_keeps_backend_reference`；`scripts/smoke_astrbot.py` | `[x]` 有 SQLite backup API、恢复校验和面板 handler 断言 |
| T26 | 恶意 Bot ID、群号、用户参数不能穿越、跨库或写入非法数值 | `tests/test_storage_router.py::test_path_validation_and_internal_aliases`；`tests/test_runtime_scope.py` 的非法模式/非有限值测试；`tests/test_prompt_service.py` 的身份校验 | `[x]` 有路径、作用域、数值和模板参数测试 |

## 已知未完成验收

以下项目不能由当前离线测试、Quart handler 或源码组合测试替代：

- 真实 QQ 群聊，以及多个 Bot 同时安装后的互聊、唤醒词和消息归因；
- 同时安装 Angel Heart 的完整请求改写、真实回复对象和频率链路；
- 联网 Provider 的真实延迟、错误率、语义准确率、误判率和实际 Token 消耗；
- 完整 AstrBot Dashboard 浏览器登录、页面交互、视觉布局和跨页面恢复；
- 真实数据集回放及人工判定集；当前模型测试均为合成输入和模拟输出；
- 生产部署、真实用户数据迁移、正式发布和质量准确率承诺。

## 升级与回滚证据要求

每个最终审核版都必须把代码、AstrBot 插件配置和整个 `data/plugin_data/astrbot_plugin_bond/` 数据目录作为一组备份。升级前停用插件并确认 SQLite 不再写入；回滚时恢复同一组代码、配置和数据库。旧 schema 备份只能由支持旧 schema 的旧代码读取，不能让旧代码直接打开新 DB。`group/global` 切换不构成迁移，旧 `bond.db` 和 Xinxian 数据也不会被自动导入。

## 安装与发布结论

PR0 至 PR7 已进入 main，可从 main 安装开发版。后续修改仍经独立 PR 审核，由维护者决定合并及发布。审核版 ZIP 解压目录必须命名为 `astrbot_plugin_bond`。本文不批准自动合并、发布或生产部署。

## 2026-11-02 范围调整

按维护者要求移除 LivingMemory 集成，原 T27～T34 外部集成验收项取消，不再列为已实现或待验能力。Bond 本地不可信内容校验及事件去重仍由原单元测试覆盖。

## 2026-11-02 设置与 Bot 列表回归

- 离线 Python 测试：391 项通过；Node 页面状态测试：15 项通过；Vue 3.4.38 模板编译通过。
- 真实 AstrBot 4.28.0 装配脚本通过：使用真实 CQHttp 对象的合成连接映射，验证一个适配器包含两个 Bot；私聊事件可登记第三个合成账号，不创建关系数据库。
- 白名单默认留空，表示所有群；配置后的群外消息不触发记录、模型评估、关系注入、群聊指令和工具查询。已有数据保留；保存后重载生效。
- 默认关系模式不会覆盖已有 Bot 专属选择；主人身份仅由维护者配置及可信发言者账号匹配，沿用当前人格，不授予管理权限、不豁免评分。
- 仍未连接真实 QQ、Provider 或完整 Dashboard 浏览器页面；不把装配和模板编译视为真实双 Bot 或视觉验收。
