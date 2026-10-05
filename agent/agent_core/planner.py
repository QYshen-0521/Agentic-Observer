"""Bounded joint pointing, fibre, exposure and program search using public inputs.

Realized scores drive marginal science value. Required completion and feasible
request windows contribute bounded planning values. Connected LLM notice parsing
and task planning adjust exposure candidates and priorities at changed conditions.
"""
from __future__ import annotations

from datetime import timedelta
import math

from .geometry import (
    Moon,
    SIDEREAL_DEG_PER_SECOND,
    altaz_to_radec,
    format_utc,
    local_sidereal_deg,
    lunar_factor,
    max_hour_angle_deg,
    parse_utc,
    radec_to_altaz,
    shift_altaz,
    tangent_offsets,
    wrap180,
)
from .clock import Clock
from .llm_client import LLMClient
from .memory import TraceLog
from .state import PendingPrediction
from .advice import make_night_plan
from .calibration import PointingCalibration

PLAN_FACTOR_SAFETY = 0.9
EDGE_MARGIN_DEG = 0.08
DURATIONS = (300, 450, 600, 900, 1200, 1500, 1800, 2400, 3000, 3600)
ANCHORS = 6
ANCHOR_POOL = 300
BLOCKING_KINDS = {"terrain_obstruction", "rocket_launch"}
DIRECTION_AZ = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0,
                "SW": 225.0, "W": 270.0, "NW": 315.0}

REPORT_DROP = 0.62
REPORT_CONFIRMATIONS = 3
REPORT_SPACING_HOURS = 6.0


# Request rewards are shared by the remaining targets of a feasible request.
# Only exposures entirely inside its public time window can contribute.


def _az_distance(a: float, b: float) -> float:
    return abs(wrap180(a - b))


class Planner:
    def __init__(self, state, log=lambda text: None):
        self.state = state
        self.log = log
        self.grid = state.fiber_grid
        self.calibration = PointingCalibration(self.grid)
        self.clock = Clock()
        self.min_level = 0
        self.llm = LLMClient(log=log)
        self.trace = TraceLog(log=log)

        self.observe_count = 0
        self.reports = 0
        self.false_reports = 0
        self.repairs = 0
        self.report_pending = False
        self._request_groups = []
        self._request_bonus = {}
        self._last_advice_key = None
        self._last_advice_night = -100
        self.last_report_hours = float("-inf")
        self.suspicion_hours: list[float] = []
        self.night_index_seen: int | None = None
        self.consecutive_reports = 0
        self._last_forecast_notices: list = []
        self.total_assigned = 0
        self.total_hit = 0
        self._current_action_index = None
        self._request_thresholds_now: dict = {}
        self.required_priority = 1.0
        self.required_bonus = max(0.0, state.scoring.required_penalty) * 1.2

        log(f"planner: {len(state.ids)} targets ({sum(state.required)} required), "
            f"{len(state.nights)} nights, llm model={self.llm.model} base_url={self.llm.base_url}")

    # -- top-level decision ----------------------------------------------------

    def decide(self, payload: dict) -> dict:
        state = self.state
        now = parse_utc(payload["now_utc"])
        hours = (now - state.survey_start).total_seconds() / 3600.0

        for message in payload.get("new_messages", []):
            if message.get("record_type") == "forecast":
                self._last_forecast_notices = message.get("notices", [])
            elif message.get("record_type") == "observation_request":
                self.log(f"planner: observation request {message.get('request_id')} issued, "
                         f"{len(message.get('target_ids', []))} targets by {message.get('deadline_utc')}")
            elif message.get("record_type") == "observation_request_result":
                self.log(f"planner: observation request {message.get('request_id')} "
                         f"{message.get('status')} (reward {message.get('score_delta', 0.0)})")
        if self.calibration.feedback(payload.get("last_result")):
            self.log(f"planner: calibrated pointing offset={self.calibration.offset}")
            state.misses = [0] * len(state.ids)
        # Consume the previous valid exposure before applying a later invalidation.
        state.on_result(payload.get("last_result"), hours)
        state.on_messages(payload.get("new_messages", []), payload.get("latest_bulletin"))
        self._on_report_result(payload.get("last_result"))
        last_result = payload.get("last_result")
        if last_result and last_result.get("action") == "observe":
            self.total_assigned += int(last_result.get("assigned_count", 0))
            self.total_hit += int(last_result.get("hit_count", 0))
        self.clock.update(payload.get("wallclock"))
        self._pace(now)
        self._current_action_index = payload.get("observe_action_index")
        self._request_thresholds_now = self._request_thresholds(payload.get("active_requests") or [])
        self._prepare_requests(payload.get("active_requests") or [], now)

        night = state.current_night(now)
        if night is None:
            nxt = state.next_night_start(now)
            if nxt is None:
                return {"action": "finish", "reason": "no observing night left"}
            return {"action": "wait", "until_utc": format_utc(nxt), "reason": "daytime: sleep until the next night"}
        night_index, night_start, night_end = night

        if self.night_index_seen != night_index:
            self.night_index_seen = night_index
            self._night_advice(night_start, payload)

        if (night_end - now).total_seconds() < state.min_exposure:
            nxt = state.next_night_start(now)
            if nxt is None:
                return {"action": "finish", "reason": "survey over"}
            return {"action": "wait", "until_utc": format_utc(nxt), "reason": "night ending"}

        if state.site_closed():
            return {"action": "wait", "duration_seconds": self._to_next_slot(now, night_start),
                    "reason": "bulletin: rain/storm over the whole sky"}

        report = self._maybe_report(hours, payload)
        if report is not None:
            return report

        action = self.plan(now, night_end, night_index, hours)
        if action is None:
            return {"action": "wait", "duration_seconds": self._to_next_slot(now, night_start),
                    "reason": "nothing useful is up"}
        self.observe_count += 1
        action["reason"] = f"{len(action['assignments'])} fibres, program {action['program']}"
        return action

    def on_finish(self, payload: dict) -> None:
        self.trace.write({"event": "finish", **payload})
        self.trace.close()
        self.log(f"planner: finished termination_reason={payload.get('termination_reason')} "
                 f"observes={self.observe_count} reports={self.reports} llm_calls={self.llm.calls_made}")

    def note_action(self, action: dict) -> None:
        """Called by agent.py right after an action is validated, so the consecutive-report
        counter (enforced by validation.py) stays correct even when a fallback replaced it."""
        self.consecutive_reports = self.consecutive_reports + 1 if action.get("action") == "report" else 0
        if action.get("action") != "report":
            self.report_pending = False  # validation may have replaced a proposed report
        self.calibration.record(action, self.state.pending, self.state.min_alt)

    def _to_next_slot(self, now, night_start) -> int:
        slot = self.state.slot_seconds
        into = (now - night_start).total_seconds() % slot
        return int(max(60, min(3600, slot - into if into else slot)))

    def _pace(self, now) -> None:
        """Do less work per decision when the compute budget is short for the nights
        still to come. Budget and own cost are both real CPU seconds of this machine
        (see clock.py), so the pace does not depend on how fast the machine is."""
        state = self.state
        night_seconds = sum(max(0.0, (end - max(start, now)).total_seconds()) for start, end in state.nights if end > now)
        decisions_left = max(1.0, night_seconds / 700.0)
        per_decision = self.clock.compute_left() / decisions_left
        level = 0 if per_decision > 0.12 else 1 if per_decision > 0.04 else 2
        # Reversible pacing with hysteresis; one costly early decision must not
        # permanently remove the search budget for the remainder of the season.
        if self.clock.avg_cost > per_decision * 0.9:
            level = min(2, state.fast_level + 1)
        elif self.clock.avg_cost > per_decision * 0.35:
            level = max(level, state.fast_level)
        level = max(level, self.min_level)
        if level != state.fast_level:
            self.log(f"planner: pace level {level} ({per_decision * 1000:.0f} ms CPU per decision left, "
                     f"recent cost {self.clock.avg_cost * 1000:.0f} ms)")
            state.fast_level = level

    # -- LLM: two calls once per night, merged -----------------------------------

    def _night_advice(self, night_start, payload: dict) -> None:
        """Parse public notices, then plan; both stages affect bounded planner inputs."""
        night_date = (night_start - timedelta(hours=12)).date().isoformat()
        relevant = [n for n in self._last_forecast_notices if night_date in (n.get('nights') or [])]
        key = (tuple(sorted((n.get('event_kind', ''), n.get('direction', ''))
                            for n in relevant + ((payload.get('latest_bulletin') or {}).get('notices') or []))),
               tuple(r.get('request_id') for r in payload.get('active_requests') or []))
        if key == self._last_advice_key and self.night_index_seen - self._last_advice_night < 7:
            return
        self._last_advice_key = key
        self._last_advice_night = self.night_index_seen
        plan = make_night_plan(self.llm, self.state, payload, self._last_forecast_notices,
                               night_date, self.night_index_seen, self.clock.wall_left, self.log)
        self.state.extra_avoid = set(plan['avoid_directions'])
        self.state.duration_scale = plan['duration_scale']
        self.required_priority = plan['required_priority']
        self.trace.write({'event': 'night_plan', 'night_date': night_date, **plan})

    # -- instrument fault reporting (deterministic rules + LLM confirmation) -----

    def _on_report_result(self, result):
        if not result or result.get("action") != "report" or not self.report_pending:
            return
        self.report_pending = False
        if result.get("correct") and result.get("repaired"):
            self.repairs += 1
            self.false_reports = 0
            self.state.forget_quality_history()
        else:
            self.false_reports += 1
        self.log(f"planner: report feedback repairs={self.repairs} false_since_repair={self.false_reports}")

    def _maybe_report(self, hours: float, payload: dict):
        state = self.state
        state.force_program = None
        paid = self.false_reports >= state.false_report_free_allowance
        cooldown = 72.0 if paid else 24.0
        if self.report_pending or hours - self.last_report_hours < cooldown:
            return None
        evidence = state.fault_evidence()
        threshold = 0.40 if paid else REPORT_DROP
        if evidence is None or evidence.drop >= threshold:
            self.suspicion_hours = []
            return None
        # Program band excludes instrument efficiency. Matched DARK observations
        # during a sustained science-quality collapse corroborate an instrument fault.
        if evidence.dark_checks < 6:
            state.force_program = "DARK"
            if paid:
                return None
        elif evidence.dark_matched < 0.75 * evidence.dark_checks:
            self.suspicion_hours = []
            return None
        if self.suspicion_hours and hours - self.suspicion_hours[-1] < REPORT_SPACING_HOURS:
            return None
        self.suspicion_hours.append(hours)
        if len(self.suspicion_hours) < REPORT_CONFIRMATIONS:
            return None
        self.suspicion_hours = []
        verdict_answer = self.llm.ask_json(
            "Check telescope data quality. Program bands exclude instrument efficiency. "
            'A correct report repairs the instrument; false reports may cost points. '
            'Reply with JSON only: {"report": true|false}.',
            {**evidence._asdict(), "false_since_repair": self.false_reports,
             "free_allowance": state.false_report_free_allowance}, self.clock.wall_left,
        )
        verdict = verdict_answer.get("report") if isinstance(verdict_answer, dict) else None
        self.last_report_hours = hours
        if verdict is False:
            return None
        self.reports += 1
        self.report_pending = True
        self.log(f"planner: reporting instrument fault at {payload.get('now_utc')} evidence={evidence}")
        return {"action": "report", "reason": f"sustained quality drop to {evidence.drop:.0%}",
                "decision_source": "llm-confirmed" if verdict is True else "rule"}

    # -- planning value / achievability -----------------------------------------

    def _direction_factor(self, alt: float, az: float) -> float:
        state = self.state
        for direction in state.terrain:
            if direction in DIRECTION_AZ and alt < 50.0 and _az_distance(az, DIRECTION_AZ[direction]) <= 60.0:
                return 0.0
        factor = 1.0
        for key in state.notices:
            kind, _, direction = key.partition("|")
            if direction not in DIRECTION_AZ:
                continue
            near = _az_distance(az, DIRECTION_AZ[direction]) <= 67.5
            if kind in BLOCKING_KINDS and near and alt < 62.0:
                return 0.0
            if near and alt < 75.0:
                factor = min(factor, 0.35)
        for direction in state.extra_avoid:
            if direction in DIRECTION_AZ and _az_distance(az, DIRECTION_AZ[direction]) <= 67.5 and alt < 70.0:
                factor = min(factor, 0.35)
        for blocked_az, blocked_alt in state.blocked[-40:]:
            if _az_distance(az, blocked_az) <= 12.0 and alt <= blocked_alt + 3.0:
                factor = min(factor, 0.2)
        return factor

    def _request_thresholds(self, active_requests: list) -> dict:
        """Targets still needed in the request window, independent of lifetime factor."""
        state = self.state
        thresholds: dict[int, float] = {}
        for request in active_requests:
            if int(request.get("remaining_count", 0)) <= 0:
                continue
            threshold = float(request.get("completion_factor_threshold", 1.0))
            completed = set(request.get("completed_target_ids") or [])
            for target_id in request.get("target_ids", []):
                if target_id in completed:
                    continue
                i = state.index_of.get(target_id)
                if i is None:
                    continue
                if i not in thresholds or threshold < thresholds[i]:
                    thresholds[i] = threshold
        return thresholds

    def _prepare_requests(self, requests, now):
        self._request_groups = []
        self._request_bonus = {}
        state = self.state
        for request in requests:
            remaining = int(request.get("remaining_count", 0))
            if remaining <= 0 or not request.get("deadline_utc"):
                continue
            deadline = parse_utc(request["deadline_utc"])
            if now >= deadline or (request.get("issued_at_utc") and now < parse_utc(request["issued_at_utc"])):
                continue
            threshold = float(request.get("completion_factor_threshold", 0.5))
            completed = set(request.get("completed_target_ids") or [])
            needed = {state.index_of[t] for t in request.get("target_ids", [])
                      if t in state.index_of and t not in completed}
            # Necessary feasibility check using all remaining night intervals.
            available = sum(max(0.0, (min(end, deadline) - max(start, now)).total_seconds())
                            for start, end in state.nights if end > now and start < deadline)
            feasible = []
            lst = local_sidereal_deg(now, state.lon)
            for i in needed:
                h = state.hmax[i]
                ha = wrap180(lst - state.ra[i])
                seconds = max(state.min_exposure, threshold * state.scoring.f0t0 /
                              max(1e-9, state.flux[i] * state.scale * PLAN_FACTOR_SAFETY))
                if h > 0 and seconds <= state.max_exposure and -h <= ha <= h and seconds <= (h-ha) / SIDEREAL_DEG_PER_SECOND:
                    feasible.append((seconds, i))
            feasible.sort()
            if len(feasible) < remaining or sum(t for t, _ in feasible[:remaining]) > available:
                continue
            reward = max(0.0, float(request.get("completion_reward", 0)))
            group = {"targets": {i for _, i in feasible}, "remaining": remaining,
                     "threshold": threshold, "deadline": deadline, "reward": reward}
            self._request_groups.append(group)
            for _, i in feasible:
                self._request_bonus[i] = self._request_bonus.get(i, 0.0) + 0.65 * reward / remaining

    def _value(self, i: int) -> float:
        state = self.state
        top = max(state.scoring.program_multipliers.values())
        gain = max(0.0, state.weight[i] * top - state.best_score[i])
        if state.required[i] and state.factor[i] < state.scoring.required_threshold:
            gain += self.required_bonus * self.required_priority
        gain += self._request_bonus.get(i, 0.0)
        return gain * max(0.2, 0.8 ** state.misses[i])

    # -- main planning pass -------------------------------------------------------

    def plan(self, now, night_end, night_index: int, hours: float):
        state = self.state
        state.update_scale(hours)
        lst = local_sidereal_deg(now, state.lon)
        horizon = min(night_end, state.survey_end)
        seconds_left = (horizon - now).total_seconds()
        if seconds_left < state.min_exposure:
            return None
        min_visible = min(state.min_exposure, seconds_left) * SIDEREAL_DEG_PER_SECOND

        still_active = []
        candidates: list[tuple[float, int]] = []
        for i in sorted(set(state.active) | set(self._request_bonus)):
            v = self._value(i)
            if v <= 0.0:
                continue
            still_active.append(i)
            ha = wrap180(lst - state.ra[i])
            h = state.hmax[i]
            if -h <= ha <= h - min_visible:
                nights_left = max(1, state.last_night[i] - night_index + 1)
                setting = (1.0 + 0.5 * max(0.0, ha / h)) if h < 180 else 1.0
                candidates.append((v * (1.0 + 2.0 / nights_left) * setting, i))
        state.active = still_active
        if not candidates:
            return None
        candidates.sort(key=lambda t: -t[0])

        moon = Moon(now + timedelta(seconds=450), lst, state.lat)
        altaz_cache: dict[int, tuple[float, float]] = {}

        def altaz(i: int) -> tuple[float, float]:
            cached = altaz_cache.get(i)
            if cached is None:
                cached = radec_to_altaz(state.ra[i], state.dec[i], lst, state.lat)
                altaz_cache[i] = cached
            return cached

        visible = {i for _, i in candidates}
        achievable_cache: dict[int, float] = {}
        scoring = state.scoring

        def achievable(i: int) -> float:
            cached = achievable_cache.get(i)
            if cached is not None:
                return cached
            alt, az = altaz(i)
            lunar = lunar_factor(moon, state.ra[i], state.dec[i], scoring.lunar_model)
            model = scoring.quality_model(alt, lunar) or 0.0
            k = (state.flux[i] * model * state.scale * PLAN_FACTOR_SAFETY) / scoring.f0t0
            ha = wrap180(lst - state.ra[i])
            up = (state.hmax[i] - ha) / SIDEREAL_DEG_PER_SECOND if state.hmax[i] < 180 else 1e9
            reach = min(1.0, k * min(state.max_exposure, up, seconds_left))
            f = state.factor[i]
            program = scoring.program_band(model * state.band_scale)
            gain = max(0.0, state.weight[i] * reach * scoring.program_multipliers[program] - state.best_score[i])
            gain += self._request_bonus.get(i, 0.0) if reach >= self._request_thresholds_now.get(i, 1.0) else 0.0
            if state.required[i] and f < scoring.required_threshold and reach >= scoring.required_threshold:
                gain += self.required_bonus * self.required_priority
            damp = max(0.2, 0.8 ** state.misses[i])
            result = gain * damp * self._direction_factor(alt, az)
            achievable_cache[i] = result
            return result

        anchors: list[tuple[float, int]] = []
        for checked, (priority, i) in enumerate(candidates):
            if checked >= ANCHOR_POOL and len(anchors) >= 3 * ANCHORS:
                break
            weighted = achievable(i) * priority / max(1e-9, self._value(i))
            if weighted > 0:
                anchors.append((weighted, i))
        if not anchors:
            return None
        anchors.sort(key=lambda t: -t[0])

        n_anchors = 1 if state.fast_level >= 1 else ANCHORS
        middle = sorted({(self.grid.side - 1) // 2, self.grid.side // 2})
        fibers = range(self.grid.n) if state.fast_level < 2 else tuple(
            row * self.grid.side + col for row in middle for col in middle)
        fields = []
        tried = 0
        for _, anchor in anchors:
            if tried >= n_anchors and fields:
                break
            if tried >= n_anchors + 8:
                break
            tried += 1
            a_alt, a_az = altaz(anchor)
            near = [j for j in state.neighbours(state.ra[anchor], state.dec[anchor], 1.42 * self.grid.fov) if j in visible]
            near_values = {j: achievable(j) for j in near}
            for fiber in fibers:
                d_north, d_east = self.grid.fiber_center(fiber)
                c_alt, c_az = shift_altaz(a_alt, a_az, -d_north, -d_east)
                if not (state.min_alt + 1.5 <= c_alt <= 89.0):
                    continue
                c_alt = round(c_alt, 4)
                c_az = round(c_az, 4) % 360.0
                chosen = {}
                for j, v in near_values.items():
                    if v <= 0.0:
                        continue
                    alt, az = altaz(j)
                    offsets = tangent_offsets(alt, az, c_alt, c_az)
                    if offsets is None:
                        continue
                    fib, margin = self.grid.classify(*offsets)
                    if fib is None:
                        continue
                    score = v * (1.0 if margin >= EDGE_MARGIN_DEG else 0.65)
                    chosen.setdefault(fib, []).append((score, j, margin))
                if not chosen:
                    continue
                for options in chosen.values():
                    options.sort(reverse=True)
                    del options[3:]
                total = sum(options[0][0] for options in chosen.values())
                fields.append((total, c_alt, c_az, chosen))
        if not fields:
            return None
        fields.sort(key=lambda field: -field[0])
        # Jointly compare a bounded shortlist; do not choose a field before costing
        # the common duration and program of its actual assignments.
        plans = []
        for _, c_alt, c_az, chosen in fields[:(6 if state.fast_level == 0 else 3)]:
            plan = self._finish_plan(now, lst, c_alt, c_az, chosen, seconds_left, moon, altaz, hours, night_index)
            if plan is not None:
                plans.append(plan)
        if not plans:
            return None
        _, action, predictions = max(plans, key=lambda plan: plan[0])
        alt, az = self.calibration.command(action['pointing']['alt_deg'], action['pointing']['az_deg'])
        if not (state.min_alt <= alt <= 90.0):
            return None
        action['pointing'] = {'alt_deg': round(alt, 4), 'az_deg': round(az, 4) % 360}
        state.pending = predictions
        state.pending_action_index = self._current_action_index
        state.pending_program = action['program']
        state.pending_duration = action['duration_seconds']
        state.pending_night = night_index
        return action

    def _finish_plan(self, now, lst, c_alt, c_az, chosen, seconds_left, moon, altaz, hours, night_index):
        state, scoring = self.state, self.state.scoring
        c_ra, c_dec = altaz_to_radec(c_alt, c_az, lst, state.lat)
        c_hmax = max_hour_angle_deg(c_dec, state.lat, state.min_alt + 0.3)
        c_ha = wrap180(lst - c_ra)
        center_up = (c_hmax-c_ha) / SIDEREAL_DEG_PER_SECOND if c_hmax < 180 else 1e9
        limit = min(seconds_left, center_up, state.max_exposure)
        if limit < state.min_exposure:
            return None
        info = {}
        durations = {state.min_exposure, int(limit)}
        for base in DURATIONS:
            durations.add(round(base * state.duration_scale / 30) * 30)
        clean = not state.all_sky_notice()
        for fiber, options in chosen.items():
            info[fiber] = []
            for _, j, margin in options:
                alt, az = altaz(j)
                lunar = lunar_factor(moon, state.ra[j], state.dec[j], scoring.lunar_model)
                model = scoring.quality_model(alt, lunar)
                up = (state.hmax[j]-wrap180(lst-state.ra[j])) / SIDEREAL_DEG_PER_SECOND if state.hmax[j] < 180 else 1e9
                k = state.flux[j] * model * state.scale * PLAN_FACTOR_SAFETY / scoring.f0t0
                band = scoring.program_band(model * state.band_scale)
                item = (j, alt, az, model, up, k, band, margin)
                info[fiber].append(item)
                if k > 0:
                    for threshold in (1.0, scoring.required_threshold if state.required[j] else 1.0,
                                      self._request_thresholds_now.get(j, 1.0)):
                        durations.add(math.ceil(threshold / k / 30) * 30)
                durations.add(int(min(limit, up)))
        best = None
        programs = (state.force_program,) if state.force_program else ('DARK', 'BRIGHT', 'BACKUP')
        for duration in sorted(d for d in durations if state.min_exposure <= d <= limit):
            for program in programs:
                assignments, predictions = {}, {}
                gain = 0.0
                for fiber, options in info.items():
                    best_target = None
                    for item in options:
                        j, alt, az, model, up, k, band, margin = item
                        if up < duration:
                            continue
                        reached = min(1.0, k * duration)
                        score = state.weight[j] * reached * scoring.program_multiplier(program, band)
                        value = max(0.0, score - state.best_score[j])
                        if state.required[j] and state.factor[j] < scoring.required_threshold <= reached:
                            value += self.required_bonus * self.required_priority
                        request_value = sum(0.65 * g['reward'] / g['remaining'] for g in self._request_groups
                                            if j in g['targets'] and reached >= g['threshold']
                                            and now + timedelta(seconds=duration) <= g['deadline'])
                        value += request_value
                        value *= 1.0 if margin >= EDGE_MARGIN_DEG else 0.65
                        if best_target is None or value > best_target[0]:
                            best_target = (value, item, reached)
                    if best_target is None or best_target[0] <= 0:
                        continue
                    value, item, reached = best_target
                    j, alt, az, model, up, k, band, margin = item
                    gain += value
                    assignments[str(fiber)] = state.ids[j]
                    predictions[state.ids[j]] = PendingPrediction(model, model / 0.95, alt, az,
                        clean and self._direction_factor(alt, az) >= 1.0)
                if not assignments:
                    continue
                rate = gain / duration
                if best is None or rate > best[0]:
                    best = (rate, {'action': 'observe', 'pointing': {'alt_deg': c_alt, 'az_deg': c_az},
                                  'assignments': assignments, 'duration_seconds': duration, 'program': program}, predictions)
        return best
