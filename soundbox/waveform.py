"""Real audio waveform peaks for the player scrubber.

Decodes a local audio file (WAV / AIFF / MP3 / FLAC via libsndfile) into a small
list of normalized peak amplitudes (0..1) — one per horizontal bucket — for a flat
waveform display. Read-only; never modifies the audio file. Returns [] on any
failure (unsupported format like ALAC/.m4a, missing file, decode error) so the UI
can fall back to a plain seek line.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_BUCKETS = 700


def peaks(path, buckets: int = DEFAULT_BUCKETS) -> list[float]:
    """Normalized peak amplitude (0..1) per bucket across the whole track."""
    try:
        import numpy as np
        import soundfile as sf
    except Exception:  # noqa: BLE001 - deps missing => flat waveform
        return []
    try:
        p = Path(path)
        if not p.is_file():
            return []
        with sf.SoundFile(str(p)) as f:
            frames = len(f)
            if frames <= 0:
                return []
            block = max(1, frames // max(1, buckets))
            out: list[float] = []
            while len(out) < buckets:
                data = f.read(block, dtype="float32", always_2d=True)
                if len(data) == 0:
                    break
                out.append(float(np.abs(data).max()) if data.size else 0.0)
        if not out:
            return []
        hi = max(out) or 1.0
        return [min(1.0, v / hi) for v in out]  # normalize so it fills the bar
    except Exception:  # noqa: BLE001 - any decode issue => flat waveform
        return []


def cache_key(path: str | Path) -> str:
    """Waveform cache key: content identity via path+mtime+size (no decode)."""
    import hashlib
    p = Path(path)
    st = p.stat()
    raw = f"{p}|{st.st_mtime_ns}|{st.st_size}".encode()
    return hashlib.sha1(raw).hexdigest()


def cache_load(cache_dir: str | Path, path: str | Path) -> list[float] | None:
    """Cached peaks for path, or None. Any problem (missing, corrupt, file
    changed since caching) reads as a miss — never an error."""
    try:
        f = Path(cache_dir) / f"{cache_key(path)}.json"
        import json
        pk = json.loads(f.read_text())
        return pk if isinstance(pk, list) and pk else None
    except Exception:  # noqa: BLE001
        return None


def cache_store(cache_dir: str | Path, path: str | Path, pk: list[float]) -> None:
    """Persist peaks (atomic; ~5KB each). Failures are silently ignored —
    the cache is a pure accelerator, never load-bearing."""
    if not pk:
        return
    try:
        import json
        import os
        d = Path(cache_dir)
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{cache_key(path)}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps([round(v, 3) for v in pk]))
        os.replace(tmp, f)
    except Exception:  # noqa: BLE001
        pass
