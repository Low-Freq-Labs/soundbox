"""Measured stock-iPod phases; final readback alone owns overall completion."""
from typing import NamedTuple


class PhaseProgress(NamedTuple):
    message: str
    value: int | None
    unit: str


_STOCK_PHASES = {
    'iTunes planning': ('Preparing iPod', 0, 150, 'checks'),
    'Checking iPod files': ('Checking iPod', 150, 100, 'items'),
    'iTunes media': ('Copying songs', 250, 400, 'bytes'),
    'iTunes copying': ('Copying songs', 250, 400, 'work_bytes'),
    'iTunes verifying': ('Copying songs', 250, 400, 'work_bytes'),
    'iTunes artwork': ('Updating artwork', 650, 100, 'bytes'),
    'iTunes database': ('Saving iPod library', 750, 30, 'steps'),
    'iTunes cleanup': ('Finishing iPod', 780, 20, 'items'),
}


def stock_phase_progress(stage, done, total):
    """Map completed phase units; source bytes expose detail without advancing."""
    if type(done) is not int or type(total) is not int or done < 0 or total < 0:
        return None
    if stage == 'Acquiring source':
        return PhaseProgress('Preparing source', None, 'bytes')
    if stage == 'Waiting for cloud source':
        return PhaseProgress('Waiting for cloud source', None, 'retries')
    phase = _STOCK_PHASES.get(stage)
    if phase is None:return None
    message, base, span, unit = phase
    value = base + span * min(done, total) // total if total else None
    return PhaseProgress(message, value, unit)
