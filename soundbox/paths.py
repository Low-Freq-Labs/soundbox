"""Pure path helpers. No filesystem writes happen in this module.

These functions only transform strings / inspect paths. They are the easiest
part to unit-test, which is why they live on their own.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import unicodedata
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

# rekordbox/CDJ device exports own this top-level tree. SoundBox may observe
# whole-volume capacity, but no managed path or ordinary device transaction may
# acquire authority below it. FAT paths are case-insensitive, so compare folded.
_EXTERNAL_DEVICE_ROOTS = frozenset({"pioneer"})


def is_external_device_path(path: str | Path) -> bool:
    """Whether a device-relative path belongs to another product's root.

    Accepts manifest spelling (``/PIONEER/...``) or a relative ``Path``. This
    is purely lexical and performs no filesystem access.
    """
    parts: list[str] = []
    for part in str(path).replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return bool(parts and parts[0].casefold() in _EXTERNAL_DEVICE_ROOTS)


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
_CONTROL_TABLE = {c: "-" for c in range(0x20)}  # control chars 0x00-0x1F
_DOS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_LATIN_FALLBACKS = str.maketrans({
    "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe",
    "Ø": "O", "ø": "o", "Ł": "L", "ł": "l",
    "Ð": "D", "ð": "d", "Đ": "D", "đ": "d",
    "Þ": "TH", "þ": "th", "ß": "ss",
    "Ǝ": "E", "ǝ": "e", "Ə": "E", "ə": "e",
    "ı": "i", "Ħ": "H", "ħ": "h",
})


def _transliterate_latin(value: str) -> str:
    """ASCII-fold Latin glyphs without destroying non-Latin names."""
    out = []
    for char in value.translate(_LATIN_FALLBACKS):
        if ord(char) < 128 or "LATIN" not in unicodedata.name(char, ""):
            out.append(char)
            continue
        decomposed = unicodedata.normalize("NFKD", char)
        ascii_text = "".join(
            item for item in decomposed
            if ord(item) < 128 and item.isprintable()
        )
        out.append(ascii_text or char)
    return "".join(out)


def sanitize_component(name: str, max_len: int = 120) -> str:
    """Make a single path component safe for FAT32/exFAT (the iPod).

    Uses compatibility normalization and readable Latin transliteration, turns path
    separators into readable dashes, removes decorative ``*``/``?`` rather
    than leaving ugly placeholders, and protects DOS device names. Latin
    lookalikes stay readable (``CØSTA`` becomes ``COSTA``, never ``C_STA``);
    non-Latin names are preserved. The result is length-clamped for FAT32 and
    Rockbox's 260-character MAX_PATH.
    """
    cleaned = _transliterate_latin(unicodedata.normalize("NFKC", name or ""))
    cleaned = cleaned.translate(_CONTROL_TABLE)
    cleaned = cleaned.replace("<", "(").replace(">", ")").replace('"', "'")
    cleaned = re.sub(r"\s*[/\\|]+\s*", " - ", cleaned)
    cleaned = cleaned.replace(":", "-")
    cleaned = cleaned.replace("?", "").replace("*", "")
    # Any remaining non-printable (Unicode line/paragraph separators \x85,
    # \u2028/\u2029, stray format chars) also becomes '-' — they'd break the
    # m3u8 (its path line would literally split) and confuse FAT tooling.
    cleaned = "".join(ch if ch.isprintable() else "-" for ch in cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    if not cleaned:
        return "_"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .-_")
    root, dot, suffix = cleaned.partition(".")
    if root.upper() in _DOS_RESERVED:
        if len(cleaned) < max_len:
            cleaned = f"{root}-{dot}{suffix}"
        else:
            cleaned = f"{root[:-1]}-{dot}{suffix}"
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
    OWN folder so Rockbox can show per-track external art (works for all formats,
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
    # Each track gets its OWN folder so Rockbox can read per-track external art
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
