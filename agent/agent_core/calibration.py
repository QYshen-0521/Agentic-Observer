"""Estimate a fixed pointing offset from public assignment/hit feedback only."""
from collections import deque

from .geometry import tangent_offsets


class PointingCalibration:
    def __init__(self, grid):
        self.grid = grid
        self.offset = (0.0, 0.0)
        self.samples = deque(maxlen=160)
        self.pending = []
        self.exposures = 0

    def command(self, alt, az):
        return alt - self.offset[0], (az - self.offset[1]) % 360.0

    def record(self, action, predictions, minimum_altitude):
        self.pending = []
        if action.get('action') != 'observe':
            return
        pointing = action['pointing']
        # High-altitude targets avoid confusing horizon failures with fibre misses.
        for fiber, target in action['assignments'].items():
            prediction = predictions.get(target)
            if prediction is not None and prediction.alt >= minimum_altitude + 10:
                self.pending.append((pointing['alt_deg'], pointing['az_deg'],
                                     prediction.alt, prediction.az, int(fiber), target))

    def feedback(self, result):
        if not self.pending:
            return False
        pending, self.pending = self.pending, []
        if not result or result.get('action') != 'observe':
            return False
        hits = {hit['target_id'] for hit in result.get('hits', [])}
        # A hit with score zero still constrains geometry.
        self.samples.extend((*sample[:5], sample[5] in hits) for sample in pending)
        self.exposures += 1
        if len(self.samples) < 40 or self.exposures % 5:
            return False
        old_error = self._errors(self.offset)
        if old_error < 3:
            return False
        step = self.grid.pitch / 12
        candidates = [(a * step, z * step) for a in range(-9, 10) for z in range(-9, 10)]
        scored = [(self._errors(offset), offset) for offset in candidates]
        _, center = min(scored, key=lambda pair: (pair[0], sum(x*x for x in pair[1])))
        fine = [(center[0] + a*step/4, center[1] + z*step/4)
                for a in range(-4, 5) for z in range(-4, 5)]
        scored = [(self._errors(offset), offset) for offset in fine]
        minimum = min(error for error, _ in scored)
        winners = [offset for error, offset in scored if error == minimum]
        estimate = tuple(sum(o[k] for o in winners)/len(winners) for k in (0, 1))
        error = self._errors(estimate)
        if error <= max(1, len(self.samples)*0.025) and old_error-error >= 3:
            self.offset = estimate
            return True
        return False

    def _errors(self, offset):
        errors = 0
        for c_alt, c_az, alt, az, fiber, hit in self.samples:
            offsets = tangent_offsets(alt, az, c_alt+offset[0], (c_az+offset[1]) % 360)
            actual = self.grid.classify(*offsets)[0] if offsets is not None else None
            errors += (actual == fiber) != hit
        return errors
