# SoundBox current handoff

Updated November3,2026 after the correctness/efficiency pass, final complete gate,
both signed package profiles, installation, normal curated startup and wrap-up.
Branch `main`; application source release `d08ee91d18fe89e9fa520f7f242d1cd3015ec903`
is committed and pushed. This documentation-only closure follows that release.
The installed signed base remains `d19f3eb40b4e18a5e474e8ef26a2887a76de9b7c`,
dirty=true; its recorded runtime bytes are unchanged. Publication did not restamp
or reinstall the running app.
**Personal build157 is installed and left running at `/Applications/Soundbox.app`.**
No further input or termination is granted.

## Installed runtime and source provenance

Installed `2026-11-03T22:18:00.130599-06:00`; normal curated startup verified
`2026-11-03T22:21:50.804063-06:00`. Signed base revision `d19f3eb`, dirty=true:

- Runtime fingerprint: `fc6963120d44821d3ddb2c873560dc38c0e76cb9d628604001e6bd53213783b4`.
- Executable SHA-256: `9601842d829165d405652b641eb3a5253f53a596488a70fd385a7be8bcf885e8`.

All158 personal/154 public packaged first-party Python modules and each entrypoint
match tested source. Native section descriptors/non-zerofill bytes match tested
builds:26deck,5process-guard and20CDJ sections, including zerofill descriptors.
The installed runtime fingerprint matches the frozen application source. Build157
is derived from unchanged Git history; this dirty-source delivery is distinguished
by hashes from earlier73219a59… and d5cb4451… build157 apps. It is not a published
Git revision. Prior build155/source release `9a023536` and documentation closure
`d19f3eb` remain historical provenance.

## Delivered efficiency and correctness work

Lightweight mount watching stays active; full CDJ/stock/dual/filesystem exporters
and cache indexes load when needed after detection. Empty scans skip export-playlist
construction and stock shadow. Stock-art warming waits for an attached stock-capable
iPod and stops cooperatively on disconnect while retaining worker ownership.
The supplied trackpad recording identified short gray/accent interruptions;
120ms idle feedback bridges gestures. USB scroll positions update immediately,
with coalesced preference notifications and full lifecycle capture barriers.

Sonny then requested tackling the remaining findings from simple fixes upward,
with correctness throughout. Three subagents implemented separate domains and
reviewed each other's work; root reviewed source acquisition/publication/replay,
UI ownership, output semantics and package composition. The current inventory
covers178 first-party Python/native app-tree source files with explicit review
depths,39 changed since d5cb4451…, plus eight build/harness paths. This is not a
claim that every dependency/research script or hardware failure was proved.

The latest pass fixes expected-SHA/source-epoch pairing, failed-acknowledgement
retry rebasing, durable folder-delete Collection pins, selected cue-reference
ordering, concurrent USB manifest/settings baselines and global MyTag revision
coverage. Historical retention proof is distinct from strict causal retry proof.
Fresh source/authority/process/mount/hash/recovery guards remain; no new source
publisher or musical profile was added.

Touched-record completion/rollback, worker-scoped analysis snapshots, queue indexes,
fuzzy-score pruning, atomic artwork batches, stock-art slot indexing and CDJ
status/selected metadata/no-op reconstruction reduce repeated work. Dialogs drain
owned work before disposal, protect busy/closed state, and decode visible thumbnails
at display size. Artist writes preserve newer covers/manual blocks. State path
overlap uses ancestor membership while retaining publication guards. Catalog
alternating rows use the existing dark palette under a light OS palette.
Provider bodies support optional cooperative cancellation/deadline scopes while
preserving adapter decompression/close/pool semantics. Packaging removes development
integrations; personal regular-file bytes shrink by11,666,423 (~11.7MB).

[The item-by-item audit](performance-audit.md) records differential/fault tests,
component measurements and remaining tradeoffs. Generated CPU/Qt fixtures do not
establish live-device throughput, actual compositor latency or user trackpad
acceptance. Cross-phase PCM sharing was profiled and retained as a memory/provenance
tradeoff. Full detached queue helpers, durable state serialization and opaque
DNS/connect/header/decoder/retry waits remain explicit costs. There is no hard
wall-clock cancellation guarantee or universal bug-free claim.

## Validation and current data

Final bare `./check.sh --render` passes2531tests:2467offscreen in618.725s plus
64Cocoa in67.664s, zero skips/expected failures. Lint, mypy106core files, native
builds, Rust7library+2binary+2adapter checks (zero doctests), required offline
fixtures and inspected render pass. Clang static analysis reports zero diagnostics
for the native deck/process guard. An earlier2531-test pass preceded the catalog
contrast correction; the entire gate was rerun for the installed source above.

Both final public and personal packages pass strict signing, first-party code
and native parity, composition inspection and isolated startup. Both isolated
reports enable native deck/process guard/query and RAM-only native ORM/SQLCipher
checks, with zero devices/library/art workers. Installed isolated verification
also passes. No real source database was opened by the isolated SQL probe.
Saved white Finder/Dock Icon\r ResourceFork/Finder metadata survive; ordinary deep
signing, exact signed Contents and identity checks pass after icon restoration.
No real-app mouse, keyboard or Accessibility input was used. SoundBox was already
closed at installation; Sonny's existing install approval required no termination
or playback interruption.

Actual normal startup loads3296native songs/85playlist nodes, reuses3280analysis
records and performs0waveform/grid/cue preparation tasks. Stock-art preparation
does not start; ordinary cover fill fetches0. Canonical `library.json` is byte-exact
across installation, isolated startup and normal launch:
`724b8aa1c7aea64e10acd7a5f3d6619b4848f225cbc21ae0161a634df269df84`.
Fresh counts:100imports,10custom,524hidden,1109titles,490artists,16saved analyses
and98bindings. All10authorities, playlist icon choices and saved white app icon
are exact; five import/analysis/prep/playlist save queues are empty. Since the
preceding20:22installation, only columns_state/window_rect/window_geometry changed;
the newer UI state was preserved. Later user edits supersede these dated facts;
never restore an older checkpoint over them.

Wrap-up read-only verification at22:29 found a subsequent normal relaunch
(started22:28:25), native deck active,3296songs/85nodes,3291reused records and
zero preparation/cover fetches. Only `/settings/columns_state` changed from the
preinstall file; newer UI state was retained. Current dated library SHA-256:
`ac8b3da88f1b5575eaaae8f694274c498cead7b2ad457dc4e07a0abdfe64d1f8`.
All10authorities, saved icons, curation and five empty save queues remain exact.
No agent restart, app input, teardown or reinstall occurred during wrap-up.

## Boundaries and next action

Leave installed build157 running for Sonny's use and trackpad testing. Existing
installation approval does not grant general termination or inputs. Sonny's
wrap-up source publication is complete. Physical export/reconnect/power-cycle and public clean-machine/
notarization gates remain open. One known absent native binding/unresolved import
path and the old32kHz MP3 compatibility error remain explicit, unchanged and unguessed.

Initial full native WAV44.1/48kHz PCM16/24 and independently accepted bare MPEG-1
CBR/no-Info/Xing stereo44.1kHz MP3 publication retain their existing authorities.
VBR/Info/gapless/mono/48kHz MP3, existing-analysis replacement, gain, broader
metadata and intelligent/cloud mutations remain separate gates. Memory Cue recall,
duplicate confirmation, crate outline, palette/geometry/transport and required
conversion→waveform→BPM→key order remain intact. Earlier Mocha145/9A/425-beat source
recovery stays accepted. Preserve515user disposals/four local dismissals,
124converted WAV originals and all newer recovery. Artists remains virtual-only;
never recreate the removed1527native hierarchy entries or disposed/reference songs.

## Evidence and retention

`~/Library/Logs/Soundbox/correctness-efficiency-2026-11-03/` retains the per-file
inventory/independent review, compact benchmark/fault scripts and receipts, final
gate reports/render, public/personal package/native/composition/SQL proofs, fresh
preinstall state/authorities/icon backups, installation, installed isolated and
normal startup proofs. `gate-before-catalog-contrast/` is historical proof for
superseded source, not the installed gate. Wrap-up reconciles all2531gate cases and confirms runtime/test/harness inputs
are unchanged since the final full gate; only release documentation changes.
Never replay the completed
`install_correctness_efficiency_once.py`, earlier installers or native pilots.
Prior dated evidence remains under `runtime-efficiency-2026-11-03/` and
`export-efficiency-2026-11-03/`; the former retains the supplied recording.

One previous d5cb4451… build157 rollback remains pending Sonny's acceptance:
`/private/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-correctness-install-ca6mo7qc/Soundbox-previous.app`.
Redundant public/personal dist app/COLLECT and PyInstaller work plus the obsolete
73219a59… rollback were retired after installed normal verification, with exact
paths/bytes in `post-install-retirement.json`. Native/check/required fixture
outputs, current source/app, audio originals and recovery remain. Do not recreate
older obsolete apps or retired rollbacks.
[Older checkpoints](archive/native-analysis-delivery-checkpoints-2026-11-02-03.md)
confer no current rollback or publication authority.
