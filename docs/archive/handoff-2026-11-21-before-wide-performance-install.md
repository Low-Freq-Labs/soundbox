> Historical handoff checkpoint, November21,2026. Dated approvals, pending work
> and rollback paths grant no current app control, source/device writes, replay,
> restoration or cleanup. Use [HANDOFF](../HANDOFF.md) and
> [runtime contracts](../runtime-contracts.md) for current guidance.

# SoundBox handoff at the recorded checkpoint

## Wider audit source follow-up — November21

Sonny explicitly requested three broad parallel performance audits. Source now
contains further reviewed changes to duplicate-art comparison, unchanged
Graveyard refresh, repeated native grid recovery calculations, device Delete
selection normalization, complete import metadata repair, CDJ request copies and
legacy playlist-retirement lookup.
These changes are
not yet in the installed6f92 app recorded below. Generated timing and preserved
contracts are in [performance evidence](../performance-audit.md); bounded coverage,
baseline hashes, reproducers and focused checks are under
`~/Library/Logs/Soundbox/wide-speed-audit-20261121/`. Additional read-only audit
work continues independently; no exhaustive semantic-review claim is made.
The first sweep now accounts for all259 production paths:129 full source reads
and130 specifically bounded section reviews, with the packaged entrypoint read
separately. Seven accepted improvements touch ten production files. The final
bare gate passed all5134 offscreen cases; macOS then reported the screen locked
and native actual-presentation checks failed. The locked-session native test
process was interrupted with SIGINT after bounded diagnostics confirmed a wait
for `presentation_completed_frames`; no real app was signalled. Full gate exit130
is incomplete, not a pass. All932 saved source/test/build inputs remain unchanged.

Candidate personal174 is built at `dist/personal/Soundbox.app`, runtime source
`c53c93789e2495bf4f1596522b06c5edf041b754c18d5d897b34e242b66aaf65`,
executable SHA256
`578cee211e4ce07d7bda6d416495f8e1c4ee82dc6eeaa87126cfc60cf99086ca`.
Strict signing,236 first-party runtime/startup comparisons and four native text
sections match the frozen source. Its disposable packaged check passes native
CoreAudio/Metal, process guard/query, SQL/ORM and AES, with zero library/device/art
discovery; strict signing still passes after that isolated launch. This capability
check does not establish actual drawable presentation while macOS is locked.

Sonny has been asked to unlock. On unchanged inputs, replay the full native phase
with `QT_QPA_PLATFORM=cocoa ./.venv/bin/python scripts/check_tests.py --phase native
--report build/check/native.json`, then reconcile with
`./.venv/bin/python scripts/verify_test_reports.py`. A fresh closed-app state
snapshot and guarded install/reopen remain pending. The installed app and user
state are untouched. `validation.json`, `package.json` and `isolated/runtime.json`
under the wider evidence directory record these separate scopes.

## Installed responsiveness pass — November20–21,2026

`/Applications/Soundbox.app` is personal174, dirty Git base
`91a60c3432f9e8809dd12b61582e5044edf03082`, runtime source
`6f92e842341690e3312ad64eeaa4cdfc9ed8f744a0b275d228c9b9ba3370949d`.
Sonny quit the app himself for this installation. The fresh package passed strict
signing,236 first-party runtime/startup code comparisons, four native text-section
comparisons and exact current source fingerprint. Its disposable packaged check
passed native CoreAudio/Metal, process guard/query, SQL/ORM and AES with no live
library/device/artwork discovery. The installed executable and loaded process
match that package; ordinary postlaunch deep signing passes. Saved white icon
metadata remains intact.

Ordinary reopen at02:31UTC November21 (November20 local) loaded3298tracks and
86playlists, zero library-preparation tasks, zero artwork fetched/failed. At
02:35UTC, all6433records,217protected authority/art JSONs, curation and save queues
matched the fresh complete pre-install snapshot. The manifest's revision and
ordinary waveform-zoom preference changed; the newer256→92 zoom was preserved.
No old state was restored. This is a dated observation, never a baseline to
replace later user edits. The pre-existing unbound import, two unresolved missing
locations and failed conversion job remain guarded and preserved.

Complete physical manifest/record backup, portable JSON, protected metadata,
source/test inputs, package, install and passive runtime receipts are at
`~/Library/Logs/Soundbox/code-speed-audit-20261120/`; install evidence is in its
`install/` directory. These checks grant no new native authority or source test.

## Included behavior and measured limits

- Audible Play/autoplay and `track_ready` wait for the complete selected waveform.
  Immediate grid/cues position the paused deck; newer Play/Pause, Stop, seek and
  held/released cue intentions retain ownership. Warm generated two-hour cached
  loads reached a complete accepted surface188.53→176.82ms; normal195s control
  stayed~10ms. Native audio/DSP/waveform appearance remain unchanged.
- Manual/catalog artwork prepares off GUI before canonical publication. Latest
  choices, exact source/restore/selected-art guards, durable-save lifecycle and
  complete Info first frames remain. Generated manual-image GUI gaps fell
  136.83→4.54ms with full publication essentially unchanged.
- Review uses an exact full QImageReader worker with the original GUI scaling.
  First comparison reveal is complete; rapid selection is bounded, and Close,
  same-track audition, global Undo/Exclusions and exact recovery remain covered.
  Generated large-cover GUI gaps fell110.03→7.10ms; full comparison102.66→105.97ms.
  Its separate `_same_art` candidate comparison still measured168.29ms.
- Grid/BPM availability shares one general check and validates only selected grid
  rows while retaining all queue headers/cues/operations/requests. Actual edit
  admission and native pump still validate the full queue. Generated eight-grid
  stress gaps fell313.65→100.44ms; immediate cue feedback0.65ms. Full-patch encode/
  GUI ACK remain49.84/53.75ms and durable completion3.185s. Rejected sparse-grid/
  carrier prototypes are not integrated; full persistence stays authoritative.
- Sort/search preserves selected occurrences, current column, scroll and drafts.
  Artists/empty Graveyard filters avoid duplicate projection; random autoplay
  checks drawn candidates freshly. Native readers retire sessions/statements/pools
  and share only exact key derivation; scalar MyTag reads preserve full results.
  Complete export overlap paths retain their full boundary with lexical dedup.
- Approved sparkle A remains the ordinary note/star/crate plus the bright larger
  upper-right four-point sparkle and actual row-background halo. The enclosing
  gear and initial tiny sparkle remain rejected. Other visuals stay unchanged.

[Performance evidence](../performance-audit.md), [artwork details](../ui-polish-audit.md)
and [critical findings](../critical-audit-2026-11.md) distinguish generated timings,
remaining costs and independent physical/native acceptance.

## Validation scope

The earlier complete two-phase checkpoint passed5081 offscreen plus68 native
cases, with all5149 identities reconciled and zero skips. Its first native attempt
was interrupted while macOS was locked; exact unchanged controls reproduced zero
presentations. The unlocked replay passed72.416s. The test wait retains Qt deferred
cleanup plus an explicit Python yield under its unchanged real2s deadline.

The final bounded review/availability follow-ups passed85 Cocoa plus13 final
review cases,49 availability/native/full-freezing and128 adjacent lifecycle cases;
these overlap and are focused evidence, not a new full bare-gate pass. Existing21
artwork recovery/selection controls also pass after immutable snapshot digest reuse.
Final global lint,158-module engine types, layout/links/instruction parity and
whitespace checks pass; the isolated review render was inspected. Package and
installed proofs above are for the exact final runtime. No commit/push occurred.
Before publication or wrap-up run bare `./check.sh --render` on final inputs.

Native save work retained from the preceding delivery passed1143 RB DB recovery
cases and16 final playlist recovery cases. Exact salt/KDF reuse, verified fresh
playlist-plan reuse and promoted stale contention wakes preserve fresh source/
authority/apps/recovery checks, shared lease/journal and exact readback/ACK.
Tiny synthetic encrypted rename timing429–432→15–16ms is not live-library latency.
Its dated proof remains at `~/Library/Logs/Soundbox/native-save-speed-20261120/`.

## Retention and next work

The sole temporary previous app is accepted sparkle A/runtimead38 at
`~/Library/Logs/Soundbox/code-speed-audit-20261120/install/previous/Soundbox.app`.
Sonny accepted the installed6f92 pass before requesting the wider audit. The exact
ownedad38 rollback is eligible for receipt-verified retirement during the next
single-slot rotation; preserve6f92 until a new installed pass is accepted. The olderf94f rollback was receipt-checked
and retired during this single-slot rotation. Do not accumulate app generations.

Preserve current canonical state with its record store, newer edits, original
music/conversions, native/device recovery and immutable fixtures. Five native
canary apps, six unresolved DB captures and unclosed diagnostic recovery remain
protected. No general AppData, Trash, music or diagnostic sweep occurred.
Review duplicate comparison, remaining full-grid transfer/validation and unchanged
Graveyard row reconstruction stay explicit follow-ups; do not claim all functions
are instantaneous. Older approved-icon proof remains under
`~/Library/Logs/Soundbox/smart-sparkle-a-20261120/`. Earlier source/history and
retention limits are in [the preceding handoff](handoff-2026-11-20-before-approved-sparkle-a.md),
[older native history](handoff-2026-11-20-before-wrap.md),
[shared-library scope](../shared-library-audit-2026-11.md) and [ROADMAP](../ROADMAP.md).
