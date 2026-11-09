# Engine

Qt-free library, source protocols, analysis and device exporters.

- `library.py` / `engine.py`: source-neutral models and composition.
- `state_repository.py` / `state_io.py`: canonical state validation, locking and atomic publication.
- `native_transaction.py`, `cache_io.py`, `ipod_policy.py`: shared mechanics; domain rules stay separate.
- `rekordbox_*.py`: snapshots and independently authorized native edit domains.
- `source_acquisition.py` / `source_verification.py`: stable captured bytes and identity proofs.
- `track_analysis.py` / `waveform_sections.py`: waveform/cache facade and pure section codec.
- `stock_ipod.py`, `dual_ipod.py`, `sync.py`: distinct firmware planners and transactions.
- `cdj_*.py`: generic USB capture, native rendering and selected-playlist publication.
- `device_staging.py`: pinned descriptor IO shared by guarded device publishers.
- `providers.py`: explicit public/personal provider admission; never bypass it.

Keep source/device separation, independent authorities, fresh guards and recovery order intact.
Generic USBs have explicit export, no automatic sync status or idle catalog comparisons.

Run `./check.sh` from the root for types, immutable fixtures and all test phases.
Details: [architecture](../docs/ARCHITECTURE.md), [native prep](../docs/rekordbox-prep.md), [devices](../docs/DEVICES.md).
