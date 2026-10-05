# Project workflow

- Competition submissions must use the public GitHub repository and a fixed commit.
  Do not upload ZIP projects. Use `python submit_competition.py` after pushing.
- Keep the root `observer.project.json` as the single execution manifest. Its entry
  point is `agent/agent.py`, using participant-agent-protocol-v4.
- Preserve both connected LLM stages in `agent/agent_core/advice.py`; their bounded
  outputs must continue to affect the planner. Read `agent/AGENTS.md` for module details.
- Run `python -m unittest discover -s tests -v` before submitting code changes.
  Local public-card tooling is fetched by `tools/fetch_local_kit.py`.
- Keep tokens, `.env`, local cards, logs and generated results outside Git. Reports
  may preserve historical ZIP evidence, but ZIP is no longer a submission workflow.
- Inspect the platform's actual manifest and adapter files before confirming a
  revision. Start evaluations only when requested, not in an unattended loop.
- Use normal Git pushes. If new remote work must be replaced, retain its Git history
  and apply the authorized replacement as new commits; do not silently force-push.
