# 八卡稳健性改造与单次候选验证

执行入口仍为根 `observer.project.json` → `agent/agent.py`，协议为 participant-agent-protocol-v4。两阶段模型调用仍在 `advice.py` 中相连，公告解析结果进入夜间规划，规划输出约束实际 Pro 搜索。模型配置不变；超时、无效输出和预算不足使用确定性回退。

## 实际规划改动

- **联合收益**：在同一指向和时长下比较共同 program，再分配光纤。科学增量扣除反馈账本的真实历史最高分；必做、请求是单独的规划奖励，不写入完成状态。公开时段的曝光积分在一次搜索内缓存，投影复用球面单位向量。
- **请求**：整条请求按 remaining_count 聚合并封顶，多余成员不会重复产生无限奖励。允许部分完成的规划信用；这只是启发式价值，不表示请求已结算。先检查公开日历及截止窗口的乐观可行性；检查通过也不保证实际天气下成功。重叠请求重新计算缓存；光纤分配再做一次有界调整。
- **必做**：完成因子下界来自真实反馈，未确认完成的目标继续保护。日历记录实际可见夜晚，最后机会提高权重；不因多次失败永久压低优先级。为少量紧急目标加入完成门槛的时长候选，仍联合填充普通科学目标。未来质量只采用公开月相、气团和日历，不读取未来天气。
- **质量与故障**：运行中的 Pro 估计为规划与故障诊断提供共同质量尺度，每次曝光汇总一次证据；不把同一次曝光的多个光纤当成独立天气样本。得分超过 mismatch 上限才确认 program 匹配；不确定的饱和状态不作为精确质量证据。账本仍保留因子上下界。全天空天气及地震开始后 12 小时内的曝光不进入干净证据；持续数周的地震公告不会永久停止后续采样。最近证据同时满足至少 12 次独立曝光和至少 3 小时跨度，短曝光时向前扩展窗口，并保留至少 24 次历史曝光；有界 DARK 诊断、模型否决冷却与真实误报冷却分开。成功修复清空质量历史。
- **成本与推进**：复用初始化窗口；快速档保留科学密度、紧急必做、有效请求候选；邻域、边缘余量与标定范围根据网格缩放。调速同时使用 CPU、实际时间、平台往返开销和整季推进速度，保留恢复滞后。实际时间紧张时提高曝光推进目标，豁免临近设置的必做与请求；不保证所有机器都能完成长卡。

## 独立开关与诊断

`OBSERVER_DISABLE_FEATURES` 为逗号分隔的关闭列表：

| 开关 | 关闭的改造 |
| --- | --- |
| joint | 共同 program 联合优化 |
| integration | 公开时段曝光积分 |
| requests | 公开请求规划 |
| required | 新必做日历与最后机会保护 |
| quality | 按曝光聚合并统一质量估计 |
| fault | 新故障隔离、诊断上限和独立否决冷却 |
| diversity | 球面投影复用、扩展邻域、快速候选多样性 |
| calibration | 扩展标定搜索范围及精细化 |
| stagnation | 普通科学目标停滞抑制 |
| pacing | 新综合调速和曝光推进目标 |

`OBSERVER_FIXED_LEVEL=0/1/2` 仅用于固定搜索档位的本地成本对照。正式候选使用自动调速。初始化输出 `planner-features`，基准工具校验运行时回执，避免开关只影响旧搜索。每 128 个动作输出摘要，记录搜索规模、质量、模型调用、修复与预算；详细结果保存在被 Git 忽略的目录。

```powershell
python -m unittest discover -s tests -v
python tools/benchmark_strategy.py --out strategy_review_output/check --cards L1 L2 L3 L4 --trace
python tools/benchmark_strategy.py --out strategy_review_output/no-joint --cards L3 --disable joint --fixed-level 0 --trace
python tools/benchmark_strategy.py --out strategy_review_output/model-check --cards L1 --with-model --env-file agent/.env --trace
python tools/stress_strategy.py --out strategy_review_output/stress.json
python tools/result_diagnostics.py <result-directory-or-zip> --out strategy_review_output/diagnostics.json
```

基准保存不可变源码快照、逐文件哈希、Git 版本、官方引擎清单哈希、卡片哈希、开关、模型模式和计时条件。快照不复制 `.env`，真实模型验证必须显式引用忽略的本地配置。Windows `cpu_meter=none` 的公开卡结果不能换算成 Linux 平台成绩。合成压力测试只有公开输入，用于成本检查，不是正式卡分数预测。

## 平台验证约束

平台只启动最终候选的一次完整八卡评测；不重跑基线、不启动重复组、不自动追加评测或循环调参。失败、成绩不明确或策略回退时保存证据并停止。通过本地验证后正常 push，再由 `python submit_competition.py` 提交固定 commit；检查实际 manifest、adapter 与公开测试后确认 revision。

已有两批历史八卡结果作为比较范围，条件不一致必须标注。普通 A–D 均分相对既定历史基线退步不超过 2%，单卡不超过 5%；另检查 A1/C1 科学收益、遗漏、修复与历史波动，以及 D1 终止原因。单次结果只能初验，不能证明稳定性提升。现有最终版本保持不变，读取本次结果后再决定是否替换。Sophon 不属于本次范围。
