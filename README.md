# Agentic Observer — 原生 V4 参赛项目

当前正式入口为 `agent/agent.py`，协议为 `participant-agent-protocol-v4`。
运行只依赖 Python 3.9+ 标准库，不需要 LangChain、LangGraph 或安装模型 SDK。

2026-10-06 优化交接请先读 [HANDOFF_ZH.md](HANDOFF_ZH.md)：当前采用 Pro 衍生搜索核心，
45 项测试通过；最终源码本地四卡均分 6520.17，尚无当前版本正式成绩，尚未超过第一名。

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

正式评测完成后核查成绩，再选择用于决赛的版本并核对：

```powershell
python tools/official_cli.py --json final set <REV>
python tools/official_cli.py --json final show
```

官方 CLI 自动下载到本地忽略目录 `.survey26-cache/`，沿用本机既有登录。
Git Bash 下可用 `bash push.sh "提交说明"` 提交并推送，再运行上述 GitHub 提交脚本。

模型变量使用平台已经配置的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。
只走 OpenAI 兼容的 `/chat/completions` 接口。在公告、预报或计划更新时先解析公开信息，
再依据解析结果、观测进度与临期目标生成受约束的夜间计划；模型失败时退回确定性策略。
本地测试默认启用与平台相同的两阶段模型流程，使用 `agent/.env` 中的私有配置。
复制 `agent/.env.example` 为 `agent/.env`，填写密钥；Kimi Coding 的默认接口为
`https://api.kimi.com/coding/v1`，模型标识为 `k3`。密钥文件由 Git 忽略。
缺少密钥时，本地入口会明确报错。需要无模型对照时使用：

```powershell
python local_runner.py --card L1 --without-model --out strategy_review_output/iterations/L1-rules
```

启用模型的本地测试：

```powershell
python local_runner.py --card L1 --out strategy_review_output/iterations/L1-k3
```

两种测试都沿用正式 agent 的超时、重试和模型调用上限，详细调用结果见输出目录的 `agent.log`。
Windows 本地评分器没有平台的容器 CPU 计量，模型等待可能计入预算；本地分数用于策略对照，正式成绩以平台为准。

可复现四卡对照会先复制不含私有配置的不可变源码快照，输出目录必须是新的目录：

```powershell
python tools/benchmark_strategy.py --out strategy_review_output/iterations/rules --trace
python tools/benchmark_strategy.py --out strategy_review_output/iterations/model --cards L1 --with-model --env-file agent/.env --trace
```

`--disable integration`、`--disable stagnation`、`--disable requests`、`--disable pacing`
可在实验快照中单独关闭相应模块做消融，不修改正式源码。`--trace` 使用现有
`AGENT_TRACE_PATH` 记录搜索成本、预算、档位、预测科学增量与真实增量，默认关闭。

`agent/agent_core/` 提供天球几何、光纤分配、曝光搜索、实际反馈账本、状态回滚、
CPU/实际时间预算和统一动作校验。实现以 GOSIM 官网当前 Python 示例为基础；来源与
团队改动记录见 [agent/UPSTREAM.md](agent/UPSTREAM.md)。完整接口说明保存在 `agent/docs/`。

旧 V3 源码、评测器、启动器和 ZIP 打包工具已移除；旧源码仍可从 Git 历史找回。
保留 V4 策略、回归测试、官方协议文档、本地评测入口和比赛提交工具。
正式 agent 不读取任何任务卡或隐藏天气文件。密钥、缓存、任务卡和评测输出均不提交 Git。

当前策略与本地对照结果见 [策略说明](STRATEGY_ZH.md)。
平台版本与提交状态见 [GitHub 提交记录](GITHUB_SUBMISSION_ZH.md)。
原始成绩、日志与试验快照保存在 Git 忽略的 `strategy_review_output/`，不进入参赛源码。
旧 V3 审阅报告已清理，迁移历史可以从 Git 历史和保留的正式结果证据查询。
