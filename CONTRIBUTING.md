# 参与贡献

可以通过报告问题、补充复现步骤、修正文档或提交代码参与 Bond 开发。

## 报告问题与提出建议

先查看 [使用说明](README.md) 和 [已有 Issue](https://github.com/timeance/Astrbot_plugin_bond/issues)。同一问题请在原 Issue 补充信息。

- [报告 Bug](https://github.com/timeance/Astrbot_plugin_bond/issues/new?template=bug_report.yml)：填写 Bond、AstrBot 版本和复现步骤，分别说明预期与实际结果。不确定的环境信息可以写“不确定”。
- [提出功能建议](https://github.com/timeance/Astrbot_plugin_bond/issues/new?template=feature_request.yml)：说明使用场景、遇到的限制，以及怎样判断问题已解决。
- 使用咨询或文档问题也可以创建普通 Issue；文档小修正可以直接提交 PR。

安全漏洞按 [安全报告说明](SECURITY.md) 处理，不在公开 Issue 中披露可利用细节。

## 公开信息与隐私

Issue、PR、提交记录和附件都可能被公开访问。只提供定位问题所需的信息：

- 配置只列相关字段和值，不上传完整配置。删除 API Key、Token、Cookie、密码、Webhook 地址，以及带凭据的 URL。
- Bot、群和成员统一用 `bot-A`、`group-A`、`user-A` 等虚构标识代替；同一对象在复现步骤中使用相同标识。
- 不上传真实聊天记录、成员印象、数据库、备份、诊断导出或原始运行日志。异常信息只摘录已脱敏的错误类型和必要调用栈，去掉聊天正文、账号、服务器地址及本机目录。
- 截图先遮盖头像、昵称、账号、群名和配置值；需要展示对话时，用人工编写的示例重新复现。

不能确认是否已脱敏时，先描述现象，不附文件。若凭据已经公开，先撤销或轮换凭据；删除附件或修改文字不能保证所有副本消失。

## 开发与提交 PR

1. 从最新 `main` 建立自己的分支。存在未合并的前置 PR 时，在说明中写清依赖和合并顺序。
2. 阅读 [开发约定](https://github.com/timeance/Astrbot_plugin_bond/blob/main/AGENTS.md)，按 [开发与验证指南](docs/DEVELOPMENT.md) 准备环境、运行检查。开发使用 Python 3.12，插件目录名保持 `astrbot_plugin_bond`。
3. 一次 PR 解决一个明确的问题。涉及关系计算、数据迁移、权限或外部服务接入的改动，先用 Issue 说明方案与影响；小型修复和文档修改可直接提交。
4. 按改动范围验证。行为修改覆盖成功与失败路径，并运行开发指南要求的全量检查；纯文档修改检查内容和链接。测试使用合成数据及隔离目录。
5. 使用中文 Conventional Commits，例如 `fix: 修复关系撤销后的显示`、`docs: 补充配置说明`。按 PR 模板写明问题、变更、实际测试结果和未验证范围；页面修改附脱敏截图。

提交前检查差异，移除临时文件、运行数据和无关改动。使用 AI 辅助时，也请逐项核对生成内容和测试结果，不把模拟结果写成真实平台或模型验收。

遵循现有分层和统一的关系修改入口。涉及配置、存储或对外行为时，同步更新对应说明；版本调整按开发指南处理。不单独为文档和模板改动升版。

合并、打 tag 和创建 Release 由维护者决定。提交 PR 或通过检查不代表已发布。

## 来源与许可

引入外部代码、图片或文档前，核对其许可是否允许使用，并保留所需声明和来源。项目现有许可与来源见 [LICENSE](LICENSE)、[NOTICE](NOTICE) 和 README 的“许可与来源”。
