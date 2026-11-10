# 开发与交付

贡献入口见 [CONTRIBUTING](../CONTRIBUTING.md)。本文说明环境、代码位置、检查范围和发布步骤；功能与配置以 [用户指南](USER_GUIDE.md) 为准。

## 准备环境

使用 Git、Python 3.12 和 Node.js 22。仓库目录必须为全小写的 `astrbot_plugin_bond`，测试会按这个包名导入代码。Node 仅用于现有页面测试，无需安装 npm 依赖或构建前端。

```text
git clone https://github.com/timeance/Astrbot_plugin_bond.git astrbot_plugin_bond
cd astrbot_plugin_bond
```

Windows PowerShell：

```powershell
$env:PYTHONUTF8 = "1"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Linux / macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
source .venv/bin/activate
```

下文的 `python` 均指该虚拟环境的解释器。Windows 可直接将它替换为 `.\.venv\Scripts\python.exe`，无需修改 PowerShell 执行策略。测试产物放在本次专用目录，例如被 Git 忽略的 `artifacts/`；不要使用宿主实际数据目录。

| 依赖文件 | 用途 |
| --- | --- |
| `requirements.txt` | 插件运行依赖：Pillow 生成排行图片，aiohttp 有界获取 QQ 头像，Quart 提供页面 API。AstrBot 由宿主提供。 |
| `requirements-test.txt` | 离线测试使用的 pytest、PyYAML、图片/头像测试依赖和 Windows 时区数据。CI 与本地共用版本。 |
| `requirements-dev.txt` | 引用测试依赖，再加入固定版本的 Black、Ruff。普通开发安装这一份即可。 |

离线环境不安装 AstrBot，不连接平台或模型。真实框架检查使用另一个环境，避免测试桩掩盖导入错误。Python 3.10 只验证核心层兼容性，不代表宿主支持该版本。

## 从哪里修改

调用方向为 `main.py → api/ → services/ → core/ + storage/`。`main.py` 负责装配与钩子转发，业务变化进入服务层。

| 修改内容 | 代码入口 | 优先查看的测试 |
| --- | --- | --- |
| 关系规则与称谓 | `core/`、`services/favor_service.py` | `test_relation_policy.py`、`test_relation_lexicon.py`、`test_relation_storage.py` |
| 消息归因与分级评估 | `api/listeners.py`、`services/assessment_service.py` | `test_attribution_metadata.py`、`test_assessment.py` |
| 人格与关系注入 | `services/inject_service.py`、`resources/prompts/` | `test_persona_injection.py`、`test_prompt_service.py` |
| 管理页面和工具 | `api/page_api.py`、`api/llm_tools.py`、`pages/dashboard/` | `test_admin_profile.py`、`test_llm_query_context.py`、`dashboard.test.cjs` |
| Bot、作用域与持久化 | `storage/`、`services/runtime_service.py` | `test_storage_router.py`、`test_runtime_scope.py` |
| 打包和公开内容检查 | `scripts/build_package.py`、`scripts/check_repository.py` | `test_delivery.py`、`test_repository_checks.py` |

表中测试均位于 `tests/`。新增行为先找相邻测试，覆盖成功与失败路径；纯文档改动核对内容、链接和示例即可。

关系修改统一经过 `FavorService`，分值、额度、流水与冷却原子提交。Bot、群和成员身份来自可信平台事件；不从消息正文猜测。保持 Bot 与作用域隔离，私聊不进入自动关系评估、印象和注入。后台任务由 `TaskRegistry` 管理，评估失败不能阻断聊天。

注入只追加本次请求的关系资料，保留宿主人格和接话策略；页面写操作在服务端校验权限。自动评估和诊断默认关闭；新安装的关系注入默认开启，已有显式 `inject.enabled=false` 保留。LivingMemory 集成已移除，不因参考其工程做法恢复相关探测、配置或依赖。

存储变更须同步 `storage/migrations.py` 中的 `MIGRATIONS`、`SCHEMA_VERSION` 与 `storage/pragma_version.py` 的迁移分支，验证升级、失败和回滚边界。亲近度沿用 `favor` 列，熟悉度和信任度独立存储，不维护三维总分。印象、标签、点集与已汇总流水进度原子提交，失败不能推进进度。`PromptService` 使用独立 JSON。升级和回滚前备份整个插件数据目录，不直接操作线上数据库调试。

## 日常检查

在仓库根目录运行：

```text
python -m pytest tests/ -q
node --test tests/dashboard.test.cjs
python scripts/check_project.py
python scripts/check_repository.py
python -m black --check .
python -m ruff format --check .
```

行为、迁移或工程检查有改动时，先运行相关测试，再运行全量检查。两种格式检查使用锁定版本；需要修正时对本次修改的文件运行 `python -m black <文件>` 与 `python -m ruff format <文件>`，最后重新检查。这里的 Ruff 用于格式检查，不代表已启用全部 lint 规则。

公开内容检查读取 Git 跟踪文件的工作区内容；新文件须先 `git add <本次文件>` 才会被扫描。它检查运行数据文件、常见凭据模式、部署信息和部分疑似号码，只输出文件、行号与类别。通过扫描不等于已完成隐私审查，提交前仍须查看完整差异；发现真实凭据时先撤销或轮换，再清理公开记录。

打包从已提交的 `HEAD` 生成。先检查差异并提交本次修改，再运行：

```text
python scripts/build_package.py --output artifacts/bond-check.zip
```

脚本拒绝已跟踪文件中的未提交改动；未跟踪文件不会进入归档。它检查运行文件、Logo、依赖声明、许可和提示词资源，排除开发及运行数据，并限制 ZIP 不超过 16,000,000 字节。成功后生成同名 `.sha256` 校验和。测试、脚本、工作流和开发依赖不进入安装包；参与开发请克隆仓库。

## 原生面板合成预览

在仓库根目录运行以下命令，使用 Python 标准库启动只读模拟服务器。

```text
python scripts/preview_dashboard.py --port 8765
```

本机浅色入口为 `http://localhost:8765/?theme=light`，深色为 `http://localhost:8765/?theme=dark`。点击原生“详情”查看成员。按 `Ctrl+C` 停止。服务器绑定 `localhost`，只读取 `pages/dashboard/index.html`、`app.js`、`app.css` 与 `tests/fixtures/dashboard_demo.json`，在响应中注入模拟 bridge 和醒目的演示标识。Bot、群、800000xxx 演示号、昵称、印象、标签、流水和调用统计全部合成。

原生页面使用 Vue 3.4.38 的既有 CDN。需要完全离线渲染时，将已有的同版本 `vue.global.js` 放在本机并传入路径，例如以下命令。此参数只替换当前演示 HTML 的 Vue 地址，演示脚本依赖 Python 标准库。

```text
python scripts/preview_dashboard.py --port 8765 --vue-js artifacts/vue.global.js
```

模拟 `window.AstrBotPluginPage` 提供 `ready()`、`getContext()`、`onContext(callback)`、`apiGet(endpoint, params)`、`apiPost(endpoint, body)`。上下文提供 `isDark`，只读请求发到同源 `/api/<endpoint>`，返回与原生页面一致的 `success` 及业务字段，并附 `demo=true`、`read_only=true`。

| GET 端点 | 合成响应与支持参数 |
| --- | --- |
| `bots`、`settings` | Bot 列表、页面配置、合成 Provider 和配置版本。 |
| `groups`、`today_summary` | 群列表及今日调用、变动成员统计。 |
| `users` | `bot_id`、`scope_mode`、`group_id`、QQ `search`、`include_zero` 与分页。 |
| `member` | 同一组群与成员数据，近期评审由合成流水派生。 |
| `logs` | 按群、QQ、`fuzzy`、`dimension`、自动评估来源与数量筛选。 |
| `models`、`assessment_stats` | 已保存与运行中的合成评估配置、调用及来源统计。 |
| `prompts`、`diagnostics` | 合成模板、关闭的空诊断状态，可导出空合成诊断。 |

除 `bots` 与 `settings` 外，读取按模拟 Bot 和 `per_group` 空间校验。演示只提供按群空间，切换空间通过原生写接口返回拒绝提示。所有写方法及备份、恢复、撤销等动作返回 HTTP 405 和“仅演示，不影响真实环境”的说明。服务只提供明确允许的静态文件与合成读取接口。

fixture 在每次启动时将合成时间整体平移到启动时点，临时关系保留相对有效期。修改 fixture 后重新启动预览以载入新值。关系数值与衰减轴为预设场景，近期评审从同一流水读取，今日统计按同一合成调用和事件计算。此预览验证页面协议与合成展示，实际宿主认证、数据库迁移、模型效果与聊天链路分别按下文验证。

## 真实框架与合成回放

另建 `.venv-framework`，分别安装 CI 矩阵中的 `AstrBot==4.28.0` 和 `AstrBot==4.29.4`，使用对应环境的解释器分进程执行以下命令。每次使用新的空输出目录，示例目录已用过时换一个名称。版本固定用于验证基线，插件元数据只声明最低要求 `>=4.28`。

运行前将 `BOND_EXPECTED_ASTRBOT_VERSION` 设为本次框架版本，例如 PowerShell 的 `$env:BOND_EXPECTED_ASTRBOT_VERSION = "4.29.4"`，或 Bash 的 `export BOND_EXPECTED_ASTRBOT_VERSION=4.29.4`。脚本核对实际导入版本，输出也使用实际版本号。本地已有源码检出时可通过 `PYTHONPATH` 指向对应框架目录；离线 pytest 进程须清除该变量，防止混用真实框架与测试桩。

```text
python scripts/smoke_astrbot.py --session-dir artifacts/framework-run --runtime-dir artifacts/framework-run/runtime
python scripts/smoke_message_metadata.py --session-dir artifacts/metadata-run
python scripts/smoke_diagnostics.py --runtime-dir artifacts/diagnostics-run
python scripts/smoke_relation_runner.py --runtime-dir artifacts/relation-run
python scripts/smoke_passive_familiarity.py --runtime-dir artifacts/passive-run
```

这些脚本分别验证真实框架装配与卸载、OneBot 合成消息转换、诊断认证拒绝、脱敏和空间隔离，以及 runner 关系快照与被动熟悉度。装配检查还使用真实人格选择器验证会话人格切换和显式禁用，使用合成 JWT 经宿主认证函数和 FastAPI/Quart 桥验证原生页面访问。它们使用合成数据与离线 Provider，完整宿主登录、浏览器和 Angel Heart 同装仍须单独验收。

关系合成回放在离线环境执行：

```text
python scripts/replay_relations.py --arms baseline model-a --output artifacts/replay-run
```

回放使用真实入口、评估服务与 SQLite，模型返回预置响应。baseline 固定旧默认规则，各实验臂的入口来自当前检出；跨版本比较须保留源码摘要和各自结果。`results.json` 保存逐事件结果与逻辑指标，`timings.json` 单列非确定性耗时。方法与局限见 [关系建模短论文](research/EMOTION_MODEL_PAPER.md)，基线见 [合成回放记录](research/BASELINE.md)。

发布默认值对照可运行 `python scripts/replay_recommended_defaults.py --output artifacts/defaults-run`，使用合成事件、固定时钟与真实服务/SQLite；再次运行须换新目录。它检验参数后果，不检验模型语义准确率。

诊断导出可用 `python scripts/summarize_diagnostics.py <导出.json> --output <本地摘要.json>` 汇总。只统计实际保留的记录，不据此推断未知 Token、语义准确率或主回复延迟；真实导出文件不提交。

## 自动检查

| 工作流 | 触发时机 | 检查内容 |
| --- | --- | --- |
| CI | PR、main 推送、手动 | Linux Python 3.10/3.12 和 Windows Python 3.12 离线测试、Node 22 页面测试、元数据、公开内容、Python 3.12 安装包；独立矩阵验证 AstrBot 4.28.0、4.29.4 装配与全部 smoke。 |
| Code Format Check | PR、main 推送、手动 | `requirements-dev.txt` 锁定版本的 Black 与 Ruff 格式检查。 |
| CodeQL | PR、main 推送、每周、手动 | Python 静态安全分析。 |
| release | main 版本变更通过 CI 后；或 `v*` tag、手动指定已有 tag | 检查当前版说明、源码、格式与归档，验证 annotated tag 后发布 ZIP、SHA-256 和 CHANGELOG。 |

工作流固定 Action 提交、设置超时和最小权限。PR 检查不使用仓库密钥或 `pull_request_target`；checkout 不保留 Git 凭据。Dependabot 每月提出 Action 和 Python 依赖更新，由维护者结合 CI 审阅；依赖更新不自动合并。真实框架版本单独固定，升级兼容基线时需同时更新 smoke 说明和验证记录。

维护者可在仓库设置中将这些检查设为 main 合并的必需条件；提交工作流文件本身不会开启分支保护。

## 提交与发布

从最新 `main` 建分支，使用中文 Conventional Commits。前置 PR 尚未合并时写明依赖；一次提交只包含本次相关文件。版本以 `metadata.yaml` 和 `main.py` 为准，行为变更同步版本与 CHANGELOG；纯文档、测试和工程流程改动不单独升运行版本。

维护者通过审阅并合并正式版本 PR 决定发布：

1. 同步修改 `metadata.yaml`、`main.py` 的正式版本号，并用面向使用者的中文替换 `CHANGELOG.md`，只保留当前版本。说明本次变化、必要操作和限制；历史留在 Git 与已经发布的 Releases 中。
2. 合入 main 后，CI 的完整测试矩阵及真实框架作业成功，才调用发布工作流。它比较本次推送前后的元数据：版本不变时跳过，正式版本递增时继续；启用流程的 0.5.1 不补发。
3. 发布工作流完成现有离线、页面、格式、公开内容及安装包检查后，仅发布作业获得写权限。作业校验归档，创建 annotated tag，验证版本、目标提交及 main 归属，再推送标签；同一作业继续创建 Release，不依赖标签推送触发另一个工作流。
4. Release 使用同一份 `CHANGELOG.md`，附安装 ZIP 与 SHA-256；附件上传完成前保持草稿，全部成功后才公开。已有标签不覆盖，已有 Release 不更新或替换附件。普通提交、PR 检查和本地打包不发布，也不提交插件商店。
5. 查看 Release 附件及 Actions 结果。检查失败不发布；推送、上传或公开失败可能留下标签或草稿。先核对远端状态，再决定恢复，避免盲目重跑。保留 `v*` 标签及手动指定已有 annotated tag 的发布入口，仍执行全部构建与标签验证；已有草稿须先由维护者处理，流程不会覆盖。发布工具单独检出自本次工作流修订；目标标签源码使用自身的版本检查及打包脚本，因此旧标签的多版本 CHANGELOG 不会被新的当前版本格式约束拒绝。

结构检查和本地干跑不代表真正发布成功；首次自动发布仍须核对远端结果。真实平台验收边界见 [验收说明](ACCEPTANCE.md)。停用自动发布时移除 CI 中的 release 调用；已发布版本和标签保持原样。

久别旧友现状可运行 `python scripts/replay_reconnection.py --output artifacts/reconnection-run`；使用新的空输出目录，对照写实/Galgame在90天沉默与三次友好互动后的三维值。研究结论见 [久别旧识评估](research/RECONNECTION_ASSESSMENT.md)，不启用额外回暖机制。

0.3.8窗口、增长与三维衰减长期回放：`python scripts/replay_framework.py --output artifacts/framework-replay`。输出固定时钟的7/30/180天逻辑结果，使用预置模型响应和真实服务/SQLite；不访问网络。连续运行两次并比较results.json，应完全一致。迁移及回退见 [框架合同](ASSESSMENT_FRAMEWORK.md)。


## 0.5.0 行为与消息快照验证

`python scripts/export_behavior_cases.py --output <新空目录>` 导出14类场景、156组无Bond/旧提示/新提示请求与两条SQLite持久化合成轨迹。输入共用同一人格和历史，不调用模型，输出与评分留空；方法见 [行为验收](P0_BEHAVIOR_EVALUATION.md)。

在独立 AstrBot4.28.0 环境运行 `python scripts/smoke_relation_runner.py --runtime-dir <新目录>`，通过真实 ToolLoopAgentRunner 和离线记录 Provider 验证初始请求、工具续跑及空输出重试的最终上下文。脚本禁用网络，主入口钩子直接调用，未启动完整平台 pipeline，也不等于真实QQ或真实模型验收。离线pytest中的监听器替身只验证钩子契约，不能冒充这一边界。

## 0.5.5 被动熟悉度验证

离线新增测试为 `tests/test_passive_activity.py`、`tests/test_passive_storage.py`、`tests/test_passive_service.py`，覆盖只结算F、不刷新保温、共享额度、持久冷却和消息时序。真实AstrBot4.28.0环境运行 `python scripts/smoke_passive_familiarity.py --runtime-dir <新空目录>`，验证实际配置、消息对象、主钩子与后续直接评估。所有数据合成，禁止网络；不替代真实平台或第三方插件同装测试。规则与交付边界见 [群聊活跃熟悉度](PASSIVE_FAMILIARITY.md)。
