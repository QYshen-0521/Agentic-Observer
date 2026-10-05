"""Budget, scheduling and realized-yield regressions for the observing planner."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from test_v4_agent import initial, request
from agent_core.clock import Clock
from agent_core.geometry import parse_utc
from agent_core.planner import Planner
from agent_core.state import SurveyState, PendingPrediction
from agent_core.exposure import ExposureModel


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


class ExposureAndYieldTests(unittest.TestCase):
    def test_integration_splits_at_public_slot_boundaries(self):
        s = SurveyState(initial())
        start = parse_utc(request()['now_utc'])
        model = ExposureModel(s, start+timedelta(seconds=300), 0, start)
        with patch.object(model, 'sample', side_effect=lambda i, offset: offset) as sample:
            self.assertEqual(model.average(0, 1200), 600)
            self.assertEqual([call.args[1] for call in sample.call_args_list], [300, 900])
            model.average(0, 1200)
            self.assertEqual(sample.call_count, 2)

    def test_three_underperforming_exposures_cool_down_and_weather_resets(self):
        s = SurveyState(initial())
        for n in range(3):
            s.pending = {'T0': PendingPrediction(1, 1, 60, 170, True, 1)}
            s.on_result({'action':'observe','hits':[{'target_id':'T0','score':0}]}, n/10)
        self.assertEqual(s.science_weight(0, 1), 0.1)
        self.assertEqual(s.science_weight(0, 3), 1)
        s.on_messages([], {'notices':[{'event_kind':'cloud','direction':'N'}]})
        self.assertEqual(s.science_weight(0, 1), 1)

    def test_dark_unknown_is_one_exposure_and_not_a_confirmed_match(self):
        s = SurveyState(initial())
        s.pending_program = 'DARK'
        s.pending = {f'T{i}':PendingPrediction(1, 1, 60, 170, True) for i in range(8)}
        s.on_result({'action':'observe','hits':[{'target_id':f'T{i}','score':0.01} for i in range(8)]}, 0)
        self.assertEqual(len(s._band_checks), 1)
        self.assertFalse(s._band_checks[0][2])
        self.assertEqual(s.band_scale, 1)

    def test_science_collapse_does_not_change_program_band_scale(self):
        s = SurveyState(initial())
        s._samples.extend([(0, .2)] * 8)
        s._all_ratios.extend([.2] * 8)
        s.update_scale(0)
        self.assertEqual(s.scale, .2)
        self.assertEqual(s.band_scale, 1)

    def test_required_and_request_targets_are_exempt_from_science_cooldown(self):
        s = SurveyState(initial())
        p = Planner(s)
        s.cooldown_until[0] = 2
        v = p._value(0)
        s.suppress_stagnation = False
        self.assertEqual(p._value(0), v)
        s.required[0] = False
        s.suppress_stagnation = True
        p._request_bonus[0] = 10
        v = p._value(0)
        s.suppress_stagnation = False
        self.assertEqual(p._value(0), v)
