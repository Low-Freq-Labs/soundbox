"""Narrow, lossless management of Rockbox settings required by SoundBox.

Rockbox remains the authority for its binary tag cache.  SoundBox enables
Rockbox's own boot-time updater, asks the playlist viewer to read the tags
already written into disposable device payloads, and keeps the playlist buffer
large enough for full-library catalogues.  Every unrelated config line is
preserved byte-for-byte after UTF-8 decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .manifest import manifest_path


CONFIG_RELATIVE = Path(".rockbox/config.cfg")
MIN_PLAYLIST_FILES = 10_000
_PLAYLIST_LIMIT_KEY = "max files in playlist"
# Rockbox 4.0 has no "sort playlists" setting. Its config save drops unknown
# keys; requiring that legacy SoundBox line causes a false Sync after every boot.
# Keep existing unknown lines untouched, but never add or require this one.
REQUIRED_SETTINGS = (
    ("tagcache_autoupdate", "on"),
    ("playlist viewer track display", "title from tags"),
    (_PLAYLIST_LIMIT_KEY, str(MIN_PLAYLIST_FILES)),
)
_THEME_KEYS = ("wps", "sbs")
_ALBUM_ART = re.compile(
    r"%Cl\(\s*[^,\r\n()]+\s*,\s*[^,\r\n()]+\s*,"
    r"\s*(\d+)\s*,\s*(\d+)(?:\s*,[^)\r\n]*)?\s*\)")
_MAX_THEME_BYTES = 1024 * 1024


class RockboxConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RockboxConfigPlan:
    relative_path: Path
    payload: bytes
    payload_before: bytes | None
    sha256_before: str | None

    @property
    def changed(self) -> bool:
        return self.payload != self.payload_before


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


def desired_payload(payload: bytes | None) -> bytes:
    """Return config bytes with only SoundBox's required values changed."""
    try:
        text = (payload or b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RockboxConfigError("Rockbox config is not valid UTF-8") from exc
    lines = text.splitlines(keepends=True)
    newline = next(
        (
            "\r\n" if line.endswith("\r\n")
            else "\n" if line.endswith("\n")
            else "\r"
            for line in lines
            if line.endswith(("\r\n", "\n", "\r"))
        ),
        "\n",
    )
    required = {key.casefold(): (key, value) for key, value in REQUIRED_SETTINGS}
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        if line.endswith("\r\n"):
            content, ending = line[:-2], "\r\n"
        elif line.endswith(("\n", "\r")):
            content, ending = line[:-1], line[-1:]
        else:
            content, ending = line, ""
        key = _setting_key(content)
        desired = required.get(key)
        if desired is None:
            output.append(line)
            continue
        if key in seen:
            continue
        if key == _PLAYLIST_LIMIT_KEY:
            try:
                current_limit = int(content.split(":", 1)[1].strip())
            except (IndexError, ValueError):
                current_limit = 0
            if current_limit >= MIN_PLAYLIST_FILES:
                output.append(line)
                seen.add(key)
                continue
        output.append(f"{desired[0]}: {desired[1]}{ending}")
        seen.add(key)
    for folded, (key, value) in required.items():
        if folded not in seen:
            if output and not output[-1].endswith(("\r\n", "\n", "\r")):
                output[-1] += newline
            output.append(f"{key}: {value}{newline}")
    return "".join(output).encode("utf-8")


def plan(root: str | Path) -> RockboxConfigPlan:
    target = Path(root).expanduser().resolve(strict=True)
    rockbox = target / ".rockbox"
    if rockbox.is_symlink() or not rockbox.is_dir():
        raise RockboxConfigError("Rockbox config requires an installed .rockbox directory")
    path = target / CONFIG_RELATIVE
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RockboxConfigError("Rockbox config path is not a regular file")
    before = path.read_bytes() if path.is_file() else None
    return RockboxConfigPlan(
        CONFIG_RELATIVE,
        desired_payload(before),
        before,
        hashlib.sha256(before).hexdigest() if before is not None else None,
    )


def settings_ready(root: str | Path) -> bool:
    """Bounded read-only check used by drift scanning and the dashboard."""
    try:
        item = plan(root)
    except (OSError, RockboxConfigError):
        return False
    return not item.changed


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
