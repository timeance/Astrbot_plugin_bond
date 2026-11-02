# 决策与风险登记

日期：2026-11-02；源码行号与行为基于 `993e821bd127488820a0f639107f67c0c5402bf4`。标签区分 `confirmed in code`、`reproduced in tests`、`hypothesis`、`not applicable`；复现只证明给定合成输入下的程序行为。

| ID / 优先级 | 发现与证据 | 决策及恢复条件 |
| --- | --- | --- |
| T1 / 高 | confirmed in code：core/relation_policy.py:54–65 的 thanks/friendly/apology 分别 T+.2/.2/+1；reproduced in tests：S16/S17/S25/S26/S27 与 Model A。 | 采用 Phase1 默认 T=0；保留显式覆盖、F/A 和现有限幅。不调负权重。 |
| T2 / 高 | confirmed in code：同一默认表除三类礼貌外无正 T；S26/S27 仅区分已分类再犯，S48 未核实修复弃权。 | 采用过渡但公开无可靠自动修复；诊断性履约新类别需要标注、来源与独立协议评审。管理员设值不算自动修复。 |
| A1 / 中 | reproduced in tests：S18 的完全相同早安 50/100/200 天 F=0；S19 含表情/波浪变体可放行，单次 F=.3；S20 正常问答 200 次 F≈20。真实 AstrBot 转换脚本确认 message_str 的 @/标点/表情差异。core/assessment.py:78–86 只剥有限标点。 | 采用局部寒暄归一化候选，独立验证不会过滤实质内容；复用冷却/日限幅。拒绝“50天早安=F100”作为现状事实；推迟 F>=25 永久上限，不混同熟悉度和信息量。 |
| A2 / 中 | confirmed in code：RelationPolicy.delta 不读取状态；reproduced in tests：S11–S14 同一句话在陌生/亲近/疏远/恶意背景均 F+.2/A+.5，分类响应固定。 | 推迟到上下文/诊断性研究；不是模型准确率。拒绝以单一 F 阈值准许冒犯，不在 Phase1 改 JOKE。需要可靠归因、双方接受的上下文和盲评。 |
| A3 / 高 | confirmed in code + reproduced in tests：[HISTORY_AUDIT](HISTORY_AUDIT.md) 对应 7 项真实服务/SQLite 测试。分页/近期窗口不删除；reset 真删除；undo 与人工编辑不同；旧向量 {} 不等于零。 | 采用来源分类与完整性先验检查；Phase3 仅 shadow/replay。推迟生产 evidence 表/字段；需要证明相较 Model A 的误更新或真人表现收益、nullable/legacy_unknown 合同及完整迁移回滚。 |
| P2 / 高 | confirmed in code：api/listeners.py:78–93 任意匹配 @/Reply 即可；reproduced in tests：S04/S06/S07 的其他受话人、多目标和 Reply 来源缺失仍被更新。 | 采用 Phase2 本地冲突弃权；须测试成功指向、自身重复 @、其他 Bot、缺来源和混合顺序。不得从模型取身份。 |
| Q1 / 高 | reproduced in tests：S03 引文攻击和 S23 测试注入若两级 Provider 都误分类，仍可写入。core/assessment.py:49–75 只验证原文子串，不验证语义归属。 | hypothesis：最少上下文能改善真实误判；缺语义标注先搁置。严格输出结构不能证明引用正确，不扩大群聊监听。 |
| M1 / 已验证 | confirmed in code：main.py:296 的 self/known_bot/ignored_bot_ids 排除，api/listeners.py:96 的原文本和稳定 ID 冻结。reproduced in tests：S08/S09/S10/S39。 | 保留。未知机器人是否能从适配器识别需实环境验证；不声称排除任意未登记 Bot。 |
| R1 / 已验证 | services/assessment_service.py:137：默认关闭；小 Provider 必填；风险分类必复核，缺大模型/预算弃权，最终 confidence>=.8；core/assessment.py:89。 | 保留限制；0.8 是未校准建议阈值。拒绝将外部 .35/.60 当已校准概率。S29–S35/S46 覆盖故障和忙时。 |
| W1 / 已验证 | FavorService.apply_relation_event → SQLiteBackend.apply_relation_change，事务内写正式值、额度、日志、冷却与事件唯一键；S40/S41/S42。 | 保留唯一写入通道、WAL、幂等。不另造防刷概率引擎。 |
| O1 / 已验证 | temporary_state 与 revision 入场/提交检查；S36/S37 及既有临时关系测试覆盖撤销/到期/重启及旧任务禁止回写。 | 保留。不把临时期间消息补评，不把显示值当正式证据。 |
| I1 / 已验证 | RuntimeService.switch_mode + BotScopeStorageRouter 物理分库；S38/S39/S43，既有 runtime identity 测试。 | 模式切换不迁移合并，旧任务绑定旧服务。不扩展跨群个人资料读取。 |
| E1 / 中 | S45 阈值附近称谓反复变化；RelationResolver 是纯当前状态解析，无上一状态生命周期。 | 记录离线翻转量；推迟持久化滞回/冷却，需体验盲评、重启/临时状态合同及参数依据。 |
| N1 / 不适用 | not applicable：外部“没有 SQLite”“所有群消息同步调用模型”等说法与 storage、TaskRegistry、默认关闭及本地过滤冲突。 | 拒绝继承这些假设。未读到的原报告不补造引文。 |
| L1 / 不适用 | not applicable：LivingMemory 已移除；群友图、跨平台身份关联、权限/接话策略不在 Bond 关系功能边界。 | 不恢复、不实现，不以实验扩展生产采集范围。 |

## 后续阶段验收

1. Phase1：三类默认事件 T=0，显式自定义仍生效，旧数据可读可撤销；current 相较 Model A 不能新增错误持久化。A1 归一化保留有内容的长问答和单纯 @ 语义。
2. Phase2：S04/S06/S07 及扩展结构冲突零写入、零模型调用；清晰指向仍进入原异步链，Provider 错误不能突破本地受话人守卫。纯文本引用风险必须继续公开。
3. Phase3：至少比较 T=0 无可用来源、正负抵消、明确负证据、陈旧、legacy unknown；人工/撤销/reset 不得静默计自然证据。没有可证明的持久化收益则停止生产推进。
4. Phase4：公开接口无数值及私有证据，原人格与请求保留；主人仅称呼。无缺陷记录无改动，不制造空 PR。
5. Phase5：真实框架合成装配、Node 与既有组合测试；真实 QQ、真实 Provider、真人盲评、Dashboard 浏览器仍未验收。未执行项不能填通过。
