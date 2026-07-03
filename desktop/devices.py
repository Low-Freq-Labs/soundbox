"""External-device detection & helpers (macOS): diskutil probing, the
external-only safety guards, filesystem type (Rockbox needs FAT32), free space.

Everything here is read-only probing — no device writes.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

MAX_DEVICES = 3


def human(n) -> str:
    # Decimal units (1 GB = 1000 MB) to match macOS Finder's free-space display.
    if n is None:
        return "?"
    f = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if f < 1000 or u == "TB":
            return f"{f:.1f} {u}"
        f /= 1000
    return f"{f:.1f} TB"


# diskutil is slow; a volume's external-ness never changes, so cache per path.
# We ONLY cache a definitive answer from a SUCCESSFUL diskutil probe. A transient
# failure (diskutil timed out because the stick was busy, a momentary hiccup) must
# NOT be cached as False — doing that permanently dropped a still-mounted USB from
# the list until the app restarted (the "USB disappeared overnight" bug).
_EXTERNAL_CACHE: dict[str, bool] = {}
# Filesystem type per volume, captured from the same successful diskutil probe.
# Rockbox devices MUST be FAT32 ("msdos") — HFS+/exFAT iPods won't run Rockbox,
# so the sidebar warns the moment a non-FAT device mounts.
_FS_CACHE: dict[str, str] = {}


def _is_external(path: Path) -> bool:
    """True only for external/ejectable volumes (USB/iPod/MP3) — never system.

    Caches only the result of a successful probe (external-ness is stable per mount).
    A failed/timed-out probe returns False WITHOUT caching, so the next poll retries
    and a busy-but-present device reappears on its own."""
    key = str(path)
    if key in _EXTERNAL_CACHE:
        return _EXTERNAL_CACHE[key]
    try:
        p = subprocess.run(["diskutil", "info", "-plist", key],
                           capture_output=True, timeout=5)
    except Exception:
        return False  # transient (timeout/busy) — DON'T cache; retry next poll
    if p.returncode != 0:
        return False  # probe failed (volume busy?) — DON'T cache; retry next poll
    try:
        info = plistlib.loads(p.stdout)
    except Exception:
        return False  # unparseable output — DON'T cache; retry next poll
    result = (info.get("Internal") is not True
              and bool(info.get("Internal") is False
                       or info.get("Ejectable") or info.get("RemovableMedia")))
    _EXTERNAL_CACHE[key] = result  # definitive answer from a good probe — safe to keep
    _FS_CACHE[key] = info.get("FilesystemType") or ""
    return result


def device_fs(path) -> str:
    """Filesystem type of a probed volume ('msdos'=FAT, 'exfat', 'apfs', 'hfs', '')."""
    return _FS_CACHE.get(str(path), "")


def _external_volume_of(p):
    """The external/ejectable volume that p lives on (or IS), else None. Resolves
    symlinks first, so a link out of the device can't sneak past. Rejects system
    volumes (e.g. /Volumes/Macintosh HD) and anything outside /Volumes. Allows the
    mount root itself (fine for creating a folder there)."""
    try:
        rp = Path(p).resolve()
    except OSError:
        return None
    parts = rp.parts  # e.g. ('/', 'Volumes', 'IPOD-TEST', 'Music', 'x')
    if len(parts) < 3 or parts[1] != "Volumes":
        return None
    vol = Path("/Volumes") / parts[2]
    return vol if _is_external(vol) else None


def _on_external_device(p) -> bool:
    """True ONLY if p is a real item STRICTLY inside an external device (never the
    mount root, never a system volume, never reachable via a symlink out). The guard
    for deletes — a path-prefix check alone would let /Volumes/Macintosh HD or a
    mount root through."""
    try:
        rp = Path(p).resolve()
    except OSError:
        return False
    vol = _external_volume_of(rp)
    return vol is not None and rp != vol  # strictly inside, not the mount root


def list_devices() -> list[tuple[str, str, int | None]]:
    out = []
    vol = Path("/Volumes")
    if vol.is_dir():
        present = {str(vol / n.name) for n in vol.iterdir()}
        # Forget cache entries for volumes no longer mounted, so a re-plugged stick
        # (same name) is probed fresh instead of trusting a stale answer.
        for stale in [k for k in _EXTERNAL_CACHE if k not in present]:
            _EXTERNAL_CACHE.pop(stale, None)
            _FS_CACHE.pop(stale, None)
        for d in sorted(vol.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not _is_external(d):
                continue
            try:
                st = os.statvfs(d)
                free = st.f_bavail * st.f_frsize
            except OSError:
                free = None
            out.append((d.name, str(d), free))
    return out[:MAX_DEVICES]

