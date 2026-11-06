# 开发约定

## 目标与范围

本项目为 `astrbot_plugin_bond`，面向 AstrBot 社区。当前已实现 Bot/作用域隔离、三维关系、分级评估、人格适配与原生管理面板；LivingMemory 集成已按维护者决策移除。使用范围见 `README.md`，验证记录见 `docs/ACCEPTANCE.md`。默认关闭评估、注入及麦麦联动，不宣称可生产使用。

用户已授权按 PR0 至 PR7 分阶段开发、测试与创建 PR。后续按可审查阶段交付，不自动合并 PR、不自动部署；正式版本的自动发布按下述已授权工作流执行。

## 架构约束

- 保持 `main.py → api/ → services/ → core/ + storage/` 单向分层。
- `main.py` 负责装配与钩子转发，业务逻辑放服务层。
- 关系变动经 `FavorService` 统一入口；保留事务、撤销及版本迁移。
- 保留 SQLite WAL；分值、当日额度、流水及冷却须原子提交。
- Bot 和用户身份取自可信平台事件，不从聊天文本解析。
- 后台任务经 `TaskRegistry` 管理，评估失败不阻断回复。
- 注入只追加上下文，不覆盖 AstrBot 当前人格或接话策略。
- 不恢复 LivingMemory 集成、探测或相关配置；任何外部记忆接入须重新确认需求。
- 页面使用 AstrBot 原生插件视图；所有写 API 须在服务端校验权限。

## 检查与提交

- 插件目录须为全小写的 `astrbot_plugin_bond`，避免测试导入错误目录。
- 使用 Python 3.12 开发；命令及验证边界见 `docs/DEVELOPMENT.md`。
- 离线测试不导入要求完整框架的入口模块；真实框架 smoke 分环境、分进程运行。
- 修改 `main.py` 后执行 `python scripts/check_project.py`。
- 修改行为时验证成功与失败路径；运行全量测试和锁定版本的 Black、Ruff 格式检查。
- 提交前运行公开内容检查；打包前提交源码，归档只读取 HEAD。
- 数据迁移同步修改 MIGRATIONS、SCHEMA_VERSION 和 pragma_version 分支。
- 代码标识符英文，使用说明和注释用中文；注释解释契约，不记录开发过程。
- 使用中文 Conventional Commits；存在尚未合并的前置阶段时，可基于其分支创建堆叠 PR，并在 PR 中明确依赖。不得自动合并。
- PR 说明改动、原因、关键边界和实际测试；未验证内容须明确说明。
- `metadata.yaml` 与 `@register()` 的标识、版本、作者和源码地址保持一致。
- 后续交付使用正式版本号，不加 beta/dev 后缀；普通修复递增补丁号（例如 0.2.1 → 0.2.2），同步更新 CHANGELOG。CHANGELOG.md 只保留当前版本的使用者中文说明，AstrBot 与 Release 共用；历史说明从 Git 和 Releases 查看。版本 PR 不授权自动合并，维护者合并正式版本变更后由已授权工作流发布。
- 工作流只在任务授权范围内修改；main 的正式版本递增且 CI、发布检查通过后，工作流创建 annotated tag 和 Release；同版本提交不发布，不覆盖已有标签或 Release。保留已有 tag 的手动恢复入口，执行前核对远端状态。

## 公开内容

提交范围仅含源码、测试、工程配置及公开说明。不得提交凭据、真实账号、聊天摘录、运行数据库、日志或部署环境信息。测试使用合成数据。外发数据与用途须在 README 说明，不添加遥测。

README 面向使用者，先说明用途、实际功能、操作和限制。架构约束遵循本文，开发与验证方法见 `docs/DEVELOPMENT.md`，使用 Nofluff 中文表达。
