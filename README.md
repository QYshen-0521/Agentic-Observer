# Agentic Observer — 原生 V4 参赛项目

当前正式入口为 `agent/agent.py`，协议为 `participant-agent-protocol-v4`。
运行只依赖 Python 3.9+ 标准库，不需要 LangChain、LangGraph 或安装模型 SDK。

```powershell
python -m unittest discover -s tests -v
python tools/fetch_local_kit.py
python local_runner.py --card L1
```

比赛统一从 [GitHub 仓库](https://github.com/QYshen-0521/Agentic-Observer) 提交。
根目录唯一的 `observer.project.json` 声明 `jsonl-v4`，启动 `agent/agent.py`。

```powershell
git add -A
git commit -m "Update native V4 agent"
git push origin main
python submit_competition.py --title "Native V4 GitHub submission"
```

`submit_competition.py` 要求工作区干净、当前 main 的 commit 已推送到 origin/main，
并将这个完整 commit SHA 交给官方 CLI；远端后续更新不会改变已提交版本。
脚本仅提交项目准备任务，不自动消耗正式评测次数。根据返回的 revision ID：

```powershell
python tools/official_cli.py --json project wait <REV> --timeout 900
python tools/official_cli.py --json project show <REV> --files
python tools/official_cli.py --json project logs <REV>
python tools/official_cli.py --json project confirm <REV> --yes
python tools/official_cli.py --json eval start <REV> --yes
python tools/official_cli.py --json eval wait <BATCH> --timeout 1800
```

官方 CLI 自动下载到本地忽略目录 `.survey26-cache/`，沿用本机既有登录。
Git Bash 下可用 `bash push.sh "提交说明"` 提交并推送，再运行上述 GitHub 提交脚本。

模型变量使用平台已经配置的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。
只走 OpenAI 兼容的 `/chat/completions` 接口。每晚先解析公开天气公告和预报，
再依据解析结果、观测进度与临期目标生成受约束的夜间计划；模型失败时退回确定性策略。
本地无模型验证可设置 `OBSERVER_MODEL_DISABLED=1`。

`agent/agent_core/` 提供天球几何、光纤分配、曝光搜索、实际反馈账本、状态回滚、
CPU/实际时间预算和统一动作校验。实现以 GOSIM 官网当前 Python 示例为基础；来源与
团队改动记录见 [agent/UPSTREAM.md](agent/UPSTREAM.md)。完整接口说明保存在 `agent/docs/`。

旧 V3 源码、评测器、启动器和 ZIP 打包工具已移除；旧源码仍可从 Git 历史找回。
保留 V4 策略、回归测试、官方协议文档、本地评测入口和比赛提交工具。
正式 agent 不读取任何任务卡或隐藏天气文件。密钥、缓存、任务卡和评测输出均不提交 Git。

本地验证、公开测试与正式赛成绩记录在 `strategy_review_output/v4_rebuild/`。

2026-10-05 已完成首次正式 A–D 提交，四卡均计分，平均 24029.60260025。
首次验证证据见 [V4 重建与首次正式提交](MIGRATION_ZH.md)，该记录中的 ZIP 为历史证据。
