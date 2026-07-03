"""Rockbox .m3u8 playlist generation.

Generates UTF-8 (no BOM) playlist text. Track order is preserved exactly as
given (which is exact rekordbox order). Missing tracks are written as
'# MISSING: ...' comment lines so the playlist documents what rekordbox had
without breaking playback (Rockbox ignores lines starting with '#').

This module builds *strings* and *target paths*. It does not write files; the
sync module owns all filesystem writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import sanitize_component


@dataclass
class M3UEntry:
    """One line in a playlist.

    device_path is the on-device path ('/Music/...') if the source file exists,
    or None if the source is missing (-> written as a MISSING comment).
    """
    device_path: str | None
    artist: str
    name: str


def render_m3u8(entries: list[M3UEntry]) -> str:
    """Render playlist text. UTF-8, '\\n' line endings, '#EXTM3U' header."""
    lines = ["#EXTM3U"]
    for e in entries:
        if e.device_path is None:
            label = f"{e.artist} - {e.name}".strip(" -") or e.name or "unknown"
            # Non-printables (newlines, \x1c-class separators) would split this
            # comment into a bogus playlist entry — flatten them to spaces.
            label = " ".join("".join(ch if ch.isprintable() else " "
                                     for ch in label).split())
            lines.append(f"# MISSING: {label or 'unknown'}")
        else:
            lines.append(e.device_path)
    return "\n".join(lines) + "\n"


def tree_playlist_path(playlists_root: Path, folder_path: list[str], name: str) -> Path:
    """Mirrored layout: /Playlists/<folders...>/<name>.m3u8 (each part sanitized)."""
    parts = [sanitize_component(p) for p in folder_path]
    target = playlists_root
    for part in parts:
        target = target / part
    return target / f"{sanitize_component(name)}.m3u8"


def flat_playlist_path(playlists_root: Path, folder_path: list[str], name: str) -> Path:
    """Flat layout for Rockbox's catalog: /Playlists/<A - B - name>.m3u8."""
    joined = " - ".join(folder_path + [name]) if folder_path else name
    return playlists_root / f"{sanitize_component(joined)}.m3u8"
