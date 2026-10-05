# GitHub 提交记录

本轮策略优化已完成本地四卡对照，结果见 [STRATEGY_ZH.md](STRATEGY_ZH.md)。平台提交信息将在新版本准备完成后更新。

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
