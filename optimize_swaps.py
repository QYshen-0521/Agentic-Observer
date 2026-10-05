#!/usr/bin/env python3
"""Improve a legal practice trace by swapping equal-duration observations."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pulp

from challenge.contracts import DECISION_COLUMNS
from challenge.scoring_core import ChallengeScorer, score_files
from optimize_practice import collect_reference_waits, evaluate_start


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Move:
    tile_id: str
    early_decision_id: str
    completion: datetime
    science: float
    program: str


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-science-loss", type=float, default=350.0)
    args = parser.parse_args()

    scenario = args.scenario.resolve()
    scorer = ChallengeScorer.from_files(scenario)
    report = json.loads(args.report.read_text())
    with args.decisions.open(newline="", encoding="utf-8") as handle:
        decision_rows = list(csv.DictReader(handle))
    decision_by_id = {row["decision_id"]: row for row in decision_rows}

    action_by_id = {
        action["decision_id"]: action
        for action in report["actions"]
        if action["action"] == "observe"
    }
    primary_by_tile = {}
    completed_at = {}
    for action in report["actions"]:
        if action["outcome"] != "completed":
            continue
        tile_id = action["tile_id"]
        if tile_id and tile_id not in primary_by_tile:
            primary_by_tile[tile_id] = action
            completed_at[tile_id] = parse_utc(action["start_utc"])
    primary_ids = {
        action["decision_id"] for action in primary_by_tile.values()
    }

    def candidate_at(tile_id: str, action: dict):
        start = parse_utc(action["start_utc"])
        slot_index = scorer.slot_indices[action["slot_id"]]
        offset = int(
            (start - scorer.slots[slot_index].timestamp_utc).total_seconds()
        )
        return evaluate_start(scorer, tile_id, slot_index, offset)

    replacements = {}
    for tile_id, primary in primary_by_tile.items():
        original_row = decision_by_id[primary["decision_id"]]
        original_candidate = candidate_at(tile_id, primary)
        if original_candidate is None:
            continue
        if original_row["request_id"]:
            replacements[tile_id] = (
                tile_id,
                original_candidate.program,
                original_row["request_id"],
            )
            continue
        start = parse_utc(primary["start_utc"])
        duration = int(primary["elapsed_seconds"])
        choices = []
        for request in scorer.requests.values():
            if not request.available_from_utc <= start < request.deadline_utc:
                continue
            for repeat_tile in scorer.request_tiles[request.request_id]:
                if completed_at.get(repeat_tile, start) >= start:
                    continue
                candidate = candidate_at(repeat_tile, primary)
                if (
                    candidate is not None
                    and int((candidate.end - candidate.start).total_seconds())
                    == duration
                ):
                    choices.append(
                        (repeat_tile, candidate.program, request.request_id)
                    )
        if choices:
            replacements[tile_id] = choices[0]

    moves: dict[str, list[Move]] = {}
    move_by_key = {}
    for tile_id, primary in primary_by_tile.items():
        if tile_id not in replacements:
            continue
        original_start = parse_utc(primary["start_utc"])
        original_end = original_start + (
            parse_utc(primary["segments"][-1]["start_utc"])
            - parse_utc(primary["segments"][-1]["start_utc"])
        )
        original_end = original_start
        original_science = (
            float(primary["base_science_score"])
            + float(primary["program_bonus_score"])
        )
        duration = int(primary["elapsed_seconds"])
        original_completion = original_start
        rows = [
            Move(tile_id, "", original_completion, original_science, primary["program"])
        ]
        frontier = []
        for action in report["actions"]:
            if (
                action["outcome"] != "completed"
                or action["decision_id"] in primary_ids
                or int(action["elapsed_seconds"]) != duration
            ):
                continue
            start = parse_utc(action["start_utc"])
            if start >= original_start:
                continue
            candidate = candidate_at(tile_id, action)
            if candidate is None:
                continue
            science = candidate.score
            if original_science - science > args.max_science_loss:
                continue
            frontier.append(
                Move(tile_id, action["decision_id"], candidate.end, science, candidate.program)
            )
        best_science = float("-inf")
        for move in sorted(frontier, key=lambda item: item.completion):
            if move.science > best_science + 1e-9:
                rows.append(move)
                best_science = move.science
        moves[tile_id] = rows
        for index, move in enumerate(rows):
            move_by_key[(tile_id, index)] = move

    model = pulp.LpProblem("equal_duration_swaps", pulp.LpMaximize)
    variables = {
        key: model.add_variable(
            f"m_{key[0]}_{key[1]}", cat="Binary"
        )
        for key in move_by_key
    }
    for tile_id, rows in moves.items():
        model += pulp.lpSum(
            variables[(tile_id, index)] for index in range(len(rows))
        ) == 1
    early_users = {}
    for key, move in move_by_key.items():
        if move.early_decision_id:
            early_users.setdefault(move.early_decision_id, []).append(variables[key])
    for decision_id, used in early_users.items():
        model += pulp.lpSum(used) <= 1, f"early_{decision_id}"

    science_terms = [
        move.science * variables[key]
        for key, move in move_by_key.items()
    ]
    wait_terms = []
    waits = collect_reference_waits(scenario, args.decisions)
    for wait_index, (timestamp, elapsed, blockers) in enumerate(waits):
        relevant = [tile_id for tile_id in blockers if tile_id in moves]
        if len(relevant) != len(blockers):
            continue
        waiting = model.add_variable(f"wait_{wait_index}", cat="Binary")
        for tile_id in relevant:
            unfinished = [
                variables[(tile_id, index)]
                for index, move in enumerate(moves[tile_id])
                if move.completion > timestamp
            ]
            if unfinished:
                model += waiting >= pulp.lpSum(unfinished)
        wait_terms.append(0.001 * elapsed * waiting)
    model += pulp.lpSum(science_terms) - pulp.lpSum(wait_terms)

    result = model.solve(pulp.HiGHS(msg=False, timeLimit=120, gapRel=0.00001))
    if not result.has_solution:
        raise RuntimeError(result.status_str)

    output_rows = [dict(row) for row in decision_rows]
    output_by_id = {row["decision_id"]: row for row in output_rows}
    selected = []
    for tile_id, rows in moves.items():
        index = next(
            index
            for index in range(len(rows))
            if pulp.value(variables[(tile_id, index)]) > 0.5
        )
        move = rows[index]
        if not move.early_decision_id:
            continue
        selected.append(move)
        early = output_by_id[move.early_decision_id]
        early.update(
            tile_id=tile_id,
            program=move.program,
            request_id="",
            reason="earlier equal-duration primary",
        )
        old = output_by_id[primary_by_tile[tile_id]["decision_id"]]
        repeat_tile, program, request_id = replacements[tile_id]
        old.update(
            tile_id=repeat_tile,
            program=program,
            request_id=request_id,
            reason="equal-duration request repeat",
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output_rows)
    scored = score_files(
        scenario,
        args.out,
        args.out.with_name(f"{args.out.stem}_score_report.json"),
    )
    print(
        json.dumps(
            {
                "status": result.status_str,
                "selected_swaps": len(selected),
                "score": scored["score"],
                "out": str(args.out.resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
