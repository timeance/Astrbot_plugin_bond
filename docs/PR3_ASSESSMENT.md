# PR-3：分级互动评估

## 行为与边界

`AssessmentService` 是自动关系更新的唯一评估入口。旧标量评估不再由消息监听器调用，`judge.enabled` 不再开启评分；旧服务继续为成员印象提供摘要模型。未开启 `assessment.enabled` 时只观察成员，不调用评估模型。

可信平台事件在排队前固定 Bot、群、成员、平台、消息 ID 和原文。事件 ID 对这些身份字段取稳定摘要，重启后仍去重；消息缺少可信 ID 时不评分。物理数据库账号与事件 Bot 不一致时拒绝评估。已知 Bot 的排除沿用消息入口，其他 Bot 账号需加入 `ignored_bot_ids`。

空文本、纯寒暄、命令和未直接指向 Bot 的消息不调用模型。所有 Bot 共用在途限制，同一成员已有评估时合并为一次在途请求；无等待队列，繁忙候选被跳过，不拼接不同消息的证据。没有扩大连续对话识别范围。

小模型只输出 `event_type`、`confidence` 和 `evidence`，未知字段、非法分类、非有限置信度、伪造证据均拒绝。证据必须连续出现在当前消息原文中。正常批评、玩笑、侮辱和操纵属于高误判风险分类，仅在复核通过后应用；低置信度与引用共同出现时也可复核。复核最多一次。无复核模型、预算耗尽、超时或无效结果均不评分，不改用会话默认模型或旧标量结果。复核不是权限或数值来源。

本地规则产生三维增量，`FavorService` 继续负责经济规则、单事件上限与事务写入。正常批评只增加少量熟悉度。流水只记录实际生效变化；零变化统计为 `unchanged`。

## 配置

在 AstrBot 插件配置的 `assessment` 中设置：

| 字段 | 默认 | 行为 |
| --- | --- | --- |
| `enabled` | false | 自动评分开关 |
| `small_provider_id` | 空 | 启用时必须指定已配置 Provider |
| `large_provider_id` | 空 | 留空不复核 |
| `assessment_mode` | tiered | single 或 tiered；single 仍对高风险分类弃权 |
| `small_timeout_sec` / `large_timeout_sec` | 20 / 30 | 单次调用超时 |
| `cooldown_sec` | 120 | 评估前占位，失败也消耗冷却 |
| `max_concurrent_assessments` | 2 | 插件所有 Bot 的在途上限，1..32 |
| `per_user_daily_large_cap` | 2 | 当前 Bot 每用户每日复核上限 |
| `per_bot_daily_large_cap` | 20 | 当前 Bot 每日复核总上限 |

复核额度按 UTC 日期，在调用前原子保留；异常、超时、取消均计入次数，重启不会重置。group/global 各自保存调用账本，复核前在插件级预算锁内合计同一 Bot 的两个空间，再保留额度；关系和流水不复制、不合并。恢复旧备份会恢复当时的账本；不能把恢复当作连续计费周期的审计来源。

同一 Provider 用于两层不会节省单次模型成本。当前不提供 Token 硬预算、货币预算或自动单价估计。上限是调用次数，不能承诺固定金额。

## 统计与升级

Schema 13 新增 `assessment_attempt` 与 `assessment_call`，不保存评估原文或模型原始输出。状态区分 pending、accepted、unchanged、abstained、failed、cancelled，以及过滤、繁忙候选；调用记录区分 ok、invalid、timeout、failed、cancelled。进程中断可能留下 pending，表示结果不明，不能当成功。

管理员认证接口 `GET /astrbot_plugin_bond/assessment_stats?bot_id=...&scope_mode=...` 返回当前数据库的日期、群、模型层级、结果和 Token 汇总。Provider 未返回用量时为 null；任一调用用量未知，该分组 Token 汇总也为 null，费用始终未知。完整面板和日期筛选由 PR-5 接入。

升级前停用插件，备份代码、配置和整个数据目录。回滚必须恢复同一版本的三份备份，旧 schema 代码不能直接打开 schema 13。保留原 AGPL 许可和来源。LivingMemory 未接入，不影响本地链路。

## 200 条合成回放

`tests/test_assessment.py::test_200_synthetic_routing_replay` 包含 10 类输入，各重复 20 次，使用独立合成身份和固定模型响应，覆盖普通群聊、寒暄、命令、感谢、友善、提问、批评、玩笑、侮辱与恶意格式要求。

对照基线按每条消息调用一次模型定义，为 200 次，未运行旧插件的实际 Provider；新路由过滤 60 条，调用小模型 140 次、复核模型 60 次，120 条进入规则写入、20 条非法输出被拒绝。两层调用总数仍为 200，不能据此声称费用下降。模拟 Provider 不返回 Token，用量与费用未知。

这是路由和安全不变量回归，不是实际模型语义准确率测试。尚未用真实 Provider 测量误判率、延迟或金额，也未完成真实群聊、完整 dashboard 与 Angel Heart 联动验收。人格适配和模板管理留给 PR-4。
