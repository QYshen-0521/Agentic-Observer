# GitHub 提交记录

本轮策略优化已完成本地四卡对照，结果见 [STRATEGY_ZH.md](STRATEGY_ZH.md)。

## 本轮平台版本

- 固定源码：[eb3f667a0ed04b7ead09bdf06bc67e17000f0f5e](https://github.com/QYshen-0521/Agentic-Observer/commit/eb3f667a0ed04b7ead09bdf06bc67e17000f0f5e)。已通过普通 Git push 推送，平台返回的 source_commit 完全一致。
- 平台版本：`032cdc20-4945-48d5-88d6-c41cc745924f`。
- 来源：repository；通过 `python submit_competition.py` 提交。
- 状态：平台公开测试通过，已执行 `project confirm` 并确认成功。
- 公开测试：`survey_complete`，得分 82.826408；两阶段模型各成功 5 次，无初始化、策略或动作校验错误。此分数属于公开测试，不是正式 A–D 平均分。
- 实际启动清单：`python3 -u agent/agent.py`，工作目录为根目录，build 与 adapter_files 均为空。
- 平台将准备后清单的 protocol 标记规范化为 `jsonl-v2`，与仓库声明的 `jsonl-v4` 不同；下载核对的 13 个运行时 Python 文件与固定源码全部一致，程序仍输出 `participant-agent-protocol-v4`，公开测试正常完成。该差异如实保留，不擅自修改平台产物。
- 下载产物未发现 `.env` 或其他私有环境文件；只含公开配置模板。下载 ZIP 仅用于审阅平台产物，未用于提交。
- 本轮没有启动新的正式 A–D 评测，也没有更改最终版本选择。
- 原始提交证据：本地 `strategy_review_output/github_submissions/eb3f667a0ed04b7ead09bdf06bc67e17000f0f5e/`。

之后的提交状态文档更新不改变平台已经冻结的上述源码。

## 提交流程

比赛统一使用公开 GitHub 仓库 [QYshen-0521/Agentic-Observer](https://github.com/QYshen-0521/Agentic-Observer) 和固定 commit。

```powershell
python -m unittest discover -s tests -v
git add <本次修改的文件>
git commit -m "Optimize V4 observing strategy"
git push origin main
python submit_competition.py --title "V4 joint planning and feedback calibration"
```

脚本校验工作区干净、main 已推送，以及根执行清单，随后提交该 commit。审阅平台的实际 manifest、adapter_files 与公开测试日志后确认版本。不使用 ZIP 项目提交，不强制推送。

正式在线评测与最终版本选择是后续独立操作。提交源码或通过公开测试不等于已有正式新分数。

## 历史正式证据

| 来源 | 平台版本 | 正式 A–D 平均分 | 固定源码 |
|---|---|---:|---|
| 首次 V4 重建 | b21a1616-a0d1-48cd-a12e-cb9682700a10 | 24029.60260025 | 历史本地来源包 |
| 上次 GitHub 提交 | 52fc3d67-939a-433f-91a5-aa635833cbc9 | 22609.22246875 | 3dedd9c4c77213074b551f9ac5b394df5185b0bc |

原始证据保存在本地 `strategy_review_output/v4_rebuild/` 和 `strategy_review_output/github_submissions/`。历史 ZIP 仅是证据，不是后续提交方式。

旧 V3 审阅报告和根迁移说明已移除；旧输出集中归档在 `strategy_review_output/history_legacy/`。当前流程以根 README、本页和策略说明为准。
