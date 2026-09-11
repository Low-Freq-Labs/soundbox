#!/usr/bin/env python3
"""Read-only coverage and lifecycle audit for canonical SoundBox analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soundbox import imports, libcache
from soundbox.track_analysis import (
    LEGACY_SCHEMA,
    cache_path,
    is_dataless_source,
    is_ready,
    legacy_cache_path,
    load_manifest,
    verify_complete,
)


def audit(data_dir: Path, *, full: bool = False) -> dict:
    """Audit reachable sources plus orphan V4/V5 records without writing."""
    library_path = data_dir / "library.json"
    snapshot_path = data_dir / "libcache.json"
    state = json.loads(library_path.read_text(encoding="utf-8"))
    library = libcache.load(snapshot_path)
    if library is None:
        raise RuntimeError(f"No valid library snapshot at {snapshot_path}")
    imports.inject(library, state.get("imported") or {})
    raw_hidden = state.get("hidden_tracks") or {}
    hidden_ids = {str(track_id) for track_id in raw_hidden}
    active_ids = {
        str(track_id) for track_id in library.tracks_by_id
        if str(track_id) not in hidden_ids
    }

    current_v5: list[str] = []
    playable_legacy_v4: list[str] = []
    missing_analysis: list[str] = []
    missing_source: list[str] = []
    invalid_source: list[str] = []
    deferred_dataless: list[str] = []
    invalid_analysis: list[str] = []
    needs_current_analysis: list[str] = []
    for track_id in sorted(active_ids):
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
            cache = data_dir / "track_analysis"
            manifest = load_manifest(cache, track_id, source)
            if is_ready(cache, track_id, source):
                if full and not verify_complete(cache, track_id, source):
                    invalid_analysis.append(track_id)
                else:
                    current_v5.append(track_id)
            elif manifest is not None:
                if not verify_complete(cache, track_id, source):
                    invalid_analysis.append(track_id)
                else:
                    needs_current_analysis.append(track_id)
                    if manifest["schema"] == LEGACY_SCHEMA:
                        playable_legacy_v4.append(track_id)
            elif is_dataless_source(source):
                deferred_dataless.append(str(track_id))
            else:
                missing_analysis.append(str(track_id))
        except (OSError, ValueError):
            invalid_source.append(str(track_id))

    analysis_dir = data_dir / "track_analysis"
    expected_v5 = {
        cache_path(analysis_dir, str(track_id)).name
        for track_id in active_ids
    }
    expected_v4 = {
        legacy_cache_path(analysis_dir, str(track_id)).name
        for track_id in active_ids
    }
    orphan_v5 = sorted(
        path.name for path in analysis_dir.glob("*.sba5")
        if path.name not in expected_v5
    )
    orphan_v4 = sorted(
        path.name for path in analysis_dir.glob("*.sba4")
        if path.name not in expected_v4
    )
    return {
        "data_dir": str(data_dir),
        "library_tracks": len(library.tracks_by_id),
        "hidden_tracks": len(hidden_ids & {
            str(track_id) for track_id in library.tracks_by_id
        }),
        "total_tracks": len(active_ids),
        "ready": len(current_v5),
        "current_v5": len(current_v5),
        "playable_legacy_v4": len(playable_legacy_v4),
        "needs_current_analysis": needs_current_analysis,
        "missing_analysis": missing_analysis,
        "missing_source": missing_source,
        "invalid_source": invalid_source,
        "invalid_analysis": invalid_analysis,
        "deferred_dataless": deferred_dataless,
        "orphan_v5_records": orphan_v5,
        "orphan_v4_records": orphan_v4,
        "full_verification": full,
        "safe_to_retire_v4": (
            full
            and len(current_v5) == len(active_ids)
            and not orphan_v4
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
    parser.add_argument(
        "--full", action="store_true",
        help="decompress and hash every current V5 section")
    args = parser.parse_args()
    result = audit(args.data_dir.expanduser().resolve(), full=args.full)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Tracks: {result['total_tracks']}")
        print(f"Hidden tracks excluded: {result['hidden_tracks']}")
        print(f"Current verified V5: {result['current_v5']}")
        print(f"Playable legacy V4: {result['playable_legacy_v4']}")
        print(f"Missing analysis: {len(result['missing_analysis'])}")
        print(f"Missing source: {len(result['missing_source'])}")
        print(f"Invalid source: {len(result['invalid_source'])}")
        print(f"Invalid analysis: {len(result['invalid_analysis'])}")
        print(f"Needs current analysis: {len(result['needs_current_analysis'])}")
        print(f"Dataless analysis deferred: {len(result['deferred_dataless'])}")
        print(f"Orphan V5: {len(result['orphan_v5_records'])}")
        print(f"Orphan V4: {len(result['orphan_v4_records'])}")
        print(
            "V4 retirement: "
            + ("SAFE" if result["safe_to_retire_v4"] else "BLOCKED")
        )
    return 0 if result["safe_to_retire_v4"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
