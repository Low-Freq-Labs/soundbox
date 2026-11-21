"""Disposable negative source observations and bounded iPod planning metrics."""
from collections import OrderedDict
from pathlib import Path
from threading import Lock
import time

from .operation_control import record_metric


class NegativeSourceOmissions:
    """Remember rejection only; never provide media admission or byte proofs."""
    def __init__(self, limit: int=4096) -> None:
        self.limit=limit
        self.rows: OrderedDict[Path,tuple[tuple[int,...],str]]=OrderedDict()
        self.lock=Lock()

    def get(self, path: Path, epoch: tuple[int,...]) -> str | None:
        with self.lock:
            row=self.rows.pop(path,None)
            if row is None or row[0]!=epoch:return None
            self.rows[path]=row
            return row[1]

    def remember(self, path: Path, epoch: tuple[int,...], reason: str) -> None:
        if not reason:return
        with self.lock:
            self.rows.pop(path,None)
            self.rows[path]=(epoch,reason)
            while len(self.rows)>self.limit:self.rows.popitem(last=False)

    def discard(self, path: Path) -> None:
        with self.lock:self.rows.pop(path,None)


_STAGES={
    'stock':frozenset(('capture','sources','inventory','playlists','prune',
                       'metadata','artwork','render','finalize')),
    'dual':frozenset(('reconcile','source_setup','source_verification','stock',
                      'rockbox_paths','artwork_ownership','finalize')),
}


class PlanTimings:
    """Fixed phase names, integer microseconds, existing operation metric owner."""
    def __init__(self, kind: str) -> None:
        self.kind=kind
        self.started=self.previous=time.monotonic()

    def mark(self, stage: str) -> None:
        if stage not in _STAGES[self.kind]:raise ValueError('Unknown iPod planning phase')
        now=time.monotonic()
        record_metric(self.kind+'_plan_'+stage+'_us',max(0,round((now-self.previous)*1_000_000)))
        self.previous=now
        if stage=='finalize':
            record_metric(self.kind+'_plan_total_us',max(0,round((now-self.started)*1_000_000)))
