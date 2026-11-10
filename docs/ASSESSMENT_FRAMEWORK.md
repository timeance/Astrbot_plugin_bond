# 关系评估框架（0.7.0）

模型负责互动语义，框架负责可信来源、数值约束、调用预算与审计。关系计算使用熟悉度 F、亲近度 A、信任度 T，三维分别维护。日常操作见 [用户指南](USER_GUIDE.md)。

## 关系维护与单轮资料

`decay.maintenance_strategy=independent` 默认开启。资格按接纳事件的原始非零维度请求、现有倍率与规则、剩余每日额度判断，在精度归零前判定，再单独应用总值边界。达到数值上限可继续维护，额度耗尽或规则归零无法取得资格。`should_update=false`、失败、弃权、冷却和重复消息均跳过维护。普通问答只维护 F，亲近事件维护 A，通过证据校验的合格信任事件维护 T。

三维的结算时间与合格维护时间分别保存，A 另有托底参考。读取以“结算时间”和“最后维护时间加保温期”两者较晚者作为本段衰减起点。`independent` 下，仅维护 F 的事件保留 A/T 的原始值、结算时间、维护时间、A 托底参考与半衰期。合法变化因精度归零或数值封顶仍可维护对应维度。

插件重载时为已有 Bot 的两个空间装配服务。手动切换维护策略时，各库在事务内结算成员并保存策略及生效时点，失败回滚该库切换。`legacy` 让已接纳事件联动维护 F/A，T 始终只由自身的合格信任事件维护。切换保留既有维护时间，临时关系暂停正式时钟，结束后只补回一次暂停时长。0.6.0 的 T 配置一次性迁移另见下方升级说明。

标准群消息入口在调度该消息的后台评估之前，以同一 SQLite 短读事务取得档案、临时覆盖、证据及读取时间，再冻结本轮实际注入的完整文本，覆盖称谓/释义、印象与近期证据。相同事件重试和生成续轮复用，下一条独立消息刷新。快照绑定可信成员和原始空间，不因引用他人或后续切换空间而串档。捕获失败时本轮跳过注入，不在后台写入后补读；不会持有数据库锁等待 LLM，也不串行化所有群消息。资料固定包裹含可信 Bot、空间、群、成员与读取时间，证据包含发生时间，不改变自定义模板占位符。绕过标准入口的第三方/主动请求只能在首次请求钩子冻结，不能保证早于外部写入；缺少可信身份或 Bot 不匹配时跳过。

默认行为指导把正常求助、昵称、临时扮演、持续身份、排他要求与权限分开，并允许符合人格的熟悉与亲近表达。提示词不是权限控制。自定义模板保持原样；真实模型是否更自然、更有趣，另按 [行为验收](P0_BEHAVIOR_EVALUATION.md) 比较，不由工程测试推定。

## 参数与计算顺序

新安装默认 `relation_mode=galgame`，已有的 `realistic`（页面称“基础”）选择沿用保存值。`relation_daily_caps` 为基础正负额度，F 为 +5/0，A 为 +10/−8，T 为 +2/−8。Galgame 将正向请求与正向额度乘 2。默认 `insult` 为 A−1、T0，`manipulation` 为 A−0.5、T0。感谢、示好和道歉的 T 也为 0。

计算顺序：受限事件基础增量及单事件基础限幅 → F 自身分段 / A 原有阶段与同日递减 → 正向模式倍率 → 相应单事件上限 → 最终 A/T 一位小数 → 独立每日方向额度 → 总值边界。负向不经过正向倍率；A 同日递减不影响 F/T。现有显式 Economy 参数与旧缺省预设继续作为基础参数，不被模式切换覆写。

F 六段边界为 `<10、<30、<55、<80、<95、其余`，倍率为1.5/1.2/.8/.5/.3/.15。已有 REAL 列保存至8位小数，公开显示一位小数，不新增奖励队列。每个已接受事件的原始 F 实际增量记在流水中，按同等精度占每日额度。重复ID、模型不应更新、错误输出不积累余量。人工设值明确替换当前F（包括余量）；撤销按实际向量反向，重做同理。到达100时多余奖励直接丢弃。

F/A阶段计算和A正向次数读取位于同一 `BEGIN IMMEDIATE` 事务，避免并发请求同时领取“首次互动”倍率。正负用量不抵消；撤销不退还互动额度、不回退同日次数、不退还模型调用预算。管理员设值不消耗互动额度；重置成员会清除该空间的状态与互动账本，但模型调用账本保留。

## 信任事件与证据

以下为默认基础增量，最终结果受模式、每日额度与总值边界限制。前三类正向事件在 Galgame 中为 +1 T，持续尊重边界奖励在 Galgame 中为 +2 T，负向增量沿用表中数值。

| 事件 | 含义 | 基础 T 增量 |
| --- | --- | --- |
| `verified_correction` | 记录已独立确认的可核验纠错 | +0.5 |
| `promise_kept` | 有明确约定且记录确认实际履行 | +0.5 |
| `repair_action` | 记录确认失误后的实质修正 | +0.5 |
| `boundary_respected` | 14 天内至少 3 条独立的合格证据，持续遵守已明确的边界 | +1 |
| `boundary_violation` | 具体边界已明确传达，当前行为确实越界 | −1 |
| `repeated_pressure` | 明确边界后再次实质施压 | −2 |
| `deliberate_deception` | 记录与当前事实性主张明确矛盾，且有蓄意误导证据 | −4 |
| `severe_manipulation` | 有确凿证据的严重欺骗或强迫 | −6 |

自动信任事件要求 `assessment_mode=tiered` 且配置复核 Provider。初评与复核两次判断须同意事件类别和引用消息 ID，均接纳更新且置信度至少为 0.9。两次调用使用同一份冻结上下文与提示词。单模型模式、复核缺失、预算不足、超时或不一致时弃权。

当前证据必须是发言者本条消息的连续原文。`reference_message_id` 和 `reference_excerpt` 还须指向同 Bot、同群、同成员上下文中实际记录的 Bot 回复及其连续原文。Bot 复述用户自称、用户承诺或自报完成均无法替代独立确认。纠错、履约、实质修正等事项按同一 Bot 回复的证据锚点持久去重，跨消息、跨日或改换事件名共用该去重键。持续尊重边界另按实际行为来源去重，允许同一已明确边界后出现不同的独立行为。

持续尊重边界使用 14 天证据窗口，要求至少 3 条独立证据，每份成员关系每 5 天最多奖励一次，基础为 +1 T，Galgame 为 +2 T。证据须引用边界之后可独立核验的实际行为，并经初评与复核同意其来源和独立性。普通礼貌或重复转述按各自语义核验，同一行为的重复补述与已消费证据持久去重。奖励复用现有双模型核验、每日额度、流水与撤销流程，证据持久保存于 schema 20。该规则沿用现有信任维护资格与衰减参数。

引用存在与两次分类一致属于程序核验条件，现实中的履约、纠错和蓄意误导仍依赖语义判断。缺乏记录、归属不明或有歧义时应弃权，管理员可按流水证据复查与撤销。

## 三维衰减

当前默认启用三维衰减，F 半衰期为 30 天、基线 10，A 为 30 天、基线 0，T 为 60 天、基线 20。A 保留等级托底和显式巩固设置。共享保温期 `decay.grace_days` 默认为 5 天，仅超过保温期的时间参与衰减。两种增长模式使用同样的衰减设置。这些数值属于产品参数。

0.6.0 首次升级会统一覆盖五项 T 专属配置，包括旧版自定义值。F/A、共享保温期、关系模式与 Provider 设置沿用原值。升级成功后再保存的 T 设置会正常保留，详见 [信任配置与维护升级](MAINTENANCE_UPGRADE.md)。

基线为 0～100 的非负幅度。F 在 0～基线之间保持原值，A/T 在 −基线～+基线之间保持原值，范围外向同号基线收敛。半衰期减半的是与目标基线的差值。按默认 T 参数，保温期结束后 +40 经过 60 天约为 +30，120 天约为 +25，−40 分别约为 −30、−25。负信任随时间向基线幅度对应的负侧边界收敛。

面板按正在衰减的轴显示对应颜色的 F、A、T 字母；三轴均在衰减时只显示“衰减中”，临时关系优先显示“临时关系”。负 T 向基线恢复时仍属于 T 衰减中。状态按有效变化判断，最近互动与单纯离线天数分别显示。

自然衰减独立于互动扣分额度，F 的默认负向互动额度仍为 0。各维开关、半衰期与基线可在详细设置中分别调整。

F/T 的结算时间分别为 `familiarity_updated_at`、`trust_updated_at`，A 使用 `updated_at`。在独立维护策略的自动事件路径中，取得本维维护资格后才结算有效值、应用增量并刷新维护时间，精度归零和封顶同样按此处理。F 以未舍入的有效值选择增长分段，保存 8 位小数。调整半衰期或基线并重载后，从既有锚点按当前参数计算。F/A/T 的基线配置分别为 `decay.familiarity_baseline`、`decay.affinity_baseline`、`decay.trust_baseline`。撤销在当前有效值上反向应用实际事件增量。

## 调用与上下文

`assessment` 默认窗口300秒/5次初评，用户每日初评30/复核8，Bot每日初评300/复核100，并发2。初评和复核分别记账；复核属于同一互动，不额外授予初评机会。调用发起前持久保留，失败/超时/进程中断的已保留尝试仍占次数；缺少Provider、本地拒绝或预算不足不记未发起调用。旧 `cooldown_sec` 留档，不再参与自动评估阻断。

UTC日期调用账本与配置时区关系额度分开；滚动窗口可跨UTC零点。插件共享预算锁合计同Bot的两个数据库，数据库事务再次检查本库；稳定事件ID在两个空间的调用记录中去重。重启不清零调用账本。主动恢复旧备份会恢复当时账本，不构成连续调用审计。本版本不承诺多个独立AstrBot进程同时挂载同一数据目录的跨进程分布式预算。

上下文仅包含可信同Bot、同群、同成员的近期相关互动和必要Bot回复，以及同群已计分摘要。默认6条、4000字符、7天/8条摘要；内存会话数和条数另有硬上限，重载后近期未持久化对话不可恢复，已计分证据仍可读取。原群筛选在SQL分页之前执行；旧global流水没有可靠来源群时不加入上下文。新流水仅新增原群字段，不建立全群第三方会话存储。

初评和复核共享一次冻结的上下文和提示词快照。`should_update=false`表示模型认为当前不应计分；解析兼容旧三字段响应，但旧响应不提供显式新颖性判断。事件类别、有限置信度和当前连续原文证据继续强校验。上下文和提示词不能保证模型完全正确，真实模型效果必须独立评估。

## 关系称谓

固定词典是默认展示方式，共 76 词，覆盖三维各五档形成的 125 格主状态，并保留 20 处局部展示细分。初期可出现“眼熟之人”“浅交之人”“寻常相识”；“交浅却信赖”保留相识较浅与信任较高的组合。高 F、低 A、低 T 使用“疏远而戒备的熟人”。完整映射由 [关系词典](RELATION_LEXICON.md) 说明。

### 动态称谓与模型

`relation_title.enabled` 默认 `false`。开启后，实际群互动可触发后台刷新，使用当前会话完整的有效人格自由生成称谓，包含亲近却戒备等混合关系。生成与校验共同约束称谓符合当前三维事实、当前人格及允许的关系语义。

`relation_title.provider_id` 留空时跟随复核模型。两者均未配置时使用固定词典。单次调用默认超时 30 秒，生成失败或校验未通过时保留仍有效的缓存，否则使用固定称谓。按群空间允许参考本群有效印象，`global` 生成时省略聚合印象。缺少完整有效人格时也使用固定称谓。

本地格式校验、独立上下文复核和词典回退能限制错误输出与调用成本，不能保证模型理解完全正确。同一模型的生成和复核可能犯相同错误；当前工程测试使用合成 Provider，未证明大模型或小模型的实际通过率。启用前可用自己的模型检查陌生、亲近却戒备、疏远但信赖等代表状态。

### 触发、间隔与预算

实际互动时观察到任一 F/A/T 跨过 5 分边界才满足刷新触发条件，三轴分别计算 `floor(分数/5)`，负值同样适用。观察包含自然衰减带来的跨界。默认两次刷新尝试间隔为 12 小时，每 Bot 每个 UTC 日最多 30 次模型调用。个性化设置允许延长间隔或降低上限（0 暂停），不能低于 12 小时或超过每日 30 次；单次超时可另行调整。

一次刷新最多尝试 3 轮，首轮通过即结束。每轮的生成与校验分别记实际调用，失败、超时和重试同样计入，单次刷新可能消耗多次调用。称谓预算跨同 Bot 的群与数据空间合计、持久保存，独立于初评与复核预算。刷新使用插件共用的后台生成槽。

### 缓存与切换

查询和排行读取有效缓存，缺少缓存时显示固定词典。人格、三维五档语义、来源撤销、临时关系修订或称谓版本改变会使旧缓存失效，生成失败也不继续展示。仅跨过 5 分边界而五档语义未变时，可以保留仍适用的称谓等待刷新；普通印象更新不会立即作废称谓。缺少明确会话的管理页和跨插件只读接口使用固定称谓。

关闭动态称谓后停止生成，保留缓存。重新开启时复用仍有效的缓存；需要更新的记录继续显示固定称谓，等待下一次实际互动观察到跨界，并通过间隔与额度检查后再刷新。切换开关本身作为配置变更保存。

撤销流水后，保留旧印象原文供核对，清除其有效刷新时间；称谓生成在印象重新刷新或管理员确认前跳过这段旧印象。新印象可以正常使用。重置成员或群关系时清理对应缓存，已消费的模型调用预算保留。

## Schema 20 升级与回退

- 各 Bot 的 group/global 库逐版本升级至 20。每个版本的 DDL/DML 和版本号在同一迁移事务内提交，失败回滚至该版本起点。
- Schema 20 增加持续尊重边界所需的证据持久状态，仍沿用现有关系、预算和流水。
- Schema 19 相对 18 新增 `relation_title_cache` 与 `relation_title_calls`。前者保存称谓和校验信息，后者保存独立称谓调用预算。
- 0.6.0 的一次性 T 配置迁移保留原始 T、既有时间锚与历史流水。重载后直接按新参数计算有效值，显示值可能立即变化，省去旧规则预结算与过渡双算法。五项配置备份、内部升级标记与保存失败处理见 [升级说明](MAINTENANCE_UPGRADE.md)。
- Schema16新增每日六个方向累计、A正向次数和旧用量补齐标记、T独立时间锚、模型调用时间、流水原始群，以及窗口/事件/上下文查询索引。Schema17只新增F独立时间锚，不新增奖励队列或关系维度。
- 更早版本的迁移仍依次执行。Schema 16 为旧 T 初始化时间锚，17 为旧 F 初始化时间锚，18 分离结算与维护时间并增加 A 托底参考。跨升级的临时关系按原机制处理暂停时间。历史奖励沿用原记录。
- 旧每日净额先拆出能证明的方向用量；首次使用当天时按当前配置时区读取该日流水补齐正负总量（包括已撤销、曾实际消费的事件）。若历史日志缺失或曾使用其他时区，只能保留可证明用量下限，无法恢复丢失事实；不会伪造精确历史。次日新账本完整独立记账。
- 旧调用仍计UTC当日次数；无精确发起时间的旧调用不补造300秒窗口时间。
- 面板恢复沿用同schema备份校验，不直接导入旧版本备份；跨版本回退需恢复与备份匹配的代码和配置。
- 升级前停写并备份代码、宿主插件配置和整个插件数据目录，回滚时成套恢复。仅修改 `PRAGMA user_version` 无法让旧代码安全打开 schema 20。

## 眼熟模式

眼熟模式默认开启，已有开关选择保留。明确 @ 当前 Bot 时，默认选择触发前最近 10 分钟在本群发言的 3 位其他成员，每人最多增加 0.3 F，间隔至少 30 分钟，被动上限为 60。五项参数在熟悉度变化设置中调整；人数为 0 时不限人数，仍限制时间窗口。先选人，再检查冷却、额度与上限，按既有名单结算。

被动增长与直接互动共用每日 F 正向额度，Galgame 的总额度仍适用，单次被动奖励按其专属规则计算。被动增长保留三维维护时钟，F 高于被动上限后仍按正常规则衰减。触发者参加既有直接互动流程。仅回复 Bot、文字唤醒或 @ 他人时按各自入口规则处理。该模式只缓存可信成员身份与消息时间，重启后重新积累活跃窗口，已提交冷却与去重保留。流水来源为“群聊活跃”，同次触发合并展示并支持逐成员撤销。完整合同见 [被动熟悉度](PASSIVE_FAMILIARITY.md)。

## 验证入口

工程验证入口包括既有 pytest、Node 页面测试、Black/Ruff、工程与公开内容检查、打包门禁和独立 AstrBot 装配。信任与称谓的专项用例见 `tests/test_trust_v2.py`、`tests/test_title_service.py`，既有框架用例覆盖精度、额度、时间锚、并发、迁移及上下文预算。本说明列出验证入口，真实模型和线上效果仍需单独验收。

`python scripts/replay_framework.py --output <新目录>` 通过真实AssessmentService、FavorService和内存SQLite运行基础/Galgame每日1/5/30/60次候选互动，记录7/30/180天状态。模型响应预置且语义标签已知，不能报告真实误判率、真人体验或Token节约。实际结果见 [长期回放](research/ASSESSMENT_FRAMEWORK_REPLAY.md)，最终检查见验收记录。


## References / 参考资料

以下保留原 README 的 19 项文献、编号、用途与链接。来源核对日期沿用 2026-11-03。关系建模与合成实验见 [图文短论文](research/EMOTION_MODEL_PAPER.md)，原始输入、文献用途与未采纳结论见 [研究来源与证据边界](research/RESEARCH_SOURCES.md)。文献用于界定构念、比较机制和设计评价方法，实际分值、阈值及线上效果由产品规则和独立验收说明。

### 关系、情感与信任

<a id="ref-01"></a>

1. Berscheid, E., Snyder, M., & Omoto, A. M. (1989). **The Relationship Closeness Inventory: Assessing the Closeness of Interpersonal Relationships.** *Journal of Personality and Social Psychology, 57*(5), 792–807. [DOI: 10.1037/0022-3514.57.5.792](https://doi.org/10.1037/0022-3514.57.5.792)。用于区分互动接触与主观亲近。

<a id="ref-02"></a>

2. Laurenceau, J.-P., Barrett, L. F., & Pietromonaco, P. R. (1998). **Intimacy as an Interpersonal Process: The Importance of Self-Disclosure, Partner Disclosure, and Perceived Partner Responsiveness in Interpersonal Exchanges.** *Journal of Personality and Social Psychology, 74*(5), 1238–1251. [DOI: 10.1037/0022-3514.74.5.1238](https://doi.org/10.1037/0022-3514.74.5.1238)。用于理解互动质量与回应性，不能据此推断用户内心。

<a id="ref-03"></a>

3. Mayer, R. C., Davis, J. H., & Schoorman, F. D. (1995). **An Integrative Model of Organizational Trust.** *Academy of Management Review, 20*(3), 709–734. [DOI: 10.5465/amr.1995.9508080335](https://doi.org/10.5465/amr.1995.9508080335)。用于区分信任与喜欢、礼貌等正向态度。

<a id="ref-04"></a>

4. Lewicki, R. J., McAllister, D. J., & Bies, R. J. (1998). **Trust and Distrust: New Relationships and Realities.** *Academy of Management Review, 23*(3), 438–458. [DOI: 10.5465/amr.1998.926620](https://doi.org/10.5465/amr.1998.926620)。用于讨论矛盾信号及单一信任标量的表达限制。

<a id="ref-05"></a>

5. Kim, P. H., Ferrin, D. L., Cooper, C. D., & Dirks, K. T. (2004). **Removing the Shadow of Suspicion: The Effects of Apology versus Denial for Repairing Competence- versus Integrity-Based Trust Violations.** *Journal of Applied Psychology, 89*(1), 104–118. [DOI: 10.1037/0021-9010.89.1.104](https://doi.org/10.1037/0021-9010.89.1.104)。用于区分不同违背类型的修复条件，不采用固定“道歉恢复率”。

<a id="ref-06"></a>

6. Lewicki, R. J., & Brinsfield, C. (2017). **Trust Repair.** *Annual Review of Organizational Psychology and Organizational Behavior, 4*, 287–313. [DOI: 10.1146/annurev-orgpsych-032516-113147](https://doi.org/10.1146/annurev-orgpsych-032516-113147)。用于区分言语回应与持续行为修复。

<a id="ref-07"></a>

7. Russell, J. A. (1980). **A Circumplex Model of Affect.** *Journal of Personality and Social Psychology, 39*(6), 1161–1178. [DOI: 10.1037/h0077714](https://doi.org/10.1037/h0077714)。用于区分当前情感体验与长期关系构念。

<a id="ref-08"></a>

8. Baumeister, R. F., Bratslavsky, E., Finkenauer, C., & Vohs, K. D. (2001). **Bad Is Stronger than Good.** *Review of General Psychology, 5*(4), 323–370. [DOI: 10.1037/1089-2680.5.4.323](https://doi.org/10.1037/1089-2680.5.4.323)。用于讨论正负影响的不对称性，不提供统一扣分倍率。

### 人机关系

<a id="ref-09"></a>

9. Bickmore, T. W., & Picard, R. W. (2005). **Establishing and Maintaining Long-Term Human-Computer Relationships.** *ACM Transactions on Computer-Human Interaction, 12*(2), 293–327. [DOI: 10.1145/1067860.1067867](https://doi.org/10.1145/1067860.1067867)。长期关系型 Agent 的设计与评价背景。

<a id="ref-10"></a>

10. Skjuve, M., Følstad, A., Fostervold, K. I., & Brandtzaeg, P. B. (2021). **My Chatbot Companion – a Study of Human-Chatbot Relationships.** *International Journal of Human-Computer Studies, 149*, 102601. [DOI: 10.1016/j.ijhcs.2021.102601](https://doi.org/10.1016/j.ijhcs.2021.102601)。一对一聊天机器人关系的定性研究背景。

<a id="ref-11"></a>

11. Skjuve, M., Følstad, A., Fostervold, K. I., & Brandtzaeg, P. B. (2022). **A Longitudinal Study of Human–Chatbot Relationships.** *International Journal of Human-Computer Studies, 168*, 102903. [DOI: 10.1016/j.ijhcs.2022.102903](https://doi.org/10.1016/j.ijhcs.2022.102903)。用于提醒长期关系体验存在个体差异，不把固定阶段当作普遍规律。

### 多方对话、上下文与保守判断

<a id="ref-12"></a>

12. Ghosh, D., Fabbri, A. R., & Muresan, S. (2018). **Sarcasm Analysis Using Conversation Context.** *Computational Linguistics, 44*(4), 755–792. [DOI: 10.1162/coli_a_00336](https://doi.org/10.1162/coli_a_00336)。用于说明孤立句子不足以可靠识别反讽。

<a id="ref-13"></a>

13. Inoue, K., Lala, D., Elmers, M., Ochi, K., & Kawahara, T. (2025). **An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue.** *Proceedings of the 15th International Workshop on Spoken Dialogue Systems Technology*, 330–334. [ACL Anthology](https://aclanthology.org/2025.iwsds-1.36/)。用于支持独立处理受话人归因；原实验并非 QQ 文本群聊。

<a id="ref-14"></a>

14. Gao, J., Lian, Y., Zhou, Z., Fu, Y., & Wang, B. (2023). **LiveChat: A Large-Scale Personalized Dialogue Dataset Automatically Constructed from Live Streaming.** *ACL 2023, Volume 1: Long Papers*, 15387–15405. [DOI: 10.18653/v1/2023.acl-long.858](https://doi.org/10.18653/v1/2023.acl-long.858)。中文多方对话与受话人识别的外部数据参照。

<a id="ref-15"></a>

15. Geifman, Y., & El-Yaniv, R. (2017). **Selective Classification for Deep Neural Networks.** *Advances in Neural Information Processing Systems, 30*. [会议论文](https://papers.nips.cc/paper_files/paper/2017/hash/4a8423d5e91fda00bb7e46540e2b0cf1-Abstract.html)。借鉴“允许弃权”的风险与覆盖率权衡，不声称 Bond 实现了论文的统计保证。

<a id="ref-16"></a>

16. Sharma, M., Tong, M., Korbak, T., et al. (2024). **Towards Understanding Sycophancy in Language Models.** *ICLR 2024*. [会议论文](https://proceedings.iclr.cc/paper_files/paper/2024/hash/0105f7972202c1d4fb817da9f21a9663-Abstract-Conference.html)。用于说明模型建议仍需校验，不推导“夸奖用户必有恶意”或通用错误率。

### 记忆与生成式 Agent：比较背景

<a id="ref-17"></a>

17. Zhong, W., Guo, L., Gao, Q., Ye, H., & Wang, Y. (2024). **MemoryBank: Enhancing Large Language Models with Long-Term Memory.** *Proceedings of the AAAI Conference on Artificial Intelligence, 38*(17). [DOI: 10.1609/aaai.v38i17.29946](https://doi.org/10.1609/aaai.v38i17.29946)。记忆更新的比较背景；记忆遗忘机制不能直接当作关系衰减定律。

<a id="ref-18"></a>

18. Wang, Z., Chiu, Y. Y., & Chiu, Y. C. (2023). **Humanoid Agents: Platform for Simulating Human-like Generative Agents.** *EMNLP 2023: System Demonstrations*. [ACL Anthology](https://aclanthology.org/2023.emnlp-demo.15/)。情绪与关系分层的模拟架构参照；Bond 未实现其情绪或需求系统。

<a id="ref-19"></a>

19. Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S. (2023). **Generative Agents: Interactive Simulacra of Human Behavior.** *UIST 2023*. [作者预印本](https://arxiv.org/abs/2304.03442)。记忆、反思和生成行为的架构参照；不作为 Bond 群聊效果或 Token 节约的证据。


注入位置的取舍见 [注入方式评估](research/INJECTION_REVIEW.md)；大量成员的计算开销与实测范围见 [页面合成负载记录](research/DASHBOARD_LOAD.md)。
