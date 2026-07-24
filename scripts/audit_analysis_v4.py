#!/usr/bin/env python3
"""Read-only coverage audit for SoundBox's canonical V4 analysis.

The audit combines the last read-only rekordbox snapshot with SoundBox-imported
tracks, including tracks hidden from the browser. It never opens rekordbox and
never writes AppData or source audio. A record counts as ready only when the V4
container, checksum, schema, track id, and current source-file identity all
validate through the production loader.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soundbox import imports, libcache
from soundbox.track_analysis import (
    cache_path as v4_cache_path,
    is_ready,
    load,
)


def audit(data_dir: Path) -> dict:
    library_path = data_dir / "library.json"
    cache_path = data_dir / "libcache.json"
    state = json.loads(library_path.read_text(encoding="utf-8"))
    library = libcache.load(cache_path)
    if library is None:
        raise RuntimeError(f"No valid library snapshot at {cache_path}")
    imports.inject(library, state.get("imported") or {})

    valid_v4: list[str] = []
    current_quality: list[str] = []
    needs_current_analysis: list[str] = []
    missing_analysis: list[str] = []
    missing_source: list[str] = []
    invalid_source: list[str] = []
    for track_id in sorted(library.tracks_by_id, key=str):
        track = library.tracks_by_id[track_id]
        source = Path(track.path)
        try:
            exists = source.is_file()
        except OSError:
            exists = False
        if not exists:
            missing_source.append(str(track_id))
            continue
        try:
            valid = load(
                data_dir / "track_analysis", str(track_id), source,
                compact=True,
            )
        except (OSError, ValueError):
            invalid_source.append(str(track_id))
            continue
        if valid is None:
            missing_analysis.append(str(track_id))
        else:
            valid_v4.append(str(track_id))
            if is_ready(
                    data_dir / "track_analysis", str(track_id), source):
                current_quality.append(str(track_id))
            else:
                needs_current_analysis.append(str(track_id))

    expected_names = {
        v4_cache_path(data_dir / "track_analysis", str(track_id)).name
        for track_id in library.tracks_by_id
    }
    orphan_records = sorted(
        path.name for path in (data_dir / "track_analysis").glob("*.sba4")
        if path.name not in expected_names
    )
    return {
        "data_dir": str(data_dir),
        "total_tracks": len(library.tracks_by_id),
        # ``ready`` remains a compatibility alias for valid/playable V4
        # coverage. Current-quality coverage is deliberately separate: an
        # older V4 record can make legacy-cache retirement safe while still
        # needing the newest ultra/color regeneration.
        "ready": len(valid_v4),
        "valid_v4": len(valid_v4),
        "current_quality": len(current_quality),
        "needs_current_analysis": needs_current_analysis,
        "missing_analysis": missing_analysis,
        "missing_source": missing_source,
        "invalid_source": invalid_source,
        "orphan_v4_records": orphan_records,
        "safe_to_retire_legacy": (
            not missing_analysis and not invalid_source
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=(
            Path.home()
            / "Library/Application Support/LowFreqLabs/Soundbox"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit(args.data_dir.expanduser().resolve())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Tracks: {result['total_tracks']}")
        print(f"Valid/playable V4: {result['valid_v4']}")
        print(f"Current-quality V4: {result['current_quality']}")
        print(
            "Needs current analysis: "
            f"{len(result['needs_current_analysis'])}"
        )
        print(f"Missing V4: {len(result['missing_analysis'])}")
        print(f"Missing source: {len(result['missing_source'])}")
        print(f"Invalid source: {len(result['invalid_source'])}")
        print(f"Orphan V4: {len(result['orphan_v4_records'])}")
        print(
            "Legacy retirement: "
            + ("SAFE" if result["safe_to_retire_legacy"] else "BLOCKED")
        )
    return 0 if result["safe_to_retire_legacy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
