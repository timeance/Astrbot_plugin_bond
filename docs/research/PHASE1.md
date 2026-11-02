# Phase1：礼貌增信与整句寒暄过滤

基于 Phase0 PR #16，独立堆叠提交。运行版本 0.2.2、schema 15；后续先审 #16，再审此阶段。不自动合并或发布。

## 改动与依据

thanks/friendly/apology 默认 T=0；F/A、JOKE、insult/manipulation、事件/每日限幅不变。基于 Phase0 同输入对照，无依据 Trust 更新从 422/1195 降为 5/1195。剩余 5 条来自既有归因/语义误判，不能说信任判断已全面正确。

A1 证实“早安😊”“早安～”绕过整句寒暄过滤；仅剥离字符串边缘有限的寒暄标点、表情和空白后比较寒暄词；保留代码括号、运算符、货币及其他符号，原文保持用于模型证据校验。带实质正文的问答、无法解释的 @ 显示名不猜测删除，不设置 F 永久封顶。

三种长序列共减少 232 次合成候选进入 Provider。该数量只来自固定场景，不能外推真实成本节约率。完全相同早安本来就过滤；200 天正常问答仍累计约 F20。

## 比较与验证

同一新入口下 current 与显式 Model A 的逐场景结果完全一致；与 Phase0 冻结 Model A 相比，只有 S19 三条长序列的 33/66/133 个实际事件增量由 F+.3/A+.8 改为零，没有新增错误持久化或 Trust 误更新。对照见 PHASE1_RESULTS.json。脚本按场景及序列位置对齐，保留重复消息，不按 message_id 覆盖。

```text
python scripts/replay_relations.py --arms baseline model-a current --output <空目录>
python scripts/compare_replays.py --left <Phase0>/results.json --right <Phase1>/results.json --output <差异文件>
```

| 项目 | Phase0 Model A | Phase1 current |
| --- | ---: | ---: |
| PFUR | 9/381 | 9/381 |
| 受话人误更新 | 3/8 | 3/8 |
| Trust 误更新 | 5/1195 | 5/1195 |
| 合成调用数 | 730 | 498 |

其余指标及未知项保留于结果；基于本次代码重跑的 baseline 只是旧规则配新过滤，不冒充完整旧版本。

实际执行：全量 pytest 452 passed；Black 26.5.1 通过；Node 19 passed；check_project 0.2.2 通过。独立 AstrBot 4.28.0 装配、17 条真实转换合成用例通过。提交后使用 build_package.py 从 HEAD 验证归档，SHA 与远端检查记录于 PR。

测试覆盖默认正负/中性事件、自定义覆盖/上限、重复/冷却/日限幅、原文证据、旧 schema 升级、重开、历史 Trust 保留与撤销、切回旧规则不重算。历史来源测试显式固定旧 thanks T+.2，继续验证旧流水，不让默认变化降低旧数据覆盖。

## 兼容、回滚和限制

RelationPolicy 的显式维度覆盖优先级保持；省略 trust 将继承新零值。当前主装配没有公开三维规则表配置入口，不新增 schema、迁移或隐藏切换开关。已有数值/流水不重算，撤销按旧实际向量。回滚代码恢复未来默认规则，不补发过去缺失的增信。

取消礼貌增信后尚无经过验证的自动 Trust 修复机制；实际改正识别、正向可靠性事件及序列修复继续搁置。亲近度经济规则的配置说明更正为实际的 affinity 增长抑制，不改键或数值。没有把人工编辑、衰减或停止再犯当自动恢复。

Phase2 处理已复现的结构归因冲突。纯文本引文、测试性注入误判、玩笑情境和负向诊断性仍需独立标注证据；真实 QQ/Provider/真人和浏览器未测。
