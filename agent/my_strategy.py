"""Competition-aware scheduling strategy with LLM-enhanced planning and decision.

Two LLM stages (satisfies the competition's agent-technology requirement):
1. Task Planning (任务规划): At each night start, an LLM analyzes the night's
   windows, weather forecast, active requests and coverage state to produce
   observing priorities.
2. Action Decision (行动决策): In complex situations (multiple good candidates,
   expiring requests, anomaly suspects), an LLM picks among the top candidates
   using the plan and current snapshot.

Fallback: a deterministic coverage-aware strategy (reference implementation)
handles all other decisions and takes over entirely when no LLM is configured.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime


# ---------------------------------------------------------------------------
# Deterministic base strategy (from reference_strategy.py, lightly adapted)
# ---------------------------------------------------------------------------

REQUIRED_LAST_CHANCES = 2
FLEXIBLE_LAST_CHANCES = 1
REQUEST_URGENT_DAYS = 2.0
REQUEST_FORCE_DAYS = 2.0
REQUEST_OPPORTUNITY_SLACK = 1


def _utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _score_config(snapshot):
    config = snapshot.get("score_config")
    return config if isinstance(config, dict) else {}


def _update_public_state(candidates, snapshot, memory, now):
    """Cache published windows/catalogue facts and synchronize realized state."""
    tile_regions = memory.setdefault("_tile_regions", {})
    tile_classes = memory.setdefault("_tile_classes", {})
    for candidate in candidates:
        tile_id = str(candidate.get("tile_id") or "")
        if not tile_id:
            continue
        tile_regions[tile_id] = str(candidate.get("region_id") or "")
        tile_classes[tile_id] = str(candidate.get("scheduling_class") or "").upper()

    windows = memory.setdefault("_published_windows", {})
    publications = []
    for block_name in ("night_start", "weekly"):
        block = snapshot.get(block_name) or {}
        publications.extend(block.get("tile_windows") or [])
    for window in publications:
        tile_id = str(window.get("tile_id") or "")
        start = str(window.get("window_start_utc") or "")
        end = str(window.get("window_end_utc") or "")
        if not tile_id or not end:
            continue
        key = (tile_id, start, end)
        windows[key] = dict(window)
        if window.get("region_id") is not None:
            tile_regions[tile_id] = str(window["region_id"])
        if window.get("scheduling_class") is not None:
            tile_classes[tile_id] = str(window["scheduling_class"]).upper()

    expired = [
        key for key, window in windows.items()
        if (_utc(window.get("window_end_utc")) or float("inf")) <= now
    ]
    for key in expired:
        windows.pop(key, None)

    progress = snapshot.get("progress") or {}
    completed = {str(tile_id) for tile_id in progress.get("completed_tile_ids") or []}
    memory["_completed"] = completed

    feedback = snapshot.get("tile_last_finished")
    if isinstance(feedback, dict) and feedback.get("tile_id"):
        marker = (str(feedback["tile_id"]), float(feedback.get("score") or 0.0))
        if marker != memory.get("_last_feedback"):
            memory["_last_feedback"] = marker
            if marker[1] > 0.0:
                bests = memory.setdefault("_realized_bests", {})
                bests[marker[0]] = max(float(bests.get(marker[0], 0.0)), marker[1])


def _remaining_windows(memory, now, deadline=None):
    """Return unique published future-window counts per tile."""
    counts = {}
    for window in memory.get("_published_windows", {}).values():
        end = _utc(window.get("window_end_utc"))
        start = _utc(window.get("window_start_utc"))
        if end is None or end <= now:
            continue
        if deadline is not None and start is not None and start >= deadline:
            continue
        tile_id = str(window.get("tile_id") or "")
        if tile_id:
            counts[tile_id] = counts.get(tile_id, 0) + 1
    return counts


def _jain(counts, regions):
    values = [float(counts.get(region, 0)) for region in regions]
    total = sum(values)
    squares = sum(value * value for value in values)
    if total <= 0.0 or squares <= 0.0:
        return 0.0
    return total * total / (len(values) * squares)


def _coverage_context(candidates, snapshot, memory):
    """Build current completed-by-region counts from public progress."""
    tile_regions = memory.get("_tile_regions", {})
    completed = memory.get("_completed", set())
    counts = {}
    for tile_id in completed:
        region = tile_regions.get(tile_id)
        if region:
            counts[region] = counts.get(region, 0) + 1

    regions = {str(c.get("region_id") or "") for c in candidates}
    regions.update(str(region) for region in counts)
    known_catalogue_regions = {
        str(region) for region in tile_regions.values() if str(region)
    }
    regions.update(known_catalogue_regions)
    region_count = max(8, len(regions))

    before = _jain(counts, regions) if len(regions) == region_count else None
    if before is None:
        values = list(counts.values()) + [0] * max(0, region_count - len(counts))
        total = float(sum(values))
        squares = float(sum(value * value for value in values))
        before = total * total / (region_count * squares) if squares else 0.0
    science_so_far = sum(float(value) for value in memory.get("_realized_bests", {}).values())
    return counts, regions, region_count, before, science_so_far


def _coverage_adjusted_rate(candidate, snapshot, memory, context=None):
    """Approximate the candidate's immediate gain plus coverage-bonus delta."""
    seconds = max(1.0, float(candidate.get("nominal_exptime_seconds") or 900.0))
    immediate = float(candidate.get("estimated_total_gain") or 0.0)
    weight = float(_score_config(snapshot).get("coverage_bonus_weight") or 0.0)
    if weight <= 0.0:
        return immediate / seconds

    if context is None:
        context = _coverage_context([candidate], snapshot, memory)
    counts, regions, region_count, before, science_so_far = context
    tile_id = str(candidate.get("tile_id") or "")
    region = str(candidate.get("region_id") or "")
    after_counts = dict(counts)
    if tile_id not in memory.get("_completed", set()) and region:
        after_counts[region] = after_counts.get(region, 0) + 1

    if len(regions) == region_count:
        after = _jain(after_counts, regions)
    else:
        values = list(after_counts.values()) + [0] * max(0, region_count - len(after_counts))
        total = float(sum(values))
        squares = float(sum(value * value for value in values))
        after = total * total / (region_count * squares) if squares else 0.0

    science_gain = max(0.0, float(candidate.get("estimated_science_score") or 0.0))
    coverage_delta = weight * (
        science_so_far * (after - before) + science_gain * after
    )
    return (immediate + coverage_delta) / seconds


def _urgent_request_ids(snapshot, memory, now):
    """Find requests whose deadline and published opportunity slack are tight."""
    urgent = set()
    for request in snapshot.get("active_requests") or []:
        request_id = str(request.get("request_id") or "")
        deadline = _utc(request.get("deadline_utc"))
        if not request_id or deadline is None:
            continue
        seconds_left = deadline - now
        if seconds_left <= 0.0 or seconds_left > REQUEST_URGENT_DAYS * 86400.0:
            continue
        requirements = [
            requirement for requirement in request.get("tile_requirements") or []
            if int(requirement.get("remaining_visits") or 0) > 0
        ]
        if not requirements:
            continue
        window_counts = _remaining_windows(memory, now, deadline)
        remaining_visits = sum(
            int(requirement.get("remaining_visits") or 0)
            for requirement in requirements
        )
        opportunities = sum(
            window_counts.get(str(requirement.get("tile_id") or ""), 0)
            for requirement in requirements
        )
        if (
            seconds_left <= REQUEST_FORCE_DAYS * 86400.0
            or opportunities - remaining_visits <= REQUEST_OPPORTUNITY_SLACK
        ):
            urgent.add(request_id)
    return urgent


def _flexible_region_is_tight(candidate, snapshot, memory, chances):
    config = _score_config(snapshot)
    quota = int(config.get("flexible_quota_per_region") or 4)
    progress = snapshot.get("progress") or {}
    done = progress.get("flexible_completed_by_region") or {}
    tile_id = str(candidate.get("tile_id") or "")
    if tile_id in memory.get("_completed", set()):
        return False
    region = str(candidate.get("region_id") or "")
    shortfall = max(0, quota - int(done.get(region, 0) or 0))
    if shortfall <= 0 or chances.get(tile_id, 99) > FLEXIBLE_LAST_CHANCES:
        return False

    completed = memory.get("_completed", set())
    tile_regions = memory.get("_tile_regions", {})
    tile_classes = memory.get("_tile_classes", {})
    available = {
        tile_id
        for tile_id, count in chances.items()
        if count > 0
        and tile_id not in completed
        and tile_regions.get(tile_id) == region
        and tile_classes.get(tile_id) == "FLEXIBLE"
    }
    return len(available) <= shortfall + 1


def _deterministic_choice(candidates, snapshot, memory, now):
    """The reference deterministic strategy (used as fallback and for simple decisions)."""
    if not candidates:
        return None

    chances = _remaining_windows(memory, now)

    # 1. REQUIRED tiles at risk of being missed (highest priority)
    required_at_risk = [
        (chances.get(str(candidate.get("tile_id")), 99), rank, candidate)
        for rank, candidate in enumerate(candidates)
        if str(candidate.get("scheduling_class") or "").upper() == "REQUIRED"
        and str(candidate.get("tile_id") or "") not in memory.get("_completed", set())
        and chances.get(str(candidate.get("tile_id")), 99) <= REQUIRED_LAST_CHANCES
    ]
    if required_at_risk:
        required_at_risk.sort(key=lambda item: (item[0], item[1]))
        count, _, chosen = required_at_risk[0]
        chosen["reason"] = f"required tile has only {count} published window(s) left"
        return chosen

    context = _coverage_context(candidates, snapshot, memory)
    baseline = max(
        candidates,
        key=lambda candidate: _coverage_adjusted_rate(candidate, snapshot, memory, context),
    )
    baseline_rate = _coverage_adjusted_rate(baseline, snapshot, memory, context)

    # 2. Urgent requests
    urgent_requests = _urgent_request_ids(snapshot, memory, now)
    urgent_candidates = [
        candidate for candidate in candidates
        if str(candidate.get("request_id") or "") in urgent_requests
    ]
    if urgent_candidates:
        chosen = max(
            urgent_candidates,
            key=lambda candidate: _coverage_adjusted_rate(candidate, snapshot, memory, context),
        )
        chosen["reason"] = "request deadline and published opportunity slack are tight"
        return chosen

    # 3. Flexible quota protection
    tight_flexible = [
        candidate for candidate in candidates
        if str(candidate.get("scheduling_class") or "").upper() == "FLEXIBLE"
        and _flexible_region_is_tight(candidate, snapshot, memory, chances)
    ]
    if tight_flexible:
        chosen = max(
            tight_flexible,
            key=lambda candidate: _coverage_adjusted_rate(candidate, snapshot, memory, context),
        )
        chosen["reason"] = "last published chance for a region below flexible quota"
        return chosen

    # 4. Coverage-adjusted best
    if baseline_rate > float(candidates[0].get("estimated_gain_per_second") or 0.0):
        baseline["reason"] = "immediate gain plus competition coverage-evenness delta"
        return baseline

    candidates[0]["reason"] = "platform ranking: highest public gain per second"
    return candidates[0]


# ---------------------------------------------------------------------------
# LLM integration (Kimi / moonshot via OpenAI-compatible API)
# ---------------------------------------------------------------------------

def _llm_available():
    """Check whether LLM credentials are configured."""
    return bool(
        os.environ.get("MOONSHOT_API_KEY")
        and os.environ.get("MODEL_NAME")
    )


def _call_llm(prompt, system_prompt="You are an expert telescope scheduling assistant."):
    """Call the configured LLM (Kimi/moonshot via OpenAI-compatible API)."""
    try:
        from openai import OpenAI
    except ImportError:
        print("openai package not installed; LLM disabled", file=sys.stderr, flush=True)
        return None

    api_key = os.environ.get("MOONSHOT_API_KEY", "").strip()
    base_url = os.environ.get("MODEL_BASE_URL", "https://api.moonshot.cn/v1").strip()
    model = os.environ.get("MODEL_NAME", "kimi-k2-0711-preview").strip()
    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "15"))

    if not api_key:
        return None

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"LLM call failed ({type(exc).__name__}): {exc}", file=sys.stderr, flush=True)
        return None


def _extract_json(text):
    """Extract a JSON object from LLM output (handles markdown code blocks)."""
    if not text:
        return None
    stripped = text.strip()
    # Remove markdown code fences
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    # Find the outermost JSON object
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# LLM Stage 1: Task Planning (任务规划) — at each night start
# ---------------------------------------------------------------------------

def _build_planning_prompt(snapshot, memory):
    """Build the prompt for nightly task planning."""
    night_start = snapshot.get("night_start") or {}
    night = night_start.get("night") or {}
    windows = night_start.get("tile_windows") or []

    weekly = snapshot.get("weekly") or {}
    forecasts = weekly.get("weather_forecast") or []
    requests = weekly.get("observation_requests") or []

    active_requests = snapshot.get("active_requests") or []
    progress = snapshot.get("progress") or {}
    completed = set(progress.get("completed_tile_ids") or [])
    flexible_done = progress.get("flexible_completed_by_region") or {}

    weather = snapshot.get("current_site_weather") or {}

    # Summarize coverage state
    tile_regions = memory.get("_tile_regions", {})
    completed_by_region = {}
    for tile_id in completed:
        region = tile_regions.get(tile_id)
        if region:
            completed_by_region[region] = completed_by_region.get(region, 0) + 1

    prompt_data = {
        "night": night,
        "current_weather": weather,
        "tonight_window_count": len(windows),
        "weather_forecast_next_nights": forecasts[:3],
        "active_requests": [
            {
                "request_id": r.get("request_id"),
                "deadline_utc": r.get("deadline_utc"),
                "completion_reward": r.get("completion_reward"),
                "tiles_remaining": [
                    req for req in r.get("tile_requirements", [])
                    if int(req.get("remaining_visits", 0)) > 0
                ],
            }
            for r in active_requests
        ],
        "completed_tiles_count": len(completed),
        "completed_by_region": completed_by_region,
        "flexible_quota_progress": flexible_done,
    }

    return f"""Analyze this telescope scheduling situation and produce a concise observing plan for tonight.

Current situation (JSON):
{json.dumps(prompt_data, indent=2, default=str)}

Output a JSON object with this schema:
{{
  "priority_regions": ["list of region_ids that need more coverage, most urgent first"],
  "urgent_request_ids": ["request_ids that should be prioritized tonight"],
  "weather_strategy": "one of: aggressive (good weather, maximize observations), normal, conservative (poor weather, protect required tiles)",
  "notes": "one sentence of strategic guidance"
}}

Focus on:
1. REQUIRED tiles that might be missed (highest priority, -1000 penalty each)
2. Regions below flexible quota (4 tiles per region)
3. Requests expiring soon (completion reward vs miss penalty)
4. Coverage evenness across regions (Jain index bonus)
"""


def _llm_task_planning(snapshot, memory):
    """Stage 1: Use LLM to plan the night's observing strategy."""
    if not _llm_available():
        return None

    prompt = _build_planning_prompt(snapshot, memory)
    response = _call_llm(
        prompt,
        system_prompt=(
            "You are an expert telescope scheduler for an astronomical survey. "
            "Analyze the situation and output ONLY a valid JSON object with the requested schema. "
            "Be concise and strategic."
        ),
    )
    plan = _extract_json(response)
    if plan:
        print(f"LLM planning: {plan.get('weather_strategy', 'unknown')} strategy, "
              f"priority regions: {plan.get('priority_regions', [])}", file=sys.stderr, flush=True)
    return plan


# ---------------------------------------------------------------------------
# LLM Stage 2: Action Decision (行动决策) — for complex decisions
# ---------------------------------------------------------------------------

def _build_decision_prompt(candidates, snapshot, memory, plan):
    """Build the prompt for LLM action decision among top candidates."""
    top_candidates = candidates[:6]  # Top-6 for the LLM to choose from

    candidate_data = []
    for rank, c in enumerate(top_candidates, 1):
        candidate_data.append({
            "rank": rank,
            "tile_id": c.get("tile_id"),
            "region_id": c.get("region_id"),
            "program": c.get("program"),
            "request_id": c.get("request_id") or "",
            "scheduling_class": c.get("scheduling_class"),
            "estimated_total_gain": c.get("estimated_total_gain"),
            "estimated_gain_per_second": c.get("estimated_gain_per_second"),
            "combined_quality": c.get("combined_quality"),
            "nominal_exptime_seconds": c.get("nominal_exptime_seconds"),
        })

    weather = snapshot.get("current_site_weather") or {}
    active_requests = snapshot.get("active_requests") or []
    progress = snapshot.get("progress") or {}

    prompt_data = {
        "current_weather": weather,
        "candidates": candidate_data,
        "tonight_plan": plan or {},
        "active_requests_summary": [
            {
                "request_id": r.get("request_id"),
                "deadline_utc": r.get("deadline_utc"),
                "is_complete": r.get("is_complete"),
            }
            for r in active_requests
        ],
        "completed_tiles_count": len(progress.get("completed_tile_ids") or []),
    }

    return f"""Choose the best tile to observe right now from the ranked candidates.

Situation (JSON):
{json.dumps(prompt_data, indent=2, default=str)}

Output a JSON object with this schema:
{{
  "chosen_rank": <integer 1-{len(top_candidates)}>,
  "reason": "one short sentence explaining the choice"
}}

Consider:
1. Tonight's plan priorities (if available)
2. REQUIRED tiles that must not be missed
3. Request deadlines and rewards
4. Coverage evenness across regions
5. Weather quality (program bonus requires matching the sky band)
"""


def _llm_action_decision(candidates, snapshot, memory, plan):
    """Stage 2: Use LLM to pick among top candidates in complex situations."""
    if not _llm_available() or not candidates:
        return None

    prompt = _build_decision_prompt(candidates, snapshot, memory, plan)
    response = _call_llm(
        prompt,
        system_prompt=(
            "You are an expert telescope scheduler. Choose exactly one candidate by rank number. "
            "Output ONLY a valid JSON object with chosen_rank and reason."
        ),
    )
    choice = _extract_json(response)
    if not choice:
        return None

    try:
        rank = int(choice.get("chosen_rank", 0))
    except (TypeError, ValueError):
        return None

    if not (1 <= rank <= len(candidates)):
        return None

    chosen = candidates[rank - 1]
    reason = " ".join(str(choice.get("reason", "LLM selection")).split())[:240]
    chosen["reason"] = f"LLM: {reason}"
    return chosen


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def choose_action(candidates, snapshot, memory):
    """Choose a legal candidate, or wait only when no candidate can finish.

    Strategy architecture:
    1. Update public state cache
    2. Night start: LLM task planning (环节 3: 任务规划)
    3. Complex decisions: LLM action decision (环节 4: 行动决策)
    4. Simple decisions: deterministic fallback
    """
    now = _utc((snapshot.get("cursor") or {}).get("timestamp_utc")) or 0.0
    _update_public_state(candidates, snapshot, memory, now)

    if not candidates:
        return None

    # ------------------------------------------------------------------
    # LLM Stage 1: Task Planning at night start
    # ------------------------------------------------------------------
    is_night_start = snapshot.get("night_start") is not None
    if is_night_start and _llm_available():
        plan = _llm_task_planning(snapshot, memory)
        if plan:
            memory["_llm_plan"] = plan
            memory["_llm_plan_night"] = (snapshot.get("cursor") or {}).get("night_id")

    # Retrieve the current plan (only valid for the same night)
    current_night = (snapshot.get("cursor") or {}).get("night_id")
    plan = memory.get("_llm_plan") if memory.get("_llm_plan_night") == current_night else None

    # ------------------------------------------------------------------
    # Decide whether this is a "complex" decision worth LLM involvement
    # ------------------------------------------------------------------
    # Complex situations:
    # - Multiple candidates with similar estimated gain (top-3 within 15%)
    # - Active requests with tight deadlines
    # - Plan indicates a strategic shift is needed
    top_gains = [float(c.get("estimated_total_gain") or 0) for c in candidates[:3]]
    gains_are_close = (
        len(top_gains) >= 2
        and top_gains[0] > 0
        and (top_gains[0] - top_gains[-1]) / top_gains[0] < 0.15
    )
    has_urgent_requests = bool(_urgent_request_ids(snapshot, memory, now))
    has_plan = plan is not None

    use_llm_decision = (
        _llm_available()
        and (gains_are_close or has_urgent_requests)
        and has_plan  # Only use LLM decision when we have a plan to guide it
    )

    # ------------------------------------------------------------------
    # LLM Stage 2: Action Decision for complex situations
    # ------------------------------------------------------------------
    if use_llm_decision:
        llm_choice = _llm_action_decision(candidates, snapshot, memory, plan)
        if llm_choice is not None:
            return llm_choice

    # ------------------------------------------------------------------
    # Deterministic fallback (all other decisions)
    # ------------------------------------------------------------------
    return _deterministic_choice(candidates, snapshot, memory, now)
