"""RAM-only ruler arithmetic, independent of playback and source analysis."""
from bisect import bisect_right
from math import ceil

MODES = ('Current Position (Bars)', 'Count to next MEMORY CUE (Bars)',
         'Count to next MEMORY CUE (Beats)')


def countdown(times, memory_times, position):
    """Whole beats remaining, rounding a partial beat up; no grid extrapolation."""
    if len(times) < 2 or not times[0] <= position <= times[-1]:
        return None
    cue_index = bisect_right(memory_times, position)
    if cue_index == len(memory_times):
        return None
    cue = memory_times[cue_index]
    if cue > times[-1]:
        return None

    def phase(value):
        index = bisect_right(times, value) - 1
        if index >= len(times) - 1:
            return float(index)
        return index + (value - times[index]) / (times[index + 1] - times[index])

    return max(0, ceil(phase(cue) - phase(position) - 1e-9))
