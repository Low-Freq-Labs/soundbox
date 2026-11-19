# Engine

Qt-free library, source protocols, analysis and device exporters.

- `library.py` / `engine.py`: source-neutral models and composition.
- `state_repository.py` / `state_records.py` / `state_io.py`: canonical state, per-song records and atomic publication; portable snapshots include every record.
- `native_transaction.py`, `cache_io.py`, `ipod_policy.py`: shared mechanics; domain rules stay separate.
- `rekordbox_*.py`: snapshots and independently authorized native edit domains; prep companion reads grant no second write permission.
- `source_truth.py` / `alias_intents.py` / `metadata_provenance.py`: frozen pairs, raw scalar/ACK ownership and durable merges/resets.
- `mutation_domain.py`: revision-observed JSON containers for worker validation; plain snapshots grant no source authority.
- `worker_process.py` / `library_codec.py`: owned Qt-free helper transport; `cache_process.py` and `native_reader_process.py` keep encoding/readback outside GUI Python contention.
- `source_acquisition.py` / `source_verification.py`: stable captured bytes and identity proofs.
- `media_transition.py`: read-only actual replacement/recovery witnesses; CDJ policy does not admit or reject iPod media.
- `track_analysis.py` / `waveform_sections.py`: waveform/cache facade and pure section codec.
- `stock_ipod.py`, `dual_ipod.py`, `sync.py`: distinct firmware transactions; `stock_ipod_catalog.py` recognizes cached playlists independently of media ownership.
- `cdj_*.py`: generic USB capture, native rendering and selected-playlist publication.
- `export_snapshot.py` / `ipod_export_metadata.py` / `ipod_export_witness.py`: immutable effective iPod inputs and verified optional catalog correspondence; source-overlap guards remain complete.
- `device_staging.py` / `stock_delete.py`: cancellable descriptor IO and native indexed-entity deletion; recovery retains exact ownership.
- `providers.py`: explicit public/personal provider admission; never bypass it.

Keep source/device separation, independent authorities, fresh guards and recovery order intact.
Generic USBs have explicit export, no automatic sync status or idle catalog comparisons.

Run `./check.sh` from the root for types, immutable fixtures and all test phases.
Details: [architecture](../docs/ARCHITECTURE.md), [native prep](../docs/rekordbox-prep.md), [devices](../docs/DEVICES.md).
