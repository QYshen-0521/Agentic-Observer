#!/usr/bin/env python3
"""Build an offline, truth-weather schedule for a public practice scenario.

This is intentionally separate from the participant agent: practice scenarios
publish their weather truth and accept a results CSV, while competition
scenarios do neither.  The generated trace is always verified by the same
ChallengeScorer used by the platform.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import pickle
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from challenge.contracts import DECISION_COLUMNS
from challenge.scoring_core import (
    ChallengeScorer,
    PROGRAMS,
    load_decisions,
    score_files,
)
from challenge.weather_simulator import weather_quality


@dataclass(frozen=True)
class Candidate:
    tile_id: str
    slot_index: int
    start: datetime
    end: datetime
    program: str
    base: float
    bonus: float
    request_id: str = ""

    @property
    def score(self) -> float:
        return self.base + self.bonus


def evaluate_start(
    scorer: ChallengeScorer,
    tile_id: str,
    slot_index: int,
    offset_seconds: int = 0,
    require_midpoint_legal: bool = True,
) -> Candidate | None:
    """Return the exact best-program exposure at the requested cursor."""
    tile = scorer.tiles[tile_id]
    if slot_index >= len(scorer.slots):
        return None
    first = scorer.slots[slot_index]
    remaining = tile.nominal_exptime_seconds
    index = slot_index
    offset = offset_seconds
    segments: list[tuple[float, str]] = []
    while remaining > 0 and index < len(scorer.slots):
        slot = scorer.slots[index]
        if slot.night_id != first.night_id:
            return None
        seconds = min(remaining, slot.duration_seconds - offset)
        start = slot.timestamp_utc + timedelta(seconds=offset)
        midpoint = start + timedelta(seconds=seconds / 2)
        if (
            not scorer._tile_legal(tile, start)
            or require_midpoint_legal
            and not scorer._tile_legal(tile, midpoint)
        ):
            return None
        conditions = scorer.weather.get_effective_conditions(slot.slot_id, tile_id)
        if not conditions["is_observable"]:
            return None
        geometry = scorer.geometry.get_tile_geometry(tile_id, midpoint)
        atmospheric = weather_quality(
            conditions, float(geometry["airmass"]), scorer.weather.config
        )
        band_quality = weather_quality(
            conditions,
            float(geometry["airmass"]),
            scorer.weather.config,
            include_efficiency=False,
        )
        lunar = float(geometry["lunar_quality_factor"])
        base = (
            scorer.tile_values[tile_id]
            * seconds
            / tile.nominal_exptime_seconds
            * atmospheric
            * lunar
        )
        program_quality = band_quality if scorer.mechanics else atmospheric
        segments.append((base, scorer._quality_band(program_quality * lunar)))
        remaining -= seconds
        index += 1
        offset = 0
    if remaining:
        return None
    best_program = ""
    best_bonus = -1.0
    bonuses = scorer.config["program_bonus"]
    for program in sorted(PROGRAMS):
        bonus = sum(
            base * float(bonuses[program]) for base, band in segments if band == program
        )
        if bonus > best_bonus:
            best_program, best_bonus = program, bonus
    return Candidate(
        tile_id=tile_id,
        slot_index=slot_index,
        start=first.timestamp_utc + timedelta(seconds=offset_seconds),
        end=first.timestamp_utc
        + timedelta(seconds=offset_seconds + tile.nominal_exptime_seconds),
        program=best_program,
        base=sum(base for base, _ in segments),
        bonus=best_bonus,
    )


def evaluate_weather_interruption(
    scorer: ChallengeScorer,
    tile_id: str,
    slot_index: int,
    offset_seconds: int = 0,
) -> Candidate | None:
    """Return a safe exposure prefix that ends only at a weather closure."""
    tile = scorer.tiles[tile_id]
    first = scorer.slots[slot_index]
    remaining = tile.nominal_exptime_seconds
    index = slot_index
    offset = offset_seconds
    elapsed = 0
    while remaining > 0 and index < len(scorer.slots):
        slot = scorer.slots[index]
        moment = slot.timestamp_utc + timedelta(seconds=offset)
        if slot.night_id != first.night_id or not scorer._tile_legal(tile, moment):
            return None
        conditions = scorer.weather.get_effective_conditions(slot.slot_id, tile_id)
        if not conditions["is_observable"]:
            if not elapsed:
                return None
            start = first.timestamp_utc + timedelta(seconds=offset_seconds)
            return Candidate(
                tile_id=tile_id,
                slot_index=slot_index,
                start=start,
                end=start + timedelta(seconds=elapsed),
                program="BACKUP",
                base=0.0,
                bonus=0.0,
            )
        seconds = min(remaining, slot.duration_seconds - offset)
        elapsed += seconds
        remaining -= seconds
        index += 1
        offset = 0
    return None


def overlaps(left: Candidate, right: Candidate) -> bool:
    return left.start < right.end and right.start < left.end


def fits(candidate: Candidate, scheduled: list[Candidate], *, ignore_tile: str | None = None) -> bool:
    return all(
        (ignore_tile is not None and other.tile_id == ignore_tile)
        or not overlaps(candidate, other)
        for other in scheduled
    )


def choose_request_tiles(scorer: ChallengeScorer, candidates: dict[str, list[Candidate]]):
    """Choose the least-cost tile subset for AT_LEAST_N requests."""
    selected: dict[str, list[str]] = {}
    for request in sorted(scorer.requests.values(), key=lambda item: item.available_from_utc):
        tile_ids = list(scorer.request_tiles[request.request_id])
        feasible = [
            tile_id for tile_id in tile_ids
            if scorer._tile_has_request_opportunity(
                scorer.tiles[tile_id],
                request.available_from_utc,
                request.deadline_utc,
            )
        ]
        # The authoritative scorer excuses the whole request when fewer tiles
        # are observable than its required count.  Spending exposures on a
        # request that can never earn reward only creates conflicts.
        if len(feasible) < request.required_tile_count:
            selected[request.request_id] = []
            continue
        tile_ids = feasible
        if request.completion_mode == "ALL":
            selected[request.request_id] = tile_ids
            continue
        costs = []
        for tile_id in tile_ids:
            all_rows = candidates[tile_id]
            global_best = all_rows[0].score
            eligible = [
                row for row in all_rows
                if row.start < request.deadline_utc
            ]
            constrained = eligible[0].score if eligible else float("-inf")
            costs.append((global_best - constrained, tile_id))
        costs.sort()
        selected[request.request_id] = [
            tile_id for _, tile_id in costs[: request.required_tile_count]
        ]
    return selected


def earliest_deadlines(scorer: ChallengeScorer, request_tiles: dict[str, list[str]]):
    deadlines: dict[str, datetime] = {}
    for request_id, tile_ids in request_tiles.items():
        deadline = scorer.requests[request_id].deadline_utc
        for tile_id in tile_ids:
            deadlines[tile_id] = min(deadlines.get(tile_id, deadline), deadline)
    return deadlines


def collect_reference_waits(
    scenario: Path, decisions_path: Path
) -> list[tuple[datetime, int, tuple[str, ...]]]:
    """Replay a trace and retain its exact avoidable-wait blocker sets."""
    scorer = ChallengeScorer.from_files(scenario)
    waits: list[tuple[datetime, int, tuple[str, ...]]] = []
    consume_wait = scorer._consume_wait

    def capture(elapsed: int, kind: str):
        current = scorer.current_time()
        slot = scorer.current_slot()
        actionable = ()
        if (
            current is not None
            and slot is not None
            and scorer._has_actionable_tile()
        ):
            actionable = tuple(
                tile.tile_id
                for tile in scorer.tiles.values()
                if scorer._can_complete_from(
                    tile, scorer.slot_index, scorer.offset_seconds
                )
            )
        if actionable:
            waits.append((current, elapsed, actionable))
        return consume_wait(elapsed, kind)

    scorer._consume_wait = capture
    for decision in load_decisions(decisions_path):
        scorer.apply_decision(decision)
    return waits


def select_primary_jobs(
    scorer: ChallengeScorer,
    candidates: dict[str, list[Candidate]],
    deadlines: dict[str, datetime],
    quality_fraction: float,
) -> list[Candidate]:
    """Select one high-value non-overlapping science exposure per tile."""
    eligible: dict[str, list[Candidate]] = {}
    for tile_id, rows in candidates.items():
        deadline = deadlines.get(tile_id)
        filtered = [row for row in rows if deadline is None or row.start < deadline]
        if not filtered:
            raise RuntimeError(f"no science candidate before deadline for {tile_id}")
        threshold = filtered[0].score * quality_fraction
        early_good = sorted(
            (row for row in filtered if row.score >= threshold),
            key=lambda row: (row.start, -row.score),
        )
        remaining = [row for row in filtered if row.score < threshold]
        eligible[tile_id] = early_good + remaining

    # Earliest quality-qualified jobs first.  This directly trades a bounded
    # amount of science against the avoidable-wait penalty.
    def priority(tile_id: str):
        rows = eligible[tile_id]
        regret = rows[0].score - (rows[1].score if len(rows) > 1 else 0.0)
        return (
            rows[0].start,
            deadlines.get(
                tile_id, datetime.max.replace(tzinfo=rows[0].start.tzinfo)
            ),
            -regret,
        )

    scheduled: list[Candidate] = []
    for tile_id in sorted(eligible, key=priority):
        row = next((item for item in eligible[tile_id] if fits(item, scheduled)), None)
        if row is None:
            raise RuntimeError(f"cannot place primary observation for {tile_id}")
        scheduled.append(row)

    # Coordinate improvement without delaying completion: a better candidate
    # may replace the current one only when it ends no later.
    changed = True
    while changed:
        changed = False
        by_tile = {row.tile_id: row for row in scheduled}
        for tile_id, current in sorted(by_tile.items()):
            others = [row for row in scheduled if row.tile_id != tile_id]
            better = next(
                (
                    row for row in eligible[tile_id]
                    if row.score > current.score + 1e-9
                    and row.end <= current.end
                    and fits(row, others)
                ),
                None,
            )
            if better is not None:
                scheduled.remove(current)
                scheduled.append(better)
                changed = True
    return scheduled


def select_primary_jobs_mip(
    scorer: ChallengeScorer,
    candidates: dict[str, list[Candidate]],
    top_k: int,
    request_tiles: dict[str, list[str]],
    joint_requests: bool,
    wait_weight: float,
    active_wait_discount: float,
    reference_waits: list[tuple[datetime, int, tuple[str, ...]]],
    primary_chain: bool,
    unlock_requests: list[str],
    unlock_tiles: list[tuple[str, str]],
    tile_deadlines: dict[str, datetime],
    filler_supports: list[tuple[str, str]],
    fixed_candidates: dict[str, Candidate],
) -> tuple[list[Candidate], dict[str, list[str]]]:
    """Jointly maximize primary science and choose request target subsets."""
    try:
        import pulp
    except ImportError as exc:
        raise RuntimeError(
            "--mip requires PuLP and HiGHS; use the project .optimizer-venv"
        ) from exc

    eligible: dict[str, list[Candidate]] = {}
    fixed_deadlines = earliest_deadlines(scorer, request_tiles)
    for tile_id, rows in candidates.items():
        if not joint_requests:
            deadline = fixed_deadlines.get(tile_id)
            filtered = [
                row for row in rows if deadline is None or row.start < deadline
            ]
            selected_rows = list(filtered[:top_k])
            for request_id in unlock_requests:
                request = scorer.requests[request_id]
                if tile_id in scorer.request_tiles[request_id]:
                    selected_rows.extend([
                        row
                        for row in filtered
                        if row.end <= request.available_from_utc
                    ][:top_k])
            for unlock_tile, request_id in unlock_tiles:
                if tile_id == unlock_tile:
                    request = scorer.requests[request_id]
                    selected_rows.extend([
                        row
                        for row in filtered
                        if row.end <= request.available_from_utc
                    ][:top_k])
            if tile_id in tile_deadlines:
                selected_rows.extend([
                    row
                    for row in filtered
                    if row.end <= tile_deadlines[tile_id]
                ][:top_k])
            if wait_weight:
                best = float("-inf")
                for row in sorted(filtered, key=lambda item: item.start):
                    if row.start != scorer.slots[row.slot_index].timestamp_utc:
                        continue
                    if row.score > best + 1e-9:
                        selected_rows.append(row)
                        best = row.score
            if tile_id in fixed_candidates:
                selected_rows.append(fixed_candidates[tile_id])
            eligible[tile_id] = list(dict.fromkeys(selected_rows))
            if not eligible[tile_id]:
                raise RuntimeError(f"no MIP candidate for {tile_id}")
            continue
        # Preserve the best global choices as well as enough choices before
        # every applicable request deadline.  A globally poor early exposure
        # may be necessary to earn a request without constraining another tile.
        selected_rows = list(rows[:top_k])
        for request in scorer.requests.values():
            if tile_id not in scorer.request_tiles[request.request_id]:
                continue
            selected_rows.extend([
                row
                for row in rows
                if row.start < request.deadline_utc
            ][:top_k])
        if tile_id in fixed_candidates:
            selected_rows.append(fixed_candidates[tile_id])
        eligible[tile_id] = list(dict.fromkeys(selected_rows))
        if not eligible[tile_id]:
            raise RuntimeError(f"no MIP candidate for {tile_id}")

    model = pulp.LpProblem("practice_primary_schedule", pulp.LpMaximize)
    variables = {
        (tile_id, index): model.add_variable(
            f"x_{tile_id}_{index}", cat="Binary"
        )
        for tile_id, rows in eligible.items()
        for index in range(len(rows))
    }
    model += pulp.lpSum(
        row.score * variables[(tile_id, index)]
        for tile_id, rows in eligible.items()
        for index, row in enumerate(rows)
    )
    for tile_id, rows in eligible.items():
        model += (
            pulp.lpSum(
                variables[(tile_id, index)] for index in range(len(rows))
            )
            == 1
        )
        if tile_id in fixed_candidates:
            model += variables[
                (tile_id, rows.index(fixed_candidates[tile_id]))
            ] == 1

    if primary_chain:
        starts: dict[datetime, list] = {}
        ends: dict[datetime, list] = {}
        slot_boundaries = {slot.timestamp_utc for slot in scorer.slots}
        filler_supported_starts: set[datetime] = set()
        for tile_id, request_id in filler_supports:
            request = scorer.requests[request_id]
            if (tile_id, request_id) in unlock_tiles:
                completed_by = request.available_from_utc
            elif tile_id in tile_deadlines:
                completed_by = tile_deadlines[tile_id]
            else:
                raise RuntimeError(
                    f"filler support {tile_id}:{request_id} needs "
                    "--unlock-tile or --tile-deadline"
                )
            for slot_index, slot in enumerate(scorer.slots):
                if not (
                    completed_by <= slot.timestamp_utc
                    and request.available_from_utc
                    <= slot.timestamp_utc
                    < request.deadline_utc
                ):
                    continue
                filler = evaluate_start(
                    scorer,
                    tile_id,
                    slot_index,
                    0,
                    require_midpoint_legal=False,
                )
                if filler is not None:
                    filler_supported_starts.add(filler.end)
        for tile_id, rows in eligible.items():
            for index, row in enumerate(rows):
                variable = variables[(tile_id, index)]
                starts.setdefault(row.start, []).append(variable)
                ends.setdefault(row.end, []).append(variable)
        for timestamp, start_variables in starts.items():
            if (
                timestamp in slot_boundaries
                or timestamp in filler_supported_starts
            ):
                continue
            model += (
                pulp.lpSum(start_variables)
                <= pulp.lpSum(ends.get(timestamp, []))
            )

    for request_id in unlock_requests:
        request = scorer.requests[request_id]
        for tile_id in scorer.request_tiles[request_id]:
            early = [
                variables[(tile_id, index)]
                for index, row in enumerate(eligible[tile_id])
                if row.end <= request.available_from_utc
            ]
            if not early:
                raise RuntimeError(
                    f"cannot unlock {request_id}/{tile_id} before availability"
                )
            model += pulp.lpSum(early) == 1
    for tile_id, request_id in unlock_tiles:
        request = scorer.requests[request_id]
        early = [
            variables[(tile_id, index)]
            for index, row in enumerate(eligible[tile_id])
            if row.end <= request.available_from_utc
        ]
        if not early:
            raise RuntimeError(
                f"cannot unlock {request_id}/{tile_id} before availability"
            )
        model += pulp.lpSum(early) == 1
    for tile_id, deadline in tile_deadlines.items():
        if tile_id not in eligible:
            raise RuntimeError(f"unknown tile deadline target: {tile_id}")
        early = [
            variables[(tile_id, index)]
            for index, row in enumerate(eligible[tile_id])
            if row.end <= deadline
        ]
        if not early:
            raise RuntimeError(
                f"cannot complete {tile_id} by {deadline.isoformat()}"
            )
        model += pulp.lpSum(early) == 1

    request_variables: dict[tuple[str, str], object] = {}
    if joint_requests:
        for request in scorer.requests.values():
            feasible_tiles = [
                tile_id
                for tile_id in scorer.request_tiles[request.request_id]
                if scorer._tile_has_request_opportunity(
                    scorer.tiles[tile_id],
                    request.available_from_utc,
                    request.deadline_utc,
                )
            ]
            if len(feasible_tiles) < request.required_tile_count:
                continue
            for tile_id in feasible_tiles:
                variable = model.add_variable(
                    f"r_{request.request_id}_{tile_id}", cat="Binary"
                )
                request_variables[(request.request_id, tile_id)] = variable
                before_deadline = [
                    variables[(tile_id, index)]
                    for index, row in enumerate(eligible[tile_id])
                    if row.start < request.deadline_utc
                ]
                model += variable <= pulp.lpSum(before_deadline)
            model += (
                pulp.lpSum(
                    request_variables[(request.request_id, tile_id)]
                    for tile_id in feasible_tiles
                )
                == request.required_tile_count
            )

    if wait_weight and reference_waits:
        wait_terms = []
        for wait_index, (timestamp, elapsed, actionable_tiles) in enumerate(
            reference_waits
        ):
            waiting = model.add_variable(f"ref_wait_{wait_index}", cat="Binary")
            busy = [
                variables[(tile_id, index)]
                for tile_id, rows in eligible.items()
                for index, row in enumerate(rows)
                if row.start <= timestamp < row.end
            ]
            for tile_id in actionable_tiles:
                unfinished = [
                    variables[(tile_id, index)]
                    for index, row in enumerate(eligible[tile_id])
                    if row.end > timestamp
                ]
                if unfinished:
                    model += (
                        waiting
                        >= pulp.lpSum(unfinished) - pulp.lpSum(busy)
                    )
            wait_terms.append(
                wait_weight
                * elapsed
                * float(scorer.config["penalties"]["avoidable_wait_per_second"])
                * waiting
            )
        model.objective -= pulp.lpSum(wait_terms)
    elif wait_weight:
        candidate_slots = {
            tile_id: {row.slot_index for row in candidates[tile_id]}
            for tile_id in candidates
        }
        wait_terms = []
        for slot_index, slot in enumerate(scorer.slots):
            active_request = any(
                request.available_from_utc <= slot.timestamp_utc < request.deadline_utc
                for request in scorer.requests.values()
            )
            discount = active_wait_discount if active_request else 1.0
            if not discount:
                continue
            actionable_tiles = [
                tile_id
                for tile_id in eligible
                if slot_index in candidate_slots[tile_id]
            ]
            if not actionable_tiles:
                continue
            waiting = model.add_variable(f"wait_{slot_index}", cat="Binary")
            for tile_id in actionable_tiles:
                later = [
                    variables[(tile_id, index)]
                    for index, row in enumerate(eligible[tile_id])
                    if row.start > slot.timestamp_utc
                ]
                if later:
                    model += waiting >= pulp.lpSum(later)
            wait_terms.append(
                wait_weight
                * discount
                * slot.duration_seconds
                * float(scorer.config["penalties"]["avoidable_wait_per_second"])
                * waiting
            )
        model.objective -= pulp.lpSum(wait_terms)

    occupied: dict[int, list] = {}
    for tile_id, rows in eligible.items():
        for index, row in enumerate(rows):
            start_tick = int(row.start.timestamp()) // 150
            end_tick = int(row.end.timestamp()) // 150
            variable = variables[(tile_id, index)]
            for tick in range(start_tick, end_tick):
                occupied.setdefault(tick, []).append(variable)
    for tick, tick_variables in occupied.items():
        if len(tick_variables) > 1:
            model += pulp.lpSum(tick_variables) <= 1, f"t_{tick}"

    solve_result = model.solve(
        pulp.HiGHS(msg=False, timeLimit=90, gapRel=0.00001)
    )
    print(
        f"MIP {solve_result.status_str}: objective={solve_result.objective} "
        f"bound={solve_result.best_bound} gap={solve_result.gap_rel}"
    )
    if not solve_result.has_solution:
        raise RuntimeError(f"MIP solver failed: {solve_result.status_str}")
    selected = []
    for tile_id, rows in eligible.items():
        chosen = [
            row
            for index, row in enumerate(rows)
            if pulp.value(variables[(tile_id, index)]) > 0.5
        ]
        if len(chosen) != 1:
            raise RuntimeError(f"MIP did not select one candidate for {tile_id}")
        selected.append(chosen[0])
    if joint_requests:
        request_tiles = {}
        for request in scorer.requests.values():
            request_tiles[request.request_id] = [
                tile_id
                for tile_id in scorer.request_tiles[request.request_id]
                if (request.request_id, tile_id) in request_variables
                and pulp.value(request_variables[(request.request_id, tile_id)]) > 0.5
            ]
    return selected, request_tiles


def penalty_aware_improvement(
    scorer: ChallengeScorer,
    all_candidates: dict[str, list[Candidate]],
    deadlines: dict[str, datetime],
    primaries: list[Candidate],
) -> list[Candidate]:
    """Coordinate-search each tile's science versus avoidable-wait frontier.

    At slot boundaries, waiting is penalized while any unfinished tile has a
    truth-safe exposure available.  For each tile, candidates dominated by an
    earlier start with at least as much science can never help; the remaining
    Pareto frontier is small enough for exact set-union deltas.
    """
    actionable = {
        tile_id: {row.slot_index for row in rows}
        for tile_id, rows in all_candidates.items()
    }
    frontiers: dict[str, list[Candidate]] = {}
    for tile_id, rows in all_candidates.items():
        deadline = deadlines.get(tile_id)
        chronological = sorted(
            (row for row in rows if deadline is None or row.start < deadline),
            key=lambda row: (row.slot_index, -row.score),
        )
        best = float("-inf")
        frontier = []
        for row in chronological:
            if row.score > best + 1e-9:
                frontier.append(row)
                best = row.score
        frontiers[tile_id] = frontier

    selected = {row.tile_id: row for row in primaries}
    wait_sets: dict[tuple[str, int], frozenset[int]] = {}

    def wait_set(row: Candidate) -> frozenset[int]:
        key = (row.tile_id, row.slot_index)
        if key not in wait_sets:
            wait_sets[key] = frozenset(
                index
                for index in actionable[row.tile_id]
                if index < row.slot_index
            )
        return wait_sets[key]

    seconds = float(scorer.slots[0].duration_seconds)
    penalty_per_slot = (
        seconds * float(scorer.config["penalties"]["avoidable_wait_per_second"])
    )
    for _ in range(12):
        changed = False
        for tile_id in sorted(selected):
            current = selected[tile_id]
            others = [row for key, row in selected.items() if key != tile_id]
            other_wait: set[int] = set()
            for row in others:
                other_wait.update(wait_set(row))

            def objective(row: Candidate) -> float:
                own_wait = wait_set(row)
                union_size = len(other_wait) + len(own_wait - other_wait)
                return row.score - penalty_per_slot * union_size

            current_value = objective(current)
            best_row = current
            best_value = current_value
            for row in frontiers[tile_id]:
                if not fits(row, others):
                    continue
                value = objective(row)
                if value > best_value + 1e-9:
                    best_row, best_value = row, value
            if best_row != current:
                selected[tile_id] = best_row
                changed = True
        if not changed:
            break
    return list(selected.values())


def add_request_jobs(
    scorer: ChallengeScorer,
    all_candidates: dict[str, list[Candidate]],
    primaries: list[Candidate],
    request_tiles: dict[str, list[str]],
) -> list[Candidate]:
    scheduled = list(primaries)
    primary_by_tile = {row.tile_id: row for row in primaries}
    primary_tagged: set[str] = set()
    for request in sorted(scorer.requests.values(), key=lambda item: item.available_from_utc):
        for tile_id in request_tiles[request.request_id]:
            primary = primary_by_tile[tile_id]
            if (
                tile_id not in primary_tagged
                and request.available_from_utc <= primary.start < request.deadline_utc
            ):
                tagged = replace(primary, request_id=request.request_id)
                scheduled.remove(primary)
                scheduled.append(tagged)
                primary_by_tile[tile_id] = tagged
                primary_tagged.add(tile_id)
                continue

            # A request-tagged revisit is valid only after ordinary completion;
            # choose the earliest non-conflicting truth-safe opportunity.
            eligible = sorted(
                (
                    row for row in all_candidates[tile_id]
                    if row.start >= max(request.available_from_utc, primary.end)
                    and row.start < request.deadline_utc
                    # A decisions.csv row only identifies its slot; a non-zero
                    # offset is reproducible here only when another scheduled
                    # action forms a continuous chain into it.
                    and row.start
                    == scorer.slots[row.slot_index].timestamp_utc
                ),
                key=lambda row: row.start,
            )
            revisit = next((row for row in eligible if fits(row, scheduled)), None)
            if revisit is None:
                raise RuntimeError(
                    f"cannot place request visit {request.request_id}/{tile_id}"
                )
            scheduled.append(replace(revisit, request_id=request.request_id))
    return sorted(scheduled, key=lambda row: (row.start, row.end, row.tile_id))


def add_science_upgrades(
    scorer: ChallengeScorer,
    all_candidates: dict[str, list[Candidate]],
    schedule: list[Candidate],
) -> list[Candidate]:
    """Greedily add legal repeats that improve a tile's banked best score.

    Anomaly-era scoring keeps the best completed observation of each tile,
    rather than rejecting ordinary repeats.  Request deadlines can force an
    early, lower-quality completion, so a later repeat can recover science
    while also consuming time that might otherwise be avoidable waiting.
    """

    fixed = sorted(schedule, key=lambda row: (row.start, row.end, row.tile_id))

    def overlaps(rows: list[Candidate], candidate: Candidate) -> bool:
        return any(
            row.start < candidate.end and candidate.start < row.end
            for row in rows
        )

    choices: list[tuple[float, Candidate]] = []
    for tile_id, rows in all_candidates.items():
        tile_schedule = [row for row in fixed if row.tile_id == tile_id]
        if not tile_schedule:
            continue
        factor = scorer._anomaly_factor(tile_id)
        for candidate in rows:
            if overlaps(fixed, candidate):
                continue
            banked = max(
                (
                    row.score
                    for row in tile_schedule
                    if row.end <= candidate.start
                ),
                default=-1.0,
            )
            gain = (candidate.score - banked) * factor
            if gain > 1e-6:
                choices.append((gain, candidate))

    chosen: list[Candidate] = []
    chosen_tiles: set[str] = set()
    for _gain, candidate in sorted(
        choices, key=lambda item: (-item[0], item[1].start, item[1].tile_id)
    ):
        if candidate.tile_id in chosen_tiles or overlaps(chosen, candidate):
            continue
        chosen.append(candidate)
        chosen_tiles.add(candidate.tile_id)
    return sorted(
        [*fixed, *chosen], key=lambda row: (row.start, row.end, row.tile_id)
    )


def reselect_science_jobs(
    scorer: ChallengeScorer,
    all_candidates: dict[str, list[Candidate]],
    schedule: list[Candidate],
    top_k: int,
) -> list[Candidate]:
    """Select final science observations around fixed request-tagged visits.

    A request visit itself completes and scores its tile.  Treating that visit
    as a fixed operational job lets the MIP move the ordinary observation—or
    omit it when the request result is already best—instead of unnecessarily
    pinning request-constrained tiles to their early completion.
    """
    try:
        import pulp
    except ImportError as exc:
        raise RuntimeError(
            "--mip requires PuLP and HiGHS; use the project .optimizer-venv"
        ) from exc

    mandatory = sorted(
        (row for row in schedule if row.request_id),
        key=lambda row: (row.start, row.end, row.tile_id),
    )

    def conflicts_mandatory(candidate: Candidate) -> bool:
        return any(
            row.start < candidate.end and candidate.start < row.end
            for row in mandatory
        )

    banked = {
        tile_id: max(
            (row.score for row in mandatory if row.tile_id == tile_id),
            default=0.0,
        )
        for tile_id in scorer.tiles
    }
    eligible: dict[str, list[Candidate]] = {}
    for tile_id, rows in all_candidates.items():
        available = [
            row
            for row in rows
            if not conflicts_mandatory(row)
            and (banked[tile_id] == 0.0 or row.score > banked[tile_id] + 1e-9)
        ][: max(1, top_k)]
        if not available and banked[tile_id] == 0.0:
            raise RuntimeError(f"no science candidate for {tile_id}")
        eligible[tile_id] = available

    model = pulp.LpProblem("practice_science_reselection", pulp.LpMaximize)
    variables = {
        (tile_id, index): model.add_variable(
            f"u_{tile_id}_{index}", cat="Binary"
        )
        for tile_id, rows in eligible.items()
        for index in range(len(rows))
    }
    model += pulp.lpSum(
        (row.score - banked[tile_id])
        * scorer._anomaly_factor(tile_id)
        * variables[(tile_id, index)]
        for tile_id, rows in eligible.items()
        for index, row in enumerate(rows)
    )
    for tile_id, rows in eligible.items():
        selected = pulp.lpSum(
            variables[(tile_id, index)] for index in range(len(rows))
        )
        model += selected <= 1 if banked[tile_id] else selected == 1

    occupied: dict[int, list] = {}
    for tile_id, rows in eligible.items():
        for index, row in enumerate(rows):
            variable = variables[(tile_id, index)]
            for tick in range(
                int(row.start.timestamp()) // 150,
                int(row.end.timestamp()) // 150,
            ):
                occupied.setdefault(tick, []).append(variable)
    for tick, tick_variables in occupied.items():
        if len(tick_variables) > 1:
            model += pulp.lpSum(tick_variables) <= 1, f"u_t_{tick}"

    solve_result = model.solve(
        pulp.HiGHS(msg=False, timeLimit=90, gapRel=0.00001)
    )
    print(
        f"upgrade MIP {solve_result.status_str}: "
        f"objective={solve_result.objective} "
        f"bound={solve_result.best_bound} gap={solve_result.gap_rel}"
    )
    if not solve_result.has_solution:
        raise RuntimeError(
            f"science reselection failed: {solve_result.status_str}"
        )

    selected = [
        row
        for tile_id, rows in eligible.items()
        for index, row in enumerate(rows)
        if pulp.value(variables[(tile_id, index)]) > 0.5
    ]
    return sorted(
        [*mandatory, *selected],
        key=lambda row: (row.start, row.end, row.tile_id),
    )


def add_request_fillers(
    scorer: ChallengeScorer,
    all_candidates: dict[str, list[Candidate]],
    primaries: list[Candidate],
    schedule: list[Candidate],
) -> list[Candidate]:
    """Fill otherwise idle practice time with legal request-tagged revisits.

    Practice repeat observations are operationally valid when they name an
    active request.  They add no duplicate science, but they consume telescope
    time instead of an avoidable wait.  This follows the published scorer
    exactly and is useful only for public results-file optimization.
    """
    fixed = sorted(schedule, key=lambda row: (row.start, row.end, row.tile_id))
    primary_by_tile = {
        tile_id: min(
            (row for row in fixed if row.tile_id == tile_id),
            key=lambda row: row.end,
        )
        for tile_id in scorer.tiles
    }
    last_primary_end = max(row.end for row in fixed)
    fillers: list[Candidate] = []
    requests = sorted(scorer.requests.values(), key=lambda item: item.available_from_utc)
    slot_starts = [slot.timestamp_utc for slot in scorer.slots]
    fixed_starts = [row.start for row in fixed]
    primary_end = {row.tile_id: row.end for row in primaries}
    sys.setrecursionlimit(max(10000, len(scorer.slots) * 4))

    def state(current: datetime):
        """Normalize a cursor and return its slot plus next fixed action."""
        fixed_index = bisect.bisect_right(fixed_starts, current) - 1
        if fixed_index >= 0 and current < fixed[fixed_index].end:
            return ("fixed", fixed[fixed_index].end, None, None)
        fixed_index += 1
        next_fixed = (
            fixed[fixed_index].start
            if fixed_index < len(fixed)
            else last_primary_end
        )
        index = bisect.bisect_right(slot_starts, current) - 1
        if (
            index < 0
            or current >= scorer.slots[index].end_utc
            or scorer.slots[index].timestamp_utc > current
        ):
            index = bisect.bisect_left(slot_starts, current)
            if index >= len(scorer.slots):
                return ("done", last_primary_end, None, None)
            return ("advance", scorer.slots[index].timestamp_utc, None, None)
        return ("open", current, index, next_fixed)

    def options_at(current: datetime, index: int, next_fixed: datetime):
        slot = scorer.slots[index]
        offset = int((current - slot.timestamp_utc).total_seconds())
        active = [
            request for request in requests
            if request.available_from_utc <= current < request.deadline_utc
        ]
        options: list[Candidate] = []
        for request in active:
            for tile_id in scorer.request_tiles[request.request_id]:
                primary = primary_by_tile[tile_id]
                if primary.end > current:
                    continue
                row = evaluate_start(
                    scorer,
                    tile_id,
                    index,
                    offset,
                    require_midpoint_legal=False,
                )
                if row is None:
                    row = evaluate_weather_interruption(
                        scorer, tile_id, index, offset
                    )
                    if row is None:
                        continue
                tagged = replace(row, request_id=request.request_id)
                if tagged.end <= next_fixed:
                    options.append(tagged)
        # An unfinished tile may also be used as a zero-credit weather probe.
        # If weather closes mid-exposure the scorer consumes the safe prefix,
        # leaves the tile unfinished, and applies no invalid-action penalty.
        for tile_id, primary in primary_by_tile.items():
            if primary.end <= current:
                continue
            interrupted = evaluate_weather_interruption(
                scorer, tile_id, index, offset
            )
            if interrupted is not None and interrupted.end <= next_fixed:
                options.append(interrupted)
        return options

    @lru_cache(maxsize=None)
    def actionable(current: datetime, index: int) -> bool:
        slot = scorer.slots[index]
        offset = int((current - slot.timestamp_utc).total_seconds())
        for tile_id, completed_at in primary_end.items():
            if completed_at <= current:
                continue
            # Match ChallengeScorer._has_actionable_tile exactly.  Candidate
            # caches may use lax start-only geometry and therefore overstate
            # whether waiting at a slot boundary is penalized.
            if scorer._can_complete_from(
                scorer.tiles[tile_id], index, offset
            ):
                return True
        return False

    @lru_cache(maxsize=None)
    def value(current: datetime) -> tuple[int, int, float]:
        if current >= last_primary_end:
            return (0, 0, 0.0)
        kind, normalized, index, next_fixed = state(current)
        if kind in {"fixed", "advance"}:
            return value(normalized)
        if kind == "done":
            return (0, 0, 0.0)
        slot = scorer.slots[index]
        # The replay cursor may wait to a fixed action that starts inside this
        # slot.  Older versions prohibited that transition and forced a filler
        # chain, even when paying the short implicit wait was globally better.
        # Model the exact reachable boundary used by the authoritative scorer.
        wait_until = min(slot.end_utc, next_fixed)
        downstream = value(wait_until)
        wait_seconds = (
            int((wait_until - current).total_seconds())
            if actionable(current, index)
            else 0
        )
        wait_value = (
            downstream[0] - wait_seconds,
            downstream[1],
            downstream[2],
        )
        best = wait_value
        for row in options_at(current, index, next_fixed):
            downstream = value(row.end)
            candidate_value = (
                downstream[0],
                int((row.end - row.start).total_seconds()) + downstream[1],
                row.score + downstream[2],
            )
            if candidate_value > best:
                best = candidate_value
        return best

    current = slot_starts[0]
    while current < last_primary_end:
        kind, normalized, index, next_fixed = state(current)
        if kind in {"fixed", "advance"}:
            current = normalized
            continue
        if kind == "done":
            break
        slot = scorer.slots[index]
        best = value(current)
        chosen = None
        for row in options_at(current, index, next_fixed):
            downstream = value(row.end)
            candidate_value = (
                downstream[0],
                int((row.end - row.start).total_seconds()) + downstream[1],
                row.score + downstream[2],
            )
            if candidate_value == best:
                chosen = row
                break
        if chosen is not None:
            fillers.append(chosen)
            current = chosen.end
        else:
            current = min(slot.end_utc, next_fixed)
    return sorted(
        [*fixed, *fillers], key=lambda row: (row.start, row.end, row.tile_id)
    )


def write_decisions(path: Path, scorer: ChallengeScorer, schedule: list[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for number, row in enumerate(schedule, 1):
            writer.writerow(
                {
                    "decision_id": f"D{number:06d}",
                    "slot_id": scorer.slots[row.slot_index].slot_id,
                    "action": "observe",
                    "tile_id": row.tile_id,
                    "program": row.program,
                    "request_id": row.request_id,
                    "reason": "offline public-weather optimizer",
                }
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", type=Path, default=Path("scenarios/dev-reference")
    )
    parser.add_argument("--out", type=Path, default=Path("optimized_decisions.csv"))
    parser.add_argument("--top-k", type=int, default=500)
    parser.add_argument(
        "--offset-step",
        type=int,
        default=0,
        help="also enumerate within-slot starts at this many seconds",
    )
    parser.add_argument(
        "--no-candidate-cache",
        action="store_true",
        help="recompute exposure candidates instead of using the scenario cache",
    )
    parser.add_argument(
        "--lax-candidates",
        action="store_true",
        help="enumerate candidates using the replay engine's start-only geometry check",
    )
    parser.add_argument("--quality-fraction", type=float, default=0.9)
    parser.add_argument(
        "--horizon-days",
        type=float,
        default=None,
        help="choose each tile's best exposure within this many days of availability",
    )
    parser.add_argument(
        "--no-penalty-search",
        action="store_true",
        help="disable per-tile science versus avoidable-wait local search",
    )
    parser.add_argument(
        "--no-request-fillers",
        action="store_true",
        help="disable legal active-request revisit fillers",
    )
    parser.add_argument(
        "--mip",
        action="store_true",
        help="use PuLP/HiGHS to maximize non-overlapping primary science",
    )
    parser.add_argument(
        "--joint-requests",
        action="store_true",
        help="let the MIP choose request target subsets",
    )
    parser.add_argument(
        "--mip-wait-weight",
        type=float,
        default=0.0,
        help="weight of modeled avoidable waits in the MIP objective",
    )
    parser.add_argument(
        "--active-wait-discount",
        type=float,
        default=0.0,
        help="relative wait weight while a request filler can be active",
    )
    parser.add_argument(
        "--wait-reference",
        type=Path,
        default=None,
        help="decisions CSV whose exact wait blocker sets guide the MIP",
    )
    parser.add_argument(
        "--max-wait-blockers",
        type=int,
        default=None,
        help="model only reference waits with at most this many blockers",
    )
    parser.add_argument(
        "--wait-tick-seconds",
        type=int,
        default=0,
        help="split each modeled reference wait into fixed-duration ticks",
    )
    parser.add_argument(
        "--primary-chain",
        action="store_true",
        help="require every within-slot primary to continue another primary",
    )
    parser.add_argument(
        "--unlock-request",
        action="append",
        default=[],
        help="complete every tile of this request before it becomes active",
    )
    parser.add_argument(
        "--unlock-tile",
        action="append",
        default=[],
        metavar="TILE:REQUEST",
        help="complete one tile before the named request becomes active",
    )
    parser.add_argument(
        "--tile-deadline",
        action="append",
        default=[],
        metavar="TILE:UTC",
        help="require a tile's primary exposure to finish by an ISO UTC time",
    )
    parser.add_argument(
        "--filler-support",
        action="append",
        default=[],
        metavar="TILE:REQUEST",
        help="allow a guaranteed-complete request tile to support offset starts",
    )
    parser.add_argument(
        "--fix-report",
        type=Path,
        default=None,
        help="fix primaries to a score report except tiles named by --free-tile",
    )
    parser.add_argument(
        "--free-tile",
        action="append",
        default=[],
        help="tile allowed to move when --fix-report is used",
    )
    args = parser.parse_args(argv)

    scenario = args.scenario.resolve()
    unlock_tiles = [
        tuple(value.split(":", 1))
        for value in args.unlock_tile
    ]
    tile_deadlines = {
        tile_id: datetime.fromisoformat(value.replace("Z", "+00:00"))
        for tile_id, value in (
            item.split(":", 1) for item in args.tile_deadline
        )
    }
    filler_supports = [
        tuple(value.split(":", 1))
        for value in args.filler_support
    ]
    scorer = ChallengeScorer.from_files(scenario)
    fixed_candidates: dict[str, Candidate] = {}
    if args.fix_report is not None:
        report = json.loads(args.fix_report.read_text(encoding="utf-8"))
        slot_starts = [slot.timestamp_utc for slot in scorer.slots]
        free_tiles = set(args.free_tile)
        for action in report["actions"]:
            tile_id = action.get("tile_id")
            if (
                not tile_id
                or tile_id in free_tiles
                or action.get("base_science_score", 0.0) <= 0.0
            ):
                continue
            start = datetime.fromisoformat(
                action["start_utc"].replace("Z", "+00:00")
            )
            slot_index = bisect.bisect_right(slot_starts, start) - 1
            offset = int(
                (start - slot_starts[slot_index]).total_seconds()
            )
            candidate = evaluate_start(
                scorer,
                tile_id,
                slot_index,
                offset,
                require_midpoint_legal=not args.lax_candidates,
            )
            if candidate is None:
                raise RuntimeError(
                    f"cannot reconstruct fixed primary {tile_id} at {start}"
                )
            fixed_candidates[tile_id] = candidate
    cache_path = scenario / (
        f".optimizer_candidates_{args.offset_step or 0}"
        f"{'_lax' if args.lax_candidates else ''}.pickle"
    )
    if cache_path.exists() and not args.no_candidate_cache:
        with cache_path.open("rb") as handle:
            all_candidates: dict[str, list[Candidate]] = pickle.load(handle)
    else:
        all_candidates = {}
        for tile_id in sorted(scorer.tiles):
            rows = [
                candidate
                for index in range(len(scorer.slots))
                for offset in (
                    range(0, scorer.slots[index].duration_seconds, args.offset_step)
                    if args.offset_step
                    else (0,)
                )
                if (
                    candidate := evaluate_start(
                        scorer,
                        tile_id,
                        index,
                        offset,
                        require_midpoint_legal=not args.lax_candidates,
                    )
                )
                is not None
            ]
            rows.sort(key=lambda row: (-row.score, row.start))
            if not rows:
                raise RuntimeError(f"no valid exposure for {tile_id}")
            all_candidates[tile_id] = rows
        with cache_path.open("wb") as handle:
            pickle.dump(all_candidates, handle, protocol=pickle.HIGHEST_PROTOCOL)

    request_tiles = choose_request_tiles(scorer, all_candidates)
    deadlines = earliest_deadlines(scorer, request_tiles)
    reference_waits = (
        collect_reference_waits(scenario, args.wait_reference.resolve())
        if args.wait_reference is not None
        else []
    )
    if args.max_wait_blockers is not None:
        reference_waits = [
            wait
            for wait in reference_waits
            if len(wait[2]) <= args.max_wait_blockers
        ]
    if args.wait_tick_seconds:
        reference_waits = [
            (
                timestamp + timedelta(seconds=offset),
                min(args.wait_tick_seconds, elapsed - offset),
                blockers,
            )
            for timestamp, elapsed, blockers in reference_waits
            for offset in range(0, elapsed, args.wait_tick_seconds)
        ]
    if args.horizon_days is not None:
        for tile_id, rows in all_candidates.items():
            first_observable = min(row.start for row in rows)
            horizon = first_observable + timedelta(days=args.horizon_days)
            deadlines[tile_id] = min(deadlines.get(tile_id, horizon), horizon)
    primary_candidates = {
        tile_id: rows[: max(1, args.top_k)]
        for tile_id, rows in all_candidates.items()
    }
    if not 0.0 < args.quality_fraction <= 1.0:
        raise SystemExit("--quality-fraction must be in (0, 1]")
    if args.mip:
        primaries, request_tiles = select_primary_jobs_mip(
            scorer,
            all_candidates,
            args.top_k,
            request_tiles,
            args.joint_requests,
            args.mip_wait_weight,
            args.active_wait_discount,
            reference_waits,
            args.primary_chain,
            args.unlock_request,
            unlock_tiles,
            tile_deadlines,
            filler_supports,
            fixed_candidates,
        )
        deadlines = earliest_deadlines(scorer, request_tiles)
    else:
        primaries = select_primary_jobs(
            scorer, primary_candidates, deadlines, args.quality_fraction
        )
    if not args.no_penalty_search:
        primaries = penalty_aware_improvement(
            scorer, all_candidates, deadlines, primaries
        )
    schedule = add_request_jobs(scorer, all_candidates, primaries, request_tiles)
    if scorer.mechanics:
        schedule = reselect_science_jobs(
            scorer, all_candidates, schedule, args.top_k
        )
        schedule = add_science_upgrades(scorer, all_candidates, schedule)
    if not args.no_request_fillers:
        schedule = add_request_fillers(
            scorer, all_candidates, primaries, schedule
        )
    write_decisions(args.out, scorer, schedule)
    report = score_files(
        scenario,
        args.out,
        args.out.with_name(f"{args.out.stem}_score_report.json"),
    )
    print(
        json.dumps(
            {
                "out": str(args.out.resolve()),
                "actions": len(schedule),
                "score": report["score"],
                "completion": report["completion"],
                "requests": report["requests"],
                "wait_seconds": report["wait_seconds"],
                "termination_reason": report["termination_reason"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
