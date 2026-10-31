# PR-0 工程审查

审查日期：2026-10-31。此报告区分已验证事实与后续计划，不代表产品已验收。

## 基线与交付状态

- Xinxian：`c727e861472f7901ce1f87c1e27686f6655a96ec`，本地开发保留完整 Git 历史；远程目标仓库为独立初始化仓库，按源码快照提交，来源可由此提交追溯。
- LivingMemory：`a1e9c20a8c5fab93d6143f51be26fb1bbf8143ec`，仅审查，未复制实现。
- 框架源码：AstrBot `v4.28.0`（`a412146401426c0cdff8bbefb8627a03da519da8`）；另检查当前 main 的 Web API 兼容层。
- 本地离线上游基线：Python 3.12.14、pytest 9.2.0，**228 passed**；Black 26.5.1，39 个 Python 文件无须格式修改。
- 上游同提交 Actions：CI `34641960931` 成功，格式门 `34641960969` 成功，release 工作流 `34641960047` 失败。未据此宣称发布链可用。
- 目标仓库现为用户创建的公开仓库 `timeance/Astrbot_plugin_bond`，初始 main 为 `3e84bc864a6fe3af7129479143ca1be9ab63f14d`，仅含标准 AGPL LICENSE。PR-0 以该提交为父提交导入源码，不覆盖 main，不宣称具有 GitHub 原生 Fork 关系。原许可证内容保持不变。
- 远程 PR 与 Actions 状态以 GitHub 记录为准；本阶段不创建 tag、不发布、不部署或合并。

## 复用决策

| 类别 | 实现 | 决策 |
| --- | --- | --- |
| 直接复用 | `core/decimal.py`、`taskregistry.py`、`judge_context.py` | 复用一位小数边界、后台任务生命周期、结构化历史纯文本提取 |
| 直接复用 | SQLite WAL、busy_timeout、锁、版本迁移、事务回滚模式 | 保留现有后端，原位扩展，不另造平行存储服务 |
| 局部改造 | `ImpressionService`、流水与撤销 | 保留印象/标签/关键事件；所有访问先绑定可信 Bot 与固定作用域，修正刷新节奏 |
| 局部改造 | `JudgeService`、`InjectService` | 保留异步与失败降级；重做事件输出、证据验证、分级 Provider 和人格中立模板 |
| 局部改造 | 原生 Dashboard、命令、CI | 保留宿主页面和测试门；扩展三维编辑、权限、Bot/作用域选择与统计 |
| 必须重构 | `FavorRecord`、数值写入契约、经济规则 | 单值不能代表三维；本地策略输出增量，禁止模型直接设分，避免竞争双写 |
| 必须重构 | 全局单库装配及路由 | 每 Bot 的 group/global 两个文件；评估排队时固定路由，不自动导入旧库 |
| 必须重构 | 公开查询与 LLM 工具 | 目前公开数值，不满足合同；必须改为称谓和无数字本群 Top 10 |

三维是 familiarity/affinity/trust，不是任意关系图。无需增加通用图结构、人格维度主键或新的大型框架。

## 发现的问题与实施顺序

1. `main.py` 全局装配一个数据库；所有日志、冷却、印象沿用 `(group_id,user_id)`。PR-1 要在所有入口绑定 Bot，不能只增加一个无人使用的 router。
2. `api/listeners.py` 后台闭包仍持有活动事件；必须在排队前固定真实账号、作用域、消息 ID、UMO 和其他可信输入。
3. 冷却检查与 touch 是两次独立调用；当前同步 SQLite 实现没有内部 await，单事件循环内不让出，但抽象接口不能保证原子预留。新评估管线应由事务实现原子占位并补并发测试。
4. `ImpressionService.maybe_refresh` 只看最近 interval 条流水，达到阈值后连续评审会每次都触发汇总，需按上次汇总游标控制，避免重复成本。
5. 非中性评估要求证据非空，却未核对证据是当前作者原文子串；转发/引用文本只靠正则清洗，不能证明作者归属。PR-2/3 必须校验结构化消息与证据。
6. 部分配置直接 float/int 转换，缺少有限值与安全范围校验；管理员数值、路径和作用域必须在服务端验证。
7. `SQLiteBackend._upsert` 的列名来自内部固定调用点，当前不是可直接利用的外部 SQL 注入；但它与 AGENTS 全字面量 SQL 规则不一致，扩展时不得让用户输入变成列名。
8. `PageApi` 假设宿主鉴权，尚无覆盖写入口权限的集成测试；旧 undo 只按数据库内 log ID，路由改造必须绑定选中数据库。禁止据前端隐藏按钮推断安全。
9. 原测试没有真实框架面覆盖，不证明插件可加载、页面可访问、Angel Heart 执行顺序正确。应另设框架集成测试环境，保持离线核心测试的 logger 桩不变。
10. README 对数据流描述不完整：评估可发送会话上下文，印象汇总会发送有限的历史流水。产品文档必须说明目的、截断与关闭方式。

## AstrBot 与其他插件

`StarTools.get_data_dir(plugin_name)` 支持显式插件标识；新目录不读取 Xinxian 数据。`event.get_self_id()` 来自结构化 `message_obj.self_id`，不能从昵称/正文解析身份。`register_web_api` 在 v4.28.0 仍存在，并通过宿主认证调用注册 handler；框架提供 Quart 兼容调用层，但存在不等于页面已实测。原 `>=4.0,<5` 兼容声明过宽，开发基线收窄至 `>=4.28,<5`，已验证 4.28.0 的插件装配，其他版本及完整宿主仍待验证。

Angel Heart 优先级和真实回复对象必须在 PR-4 结合其当前源码实测。纯追加 system_prompt 是必要条件，不能单凭优先级数值宣称兼容。Bond 不参与唤醒/接话频率。

## LivingMemory 最小可靠方案

其元数据插件名为 `astrbot_plugin_livingmemory`、版本 2.8.2，Star 通过 metadata 注册。核心初始化为异步；仅发现插件并不代表就绪。可以通过宿主注册表重新获取实例，检查 activated 与初始化状态，但不能缓存失效实例。

它公开给模型的工具有 recall/memorize/forget/read-evidence；没有稳定的 `self.api` facade。`initializer.memory_engine.search_memories(query,k,session_id,persona_id)` 是底层检索面，直接调用会绕过工具策略；查询还可能触发旧 session 数据迁移。不能将“能调用”当成“安全只读”。

`core/memory_scope.py` 包含 allowlist 及 session/user/global 模式；user/global 本来就允许跨会话，别名还能合并身份。底层接口没有独立 bot_id 过滤契约。UMO 含适配器标识，但适配器与真实 Bot 账号的映射、历史重用和结果来源仍需验证。

PR-6 默认只做禁用即零调用的可选探测与状态；在严格来源证明的读取契约通过后，才允许有超时、限长、作用域验证的增强。无法证明 bot/group/subject 隔离的结果全部跳过；全局关系模式不授权跨群原文召回。写入单独验收，要求稳定 event ID/source 幂等与公开接口支持；当前不能宣称写入可用。不得读取或修改 LivingMemory 数据库，不注册重复工具，不添加强制依赖。

## 许可与发布

上游 README 明确写 `AGPL-3.0`，但没有独立 LICENSE。保留原作者 yiwuerxin、原 README 和来源；本地保留 Git 历史，远程导入源码快照，补充标准许可证全文和 NOTICE，不声称取得额外授权。发布前确认归属无冲突、打包保留许可文件，为实际运行版本提供对应源码；不能只链接到缺少部署修改的上游。

真实框架独立环境已执行 `scripts/smoke_astrbot.py`：AstrBot 4.28.0 的真实 Context、AstrBotConfig、StarTools 装配成功，完整 schema 与空配置均保持评估/注入/联动关闭，数据目录正确，schema v9 初始化成功，6 个原生页面 API 注册成功，Quart 请求上下文下空库用户查询成功，两轮卸载均关闭数据库。此测试没有启动完整宿主、页面登录或连接 Bot/Provider，不替代端到端验收。

PR-0 不实施三维语义或自动迁移。旧恋爱/主人经济规则留作可测试的上游实现，评估、注入及麦麦联动默认关闭，避免把这些旧语义当作新产品默认。后续阶段按 `docs/PRD.md` 实施，在验收之前不建议生产安装。

## 工程自动化补充

CI 增加 AstrBot 4.28.0 真实框架装配、元数据一致性、安全默认值及发布包干跑。保留离线测试、格式和敏感数据检查。CodeQL 对公开仓库的 PR/main 及每周执行；Dependabot 每月检查 Actions 更新。发布链校验 annotated tag、版本及 main 祖先关系，保留许可证和来源信息并生成 SHA-256；本阶段不触发发布。具体命令见 [开发指南](DEVELOPMENT.md)。

公开提交仅包含继承的公开源码、测试及本项目工程说明。原上游过程笔记不导入；开发约定和架构说明已重写，不携带部署流程、私有配置或真实账号。已复核新增改动并通过四项敏感数据检查。

## 本次交付验证

本地 Python 3.12：236 项测试通过，Black 检查 43 个 Python 文件通过，元数据/默认值/赋值顺序及发布包检查通过，四项敏感数据检查通过。AstrBot 4.28.0 独立装配与卸载检查通过。未执行真实 Bot/Provider/Angel Heart 联动、完整宿主页面登录或真实发版。

首次远程写入因 GitHub App 未授权新仓库而返回 403；用户补充授权后，仓库已进入授权清单，PR 分支创建成功。main 仍为原始许可证提交。远程提交、PR 和 CI 结果以 GitHub 页面记录为准；本地检查不能替代托管 Actions 结果。
