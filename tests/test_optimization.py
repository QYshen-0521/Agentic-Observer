"""Budget, scheduling and realized-yield regressions for the observing planner."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from test_v4_agent import initial, request
from agent_core.clock import Clock
from agent_core.geometry import parse_utc
from agent_core.planner import Planner
from agent_core.state import SurveyState, PendingPrediction


class PacingTests(unittest.TestCase):
    def test_recovery_needs_eight_healthy_turns(self):
        p = Planner(SurveyState(initial()))
        p.state.fast_level = 2
        p.clock.cpu_left = 900
        p.clock.wall_left = 1800
        p.clock.level_cost = [0.01, 0.005, 0.002]
        now = parse_utc(request()['now_utc'])
        for _ in range(7):
            p._pace(now)
            self.assertEqual(p.state.fast_level, 2)
        p._pace(now)
        self.assertEqual(p.state.fast_level, 0)

    def test_platform_overhead_and_reserve_force_immediate_downgrade(self):
        p = Planner(SurveyState(initial()))
        p.clock.cpu_left = 900
        p.clock.wall_left = 121
        p.clock.platform_cost = 0.5
        p._pace(parse_utc(request()['now_utc']))
        self.assertEqual(p.state.fast_level, 2)
        self.assertLessEqual(p.clock.compute_left(), 0.8)

    def test_night_progress_excludes_daytime_and_uses_last_64_turns(self):
        c = Clock()
        now = parse_utc(request()['now_utc'])
        nights = [(now, now+timedelta(hours=3)), (now+timedelta(days=1), now+timedelta(days=1,hours=3))]
        c.observe_progress(now, nights)
        for n in range(1, 71):
            c.observe_progress(now+timedelta(seconds=n*60), nights)
        self.assertEqual(len(c.advances), 64)
        self.assertEqual(c.decisions_left(600), 10)
        c.observe_progress(now+timedelta(days=1), nights)
        self.assertEqual(c.decisions_left(600), 10)
