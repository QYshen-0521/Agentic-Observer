"""Survey state: the target catalogue, learned sky-quality scale, and per-target
progress. Built once from `initialize`, then updated from every `decision_request`'s
messages and `last_result`. Holds no hidden data -- only what the public protocol
hands us, plus what we infer from our own hits (never from a file).

The public catalogue uses parallel arrays indexed by target. Actual scores and
completion-factor intervals have separate ledgers because program matching is hidden.
"""
from __future__ import annotations

import bisect
import math
import statistics
from collections import deque
from typing import NamedTuple, Optional

from .geometry import FiberGrid, max_hour_angle_deg, parse_utc, wrap180
from .scoring import ScoringModel

ALT_MARGIN_DEG = 0.6
SKY_MEMORY_HOURS = 2.0
RECENT_SAMPLES = 12
EARLIER_SAMPLES = 24
SIDEREAL_DEG_PER_SECOND = 360.98564736629 / 86400.0


class PendingPrediction(NamedTuple):
    model: float          # lunar/airmass quality model used at planning time
    band_model: float      # model / 0.95, used for program-band back-estimation
    alt: float
    az: float
    clean: bool            # true when no all-sky notice / directional block applied at plan time
    expected_gain: float = 0.0  # predicted SCIENCE increment, excluding request/required value


class FaultEvidence(NamedTuple):
    recent_median: float
    earlier_median: float
    drop: float
    recent_samples: int
    recent_nights: int
    earlier_samples: int
    dark_checks: int
    dark_matched: int


class ExposureRecord(NamedTuple):
    action_index: int
    target_id: str
    factor_low: float
    factor_high: float
    score: float


def _mod(a: float, n: float) -> float:
    m = a % n
    return m + n if m < 0 else m


class SurveyState:
    def __init__(self, init_payload: dict):
        self.public_init = init_payload
        site = init_payload["site"]
        survey = init_payload["survey"]
        instrument = init_payload["instrument"]
        limits = init_payload.get("limits", {})

        self.lat = float(site["latitude_deg"])
        self.lon = float(site["longitude_deg"])
        self.min_alt = float(site.get("minimum_altitude_deg", 30.0))
        self.sun_altitude_limit_deg = float(site.get("sun_altitude_limit_deg", -18.0))

        self.survey_start = parse_utc(survey["start_utc"])
        self.survey_end = parse_utc(survey["end_utc"])
        self.slot_seconds = int(survey.get("slot_seconds", 900))
        self.nights = [(parse_utc(n["observing_start_utc"]), parse_utc(n["observing_end_utc"]))
                       for n in survey.get("nights", [])]

        self.fiber_grid = FiberGrid(instrument)
        exposure = instrument.get("exposure", {})
        self.min_exposure = int(exposure.get("min_duration_seconds", 60))
        self.max_exposure = int(exposure.get("max_duration_seconds", 3600))

        self.scoring = ScoringModel(init_payload.get("scoring", {}), site)

        reporting = init_payload.get("scoring", {}).get("reporting", {})
        self.max_consecutive_reports = int(reporting.get("max_consecutive_reports", limits.get("max_consecutive_reports", 32)))
        self.false_report_free_allowance = int(reporting.get("false_report_free_allowance", 0))
        self.response_max_bytes = int(limits.get("response_max_bytes", 524288))

        # Parallel arrays, one slot per target, in catalogue order.
        self.ids: list[str] = []
        self.ra: list[float] = []
        self.dec: list[float] = []
        self.flux: list[float] = []
        self.weight: list[float] = []
        self.required: list[bool] = []
        self.index_of: dict[str, int] = {}

        columns = init_payload.get("targets", {}).get("columns", [])
        col = {name: idx for idx, name in enumerate(columns)}
        for row in init_payload.get("targets", {}).get("rows", []):
            target_id = str(row[col["target_id"]])
            self.index_of[target_id] = len(self.ids)
            self.ids.append(target_id)
            self.ra.append(float(row[col["ra_deg"]]))
            self.dec.append(float(row[col["dec_deg"]]))
            self.flux.append(float(row[col["feature_flux"]]))
            self.weight.append(float(row[col["science_weight"]]))
            self.required.append(bool(row[col["required"]]))

        n = len(self.ids)
        self.hmax = [max_hour_angle_deg(self.dec[i], self.lat, self.min_alt + ALT_MARGIN_DEG) for i in range(n)]
        self.factor = [0.0] * n
        self.factor_high = [0.0] * n
        self.best_score = [0.0] * n
        self.misses = [0] * n
        self.attempts = [0] * n
        self.active = [i for i in range(n) if self.hmax[i] > 0.0]

        self._cells: dict[int, list[tuple[float, int]]] = {}
        self._build_index()
        self.first_night, self.last_night = self._build_windows()

        self.scale = 1.0
        self.prior_scale = 1.0
        self.band_scale = 1.0
        self._samples: deque = deque(maxlen=24)           # (hours, ratio)
        self._all_ratios: deque = deque(maxlen=400)        # ratio
        self.clean_history: list[tuple[float, int, float]] = []  # (hours, night, ratio)
        self.pending_night = -1
        self._band_checks: deque = deque(maxlen=60)        # (hours, program, matched, model)
        self.force_program: Optional[str] = None
        self.pending: dict[str, PendingPrediction] = {}
        self.pending_program = "BACKUP"
        self.pending_duration = 0
        self.blocked: list[tuple[float, float]] = []       # (az, alt) where a hit scored zero
        self.notices: set[str] = set()                      # "kind|direction"
        self.terrain: set[str] = set()
        self.extra_avoid: set[str] = set()
        self.duration_scale = 1.0
        self.fast_level = 0

        # Actual scores are exact; factors remain intervals when program matching is
        # ambiguous. Never label an estimate as the backend's exact completion factor.
        self.ledger: list[ExposureRecord] = []
        self.pending_action_index: Optional[int] = None
        self.last_science_gain = 0.0
        self.stagnation = {}
        self.cooldown_until = {}
        self.suppress_stagnation = True
        self.progress_epoch = 0
        self._cell_ra = {key: [r for r, _ in band] for key, band in self._cells.items()}

    # -- spatial index -------------------------------------------------------

    def _build_index(self) -> None:
        for i in self.active:
            key = math.floor(self.dec[i])
            self._cells.setdefault(key, []).append((self.ra[i], i))
        for band in self._cells.values():
            band.sort(key=lambda pair: pair[0])

    def neighbours(self, ra: float, dec: float, radius: float):
        """Indices within `radius` degrees of (ra, dec), using the 1-degree declination-band index."""
        found: list[int] = []
        cos_dec = max(0.05, math.cos(math.radians(min(89.0, abs(dec) + radius))))
        width = radius / cos_dec
        lo_key, hi_key = math.floor(dec - radius), math.floor(dec + radius)
        for key in range(lo_key, hi_key + 1):
            band = self._cells.get(key)
            if not band:
                continue
            spans: list[tuple[float, float]]
            lo, hi = ra - width, ra + width
            if lo < 0:
                spans = [(0.0, hi), (lo + 360.0, 360.0)]
            elif hi >= 360:
                spans = [(lo, 360.0), (0.0, hi - 360.0)]
            else:
                spans = [(lo, hi)]
            keys = self._cell_ra[key]
            for low, high in spans:
                start = bisect.bisect_left(keys, low)
                end = bisect.bisect_right(keys, high)
                for k in range(start, end):
                    found.append(band[k][1])
        return found

    def _build_windows(self):
        """First/last night index on which each target has >=20 minutes above the limit."""
        need = 20 * 60 * SIDEREAL_DEG_PER_SECOND
        spans = []
        for start, end in self.nights:
            from .geometry import local_sidereal_deg
            l0 = local_sidereal_deg(start, self.lon)
            span = (end - start).total_seconds() * SIDEREAL_DEG_PER_SECOND
            spans.append((l0, span))
        n = len(self.ra)
        first_night = [len(self.nights)] * n
        last_night = [-1] * n
        for i in self.active:
            h = self.hmax[i]
            for k, (l0, span) in enumerate(spans):
                if h >= 180.0:
                    overlap = span
                else:
                    a = _mod(self.ra[i] - h - l0, 360.0)
                    overlap = max(0.0, min(span, a + 2 * h) - a) + max(0.0, min(span, a - 360.0 + 2 * h))
                if overlap >= need:
                    if first_night[i] > k:
                        first_night[i] = k
                    last_night[i] = k
        return first_night, last_night

    # -- messages and results -------------------------------------------------

    def on_messages(self, messages: list[dict], latest_bulletin: Optional[dict]) -> None:
        old_notices = self.notices
        for message in messages:
            if message.get("record_type") == "bulletin" and message.get("initial"):
                for notice in message.get("notices", []):
                    if notice.get("event_kind") == "terrain_obstruction":
                        self.terrain.add(notice.get("direction"))
            elif message.get("record_type") == "state_resync":
                self._resync(message)
        notices = (latest_bulletin or {}).get("notices", [])
        self.notices = {f"{n.get('event_kind')}|{n.get('direction')}" for n in notices
                        if n.get("event_kind") != "terrain_obstruction"}
        if old_notices != self.notices:
            self.reset_stagnation()

    def reset_stagnation(self):
        self.stagnation.clear()
        self.cooldown_until.clear()

    def science_weight(self, i, hours):
        return 0.1 if self.suppress_stagnation and hours < self.cooldown_until.get(i, -1.0) else 1.0

    def _resync(self, message: dict) -> None:
        """Rollback score and factor bounds using valid exposure history and public resync."""
        previous_score = sum(self.best_score)
        window = message.get("invalidated_window") or {}
        start, end = window.get("action_index_start"), window.get("action_index_end_exclusive")
        if start is not None and end is not None:
            self.ledger = [entry for entry in self.ledger if not (start <= entry[0] < end)]
        else:
            self.ledger = []  # no window given: nothing in the ledger can be trusted
        low: dict[str, float] = {}
        high: dict[str, float] = {}
        for entry in self.ledger:
            low[entry.target_id] = max(low.get(entry.target_id, 0.0), entry.factor_low)
            high[entry.target_id] = max(high.get(entry.target_id, 0.0), entry.factor_high)

        best_scores = message.get("best_scores") or []
        best: dict[str, float] = {}
        if best_scores and isinstance(best_scores[0], dict):
            for row in best_scores:
                best[row.get("target_id")] = float(row.get("best_score", 0.0))
        else:
            for target_id, score in zip(message.get("observed_target_ids", []), best_scores):
                best[target_id] = float(score)
        top_multiplier = max(self.scoring.program_multipliers.values()) if self.scoring.program_multipliers else 1.2

        for i in range(len(self.ids)):
            target_id = self.ids[i]
            score = best.get(target_id, 0.0)
            self.best_score[i] = score
            fallback = min(1.0, score / (self.weight[i] * top_multiplier)) if score > 0 and self.weight[i] > 0 else 0.0
            self.factor[i] = low.get(target_id, fallback)
            self.factor_high[i] = high.get(target_id, min(1.0, score / max(1e-9, self.weight[i] * self.scoring.mismatch_multiplier)))
            self.misses[i] = 0
            self.attempts[i] = 0
        self.active = [i for i in range(len(self.ids)) if self.hmax[i] > 0.0]
        self.pending.clear()
        self.pending_action_index = None
        self.last_science_gain += sum(self.best_score)-previous_score
        self.reset_stagnation()
        self.progress_epoch += 1

    def site_closed(self) -> bool:
        for key in self.notices:
            kind, _, direction = key.partition("|")
            if kind in ("rain", "storm") and direction == "ALL":
                return True
        return False

    def all_sky_notice(self) -> bool:
        return any(key.partition("|")[2] == "ALL" for key in self.notices)

    def on_result(self, last_result: Optional[dict], hours: float) -> None:
        self.last_science_gain = 0.0
        action_index = self.pending_action_index
        self.pending_action_index = None
        if not last_result or last_result.get("action") != "observe" or not self.pending:
            self.pending.clear()
            return
        hits = {h.get("target_id"): float(h.get("score", 0.0)) for h in last_result.get("hits", [])}
        any_positive = any(score > 0 for score in hits.values())
        scoring = self.scoring
        multipliers = scoring.program_multipliers
        mismatch = scoring.mismatch_multiplier
        declared_multiplier = multipliers.get(self.pending_program, 1.0)
        f0t0 = scoring.f0t0

        exposure_ratios = []
        clean_ratios = []
        matched_models = []
        clean_models = []
        for target_id, prediction in self.pending.items():
            i = self.index_of.get(target_id)
            if i is None:
                continue
            score = hits.get(target_id, 0.0)
            gain = max(0.0, score - self.best_score[i])
            self.last_science_gain += gain
            if prediction.expected_gain > 1e-9:
                streak = self.stagnation.get(i, 0) + 1 if gain < 0.1 * prediction.expected_gain else 0
                self.stagnation[i] = streak
                if streak >= 3:
                    self.cooldown_until[i] = hours + 2.0
                    self.stagnation[i] = 0
            if prediction.clean:
                clean_models.append(prediction.band_model)
            if target_id not in hits:
                self.misses[i] += 1
                continue
            score = hits[target_id]
            if score <= 0.0:
                if any_positive:
                    self.blocked.append((prediction.az, prediction.alt))
                continue
            weight = self.weight[i] if self.weight[i] > 0 else 1e-9
            self.best_score[i] = max(self.best_score[i], score)
            multiplier_seen = score / weight
            # A score above the largest mismatched score proves a match. Below that,
            # both hypotheses can be physically valid (instrument efficiency is hidden).
            possible = [score / (weight * m) for m in {declared_multiplier, mismatch}
                        if m > 0 and score / (weight * m) <= 1.0 + 2e-4]
            if not possible:
                possible = [1.0]  # rounded saturated feedback
            factor = min(1.0, min(possible))
            upper = min(1.0, max(possible))
            if prediction.clean and multiplier_seen > mismatch + 2e-4:
                matched_models.append(prediction.band_model)
            self.factor[i] = max(self.factor[i], factor)
            self.factor_high[i] = max(self.factor_high[i], upper)
            if action_index is not None:
                self.ledger.append(ExposureRecord(action_index, target_id, factor, upper, score))
            if self.required[i] and self.factor[i] < scoring.required_threshold:
                self.attempts[i] += 1
            if factor < 0.97 and self.flux[i] > 0 and self.pending_duration > 0 and prediction.model > 0:
                ratio = (factor * f0t0) / (self.flux[i] * self.pending_duration * prediction.model)
                exposure_ratios.append(ratio)
                if prediction.clean:
                    clean_ratios.append(ratio)
        # Fibre hits in one exposure share weather: one exposure is one sample.
        if exposure_ratios:
            ratio = statistics.median(exposure_ratios)
            self._samples.append((hours, ratio))
            self._all_ratios.append(ratio)
        if clean_ratios:
            self.clean_history.append((hours, self.pending_night, statistics.median(clean_ratios)))
        if clean_models:
            # One independent exposure, including UNKNOWN outcomes. A low score
            # cannot prove a mismatch: instrument efficiency is not observable.
            self._band_checks.append((hours, self.pending_program, bool(matched_models),
                                      statistics.median(matched_models or clean_models)))
        self._update_band_scale(hours)
        self.pending.clear()
        self.update_scale(hours)

    def has_recent_sample(self, hours: float) -> bool:
        return any(when >= hours - SKY_MEMORY_HOURS for when, _ in self._samples)

    def update_scale(self, hours: float) -> None:
        if len(self._all_ratios) >= 8:
            ordered = sorted(self._all_ratios)
            self.prior_scale = ordered[len(ordered) // 2]
        recent = sorted(ratio for when, ratio in self._samples if when >= hours - SKY_MEMORY_HOURS)
        self.scale = max(0.05, recent[len(recent) // 2]) if len(recent) >= 4 else self.prior_scale
        # Science scale includes instrument efficiency. Band scale is constrained
        # separately by confirmed program matches, never by a quality collapse.

    def _update_band_scale(self, hours):
        lower, upper = [], []
        for when, program, confirmed, model in self._band_checks:
            if not confirmed or when < hours - SKY_MEMORY_HOURS or model <= 0:
                continue
            if program == 'DARK':
                lower.append(self.scoring.program_bands['DARK'] / model)
            elif program == 'BRIGHT':
                lower.append(self.scoring.program_bands['BRIGHT'] / model)
                upper.append(self.scoring.program_bands['DARK'] / model)
            else:
                upper.append(self.scoring.program_bands['BRIGHT'] / model)
        lo = statistics.median(lower) if lower else 0.05
        hi = statistics.median(upper) if upper else 2.0
        if lo <= hi:
            self.band_scale = min(hi, max(lo, self.band_scale))

    # -- fault diagnostics ------------------------------------------------------

    def fault_evidence(self) -> Optional[FaultEvidence]:
        history = self.clean_history
        if len(history) < RECENT_SAMPLES + EARLIER_SAMPLES:
            return None
        recent = history[-RECENT_SAMPLES:]
        earlier = history[:-RECENT_SAMPLES]
        span = recent[-1][0] - recent[0][0]
        nights = len({night for _, night, _ in recent})
        if span < 3.0:
            return None
        recent_sorted = sorted(r for _, _, r in recent)
        earlier_sorted = sorted(r for _, _, r in earlier)
        recent_median = recent_sorted[len(recent_sorted) // 2]
        earlier_median = earlier_sorted[len(earlier_sorted) // 2]
        dark_line = self.scoring.program_bands["DARK"] * 1.3
        dark = [c for c in list(self._band_checks)[-16:]
                if c[0] >= recent[0][0] and c[1] == "DARK" and c[3] * self.band_scale >= dark_line]
        return FaultEvidence(
            recent_median=round(recent_median, 3),
            earlier_median=round(earlier_median, 3),
            drop=round(recent_median / max(1e-9, earlier_median), 3),
            recent_samples=len(recent),
            recent_nights=nights,
            earlier_samples=len(earlier),
            dark_checks=len(dark),
            dark_matched=sum(1 for c in dark if c[2]),
        )

    def forget_quality_history(self) -> None:
        self.clean_history = []
        self._band_checks.clear()
        self._samples.clear()
        self._all_ratios.clear()
        self.prior_scale = 1.0
        self.reset_stagnation()
        self.progress_epoch += 1

    # -- night lookup -------------------------------------------------------------

    def current_night(self, now):
        for index, (start, end) in enumerate(self.nights):
            if start <= now < end:
                return index, start, end
        return None

    def next_night_start(self, now):
        for start, _end in self.nights:
            if start > now:
                return start
        return None
