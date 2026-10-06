# 2026-10-06 交接状态

当前 main 为 Pro 核心候选，已完成本地核心四卡验证与 45 项回归测试；详细范围和待完成工作见 [HANDOFF_ZH.md](HANDOFF_ZH.md)。此 Pro 版本尚未提交到平台，不能把旧 revision 的分数归给当前 main。

上一相位版本 revision `41a8c90f-8333-46d3-a2d9-4a8b3150dd3d` 的正式普通均分为 26763.022682，未优于基线；其批次 `e797eb28-cdd5-4d6d-8536-fd0ec4a7b751` 的困难 B1/D1 最后查询时仍运行。后续先查看该批次，不要自动启动重复评测或更换最终版本。

---

# GitHub 提交记录

更新：2026-10-06。本轮 P0–P3 改动与本地验收见 [STRATEGY_ZH.md](STRATEGY_ZH.md)。

## 2026-10-06 本轮正式基线

用户已授权提交并正式评测。源码 `7afcb17b84821f16487a40958e2a9923212a6aff` 的 revision 为 `033e9bea-d550-4a52-bbac-9c398d3c0710`，已自动批准，公开测试 140.300059 分。实际 manifest 为根目录下 `python3 -u agent/agent.py`，build 与 adapter_files 为空；平台将 protocol 规范化为 `jsonl-v2`，程序仍使用 V4。

正式批次 `108f83fc-8b01-4f28-83d3-e2337370f7b1` 已产生 A/B/C/D 分数 22318.235782 / 34237.832175 / 22256.737494 / 31903.001677，平均 27678.951782。困难卡仍在运行，整个批次尚未完成。本轮视场相位、吞吐率粗排和完成质量代理优化的本地验收为平均 +4.64%、必做缺失合计 12 → 7，38 项测试通过；新版平台提交与正式成绩在完成后另行记录。

以下内容保留此前提交时的历史证据，其“正式次数为 0”等描述只对应当时状态。

## 上轮 P0–P3 平台版本

已完成最终四卡验收：平均 5357.046653 分，较上轮本地基线提高 2.14%；各卡均提分，必做缺失合计 13 → 12，无非法动作或预算耗尽。34 项测试通过，17 个官方引擎文件校验一致。

- 固定源码：[9318e8d2f76700c4e311cc3d6d9166129d0a0df0](https://github.com/QYshen-0521/Agentic-Observer/commit/9318e8d2f76700c4e311cc3d6d9166129d0a0df0)，通过正常 Git push 同步后，由 `python submit_competition.py` 提交；平台返回的 source_commit 和 source_ref 均与之完全一致。
- 平台 revision：`4748671f-4e3a-46f9-95ea-597cbdd517c8`；状态 **approved**，已执行并核对 `project confirm`。
- 公开测试：**88.736123** 分，`survey_complete`，run ID 为 `d40dbc90-c0ca-4a32-9b2b-a1a6717a6e7f`；无初始化、策略或动作校验错误。
- 模型日志：公告解析成功 7 次、夜间规划成功 1 次；18 次实际调用中记录 10 次超时，随后执行确定性回退。两阶段均有真实成功输出，但当前平台夜间规划成功率偏低，需要在后续正式复测中关注模型响应时限与成对预算分配；不能把公开测试通过解释为模型服务没有问题。
- 实际 manifest：`python3 -u agent/agent.py`、根工作目录、空 build；平台将 protocol 规范化为 `jsonl-v2`，仓库声明仍为 `jsonl-v4`，程序使用 `participant-agent-protocol-v4`。实际 adapter_files 为空。
- 已下载并审计平台产物：15 个运行时 Python 文件逐字节匹配该固定 commit 的 Git blob；未发现额外 Python 文件、`.env` 或私有环境变体。只有公开配置模板。下载 ZIP 仅用于审计，不是提交来源。
- 原始提交、平台状态、完整日志、下载产物及哈希审计记录在本地忽略目录 `strategy_review_output/github_submissions/9318e8d2f76700c4e311cc3d6d9166129d0a0df0/`。
- 新版正式评测次数为 **0**；本轮未启动正式 A–D，也未更改最终版本选择。新版正式提升尚未验证，上轮正式基线仍为 **28067.52**。

本页提交状态的后续文档更新不会改变平台冻结的上述运行源码。

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
