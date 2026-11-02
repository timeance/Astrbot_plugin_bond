# 剩余关系证据：最终落分候选的离线对照

日期：2026-11-02。指定基准 main d287221c241fe88740ab2186a8ffa63676d90dcf，独立工作树分支 test/remaining-relation-evidence。只新增研究脚本、测试、新夹具及本说明/REMAINING_RESULTS.json，不修改生产文件、既有夹具或 gold。

实验复用 tests/research_support.py 的真实消息钩子、AssessmentService、FavorService、SQLiteBackend 和重开校验；框架消息组件与 Provider 是原有离线替身，不是真实 QQ 或完整 AstrBot 装配。候选只在内存中给真实 assess 最终弃权条件追加 gate，位置在大小模型复核之后、apply_relation_event 之前。优先选择AssessmentService._assess，缺少时选择assess；AST要求唯一同名异步方法、唯一最终uncertain条件及原abstained状态结构，按选中的方法名取编译namespace并patch同名方法，退出后恢复原方法。最终分支结构变化时拒绝注入。另用内存中将真实主体移为_assess、assess仅转发的包装结构回放20条新样本，逐项状态/增量/Provider轨迹与原结构一致，且包装函数未被替换。这只验证指定包装结构；未在两PR实际合并的工作树运行，不能宣称合并后完整兼容。

各臂继续使用同一 SYSTEM_PROMPT、原始平台身份和正文、同一预置 Provider 响应。审计逐次核对提示词恰为原模板及 target_bot/sender/text，记录真实替身 completion_text；原集和新集分别比较完整调用轨迹指纹，四臂均一致。gold、repair annotations 不进入 Provider，也不参与 gate 决策。默认 confidence=0.95 仍是替身常量，不是实测可信度。

## 原始 1201 条：固定分母与弃权代价

未改变的原集包含1201条事件，其中不应更新381条、应更新820条。基准实际写入的应更新事件为447条；报告同时保留“全部应更新”与“基准已实际写入”两种分母，不用 accepted 状态次数替代实际增量。Trust 应为零1195条、应为负4条、未定2条、正向修复0条。原始 canonical fixture SHA-256 与冻结 Phase2 一致。

| 臂 | PFUR | 比基准少的错误更新/381 | 应更新被抑制/820 | 基准应更新写入被抑制/447 | Trust误更新/1195 | 预期负向Trust被抑制/4 |
| --- | --- | --- | --- | --- | --- | --- |
| control | 6/381 | 0/381 | 0/820 | 0/447 | 2/1195 | 0/4 |
| quoted_evidence | 4/381 | 2/381 | 0/820 | 0/447 | 1/1195 | 0/4 |
| joke_abstain | 3/381 | 3/381 | 1/820 | 1/447 | 2/1195 | 0/4 |
| manipulation_no_source | 5/381 | 1/381 | 4/820 | 4/447 | 1/1195 | 4/4 |

三个候选分别与 control 比较，没有叠加成联合臂；减少量不能直接相加作为联合实测结果。各臂未新增错误更新，Provider调用均为492次；最终落分 gate 不节省既有模型调用。

- quote：仅改变 S03、S47；引用误更新2/4降为0/4，受话人误更新仍为0/8。S03的Trust误扣被挡住，S23仍在。
- joke：S11/S13/S14三条错误更新全部被挡（3/3），S12一条预期更新也被挡（1/1）。四条Provider payload相同，S12/S14连初始F/A/T也相同；缺少可见玩笑边界，不能宣称关系阈值能可靠区分。
- manipulation：S23错误扣信被挡（1/1），同时挡住S24、S26第一步、S27第一及第三步四条预期负向Trust（4/4）。真实入口没有独立诊断来源字段，本臂将其记为 unavailable；没有把 gold.trust_diagnostic 或模型类别升级为来源。这是来源不足时的策略对照，不是测试意图识别器。

S22/S23正文、身份与Provider payload相同，预置响应分别为 uncertain/manipulation；两条gold都不应更新。该差别只能演示错误分类后的路由，不能估计真实模型分类率。

## Quote规则与20条独立新样本

最小支持中文双引号“”及ASCII双引号""。扫描原文，不改原文或Provider evidence；找出evidence全部出现位置（含重叠匹配）。任一出现位置与引用内容区间重叠，或证据跨越引用及外部正文，就保守弃权，不能通过只选第一个外部匹配绕过。嵌套、未闭合、错配、转义ASCII引号，以及未支持的「」/『』均记句法不确定并弃权，不推断作者。

“引用区间”仅指受该句法界定的字符区间。强调引号、代码/JSON字符串同样可能命中；不能从引号认定第三方作者，也不能从未命中推断发言者认同或陈述属实。

新集单列20条，不并入1201/381/1195：

- gold不应更新8条：control错误更新8/8，quote后0/8。
- gold应更新12条：control实际更新12/12，quote保留7/12、抑制5/12。
- quote共弃权13/20；joke/manipulation臂在这20条上没有对应类别，结果与control相同，不能拿来证明它们的识别效果。

| 被抑制的应更新新样本 | 最终证据与原因 |
| --- | --- |
| Q03-duplicate-inside-outside | 先直接感谢、再引用同一句感谢；evidence“谢谢你”同时出现在内外，全部匹配策略弃权。 |
| Q11-author-emphasis | 发言者用ASCII双引号强调自己的感谢；evidence被引号包围，仍被保守拒绝。 |
| Q13-wide-evidence | evidence覆盖含“早安”引用及独立感谢的整句，跨越引用与自述，仍被拒绝。 |
| Q17-explicit-self-quote | 正文明确为“我想亲口对你说：“谢谢你””；固定thanks证据在自引区间，合理感谢被拒绝。 |
| Q19-code-question-inside | 正常JSON提问，固定normal_question证据选中代码字符串“谢谢你”，正常问答的熟悉度更新被拒绝。 |

其余通过的应更新对照是直接感谢、直接批评、引用之外的独立感谢、复核后仍在引用之外的批评、两次外部重复感谢，以及JSON/Python正常提问的引号外证据（Q05/Q06/Q07/Q08/Q16/Q18/Q20，共7/7）。Q04的小模型证据在引号外，大模型最终返回引号内感谢；实际记录最后类别为thanks并弃权，证明检查的是复核后的最终证据。Q09/Q10/Q14/Q15覆盖嵌套、未闭合、未支持符号与转义；Q12覆盖JSON代码字符串。

Q18/Q19使用同一句正文、相同normal_question类别、相同gold，分别预置引号外提问证据和字符串内证据；这是两条事先固定的新控制，所有研究臂各自保留同一响应，没有为候选改Provider响应。Q17明确自引感谢，Q20为print字符串的正常输出提问。四条控制在control中均实际更新，quote只保留Q18/Q20；仅凭引号位置无法分辨自引感谢、代码字符串与第三方转述，不能给命中项推断作者。

验收输入为冻结原集1201条、新夹具20条及六条repair来源合同。路由合同验收：quote在原集只改变S03/S47，三个原分母保持不变；新集8/8错误写入被挡、7/7可保留对照继续落分，如实报告5/12合理更新被抑制，逐臂Provider调用轨迹不变。这些合同通过只证明固定输入下的真实服务路由。生产接入验收要求自引感谢和正常代码提问等合理互动不因引号句法被误挡；Q17/Q19已给出反例，故该项不通过，搁置生产接入。这里的8/8、7/7、5/12均是人为固定合成合同，不是抽样准确率。

## 六条repair来源关联只读合同

复用既有 shadow.run_case，经真实FavorService生成隔离SQLite流水；normal_question只作T=0的来源载体。建夹具会写合成库，随后来源派生和关联分析只读。负行为/修复的语义及 independent_confirmation 都是独立合成标注，只示范信息上界，不是生产可知事实。

关联检查先验证来源仍保留、完整Bot/scope/group/user分区、judge自然来源、未撤销、修复时间在原行为之后，再看外部确认及正负标签。人工/撤销/缺源/跨区不能靠正标签变成自然修复。按完整event_key关联并输出log引用，绝不只按跨库重复的裸ID合并。公开来源列表把合成时间写为UTC ISO日期，来源SHA仍基于原始数值字段；这是报告格式转换，既有号码扫描规则保持不变。

| 合同 | 结果 |
| --- | --- |
| valid_same_partition | 同分区、保留且未撤销的两个来源满足时序与外部合成标签；仅上界eligible=true。 |
| verbal_only | 两来源存在，但没有独立修复确认，拒绝：verbal_claim_only。 |
| reset_missing | 原来源经真实reset消失，拒绝：missing_retained_source；不补造历史。 |
| manual_source | repair来源是管理员设值，拒绝：manual_or_audit_source。 |
| reversed_source | 原来源经真实undo标记已撤销，拒绝：reversed_source。 |
| cross_partition | 引用另一群来源，即使可在库存中找到也拒绝：cross_partition。 |

合同6/6通过：上界有效1/1，拒绝5/5；来源派生零SQL写入6/6，关联函数输入内容指纹不变，六例正式Trust均为0。既有shadow的13场景及五项来源/内容/计数/归属/零写入检查仍各13/13。关联函数仅消费已读取的来源，不持有数据库写入句柄。

生产修复正例分母是0，报告为0/0、value=null。semantic_gain、persistent_false_update_gain、semantic_accuracy仍为null。没有增加生产修复类型、证据schema、迁移、配置键或Trust更新路径。不能证明真实修复事实、恢复量、恢复时间，或摘要优于计数。

## 复现与产物

以下命令在指定独立工作树执行，使用本任务offline-env/Scripts/python.exe。原始输出目录已保留；复跑时另选新的remaining-前缀目录，脚本拒绝复用非空输出目录。

~~~powershell
$sessionRoot = '<本次会话目录>'
Set-Location (Join-Path $sessionRoot 'remaining/astrbot_plugin_bond')
$python = Join-Path $sessionRoot 'offline-env/Scripts/python.exe'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = Join-Path $sessionRoot 'remaining-temp'
$env:TMP = $env:TEMP
$env:BLACK_CACHE_DIR = Join-Path $sessionRoot 'remaining-black-cache'

& $python -B -m pytest -q -p no:cacheprovider "--basetemp=$sessionRoot/remaining-pytest-03" tests/test_attribution_candidates.py
& $python -B scripts/replay_attribution_candidates.py --output "$sessionRoot/remaining-attribution-run-03"
& $python -B -m black --required-version 26.5.1 --check scripts/replay_attribution_candidates.py tests/test_attribution_candidates.py
~~~

实际结果：新增测试30 passed in 30.28s；CLI退出0；Black 26.5.1检查退出0，2 files would be left unchanged。主代理在最终独立分支重新运行全量测试：593 passed in 55.00s；Black检查80文件、Node19项与check_project通过。隔离AstrBot4.28.0合成装配通过，24个真实转换元数据用例通过；均不连接真实平台或Provider。

命令产物在会话根remaining-attribution-run-03：results.json包含四臂逐事件真实增量、状态、调用轨迹、gate理由及来源引用；summary.json保存完整分子/分母和源码/夹具指纹；runtime保存隔离合成数据库。主代理独立回放的初版summary与前次逐字节一致。公开时间改为UTC日期后，再次运行scripts/replay_attribution_candidates.py --output <另一新目录>，退出0；除时间表示和对应源码摘要外，报告与初版一致。当前REMAINING_RESULTS.json与这次最终summary.json逐字节一致，不将运行数据库或完整回放产物放进仓库。

测试覆盖20条句法合同、原集实际增量与分母、两级最终证据、Provider轨迹不变、源文件不变、同输入语义冲突、assess/_assess包装结构、六条repair及既有13条shadow来源检查。所有生产文件及原始夹具/研究支持文件的前后SHA-256相同。

## 接入建议与限制

搁置quote gate的生产接入。原始合成集少两次误写且未抑制原集应更新项，但新合同出现5/12合理更新被抑制，包括明确自引感谢和正常代码提问。引号区间是可可靠复算的句法事实，第三方归属、作者意图和互动类别是需要独立语义标注的假设；前者不足以代替后者。不能以字符串或感谢关键词的临时豁免掩盖反例。脚本保留为离线代价对照，未来若重新研究，应先取得另行许可的作者/受话人/引用边界标注，并提出可在这些正常控制上通过的候选。

不建议把joke全部弃权或manipulation来源不足弃权包装成语义修复；前者已损失1/1预期玩笑更新，后者已损失4/4预期负向Trust。它们是测量策略代价的研究臂。

没有真实Provider、真人盲评、QQ群、Angel Heart同装或浏览器验证。词典不在本实验范围；真实上下文能否补足语义、引号外的无标记转述、可靠来源如何获得、自动Trust修复收益均未知。本阶段只交付可复跑实验与搁置结论，不将研究臂作为生产默认机制。
