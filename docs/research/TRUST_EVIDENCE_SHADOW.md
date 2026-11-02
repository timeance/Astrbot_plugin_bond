# Trust 离线流水派生与合成标注上界

日期：2026-11-02。Phase0 基点 `db0624139b177cb3fc70047fd214ac564c75a5d5`，本实验基于该工作树的真实 FavorService、SQLiteBackend 和迁移代码。实验只增加脚本、测试、合成夹具与结果说明；不修改生产、schema、版本或评估协议，不调用 Provider。

## 要比较什么

先问现有流水能确定派生什么，再比较同一来源上的离线计数和确定性摘要。这里的 B-lite 只指带完整来源引用的计数列举，没有模型、概率、语义提取或自动更新。

现有日志可以确认：保留行、分区、时间、实际变化向量、写入来源、撤销标记。它没有独立的可靠性诊断性、事实核验、完整作者/受话人或 quote provenance。生产可知的可靠性正负计数统一为 `null`，历史证据状态为 `unknown`；不能用历史 T 正负、当前 T、没有日志或显式零向量补出诊断语义。

已有代码依据沿用 [历史审计](HISTORY_AUDIT.md)。本实验的正常数据全部经 FavorService 写入；唯一直接插行的来源夹具先按生产 MIGRATIONS 建立 v11 库，再由 SQLiteBackend 升级，不修改迁移。合成身份为数字代号，没有真实聊天或用户数据。

实验夹具完整复制 Phase0 `scenarios.json` 的 `baseline_rules`（9 类事件、每类 F/A/T），以 `RelationPolicy(rules=historical_rules)` 显式覆盖默认规则。它只构造旧历史，不代表当前生产自动增信；后续默认 thanks/apology 等规则变化不应改变本实验。报告输出完整规则、来源基点与 `historical_rules_sha256`，整体夹具另有指纹。

## 同源方法

1. 冻结壁钟，各场景使用新的隔离 SQLite。通过真实服务制造事件、人工设值/资料、undo、reset；另覆盖旧字段、衰减与 Bot/scope/群/成员隔离。
2. 分页穷尽保留流水，以 `bot/scope/group/user/log/id` 标识来源，另保留有分区的 event_id 引用。ID 在不同库重复不合并；全局群键规范为 `__global__`。
3. 原始保留行和内容 SHA-256 同时交给计数与摘要。两者都保留全部来源引用；人工、撤销、陈旧、旧字段和未知项分别列出，不能只列有利样本。
4. 来源归类优先于合成标注：人工和撤销审计不变成自然行为；被撤销的原行不再计有效行为；缺向量或稳定事件 ID 的来源保持 legacy_unknown。30 天窗口包含截止时刻，更早的行为标 stale，来源仍保留。该窗口是实验参数，不是经验证的心理学半衰期。
5. 计数独立累加类别；摘要列举类别及其来源引用。检查同源引用、内容指纹、计数与枚举、全部行归属以及分析前后 SQLite total_changes。它们只证明表示一致性，不能当准确率或收益率。

`known_logged_trust_delta_sum` 仅对确有 trust 字段的日志向量求和，包含人工与撤销；缺字段引用另列。它不是完整 T 账本、可靠性证据数量或当前 T 的重建。衰减后的有效亲近度与日志实际增量分别记录，不能把等待带来的读值变化计为事件写入。

## 合成标注上界

独立标签在 `tests/fixtures/research/trust_evidence_shadow.json` 的 annotations 中，明确标记 `synthetic_external_annotations_not_production_observable`。标签不传入 FavorService 的事件内容或任何 Provider，只有只读派生完成后才用于上界对照。

合成设定可以明确某来源为 positive、negative 或 nondiagnostic，但这些不是当前生产可知事实。部分标注事件用现有 normal_question 作为兼容写入载体，使 T=0 的真实流水也能在独立标注下出现正/负来源；这不代表生产已会识别履约或违约。未标注来源为 unknown，绝不由旧 T 符号补标签。

没有对应来源的标签不能造行。reset 后标签所指日志缺失；隔离场景的其他分区标签不属于当前选择。两者均在 annotations_without_retained_source 中明确列出，但该列表本身不证明删除原因；只有合成操作记录告诉我们本例执行过 reset。

计数中的 0 仅指当前选定、保留、窗口内且通过来源约束的相应条目为零，不证明完整历史 E=0。摘要不恢复 reset 前内容；被撤销来源的重做语义也不在本实验中自动重建。

## 场景与判断边界

| 场景 | 要验证的差别 |
| --- | --- |
| no_evidence | 无保留来源；不能从 T=0 宣称无历史证据。 |
| unannotated_nonzero_t / nondiagnostic | 同样真实 T+.2，独立标注的未知与非诊断性不同；旧数值不产生诊断标签。 |
| numeric_cancellation | 真实 T+1/-1 抵消为零，但无独立标注，语义仍未知。 |
| labeled_cancellation / labeled_negative | 同样当前 T=0，上界可分别有正负各一或明确负一；差异来自外部合成知识。 |
| stale | 旧来源被窗口排除但引用未删除；测试截止边界。 |
| legacy_unknown | 旧 relation_delta={}、event_id=NULL 与显式零向量不同。 |
| manual | 人工 T=40 与资料审计不能因外部正标签而计自然正证据。 |
| undo | 原行为 reversed，反向流水是审计；两条来源不能变两份自然证据。 |
| reset | 来源确实被服务删除；旧标签缺源，不补造历史。 |
| isolation | 同 event_id 分布于不同 Bot、scope、群、成员，共五份来源；选定对象只能计自己的一份。 |
| decay | 有效 A 随等待变化，两条自然事件实际向量仍为零；不把 decay 当新写入。 |

## 运行与复核

在包目录运行，使用项目锁定版本的 Python 3.12 / pytest / Black。输出目录必须是新的空路径，重用目录会拒绝，不删除已有数据。运行数据库保存在指定输出目录；测试临时数据位置由 `--basetemp` 指定。

```powershell
python -B scripts/replay_trust_evidence.py --output <output-dir>
python -B -m pytest -q -p no:cacheprovider --basetemp=<pytest-temp-dir> tests/test_trust_evidence_shadow.py
python -B -m black --check scripts/replay_trust_evidence.py tests/test_trust_evidence_shadow.py
```

脚本输出 REPORT.md、results.json 及 runtime 内的合成数据库。完整 JSON 含原始保留流水、分区引用、内容指纹、分类排除项、逐步正式状态和检查的分子/分母。小汇总保存在 [实验结果](TRUST_EVIDENCE_SHADOW_RESULTS.json)。分母是固定合成场景数，不是聊天数量、真人分布或独立统计样本。

针对性验证：`12 passed in 1.67s`（只运行本文件，包含将生产默认规则全部置零后，历史流水与报告仍完全一致的检查）；CLI 成功输出 13 场景；同源、同内容、计数与枚举、来源归属及分析零写入五项检查均为 `13/13`。两个 Python 文件通过 Black 26.5.1 格式检查。历史规则 SHA-256 为 `6df3f908317b211cd5e7ffe825ca4a1bb7bacc4751150b5bda982242ccb4c7f9`。主验证另执行全量443 passed、Black76文件、Node19项、工程门禁；独立AstrBot4.28.0装配与17条转换通过。将三个研究执行文件加入独立Phase2检出后，全量563 passed；13场景的来源和结果与本研究分支完全相同，仅生产源码指纹不同。主验证CLI完整JSON也与先前run-b一致。

## 决策

来源标注充分的合成上界能区分“零标量下的无保留来源、抵消与负来源”；计数和列举摘要可以保持相同来源。这只是可追踪表示能力，不证明生产日志已经具有这些诊断语义，也不证明摘要优于计数。

语义收益与持久化误更新收益均为 `null`。本设计不发出更新建议、不向生产写入，因此不能测出持久化误更新收益。结论为 `stop_no_proven_production_benefit`：停止生产推进，不提出 evidence schema、迁移或 schema RFC。
