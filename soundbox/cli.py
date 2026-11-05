"""Soundbox CLI — a maintenance/debug tool. The desktop app is the product.

Works from a rekordbox XML export (the desktop app reads the live database;
the CLI predates that and stays XML-based on purpose — it is handy for scripted
checks with no GUI). Commands:
  inspect    read-only: playlist tree + track counts, unresolved keys
  preview    read-only: which source files exist, real copy size
  dry-run    plan a sync; writes NOTHING
  sync       copy files + write playlists under the target folder ONLY
  validate   re-read generated .m3u8s and report missing referenced files
Note: the CLI path does not apply desktop-only features (title tidying,
deletes/removals, cover art, device manifest).
"""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from . import __version__, rekordbox_xml, sync as sync_mod


def _cmd_inspect(args: argparse.Namespace) -> int:
    lib = rekordbox_xml.load(args.xml)

    print(f"Soundbox v{__version__}  (READ-ONLY inspect)")
    print(f"Source XML : {lib.source_xml}")
    print(f"Collection : {lib.track_count} tracks")
    print(f"Playlists  : {len(lib.playlists)}")
    print("-" * 60)

    total_refs = 0
    total_resolved = 0
    total_missing = 0
    for pl in lib.playlists:
        resolved = lib.resolve(pl)
        missing = len(pl.effective_keys) - len(resolved)
        total_refs += len(pl.effective_keys)
        total_resolved += len(resolved)
        total_missing += missing
        flag = f"  [!! {missing} unresolved key(s)]" if missing else ""
        print(f"{pl.display_path}  ({len(pl.effective_keys)} tracks){flag}")

    print("-" * 60)
    print(
        f"Totals: {total_refs} playlist entries, "
        f"{total_resolved} resolved to collection tracks, "
        f"{total_missing} unresolved."
    )
    print("\nNothing was written. This command only reads the XML export.")
    return 0


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def _select_playlists(lib, names: list[str] | None):
    """Return playlists matching the given names (by playlist name OR full
    display path). No names => all playlists. Read-only selection."""
    if not names:
        return list(lib.playlists)
    wanted = set(names)
    selected = [
        pl for pl in lib.playlists
        if pl.name in wanted or pl.display_path in wanted
    ]
    return selected


def _cmd_preview(args: argparse.Namespace) -> int:
    """READ-ONLY. Decode source paths, check which files exist, report sizes.

    Reads the XML export and stat()s source files. It never opens, copies,
    moves, or modifies any source file, and it writes nothing.
    """
    lib = rekordbox_xml.load(args.xml)
    playlists = _select_playlists(lib, args.playlists)

    print(f"Soundbox v{__version__}  (READ-ONLY preview — nothing is written)")
    print(f"Source XML : {lib.source_xml}")
    print(f"Playlists  : {len(playlists)} selected")
    print("=" * 70)

    # Track which unique source files we'd actually need to copy (dedup across
    # playlists), so the size total reflects real copy size, not double-counts.
    unique_found: dict[str, int] = {}     # path -> size
    unique_missing: set[str] = set()
    missing_examples: list[str] = []

    for pl in playlists:
        tracks = lib.resolve(pl)
        found = missing = 0
        pl_bytes = 0
        for t in tracks:
            p = t.path
            try:
                exists = p.is_file()
            except OSError:
                exists = False
            key = str(p)
            if exists:
                size = p.stat().st_size
                found += 1
                pl_bytes += size
                unique_found.setdefault(key, size)
            else:
                missing += 1
                if key not in unique_missing:
                    unique_missing.add(key)
                    if len(missing_examples) < 25:
                        missing_examples.append(f"{t.artist} - {t.name}  ->  {key}")
        flag = f"   [MISSING {missing}]" if missing else ""
        print(f"{pl.display_path}  ({len(tracks)} trk, {found} found, {_human(pl_bytes)}){flag}")

    print("=" * 70)
    unique_bytes = sum(unique_found.values())
    print(f"Unique source files found  : {len(unique_found)}  ({_human(unique_bytes)} to copy)")
    print(f"Unique source files MISSING: {len(unique_missing)}")
    if missing_examples:
        print("\nMissing (showing up to 25):")
        for line in missing_examples:
            print(f"  - {line}")
        if len(unique_missing) > len(missing_examples):
            print(f"  ...and {len(unique_missing) - len(missing_examples)} more.")
    print("\nNothing was written or modified. Read-only preview.")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    """Handles both `dry-run` (dry_run=True) and `sync` (dry_run=False)."""
    dry = args.dry_run
    lib = rekordbox_xml.load(args.xml)
    selected = _select_playlists(lib, args.playlists)
    if not selected:
        print("No matching playlists. Use `inspect` to see exact names.")
        return 1

    target = Path(args.target).expanduser()
    mode = "DRY-RUN" if dry else "SYNC"
    print(f"Soundbox v{__version__}  ({mode})")
    print(f"Source XML : {lib.source_xml}")
    print(f"Target     : {target}")
    print(f"Playlists  : {len(selected)} selected")
    print(f"Layout     : {args.layout}")

    try:
        report = sync_mod.run(
            lib, selected, target, layout=args.layout, dry_run=dry,
            derive_artists=args.derive_artists,
        )
    except RuntimeError as exc:  # safety guard tripped
        print(f"\n{exc}")
        return 2

    report.print()

    # Post-sync: scrub macOS AppleDouble (._*) clutter from our folders only.
    if not dry and args.clean:
        removed = sync_mod.clean_applefiles(target)
        print(f"\nCleaned {removed} macOS AppleDouble (._*) file(s) from Music/ and Playlists/.")

    if dry:
        print("\nThis was a DRY-RUN. Nothing was written. Re-run with `sync` to copy.")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Read generated .m3u8 files under target and report missing referenced files."""
    target = Path(args.target).expanduser()
    playlists_dir = target / "Playlists"
    if not playlists_dir.is_dir():
        print(f"No Playlists folder at {playlists_dir}")
        return 1

    total = missing = 0
    for m3u in sorted(playlists_dir.rglob("*.m3u8")):
        # Skip macOS AppleDouble sidecars (._foo.m3u8) and other dotfiles —
        # these are binary metadata files auto-created on FAT volumes.
        if m3u.name.startswith("._"):
            continue
        bad: list[str] = []
        for line in m3u.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            total += 1
            # Device-absolute '/Music/...' is resolved relative to target root.
            ref = target / line.lstrip("/")
            if not ref.is_file():
                missing += 1
                bad.append(line)
        if bad:
            print(f"\n{m3u.relative_to(target)}  [{len(bad)} missing]")
            for b in bad:
                print(f"  - {b}")
    print(f"\nChecked {total} entries across playlists. Missing: {missing}.")
    return 0 if missing == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soundbox",
        description="Safe, read-only-first bridge from rekordbox XML to Rockbox/iPod.",
    )
    parser.add_argument("--version", action="version", version=f"soundbox {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser(
        "inspect", help="List playlists and track counts from a rekordbox XML export (read-only)."
    )
    p_inspect.add_argument("--xml", required=True, help="Path to a rekordbox XML export.")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_preview = sub.add_parser(
        "preview",
        help="Read-only: check that source files exist and report sizes (no writes).",
    )
    p_preview.add_argument("--xml", required=True, help="Path to a rekordbox XML export.")
    p_preview.add_argument(
        "--playlists", nargs="*", default=None,
        help="Playlist names (or full 'A / B / C' paths). Omit for all playlists.",
    )
    p_preview.set_defaults(func=_cmd_preview)

    # Shared args for dry-run / sync.
    def add_sync_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--xml", required=True, help="Path to a rekordbox XML export.")
        p.add_argument("--target", required=True, help="Target folder (e.g. /Volumes/USB).")
        p.add_argument("--playlists", nargs="*", default=None,
                       help="Playlist names or full paths. Omit for all.")
        p.add_argument("--layout", choices=["tree", "flat", "both"], default="both",
                       help="How to lay out playlists under /Playlists (default: both).")
        p.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True,
                       help="Remove macOS ._* sidecar clutter after sync (default: on).")
        p.add_argument("--derive-artists", action=argparse.BooleanOptionalAction, default=True,
                       help="Guess artist from 'Artist - Title' names when the tag is blank (default: on).")

    p_dry = sub.add_parser("dry-run", help="Show exactly what would be copied/written. Writes NOTHING.")
    add_sync_args(p_dry)
    p_dry.set_defaults(func=_cmd_sync, dry_run=True)

    p_sync = sub.add_parser("sync", help="Copy files and write playlists to the target folder ONLY.")
    add_sync_args(p_sync)
    p_sync.set_defaults(func=_cmd_sync, dry_run=False)

    p_val = sub.add_parser("validate", help="Check generated playlists for missing referenced files.")
    p_val.add_argument("--target", required=True, help="Target folder to validate.")
    p_val.set_defaults(func=_cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
