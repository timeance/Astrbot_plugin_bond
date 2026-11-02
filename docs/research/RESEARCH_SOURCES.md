# 研究来源与证据边界

整理日期：2026-11-03。对应 [图文短论文](EMOTION_MODEL_PAPER.md) 与 [README 参考文献](../../README.md#references)。

## 原始材料与用途

两份模型生成的独立研究与一份交叉评审 RFC 提供了候选机制、反例和评价思路。研究建议与已实现功能分别列示；实现事实以源码为准，实验结论以对应的归档数据为准。

| 材料 | 文档性质 | 在本次整理中的用途 |
| --- | --- | --- |
| 《AstrBot Bond：面向群聊 AI Agent 的社会关系建模跨学科研究》；文件名前缀 ChatGPT | 初始研究报告，正文未给出独立修订号 | 提取 F/A/T 的构念区分、信任诊断性、弃权与评价框架 |
| 《多人群聊 AI Agent 的长期社会关系机制及 AstrBot Bond 架构独立评估报告》；提供文件名为《Gemini：AstrBot Bond 关系架构独立研究.txt》 | 独立研究，声明当时无法访问 Bond 源码 | 作为复杂模型与反例库的比较输入；其源码推测须另行核对 |
| 《AstrBot Bond：ChatGPT × Gemini 双模型研究交叉评审与设计 RFC》v1.1，2026-11-02 | 设计评审，不是实验论文或合并凭证 | 保留分阶段验证、证据不足时停止复杂化的判断 |
| 当前源码、[Phase0](PHASE0_RESULTS.json) / [Phase1](PHASE1_RESULTS.json) / [Phase2](PHASE2_RESULTS.json) 结果 | 实现与可重复工程证据 | 决定短论文中“已实现”和“已验证”的范围 |

两份研究报告和交叉评审 RFC 均不属于外部同行评审证据。本文整理其用途与结论，不收录全文，也不沿用无法追溯到原始出版物的模型内部引用标记。

为识别本次输入版本，三份提供文件的 SHA-256 如下。摘要只定位文件，不证明内容正确。

~~~text
ChatGPT 研究：6478a860ff4753b9404cd390a67a10d87851e7b4b4ae0cdcee84a158b5e475d1
Gemini 研究： a2a9f243e8b55857e618be731417f8217aab2c57a1065d0e6a69d2dc5b9cce5a
交叉评审 RFC：e8eceb56e11ad82966a85bf7704b54dfeb64305919d5502e5b9862ec4291fe86
~~~

## 文献核对范围

README 收录能够追溯到论文、出版社、会议或作者机构，且对项目有明确用途的文献。核对范围包括书目信息、摘要及与引用结论有关的页面，未逐篇取得全部付费全文或重现实验。DOI 用于定位原出版物；摘要可访问不等于其中全部统计数字已经核验。

| README 编号 | 核对入口 | 允许使用的结论与限制 |
| --- | --- | --- |
| [1](../../README.md#ref-01) | [作者机构记录](https://experts.umn.edu/en/publications/the-relationship-closeness-inventory-assessing-the-closeness-of-i/) | RCI 区分频率、多样性、影响强度等测量面向；不能称其验证了 Bond 的 F/A/T |
| [2](../../README.md#ref-02) | [PubMed 原论文摘要](https://pubmed.ncbi.nlm.nih.gov/9599440/) | 亲密体验涉及披露与回应性；不能把聊天文本直接当用户真实心理 |
| [3](../../README.md#ref-03) | [Academy of Management](https://journals.aom.org/doi/10.5465/amr.1995.9508080335) | 信任与相邻正向构念需区分；不规定插件事件权重 |
| [4](../../README.md#ref-04) | [Academy of Management](https://journals.aom.org/doi/abs/10.5465/amr.1998.926620) | 信任与不信任可以并存的理论框架；不等同“五维模型已被证明最佳” |
| [5](../../README.md#ref-05) | [PubMed 原论文摘要](https://pubmed.ncbi.nlm.nih.gov/14769123/) | 不同信任违背类型下，道歉与否认的效果不同；不沿用通用修复百分比 |
| [6](../../README.md#ref-06) | [Annual Reviews](https://www.annualreviews.org/content/journals/10.1146/annurev-orgpsych-032516-113147) | 信任修复的综述背景；不能把后续行为要求简化为已经校准的 K 次规则 |
| [7](../../README.md#ref-07) | [大学托管原论文](https://pdodds.w3.uvm.edu/research/papers/others/1980/russell1980a.pdf) | 人的当前情感体验模型；不提供 Bot 的长期关系维度或半衰期 |
| [8](../../README.md#ref-08) | [Sage 原刊页面](https://journals.sagepub.com/doi/10.1037/1089-2680.5.4.323) | 跨领域的负面影响综述；不规定统一负向倍率 |
| [9](../../README.md#ref-09) | [MIT 作者稿](https://courses.media.mit.edu/2004spring/mas630/Articles/BP-TOCHI.pdf)、[DOI 书目记录](https://api.crossref.org/works/10.1145%2F1067860.1067867) | 长期关系型 Agent 的设计与评价；作者稿页面与正式发表日期不同，书目按 2005 年 ACM 期刊版 |
| [10](../../README.md#ref-10)、[11](../../README.md#ref-11) | [2021 出版社页面](https://www.sciencedirect.com/science/article/pii/S1071581921000197)、[2022 出版社页面](https://www.sciencedirect.com/science/article/pii/S1071581922001252) | Replika 一对一关系的定性 / 纵向研究；不直接外推 QQ 多人群聊 |
| [12](../../README.md#ref-12) | [ACL Anthology](https://aclanthology.org/J18-4009/) | 该实验中会话上下文有助于反讽识别；不等于 Bond 已增加或验证多轮上下文 |
| [13](../../README.md#ref-13) | [ACL Anthology](https://aclanthology.org/2025.iwsds-1.36/) | 三人多模态对话的受话人识别难题；不是 QQ 文本准确率 |
| [14](../../README.md#ref-14) | [ACL Anthology](https://aclanthology.org/2023.acl-long.858/) | 中文直播多方对话的外部基准；不能替代 Bond 的授权标注集 |
| [15](../../README.md#ref-15) | [NeurIPS 会议记录](https://papers.nips.cc/paper_files/paper/2017/hash/4a8423d5e91fda00bb7e46540e2b0cf1-Abstract.html) | 弃权与风险 / 覆盖率权衡；原实验为视觉分类，Bond 不继承其统计保证 |
| [16](../../README.md#ref-16) | [ICLR 会议记录](https://proceedings.iclr.cc/paper_files/paper/2024/hash/0105f7972202c1d4fb817da9f21a9663-Abstract-Conference.html) | 模型可能迎合用户；不是 Bond 通用错误率，也不能证明称赞者在攻击 |
| [17](../../README.md#ref-17) | [AAAI 会议记录](https://ojs.aaai.org/index.php/AAAI/article/view/29946) | 记忆更新机制的比较；遗忘曲线不能直接证明关系应如何衰减 |
| [18](../../README.md#ref-18) | [ACL Anthology](https://aclanthology.org/2023.emnlp-demo.15/) | 需求、情绪和关系共同参与模拟的架构；Bond 未实现该系统 |
| [19](../../README.md#ref-19) | [作者预印本](https://arxiv.org/abs/2304.03442) | 记忆、反思与行为生成的架构；模拟中的效果不能当作 Bond 实验结果 |

## 未采用的外推

- 不把原报告中的 53.35% / 85.40% 写成 QQ 群聊识别率；本次以可核对的多方对话论文说明问题存在，不沿用这些数字。
- 不使用 78% / 31% / 58% 作为道歉或行为修复系数，也不使用“通用 75% 奉承错误率”。
- 不采用“少于某熟悉度的玩笑必然冒犯”“重复夸奖必然恶意”或“账本没记到就证明用户撒谎”的前提。
- 不将 Beta/Gaussian 先验、15 分钟情绪半衰期、修复 K 次、固定锚点、2ms 或 Token 节约百分比写成当前实现或已验证参数。
- 不把 Gemini 的“仓库不可访问”扩写成当前事实，其关于无 SQLite、无异步门控等假设均须以源码纠正。
- 不把仅给出简称、缺少可追溯版本或依靠二手数字的引用补写成完整书目。这里的 19 条是经筛选的参考资料，不是对两份报告所有引文的背书。

## 从研究建议到当前成果

| 研究方向 | 当前可写入成果的内容 | 尚不能宣称 |
| --- | --- | --- |
| 关系构念分离 | F/A/T 独立保存与表达；礼貌及道歉默认 T=0 | F/A/T 已得到人类心理测量验证 |
| 受话人归因 | 类型化 At/Reply 冲突否决 | 已可靠理解隐含受话人、纯文本引用或群内反讽 |
| 不确定性 | 模型可弃权，失败与无有效复核时不更新关系 | confidence 是校准概率，或不再存在语义误判 |
| Trust 证据与修复 | 已完成只读流水派生与合成来源合同；生产推进搁置 | 已上线 evidence 字段、违背锁或自动信任修复 |
| 称谓和人格 | 五档、125 格、55 词条；附释义，保留人格及自然查询表达 | 已上线滞回、人格评分矩阵，或真人认可全部词条 |
| 可观察性 | 可选本机诊断与离线汇总 | 已获得真实准确率、费用收益或回复延迟结论 |

本次实现快照为 main `daf74a66e0934acc318c1c5d21813636b3efb59d`，运行版本 0.2.6。阶段文档中的“待合并”和旧版本号描述各自交付时点；这些表述不代表当前合并状态。短论文中的代码事实按上述快照核对，实验结论沿用原始结果 JSON。

图 3 只读取现有归档文件：`PHASE0_RESULTS.json` 的 `arms.baseline.metrics`，`PHASE1_RESULTS.json` 与 `PHASE2_RESULTS.json` 的 `right.metrics`。三者夹具摘要相同：`69fb8c354129d4f4264b0845f4dc227bb5a8b2fcc3f202218370e2867ac77dae`。其中 commit 字段可能记录运行时尚未提交的 HEAD；解释行为时还需看 `source_files_sha256`，不能只凭 commit 字段假定与干净提交完全相同。
