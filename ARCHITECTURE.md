# 工程结构

当前实现继承 Xinxian 的单值关系模型。本文描述现有代码；三维关系及数据隔离目标见 [需求文档](docs/PRD.md)，当前缺口见 [工程审查](docs/PR0_AUDIT.md)。

## 分层

| 目录或入口 | 职责 |
| --- | --- |
| `main.py` | 插件注册、依赖装配、生命周期、框架钩子转发 |
| `api/` | 命令、平台事件、LLM 工具、页面接口与跨插件 facade |
| `services/` | 数值变动、评估、成员印象和提示词注入 |
| `core/` | 关系类型、精度、衰减、身份判断、配置与任务管理 |
| `storage/` | SQLite、表结构、版本迁移和事务 |
| `pages/dashboard/` | AstrBot 原生插件页面 |
| `resources/prompts/` | 评估与注入模板 |

依赖方向为 `main.py → api/ → services/ → core/ + storage/`。核心逻辑不依赖框架运行面，便于离线测试。

## 写入与后台任务

数值变化经 `FavorService` 处理。写入前应用精度、限幅及经济规则；数值、当日额度、流水和冷却在同一事务内提交。撤销也使用事务，避免重复撤销产生多次变动。

SQLite 保留 WAL、busy timeout 和版本化迁移。当前仅有一个 `bond.db`；按 Bot 真实账号及 group/global 分库仍是后续改造。

LLM 评估通过受管理的后台任务进行，异常不进入聊天回复链。当前缺少分级模型、完整证据校验与请求成本管理，不将旧评估默认开启。

## 身份、页面和注入

身份来自平台事件。页面注册到 AstrBot 原生管理面板；写入权限与多 Bot 路由将在后续阶段补充完整验收。提示词注入只追加关系上下文，保留宿主人格与其他插件上下文。

当前未实现 LivingMemory 适配。未来只有可证明来源、Bot 和作用域的外部记忆才能补充上下文，不允许其直接写关系状态。

## 来源与验证

上游基线为 [Xinxian c727e86](https://github.com/yiwuerxin/astrbot_plugin_xinxian/commit/c727e861472f7901ce1f87c1e27686f6655a96ec)。更早的架构说明与版本历史保留在上游，本文不继承其中特定人格和部署流程。

测试、CI 与发布门禁见 [开发指南](docs/DEVELOPMENT.md)。
