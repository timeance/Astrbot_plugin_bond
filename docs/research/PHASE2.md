# Phase2：可信元数据冲突时弃权

基于 Phase1 PR #17，审核顺序为 #16 → #17 → 本阶段。版本 0.2.3，schema 15 不变。

## 现状失败与最小修正

Phase0 已复现 S04（回复他人但同时 @ 当前 Bot）、S06（多目标）、S07（引用来源不明但同时 @ Bot）持久化误更新。旧 _chain_flags 对任意 self @/Reply 返回真，随后用 OR 入场。两级模型都误分类时，本地没有冲突否决。

新规则只读取 AstrBot 的类型化 At/Reply：
- self ID 必须符合已有可信账号格式；任意 @ 对象不是当前 Bot（包括 all、缺失）则整体不进入评估。
- Reply 作者必须是当前 Bot，且来源 ID 存在、非零；来源 ID 不生成、不从正文/昵称/旧 qq 字段猜测。
- 多个不同来源 Reply 弃权；一致重复来源或重复 @ self 保留。有效 int/string ID 同等处理。
- 无明确指向返回 false；清晰 @ self、Reply self 或一致组合继续使用原异步链。

不修改原消息、模型协议或关系写入通道，不读取额外上文，不扩大群聊采集或跨群读取。不改变宿主接话；入口仍可沿原流程更新成员显示名或记 filtered 状态，零改分不等于数据库完全没有记录。

## 同输入差异

```text
python scripts/replay_relations.py --arms model-a current --output <Phase2空目录>
python scripts/compare_replays.py --left <Phase1>/results.json --left-arm current --right <Phase2>/results.json --output <差异文件>
```

与冻结 Phase1 current 比较，只改变 S04/S06/S07 三条事件。每条不再调用 Provider、不再写入关系增量。没有新增错误持久化或 Trust 误更新。详细源码、规则、分母和三条前后向量见 PHASE2_RESULTS.json。

| 合成指标 | Phase1 | Phase2 |
| --- | ---: | ---: |
| PFUR | 9/381 | 6/381 |
| 受话人误更新 | 3/8 | 0/8 |
| Trust 误更新 | 5/1195 | 2/1195 |
| Provider 调用（含复核） | 498 | 492 |

当前实验臂与同入口 Model A 一致；旧规则 baseline 配新入口不能代表完整旧版本。分母固定来自合成合同，不作为真实模型准确率。目标精度只衡量合成准入；纯文本引文可能指向 Bot 却被错误归给发言者，另用 quote_false_update_rate 报告，不能被 0/8 掩盖。

## 验证、兼容与回滚

新增 99 项参数化元数据合同，覆盖组件顺序、int/string、重复、all、多目标、来源缺失/冲突、伪装组件和正文身份；真实框架消息转换扩至 24 条。服务回放断言结构冲突零调用零增量，原忙时、超时、预算、临时覆盖、重启/旧任务身份冻结继续验证。

实际执行全量 pytest 551 passed、Black 通过、Node 19 passed、check_project 0.2.3 通过。独立 AstrBot 4.28.0 装配及 24 条转换检查通过。提交后归档门禁与远端当前 SHA 检查记录在 PR。

没有 schema、配置键或协议迁移，旧库继续可读/审计/撤销；取消该代码提交即恢复旧入口行为，不对过去关系自动补评或回填。已有错误可经管理员审阅流水后撤销，不批量修改历史。未知 Bot 只有在运行时已知账号/忽略列表中才会被现有主入口排除；不声称识别所有未登记机器人。

## 搁置项

S03/S47 纯文本引用和 S23 测试性注入误分类仍可能产生错误长期更新；结构化输出及原文子串验证不解决语义归属。没有扩大上下文、引入新置信阈值或玩笑状态门控。需要获许可标注集与最少上下文对照才能继续，不能从本实验推断真人效果或生产就绪。真实 QQ、Provider、Angel Heart 同装、浏览器和真人盲评均未执行。
