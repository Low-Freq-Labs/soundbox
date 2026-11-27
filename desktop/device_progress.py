"""Measured device presentation; final readback alone owns completion."""
from typing import NamedTuple


class PhaseProgress(NamedTuple):
    message: str
    value: int | None
    unit: str


class CopySample(NamedTuple):
    copied: int
    bytes_per_second: float | None


class CopyAverage:
    """One worker's measured copy deltas and event-driven display cadence.

    A CDJ plan publishes each path once in one ordered worker stream. Its
    current-file counter can repeat or reset without recounting prior bytes.
    Source acquisition, checks and publication acknowledgements are not bytes.
    """
    def __init__(self):
        self.copied=0;self.started=None;self.last_sample=None
        self._label=None;self._high_water=0

    def observe(self,label,done,now):
        if self.started is None:self.started=now
        if label!=self._label:self._label=label;self._high_water=0
        self.copied+=max(0,done-self._high_water)
        self._high_water=max(self._high_water,done)

    def sample(self,now,*,force=False):
        if self.started is None:return None
        if not force and self.last_sample is not None and now-self.last_sample<0.5:return None
        self.last_sample=now;elapsed=now-self.started
        return CopySample(self.copied,self.copied/elapsed if elapsed>=1 else None)


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
