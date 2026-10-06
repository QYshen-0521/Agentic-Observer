"""Explicit runtime ablations shared by the live planner and local benchmark."""
import os

FEATURES = frozenset({'joint', 'integration', 'requests', 'required', 'quality',
                      'fault', 'diversity', 'calibration', 'stagnation', 'pacing'})


def enabled(name):
    disabled = set(filter(None, os.environ.get('OBSERVER_DISABLE_FEATURES', '').split(',')))
    unknown = disabled - FEATURES
    if unknown:
        raise ValueError('Unknown strategy features: ' + ','.join(sorted(unknown)))
    return name not in disabled


def fixed_level():
    value = os.environ.get('OBSERVER_FIXED_LEVEL')
    if value is None:
        return None
    level = int(value)
    if level not in (0, 1, 2):
        raise ValueError('OBSERVER_FIXED_LEVEL must be 0, 1 or 2')
    return level
