"""Background QThread workers.

⚠ QThread lifetime rule (see soundbox-gotchas): the OWNER must hold a ref until
the built-in `finished` signal fires (MainWindow._finish_worker) — dropping the
last ref in a custom-signal slot GC's the thread mid-run → SIGABRT.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from soundbox import artfetch, engine, metadata, rekordbox_db
from soundbox.rekordbox_xml import Playlist

from .devices import list_devices

LOG = logging.getLogger("soundbox")


class ExportWorker(QThread):
    """Processes a growable queue of (device, Playlist) jobs in the background.

    One worker = one EXPORTING bar. New jobs added mid-run grow the total so the
    bar always estimates everything queued.
    """
    progress = Signal(int, int, str)
    finished_all = Signal(list)

    def __init__(self, lib, overrides=None, titles=None, art_cache=None):
        super().__init__()
        self.lib = lib
        self.overrides = overrides or {}
        self.titles = titles or {}
        self.art_cache = art_cache
        self._lock = threading.Lock()
        self._jobs: list[tuple[str, Playlist]] = []
        self._index = 0
        self._total = 0
        self._base_done = 0
        self._stop = False
        self._reports: list[tuple[str, object]] = []

    def add(self, device: str, playlists: list[Playlist]):
        with self._lock:
            for pl in playlists:
                self._jobs.append((device, pl))
                self._total += max(1, len(self.lib.resolve(pl)))
        # No emit here: the next per-file progress tick updates the bar at the
        # real current position with the new total, so adding a playlist mid-export
        # smoothly lowers the % instead of jarringly snapping the bar to 0%.

    def cancel(self):
        with self._lock:
            self._stop = True

    def run(self):
        try:
            while True:
                with self._lock:
                    if self._stop or self._index >= len(self._jobs):
                        break
                    device, pl = self._jobs[self._index]
                    self._index += 1
                    base = self._base_done
                est = max(1, len(self.lib.resolve(pl)))

                def cb(d, _t, label, _base=base, _name=pl.name):
                    with self._lock:
                        tot = self._total
                    self.progress.emit(_base + d, tot, f"{_name} — {label}")

                try:
                    # clean=False: scrub AppleDouble once at end, not per playlist.
                    rep = engine.export_playlists(
                        self.lib, [pl], device, dry_run=False, derive_artists=True,
                        on_progress=cb, should_cancel=lambda: self._stop, clean=False,
                        artist_overrides=self.overrides, title_overrides=self.titles,
                        art_cache=self.art_cache)
                    self._reports.append((device, rep))
                except Exception as exc:  # noqa: BLE001 - surface failure in report
                    self._reports.append((device, str(exc) or repr(exc)))
                with self._lock:
                    self._base_done = base + est
                    total = self._total
                self.progress.emit(self._base_done, total, "")
        except Exception as exc:  # never let the thread take down the app
            self._reports.append(("(worker)", repr(exc)))
        # Scrub AppleDouble clutter ONCE per device touched (not per playlist) — but
        # only in the folders actually written this batch (fast on a full device).
        dev_dirs: dict[str, set] = {}
        for dev, rep in self._reports:
            wd = getattr(rep, "written_dirs", None)
            if wd:
                dev_dirs.setdefault(dev, set()).update(wd)
        for dev in dict.fromkeys(d for d, _ in self._reports):
            try:
                engine.clean_device(dev, dev_dirs.get(dev))
            except Exception:  # noqa: BLE001
                pass
        self.finished_all.emit(self._reports)


class ResyncWorker(QThread):
    """Brings ONE device back in line with rekordbox (re-export changed playlists,
    move renamed ones, prune orphans) off the UI thread."""
    progress = Signal(int, int, str)
    done = Signal(str, object)  # device, SyncReport | error string

    def __init__(self, lib, device, overrides=None, titles=None, art_cache=None, extra=None):
        super().__init__()
        self.lib = lib
        self.device = device
        self.overrides = overrides or {}
        self.titles = titles or {}
        self.art_cache = art_cache
        self.extra = extra or []  # custom SoundBox playlists (not in lib.playlists)
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        def cb(d, t, label):
            self.progress.emit(d, t, label)
        try:
            rep = engine.resync_device(
                self.lib, self.device, on_progress=cb,
                should_cancel=lambda: self._stop,
                artist_overrides=self.overrides, title_overrides=self.titles,
                art_cache=self.art_cache, extra=self.extra)
            self.done.emit(self.device, rep)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the app
            self.done.emit(self.device, str(exc) or repr(exc))


class RefreshWorker(QThread):
    """Reads the live rekordbox DB off the UI thread (copy + parse is slow)."""
    done = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            lib = rekordbox_db.load_live()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(lib)


class DeviceScanWorker(QThread):
    """Enumerate external devices + read each one's free space and sync drift OFF
    the UI thread.

    Touching a removable volume (os.statvfs, reading the manifest) can BLOCK for
    seconds on macOS fskit whenever the stick is busy — during our own export, or
    when the user is copying/clearing files in Finder. Doing it on the main-thread
    timer beachballs the whole app (rainbow wheel) and freezes the FINALIZING
    animation. Running it here keeps the UI responsive no matter what is hammering
    the disk; results are applied on the main thread via `done`.
    """
    done = Signal(list)  # list[tuple[name, path, free, drift]]

    def __init__(self, lib, extra=None):
        super().__init__()
        self.lib = lib
        self.extra = extra or []  # custom SoundBox playlists (not in lib.playlists)

    def run(self):
        out = []
        for name, path, free in list_devices():
            drift = 0
            if self.lib is not None:
                try:
                    dr = engine.device_drift(self.lib, path, self.extra)
                    drift = dr.count() if dr else 0
                except Exception:  # noqa: BLE001 - a bad manifest never breaks the poll
                    drift = 0
            out.append((name, path, free, drift))
        self.done.emit(out)


class DeleteWorker(QThread):
    """Delete files/folders from a device OFF the UI thread, reporting progress.

    Deleting a big track-folder tree over a slow USB blocks for a while; on the main
    thread that's a rainbow cursor with no feedback. Here it drives the same
    progress bar as an export instead. Stops cleanly between items on cancel."""
    progress = Signal(int, int, str)  # done, total, current name
    done = Signal(list)               # list[str] of errors

    def __init__(self, paths: list[Path]):
        super().__init__()
        self.paths = paths
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        errors = []
        total = len(self.paths)
        for i, path in enumerate(self.paths, 1):
            if self._stop:
                break
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
            self.progress.emit(i, total, path.name)
        self.done.emit(errors)


class ArtFillWorker(QThread):
    """Background trickle: fetch missing cover art for the whole library, slowly,
    one track at a time, cached + resumable. Uses the user's curated artist/title
    so matches (and the artist-avatar fallback) keep improving over time."""
    progress = Signal(int, int)  # done, total

    DELAY = 1.6  # seconds between fetches — polite, never a burst

    def __init__(self, lib, cache, artist_overrides, title_overrides):
        super().__init__()
        self.lib = lib
        self.cache = cache
        self.artist_overrides = artist_overrides
        self.title_overrides = title_overrides
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import time
        todo = []
        hidden = getattr(self.lib, "hidden_ids", set())
        for t in self.lib.tracks_by_id.values():
            if t.track_id in hidden:
                continue  # deleted from SoundBox — don't fetch for it
            if self.cache.status(t.track_id) is not None:
                continue  # already fetched or known-miss
            try:
                if not t.path.is_file():
                    continue  # only tracks we can actually export
            except OSError:
                continue
            todo.append(t)
        total = len(todo)
        LOG.info("Art fill: %d tracks need cover art", total)
        ok = err = 0
        for i, t in enumerate(todo, 1):
            if self._stop:
                break
            title = (self.title_overrides or {}).get(t.track_id) or t.name
            artist = metadata.effective_artist(
                t.track_id, title, t.artist, self.artist_overrides, True,
                self.lib.artist_registry, stem=t.path.stem)
            try:
                res = artfetch.fetch_best(artist, title)
            except Exception as exc:  # noqa: BLE001
                res = None
                err += 1
                if err <= 3:
                    LOG.warning("Art fetch failed for %r: %r", title[:40], exc)
            if res:
                self.cache.store_ok(t.track_id, res); ok += 1
            else:
                self.cache.store_none(t.track_id)
            self.progress.emit(i, total)
            time.sleep(self.DELAY)
        LOG.info("Art fill ended: %d fetched, %d failed", ok, err)


class WaveformFillWorker(QThread):
    """Background trickle: pre-decode + cache the waveform for every track whose
    peaks aren't cached yet, so the scrubber is instant when you load one — no
    waiting on a live decode. Local decode only (no network): libsndfile for
    WAV/AIFF/MP3/FLAC, the Qt/ffmpeg path for ALAC/AAC. Cached + resumable; the
    cache key includes mtime+size, so an edited file re-decodes.

    Runs OFF the UI thread and honors requestInterruption() — qt_peaks spins a
    local event loop and polls interruption mid-decode, so shutdown breaks a
    long .m4a cleanly (see soundbox-gotchas: the 3 QAudioDecoder traps)."""
    progress = Signal(int, int)  # done, total

    DELAY = 0.05  # tiny yield between files — gentle on CPU, never a UI beachball

    def __init__(self, lib, cache_dir):
        super().__init__()
        self.lib = lib
        self.cache_dir = cache_dir

    def run(self):
        import time
        from soundbox.waveform import cache_store, is_cached, peaks
        from .player import qt_peaks
        todo = []
        hidden = getattr(self.lib, "hidden_ids", set())
        for t in self.lib.tracks_by_id.values():
            if t.track_id in hidden:
                continue
            try:
                if not t.path.is_file() or is_cached(self.cache_dir, t.path):
                    continue
            except OSError:
                continue
            todo.append(str(t.path))
        total = len(todo)
        LOG.info("Waveform fill: %d track(s) need peaks", total)
        done = 0
        for i, path in enumerate(todo, 1):
            if self.isInterruptionRequested():
                break
            try:
                pk = peaks(path) or qt_peaks(path)  # cheap path first, ffmpeg fallback
                if pk:
                    cache_store(self.cache_dir, path, pk)  # cache_store ignores []
                    done += 1
            except Exception as exc:  # noqa: BLE001 — one bad file never stops the fill
                LOG.debug("Waveform fill: decode failed for %s: %r", path, exc)
            self.progress.emit(i, total)
            time.sleep(self.DELAY)
        LOG.info("Waveform fill ended: %d cached", done)


class OneShotTask(QThread):
    """Run one callable off the UI thread; emits its result — or the exception
    (callers type-check). Used for backup zip/restore (193MB of art shouldn't
    beachball the app)."""
    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            self.done.emit(exc)


class _OneShotArt(QThread):
    """A single art fetch (manual 'fetch now' / paste-URL), off the UI thread."""
    done = Signal(str, object)  # track_id, ArtResult | None | Exception

    def __init__(self, tid, fn):
        super().__init__()
        self.tid = tid
        self.fn = fn

    def run(self):
        try:
            res = self.fn()
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI + log
            # NEVER collapse a failure into "no art found": a network error and
            # a genuinely artless page are different answers (a swallowed
            # exception here once made a working URL look art-less).
            res = exc
        self.done.emit(self.tid, res)


def _running(w) -> bool:
    """True if a worker QThread exists and is still running — safe even if its C++
    object was already deleted (returns False instead of raising)."""
    try:
        return w is not None and w.isRunning()
    except RuntimeError:
        return False

