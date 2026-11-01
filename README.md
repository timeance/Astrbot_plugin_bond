# Bond

Bond 是面向 AstrBot 的关系记忆插件。它按 Bot 和数据作用域保存成员关系、印象与变动流水，并把关系称谓和经过校验的资料用于后续对话。

当前版本为 `0.2.0-dev`。PR0 至 PR7 按依赖顺序审核，最终验收记录见 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)。截至本次文档更新，`main` 仍只有许可证提交，不能作为用户安装来源。完成全部审核前不要自动合并或发布；试用代码位于 [`feat/pr7-release-readiness`](https://github.com/timeance/Astrbot_plugin_bond/tree/feat/pr7-release-readiness)，安装时使用该分支快照或审核版 ZIP。

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

LivingMemory 是可选增强，不是运行依赖。当前调查基线为公开仓库提交 `a1e9c20`，版本 `2.8.2`。调查未发现能够证明真实 Bot 和群作用域的公开 API，因此 Bond 只保留动态发现、配置状态和 `unsupported_scope` 降级，不声称完成真实召回、外部写入或持久去重。

`livingmemory.enabled`、`livingmemory.read_enabled`、`livingmemory.write_enabled` 独立控制：

- 关闭总开关时不探测缺失实例、不读取、不写入，也不报可选插件错误；
- 启用后每次按需重新发现实例，不缓存旧对象；缺失、禁用、接口变化或错误时回到本地 standalone；
- Bond 不读取 LivingMemory 内部数据库，不导入、不迁移、不删除其历史，也不要求安装 LivingMemory；
- 外部记忆文本不能直接修改 Bond 的三维关系、印象或流水。

## 安装与配置

1. 审核链为 #1 → #2 → #3 → #4（审查修复）→ #5 → #6 → #7 → #8 → #9。合并前试用时下载 `feat/pr7-release-readiness` 分支 ZIP，解压后将顶层目录改名为 `astrbot_plugin_bond`。当前 `main` 不能安装。
2. 将插件放入 AstrBot 的 `data/plugins/astrbot_plugin_bond/` 目录，保持目录名为 `astrbot_plugin_bond`，再重载插件。
3. 首次收到可信 Bot 的群事件后，Bond 才会为该 Bot 初始化 `group.db` 和 `global.db`。面板只显示已经产生可信事件的 Bot。
4. 初次运行保持以下开关关闭，确认本地管理和公开称谓查询正常后再单独启用需要的功能。

| 配置项 | 默认建议 | 说明 |
| --- | --- | --- |
| `assessment.enabled` | `false` | 自动调用模型评估三维互动 |
| `assessment.small_provider_id` | 空 | 启用评估时必填 |
| `assessment.large_provider_id` | 空 | 可选的复核模型 |
| `inject.enabled` | `false` | 是否向回复追加关系资料 |
| `livingmemory.enabled` | `false` | LivingMemory 总开关 |
| `livingmemory.read_enabled` | `false` | 独立读取开关 |
| `livingmemory.write_enabled` | `false` | 独立写入开关 |

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
