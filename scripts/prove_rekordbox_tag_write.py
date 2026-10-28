"""Exercise tag writes on an encrypted private copy, never the source.

All three IDs/paths are explicit. No live-write option exists. The private JSON
report records the candidate for human review, exact semantic changes, encryption
and backup checks, and unchanged source DB/WAL bytes. No audio/ANLZ/XML is read.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soundbox import rekordbox_write as writer  # noqa: E402
from soundbox.rekordbox_db import _snapshot_identity  # noqa: E402
from soundbox.state_io import atomic_bytes  # noqa: E402


def _source_proof(path: Path) -> dict:
    result = {}
    for suffix in ("", "-wal"):
        target = path.with_name(path.name + suffix)
        before = _snapshot_identity(target)
        if before is None:
            result[suffix or "db"] = None
            continue
        sha = hashlib.sha256()
        with target.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(block)
        after = _snapshot_identity(target)
        if before != after:
            raise RuntimeError("Source changed during read-only hashing; retry when quiet")
        result[suffix or "db"] = {"identity": before, "sha256": sha.hexdigest()}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--track-id", required=True)
    parser.add_argument("--tag-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    report = args.report.expanduser().absolute()
    # Reports are developer output only, never alongside the source library.
    build = Path(__file__).resolve().parents[1] / "build"
    if not report.resolve().is_relative_to(build):
        parser.error("--report must be under this checkout's ignored build/ directory")
    if report.is_symlink() or report.resolve().is_relative_to(source.parent):
        parser.error("Report must not alias or overlap source library files")
    before = _source_proof(source)
    workspace = writer.OfflineWorkspace.capture(source)
    succeeded = False
    try:
        conn = writer._connect(workspace.database, readonly=True)
        try:
            track = conn.execute("SELECT Title,UUID FROM djmdContent WHERE ID=?",
                                 (args.track_id,)).fetchone()
            tag = conn.execute("SELECT Name FROM djmdMyTag WHERE ID=?", (args.tag_id,)).fetchone()
        finally:
            conn.close()
        if not track or not tag:
            raise RuntimeError("Explicit track/tag ID does not exist in the copied library")
        initial = workspace._read_state(workspace.database)
        plan = workspace.plan_tag_assignment((args.track_id,), args.tag_id, True)
        original_membership = plan.expected_present[0]
        # Exercise a real change in each direction on the copy, regardless of
        # the selected track's original membership. The real source never opens.
        first = workspace.apply(workspace.plan_tag_assignment(
            (args.track_id,), args.tag_id, not original_membership))
        changed = workspace._read_state(workspace.database)
        second = workspace.apply(workspace.plan_tag_assignment(
            (args.track_id,), args.tag_id, original_membership))
        noop = workspace.apply(workspace.plan_tag_assignment(
            (args.track_id,), args.tag_id, original_membership))
        changed_tables = sorted(name for name in initial["tables"]
                                if initial["tables"][name] != changed["tables"][name])
        after = _source_proof(source)
        if before != after:
            raise RuntimeError("Source DB/WAL changed during the copy proof; no clean proof recorded")
        result = {
            "scope": "offline-copy-only; native rekordbox acceptance NOT performed",
            "source": str(source), "source_unchanged": before == after,
            "source_proof": before, "library_id": workspace.metadata["library_id"],
            "database_version": workspace.metadata["db_version"],
            "candidate": {"track_id": args.track_id, "title": track[0], "track_uuid": track[1],
                          "tag_id": args.tag_id, "tag": tag[0],
                          "originally_assigned": original_membership},
            "changed_tables": changed_tables,
            "schema_unchanged": initial["schema"] == changed["schema"],
            "first_edit": first.outcome, "reverse_edit": second.outcome,
            "repeat_edit": noop.outcome,
            "baseline_encrypted": (workspace.root / "baseline.db").read_bytes()[:16]
                                  != b"SQLite format 3\0",
            "baseline_verified": writer._digest(workspace._read_state(workspace.root / "baseline.db"))
                                 == workspace.metadata["baseline_sha256"],
            "remaining_transaction": workspace.transaction.exists(),
            "no_source_audio_analysis_or_xml_reads": True,
        }
        if (changed_tables != ["agentRegistry", "djmdSongMyTag"]
                or not result["schema_unchanged"] or not result["baseline_verified"]
                or (first.outcome, second.outcome, noop.outcome) != ("changed", "changed", "unchanged")):
            raise RuntimeError("Offline proof did not satisfy its exact-delta contract")
        atomic_bytes(report, writer._json(result))
        succeeded = True
        print(f"Offline tag proof passed; source unchanged. Private evidence: {report}")
    finally:
        if succeeded:
            # Fresh disposable copy only; no arbitrary target or retained recovery.
            shutil.rmtree(workspace.root)
        else:
            print(f"Offline proof failed; preserved evidence at {workspace.root}", file=sys.stderr)


if __name__ == "__main__":
    main()
