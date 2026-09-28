# Sync performance audit — September 26, 2026

This audit accompanied the real composite run without restarting SoundBox,
starting another live planner, or reading device media. Validation uses small,
synthetic devices. See `HANDOFF.md` for the installed build and remaining live
acceptance; source tests are not a device throughput benchmark.

## Evidence

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

## Changes and their bounds

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

## Costs that remain deliberate or need separate measurement

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
  export into the schema-3 incremental planner. Its staging behavior remains a
  separate follow-up; never route a composite iPod through it.
- Classic-iPod write pacing remains 150 ms per relevant operation. USB copy,
  on-device verification, database/artwork validation, and final dashboard
  refresh remain real costs. Unchanged payloads must not be recopied to make
  the progress bar look active.
- Cloud authentication and network latency remain external dependencies. A
  cache miss may hydrate a complete cloud file; an evicted file with changed
  metadata must be verified again. No retry policy can safely invent its bytes.

## Acceptance

Regression checks cover retry without source rehash, same-size edits with
preserved mtime, corrupt/missing proof records, unreadable and continuously
changing sources, timestamp-only planning/commit refresh, changed bytes refused
before device writes, a clean subsequent plan, unchanged stock compatibility,
changed-format rejection, overlapping playlists, combined initial drops, and
generic queue arrivals/device boundaries.

After installation, run one normal confirmed sync and then an unchanged sync.
Compare phase timings, full source-proof bytes, derivative/copy counts, and
finalization time. Compare a combined overlapping-playlist drop with separate
generations using synthetic fixtures first. Do not claim an end-to-end speedup
until the installed application has completed those real-device checks.

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
hardware acceptance: initial preflight refresh does not cover eviction between
preflight and staging. Fix that boundary with exact source/payload verification
and one-track staging before asking for another full live retry.
