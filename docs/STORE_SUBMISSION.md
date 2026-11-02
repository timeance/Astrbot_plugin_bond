# Bond 插件商店提交准备

核查日期：2026-11-03。插件版本：0.3.0。本文提供提交资料与验收清单；尚未向商店提交。

## 提交入口

在 [AstrBot Cloud 发布页](https://cloud.astrbot.app/publish) 登录后提交 GitHub 仓库地址，平台会读取 `metadata.yaml`。当前官方发布指南已改用 Cloud；旧索引仓库标记为过时，不再按旧 Issue 模板提交。[发布指南](https://docs.astrbot.app/dev/star/plugin-publish.html) · [旧索引说明](https://github.com/AstrBotDevs/AstrBot_Plugins_Collection/blob/main/README.md)

Cloud 登录后的账号归属、重名检查及额外表单字段仍需维护者确认。准备完成不等于已经上架。

## 可复制的提交资料

以下字段与 [metadata.yaml](../metadata.yaml) 一致：

```yaml
name: astrbot_plugin_bond
display_name: Bond 关系系统
short_desc: 按 Bot 隔离的三维关系与成员印象
desc: 保存熟悉度、亲近度、信任度与成员印象，支持分级评估、人格适配及管理面板。
version: "0.3.0"
author: yiwuerxin, Timeance
repo: https://github.com/timeance/Astrbot_plugin_bond
tags:
  - 关系系统
  - 群聊
  - 人格适配
support_platforms:
  - aiocqhttp
astrbot_version: ">=4.28,<5"
```

若表单提供较长的介绍栏，可使用：

> Bond 为长期多人群聊记录相处的痕迹。它分别保存 Bot 对每位成员的熟悉度、亲近度与信任度，用 55 个关系词条描述不同状态，再交由当前人格自然表达。支持群内查询、分级评估和原生管理面板，可以调整、撤销或临时覆盖关系；不同 Bot 的记录独立保存。自动评估、关系注入与诊断默认关闭，私聊不建立关系记录。当前支持 OneBot aiocqhttp、AstrBot >=4.28,<5；启用评估会调用所选模型并发送必要消息与上下文，真实群聊表达效果仍待验收。

市场规范要求名称、作者、版本、仓库地址和描述；展示名、短简介、标签、Logo、平台和版本范围属于可选字段。市场身份包含 `author/name`，提交时保留现有作者及插件标识。[市场规范](https://docs.astrbot.app/dev/plugin-market/2026-06-27.html)

## 仓库已准备的内容

| 内容 | 位置与范围 |
| --- | --- |
| 使用说明 | [README](../README.md) 包含安装、配置、群聊查询、数据隔离、模型数据外发及已知限制。 |
| 图文介绍 | [关系词典](RELATION_LEXICON.md) 和 [情感建模短论文](research/EMOTION_MODEL_PAPER.md) 说明状态含义、实现与合成实验结果。 |
| 运行依赖 | [requirements.txt](../requirements.txt) 声明 Pillow、Quart，最低版本与 AstrBot 4.28.0 的宿主依赖一致；绘图、测试工具不列为插件运行依赖。 |
| Logo | 根目录 [logo.png](../logo.png) 为 1254×1254 PNG，符合 1:1 要求；官方推荐 256×256，并未要求必须为该尺寸。 |
| 许可与来源 | [LICENSE](../LICENSE)、[NOTICE](../NOTICE) 及 README 保留上游署名、来源与 AGPL v3 说明。 |
| 代码格式 | 本次使用 Ruff 0.15.22 和 Black 26.5.1 检查，92 个 Python 文件均通过；6 个文件仅调整格式，语法树与修改前一致。 |
| 可重复检查 | [开发指南](DEVELOPMENT.md) 提供元数据、测试、合成装配和打包命令；[验收记录](ACCEPTANCE.md) 区分已通过检查与真实运行缺口。 |

公开压缩包须小于或等于 **16MB**。本次补齐文档、标签与依赖声明，并统一少量代码格式；运行逻辑和数据结构不变。提交前核对最终公开分支的 CI 和压缩包，不用旧结果代替新版本检查。

## 提交前待确认

- [ ] 在干净的 AstrBot 4.28.0 / Python 3.12 环境从最终仓库版本安装、重载、停用与卸载，确认依赖、Logo、管理页面和默认关闭状态。
- [ ] 真实 OneBot 群聊验证查询、白名单、私聊不建档、多 Bot/群隔离及消息归因；公开回复不泄漏分值或其他群资料。
- [ ] 完整宿主登录链路验证页面读写、未认证拒绝、调整/撤销、临时关系到期与重启保持、备份恢复。
- [ ] 使用真实 Provider 验证评估、关系注入、人格表达、失败处理与实际用量；如宣传 Angel Heart 同装能力，再验收相应聊天链路。
- [ ] 确认 Cloud 账号、仓库归属、插件身份及最终公开分支；提交前保留试用与限制说明，不宣称上述场景已验收。

这些项目用于验证 Bond 已声明的功能，不代表 Cloud 强制要求同样的测试清单。真实模型与浏览器验收需要实际环境；离线测试和合成装配不能代替。

## 官方依据

以下来源于 2026-11-03 核对，具体提交规则仍以届时官方页面为准：

- [发布指南](https://docs.astrbot.app/dev/star/plugin-publish.html)：Cloud 入口、元数据读取、可选标签及 16MB 包限制。
- [插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)：Logo、平台 key、版本范围、依赖声明、测试与 Ruff 格式化要求。
- [市场 JSON 规范（2026-06-27）](https://docs.astrbot.app/dev/plugin-market/2026-06-27.html)：必填与可选字段、插件身份一致性。
- [AstrBot 4.28.0 依赖声明](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.0/pyproject.toml)：Python >=3.12、Pillow >=11.2.1、Quart >=0.20.0，以及该版本采用的 Ruff 版本。
