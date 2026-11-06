# Bond PR0–PR7 验收证据与限制

本文保留 PR0 至 PR7 的代码验证记录，并补充后续版本的验证范围；早期需求沿用 T01–T34 编号。更新日期为 2026-11-03。完整宿主、真实平台和模型语义验收另列，不以离线测试代替。

## 0.5.1 分阶段导入验证（2026-11-06）

本次将 0.5.1 源码按独立维护、消息快照与行为指导、分级诊断三个 PR 导入 0.4.4 基线，并修复导入审查中确认的问题。

- 最终叠加版本 Python 3.12：1457 passed、1 skipped；跳过项为 Windows 符号链接权限限制。Node 页面测试 69 passed。
- Black 与 Ruff 格式检查通过（127 个 Python 文件），元数据、安全默认值与初始化顺序检查通过。
- 真实 AstrBot 4.28.0 装配与卸载、2 Bot × 2 数据空间、诊断旧配置默认档位补全与显式选择、导出脱敏、认证拒绝、选择性清除和正式状态不变通过。
- 实际 ToolLoopAgentRunner 到离线记录 Provider 的初次调用、工具续轮、空输出重试通过；人格与历史保留，同事件关系快照不变，下条独立消息刷新。
- 使用合成数据库、消息及离线 Provider，未运行真实 QQ 群、联网聊天模型、完整 HTTP 登录或浏览器验收。三臂行为材料为待填写的对照输入和评分表，未证明真实模型表达效果改善。
- PR 按依赖顺序等待维护者合并；没有部署或发布。远端 CI 与 Codex Review 以对应 PR 最新提交的记录为准。

## 0.3.6 本地验证（2026-11-03）

本次修改默认深蓝及独立明暗切换、基础配置快速开始、推荐值确认流程和九款随机排行插图。数据结构保持 schema 15。

本地 Python 3.12 全量为 **892 passed、1 skipped**；跳过项受 Windows 符号链接权限限制。Node 页面测试 **49 passed**；107 个 Python 文件通过锁定版本 Black、Ruff 格式检查，项目检查通过。

- 基础设置可在没有 Bot 时读写，服务端认证保留；Provider 响应仅含模型名称和 ID。两处配置保存共用 revision，缺失或过期时返回 409，并提示刷新；保存失败回滚，运行配置待重载后生效。
- Node 页面用例覆盖主题记忆、无 Bot 基础设置、模型选项、推荐值取消与确认、覆盖范围和保存时机；推荐值确认不会自动保存。
- 排行覆盖九款透明资源、并发选择、连续去重、坏图回退和全部不可用时的正文生成。每款均生成合成排行榜预览；安装素材为 576×576，完整原图另存。
- 深浅主题正文、次级文字、按钮与状态提示的静态对比度检查通过；本次没有操作浏览器，窄屏布局、真实宿主主题变化、键盘焦点和弹窗交互仍须浏览器验收。
- 本地离线测试不能代替真实 Provider、宿主原配置入口自动重载和平台聊天验收。本次未合并、部署或发布。

## PR0–PR7 历史交付记录

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

## 2026-11-02：证据驱动 Phase0

新增独立合成研究基线：52 场景、每臂 1201 消息，431 项离线 Python 测试及 19 项 Node 测试通过；真实 AstrBot 4.28.0 装配和 17 个消息转换用例通过。研究不改变运行默认值或 schema。分母与基线见 [合成回放基线](research/BASELINE.md)，运行命令见 [开发指南](DEVELOPMENT.md#合成关系回放)；不代表真实 QQ、Provider 或真人语义验收。后续记录不得把本条的历史测试数量当成最新数量。


## 2026-11-02：0.2.6 自然语言查询的人格边界

- 查询工具返回明确标注“当前 Bot → 当前发言者／本群成员”的资料；成功、空榜、私聊和群白名单拒绝路径均有验证。没有人格分类或固定答句，不要求披露或复述。
- `/我的关系`、`/好感排行` 原文本兼容；普通查询仍无分值、印象或历史。新增 14 项工具测试，并验证三种不同人格的原系统提示、用户输入和历史保留。
- 全量离线测试 671 项通过、1 项跳过（Windows 创建真实符号链接的权限限制，已有替代路径测试）；Node 页面测试 22 项通过；Black 26.5.1 检查 91 个文件通过；`scripts/check_project.py` 通过。
- 真实 AstrBot 4.28.0 合成装配通过，包括新工具入口、2 Bot/4 SQLite、17 路页面接口、白名单、认证及卸载。没有连接真实 QQ、Provider 或浏览器；不能据此判断自然语言主客体理解或人格表达准确率。
- 版本从 0.2.5 递增为 0.2.6；schema、配置、关系计算及存储路径均不变。仅回退本次代码即可恢复旧工具文本，数据无需迁移。

## 2026-11-03：0.3.5 两套节奏与图片排行

- 写实保持原推荐规则；Galgame 仅增加正向 F/A 请求与每日额度。真实服务/SQLite 对照覆盖重启、跨日、去重、往返切换、Trust与负向不变，见[本轮记录](research/RELATION_MODES_AND_RANKING.md)。
- 亲近度基线固定0，F/T不随沉默衰减；schema保持15，无需重置数据。旧非零基线从原时间锚重新计算的兼容影响已写入README。
- 排行展示昵称、头像和关系词条，不展示分值；三种入口共同检查所有人/仅主人权限。模拟头像失败、大小限制、取消、缓存及绘图回退通过；未连接真实头像端点。
- 离线Python：829通过、1跳过（Windows真实符号链接权限）；Node：34通过；Black/Ruff：107文件通过；工程元数据检查通过。
- 独立AstrBot4.28.0合成装配通过，包含真实图片消息组件、权限与新模板；24条OneBot合成转换、两Bot/两作用域诊断检查通过。仍未验收真实QQ、Provider、Angel Heart同装或浏览器，不宣称统计准确率或生产就绪。
- 管理页面浅蓝主题已集成；布局与交互逻辑沿用既有实现，危险操作与错误保持红色，浏览器视觉仍未验收。
- 久别旧友两次离线回放的逻辑JSON一致；专用回暖机制因持久状态与撤销复杂度不采用，依据见[久别旧识评估](research/RECONNECTION_ASSESSMENT.md)。


## 2026-11-03：0.3.7 关系评估框架

最终验收基准为已合入0.3.6的 main `3512f29682040b192e1ca949d7e24d789c0a6c63`，保留其深色界面、快速开始与九款排行插图。规则合同见 [关系评估框架](ASSESSMENT_FRAMEWORK.md)，长期数据见 [固定时钟回放](research/ASSESSMENT_FRAMEWORK_REPLAY.md)。本条取代旧记录中关于120秒冷却、寒暄关键词过滤、Galgame×1.25、净额限幅和信任不衰减的当前状态描述；旧记录保留为阶段历史。

- Python 3.12：`python -m pytest tests/ -q`，**1060通过、1跳过**。跳过为Windows真实符号链接权限限制；测试使用独立临时目录与离线Provider。
- Node 24.13：`node --test tests/dashboard.test.cjs`，**59通过**；CI使用Node22。包含模式实际值、当前运行值、保存/恢复默认、保持模型选择、非法窗口/上下文参数及旧预设缺省显示一致性。
- 锁定Black 26.5.1 / Ruff 0.15.22：**113个Python文件通过**；`scripts/check_project.py`与`git diff --check`通过。归档从提交后的HEAD构建，公开内容与打包门禁结果记录在对应PR。
- 独立AstrBot4.28.0：`scripts/smoke_astrbot.py`通过，包含2 Bot/4 SQLite、Galgame默认参数、独立T衰减配置、调用窗口预算、回复钩子冻结身份、新旧配置、17路Web接口、备份/恢复与卸载。`scripts/smoke_message_metadata.py`的24例转换以及`scripts/smoke_diagnostics.py`通过。
- 数值验证覆盖全部F段边界、A原有曲线、模式只放大正向、单次上限、最终舍入、F小数余量、跨日和总界限。正负方向分别记账，交替加减和撤销不会返还已用额度；时间衰减不占扣分额度。
- A60天、T正负两侧180天、F不衰减；F/A写入不重置T时钟。临时关系、手工设值、撤销/重做及重启均有验证。Schema15→16升级、故障回滚、旧日志缺失下限、跨升级临时关系、已过期尚未结算的覆盖均有回归。
- 初评/复核账本在同Bot跨群与数据空间合计，并发和重启不清零；不同Bot独立。多次有效交流可在同窗口分别计分，稳定ID去重；失败和超时仍计实际调用次数。
- 两次7/30/180天回放的results.json字节一致；每天30/60候选的调用与轨迹相同。仅普通问答的A/T保持0，预置友好互动不自动增信。不能据此声称模型会正确识别真实重复感谢或玩笑。

未运行真实QQ、联网Provider、真人标注或浏览器；真实误判率、Token成本和主回复p50/p95仍未知。原有故障注入会产生预期异常日志，不等于连接过真实平台。没有自动合并、发布或部署。升级无需重置，但必须成组备份代码、宿主配置和数据；回滚恢复升级前备份，不能只降低schema号。


## 2026-11-03：0.3.8 三维衰减与基线

按维护者追加需求，三维默认半衰期更新为F60天/A30天/T90天，默认基线均为0。F新增默认开启的衰减；F低于基线不变，A/T在正负基线之间不变、区间外向同号基线收敛。此条取代上条F不衰减及A60/T180的当前默认说明；保留此前测试记录作为阶段历史。

- 全量 `python -m pytest tests/ -q --tb=short`：**1131通过、1跳过**；跳过仍为Windows真实符号链接权限。新增覆盖基线20的正负两侧、区间内不推分、配置重载、并发结算、临时关系、撤销、分数精度和额度不退款。
- `node --test tests/dashboard.test.cjs`：**59通过**；三维参数编辑、范围校验、恢复默认、模式切换不改变基线、保存值与运行值分离通过。
- Black26.5.1 / Ruff0.15.22：**114文件通过**；工程元数据检查版本为0.3.8。公开内容与提交后远端检查结果见PR。
- 真实AstrBot4.28.0装配通过：F60/A30/T90、三维基线默认与自定义值、旧已失效基线清理、2 Bot/4 SQLite、17路接口及备份恢复。24条OneBot合成转换和两Bot/两空间诊断检查通过。
- Schema16→17只增加熟悉度独立时间锚，旧熟悉度从升级时起算；跨升级的临时覆盖和已到期未结算覆盖不会重复延后时钟。迁移失败回滚、重启及同版本备份恢复验证通过；旧版本备份仍须配套代码、配置回退。
- 两次新默认7/30/180天回放JSON字节一致，SHA-256为 `1c0e2c1aef21054158b6a4b4ed24f5aa5575d8605dd09b4b07f7450ce15490a5`；[回放报告](research/ASSESSMENT_FRAMEWORK_REPLAY.md)已替换为这组参数的实际结果。

读取和有效事件写入时计算衰减，没有新增定时全表扫描、关系维度或奖励队列。既有明确保存的模式、半衰期等自定义值保持不变；需要采用新默认时，在“关系变化”恢复默认、保存并重载。无需重置成员数据。没有运行真实QQ、真实Provider、真人标注或浏览器，不据合成结果宣称误判率降低；本次交付PR，不自动合并、发布或部署。


## 2026-11-03：0.3.9 设置精简与今日概览

- 详细设置和评估设置默认折叠；逐项帮助解释当前字段。运行额度、三维衰减速度和基线使用表格显示，保留保存值与运行值的区别。
- 首次打开为浅色，优先恢复当前浏览器保存的深浅主题。今日概览读取所选 Bot 两空间的调用账本、当前空间的当日实际变动成员，统计故障显示失败。
- 全量离线测试：`python -m pytest tests/ -q --tb=short`，1140 passed、1 skipped。跳过项为 Windows 真实符号链接权限用例。
- `node --test tests/dashboard.test.cjs`：62 passed；Vue 3.4.38 模板在 Node 中编译通过，未做浏览器视觉验收。
- Black 26.5.1、Ruff 0.15.22 格式检查通过，114个文件；配置 schema 对照确认字段、默认值、选项和校验限制不变。
- AstrBot 4.28.0 独立空目录装配通过，实际断言18条 Web API、隔离数据库、认证、配置和卸载。模拟宿主认证，无真实 QQ 或 Provider。
- 注入生命周期合成探针16项通过。本版不修改注入位置，取舍见 [注入方式评估](research/INJECTION_REVIEW.md)。
- 1,000/10,000合成成员的分页和今日概览测量见 [页面负载记录](research/DASHBOARD_LOAD.md)。不视为生产延迟或缓存收益。

本次无数据迁移，schema 保持17。升级保留关系、流水与已有配置，不需要重置。回退0.3.8代码即可撤回本版界面和统计入口，数据库格式相同。未打包、合并、发布或部署。
