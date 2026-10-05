# 原生 V4 参赛程序

正式入口为 `agent.py`，策略位于 `agent_core/`，只依赖 Python 标准库。
项目基础来自 GOSIM 官方 Python 示例，来源、许可和改动记录见 `UPSTREAM.md`。
完整协议与计分规则见 `docs/`。

在公告、预报或计划更新时先由大模型解析公开信息，再把解析结果、实际观测进度和临期必做
目标交给大模型制定夜间计划。输出经来源和数值约束后，实际调整方向避让、曝光候选
和必做目标优先级。几何、光纤分配与动作合法性仍由程序检查，失败时回退至确定性策略。

模型使用平台保存的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，默认
Kimi Coding Plan 的 `k3`。本地设置 `OBSERVER_MODEL_DISABLED=1` 可关闭调用。
`.env.example` 仅是配置模板；密钥不得写入 Git。直接运行 agent 不会自动加载 `.env`，
本地评测器的 `--with-model` 选项会读取配置。

比赛统一提交 GitHub 仓库根目录，不单独提交此子目录。唯一执行清单位于仓库根目录，
启动命令为 `python3 -u agent/agent.py`。

从仓库根目录完成测试、提交并推送后运行：

```powershell
python submit_competition.py
```

该脚本使用官方 CLI 提交已推送的固定 commit，公开测试通过后再审阅、确认和发起评测。
完整工作流程见根目录 `README.md`；后续不再使用 ZIP 提交。
