"""Regressions for exact assignments after approximate field screening."""
import random
import unittest
from datetime import timedelta

from test_v4_agent import initial, request
from agent_core.exposure import ExposureModel
from agent_core.fields import phase_fields
from agent_core.geometry import (
    altaz_to_radec, local_sidereal_deg, parse_utc, shift_altaz, tangent_offsets,
)
from agent_core.state import SurveyState


class FieldSearchTests(unittest.TestCase):
    def search(self, side, altitude=60):
        state = SurveyState(initial(side))
        grid = state.fiber_grid
        centers = tuple(grid.fiber_center(i) for i in range(grid.n))
        rng = random.Random(17)
        points = [(altitude, 170)] + [shift_altaz(altitude, 170,
                  rng.uniform(-grid.fov, grid.fov), rng.uniform(-grid.fov, grid.fov))
                  for _ in range(60)]
        visible = set(range(len(points) - 1))
        fields = phase_fields(grid, centers, [(1, 0)], lambda *_: range(len(points)),
                              visible, points.__getitem__, lambda _: 1,
                              lambda _: 600, 0, .08)
        return grid, centers, points, visible, fields

    def test_exact_reprojection_at_zenith_and_different_grid_sizes(self):
        for side in (3, 4, 10):
            for altitude in (60, 88.3):
                with self.subTest(side=side, altitude=altitude):
                    grid, _, points, visible, fields = self.search(side, altitude)
                    self.assertTrue(fields)
                    self.assertLessEqual(len(fields), 25)
                    for _, alt, az, chosen in fields:
                        self.assertLessEqual(alt, 89)
                        for fiber, choices in chosen.items():
                            self.assertLessEqual(len(choices), 3)
                            for _, i, margin in choices:
                                self.assertIn(i, visible)
                                actual, actual_margin = grid.classify(
                                    *tangent_offsets(*points[i], alt, az))
                                self.assertEqual(actual, fiber)
                                self.assertAlmostEqual(margin, actual_margin)

    def test_phase_search_fills_more_fibers_than_only_centering_anchor(self):
        grid, centers, points, _, fields = self.search(4)
        old_best = 0
        for north, east in centers:
            alt, az = shift_altaz(*points[0], -north, -east)
            hits = {grid.classify(*tangent_offsets(*point, alt, az))[0]
                    for point in points[:-1]}
            old_best = max(old_best, len(hits - {None}))
        self.assertGreater(max(len(field[3]) for field in fields), old_best)


class PublicInterpolationTests(unittest.TestCase):
    def test_interpolation_matches_direct_public_geometry_over_exposure(self):
        state = SurveyState(initial())
        start = parse_utc(request()['now_utc'])
        for day in (0, 8, 18):
            now = start + timedelta(days=day)
            lst = local_sidereal_deg(now, state.lon)
            for alt in (35, 60, 85):
                for az in (10, 100, 190, 280):
                    state.ra[0], state.dec[0] = altaz_to_radec(alt, az, lst, state.lat)
                    model = ExposureModel(state, now, lst, now)
                    for offset in (30, 150, 450, 915, 1815, 3585):
                        actual = model.exact_sample(0, offset)
                        self.assertLess(abs(model.sample(0, offset) - actual), 1e-3)

    def test_node_cache_is_local_to_public_decision_snapshot(self):
        state = SurveyState(initial())
        now = parse_utc(request()['now_utc'])
        lst = local_sidereal_deg(now, state.lon)
        first = ExposureModel(state, now, lst, now)
        first.sample(0, 150)
        self.assertEqual(set(first.samples), {(0, 0), (0, 300)})
        second = ExposureModel(state, now + timedelta(seconds=60), lst, now)
        self.assertEqual(second.samples, {})


if __name__ == '__main__':
    unittest.main()
