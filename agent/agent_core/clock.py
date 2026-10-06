"""Time budget on the platform's fair clock.

Each card has a budget of 900 *normalized CPU seconds*. Only the CPU time the agent uses
inside its own turns is charged, divided by the machine's `speed_factor`; waiting for a
model, the network or the engine is free. A real-time cap (30 minutes) ends hung runs.
Every `decision_request` carries `payload.wallclock` with, among others:

    remaining_seconds            budget left, normalized seconds
    remaining_real_cpu_seconds   the same budget in real CPU seconds of THIS machine
    wall_remaining_seconds       real time left before the 30-minute cap

Pace compute on `remaining_real_cpu_seconds` and measure your own work with process CPU
time (`time.process_time()`), so both numbers are in the same unit. A wall clock
(`time.monotonic()`) would also count waiting and other processes, and comparing it with
the normalized `remaining_seconds` makes an agent too timid on slow machines and too
greedy on fast ones.
"""
from __future__ import annotations

import time
from collections import deque
import statistics

# Leave a fifth of the real time for the engine, model waits and safety: on a slow
# machine (speed_factor 2) the CPU budget alone would fill the whole 30-minute cap.
WALL_SHARE = 0.8


class Clock:
    def __init__(self) -> None:
        self.cpu_left = float("inf")   # real CPU seconds of this machine
        self.wall_left = float("inf")  # real seconds before the hard cap
        self._started = None
        self.last_cost = 0.0           # CPU seconds of the last decision
        self.avg_cost = 0.0            # smoothed CPU seconds per decision
        self.level = 0
        self.level_cost = [0.06, 0.02, 0.008]
        self.level_seen = [False] * 3
        self.search_cost = self.level_cost[:]
        self._search_samples = [deque(maxlen=64) for _ in range(3)]
        self.last_search_cost = 0.0
        self._overheads = deque(maxlen=64)
        self.advances = deque(maxlen=64)
        self._last_now = None
        self._ended_wall = None
        self.platform_cost = 0.0
        self.wall_cost = 0.0
        self.last_wall_cost = 0.0
        self._started_wall = None

    def update(self, wallclock: dict) -> None:
        """Read the clock fields of one decision_request. Older local runners only send
        `remaining_seconds` (then real time), so it is the fallback for both."""
        wallclock = wallclock or {}
        fallback = wallclock.get("remaining_seconds")
        cpu = wallclock.get("remaining_real_cpu_seconds", fallback)
        wall = wallclock.get("wall_remaining_seconds", fallback)
        if cpu is not None:
            self.cpu_left = float(cpu)
        if wall is not None:
            self.wall_left = float(wall)

    def compute_left(self) -> float:
        """Real CPU seconds this agent may still spend thinking."""
        return min(self.cpu_left, WALL_SHARE * max(0.0, self.wall_left - 120.0))

    def observe_progress(self, now, nights):
        if self._last_now is not None:
            advance = sum(max(0.0, (min(end, now) - max(start, self._last_now)).total_seconds())
                          for start, end in nights if end > self._last_now and start < now)
            # A long daytime wait can cross an entire night; it is not a typical
            # exposure and must not inflate the expected progress of one turn.
            if 0 < advance <= 3600:
                self.advances.append(advance)
        self._last_now = now
        if self._ended_wall is not None and self._started_wall is not None:
            gap = max(0.0, self._started_wall - self._ended_wall)
            self.platform_cost = 0.9 * self.platform_cost + 0.1 * gap

    def decisions_left(self, night_seconds):
        advance = sum(self.advances) / len(self.advances) if self.advances else 700.0
        return max(1.0, night_seconds / max(60.0, advance))

    # Own cost, in process CPU seconds (all threads), around each decision.
    def start_decision(self) -> None:
        self._started = time.process_time()
        self._started_wall = time.monotonic()

    def end_decision(self, model_wait=0.0) -> None:
        if self._started is None:
            return
        self.last_cost = time.process_time() - self._started
        self.avg_cost = self.last_cost if self.avg_cost == 0.0 else 0.9 * self.avg_cost + 0.1 * self.last_cost
        level = self.level
        self._overheads.append(max(0.0, self.last_cost-self.last_search_cost))
        overhead = statistics.median(self._overheads)
        # Search size varies between fields. A short burst of expensive fields
        # must not predict that every remaining night will have the same cost.
        # The per-level median also resists coarse Windows CPU timer samples;
        # sustained increases still replace the bounded window promptly.
        self._search_samples[level].append(self.last_search_cost)
        self.search_cost[level] = max(.001, statistics.median(self._search_samples[level]))
        self.level_cost = [cost+overhead for cost in self.search_cost]
        self.level_seen[level] = True
        self._ended_wall = time.monotonic()
        self.last_wall_cost = self._ended_wall - self._started_wall
        wall = max(self.last_cost, self.last_wall_cost - model_wait)
        self.wall_cost = wall if not self.wall_cost else 0.9 * self.wall_cost + 0.1 * wall
        self._started = None
