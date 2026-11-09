# SoundBox local CDJ renderer

Pinned rbxport revision `b739d369665b492534dc2eae0733322c040a26d5`,
GPL-2.0-or-later. Upstream crate sources/notices are retained under `vendor/rbxport`.
The only crate-manifest change explicitly selects their nested workspace; the
upstream workspace omits its Tauri member. `scripts/check_cdj_vendor.py` verifies
243 upstream crate files with that manifest adaptation normalized. Upstream
Markdown is preserved verbatim and describes the pinned upstream tree. Links to
upstream documentation or crates omitted from this vendor subset may not resolve
locally; do not modify the pinned files to repair those links.
Full upstream documentation: <https://github.com/chrisle/rbxport>.

`./build_cdj.sh` builds with the existing isolated Rust1.99 toolchain and locked
dependencies. `--test` runs release-profile library and binary regressions,
including pagination/merge checks, plus the research-adapter tests. The fixture
adapter shares `complete_metadata`; it no longer has a separate one-page writer.

The executable accepts only explicit local workspaces. It renders both library
formats, completes verified native fields, preserves native browse/color settings,
and inspects/merges copied native databases. The shipped executable is the optimized
release profile under build/cdj/release. Incremental rendering accepts only an
exact audio-only Contents set already captured inside its disposable local output;
no metadata, linked paths or other existing files are admitted. It never discovers mounts, opens the live
source library or writes a USB. Python captures inputs and owns authorized
publication/recovery through the dedicated generic-FAT32/exFAT CDJ session.
The advisory workspace lock `.rbxport-write.lock` is removed after successful
initialization; it is not a player asset or part of the USB candidate inventory.

Selected merges validate the requested playback records and exact memberships
in both native formats while preserving each format's unrelated baseline; a
pre-existing whole-catalog difference is not an export veto. Allocation reserves
both namespaces. Removed selected members are checked against remaining playlist
references in both formats, and shared/unrelated assets survive. The parent
transaction retains affected originals until durable commit, then retires exact
removed-media backups to reclaim space. Plus-only display remains supported.

Source packaging includes the executable and upstream license/full GPL text, not
private fixtures. Distribution review and the wider CDJ model/firmware/control
acceptance matrix remain open. Build163's FAT32/exFAT fixture writes, selected
updates and zero-write repeats are bounded evidence in the
[November4 physical USB timing record](../../docs/performance-audit.md#physical-usb-fixture-timings--november4),
not certification of player behavior or full-library performance. Exact dated
receipts remain under `~/Library/Logs/Soundbox/usb-simplification-2026-11-04/`;
the later USB audit uses `~/Library/Logs/Soundbox/usb-deep-audit-2026-11-04/`.
See [the complete contract](../../docs/cdj-usb-export.md).

The `analyze` command reads one already captured local audio file through pinned
rbl-audio/rbl-analysis. It checks rate/channel budgets before collecting and caps
the retained mono head at thirty minutes/96million samples, then streams and
checks the remaining decode through EOF. Tempo beyond that head is extrapolated;
it does not claim to detect later transitions. The complete input duration is
bounded to24hours. The default adaptive/attack-placement analysis and independent
key-only path preserve component provenance. Beat rows use SoundBox's
[beat, milliseconds, BPM] order. It has no desktop source-write API. Result
persistence, cancellation and worker ownership live in the parent; shared source
analysis acceptance is tracked in [the analysis plan](../../docs/analysis-playlists-next.md).
Current installed provenance and release validation are in [HANDOFF](../../docs/HANDOFF.md).
