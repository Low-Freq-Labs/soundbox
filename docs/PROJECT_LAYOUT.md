# Project layout and retention

Current source/install identity, checks and retention: [HANDOFF](HANDOFF.md).
Older delivery/storage inventories are [historical evidence](archive/project-layout-through-2026-11-05.md), not current cleanup instructions.

## Active checkout

| Location | Purpose |
| --- | --- |
| [soundbox](../soundbox/README.md) | Qt-free library, state, source protocols, analysis and exporters |
| [desktop](../desktop/README.md) | PySide6 views, controls, controllers and worker ownership |
| [native](../native/README.md) | Audio/Metal deck, process guard and Rust CDJ helper |
| [soundbox_personal](../soundbox_personal/README.md) | Explicitly admitted personal providers |
| [tests](../tests/README.md), [scripts](../scripts/README.md) | Isolated regression suites and maintained tooling |
| [research](../research/README.md) | Offline prototypes/proofs; excluded from production imports/packages |
| [assets](../assets/README.md), [profiles](../profiles/README.md) | Bundled visuals and validated package composition |
| [docs](README.md), [archive](archive/README.md) | Current references versus dated evidence |

Every authored folder has a short README. Vendor trees stay pinned; generated/private roots are described here rather than edited for guide coverage.

## Generated output and local dependencies

- `.venv/`: active pinned development environment; the installed app is independent of it.
- `build/native/`, `build/cdj/release/soundbox-cdj`: checked native outputs required by source runs/tests.
- `build/check/`: current full-gate inventories/results; `focused/` holds explicitly scoped exact/named-area checks and phase receipts, never complete-release proof.
- `build/rekordbox-write-proof/`: retained private copy-only proof evidence; no source authority.
- `build/personal/`, `build/public/`, `dist/`: reproducible packaging intermediates/candidates; preserve an unaccepted handoff until resolved.
- Python, mypy and Hypothesis caches are generated. Ignored never automatically means disposable.

Root build/check scripts recreate their own outputs. Cleanup must identify exact owned paths and preserve necessary evidence/inputs; do not sweep unrelated scratch, apps or user data.

## Protected inputs and user state

- `soundbox-ipod-ref/`, `soundbox-waveform-ref/`, `soundbox-cdj-ref/` are ignored private fixtures/research/recovery, not build trash.
- The CDJ root also holds the isolated Rust toolchain and required offline integration capture. Preserve them.
- [Tracked fixture manifests](../tests/fixtures/README.md) pin required inputs. Missing files fail preflight; never silently regenerate or replace them.
- Live AppData holds crown-jewel `library.json` and its referenced `.state-records/`, curated artwork/defaults, authorities, original conversions and native recovery. Never wipe or restore it for code cleanup. A manifest alone is not a complete backup; portable exports materialize all records.
- USB `.soundbox-cdj/transactions/` and Mac `cdj-recovery/` are transaction evidence, separate from application rollbacks.
- App rollback is one temporary previous bundle during installation, retired after accepted verification. HANDOFF records current retention; musical recovery has its separate rules.
- Newer user edits supersede every dated count/hash. Old receipts and completed installers are evidence, never replay permission.

Unresolved recovery and pending acceptance retain their required payloads. Sonny's November10 cleanup explicitly retires completed diagnostic duplicates and historical pilot DB snapshots outside current runtime recovery; compact proof receipts remain. Current writer recovery, pending captures, required offline fixtures and original music/conversions stay protected. Ordinary completed native transactions continue their accepted receipt-backed retirement rules.

Sonny renewed cleanup on November18. After the verified de410 install, the exact
reviewed manifest retired completed DB/icon/snapshot copies and reproducible
personal packaging/Cargo debug output: about2.6GB distinct file bytes. It kept
the one pending f502 fallback, six ambiguous/pending DB copies,73compact evidence
files, source/tests, current recovery, originals, pinned inputs and active
native/check/release outputs. `~/Library/Logs/Soundbox/cleanup-2026-11-18/`
contains the manifest and completion receipt; retired snapshot payloads must not
be treated as replay/recovery inputs. Newer curation was never restored over.

The final November18 wrap followed accepted installed iTunes export/reconnect/Sync.
It retired the sole old application fallback and regenerated personal distributions,
personal packaging and Cargo debug output:1,612,310,155distinct regular-file bytes.
The current installed app, `.venv`, active native/check/CDJ release outputs,
curation/originals/recovery and private fixtures remain. Exact guarded manifests
are in the current handoff's private `wrap-up/connected-wrap-cleanup-*` receipts.
This byte count is not a measured physical APFS space gain. No app control or
source/device write was used, and no duplicate candidate/previous app remains
from this delivery.

## Evidence and navigation

Cleanup review/gate/package receipts: `~/Library/Logs/Soundbox/codebase-cleanup-2026-11-08/`.
Older evidence locations remain in the historical layout record and dated audits; explicitly deleted archives are not recoverable inputs.
Do not create repeated full-library captures for routine checks. Keep receipts small and distinguish synthetic/offline, installed-state and hardware evidence.
See [runtime contracts](runtime-contracts.md), [release provisioning](test-release-gate.md) and [architecture](ARCHITECTURE.md).
