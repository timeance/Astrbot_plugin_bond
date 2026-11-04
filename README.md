<p align="center">
  <img src="logo.png" width="360" alt="Bond：八千代浅笑比 V 的 Q 版徽章" />
</p>

<h1 align="center">Bond 关系好感系统</h1>

<p align="center">
  <strong>熟悉，不一定亲近。亲近，也可以有所保留。</strong><br />
  为 AstrBot 的长期群聊保留相处的痕迹，让当前人格决定如何表达。
</p>

<p align="center">
  <a href="#quick-start">快速开始</a> ·
  <a href="#relationship">三维关系</a> ·
  <a href="docs/RELATION_LEXICON.md">关系词典</a> ·
  <a href="#dashboard">管理面板</a> ·
  <a href="#boundaries">能力与限制</a>
</p>

<p align="center">
  <strong>v0.4.1</strong> · AstrBot ≥4.28 / &lt;5 · OneBot aiocqhttp · AGPL-3.0
</p>

---

Bond 是面向 **长期多人群聊** 的关系记忆插件。它分别记录 Bot 对每位成员的熟悉度、亲近度与信任度，将三维状态转成自然语言描述，供当前人格在回复时参考。

关系可以随群聊互动更新，也可以在管理面板里查看、调整、撤销或设置限时覆盖。每份关系资料都属于对应的 Bot 账号与数据空间；群内公开查询只显示关系称谓，不展示具体分值。

> **初次安装默认关闭自动评估、关系注入与诊断记录。** 先确认管理与查询正常，再按需启用。当前支持从 main 安装试用；真实模型的表达效果仍待验收。

## ✨ Bond 能带来什么

| 特色 | 你会用到的能力 |
| --- | --- |
| **三维关系，分别记录** | 熟悉度、亲近度、信任度各自计算，可以同时表达“亲近”和“戒备”。 |
| **55 个中文关系词条** | 三个维度各分五档，125 种组合映射为 55 个中文词条；底层仍只维护三个维度。 |
| **表达沿用当前人格** | 词条提供理解线索，Bot 不必照读标签，也没有预设的冷淡或热情答法。 |
| **每个 Bot 有自己的关系** | 按真实 Bot 账号独立存储；群内关系与全局关系分开管理。 |
| **关系可查、可调、可撤销** | 管理面板保留变动流水，支持正式调整与限时体验，临时关系结束后恢复正式值。 |
| **可选诊断，方便验证** | 本机记录脱敏的变化、称谓与调用情况，帮助排查漂移和失败，不额外调用模型。 |

<a id="relationship"></a>
## 🌿 三层处理，让关系更容易理解

**三个维度负责计算，关系词典负责解释，当前人格负责表达。**

<p align="center">
  <img src="docs/assets/relationship-overview.svg" width="1000" alt="三层关系处理：熟悉度、亲近度、信任度各分五档，125 种组合映射到 55 个词条，最后交由当前人格表达。亲近却戒备、疏远但信赖、亲密挚友是并列的状态示例。" />
</p>

### ① 计算：熟悉、亲近、信任各有含义

| 维度 | 描述什么 | 从低到高的五档 |
| --- | --- | --- |
| **F · 熟悉度** | 有多熟悉 | 陌生 → 眼熟 → 相识 → 熟悉 → 熟络 |
| **A · 亲近度** | 相处时的亲疏 | 疏远 → 疏淡 → 平和 → 亲近 → 亲密 |
| **T · 信任度** | 放心还是戒备 | 戒备 → 谨慎 → 保留 → 放心 → 信赖 |

熟悉度范围为 `0..100`，亲近度与信任度为 `-100..100`，三者不合成一个总好感分。分档用于描述状态，不是经过心理学校准的概率。初期少量有效交流即可从“陌生”走向“眼熟、相识”；熟悉不等于亲近，更不等于信任。

### ② 理解：同样熟悉，也可以是不同的关系

| 词典中的状态 | 它保留的细节 |
| --- | --- |
| **亲近却戒备** | 有亲近感，也有明确戒心；亲近不会抵消边界。 |
| **疏远但信赖** | 不够热络，仍可以保有较强信任。 |
| **亲密而保留** | 已经熟悉且亲近，信任上仍有所保留。 |
| **亲密挚友** | 熟悉、亲近和信任均较强，仍不改变人格原则或权限。 |

这些词条表示并列的关系组合，不是一条逐级升级的路线。完整的 55 个词条、释义与档位映射见 [关系词典](docs/RELATION_LEXICON.md)。

### ③ 表达：让角色用自己的方式回应

启用关系注入后，Bond 会在当前请求中追加关系资料，同时保留原有的人格设定、用户输入和对话历史。系统不要求模型披露或照读称谓，具体如何回应由当前人格、系统规则和对话语境决定。

自然语言工具提供的是 **当前 Bot 对成员的关系**，不能据此推断成员对 Bot 的感受，也不能回答群友之间谁最喜欢谁。唤醒、接话与主动发言仍由 AstrBot 或 Angel Heart 等插件决定。

<a id="quick-start"></a>
## 🚀 快速开始

### 1. 安装并确认 Bot

- 使用 AstrBot `>=4.28,<5` 和 `aiocqhttp`；真实框架装配已在 `4.28.0` 验证，Python 版本跟随宿主要求。
- 在 AstrBot 的插件管理中通过本仓库地址安装；也可下载 main 的 ZIP，解压到 `data/plugins/astrbot_plugin_bond/`。**插件目录必须全小写**。
- 重载后打开 Bond 管理页面，在「详细设置 → 基础设置」选择评估模型并按需开启自动评估、在回复中体现关系，填入适用群与需要忽略的机器人账号。尚未发现 Bot 时也可完成这些设置。
- 在「详细设置 → 基础设置」选择关系模式，在「关系变化」调整基础参数；新安装默认为 Galgame。保存并重载后生效。连接 OneBot 后点击“刷新 Bot 列表”，选中需要管理的 Bot 与数据空间。

插件列表左侧与 README 使用同一版 Logo。更新插件后重载并刷新插件页面即可；手动安装时请保留根目录的 `logo.png`。

Bot 账号来自平台连接或可信消息事件；一个适配器可以包含多个账号。私聊可帮助登记 Bot，但不会建立关系记录。仅填写适配器配置、尚未连接或收到事件时，列表可能没有可识别的账号。

### 2. 先试公开查询

在已启用 Bond 的群聊中发送：

| 命令 | 用途 |
| --- | --- |
| `/我的关系` | 查看当前 Bot 对自己的关系称谓。 |
| `/好感排行` | 查看本群前 10 名的昵称、头像与当前关系，按亲近度排序，不显示分值。 |

排行默认使用八千代月读空间风格图片，右上角从九款无文字角色插图中随机选择，插件运行期间避免连续同款。昵称缺失时显示“未命名成员”，不会用账号替代昵称。可在「详细设置 → 基础设置」选择 **所有人 / 仅主人**，或关闭图片改用文字。仅主人模式按 Bond「主人 QQ」名单与可信发言者账号匹配，未配置时无人可查；斜杠命令、文字唤醒和模型工具共用权限。权限限制谁能发起查询，不能阻止群内成员看到或转发已经发送的榜单；它不授予任何管理权限。

<p align="center"><img src="docs/assets/ranking-preview.png" width="520" alt="合成排行示例：无文字八千代角色插图与月夜星光，显示名次、昵称、头像和当前关系，查询者高亮；没有分值。" /></p>
<p align="center"><em>合成昵称与头像示例，非真实群聊资料。</em></p>


`/我的关系` 对所有群成员开放；`/好感排行` 遵循配置的查询权限，直接返回公开资料。自然语言询问由当前人格组织表达，不强制复述关系称谓。聊天查询不展示具体分值、私有证据或其他群资料。

### 3. 按需开启自动变化与回复参考

| 想要的效果 | 设置方法 |
| --- | --- |
| 互动后自动评估关系 | 在「详细设置 → 基础设置」开启自动评估，选择日常评估模型与评估模式；复核模型可留空。 |
| 回复时参考当前关系 | 在基础设置开启「在回复中体现关系」。它与自动评估分别控制，也可配合手动设定的关系使用。 |
| 只在指定群使用 | 填写 `group_whitelist`；留空表示所有群。名单外不记录、不评估、不注入，也不响应 Bond 群聊查询。 |
| 观察规则与称谓的运行 | 在“详细设置 → 关系诊断”打开诊断记录。 |

**Bond 页面保存基础与详细设置后需要重载插件；提示词保存后立即生效。** AstrBot 原插件配置也可编辑同一份配置，宿主保存时会自动重载。切换编辑入口后先刷新，避免提交另一处已过期的表单。自动评估需要调用你选择的模型，会产生相应用量。用量统计缺失时显示未知，不估算货币成本。

### 印象与标签如何更新

默认每积累8条尚未用于生成印象的自动评估流水，尝试更新一次印象。已撤销的流水不参与，手动调整不计入；这不是消息数或模型调用次数。因每日额度等限制而写入的零变化流水也会计入，自动更新检查在一次关系实际变化后进行。

按群记录时，每个群分别积累；全局空间中，同一 Bot 对同一成员的跨群记录共同积累。不同 Bot 和不同数据空间相互独立。全局汇总可能引用这个成员在不同群的互动，但不会因此在群聊回复中公开其他群的印象或历史。

默认的一句话模式同时更新印象和标签，标签最多3个；分点模式更新印象要点并保留已有标签。模型不可用或生成失败时，保留原内容和待汇总记录。临时关系期间暂停自动更新。更新频率可在详细设置中调整，已有设置继续保留。

AstrBot 插件配置保留启用、模型、适用范围和排行等常用选项。三维变化、印象与回复、评估高级参数放在 Bond 页面中，日常使用可以保留默认值。

### 4. 一套基础参数，两种关系节奏

新安装默认 **Galgame**。评估仍默认关闭：先选择模型，再开启自动评估；需要回复参考关系时，另行开启关系注入。

模型判断互动性质、指向、上下文与语义重复；Bond 校验身份、协议和当前原文证据，再执行本地数值规则。普通有内容问答可以增加熟悉度，不自动增加亲近度。感谢、示好和道歉不自动恢复信任；本版没有新增自动正向 Trust 事件。

| 每日互动额度 | 基础模式 | Galgame（默认） | 两种模式扣分额度 |
| --- | --- | --- | --- |
| 熟悉度 F | +5 | +10 | 0 |
| 亲近度 A | +10 | +20 | −8 |
| 信任度 T | +2 | +4 | −5 |

Galgame 将所有正向请求和正向每日额度统一乘 2；负向单次变化、负向额度、模型、调用次数与衰减不变。正负用量独立累计，加减不能抵消已用额度；撤销恢复分值，不退还已消费的互动额度或调用次数。自动时间衰减不占扣分额度。切换模式不翻倍旧分数、不重算历史、不清空当天用量。

**按熟悉程度调整积累速度。** F 在 0/10/30/55/80/95 六段分别使用 150%/120%/80%/50%/30%/15% 基础倍率，再应用模式倍率。小于 0.1 的有效增长保存在现有数值中，展示取一位小数；重启不丢失。A 保留现有阶段曲线和同日正向收益 100%/75%/50%/25% 的递减，T 没有阶段曲线。

**关系保温期默认5天。** 即使一段时间没有互动，彼此的熟悉感也不会马上消失。超过这段时间后，关系才会逐渐淡化。三个维度只计算超过保温期的时间；设为0可取消保温期。新的有效互动会重新计时，不会恢复此前已淡化的关系。

默认半衰期为熟悉度 **60 天**、亲近度 **30 天**、信任度 **90 天**，默认基线均为 0；亲近度保留原有托底。每维的开关、半衰期与基线都可在“详细设置 → 关系变化”调整。

基线是衰减的停止范围，不是自动赠送的分数。熟悉度低于基线时保持不变；亲近度与信任度使用正负对称范围。例如信任基线设为 20，+40 在保温期结束90天后约为 +30，−40 约为 −30，之后继续向 +20/−20 收敛；−20～+20 内保持不变。时间不会把负信任变为正信任。临时关系期间暂停三维正式衰减；修改参数并重载后，对已有成员也使用当前设置。

**允许连续有内容的交流。** 同一 Bot 下，每位成员的滚动初评窗口为 300 秒最多 5 次；每天最多初评 30 次，其中最多 8 次可进一步复核。每 Bot 每天总计初评 300 次、复核 100 次；插件全局并发上限 2。个人窗口和调用预算跨群、跨 group/global 空间共享，不同 Bot 独立；关系数据仍按空间隔离。失败、超时也记调用次数，预算不足或本地拒绝不触发模型。调用日额度按 UTC 00:00 刷新，关系日额度按配置时区刷新。

初评和复核使用同一份有界上下文：默认最近 6 条相关发言/回复、7 天内最多 8 条同群已计分证据摘要，上下文预算 4000 字符。只收集当前成员与当前 Bot 的相关互动，不监听完整群友对话。历史不能再次计分，当前消息仍须提供原文证据；跨日不意味着自动再次奖励。语义重复由模型判断，框架不以寒暄关键词代替判断，也不承诺完全防刷。

在「详细设置 → 基础设置」选择关系模式，在「关系变化」按熟悉度、亲近度、信任度分别调整参数；在「评估」调整窗口、预算、并发与上下文。**恢复默认只填入当前设置区，检查后保存并重载**，不会清除模型选择、成员状态或流水。已有明确保存的模式与参数继续保留，重装保留配置也不会自动替换旧值。评估是否合适仍受接入模型影响，建议固定模型与人格观察 2–3 天。

升级、精度、额度与回滚说明见 [关系评估框架](docs/ASSESSMENT_FRAMEWORK.md)。

<a id="dashboard"></a>
## 🛠️ 在面板里管理关系

Bond 使用 AstrBot 原生管理页面，写操作由宿主认证保护。页面首次打开使用浅色，可在右上角切换深色；再次打开沿用当前浏览器上次的选择。设置默认折叠，字段旁的问号解释用途。

当前总览的“今日概览”显示所选 Bot 的初评、复核次数与运行配额，以及当前数据空间中关系发生变动的人数。调用按 UTC 日期跨群合计，失败也计入；人数按关系设置的时区统计，三维任一实际改分或撤销均计入，同一成员只算一人，不含临时关系和自然衰减。翻页和搜索不改变统计范围，点击“刷新”可更新概览。

| 入口 | 可以做什么 |
| --- | --- |
| **成员关系** | 查看三维状态与称谓，编辑关系与印象。已观察的全零成员默认折叠，可搜索找回；每页可选 30／50／100／200 人。 |
| **变动流水** | 对照熟悉度、亲近度与信任度的增量，按维度或自动评估来源筛选，预览并撤销整个事件。 |
| **临时调整** | 给选定成员设置持续 1 分钟至 30 天的临时关系，用来体验不同状态。 |
| **提示词** | 分别配置互动评估与回复行为，支持全局设置、按 Bot 与群覆盖、模板文本预览；删除群级覆盖后继承全局模板。 |
| **评估** | 调整滚动窗口、超时、并发、上下文与初评/复核预算，查看已保存值、当前运行值及调用统计；按所选维度进入评估流水。 |
| **详细设置** | 基础设置集中启用、模型、群与排行选项；低频参数按用途收纳，关系变化按三个维度排列。 |
| **备份与恢复** | 对选定 Bot 的数据空间操作，便于升级前留存和恢复。 |

### 临时体验，不抹去正式关系

临时关系生效时，称谓、排行和回复参考使用临时值，面板同时显示正式值与截止时间。期间暂停该关系的自动评估、自动印象更新和正式衰减，已发出的旧任务不能回写。

到期或点击“立即结束临时关系”后恢复正式值，随后继续衰减与评估；期间消息不补评。临时状态保存在本地，重启后继续生效。结束临时关系后，才能调整正式分数、撤销分数流水或重置；印象与标签仍可手动编辑。

<details>
<summary>昵称显示、全局详情与多 Bot 排查</summary>

- “全部群”显示 QQ 昵称，指定群显示群名片，没有群名片时回退昵称。旧记录缺少可靠昵称时显示“昵称未知”，不会把旧群名片当作 QQ 昵称。
- 成员列表和详情均可“刷新昵称”；需要对应 Bot 在线，同一成员两次刷新至少间隔 30 秒。平台查询失败时保留原资料，长昵称可悬停查看。
- 全局空间的“全部群”可查看、调整正式关系；群资料与事件操作仍需选择真实群。按群空间保留独立行，不合并分值。
- 多个 Bot 使用不同 AstrBot 配置时，每份配置都要启用 Bond，群白名单也要允许目标群。平台收到消息，不等于该配置已启用插件。
- 旧版热更新后若成员详情提示参数无效，先重载插件并刷新页面；必要时重启 AstrBot，以清除旧成员接口。

</details>

<a id="data-spaces"></a>
## 🗂️ 每个 Bot，都有自己的关系记录

| 数据空间 | 适合的使用方式 | 隔离规则 |
| --- | --- | --- |
| `per_group`（默认） | 同一人在不同群有不同的相处状态。 | 按 Bot、群、成员分别记录。 |
| `global` | 同一 Bot 跨群使用一份成员关系状态。 | 每个 Bot 仍独立；本群排行只列出本群已观察到的成员，不召回其他群印象与历史。 |

切换空间只改变使用哪份数据，**不会迁移、合并、复制或删除原记录**。临时关系同样遵循空间规则：全局模式跨群共用，按群模式仅影响选定群。面板中的 Bot 专属模式优先于 `default_scope_mode`。

数据保存在 `data/plugin_data/astrbot_plugin_bond/`，每个真实 Bot 分别使用 `bots/{bot_id}/group.db` 与 `bots/{bot_id}/global.db`。

## 🔎 可选诊断：看看机制实际发生了什么

诊断默认关闭。启用后在本机记录已发生的评估结果、三维变化、称谓、调用用量与耗时，不额外调用模型。管理员可在当前 Bot、当前空间下查看状态、导出或清除；关闭后仍可管理旧记录。

- 不记录聊天原文、引用内容、模型原始输出、昵称、原始账号或密钥；成员与事件使用本次加载内的代号，跨群、重载后不关联。
- 写入或导出时清理超过 7 天的记录，所有 Bot 与空间合计最多 20MB，同日记录合并为大小受限的片段，按片段起始时间过期，实际窗口可略短于7天；量大时更早轮转。单次导出最多32768行，说明丢弃、失败和截断，重启前的缺口计数未知。
- 这是脱敏记录，不保证无法间接识别，也不改变原有关系流水的保存方式；清除诊断不影响正式关系。

诊断适合排查漂移、称谓翻转和失败路径。判断语义是否准确、词语是否贴切仍需要人工标注，详见 [诊断说明](docs/research/DIAGNOSTICS.md)。

<a id="boundaries"></a>
## 使用前，了解这些边界

**关系只影响态度和表达。** Bond 不改变系统规则、管理权限、人格核心或发言策略；配置中的“主人”只是沿用当前人格的称谓，不授予权限，也不豁免评分。关系状态用于角色表现，不代表 AI 拥有真实感情。

**先确认归因，再考虑变化。** 自动评估只接受没有冲突的当前 Bot 指向。其他 @ 对象、多目标、引用来源缺失或冲突时跳过更新。空文本、不满足入口条件的消息和相同稳定消息 ID 的重复处理由框架拒绝；是否只是寒暄、重复感谢或新的实质互动交由模型结合有界上下文判断。滚动调用窗口与分值额度分别约束调用和变化，纯文本转述仍可能被模型误解。

**模型提出事件建议，本地规则决定变化。** 模型不能直接修改三维数值。失败、超时、解析失败、复核预算不足或结果不确定时，本次不改关系，正常对话继续。并发已满时候选记为 `busy` 并丢弃，不另建排队或合并消息调用。

**感谢和道歉不自动换来信任。** 默认规则只让这类事件影响熟悉度／亲近度，不增加信任。当前没有经过验证的自动 Trust 修复机制；实际改正的识别仍待研究，管理员调整不算自动修复。升级不重算旧分值。

**图片排行会获取 QQ 头像。** 获准查询后，仅向固定 QQ 头像服务发送榜内至多10名成员的账号来获取头像，不发送关系称谓、分值或聊天。头像只短时缓存在内存，失败使用昵称占位；图像在本机生成，内置中文字体，无需在线渲染或额外模型。关闭图片或使用模型工具查询时不请求头像。

**模型调用有数据外发。** 启用评估或刷新印象时，消息文本、允许的上下文或有限流水可能发送给所选 AstrBot Provider。Bond 不自动上传本地诊断；请勿向 Issue、PR 或截图提交真实聊天、账号、数据库和密钥。

<details>
<summary>提示词文件、配置重载与外部记忆</summary>

提示词保存在独立的 `data/plugin_data/astrbot_plugin_bond/prompts.json`。评估和行为模板分开管理，群级覆盖按真实 Bot ID 与群 ID 保存；评估模板始终保留程序的输出协议，未知占位符会被拒绝。

提示词文件损坏时保留原文件，暂停自动评估与关系注入；本地查询和管理继续运行。修复或恢复文件后重载插件，详见 [提示词说明](docs/PROMPTS.md)。

基础与详细配置适用于所有 Bot。Bond 页面保存后重载生效；已开始的任务按原配置执行完毕。群白名单外的历史资料保留，管理员仍可在面板管理。配置仍由 AstrBot 保存，隐藏字段保留；Bond 基础设置与评估页共用版本校验，过期保存会被拒绝，需刷新后重试。AstrBot 原配置入口不使用 Bond 的版本校验，因此在两处来回编辑时先刷新原配置表单。

LivingMemory 集成已移除，不再探测、读取或写入其记忆。本地关系、印象与流水不受其安装或卸载影响，见 [接口调查](docs/LIVINGMEMORY.md)。

</details>


## 升级、备份与回滚

升级前停用插件，并一起备份代码、AstrBot 插件配置和整个 `data/plugin_data/astrbot_plugin_bond/` 数据目录。复制 SQLite 文件前须停止写入；在线备份使用插件提供的备份功能。

当前数据结构为 schema 17。升级无需重置数据；回滚至 schema 16 或更早的代码时，须恢复升级前匹配的代码、配置与数据备份。旧代码不能直接打开新库。Bond 不自动导入旧 `bond.db` 或 Xinxian 数据。

## 文档与参与

| 想了解什么 | 从这里开始 |
| --- | --- |
| 完整关系称谓与组合 | [关系词典](docs/RELATION_LEXICON.md) |
| 自定义表达与模板 | [提示词说明](docs/PROMPTS.md) |
| 管理功能与接口 | [管理面板](docs/PR5_MANAGEMENT.md) |
| 安装后的验证边界 | [验收记录](docs/ACCEPTANCE.md) |
| 情感与关系建模 | [图文短论文](docs/research/EMOTION_MODEL_PAPER.md) · [研究来源](docs/research/RESEARCH_SOURCES.md) |
| 开发与测试 | [开发指南](docs/DEVELOPMENT.md) |
| 参与贡献与反馈 | [贡献指南](CONTRIBUTING.md) · [安全问题报告](SECURITY.md) |
| 插件商店提交准备 | [提交资料与自查](docs/STORE_SUBMISSION.md) |

欢迎在 [Issues](https://github.com/timeance/Astrbot_plugin_bond/issues) 提交脱敏复现，或通过 PR 改进代码与文档。


## 许可与来源

Bond 基于 [yiwuerxin/astrbot_plugin_xinxian](https://github.com/yiwuerxin/astrbot_plugin_xinxian)（心弦好感度）开发，保留原作者署名，并沿用 **GNU AGPL v3** 许可证。
项目 Logo 与九款排行角色插图是以《超时空辉夜姬！》角色八千代为灵感、由 AI 辅助绘制的非官方同人素材，非官方联名。

<a id="references"></a>

## References / 参考资料

关于情感与关系建模的设计、实现和合成实验，见图文短论文 [《AstrBot Bond：群聊中的情感与社会关系建模》](docs/research/EMOTION_MODEL_PAPER.md)。原始研究输入、文献用途和未采纳结论见 [研究来源与证据边界](docs/research/RESEARCH_SOURCES.md)。

以下文献用于界定构念、比较机制和设计评价方法，不能证明 Bond 具有真实情感，也不能直接给出关系分值、阈值、修复比例或真实群聊效果。条目按主题编排，编号供短论文引用；来源核对日期为 2026-11-03。

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


注入位置的取舍见 [注入方式评估](docs/research/INJECTION_REVIEW.md)；大量成员的计算开销与实测范围见 [页面合成负载记录](docs/research/DASHBOARD_LOAD.md)。
