"""Snapshot-local time integration of PUBLIC sky geometry, never hidden weather."""
from datetime import timedelta

from .geometry import Moon, SIDEREAL_DEG_PER_SECOND, lunar_factor, radec_to_altaz


class ExposureModel:
    def __init__(self, state, now, lst, night_start):
        self.state, self.now, self.lst = state, now, lst
        self.slot_offset = (now - night_start).total_seconds() % state.slot_seconds
        self.moons, self.samples, self.exposures = {}, {}, {}

    def sample(self, i, offset):
        # Smooth public geometry is shared across the many duration candidates.
        # Interpolate between five-minute nodes, retaining slot-midpoint weights.
        lower = int(offset // 300) * 300
        upper = lower + 300
        a = self.exact_sample(i, lower)
        if offset == lower:
            return a
        b = self.exact_sample(i, upper)
        return a + (b - a) * (offset - lower) / 300

    def exact_sample(self, i, offset):
        key = (i, offset)
        if key not in self.samples:
            state = self.state
            lst = (self.lst + offset * SIDEREAL_DEG_PER_SECOND) % 360.0
            if offset not in self.moons:
                self.moons[offset] = Moon(self.now + timedelta(seconds=offset), lst, state.lat)
            alt, _ = radec_to_altaz(state.ra[i], state.dec[i], lst, state.lat)
            self.samples[key] = state.scoring.quality_model(alt, lunar_factor(
                self.moons[offset], state.ra[i], state.dec[i], state.scoring.lunar_model))
        return self.samples[key]

    def average(self, i, duration):
        key = (i, duration)
        if key not in self.exposures:
            elapsed, integral = 0.0, 0.0
            while elapsed < duration:
                into = (self.slot_offset + elapsed) % self.state.slot_seconds
                piece = min(duration-elapsed, self.state.slot_seconds-into)
                integral += piece * self.sample(i, elapsed+piece/2.0)
                elapsed += piece
            self.exposures[key] = integral / duration
        return self.exposures[key]
