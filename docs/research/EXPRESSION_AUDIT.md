# 关系表达边界审查

日期：2026-11-02。源码基线：`993e821bd127488820a0f639107f67c0c5402bf4`，分支 `test/phase0-relation-evidence`。本文仅检查称谓、公开输出和请求拼接契约，不评判模型回复质量。

## 结论与证据等级

在所查入口及本次执行的 **23 个现有定向测试**中，尚未复现 prompt 边界缺陷。人格保留、资料序列化、主人标记和请求字段组合已有契约验证；结论为“已验证的范围保留现状，无生产改动”，不能写成“所有提示注入均安全”或“真实人格表现通过”。

**未测真实人格表现、QQ、真人或真实 Provider，也未同时安装 AngelHeart 验收实际双插件聊天。** 所用请求、身份和关系数据均为合成数据；测试环境不安装真实 AstrBot。没有新增测试、运行全量测试、修改生产逻辑或进行发布。

## 函数与测试对应

路径相对仓库根目录。“通过”指本次定向执行；源码审查和既有产物不算新增运行验收。

| 范围 | 精确函数或入口 | 测试与证据 | 判定 |
| --- | --- | --- | --- |
| 三维称谓 | `core/relation_policy.py::RelationResolver._keys / resolve / resolve_labels`；`services/favor_service.py::FavorService._relation_title / relation_title` | `tests/test_relation_policy.py::test_resolver_keeps_relationship_dimensions_composable`、`test_resolver_accepts_configurable_thresholds_and_labels`；`tests/test_relation_storage.py::test_relation_persistence_scope_and_mixed_state` | 通过；熟悉、亲近与信任可并存，“亲近但戒备”不会被合并成一个总等级。 |
| 公开自查 | `main.py::BondPlugin._cmd_self_relation / _tool_query` → `api/llm_tools.py::tool_query_favor` | 源码核对；`scripts/smoke_astrbot.py::check` 已有拒绝查他人、结果不含示例分值及默认角色名的断言，本次未重跑该 smoke | 仅取可信发言者自己的称谓；不能把源码与断言存在等同于本次平台验收。 |
| 公开排行 | `main.py::BondPlugin._cmd_rank / _tool_rank`；`api/commands.py::handle_ranking / rank_reply / try_text_wake` → `api/llm_tools.py::tool_query_ranking` | `tests/test_review_regressions.py::test_private_ranking_never_queries_group_data`；`tests/test_review_semantics.py::test_main_ranking_entries_reject_private_event_before_runtime`；`tests/test_runtime_scope.py::test_global_ranking_only_observed_members_and_shared_profile` | 通过；私聊拒绝且不读取群数据、全局榜仅列本群已观察成员。源码限制最多十人，只输出名字与称谓；分值不公开的真实装配断言见上述 smoke，未重跑。 |
| 人格与资料边界 | `services/inject_service.py::InjectService.build_relation_block / inject`；`api/listeners.py::_on_llm_request_inner` | `tests/test_persona_injection.py::test_relation_block_preserves_persona_and_serializes_untrusted_text` | 通过；保留原 system_prompt 前缀、prompt 和 contexts，转义合成恶意印象；事件只输出 reason，不输出原文或 delta。JSON 序列化不构成模型必然服从边界的证明。 |
| 模板格式边界 | `services/prompt_service.py::PromptService._validate_template / set_template` | `tests/test_prompt_service.py::test_format_attacks_are_rejected_without_changing_file`（7 个参数） | 通过；拒绝未知字段、属性/索引访问、转换、格式说明符及损坏格式，持久文件不变。只覆盖格式契约。 |
| 默认行为及评估协议 | `resources/prompts/bond_behavior.txt`；`services/prompt_service.py::PromptService.resolve`；`services/inject_service.py::InjectService.build_relation_block` | `tests/test_prompt_service.py::test_default_behavior_prompt_keeps_persona_and_speaking_boundaries`、`test_assessment_custom_template_does_not_need_protocol_but_resolve_keeps_it`、`test_global_resolve_uses_the_same_immutable_assessment_contract` | 通过；模板保留当前人格、权限和发言频率约束；自定义评估模板仍追加固定协议。没有 Provider 服从性结论。 |
| 主人标记 | `core/identity.py::parse_master_ids / is_master`；`services/favor_service.py::FavorService.is_master`；`api/listeners.py::_on_llm_request_inner`；`services/inject_service.py::InjectService.build_relation_block` | `tests/test_core.py::TestIdentity::test_parse / test_is_master`；`tests/test_persona_injection.py::test_only_configured_master_has_trusted_identity_without_extra_permissions` | 通过；标记取自配置账号与事件发言者匹配，自称主人留在不可信资料中。标记服从当前人格，不授予管理权限、不豁免评分；实际平台权限未测。 |
| AngelHeart 请求组合 | `main.py::BondPlugin._on_llm_req`；`api/listeners.py::on_llm_request / _on_llm_request_inner`；`services/inject_service.py::InjectService.inject` | `tests/test_persona_injection.py::test_angel_heart_context_rewrite_and_bond_append_compose` | 通过；测试手动构造重写后的请求，验证 Bond 追加系统资料后不覆盖 prompt/contexts。没有调用真实 AngelHeart 重写方法或框架调度。 |

`main.py::_cmd_set / _cmd_reset / _cmd_set_rel / _cmd_set_tags / _cmd_refresh_imp` 使用宿主管理员权限装饰器；主人关系标记不参与这些权限判定。此项为源码核对，不代替真实权限链验收。

当前三维注入路径调用 `build_relation_block`，没有调用旧 `InjectService.build_block`。旧 `persona_anchor`、`master_prompt`、等级主人专属文案的存在及旧单测，不能当作当前路径覆盖人格的复现证据。当前主模型人格直接保留在请求中，也不以旧 `JudgeService._persona_ctx_for` 的默认称呼替换。

`docs/PROMPTS.md` 记录 AngelHeart `77ee3aa99ea7450a8363b3ebea651f92dcb34693` 的优先级 50 重写，以及 Bond 的优先级 -100 追加。本次只核对 Bond 入口及合成请求组合，未重新核验外部版本或真实双插件执行顺序。

## 离线现象与尚待验证的假设

冻结的 `PHASE0_RESULTS.json` 的 `S43-persona-global-privacy` 在 baseline/model-a 两组均保留两种合成人格前缀，并排除私密合成印象。这是保存的请求对象结果，已只读复核；主验证流程已重复运行全部合成回放。

同一产物的 `S45-threshold-oscillation` 从 `(familiarity, affinity, trust)=(9.9,39.8,39.9)` 开始，baseline 的四步结果为：

| 步骤 | 关系状态 | 称谓 |
| --- | --- | --- |
| 感谢 | `(10.1,40.3,40.1)` | 眼熟、亲近且信任 |
| 侮辱 | `(10.1,39.3,38.6)` | 眼熟 |
| 道歉 | `(10.2,40.0,39.6)` | 眼熟、亲近 |
| 再次侮辱 | `(10.2,39.0,38.1)` | 眼熟 |

这复现了合成数据越过阈值时的标签切换；model-a 同样出现亲近标签往返。它不证明真实对话语气震荡，也不证明某次降级不应发生。`RelationResolver._keys` 按当前值直接判断，没有前一称谓状态；默认熟悉度边界为 10/40，亲近度、信任度边界为 ±40。阈值附近可能频繁切换是明确假设，是否损害表达仍须另行标注和验收。

完整回放已由主验证流程重复执行，逻辑结果一致；规则、配置、协议、main.py 与各生产模块源码摘要保存在 PHASE0_RESULTS.json。此处复核不增加真人或真实 Provider 验收结论。

回放中的 `tests/research_support.py::summarize` 指标 `title_flips_per_100_turns` 统计每条记录前后称谓是否变化，包含合理升级和降级，不是经人工标注的“错误震荡率”。不能把它直接当作滞回修复收益或模型语义准确率。

## Phase4 处理边界

**Phase4 没有复现缺陷就不制造代码改动。** 当前保留已有表达逻辑；若后续发现覆盖人格、泄露资料、越权身份或错误字段重写，应先固定输入、请求前后状态、版本及失败断言，再做必要修复。

**滞回先搁置。** 实施前须先明确称谓状态在 Bot、scope、成员维度的生命周期，以及重启、跨群共享、撤销/重做、管理员设值、临时覆盖及到期恢复时的更新规则；还须有独立标注区分合理转变与无意义往返，并定义升级、降级和风险警示的允许延迟。证据不足时不凭单一阈值片段增加持久状态或调整标签。
