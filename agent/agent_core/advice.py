"""Two connected LLM stages: public-notice parsing, then bounded nightly planning."""
from __future__ import annotations

import math

DIRECTIONS = {'N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'}

def bounded_number(value, fallback: float, low: float, high: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(high, max(low, number)) if math.isfinite(number) else fallback

def valid_directions(value, grounded: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({v for v in value if isinstance(v, str) and v in DIRECTIONS & grounded})

def make_night_plan(llm, state, payload, forecast, night_date, night_index, wall_left, log):
    notices = (payload.get('latest_bulletin') or {}).get('notices') or []
    relevant = [n for n in forecast if night_date in (n.get('nights') or [])]
    grounded = {n.get('direction') for n in notices + relevant if n.get('direction') in DIRECTIONS}
    bulletin_text = '; '.join(f"{n.get('event_kind')} toward {n.get('direction')}" for n in notices) or 'No active notices.'
    parsed = llm.ask_json(
        'Stage 1: parse the public telescope weather bulletin and forecast. Return only JSON '
        '{"avoid_directions": [compass codes], "duration_scale": number 0.7-1.4}. '
        'Use only directions explicitly present in the input. Do not invent numeric weather '
        'measurements, hidden instrument faults, or future events.',
        {'stage': 'notice_parsing', 'night': night_date, 'bulletin_text': bulletin_text,
         'forecast_notices': relevant}, wall_left,
    )
    parsed = parsed if isinstance(parsed, dict) else {}
    weather = {'avoid_directions': valid_directions(parsed.get('avoid_directions'), grounded),
               'duration_scale': bounded_number(parsed.get('duration_scale'), 1.0, 0.7, 1.4)}
    log(f"llm-stage notice_parsing: {'ok' if parsed else 'fallback'} {weather}")
    required_remaining = sum(required and factor < state.scoring.required_threshold
                             for required, factor in zip(state.required, state.factor))
    scarce = sum(state.required[i] and state.factor[i] < state.scoring.required_threshold
                 and state.last_night[i] <= night_index + 1 for i in range(len(state.ids)))
    requests = [{'request_id': r.get('request_id'), 'deadline_utc': r.get('deadline_utc'),
                 'remaining_count': r.get('remaining_count'), 'completion_reward': r.get('completion_reward')}
                for r in (payload.get('active_requests') or [])[:8]]
    planned = llm.ask_json(
        'Stage 2: plan this observing night using the parsed weather and realized survey progress. '
        'Return only JSON {"required_priority": number 1.0-1.5, "duration_scale": number 0.85-1.15, '
        '"reason": "brief rationale"}. Raise required_priority when required targets have few '
        'remaining nights. Use duration_scale to adjust exposure candidates, not to override '
        'visibility or fibre constraints. The supplied scoring settings are authoritative. '
        'Requests earn rewards when completed and have no miss penalty. Do not propose actions.',
        {'stage': 'night_planning', 'night': night_date, 'parsed_weather': weather,
         'required_remaining': required_remaining, 'required_with_few_nights': scarce,
         'required_factor_threshold': state.scoring.required_threshold,
         'required_miss_penalty': state.scoring.required_penalty, 'active_requests': requests,
         'learned_quality_scale': state.scale}, wall_left,
    )
    planned = planned if isinstance(planned, dict) else {}
    plan = {'avoid_directions': weather['avoid_directions'],
            'duration_scale': bounded_number(weather['duration_scale'] * bounded_number(
                planned.get('duration_scale'), 1.0, 0.85, 1.15), 1.0, 0.7, 1.4),
            'required_priority': bounded_number(planned.get('required_priority'), 1.0, 1.0, 1.5),
            'parsing_ok': bool(parsed), 'planning_ok': bool(planned)}
    log(f"llm-stage night_planning: {'ok' if planned else 'fallback'} {plan}")
    return plan
