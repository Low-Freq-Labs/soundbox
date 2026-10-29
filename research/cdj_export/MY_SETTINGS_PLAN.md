# Historical USB My Settings proposal

October28 proposal, superseded by the October29 production controls documented in
[the CDJ guide](../../docs/cdj-usb-export.md). Production uses44 fields with explicit
Save to Global / Load Global. Named presets, Follow Global/Custom and automatic
propagation below remain deferred design, not current behavior. The following
records the original offline proposal; it must not override current guidance.

October 28, 2026. Requested behavior and an offline prototype at that time.
Start with [RBXPORT_HANDOFF](RBXPORT_HANDOFF.md) for export safety, evidence and
the pinned dual-format source kit. This work adds no live USB write authority.

**Priority:** Sonny subsequently required actual playlist generation/comparison
before more testing. The [export fixture backend](EXPORT_PARITY.md) now passes
his approved native-equivalence gate; app integration and the production export
transaction remain ahead of further My Settings UI/native test work. Keep this
profile design and tempo proof staged for the developer; do not use additional
settings experiments as a substitute for completing the export path.

## Confirmed user interaction

Sonny's native screenshots show the current friction: global rekordbox
Preferences → DJ System → My Settings is editable, while the selected USB's
My Settings page displays values. SoundBox must allow editing directly on the
selected USB's My Settings page, with no detour into global preferences.

The user confirmed this model:

- **Global My Settings** holds defaults and named custom presets. An active
  global profile has a stable ID and revision; its values are logical settings,
  not a raw copy of another USB's files.
- Each USB is **Follow Global** or **Custom**. Global changes automatically
  reach compatible connected USBs following Global; Custom USBs keep their
  profiles. A disconnected follower receives the latest revision when it next
  connects, after confirming that its baseline is still the one SoundBox knows.
- A direct USB edit that differs from current Global saves that USB and makes
  it Custom. It does not silently edit Global or another USB. A no-op is a no-op.
- When supported USB values differ from Global, show
  **Save to Global My Settings** with a reviewable difference summary. Activating
  it promotes the USB's recognized values to the active Global profile, while
  retaining Global values for settings absent/unrecognized on that USB.
- Promotion triggers normal propagation to other Follow Global USBs. Other
  Custom USBs remain unchanged. The edited USB remains Custom unless the user
  explicitly switches it back to Follow Global; matching values alone do not
  establish future write intent. Hide the promotion button when values match.
- Switching a USB to Follow Global deliberately applies the active profile.
  An explicit Apply Global action may be offered for copying its current values
  while retaining Custom mode; the visual design remains Sonny's.

This is SoundBox's own export-profile system, not synchronization with rekordbox
desktop preferences. Its exported player Quantize fields and mixer Beat FX
Quantize are independent of rekordbox's global Quantize setting and SoundBox Q.
Never read/change that desktop global preference or drive the native UI remotely.

## State and propagation contract

Store named profiles and the active Global revision through SoundBox's normal
canonical state repository, preserving unknown extension data. USB binding state
records its explicit volume identity, mode, last applied profile/revision, and
the exact last-applied settings-file hashes. Volume names/device nodes are not
durable identities. Back up crown-jewel user state before any future migration.

Fresh explicitly created CDJ targets can start following the chosen global
defaults. Existing USBs without a binding preserve their current profile and
start Custom until the user chooses Follow Global. If a deck/another app changes
settings since the last applied baseline, preserve the new bytes and expose a
Custom state rather than overwriting them based on a stale follower flag.

Global profile saves persist before propagation. Queue immutable target/profile
intents through the shared serialized device lane, retaining the newest pending
Global revision per target. Keep ownership through QThread.finished and do not
write from UI callbacks, discovery, transport events or drift polling. Playback
itself is not a suppression condition. Resolve conflicts/failure/cancellation at
safe boundaries; an unfinished settings save must remain visibly pending/failed.
Profile status and managed-track Sync status are distinct.

Compare recognized logical field values for the promotion button, not file
hashes, checksums or padding. Preserve the raw per-USB baseline for publication.
Never clone one device's unknown bytes/version/header into other devices just
because their visible preferences agree. Unsupported fields stay visible as
unknown/read-only; missing profile files need an independently validated template
before creation, not a guessed default serialization.

## Prototype staged for the developer

- [mysettings_fields.json](mysettings_fields.json): candidate field catalogue,
  offsets and enum tokens from installed **pyrekordbox 0.4.4**, with source hash
  and documentation provenance. 22 player fields, 7 additional player fields,
  13 mixer fields. UI labels should be translated for people; do not expose
  byte offsets/hex or internal enum tokens in the product.
- [mysettings_codec.py](mysettings_codec.py): bytes-only `inspect` and `patch`.
  It reads no devices, user state, source library or global settings, and offers
  no file publication API. A patch requires the exact original SHA-256, known
  framing/version, valid CRC, a recognized field/current value and allowed choice.
  It changes only requested bytes and CRC, retaining every other original byte.
- [test_cdj_mysettings_codec.py](../../tests/test_cdj_mysettings_codec.py):
  hermetic checks of stale bases, corruption/framing, field/value validation,
  unknown-byte/value preservation, multi-field changes, no-ops, exact restoration
  and the distinct mixer checksum region. No private fixtures or live state used.

Usage entirely in RAM after the caller captures a local copy:

```python
from research.cdj_export.mysettings_codec import inspect, patch

view = inspect("MYSETTING.DAT", captured_bytes)
candidate = patch(
    "MYSETTING.DAT", captured_bytes, {"tempo_range": "sixteen"},
    expected_sha256=view["sha256"],
)
```

Use separate file-specific field sets for `MYSETTING2.DAT` and
`DJMMYSETTING.DAT`. Preserve `djprofile.nxs` opaquely. The existing DEVSETTING
and library-settings source handles other General/Category/Sort/Column/Color
concerns described in the handoff; this codec does not implement those.

The [pyrekordbox tutorial](https://pyrekordbox.readthedocs.io/en/stable/tutorial/mysetting.html)
describes reading/building these files. Its general `SettingsFile.build()`
constructs from defaults and known fields rather than preserving original unknown
bytes. Do not substitute it for a byte-preserving edit. These mappings are
research inputs, not native acceptance. In particular, newer controls or the
combined PLAY MODE/AUTO PLAY MODE label may contain additional behavior not
represented by this catalogue. Expose only what has actually been implemented
and tested; do not guess values for additional fields.

## Evidence and completed native tempo reference

Private evidence: `soundbox-cdj-ref/20261028-mysettings-offline-proof/proof.json`.
All **42** candidate fields decode from the captured native files. **155**
field/choice candidates parse through pyrekordbox and reverse to
the exact original file. Every candidate changes only its field byte and checksum.
The catalogue and comparison parser share schema provenance, so these checks
test implementation/preservation rather than independently validating native
field mappings. They are not rekordbox/CDJ acceptance.

The staged first candidate is
`20261028-mysettings-offline-proof/MYSETTING-tempo16-candidate.DAT`:
TEMPO RANGE **±10 → ±16**, absolute file offset **140 / 0x8c**, byte
**0x81 → 0x82**. Other differences are confined to the CRC at offsets 144–145.
The [rekordcrate tempo-range reference](https://docs.rs/rekordcrate/0.3.0/rekordcrate/setting/enum.TempoRange.html)
independently agrees on the enum values.

**Native reference comparison completed.** Sonny changed TEMPO RANGE to ±16
in real rekordbox and closed it. A guarded capture of the same FAT32 target is
preserved at `soundbox-cdj-ref/20261028-mysettings-tempo16-reset-fat32/`.
The complete 148-byte native `MYSETTING.DAT` is identical to both the candidate
prepared **before** his edit and a fresh application of the pure transform.
Native/candidate SHA-256:
`b2f847a830c5d7a67646e543c002a8726f27ff942c39c87cbffc8af92401b433`.

Only file offsets 140 (tempo) and 144–145 (CRC) changed in that profile. The
other four settings/profile files and all 15 analysis companions are unchanged.
Native application also changed the three database files' raw bytes; the
compared classic track/playlist fields and all 22 Plus table row sets are
unchanged. Preserve these collateral observations; do not claim that only one
file on the USB changed. The capture's `tempo16-native-parity.json` records the
exact comparison. Length/CRC, PDB structure, copy-only SQLCipher integrity and
all five snapshots' metadata hashes pass.

This validates **native output parity for one tempo edit**, not all 42 mappings,
actual SoundBox device publication, native reopening of a modified candidate,
recovery or physical behavior. `native_reference_checks` on that field in the
catalogue records the bounded evidence; it does not authorize any live write.
The original offline proof receipt remains an honest record from before this
native comparison.

**Keep Sonny's current ±16 native setting.** It was his deliberate UI change;
no old preference/profile is restored. The agent has made no USB/global writes
and has not read native desktop global preferences.

Next acceptance steps:

1. For other controls, collect one native field change at a time, explicitly
   targeting the disposable test USB. Native automatic application was enabled
   in the screenshots, so do not change Preferences or apply to other connected
   devices automatically. Sonny operates the native UI.
2. Compare each complete native result with a candidate staged from its exact
   captured base, and inventory every other file for collateral changes.
3. Design/review the dedicated settings publication and recovery contract before
   writing any candidate to a selected disposable USB. Leave ordinary exporters'
   `/PIONEER` guard in place. Native UI reopening and CDJ-3000 setting load/behavior
   are later gates; mixer and newer-player behavior require their own coverage.

## Production integration and acceptance checklist

The developer can build the logical profile model, field-driven USB editor,
Global/preset editor, difference summary/button and propagation planner against
fake/disposable inputs now. Real settings publication still needs its dedicated
CDJ `/PIONEER` contract; ordinary stock/Rockbox/generic exporters stay excluded.

Before live publication, capture/check exact target identity and file bases;
stage all affected settings files, validate resulting values/CRC/unchanged bytes,
retain recovery and publish as one coordinated settings job. Recheck the base at
commit. On failure, restore exact old files or retain an honest recoverable state;
never claim success for a partly applied multi-file profile. Settings-only edits
do not reload audio, rewrite library membership or invoke native DB source edits.

Required checks cover:

- USB edit → Custom; optional promotion → Global; every other Custom unchanged.
- Global revision propagation to followers, including a busy lane, unplug/remount,
  reconnect with a newer Global revision and external deck edits.
- Equivalent values with different padding/checksums hide the promotion button;
  missing/unsupported fields neither fabricate values nor wipe Global fields.
- Crash/full-target/base-change failures and exact recovery across the three DATs.
- Supported player/mixer values, native one-field deltas and physical behavior.
- Ordinary playlist sync and no-op profile application retain the exact profile.
- UI render/harness isolation: suppress real device discovery and never read
  actual AppData/preferences just because test state is separate.

Run focused prototype checks from the repository root:

    .venv/bin/python -m unittest discover -s tests -p test_cdj_mysettings_codec.py

Production UI/runtime work still requires the full project check/render and safe
build/install/relaunch gates. This research-only handoff does not require restarting
the installed app and does not implement the production profile state/queue/UI.
