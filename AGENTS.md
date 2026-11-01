# 开发约定

## 目标与范围

本项目为 `astrbot_plugin_bond`，面向 AstrBot 社区。当前已在 PR-0 工程基线上实施 PR-1 Bot/作用域隔离；三维关系与通用人格适配仍待实现；需求见 `docs/PRD.md`。默认关闭评估、注入及麦麦联动，不宣称可生产使用。

用户已授权本阶段导入公开上游源码、补齐 CI 和中文 README。后续按可审查阶段交付，不自动合并 PR、不自动部署或发布。

## 架构约束

- 保持 `main.py → api/ → services/ → core/ + storage/` 单向分层。
- `main.py` 负责装配与钩子转发，业务逻辑放服务层。
- 关系变动经 `FavorService` 统一入口；保留事务、撤销及版本迁移。
- 保留 SQLite WAL；分值、当日额度、流水及冷却须原子提交。
- Bot 和用户身份取自可信平台事件，不从聊天文本解析。
- 后台任务经 `TaskRegistry` 管理，评估失败不阻断回复。
- 注入只追加上下文，不覆盖 AstrBot 当前人格或接话策略。
- LivingMemory 为可选增强，不添加强制依赖，不访问其内部数据库。
- 页面使用 AstrBot 原生插件视图；所有写 API 须在服务端校验权限。

## 检查与提交

- 插件目录须为全小写的 `astrbot_plugin_bond`，避免测试导入错误目录。
- 使用 Python 3.12 开发；命令及验证边界见 `docs/DEVELOPMENT.md`。
- 离线测试不导入要求完整框架的入口模块；真实框架 smoke 分环境、分进程运行。
- 修改 `main.py` 后执行 `python scripts/check_project.py`。
- 修改行为时验证成功与失败路径；运行全量测试和锁定版本的 Black。
- 数据迁移同步修改 MIGRATIONS、SCHEMA_VERSION 和 pragma_version 分支。
- 代码标识符英文，使用说明和注释用中文；注释解释契约，不记录开发过程。
- 使用中文 Conventional Commits；存在尚未合并的前置阶段时，可基于其分支创建堆叠 PR，并在 PR 中明确依赖。不得自动合并。
- PR 说明改动、原因、关键边界和实际测试；未验证内容须明确说明。
- `metadata.yaml` 与 `@register()` 的标识、版本、作者和源码地址保持一致。
- 工作流只在任务授权范围内修改；发布须由维护者合并后创建 annotated tag。

## 公开内容

提交范围仅含源码、测试、工程配置及公开说明。不得提交凭据、真实账号、聊天摘录、运行数据库、日志或部署环境信息。测试使用合成数据。外发数据与用途须在 README 说明，不添加遥测。

README 面向使用者，先说明用途、实际功能、操作和限制。架构与验证细节分别放 `ARCHITECTURE.md` 和开发指南，使用 Nofluff 中文表达。
