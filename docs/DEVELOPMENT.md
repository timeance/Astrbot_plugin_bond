# 开发与交付

## 本地检查

使用 Python 3.12，Windows 请设置 `PYTHONUTF8=1`。仓库目录命名为 `astrbot_plugin_bond`。离线测试只使用框架日志桩，不需要安装 AstrBot 或调用模型。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
python -m black --check .
python scripts/check_project.py
python scripts/build_package.py --output /tmp/bond-check.zip
```

打包检查读取 Git 中的 HEAD；先提交修改，再检查待交付的归档。归档保留 LICENSE、NOTICE、用户文档和运行文件，排除测试、工作流及本地运行数据。

真实框架检查须使用另一个 Python 环境，避免离线测试桩掩盖导入问题：

```bash
python -m pip install AstrBot==4.28.0
python scripts/smoke_astrbot.py --session-dir <本次测试目录> --runtime-dir <本次测试目录>/runtime
```

每次使用新的空运行目录。该检查验证真实框架装配、两个 Bot 的四个数据库、模式持久化、在飞评估身份冻结、页面 API 注册、服务端认证拒绝、备份恢复与卸载。不会连接平台或远程模型；认证上下文由测试模拟，不证明完整宿主 HTTP 登录及 Angel Heart 联动已通过。

## 自动检查

| 工作流 | 触发时机 | 检查内容 |
| --- | --- | --- |
| CI | PR、main 推送、手动运行 | Python 3.10/3.12 离线测试、赋值顺序、元数据与安全默认值、敏感数据、发布包、AstrBot 4.28.0 装配 |
| Code Format Check | PR、main 推送、手动运行 | 锁定版本的 Black |
| CodeQL | PR、main 推送、每周、手动运行 | Python 静态安全分析；公开仓库启用 |
| release | `v*` tag、手动指定已有 tag | 验证版本和 tag、运行测试及格式检查、校验发布包、创建 Release |

核心层在 Python 3.10 上的测试通过，不代表 AstrBot 宿主支持 Python 3.10。真实框架要求 Python 3.12+。各工作流设置最小权限和执行时限，PR 检查不读取仓库密钥，不使用 `pull_request_target`。

## 提交与 PR

沿用单向分层，关系修改通过统一服务入口。新行为补充成功与失败路径测试。用户文档写用途、操作与限制；架构细节放入开发文档。

使用中文 Conventional Commits。前置阶段尚未合并时，可从其分支建立堆叠 PR，写明依赖和合并顺序。PR 说明问题、改动和测试范围，不提交真实用户数据，不自动合并。每个阶段以可验证结果为交付边界。

## 发布

当前开发基线不发布正式版。未来发版先由维护者合并 PR，再同步 `metadata.yaml` 与 `main.py` 的版本，创建带说明的 annotated tag，如版本 `0.2.0` 对应 `v0.2.0`。预发布版本使用带连字符的版本与 tag，Release 自动标为 prerelease。

发布仅接受 main 历史中的提交，tag 版本须与两个元数据入口一致。工作流先完成检查，再生成归档、校验文件及 SHA-256 校验和，最后创建 GitHub Release。普通 PR 和分支推送只做验证，不发版。

首次启用工作流后，应以远程 Actions 结果为准。本地检查通过不等于 GitHub 托管运行已成功；CodeQL 也不能替代人工审查和运行时安全测试。

## 印象汇总进度

Schema 11 将成功汇总的流水 ID 保存在本地，并与印象、标签和点集原子提交。失败不推进进度；提交前再次检查评审是否被撤销。版本 10 保留 PR-1 已使用的群成员索引结构，避免同一版本号出现两种数据库结构。升级与回滚前均须备份整个数据目录。
