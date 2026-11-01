# Bond

Bond 是面向 AstrBot 的关系记忆插件。它按 Bot 和数据作用域保存成员关系、印象与变动流水，并把关系称谓和经过校验的资料用于后续对话。

当前版本为 `0.2.0-dev`，可从本仓库 main 安装试用。已完成的验证及待验项目见 [验收记录](docs/ACCEPTANCE.md)。

## 兼容性

- AstrBot `4.28.0` 已完成插件装配实测，元数据兼容范围为 `>=4.28,<5`。
- 离线 CI 覆盖 Python `3.10` 和 `3.12`；AstrBot `4.28.0` 装配检查使用 Python `3.12`。
- 目标平台为 `aiocqhttp`。真实 QQ 群聊、同时安装 Angel Heart 的聊天链路、联网模型效果和完整 Dashboard 浏览器流程仍待最终验收。

## 功能边界

### 关系与数据

- 关系由熟悉度 `familiarity`、亲近度 `affinity`、信任度 `trust` 三个维度组成，不压缩成总分。熟悉度范围为 `0..100`，亲近度和信任度范围为 `-100..100`。
- 数据目录为 `data/plugin_data/astrbot_plugin_bond/`。每个真实 Bot 独立拥有 `bots/{bot_id}/group.db` 和 `bots/{bot_id}/global.db`。
- `per_group` 与 `global` 只是数据空间路由。切换模式不会迁移、复制、合并或删除历史；全局排行仍只列出当前群已经观察到的成员。
- 普通群友可使用 `/我的关系` 查询自己的关系称谓，使用 `/好感排行` 查询本群前 10 名的称谓。公开查询不显示任何关系分值，也不能查询他人的私有数值。

### 管理面板

Bond 使用 AstrBot 原生 Dashboard 的页面接口，写操作要求宿主认证。管理功能包括：

- 选择 Bot、`per_group/global` 数据空间和群号；
- 编辑熟悉度、亲近度、信任度，保存理由并写入审计流水；
- 编辑成员印象和标签，查看关系流水、评估结果与撤销预览；
- 查看评估调用次数、Token 用量和失败状态；Provider 未提供用量时显示未知，货币费用不作估算；
- 分别管理评估提示词和行为提示词，支持全局设置、Bot 群级覆盖、预览和恢复上级设置；
- 保存模型配置后重载插件，所有 Bot 和作用域重新使用同一份配置。重载前已经开始的任务继续使用旧配置。

### 自动评估与成本

自动评估默认关闭。启用 `assessment.enabled` 时必须配置 `assessment.small_provider_id`，`assessment.large_provider_id` 可留空。小模型输出事件建议，三维变化由本地规则、限幅和 SQLite 事务决定；模型不能直接写入分值。

空文本、寒暄、重复消息和不满足触发条件的消息会被本地过滤。小模型失败、超时、解析失败、复核预算耗尽或结果不确定时，本次不改变关系，正常对话继续。达到在途并发上限时候选直接记为 `busy` 并丢弃，不排队，也不把多条消息合并成一次模型调用。

统计包含调用数、Token 用量和失败状态。Provider 没有返回 Token 时用量为未知；当前没有可靠的货币单价和费用计算，不能把插件描述为免费或零成本。

### 提示词

提示词保存在独立文件 `data/plugin_data/astrbot_plugin_bond/prompts.json`，与 AstrBot 插件配置分开。评估提示词和行为提示词分别管理，群级覆盖按真实 Bot ID 和群 ID 保存。保存成功后当前进程立即使用新模板；评估模板始终保留程序的结构化输出协议，未知占位符会被拒绝。文件损坏时保留原文件，面板提示修复或恢复 `prompts.json` 后重载；自动评估和关系注入暂停，本地关系查询与管理继续运行。

Bond 只追加当前会话的关系资料，保留 AstrBot 当前人格，不替换人格、输入或历史。全局关系模式不召回其他群的印象和聊天流水。

### LivingMemory

LivingMemory 无需安装，Bond 可以独立运行。当前版本不读取或写入 LivingMemory：已调查的公开接口无法确认记忆属于哪个 Bot 和群。

在 Bond 插件页面打开“可选集成”，点击“检查插件状态”，可查看是否安装、是否启用及接口限制。检查只查询插件注册状态，不调用记忆接口、不修改配置。未安装、停用、卸载或检查失败时，关系、印象和历史记录继续保存在 Bond 本地，已有数据不删除、不迁移。

旧版保存的三个 LivingMemory 开关保留在配置中，但不再显示为可操作选项。它们不会让当前版本获得读写能力。当前调查基线为 LivingMemory `2.8.2`，提交 `a1e9c20`。

## 安装与配置

1. 在 AstrBot 插件市场通过仓库地址安装，或下载 main 的 ZIP，解压后将顶层目录改名为 `astrbot_plugin_bond`。
2. 将插件放入 AstrBot 的 `data/plugins/astrbot_plugin_bond/` 目录，保持目录名为 `astrbot_plugin_bond`，再重载插件。
3. 首次收到可信 Bot 的群事件后，Bond 才会为该 Bot 初始化 `group.db` 和 `global.db`。面板只显示已经产生可信事件的 Bot。
4. 初次运行保持以下开关关闭，确认本地管理和公开称谓查询正常后再单独启用需要的功能。

| 配置项 | 默认建议 | 说明 |
| --- | --- | --- |
| `assessment.enabled` | `false` | 自动调用模型评估三维互动 |
| `assessment.small_provider_id` | 空 | 启用评估时必填 |
| `assessment.large_provider_id` | 空 | 可选的复核模型 |
| `inject.enabled` | `false` | 是否向回复追加关系资料 |

插件配置只保留自动评估、关系注入、模型选择和忽略机器人账号。详细设置请打开 Bond 插件页面：

- **提示词**：分别设置互动评估和回复行为，支持全局默认、指定 Bot 的群级覆盖、校验、预览与恢复。
- **评估**：调整评估模式、冷却、超时、并发和每日复核次数，查看调用统计。
- **详细设置**：调整关系增长、时间衰减、印象、近期事件和排行。各组默认折叠，对所有 Bot 生效。
- **可选集成**：查看 LivingMemory 状态，当前不提供记忆读写开关。

基础和详细配置保存后需要重载插件；提示词保存后立即生效。配置仍由 AstrBot 保存，隐藏字段及已有值保留，不复制到第二份配置文件。旧标量评估和旧人格指引不再显示为设置入口。详细设置若被其他管理员修改，保存会被拒绝，请刷新后重新编辑。

启用评估或手动刷新印象时，消息文本、配置允许的上下文或有限流水可能发送给所选 AstrBot Provider。不要把真实聊天记录、账号标识、数据库或密钥提交到 Issue、截图或 PR。

## 升级与回滚

升级前停用插件并备份：

- 插件代码；
- AstrBot 保存的插件配置；
- `data/plugin_data/astrbot_plugin_bond/` 整个数据目录。

复制 SQLite 文件前必须停止写入，在线备份使用插件提供的 SQLite backup API。回滚时恢复同一版本的代码、配置和数据备份；旧 schema 的备份只能交给支持该 schema 的旧代码读取，不能让老代码直接打开新数据库。Bond 不自动导入旧 `bond.db` 或 Xinxian 数据，也不因切换 `per_group/global` 自动迁移关系。

## 许可与来源

本仓库包含 GNU AGPL v3 许可证全文，来源和修改归属见 [NOTICE](NOTICE)。Bond 源自 yiwuerxin 的 `astrbot_plugin_xinxian`，保留上游说明和原作者署名；上游固定基线 README 明确声明 `AGPL-3.0`，该基线没有独立 LICENSE。Bond 补齐标准许可证全文并沿用该声明，不声称取得额外授权或 GitHub 原生 Fork 关系。修改后通过网络提供服务时，应依许可证向使用者提供实际运行版本的对应源码。

相关文档：

- [需求与阶段](docs/PRD.md)
- [PR0 工程审查](docs/PR0_AUDIT.md)
- [提示词说明](docs/PROMPTS.md)
- [管理面板说明](docs/PR5_MANAGEMENT.md)
- [LivingMemory 调查](docs/LIVINGMEMORY.md)
- [PR0–PR7 验收证据与限制](docs/ACCEPTANCE.md)
- [开发与交付](docs/DEVELOPMENT.md)
