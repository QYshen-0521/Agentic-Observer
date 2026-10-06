# GitHub 提交记录

更新：2026-10-06。本轮 P0–P3 改动与本地验收见 [STRATEGY_ZH.md](STRATEGY_ZH.md)。

## 本轮候选

候选已完成最终四卡验收：平均 5357.046653 分，较上轮本地基线提高 2.14%；各卡均提分，必做缺失合计 13 → 12，无非法动作或预算耗尽。34 项测试通过，17 个官方引擎文件校验一致。平台固定源码、revision、公开测试和确认状态在提交成功后填写。本轮不会自动启动正式 A–D 评测，也不会更改最终版本选择。

## 已完成正式评测的上轮版本

- 固定源码：[eb3f667a0ed04b7ead09bdf06bc67e17000f0f5e](https://github.com/QYshen-0521/Agentic-Observer/commit/eb3f667a0ed04b7ead09bdf06bc67e17000f0f5e)。
- 平台版本：`032cdc20-4945-48d5-88d6-c41cc745924f`；GitHub 来源，经公开测试后已确认。
- 公开测试：`survey_complete`，82.826408 分；公告解析与夜间规划各成功 5 次。这属于项目准备测试，与正式 A–D 得分分开。
- 用户随后启动的正式批次 `8dc7547f-6ce0-44e3-805d-ab7c2539fba1` 已完成，A/B/C/D 为 22649.154255 / 35014.212038 / 22311.345611 / 32295.350390，平均 **28067.515574**。本轮使用它作为正式基线。
- 上轮实际启动为 `python3 -u agent/agent.py`，工作目录为根目录，build 与 adapter_files 为空；13 个运行时 Python 文件与固定 Git 源码一致。
- 上轮平台将准备后 manifest 的 protocol 规范化为 `jsonl-v2`，不同于仓库 `jsonl-v4`；程序仍输出 `participant-agent-protocol-v4`，公开及正式测试均完成。新版产物仍须单独核对。
- 原始项目证据在本地 `strategy_review_output/github_submissions/eb3f667a0ed04b7ead09bdf06bc67e17000f0f5e/`；正式日志在 `strategy_review_output/runtime_diagnosis_20261005/`。

此前“未启动正式评测”只描述当时提交操作的边界；上面已更新为后来实际完成的正式批次。项目提交、公开测试、确认版本、启动正式评测及选择最终版本分别执行，不能互相替代。

## 提交流程

使用公开 [QYshen-0521/Agentic-Observer](https://github.com/QYshen-0521/Agentic-Observer) 仓库和固定 commit。

```powershell
python -m unittest discover -s tests -v
git add <本次修改的文件>
git commit -m "Optimize V4 observing strategy"
git push origin main
python submit_competition.py --title "V4 measured pacing, integrated exposures and request scheduling"
```

脚本校验工作区干净、main 已推送及根执行清单，随后提交准确 commit。公开测试结束后，检查实际 manifest、execution_settings、adapter_files、日志及下载产物与 Git blob 的一致性，再执行 `project confirm`。下载 ZIP 仅用于审计产物，不作为提交来源。正常推送，不 force-push；私有配置、任务卡、日志、快照和结果不入 Git。

正式评测在明确请求后独立启动；本地提升比例不能代替正式新版分数。提交后的文档状态更新不改变平台冻结源码。

## 历史正式证据

| 版本 | 平台 revision | 正式 A–D 平均 | 固定源码 |
|---|---|---:|---|
| 首次 V4 重建 | b21a1616-a0d1-48cd-a12e-cb9682700a10 | 24029.60260025 | 历史本地来源包 |
| 初次 GitHub 提交 | 52fc3d67-939a-433f-91a5-aa635833cbc9 | 22609.22246875 | 3dedd9c4c77213074b551f9ac5b394df5185b0bc |
| 上轮联合规划优化 | 032cdc20-4945-48d5-88d6-c41cc745924f | 28067.5155735 | eb3f667a0ed04b7ead09bdf06bc67e17000f0f5e |

旧 V3 报告与迁移说明已清理；旧输出归档于 `strategy_review_output/history_legacy/`，源码可从 Git 历史找回。当前以根 README、策略说明及本页为准。
