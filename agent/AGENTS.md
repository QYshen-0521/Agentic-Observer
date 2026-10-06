# AGENTS.md -- python-agent

Guide for AI coding assistants working in this project. Humans: see README.md /
README.zh.md.

## What this is

A complete, standard-library-only Python agent for the GOSIM survey26 hackathon's
telescope-survey challenge. It speaks `participant-agent-protocol-v4` (one JSON
object per line on stdin/stdout), runs a deterministic anchor-search planner, and
uses a connected public-notice parsing -> nightly task planning LLM pipeline,
plus occasional fault confirmation. Model failures fall back to deterministic planning.
Read `agent.py` first -- it is the thin
stdin/stdout loop that dispatches to everything else.

## Module map

```
agent.py                 entry point: stdin/stdout loop, one branch per message type
agent_core/
  protocol.py             transport: read/write one JSON object per line
  state.py                 SurveyState: catalogue + everything tracked across decisions
  geometry.py              public sky maths (sidereal time, alt/az, fibre grid)
  calibration.py           fixed pointing offset inferred from public hit feedback
  exposure.py              snapshot-local public-slot midpoint integration
  fields.py                bounded grid phase screening and exact field reprojection
  requests.py              bounded future-window request beam search
  advice.py                connected notice parsing -> bounded nightly model plan
  scoring.py               factor/score estimates from PUBLIC scoring config only
  planner.py               decision logic: wait / observe / report / finish
  pro_planner.py           active Pro-derived search: science density and time price
  pro_skymath.py           attributed public geometry used by the Pro search
  llm_client.py            OpenAI-compatible chat client, defaults to Kimi Coding Plan
  clock.py                 CPU/wall budgets, measured night progress and search costs
  memory.py                optional, best-effort JSONL decision trace (off by default)
  validation.py            protocol-legal action checking + a deterministic fallback
../observer.project.json   sole platform manifest (runs agent/agent.py from the repo root)
requirements.txt           none needed -- standard library only
```

## Changing the strategy

The active target ranking, fibre filling and exposure sizing live in
`agent_core/pro_planner.py`, connected through `Planner._pro_plan` in planner.py.
The previous phase search remains in `_legacy_plan` for ablations; it is not the
default runtime search. Public completion bounds and rollback remain in state.py;
Pro estimates must never replace the actual feedback ledger. Read HANDOFF_ZH.md
at the repository root before continuing optimization.
The two connected calls (notice parsing + task planning) are issued by advice.py
from the night-advice path in planner.py through agent_core/llm_client.py; the
instrument-fault confirmation call is a third, rarer call from the same module. You
can change the ranking heuristic, add new signals to `SurveyState`, or revise the
LLM prompts. Preserve two meaningful LLM-driven stages for the competition's
technology requirement. `validation.py` separately requires every stdout response
to be protocol-legal.

## Configuring the LLM (Kimi key / base URL / model)

Copy `.env.example` to `.env` and set:

- `OPENAI_API_KEY` (or `KIMI_API_KEY`) -- enables model stages; without it the agent falls back to rules.
- `OPENAI_BASE_URL` -- defaults to the Kimi Coding Plan endpoint
  (`https://api.kimi.com/coding/v1`; use `https://api.kimi.ai/coding/v1` outside
  mainland China) when unset.
- `OPENAI_MODEL` -- defaults to `k3` when unset.

Any other OpenAI-compatible `/chat/completions` endpoint works too -- just point
`OPENAI_BASE_URL` / `OPENAI_MODEL` at it. On the platform,
the variables your team saves under "Keys and network" (e.g. `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `OPENAI_MODEL`) are the program's environment, and it calls the
provider directly. `.env` and its private variants must remain untracked; only
credential-free `.env.example` templates belong in Git.

## Submitting as a complete GitHub project

```bash
cd ..
git push origin main
python3 submit_competition.py
```

The helper submits the exact pushed commit using the official CLI's
`project submit-repo`. Submit the repository root, not the agent subdirectory.
See ../AGENTS.md and ../README.md. ZIP submissions are no longer used here.

## Protocol & scoring

Full protocol reference and scoring formulas are not duplicated here -- see `docs/`
(bundled in the downloadable ZIP of this example) for the complete participant
guide in Chinese and English.
