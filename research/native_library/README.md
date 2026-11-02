# Shared native-library proofs

These are **research tools**, excluded from app packages. The core offline
workspaces have no source discovery or publish-to-source API. Separate
one-plan source pilots are described below. The separate core registration authority
loader is not provisioned or connected to the app; these research workspaces never
load it. Existing
Tag/Cue/Grid/Playlist permissions do not authorize registration or analysis writes.
The [shared-workflow plan](../../docs/shared-library-workflow-plan.md) defines the
requested product behavior; [HANDOFF](../../docs/HANDOFF.md) records live status.

`registration.py` constructs the observed bare content row independently from
explicit file facts, native identity/clock seeds and frozen library properties.
It currently accepts only an untagged PCM16 stereo44.1kHz WAV with integral seconds.
It preserves the original canonical file inode even though inspection uses one
hashed disposable source snapshot. It rejects existing ID/UUID/path collisions,
including deleted rows; this is not yet the unified identity/reimport resolver.
No general metadata, codec or initial-analysis claim follows from this shape.

`registration_workspace.py` admits writes only inside an original private marked
TEMP workspace copied from an explicit frozen capture (`master.db`, `state.json`,
`masterPlaylists6.xml`). SQL connections never open the protected capture. A
workspace captures the selected audio identity once, then durably admits the
exact intention before SQL staging. Changed digests or superseded plans cannot
be substituted after admission. A complete semantic base binds the plan; the
only SQL changes are one content insert and `localUpdateCount.int_1`.
All existing rows, unknown tables, XML and source
audio remain exact. Mutation SQL is also guarded against an arbitrary connection,
attached DBs and trigger side effects. This does not update playlists or ANLZ.

An encrypted before/after pair proves the full delta before a durable journal
permits working-copy mutation. Recovery validates identities, hashes and the whole
pair before SQLite handles a TEMP hot journal. Exact before means rolled back;
exact after means committed; anything else remains preserved. An inactive
zero-header leftover can be retired only with a repeated exact proof. The durable
receipt supports idempotent committed replay. Preparation and successful payloads
retire inside this disposable workspace; the small receipt remains. This deliberately
linear offline inventory is not a proposed production per-import algorithm.

Run synthetic tests without live sources, devices, AppData or native references:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p test_registration_proof.py
```

The tests use real process exits before/after publication, including spilled hot
journals, as well as stale input, source drift, aliases, unexpected schemas,
opaque BLOB fields, corrupted recovery, XML preservation and byte-identical files
at different canonical paths. The replay test ensures unrelated native registry
differences remain visible as failure.

`registration_replay.py` takes explicit frozen before/after directories, the
canonical source WAV and a new report path. It takes only ID/UUID/clock seeds from
the native expected row and reconstructs every other field. It retains a receipt
and all comparison deltas. A full semantic/XML mismatch exits nonzero; there is no
ignore-registry switch. Failed transactions retain their TEMP evidence.

```sh
.venv/bin/python -m research.native_library.registration_replay BEFORE AFTER SOURCE.wav --report NEW_REPORT.json
```

The original October29 sample reproduces the complete content table, but its
capture interval includes five additional notification/path registry rows. Its
strict full comparison still fails in `agentRegistry`; the writer preserves those
rows rather than replaying them. The new October30 startup-complete before/import
pair closes this gap: independently constructed row, full semantic inventory and
byte-identical XML match exactly, with no registry exclusions. The native counter
advances once. This is frozen-copy/native-reference correctness; it does not
provision source write authority or substitute for acceptance of an app-authored
source candidate. The old mismatch remains recorded as historical evidence.

`components.py` is the pure three-way component contract for the next adapters.
It compares a pending intention's exact base with a verified current component,
preserves independent native changes, retires already-satisfied/no-op drafts and
keeps same-component conflicts intact. Analysis cannot replace locked/manual
components; explicit manual intentions still respect locks. Source/library/track
bindings must agree, and one revision cannot describe different values. Adapters
must normalize each component's units/shape and derive revisions from its native
fields; this generic contract supplies no native serializer, migration approval,
writer, cache authority or persistence. Its synthetic tests are in
`tests/test_shared_components.py`.

`identity.py` proposes exact-path native identity reuse or missing registration
while retaining the stable logical ID. It does not match title/artist or merge
distinct files with identical audio. Multiple/historical rows, a changed prior
binding or two logical IDs sharing a native row require explicit reconciliation.
The caller must first capture a verified canonical file identity; this pure
planner never performs filesystem discovery, creates native IDs, migrates old
curation/aliases or grants writes. `tests/test_shared_identity.py` covers those
boundaries. The private85-import metadata audit found one existing exact-path
native row and84 without a native path match; it performs no bulk registration.

`registration_pilot.py` is an unintegrated, separately approved source test for
only the generated32-second `SoundBox-first reference.wav`, bound to its exact
known audio SHA-256. Preparation constructs and verifies encrypted before/after
copies with unchanged XML, then stages a concrete identity/clock/counter plan.
Publication/recovery require that exact plan's separate approval, closed native
apps and SoundBox, the accepted version, exact source/library/schema/file identity,
and the shared Tag/Cue/Grid/Playlist lock/journal. The Cue transport supplies those
guards, not Cue permission. The pilot uses its own approval and recovery formats.
It inserts one bare content row and advances only the local counter once. XML is
guarded byte-for-byte and never rewritten. No audio/analysis/playlist changes or
production registration authority follow from this pilot. Older synthetic-row
or playlist approvals never authorize it. A successful SQL commit still needs
Sonny's native reopen/inspection/normal-save acceptance.

Synthetic source-adapter tests cover missing/foreign approval, changed state,
shared-domain recovery exclusion, unplanned XML changes, damaged archives,
idempotency and real process exits under DELETE/WAL/spilled-journal modes:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p test_registration_pilot.py
.venv/bin/python -m research.native_library.registration_pilot --help
```

All CLI paths are explicit. `prepare`/`show` are read/copy-only; `apply`/`recover`
require the exact separately approved file. No approval is inferred from a
native-reference action or created by preparation. Full semantic inventories are
deliberately offline/first-pilot work, not the intended production save algorithm.

The October30 bare source pilot passed its separate explicit approval, exact
source readback and Sonny's native reopen/inspection/save/quit. The row, counter,
existing content, XML and audio survived unchanged; native startup bookkeeping
stays visible. Its scope remains one known generated WAV; no production authority
or analysis permission was provisioned. HANDOFF records the acceptance receipt.

`fixture_analysis.py` independently constructs initial-analysis metadata for that
exact audio digest: existing8B key lookup, one mixer row, target update, three
MD5/size/path registrations and the isolated native +20 counter sequence. It
preserves all unrelated fields and rejects existing analysis/cues, ambiguity and
unknown packet shapes. The cached native gain/peak and observed Length31 are
explicitly fixture-bound; neither supplies a general DSP/endpoint policy. Frozen
TEMP replay matches the complete native inventory and rolls back exactly.

`renderer/` is a separate TEMP-only Rust proof, using the existing pinned local
toolchain and untouched vendor. It authors fresh waveform sections around the
supplied captured grid, retaining explicit reference opaque headers/PWVC. It has
no SQL or publisher. The generated packet has matching section order/dimensions
but different waveform values; its empty PQT2 uses observed manual invalidation
bytes whose suitability for initial publication remains unaccepted. Its2 Rust
tests cover input/path/endpoint guards and output preservation. Build/test with
the toolchain environment from build_cdj.sh, using this crate's own locked manifest
and shared build/cdj target; do not install a system toolchain or modify vendor.

`analysis_workspace.py` proves DB plus three-file publication only in an original
marked TEMP root. Its physical share is a private mapping; logical rb_local_path
values never select filesystem write targets. A journaled directory inode owns
staging; the complete directory renames before SQL commit. Recovery distinguishes
verified before/after states, cleans only its owned directory and preserves
unknown/corrupt data. WAL normalization applies only to the disposable working
copy. Empty proof parent directories can remain after rollback. Six synthetic
tests exercise real directory/SQL crash boundaries, WAL/spill recovery, occupied
targets, unknown files/DB state and trigger rejection. No analysis source adapter,
source permission or application integration follows from this proof.

`analysis_pilot.py` is a separate, unintegrated source adapter restricted to the
already accepted generated SoundBox-first registration: fixed native ID/UUID,
filename and known audio digest. Preparation verifies the full encrypted before/
after DB/XML pair and all three generated files on disposable copies. Publication
requires a new exact-plan approval; the registration approval explicitly excludes
analysis and cannot be reused. Native apps and SoundBox must be closed at every
guard. The adapter uses the same shared native lock/journal, its own recovery
format, and explicit native-share inode/path bindings. It atomically publishes
only a previously absent owned directory, then changes only target metadata,
three contentFile rows, one mixer and the native counter. XML/audio/playlists and
all other rows remain exact. Recovery validates the complete pair and directory
inode/bytes before cleanup or forward completion; unknown state remains intact.
It never restores the whole source DB or normalizes source journal mode. Native
acceptance and a general gain/endpoint/format policy remain separate gates.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p test_initial_analysis_pilot.py
.venv/bin/python -m research.native_library.analysis_pilot --help
```

Its synthetic tests cover approval/domain exclusion, occupied/redirected targets,
changed audio, corrupt recovery, unknown DB/XML/files, exact idempotence and13
real process exits across directory, SQL, DELETE/WAL/spill and receipt boundaries.
Preparation never infers source approval. Sonny separately approved the exact
generated-track candidate and its publication passed native reopen/load/save/quit:
target and all three files stayed exact, with only startup bookkeeping changed.
This accepts only that known WAV; no production authority, general format/gain/
endpoint policy or app integration follows. See HANDOFF for the subsequent
generated format matrix and pending native reference action.


`media_registration.py` is a later copy-only multi-format constructor/inspector,
without a source publisher or production probe. Its original marked-TEMP SQL
boundary is separate from the known-WAV adapters, which remain narrow. A captured
source keeps its original path/inode/digest; an explicitly supplied development
ffprobe reads only the local capture. Only referenced family/codec combinations
are admitted. Four synthetic tests cover the capture boundary, duration framing,
rollback/commit, opaque-row preservation and foreign/tampered workspace rejection.
The frozen11-format native bare replay now matches the entire semantic inventory
and XML exactly, without registry exclusions. The observed extensible-WAV bare
Length0 is not an assertion that decoding failed. Native initial analysis and
broader metadata/component authority remain separate; see HANDOFF for current
format-reference evidence.

`metadata.py` is a pure reconstruction of the observed ASCII title/artist save
protocol on analysed local rows with an empty album. It has no SQL, filesystem,
audio tagging or source-publication API. Explicit identity/clock seeds construct
new artists; exact existing names reuse their records. Supplied complete counts
across content artist/remixer/original-artist/composer and album-artist roles
distinguish shared artists from the observed unshared local artist retirement.
Historical/synced orphan retirement, simultaneous creation/retirement, Unicode,
nonempty albums and unobserved row states remain unsupported. Six synthetic tests
cover preservation, ambiguous/stale identities/reference facts and those limits.
All five generated native UI saves reconstructed independently and replayed on
one encrypted TEMP copy match the complete native inventories and XML, without
excluded tables or fields. Native UI rewrote only the generated WAV's tags; its
PCM and format bytes remained identical. This research provides no source writer,
recovery adapter, metadata migration permission or app integration.


October31 registration continuation: the read-only bundled media probe and separate
`media_workspace`/`media_pilot` passed a separately approved ten-generated-file
source registration test and native reopen/normal-quit acceptance. The original
one-WAV scope and approval remain narrow. The pure row/capture/identity code now
lives in `soundbox.native_registration_plan`, `soundbox.native_media` and
`soundbox.native_identity`; research wrappers reuse it. The separate general
`soundbox.rekordbox_registration_store` passes eleven encrypted synthetic source tests
(including nine actual exits and shared-domain exclusion) and complete native
candidate replay on TEMP only at that checkpoint. Ordinary registration later
received its own accepted production authority and desktop integration; the
research pilot itself supplies neither. Initial supported WAV analysis likewise
uses a separately accepted production domain. Broader codecs/gain/replacement
remain gated; HANDOFF records current evidence/provenance.


## Requested saved components on the two real imports (October31)

`component_analysis.py` is a separate pure PCM16/24 WAV saved-grid/key/waveform
planner. It preserves Length and existing musical data, creates no mixer/gain or
cue rows and requires the packet grid to equal the complete saved result. The
standalone `component_pilot.py` is fixed to the two real native IDs/UUIDs/audio
hashes/BPM/keys requested by Sonny, with new exact plan/authorization/recovery
formats. It does not widen `analysis_pilot.py`, the fixed generated-WAV gain cache,
registration permission or the TEMP-only SQL adapters. Prepared pairs can chain
on copies before either publishes. The source path remains independently guarded
by closed apps, the common lease/journal, immutable audio identity, private paired
encrypted evidence and exact DB/directory recovery. Its SQL authorizer excludes
gain/mixer/cue/audio/XML/playlist and unrelated metadata changes.

The current two-song request supplied the new semantic authorization; the receipt
explicitly distinguishes that from user review of a plan hash. Both publications
committed and passed native exact Collection search, paused deck load and normal
quit: WHOLE174/9A (561 beats), Innerbloom140/6A (721 beats), six exact packets and
all non-registry tables including memberships preserved. Seven native registry
housekeeping differences remain explicit. This accepts these two transfers only;
the two-song pilot itself has no production authority loader or automatic
desktop publisher. The later independently accepted ordinary WAV analysis domain
is installed; it does not grant general codec/gain/partial-component permission.
Current evidence and installed state are in HANDOFF. Synthetic tests stay TEMP-only; no new songs enter the user
library for this work.
