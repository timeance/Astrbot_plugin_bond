# 历史证据审计

日期：2026-11-02；审计基线：`993e821bd127488820a0f639107f67c0c5402bf4`，分支 `test/phase0-relation-evidence`。以下行号对应该基线的生产源码；研究标签是本文的标注约定，不是当前生产数据库新增字段。

## 来源与保留语义

| 观察 | 当前代码出处 | 可以得出的结论与边界 |
| --- | --- | --- |
| 流水默认窗口与排序 | `services/favor_service.py:478–490`；`storage/sqlite_backend.py:1206–1256`，尤其 `1238–1239` | 服务默认 20 条、后端默认 200 条；按 `ts DESC,id DESC LIMIT ? OFFSET ?` 查询。这里只读，不代表只保留这些历史。分页期间如有并发写入，offset 可能漂移；A3 应使用冻结快照。 |
| 近期注入窗口 | `services/favor_service.py:493–535` | 默认查至多 `max(count*5,count)` 条，再排除撤销行、按天数/显著性窗口过滤并取前 count 条。没有 DELETE，不是完整历史导出。没有传入 logs 时此方法直接用所给 group_id，调用者需注意作用域键。 |
| 显式 reset | `services/favor_service.py:852–854`；`storage/sqlite_backend.py:1118–1171`，成员日志删除在 `1147–1149`，群日志删除在 `1126` | 删除指定成员或群的 favor、流水、额度、冷却等所列数据；这才是明确的历史删除。不能外推为所有审计表都会被删除。临时关系生效时 reset 受写入守卫约束。 |
| 三维实际增量 | `storage/sqlite_backend.py:504–516`、`547–584` | 先每日限幅、数值封顶，再算实际向量 real；日志 scalar delta 是实际 affinity，relation_delta 保存实际三维向量。拟请求增量不等于落库增量，零向量也可能有日志。 |
| 来源及截短文本 | `services/favor_service.py:270–284`、`305–316`；`storage/sqlite_backend.py:457–459`、`566–580` | 自然事件入口采用后端默认 judge；管理设值显式 admin。reason/message 最多 200 字符，日志保存 event_id，但没有独立的 event_type、受话人事实或完整 quote provenance 列。仅凭日志不能声称能重建原始群聊。 |
| 撤销及重做 | `services/favor_service.py:833–850`；`storage/sqlite_backend.py:1297–1399`，尤其 `1338–1372`、`1373–1395` | 撤销按当前状态和值域重算实际反向量；原行 reversed=1，追加 source=undo 的流水。撤销 undo 行即重做；它是对实际撤销的反向，不是重新判读一条聊天。反向被封顶时不能假设等于原请求向量。 |
| 人工数值/资料编辑 | `services/favor_service.py:286–339`、`765–790`；`storage/sqlite_backend.py:626–684`、`744–778` | 三维设值为 admin；旧标量设值默认 admin 且可无 relation_delta。资料编辑为 admin_profile，带操作者及零标量，不代表自然行为；`services/favor_service.py:791–799`、`storage/sqlite_backend.py:1319–1320` 拒绝将其作为数值撤销。 |
| 旧数据迁移 | `storage/migrations.py:131–138`、`155–175` | v12 新增 familiarity/trust 默认 0，旧日志 relation_delta 默认 '{}'，event_id 可为 NULL。默认值是兼容填充，不是历史事实补全。 |
| 临时关系冻结 | `storage/sqlite_backend.py:1574–1629`、`1631–1691`；`services/favor_service.py:178–195` | 展示可用临时值，正式写入受 active/revision 守卫；到期/取消平移衰减锚点并记 override_audit。临时显示值不构成自然互动证据，不应补造期间的正式增量。 |
| Bot / scope 分区 | `storage/router.py:16`、`224–246`；`core/scope.py:8`、`29–36`；`services/favor_service.py:462–466` | 不同 Bot 与 group/global 使用物理分库；群/成员键另作精确匹配。全局模式同一 Bot 跨群共享是定义内行为，不是串库。比较历史前必须固定完整分区身份。 |

`source` 是写入来源标签，不等于作者身份的独立证明。跨插件标量入口允许调用方传 source（`services/favor_service.py:677–694`），所以即使 source=judge 也不能跳过来源核查。流水的 `trust` 数值描述旧策略结果，不证明事实可信度、守约能力或可靠性。

## 四类历史来源标签

标签描述来源及用途；`reversed`、完整性和诊断性另外标注。它们不直接对应 E 的数值，也不自动授予“确定行为证据”资格。

| 研究标签 | 归类依据 | 可以使用的范围 |
| --- | --- | --- |
| `behavior_candidate`（可追溯行为候选） | 可信自然入口来源，事件可对应，三维向量明确；仍需核查原文、作者、受话人及引文。 | 可核对当时落库事实。只有来源足够、未失效且人工语义标注通过的部分，才能用于相应维度的诊断性证据。 |
| `legacy_unknown`（旧数据或来源不足） | 旧日志缺 relation_delta/event_id，或来源消息缺失、截短、身份/引文不可复核。非旧 schema 的不完整日志也进入此类。 | 可报告已有标量、时间、reason 等字段；缺失维度和行为语义保持未知。不能把 '{}' 当显式零向量，不能由默认 trust=0 推定历史无信任证据。 |
| `manual_edit`（人工设定与资料审计） | 可确认的 admin/admin_profile 设值、人工资料和临时覆盖审计。 | 解释管理干预、初始/显示状态及操作者；从自然行为证据分母中按预先声明规则剥离并报数量，不能把手动加分当关系学习成功。 |
| `reversal_audit`（撤销/重做审计） | source=undo，reason 指向被撤销日志；链路要在相同分区核查。 | 重建实际状态变动及失效关系；不能当新增互动或第二份同义证据。重做不清除原行 reversed 标记。 |

已撤销的行为原行仍保留来源标签，同时标 `reversed=true`，不再作为当前有效行为证据；撤销/重做链保留完整供审计。人工编辑产生的缺向量行优先归人工类，不能仅凭 '{}' 误分为旧数据。来源冲突或链路不可恢复时标完整性 `partial/unknown`，不为了凑齐四类而确定归因。

## A3 比较顺序与 E 的边界

1. 固定代码/策略版本、Bot、scope、群、成员、时间边界及数据库快照。建立已知源 ID 清单，记录所用 schema 和发生过的 reset/恢复；来源范围只能声称是该快照中保留的日志，不能暗示覆盖所有真实聊天。
2. 分页穷尽源集合，先比较 ID、数量、去重、稳定顺序、来源、实际向量、撤销标记及可用文本。页大小 20 与一次读取 206 条只是查询方法；两者来源行完全相同后才可比较下游处理。
3. 若比较完整历史与近期视图，明确近期视图的 count/days、候选读取窗口、显著性参数及排除规则。这是输入策略差异，不能直接命名为存储保留截断或证据物理丢失。
4. 处理人工和撤销链，再按 Golden Set 的身份、受话人、quote provenance、维度和诊断性标注比较。历史 trust 增量不能自动映射为可靠性方向。
5. 缺原始消息、缺 relation_delta、无法恢复 reset 前历史、来源不全或未标注时，E 为 `unknown`，分别报告原因。只有声明范围内来源完整且诊断性检查已完成、确无合格证据才是 `E=0`；零日志、零增量、零信任分都不是这个结论的充分条件。

来源完整性失败或未知时，记录该失败/未知，并限制后续结论。不得用默认值、当前分数或合成重建的消息补造“确定证据”；来源差异的诊断优先于质量均值。Phase0 验证这些存储事实；E 的派生与摘要比较留给 Phase3 离线实验，不宣称已实现生产证据量。

## 回归覆盖与复用

新增 `tests/test_research_history.py` 使用真实 SQLite，正常样本均由 FavorService 写入。唯一直接插行的夹具建立真实 v11 合成库，再经生产迁移打开；没有修改生产逻辑。

| 范围 | 覆盖 |
| --- | --- |
| A3 分页 | `test_a3_pagination_is_a_read_window_not_retention`：205 条跨越两个默认窗口；比较全行和稳定顺序，关闭重开后检查最老日志。 |
| recent_events | `test_recent_events_filters_without_deleting_history`：7 天普通窗口、14 天显著窗口、15 天源行、关闭过滤及重开。 |
| reset | `test_reset_explicitly_deletes_only_selected_history`：成员/群两个参数，目标日志确实删除，其他分区和成员按范围保留，重开复核。 |
| undo/redo | `test_undo_redo_preserves_actual_vectors_sources_and_reversed_marks`：饱和后实际向量、反向及重做向量、来源、原行标记、无重复行为 event_id、拒绝重复撤销。 |
| manual edit | `test_manual_edits_remain_distinct_from_judged_behavior`：judge/admin/admin_profile 来源并存，人工向量与标量编辑分别保留，资料编辑不变分。 |
| legacy | `test_legacy_missing_vector_stays_missing_after_migration`：旧日志缺失与新显式零向量不同，升级和重开不补造字段。 |
| 临时冻结与隔离（复用） | `tests/test_temporary_relations.py::test_expiry_update_freezes_decay_without_replaying`、`test_all_score_writes_and_old_generations_are_blocked`、`test_persistence_isolation_titles_and_cancel`。 |
| Bot/scope（复用） | `tests/test_storage_router.py::test_four_database_isolation_and_existing_storage_behaviors`；`tests/test_relation_storage.py::test_relation_persistence_scope_and_mixed_state`。 |

这些是存储与服务契约回归，不证明真实框架接入、真实群聊归因、真人金标质量或可靠性语义已通过。实际执行结果见 BASELINE.md，不能仅凭存在测试文件标为通过。
