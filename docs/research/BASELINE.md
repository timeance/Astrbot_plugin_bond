# 测试与环境基线

日期：2026-11-02。独立检出 main `993e821bd127488820a0f639107f67c0c5402bf4`，工作分支 `test/phase0-relation-evidence`。原工作区保留。创建 PR 前再次查询：开放 PR 为 0。版本 0.2.1，schema 15；未使用生产数据库或用户聊天。

## 本地实际执行

开发环境 Python 3.12.14、pytest 9.2.0、Black 26.5.1、Node v24.13.0。框架环境单独安装 AstrBot 4.28.0，离线测试不与其混用；所有库和缓存位于本次测试目录。

| 命令（目录用占位符替代） | 结果 |
| --- | --- |
| `python -m pytest tests/ -q --basetemp=<基线空目录>` | 修改前 413 passed，8.36s |
| `python -m pytest -q --tb=short --basetemp=<本次空目录>` | Phase0 431 passed，22.66s；新增 18 项 |
| `python -m black --check .` | 74 个 Python 文件通过 |
| `node --test tests/dashboard.test.cjs` | 19 passed |
| `python scripts/check_project.py` | 通过，版本 0.2.1；默认评估、注入及可选联动保持关闭 |
| `python scripts/smoke_astrbot.py --session-dir <本次目录>/smoke --runtime-dir <本次目录>/smoke/runtime` | 通过；真实框架导入、2 Bot/4 SQLite、切换/旧任务、16 API、宿主认证拒绝、备份/恢复、损坏模板与卸载 |
| `python scripts/smoke_message_metadata.py --session-dir <本次目录>/metadata` | 17 个真实转换用例通过；缺失引用故障按设计记录；无网络/子进程 |
| `python scripts/replay_relations.py --arms baseline model-a --output <本次目录>/run-a`，再输出至 run-b | 52 场景、每臂 1201 消息；两份完整逻辑 JSON 相同 |
| `python scripts/build_package.py --output <本次目录>/phase0.zip` | 提交后读取 HEAD 检查归档；具体 SHA/结果记录在 PR 验证栏 |

两次 results.json 的文件 SHA-256：`b02472448331fea76a799be179ae10687826a64afbb7ebfbf649e235141880e2`。提交文件 PHASE0_RESULTS.json 是保留全部场景配置、规则和汇总/短轨迹的精简快照；完整逐事件报告可用相同命令重建。tested_commit 是运行时 Git HEAD，未提交工具修改由文件和夹具指纹补充；不是声称工具已存在于 main。

`timings.json`，保存离线入口耗时数组，且与逻辑结果分离。这不是主模型生成回复延迟。Provider 调用 730/臂，失败 1、超时 1、busy 10；Token 未由预置响应提供，保留 null。统计的 status 是持久化 attempt 状态；重复 ID 可显示原 attempt 的 accepted，但新流水为零，不能只用该状态推定新写入。真正 delta 只计当前新增流水，并穷尽各分库核对主体。

历史 main 树相同的前置提交 `3b5bf6d0b0a4d5a7862840ef534aa91905afa0be` 曾有 CI/Black/CodeQL 成功记录（41335442670 / 41335442647 / 41335442633）。它们仅作历史来源，不替代本 PR SHA 的远端检查；PR 提交后核对对应新 SHA 的工作流。

## 默认值和链路

生产 AssessmentConfig 默认 disabled，小/大 Provider 为空，tiered，超时 20/30 秒，冷却 120 秒，大模型用户每日 2 次、Bot 每日 20 次；AssessmentCapacity 默认 2。回放主动启用合成 Provider，并将普通超时设 2 秒、专门故障场景 .02 秒；差异逐场景保存，不能说默认启用。

现有模型协议只允许 event_type/confidence/evidence，原文必须为当前消息子串；分值由本地 RelationPolicy → FavorService 写入。默认每日 F 正向 2；normal_question 每次 .1、friendly .3，不能将每日上限误当每次增量。长期 F 轨迹、重复事件、冷却和实际限幅由真实写入链记录。

## 未验收清单

| 项目 | 状态与可复现的后续步骤 |
| --- | --- |
| 真实 QQ / 多 Bot 消息 | 未执行。经单独授权的测试群固定平台/适配器版本，验证自己、其他已知/未知 Bot、Reply 缺失和混合 @；逐条比对可信身份与流水，不收集额外群聊。 |
| 真实 Provider | 未执行。先完成获许可脱敏双标注，冻结模型/提示/样本，独立报告误归因、误更新、Token、超时/失败和预算；confidence 仍不当校准概率。 |
| 主回复延迟 | 未知。测试群分别启用/关闭关系评估，记录同一宿主链的回复开始/完成时间，报告 p50/p95 与样本量；不以本地监听器耗时代替。 |
| Angel Heart 同装 | 仅既有合成请求组合通过。真实插件调度、真实模型回复和主动发言未验收，不接管发言决策。 |
| Dashboard 浏览器 | Node 19 项与服务 API 合成认证通过；未使用浏览器，不声称交互布局或真实 HTTP 登录体验通过。 |
| 真人盲评 / 信任修复 | 未执行。按 GOLDEN_SET.md 分组盲评连续性、差异、过度反应和隐私。实际改正的识别与自动 T 恢复尚无可验证实现。 |

不将离线通过写成生产就绪；未校准的概率、半衰期、五维模型和复杂身份图不属于此次交付。
