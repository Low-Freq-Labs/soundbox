# Sync performance audit — September 26, 2026

The later [November4 iPod audit](performance-audit.md#ipod-deep-audit--november4-after-usb-testing)
adds source/catalog reuse, ownership/path corrections and GUI retirement fixes.
It uses offline fixtures only and does not close the historical hardware gates.
See HANDOFF for source/package/install status.

This audit accompanied the real composite run without restarting SoundBox,
starting another live planner, or reading device media. Validation uses small,
synthetic devices. See `HANDOFF.md` for the installed build and remaining live
acceptance; source tests are not a device throughput benchmark.

Reviewed November3 against release `d08ee91`; the current wrapper was rechecked
November5 against responsiveness build167/runtime `3fb0ff60…`. All run times, counts and installation
instructions below belong to the dated September campaign. Current source
acquisition requires an exact digest and a stable captured filesystem epoch;
old proofs never bless an unchecked newer epoch. Current installed provenance and
remaining physical gates are in [HANDOFF](HANDOFF.md), with later component
measurements in [the performance audit](performance-audit.md). Here, generic
ExportWorker/schema-2 refers to the filesystem engine, not the current native CDJ
USB path. Current CDJ USBs update only selected playlists, have no automatic
sync/pending state or idle/source-event scan, and start no final completion/eject
read. Dated FAT32/exFAT fixture timings are in the
[November4 USB audit](performance-audit.md#physical-usb-fixture-timings--november4);
they do not close the separate historical iPod performance/failure gates.

The latest stock planning follow-up shares source presence/canonical observations
within each membership phase, starts a fresh observation map for the next phase,
and reuses the exact final patched database parse for full semantic validation.
It preserves acquisition/compatibility/recovery/commit checks and native rendering
on no-ops. Its generated50000-membership result4013.40→1206.89ms is in the current
performance audit; it is separate from every physical time below.

The November4 interrupted-adoption correction supersedes the old persisted-proof
reuse described in the September table below: affected device bytes are freshly
hashed because FAT32/exFAT epochs can repeat. Canonical Mac-source verification
and ordinary retained export inventory have their own separate scopes.

## September 28: ordinary Sync trusts committed inventory

For this September schema-3 iPod campaign, Sonny confirmed that SoundBox
exclusively managed this iPod's music. This is not exclusive authority over CDJ
USB libraries: rekordbox and SoundBox remain equal CDJ exporters. The
expected external edit is a Rockbox append of an already-owned song to a
playlist. The desktop therefore uses incremental composite Sync by default:

- Read the committed manifest, database identity and small playlist baselines;
  reconcile library metadata and exact-prefix-proven custom playlist additions.
- Retain unchanged device media/art without per-file USB stats or media tree
  walks. Changed/deleted/new destinations retain their live safety checks.
- Reuse a committed source contract when canonical path, size/mtime, display and
  media policy match. ctime-only churn does not trigger hashes, for local and
  cloud sources alike. Original proof timestamps remain unchanged; this is a
  historical contract, not a newly verified source digest. Actual captures and
  copies still verify exact source/output bytes. New, relocated and changed
  sources continue through the verified preparation path.
- Deduplicate already-owned custom playlist sources from their matching
  committed hashes. Broad clutter discovery belongs to explicit Repair or
  journal-backed interrupted recovery. Post-delete empty-directory cleanup
  visits only ancestors of deleted paths, never unrelated media trees.

Verify explicitly checks device references/sizes; Deep Verify also hashes
managed files. Both now read the actual schema-3 file inventory (the older
reader silently stripped it). Repair audits inventory and restores absent
owned Rockbox media/art, with guarded vacancy and transaction rollback. Existing
unexpected/corrupt occupants fail closed; Repair is not a promise to overwrite
all corruption. Unjournaled `.sbpart` discovery belongs to Verify/Repair;
normal polling still reports manifest-bound interrupted recovery journals.
The lower-level planner retains conservative auditing by default; desktop normal
Sync/drop opts into inventory reuse, and the explicit Repair action selects the
audit path. Generic/stock-only exporters keep their existing policies.

The preceding installed build's generation-61 Sync took **934.45 seconds planning
and 980.03 seconds committing**, reading **40.42 GB** of canonical audio while
writing **60.03 MB** to the device. That observed run explains why reducing USB
writes alone could not fix the delay. Readiness-only build 115's Finder-closed
generation 62 then completed in **994.57 + 948.26 seconds**, reading **40.75 GB**
to write **74.09 MB**. Its two changed sources were each processed once, not once
per firmware. This is the repeated-read baseline; the incremental build was
installed afterward at 21:58; its development provenance is archived in the
rollback evidence. `HANDOFF.md` records the final release installation.

New synthetic regressions forbid source/audio reads and unchanged device
media/art stats or directory scans during a no-op, one-title edit and adoption
of an already-owned playlist song. They also cover local ctime churn, retained
manifest dedup, explicit missing-file repair and same-size corruption detected
only by Deep Verify. These are I/O behavior assertions, not a hardware timing
claim. The first installed one-title test subsequently committed generation 63
at 22:03:20: **18.30 s planning + 8.46 s commit = 26.76 s**, with **69,562,392
canonical bytes read**, **34,781,296 device bytes written**, 2880 retained sources
and media files, 14,405 retained art files, and zero source-proof rehashes.
Only the edited AYY0G WAV was acquired once per phase. Final dashboard inspection
took another 1.883 s. The prior run changed two tracks and this run changed one,
so the observed roughly 73× elapsed improvement is not a matched workload
benchmark; the counters directly establish elimination of unchanged-source
rehashing. No-op, playlist-only and other acceptance gates remain open. See
[HANDOFF.md](HANDOFF.md).

## September 26 historical evidence

- The mounted schema-3 baseline contained 2,884 media records, representing
  111.66 GB of canonical sources. Every record already used the current
  normalized media profile and had a verified source hash. A profile migration
  was therefore not the explanation for scanning this collection again.
- The previous relocation investigation found 2,634 source paths moved. A
  first exact-byte verification of those new paths is required; repeating every
  completed proof after a later failure is not.
- A one-second macOS process sample caught the `ProgressTask` worker waiting
  in `sf_open → guess_file_type → header_read → read`. This is an observed
  header-read bottleneck, not evidence that hashing dominated the entire run.
  The ephemeral sample is `/tmp/soundbox-sync-live-sample.txt`.
- At 05:29:49, the installed build finished planning but failed source preflight
  on AHEE's changed timestamps. This was before device mutation. App-side
  custom membership can already have been saved at this boundary, so preserve
  the latest `library.json`; do not restore the old pre-sync copy.

## September 26 changes and their bounds

| Path | Previous cost/behavior | Revised behavior |
| --- | --- | --- |
| Composite relocation / exact custom dedup | Full hashes lost when later planning failed; dedup and relocation could read the same source twice | Individually persisted stable full-hash proofs; same canonical path/device/inode/size/mtime/ctime reuses the result, otherwise full verification |
| Source timestamps changed without content changes | Planning rebuilt derivatives; commit refused even identical bytes | Match exact source hash against a stable baseline; reuse the derivative and refresh preflight contracts/manifest timestamps before writing |
| `plan_stock_ipod_sync` format inspection | Source and identical payload inspected for each playlist occurrence | Once per unique path for unproven files; no source open when the verified desired payload exactly matches its managed stock baseline |
| Initial multi-playlist dual drop | First playlist committed alone, remaining playlists in a second generation | All already-confirmed playlists enter one generation; later arrivals form the next batch |
| Generic `ExportWorker` | Independent export for every queued playlist | Contiguous jobs for one target form a shared plan; different targets keep FIFO order |
| Composite stock source-row selection | Rebuilt the entire desired-source string set inside the manifest-row loop | Build the set once |
| Diagnostics | Start/progress/success timing largely absent from the log | Worker durations, planning phase durations, current file before its read, and periodic source-proof hit/read/byte counters |

Proof records contain no audio and survive ordinary derivative cleanup. They
remain acceleration only: device ownership, source/payload correctness,
stock database/artwork validation, commit ordering, and recovery checks retain
their existing authority. Timestamp refresh never accepts different source
bytes. A continuing change fails closed, and refreshed commit plans are copies
of the caller's original plan.

## September 26 remaining costs (historical baseline)

- New/changed media still produces a verified disposable derivative during
  planning, discards it, and rematerializes one track at a time during commit.
  This bounds staging by the largest track rather than the whole library.
  Preparation, source preflight, essence verification, copy, and destination
  verification perform multiple reads. Consolidating those passes requires
  preserving their distinct mutation/recovery boundaries; this patch does not
  claim to have eliminated them.
- The generic filesystem exporter still verifies existing destination bytes
  and prepares its selected derivatives before copy. Batching removes repeated
  work across overlapping playlists in that batch, but does not turn schema-2
  export into the schema-3 incremental planner. The later lifecycle consolidation
  bounds generic execution to one captured source/derivative at a time; the
  whole-selected-set staging described by this baseline is superseded. Never
  route a composite iPod through the generic exporter.
- Classic-iPod write pacing remains 150 ms per relevant operation. USB copy,
  on-device verification, database/artwork validation, and final dashboard
  refresh remain real costs. Unchanged payloads must not be recopied to make
  the progress bar look active.
- Cloud authentication and network latency remain external dependencies. A
  cache miss may hydrate a complete cloud file; an evicted file with changed
  metadata must be verified again. No retry policy can safely invent its bytes.

## September acceptance checklist

Regression checks cover retry without source rehash, same-size edits with
preserved mtime, corrupt/missing proof records, unreadable and continuously
changing sources, timestamp-only planning/commit refresh, changed bytes refused
before device writes, a clean subsequent plan, unchanged stock compatibility,
changed-format rejection, overlapping playlists, combined initial drops, and
generic queue arrivals/device boundaries.

The campaign proposed a normal confirmed sync followed by an unchanged sync.
Compare phase timings, full source-proof bytes, derivative/copy counts, and
finalization time. Compare a combined overlapping-playlist drop with separate
generations using synthetic fixtures first. Do not claim an end-to-end speedup
until the installed application has completed those real-device checks. This
historical checklist grants no new live run or application restart.

## Build 108 live follow-up

A subsequent real run measured 619.92 seconds planning (580.68 seconds source
verification, 17.03 stock, 3.74 Rockbox paths, 16.11 artwork/ownership). Source
proof reads totaled 113.20 GB across 2,881 desired sources; the live-set group
was much slower than the remaining ordinary tracks.

Commit passed initial timestamp revalidation but failed after 121.64 seconds
in CRANKDAT's per-file materialization. SeaDrive concurrently removed 83 files /
10,149 MB from its cache. The failed source was then a zero-block placeholder
with only ctime changed relative to its latest proof. The original generation
58 manifest remained exact and rollback reported no error. This is failed
hardware acceptance for build108: initial preflight refresh did not cover eviction
between preflight and staging. The subsequent lifecycle consolidation added exact
source/payload capture and one-track staging; see [its dated results](ipod-lifecycle-audit-2026-09.md).
The November3 pass additionally pairs every expected digest with the stable
captured epoch. Neither fix establishes an unperformed physical retry.
