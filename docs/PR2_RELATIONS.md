# PR-2：三维关系与本地规则

## 已实现

- `RelationState` 校验三个独立维度，拒绝布尔值、字符串、非有限数值和越界输入，保留一位小数。
- `RelationPolicy.deltas(event_type, intensity)` 从经过验证的事件类别生成增量，不接受模型指定总分。正常批评不扣亲近度或信任度，不确定事件不改变关系。
- `RelationResolver` 支持可配置阈值和称谓，可表达熟悉、亲近与戒备并存。
- `FavorService.get_relation`、`apply_relation_event` 和 `set_relation` 统一读取、自然更新和管理员调整。写入身份必须由调用方从可信平台事件取得；这些方法不注册为 LLM 工具。
- 状态、三维当日额度、冷却、事件 ID 和流水在同一 SQLite 事务中提交。事件 ID 在当前数据库、成员与群键内去重；全局模式使用统一群键。
- 管理员调整保留流水，不占自然评估额度。撤销反向应用实际生效的三维增量，尊重各维度边界；撤销反向流水可重做。
- 原有成员印象、标签、重要事件、经济规则、SQLite WAL 与标量接口保留。旧接口只更新亲近度。

## 数据结构与迁移

Schema 12 为 `favor` 新增 `familiarity` 和 `trust`。亲近度继续使用 `favor` 列，`FavorRecord.affinity` 是读取属性，避免存两份相同状态。旧库升级时保留已有亲近度、印象与流水，另两维默认 0。

`daily_gain` 增加熟悉度和信任度额度；`favor_log` 增加三维实际增量 JSON 和稳定事件 ID。旧流水保持原义；撤销旧流水只反向亲近度。各 Bot 的 group/global 数据库独立迁移。

升级前停用插件，备份整个数据目录、配置和代码。Schema 12 不能直接由旧代码打开；回滚须恢复升级前同一套备份。空间恢复入口只接受当前 schema 的完整备份。

## 服务配置

构造 `FavorService` 时可传 `relation_policy`、`relation_resolver` 和 `relation_caps`。每日限幅使用 `{维度: (正向额度, 负向额度)}`，默认 `familiarity=(2,0)`、`affinity=(3,6)`、`trust=(1,4)`。`RelationPolicy` 支持事件规则和单事件上限，`RelationResolver` 支持阈值及文字映射。

自然更新要求稳定 `event_id`，默认同一成员冷却 120 秒。管理员入口必须由上层先完成宿主权限校验；本阶段不新增对外写 API。

## 后续接入

PR-3 将完成可信身份、证据和模型输出校验后再调用事件入口。目前自动聊天评估仍使用上游标量协议，默认关闭。PR-4 处理人格注入，PR-5 提供完整管理页面、配置和隐藏分值的群聊查询。临时情绪与 LivingMemory 不在本 PR 范围内。
