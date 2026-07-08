"""Pure path helpers. No filesystem writes happen in this module.

These functions only transform strings / inspect paths. They are the easiest
part to unit-test, which is why they live on their own.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


def file_landed_time(path: str | Path) -> float:
    """When this file first landed on THIS computer, as a POSIX timestamp.

    Uses the filesystem BIRTH time (`st_birthtime` on macOS/APFS — the true
    creation date, set when the file was written here, regardless of the audio's
    original release or when rekordbox catalogued it). Falls back to `st_ctime`
    where birthtime is unavailable (Linux ext4), then `st_mtime`. Returns 0.0
    for a missing/unreadable file so it sorts oldest. Read-only stat; the file
    is never touched. This is the one true "date added to the computer" that
    works identically for rekordbox tracks and imported (non-rekordbox) files."""
    try:
        st = os.stat(path)
    except OSError:
        return 0.0
    bt = getattr(st, "st_birthtime", 0.0) or 0.0
    return float(bt) or float(st.st_ctime or st.st_mtime or 0.0)

# rekordbox encodes track locations as URIs like:
#   file://localhost/Users/me/Music/Some%20Track%20(VIP).flac
# We must (1) drop the scheme/host, (2) percent-decode the rest.
_FILE_PREFIXES = ("file://localhost/", "file:///", "file://")


def decode_location(location: str) -> Path:
    """Turn a rekordbox ``Location`` URI into a real local filesystem path.

    This is a pure transformation. It does NOT check whether the file exists
    and it does NOT touch the filesystem.
    """
    raw = location.strip()
    # Strip the file:// scheme + optional localhost host, then percent-decode.
    for prefix in _FILE_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix) - 1:]  # keep the leading "/"
            break
    else:
        # Fall back to urlparse for anything unexpected.
        parsed = urlparse(raw)
        raw = parsed.path or raw
    return Path(unquote(raw))


# Characters illegal in FAT32/exFAT filenames (the iPod's filesystem).
# Also covers ':' which macOS shows as '/' in Finder and '/' itself.
_ILLEGAL = '<>:"/\\|?*'
_ILLEGAL_TABLE = {ord(c): "-" for c in _ILLEGAL}
_CONTROL_TABLE = {c: "-" for c in range(0x20)}  # control chars 0x00-0x1F


def sanitize_component(name: str, max_len: int = 120) -> str:
    """Make a single path component safe for FAT32/exFAT (the iPod).

    Replaces illegal characters with '-', strips control chars, trims trailing
    dots/spaces (which FAT silently drops), and CLAMPS length — FAT32 caps a
    component at 255 chars and Rockbox's total MAX_PATH is 260, so unbounded
    names would fail on-device. Preserves Unicode. Returns a non-empty string
    ('_' if everything was stripped).
    """
    cleaned = name.translate(_ILLEGAL_TABLE).translate(_CONTROL_TABLE)
    # Any remaining non-printable (Unicode line/paragraph separators \x85,
    # \u2028/\u2029, stray format chars) also becomes '-' — they'd break the
    # m3u8 (its path line would literally split) and confuse FAT tooling.
    cleaned = "".join(ch if ch.isprintable() else "-" for ch in cleaned)
    cleaned = cleaned.rstrip(" .").strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .-_")
    return cleaned or "_"


# Device-path budget (see device_music_path): with these caps the WORST-case
# full path stays comfortably under Rockbox's MAX_PATH (260). The UI enforces
# the same caps on manual title/artist edits, so what the user types in the
# app is exactly what lands on the device — never silently different.
ARTIST_MAX = 60
TITLE_MAX = 80


def device_music_path(source: Path, artist: str, music_root: str = "Music",
                      name_override: str | None = None) -> str:
    """Build the on-device (Rockbox) path for a copied track.

    Layout: /<music_root>/<Artist>/<Track>/<Track.ext> — every track gets its
    OWN folder so Rockbox can show a per-track cover.jpg (works for all formats,
    including WAV). Artist and filename are sanitized for FAT32; the original
    extension is preserved. If name_override is given (a user title edit or an
    auto-tidied title), the stem becomes that instead of the source stem.
    Returns a device-absolute POSIX path like '/Music/Artist/Track/Track.flac'.
    """
    # Tight per-part budget so the FULL device path stays under Rockbox's
    # MAX_PATH (260): "/Music/" + artist(≤60) + track-folder(≤80) + file(≤80+ext)
    # ≈ 234 worst-case. Truncation collisions are already handled downstream
    # (sync appends a short source-hash suffix on device-path collisions).
    artist_dir = (sanitize_component(artist, max_len=ARTIST_MAX)
                  if artist.strip() else "Unknown Artist")
    stem = (name_override or "").strip() or source.stem
    stem = sanitize_component(stem, max_len=TITLE_MAX)
    filename = stem + source.suffix
    # Each track gets its OWN folder so Rockbox can read a per-track cover.jpg
    # (works for every format, incl. WAV): /Music/<Artist>/<Track>/<Track.ext>
    return f"/{music_root}/{artist_dir}/{stem}/{filename}"


def is_within(child: Path, parent: Path) -> bool:
    """True if ``child`` is the same as or nested inside ``parent``.

    Used as a safety guard so we never write a sync target into the source
    music library or a rekordbox folder. Pure path math; no I/O beyond
    resolving the paths.
    """
    try:
        c = child.expanduser().resolve()
        p = parent.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return c == p or p in c.parents
