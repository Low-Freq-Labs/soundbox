> Historical handoff checkpoint, November21,2026. Dated approvals, pending work
> and rollback paths grant no current app control, source/device writes, replay,
> restoration or cleanup. Use [HANDOFF](../HANDOFF.md) and
> [runtime contracts](../runtime-contracts.md) for current guidance.

# SoundBox handoff at the recorded checkpoint

## Source cleanup in progress — November21

Sonny requested independent cleanliness reviews, support for his one current
installation rather than hypothetical older users, and short durable navigation
notes. Independent pre-cleanup source ratings were6.5/10 and7/10; build hygiene8/10.
Current source removes confirmed dormant sync/media helpers and the artwork alias,
trims dependency installer/non-SQLite packaging, and verifies that all23maintained
directory guides are reachable from the root README. Focused cleanup/package/
layout checks pass; these edits are not yet in installedc53c below.
V4 waveform retirement is being checked against actual zero-V4/current-V5 cache
inventory and exact warm preparation receipts. No live cache migration or new
installation has occurred. Preserve current state and older V5 rendering math;
new portable backups remain supported. Detailed evidence is under
`~/Library/Logs/Soundbox/cleanliness-review-20261121/`.

## Installed wider responsiveness pass — November21,2026

`/Applications/Soundbox.app` is personal174, dirty Git base
`91a60c3432f9e8809dd12b61582e5044edf03082`, runtime source
`c53c93789e2495bf4f1596522b06c5edf041b754c18d5d897b34e242b66aaf65`.
Executable SHA256:
`578cee211e4ce07d7bda6d416495f8e1c4ee82dc6eeaa87126cfc60cf99086ca`.
Sonny explicitly requested installation and quit SoundBox normally himself.
Fresh complete state backup preceded the guarded app rotation and ordinary reopen
at03:30UTC November21 (November20 local). No real app was automatically terminated,
no old state restored, and no live-source/device test or commit/push occurred.

At03:31UTC the running process59377 matched the installed executable's path,
device and inode. Normal startup loaded3298tracks/86playlists with native
CoreAudio/Metal, zero library-preparation tasks, zero art fetched/failed and the
saved white icon `#ffffff`. Fresh manifest, complete portable state, all6433record
files,217protected artwork/authority JSONs, curation and save queues matched the
pre-install snapshot exactly. Cache counts stayed4065art/3350analysis files.
This is a dated observation; later user edits always supersede it. The pre-existing
unbound import, two unresolved locations and one failed conversion remain preserved.

Evidence, complete manifest/record backup, portable JSON, protected metadata and
compact install/runtime receipts are at
`~/Library/Logs/Soundbox/wide-speed-audit-20261121/`, with live-state handoff proof
in `install/`. Source and installed identities are independently recorded there.

## Delivered changes

Seven improvements across ten production files retain current visuals and QOL:

- Review compares duplicate artwork off GUI and derives the original sample once
  per worker batch. Exact pixels/thresholds, candidate order, fresh policy and
  selection guards, complete first comparisons, Close/Undo/retry remain covered.
  Generated cached comparison619.61→287.92ms; maximum GUI gap517.22→8.78ms.
- Unchanged Graveyard refresh reuses exact Qt items after fresh complete display
  comparison and source/restore checks. Current tooltips, selection/current
  column and scroll survive. Generated3740-row refresh90.74→11.85ms.
- Native grid recovery reuses one already proved pure transformation, keeping
  fresh source/authority/backup/journal/payload/endpoint checks and cold recovery.
  Generated28800-beat transaction1142.30→304.02ms with identical outputs.
- Device Delete selection uses lexical ancestor lookup after every original
  per-path guard. Same mount, count, order and confirmation; generated1000-file
  TEMP preconfirmation5150.35→160.13ms. No actual device deletion was tested.
- Complete typed import metadata skips a repair stat that produced no result;
  incomplete/malformed repair and missing-reference injection remain. Generated
  full3300-row library application130.18→113.91ms.
- CDJ render requests avoid copying full inventories immediately replaced by
  selected changes; retained inputs remain detached and source guards unchanged.
  Generated ten-of3300 request-copy component69.58→0.42ms, not total export time.
- Legacy playlist retirement traverses pending-proof candidates once per call,
  retaining exact ownership/path/hash checks, first-valid order and full fallback.
  Generated86-playlist lookup38.19–38.88→2.65–2.77ms; empty selection does no work.

The preceding responsiveness pass remains included: complete waveform before
Play/autoplay/readiness, latest cue/transport ownership, complete selected art
before Info's first frame, ordered durable saves and failure/retry/Quit ownership,
prepared ordinary pages and on-demand device pages. Approved sparkle A remains
unchanged. Native audio/DSP/waveform appearance and source audio remain unchanged.
See [performance evidence](../performance-audit.md), [artwork details](../ui-polish-audit.md)
and [runtime contracts](../runtime-contracts.md).

## Validation and audit scope

All5134offscreen cases and68unlocked Cocoa cases pass, zero skips/expected failures;
exact worker/phase inventories reconcile5202tests. The unlocked native phase took
73.037s. The initial bare `./check.sh --render` passed static/native renderer,
158-module engine types, fixtures, offscreen tests and inspected render, then hit
macOS screen lock. Its owned native process was interrupted waiting for actual
presentation; original command exit130 remains incomplete. The unchanged native
phase was replayed after unlock and both full-phase reports reconciled. This is
complete two-phase evidence, not a new successful bare-command invocation.
All932saved runtime/test/build inputs stayed identical through packaging/install.
Before commit/push or wrap-up run the complete bare gate on intended final inputs.

Affected focused evidence additionally includes252prep recovery cases,74playlist
migration/sync cases,70CDJ cases,65Cocoa review/provider cases,57Cocoa art/Info/Quit
lifecycle cases and21Cocoa Graveyard cases. These overlap; do not sum them with
full validation. Strict package signing,236first-party runtime/startup code and
four native text-section comparisons match tested source. Disposable packaged
CoreAudio/Metal/process-query/SQL/ORM/AES checks passed without library/device/art
work. Postlaunch ordinary deep signing passes; OS-owned saved-icon metadata stays.
These results grant no new native authority or physical-device acceptance.

The delegated first sweep covered259production paths:129full reads and130files
with bounded semantic sections, plus the packaged entrypoint. Per-owner ranges,
current/baseline hashes and deduplicated `coverage-union.json` remain explicit;
this does not claim every function was reviewed or is instantaneous.

## Remaining work and retention

Separate unimplemented leads include full-grid encode/GUI ACK, bulk conversion
receipt queue scans, import canonicalization, XML parsing, catalog witness reuse,
duplicate editor restoration, stock-device projection and final artwork scan
waits. State-transfer/backup-renderer experiments remain proposals; rejected
heartbeat controls never ship. See [critical findings](../critical-audit-2026-11.md).

The sole previous app is runtime6f92 at
`~/Library/Logs/Soundbox/wide-speed-audit-20261121/install/previous/Soundbox.app`.
Retain it until Sonny accepts this installed pass, then retire that exact owned
rollback and duplicate personal packaging. The olderad38 app was receipt/hash/
signature checked and retired before this single-slot rotation; no app generations
were accumulated. Do not alter canonical state, newer edits, music/conversion
originals, native/device recovery, required fixtures, five native canaries or six
unresolved DB captures. No general AppData/Trash/music cleanup occurred.

[Previous handoff](handoff-2026-11-21-before-wide-performance-install.md)
retains preceding installed identity, validation history and older retention links.
