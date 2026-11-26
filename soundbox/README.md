# Engine

Qt-free library, source protocols, analysis and device exporters.

- `library.py` / `engine.py`: source-neutral models and composition.
- `state_repository.py` / `state_records.py` / `state_io.py`: canonical state, records and atomic publication; `artwork_identity.py` binds cover ownership and guarded saved-owner adoption.
- `native_transaction.py`, `cache_io.py`, `ipod_policy.py`: shared mechanics; domain rules stay separate. `artwork_observation.py` observes current cover choices without granting writer authority.
- `rekordbox_*.py`: independently authorized native edit domains; `rekordbox_installations.py` observes installed versions without granting permission. Membership replay composes accepted commands; companion snapshots grant no second write authority.
- `source_truth.py` / `alias_intents.py` / `metadata_provenance.py`: frozen pairs, raw scalar/ACK ownership and durable merges/resets; `metadata_cleanup_policy.py` binds Clean/Keep choices to exact sources, independently of write authority.
- `mutation_domain.py`: revision-observed JSON containers for worker validation; plain snapshots grant no source authority.
- `worker_process.py` / `library_codec.py`: owned Qt-free helper transport; `cache_process.py` and `native_reader_process.py` keep encoding/readback outside GUI Python contention.
- `source_acquisition.py` / `source_verification.py`: stable captured bytes and identity proofs.
- `media_transition.py`: read-only actual replacement/recovery witnesses; CDJ policy does not admit or reject iPod media.
- `track_analysis.py` / `waveform_sections.py`: current V5 waveform/cache facade and pure section codec; `rekordbox_analysis.py` reads bounded optional PWV4/PWV6 preview metadata.
- `stock_ipod.py`, `dual_ipod.py`, `sync.py`: distinct firmware transactions; `stock_ipod_catalog.py` recognizes cached playlists independently of media ownership; `stock_plan_observations.py` holds disposable negative-only facts/timings; `stock_sync_reuse.py` proves worker-only catalog no-op routing.
- `cdj_*.py`: scoped USB capture/rendering/publication; `cdj_usb.py` observes attachment setup without payload reads; `cdj_delete.py` removes selected native playlists and exact-owned unused assets.
- `export_snapshot.py` / `ipod_export_metadata.py` / `ipod_export_witness.py`: immutable effective iPod inputs and verified optional catalog correspondence; source-overlap guards remain complete.
- `device_staging.py` / `stock_delete.py`: cancellable descriptor IO and native indexed-entity deletion; `transaction_outcome.py` retires completed recovery payloads with exact ownership.
- `providers.py`: explicit public/personal provider admission; never bypass it.

Keep source/device separation, independent authorities, fresh guards and recovery order intact.
Generic USBs have explicit export, no automatic sync status or idle catalog comparisons.

Run affected area/depth and static checks locally; complete `./check.sh --render` before publication.
Details: [architecture](../docs/ARCHITECTURE.md), [native prep](../docs/rekordbox-prep.md), [devices](../docs/DEVICES.md).
