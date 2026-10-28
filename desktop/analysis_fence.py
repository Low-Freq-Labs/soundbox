"""Fence stale preparation publications before a source-grid transaction."""
from threading import RLock


class AnalysisWriteFence:
    def __init__(self):
        self._lock = RLock()
        self._protected = set()

    def protect(self, track_id):
        # Called in the source worker, never the GUI thread. Wait out a
        # publication already in progress, then reject this worker's old data.
        with self._lock:
            self._protected.add(str(track_id))

    def publish(self, track_id, callback):
        with self._lock:
            return False if str(track_id) in self._protected else callback()
