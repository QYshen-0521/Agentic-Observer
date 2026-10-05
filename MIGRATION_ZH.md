# V4 重建与首次正式提交（历史记录）

后续统一采用 GitHub 提交。旧 V3、ZIP 打包入口和重复清单已删除；保留本页作为首次跑通正式赛的证据。

2026-10-05 已按审阅建议以官网 Python V4 示例重建主干，并通过官方 CLI 完成一次在线正式评测。

- 版本：`b21a1616-a0d1-48cd-a12e-cb9682700a10`
- 批次：`4394d2fd-48d5-44d7-b9c3-2bb7aeef017d`
- 阶段：online；启用模型；没有 repeat；仅启动一次正式评测。
- 整批及 A–D 四卡状态：scored；四卡均 survey_complete。
- 平均分：**24029.60260025**。
- 此次评测完成时，平台默认最终版本为该版本（来源 best）；后续 GitHub 提交会另记版本。

| 卡 | 正式总分 | 必做缺失 | 标准化 CPU 秒 | 实际运行秒 | 结束原因 |
|---|---:|---:|---:|---:|---|
| A | 16285.538048 | 3 | 158.222 | 425.945 | survey_complete |
| B | 32960.953409 | 2 | 254.788 | 720.185 | survey_complete |
| C | 16909.523632 | 0 | 137.385 | 322.843 | survey_complete |
| D | 29962.395312 | 2 | 110.164 | 869.479 | survey_complete |

## 当前代码

正式入口为 [agent/agent.py](agent/agent.py)。保留官方几何、光纤分配、评分与实际反馈账本，加入两个串联且实际影响调度的 LLM 阶段：公开公告/预报解析 → 根据观测进度制定夜间计划。模型建议经过来源、范围与统一动作检查，失败时回退。运行仅依赖 Python 标准库，当前仓库根目录保留唯一的执行清单。

旧 V3 源码和评测器已从工作区移除，可从旧 Git 提交回看。本地入口 [local_runner.py](local_runner.py) 改用官方 V4 引擎；Windows 只替换本地传输层，17 个官方 engine 文件校验通过。上游来源和许可见 [agent/UPSTREAM.md](agent/UPSTREAM.md)。

## 验证证据

8 项回归测试通过；关闭模型的 L1–L4 全部完整运行。L1 为 4525.108970，官方原版同卡对照为 4458.556163；该单卡对照不能说明正式比赛整体优于原版。

公开测试通过，正式四卡确认使用 participant-agent-protocol-v4。四卡两个模型阶段均有真实成功调用，未发现初始化、策略和动作校验错误。D 卡经过一次数据丢失重同步，1125 条观测失效后仍完整完成。

[完整提交报告](strategy_review_output/v4_rebuild/SUBMISSION_ZH.md)、[提交 ZIP](strategy_review_output/v4_rebuild/agent-v4.zip)、[正式结果 ZIP](strategy_review_output/v4_rebuild/official-results-4394d2fd.zip) 保存在本地输出目录；输出目录受 .gitignore 忽略，提交仓库时需另行归档证据。

提交 ZIP 的 28 个文件与当前 agent/ 逐项相同，不含 .env。SHA-256：`6cfa212a08510beca82cc25d3fa22843fe9637039ad4b3af894b0e17211ee634`。平台的 source_digest 属于另一个标识，不应当作此 ZIP 的摘要或 Git commit。

## 仍可优化

四卡都没有完成观测请求；A/B/D 还缺 3/2/2 个必做目标，C 全部完成。尚未做专门的指向偏移标定优化。累计模型等待预算耗尽后多数夜晚使用确定性规划。当前成果是已经成功计分的参赛基线，不构成获奖或最终排名保证。

## 本地复现

```powershell
python -m unittest discover -s tests -v
python tools/fetch_local_kit.py
python local_runner.py --card L1
python submit_competition.py
```

默认本地验证关闭模型，使用 --with-model 可读取本地配置启用模型。正式评测使用平台既有环境变量；密钥没有写入代码或上传包。
