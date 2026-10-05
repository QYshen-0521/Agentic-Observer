"""Bounded request beam search over public visibility and fibre geometry."""
import math
from datetime import timedelta

from .geometry import (SIDEREAL_DEG_PER_SECOND, local_sidereal_deg, parse_utc,
                       radec_to_altaz, shift_altaz, tangent_offsets, wrap180,
                       altaz_to_radec, max_hour_angle_deg)
from .exposure import ExposureModel


class RequestScheduler:
    def __init__(self, state, direction_factor):
        self.state, self.direction_factor = state, direction_factor
        self.cache = {}
        self.failed = {}
        self.searches = 0

    def plan(self, request, now, opportunity_rate):
        s = self.state
        completed = frozenset(request.get('completed_target_ids') or [])
        deadline = parse_utc(request['deadline_utc'])
        ids = tuple(i for t in request.get('target_ids', [])
                    if t not in completed and (i := s.index_of.get(t)) is not None)
        need = int(request['remaining_count'])
        threshold = float(request.get('completion_factor_threshold', .5))
        key = (completed, need, threshold, deadline, round(s.scale, 1),
               frozenset(s.notices), frozenset(s.extra_avoid), s.progress_epoch)
        previous = self.cache.get(request.get('request_id'))
        if previous and previous[0] == key and now <= previous[1]['next_start']:
            return previous[1]
        if now < self.failed.get((request.get('request_id'), key), now):
            return None
        self.searches += 1
        if len(ids) < need or need > 8:
            self.failed[(request.get('request_id'), key)] = now+timedelta(seconds=s.slot_seconds)
            return None
        # Public requests normally have eight alternatives. Bound larger input
        # sets without assuming that all alternatives must be observed.
        ids = tuple(sorted(ids, key=lambda i: -s.flux[i])[:max(8, need)])
        beam = [(frozenset(), now, 0.0, ())]
        for _ in range(need):
            expanded, winners = [], []
            for done, moment, cost, steps in beam:
                for start, duration, hits, science_gain in self._options(ids, done, moment, deadline, threshold):
                    end = start + timedelta(seconds=duration)
                    new_done = done | hits
                    new_cost = cost + max(0.0, opportunity_rate*duration-science_gain)
                    item = (new_done, end, new_cost, steps+((start, end, hits),))
                    (winners if len(new_done) >= need else expanded).append(item)
            if winners:
                done, end, cost, steps = min(winners, key=lambda x: (x[2], x[1]))
                if cost >= max(0.0, float(request.get('completion_reward', 0))):
                    self.failed[(request.get('request_id'), key)] = now+timedelta(seconds=s.slot_seconds)
                    return None
                plan = {'targets':set(done), 'steps':steps, 'cost':cost,
                        'next_start':steps[0][0], 'end':end}
                self.cache[request.get('request_id')] = (key, plan)
                return plan
            distinct = {}
            for item in sorted(expanded, key=lambda x: (x[2], x[1], -len(x[0]))):
                signature = (item[0], item[1].replace(second=0, microsecond=0))
                distinct.setdefault(signature, item)
            beam = list(distinct.values())[:8]
            if not beam:
                self.failed[(request.get('request_id'), key)] = now+timedelta(seconds=s.slot_seconds)
                return None
        return None

    def _options(self, ids, done, moment, deadline, threshold):
        s, grid = self.state, self.state.fiber_grid
        middle = sorted({(grid.side-1)//2, grid.side//2})
        fibers = tuple(r*grid.side+c for r in middle for c in middle)
        for anchor in ids:
            if anchor in done or s.hmax[anchor] <= 0:
                continue
            # Try each remaining night: today's geometry does not establish
            # whether a target can be observed before tomorrow's deadline.
            for night_start, night_end in s.nights:
                start = max(moment, night_start)
                end = min(deadline, night_end)
                if end <= start:
                    continue
                lst = local_sidereal_deg(start, s.lon)
                ha = wrap180(lst-s.ra[anchor])
                h = max_hour_angle_deg(s.dec[anchor],s.lat,s.min_alt+2.5)
                if h <= 0:
                    continue
                wait = (-h-ha if ha < -h else 360-h-ha if ha > h else 0) / SIDEREAL_DEG_PER_SECOND
                start += timedelta(seconds=max(0.0, wait)+ (1.0 if wait > 0 else 0.0))
                if (end-start).total_seconds() < s.min_exposure:
                    continue
                lst = local_sidereal_deg(start, s.lon)
                positions = {i:radec_to_altaz(s.ra[i],s.dec[i],lst,s.lat) for i in ids if i not in done}
                exposure = ExposureModel(s, start, lst, night_start)
                for fiber in fibers:
                    north, east = grid.fiber_center(fiber)
                    c_alt, c_az = shift_altaz(*positions[anchor], -north, -east)
                    if not s.min_alt+1.5 <= c_alt <= 89:
                        continue
                    assigned = {}
                    for i, (alt, az) in positions.items():
                        offsets = tangent_offsets(alt,az,c_alt,c_az)
                        target_fiber, margin = grid.classify(*offsets) if offsets is not None else (None,0)
                        ha = wrap180(lst-s.ra[i])
                        if target_fiber is None or margin < .02 or not -s.hmax[i] <= ha <= s.hmax[i]:
                            continue
                        if self.direction_factor(alt,az) < 1:
                            continue
                        model = exposure.average(i, s.min_exposure)
                        duration = s.min_exposure
                        for _ in range(2):
                            duration = max(s.min_exposure, math.ceil(threshold*s.scoring.f0t0 /
                                max(1e-9,s.flux[i]*model*s.scale*.9)/30)*30)
                            if duration > s.max_exposure:
                                break
                            model = exposure.average(i,duration)
                        up = (s.hmax[i]-ha)/SIDEREAL_DEG_PER_SECOND if s.hmax[i] < 180 else 1e9
                        if duration > min(s.max_exposure, up, (end-start).total_seconds()):
                            continue
                        option = (duration, i, up)
                        if target_fiber not in assigned or option < assigned[target_fiber]:
                            assigned[target_fiber] = option
                    for duration in sorted({x[0] for x in assigned.values()}):
                        # The field centre must remain above the altitude limit too.
                        ra, dec = altaz_to_radec(c_alt,c_az,lst,s.lat)
                        h = max_hour_angle_deg(dec,s.lat,s.min_alt+.3)
                        if h < 180 and duration > (h-wrap180(lst-ra))/SIDEREAL_DEG_PER_SECOND:
                            continue
                        hits = frozenset(i for required,i,up in assigned.values() if required <= duration <= up)
                        if not hits:
                            continue
                        science = sum(max(0.0,s.weight[i]*min(1.0,s.flux[i]*duration*
                            exposure.average(i,duration)*s.scale*.9/s.scoring.f0t0)-s.best_score[i]) for i in hits)
                        yield start, duration, hits, science
