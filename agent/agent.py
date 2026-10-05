#!/usr/bin/env python3
"""Entry point for the python-agent example (participant-agent-protocol-v4).

Reads one JSON object per line on stdin, writes one JSON object per line on
stdout, logs only to stderr. The loop itself is deliberately thin: all the
decision-making lives in agent_core/ (protocol I/O, state tracking, geometry,
scoring, planning, the LLM client, memory/log, action validation) so this file
stays a readable map of "what happens for each message type".

Model variables are optional for local tests: missing credentials or failed calls
use the deterministic planner. The platform's configured model enables connected
notice parsing and nightly planning. See README for configuration details.

  initialize        -> build SurveyState + Planner from the public payload
  decision_request   -> Planner.decide(), validated, sent back as decision_response
  finish              -> Planner.on_finish() logs a summary and the process exits

Pacing uses the fair clock (agent_core/clock.py): the budget is CPU time, so the
agent measures its own cost with process CPU time, not with a wall clock.

Any planner exception is caught here and replaced with a safe fallback action --
a bug in the strategy must never end the run as agent_error or hang the process.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 9):
    sys.stderr.write("agent: Python 3.9 or newer is required\n")
    raise SystemExit(3)

from agent_core.llm_client import MissingAPIKeyError, model_disabled, require_api_key
from agent_core.planner import Planner
from agent_core.protocol import log, read_messages, send_response
from agent_core.state import SurveyState
from agent_core.validation import ActionRejected, fallback_action, validate_action


def main() -> int:
    try:
        require_api_key()
    except MissingAPIKeyError as exc:
        log(f"agent: {exc}; running deterministic fallback")
    if model_disabled():
        log("agent: OBSERVER_MODEL_DISABLED=1, running rules only (no model calls)")

    state = None
    planner = None
    for message in read_messages(sys.stdin):
        kind = message.get("message_type")

        if kind == "initialize":
            try:
                state = SurveyState(message["payload"])
                planner = Planner(state, log=log)
            except Exception as exc:  # noqa: BLE001 - never crash on a malformed initialize
                log(f"agent: failed to initialize ({type(exc).__name__}: {exc}); will fall back on every decision")
                state = None
                planner = None

        elif kind == "decision_request":
            sequence = message["decision_sequence"]
            if planner is not None:
                planner.clock.start_decision()  # measure our own CPU cost per decision
            consecutive_reports = planner.consecutive_reports if planner is not None else 0
            try:
                action = planner.decide(message["payload"]) if planner is not None else fallback_action("not initialized")
                action = validate_action(action, state, consecutive_reports, message['payload'].get('now_utc'))
            except ActionRejected as exc:
                log(f"agent: planner produced an invalid action ({exc}); falling back")
                action = fallback_action("validation-rejected")
                if state is not None:
                    state.pending.clear()
                    state.pending_action_index = None
            except Exception as exc:  # noqa: BLE001 - a strategy bug must not end the run
                log(f"agent: planner error ({type(exc).__name__}: {exc}); falling back")
                action = fallback_action("planner-exception")
                if state is not None:
                    state.pending.clear()
                    state.pending_action_index = None
            if planner is not None:
                planner.note_action(action)
            send_response(sequence, action, state.response_max_bytes if state is not None else 524288)
            if planner is not None:
                planner.clock.end_decision(max(0.0, planner.llm.wait_used - planner._model_wait_start))
                planner.trace.write({'event': 'decision_metrics', 'sequence': sequence,
                    'action': action['action'], 'level': planner.state.fast_level,
                    'cpu_seconds': planner.clock.last_cost, 'wall_seconds': planner.clock.last_wall_cost,
                    'search_cpu_seconds': planner.clock.last_search_cost,
                    'predicted_science_gain': sum(getattr(p, 'expected_gain', 0.0) for p in planner.state.pending.values()),
                    'realized_science_gain': getattr(planner.state, 'last_science_gain', 0.0),
                    **planner._pace_estimate})

        elif kind == "finish":
            if planner is not None:
                try:
                    planner.on_finish(message.get("payload", {}))
                except Exception as exc:  # noqa: BLE001 - finish must not raise after the score is fixed
                    log(f"agent: error during finish logging ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
