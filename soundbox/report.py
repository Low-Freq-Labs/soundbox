"""Sync reporting. Pure data + printing; no filesystem access."""

from __future__ import annotations

from dataclasses import dataclass, field


def human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


@dataclass
class PlaylistReport:
    name: str
    in_playlist: int = 0       # tracks rekordbox lists
    found: int = 0             # source files present on disk
    missing: int = 0           # source files absent
    copied: int = 0            # files actually copied this run
    already_present: int = 0   # files already on target, identical
    conflicts: int = 0         # target exists, different content (NOT overwritten)
    playlist_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class SyncReport:
    dry_run: bool
    playlists: list[PlaylistReport] = field(default_factory=list)
    bytes_to_copy: int = 0
    # Folders actually written this run (copied-file dirs + playlist dirs). Lets the
    # AppleDouble scrub target only what we touched instead of the whole device.
    written_dirs: set = field(default_factory=set)
    # True when the run was cancelled mid-copy. Playlists still got honest partial
    # m3u8s, and the manifest flags incomplete ones so the next Sync finishes them.
    cancelled: bool = False

    def print(self) -> None:
        mode = "DRY-RUN (nothing written)" if self.dry_run else "SYNC"
        print(f"\n===== Soundbox {mode} report =====")
        for pr in self.playlists:
            print(f"\nPlaylist name        : {pr.name}")
            print(f"Tracks in playlist   : {pr.in_playlist}")
            print(f"Tracks found         : {pr.found}")
            print(f"Tracks missing       : {pr.missing}")
            print(f"Tracks copied        : {pr.copied}")
            print(f"Tracks already present: {pr.already_present}")
            print(f"Conflicts (skipped)  : {pr.conflicts}")
            print(f"Playlist file written: {', '.join(pr.playlist_files) or '(none)'}")
            if pr.errors:
                print("Errors / skipped     :")
                for e in pr.errors:
                    print(f"  - {e}")
            else:
                print("Errors / skipped     : none")
        verb = "would be copied" if self.dry_run else "copied"
        print(f"\nTotal data {verb}: {human(self.bytes_to_copy)}")
