"""Read-only Rockbox theme and optional database observations.

Firmware preferences belong exclusively to the user and Rockbox.
"""

from __future__ import annotations

from pathlib import Path
import re

from .manifest import manifest_path


CONFIG_RELATIVE = Path(".rockbox/config.cfg")
_THEME_KEYS = ("wps", "sbs")
_ALBUM_ART = re.compile(
    r"%Cl\(\s*[^,\r\n()]+\s*,\s*[^,\r\n()]+\s*,"
    r"\s*(\d+)\s*,\s*(\d+)(?:\s*,[^)\r\n]*)?\s*\)")
_MAX_THEME_BYTES = 1024 * 1024


def _setting_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or ":" not in stripped:
        return ""
    return stripped.split(":", 1)[0].strip().casefold()


def _setting_values(payload: bytes) -> dict[str, str]:
    text = payload.decode("utf-8")
    values: dict[str, str] = {}
    for line in text.splitlines():
        key = _setting_key(line)
        if key and ":" in line:
            values[key] = line.split(":", 1)[1].strip()
    return values


def theme_album_art_sides(root: str | Path) -> tuple[int, ...]:
    """Read exact square album-art sizes requested by the active WPS/SBS.

    Theme files are firmware configuration, not SoundBox state. Reads are
    bounded below ``.rockbox`` and malformed/dynamic requests simply fall back
    to the universal per-track cover.
    """
    target = Path(root)
    rockbox = target / ".rockbox"
    config = target / CONFIG_RELATIVE
    try:
        values = _setting_values(config.read_bytes())
        rockbox_resolved = rockbox.resolve(strict=True)
    except (OSError, UnicodeError):
        return ()
    sides: set[int] = set()
    for key in _THEME_KEYS:
        raw = values.get(key, "")
        if not raw or raw == "-" or "\\" in raw:
            continue
        candidate = target / raw.lstrip("/")
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(rockbox_resolved):
                continue
            if not resolved.is_file() or resolved.stat().st_size > _MAX_THEME_BYTES:
                continue
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for width_text, height_text in _ALBUM_ART.findall(text):
            width, height = int(width_text), int(height_text)
            if width == height and 1 <= width <= 240:
                sides.add(width)
    return tuple(sorted(sides, reverse=True))


def database_auto_update_enabled(root: str | Path) -> bool:
    """Observe the saved preference; absent/unreadable settings default to off."""
    try:
        values = _setting_values((Path(root) / CONFIG_RELATIVE).read_bytes())
    except (OSError, UnicodeError):
        return False
    return values.get("tagcache_autoupdate", "off").casefold() == "on"


def database_file_count(root: str | Path) -> int:
    """Count only Rockbox's documented rebuildable database TCD files."""
    rockbox = Path(root) / ".rockbox"
    try:
        return sum(
            1 for path in rockbox.glob("database_*.tcd") if path.is_file())
    except OSError:
        return 0


def database_refresh_pending(root: str | Path) -> bool:
    """Whether Rockbox's index predates SoundBox's latest managed state.

    SoundBox never interprets or patches TCD payloads. This bounded timestamp
    comparison only avoids presenting an old firmware-owned index as current
    after a SoundBox commit. FAT timestamps have two-second granularity, so
    equal or near-equal mtimes are treated as reconciled.
    """
    target = Path(root)
    index = target / ".rockbox/database_idx.tcd"
    inputs = (manifest_path(target), target / CONFIG_RELATIVE)
    try:
        newest_input = max(
            path.stat().st_mtime for path in inputs if path.is_file())
    except (OSError, ValueError):
        return False
    try:
        index_mtime = index.stat().st_mtime
    except OSError:
        return True
    return index_mtime + 2.0 < newest_input
