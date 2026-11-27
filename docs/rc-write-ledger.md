# RC native/device write coverage ledger

Initial inventory, November26,2026, against production code `d8ce06c` (personal180).
This is a partial family-level map for [the RC checklist](rc-beta-checklist.md),
not the completed action-by-action ledger required by RC-001, WRITE-001–003 and TEST-001. Test references
identify existing coverage, not fresh passes unless explicitly recorded below.
Source validation, installed delivery, independent native reopening and physical
firmware acceptance remain separate evidence.

## Shared ownership and evidence boundaries

[TagEditController](../desktop/tag_controller.py) serializes source jobs through
`_run_source_job` and retains workers through finished/join and canonical ACK.
[Native transaction mechanics](../soundbox/native_transaction.py) provide fresh
source guards and the shared writer lock; each store retains its independently
accepted exact-library authority, constrained mutations, journal and recovery.
The lease itself grants neither another domain's SQL nor recovery permission.

[DeviceOperationCoordinator](../soundbox/device_operations.py) owns a captured
mount/operation through finalization; [DeviceSession](../soundbox/sync.py) and
[CdjSession](../soundbox/cdj_transaction.py) guard individual filesystem accesses.
Coordinator serialization does not protect against an external file/catalog
arrival. A previously verified copy, an earlier vacancy check or whole-set
recovery preflight cannot authorize a later replacing rename or path-only unlink.

## Initial family inventory

| Write family / entry | Controller and durable intent owner | Publication and readback | Durable ACK / recovery | Existing coverage entry points |
| --- | --- | --- | --- | --- |
| Title/artist, aliases and Clean/Keep derivation | [MetadataController](../desktop/metadata_controller.py), `pending_metadata_edits`, source-truth generations; shared source lane | [SourceMetadataStore](../soundbox/rekordbox_metadata_store.py); complete fresh library readback and exact native ID/UUID/path plus paired values; bounded groups preserve order | Sparse canonical ACK and provenance, `metadata_receipt_acks`; store recovery and exact receipt retirement | [store](../tests/test_rekordbox_metadata_store.py), [controller](../tests/test_metadata_controller.py), [group readback](../tests/test_metadata_batch.py), [state ACK](../tests/test_metadata_async_state.py) |
| My Tag assignment/removal | [TagAssignmentIntents](../desktop/tag_assignment_intents.py), `pending_tag_assignments`; [TagEditController](../desktop/tag_controller.py) | [LiveTagStore](../soundbox/rekordbox_tags.py) / [native writer](../soundbox/rekordbox_write.py); verified tag readback before projection | Ordered tag-state lane and exact head ACK; shared journal dispatch | [intent ownership](../tests/test_tag_assignment_intents.py), [store](../tests/test_rekordbox_tags.py), [writer recovery](../tests/test_rekordbox_write.py) |
| Tag create/delete/rename/order | [TagDefinitionController](../desktop/tag_creation_controller.py); separately admitted structural jobs | [definition/deletion](../soundbox/rekordbox_tag_definitions.py), [rename](../soundbox/rekordbox_tag_rename.py), [order](../soundbox/rekordbox_tag_order.py); refreshed tag/category identity verification | Canonical acknowledgement precedes complete library publication; separate authority files/formats dispatched by tag recovery | [definitions](../tests/test_rekordbox_tag_definitions.py), [delete recovery](../tests/test_tag_deletion_recovery.py), [rename](../tests/test_rekordbox_tag_rename.py), [order queue](../tests/test_tag_order_queue.py) |
| Native playlists/folders, tag-ANY smart creation, admitted name/order/moves and membership | [PlaylistEditController](../desktop/rekordbox_playlists.py), `rekordbox_playlist_edits`; `SourceIntentAdmission` / `SourceQueueAcknowledgement` | [PlaylistStore](../soundbox/rekordbox_playlist_store.py); fresh catalog and exact committed revision edge, XML/clocks and selected Collection pins; broader existing smart-rule edits remain a separate gap | Sparse queue/canonical ACK before catalog publication; recover before each source job, refuse unproved tail rebase | [transitions](../tests/test_playlist_transition_proof.py), [tree delete](../tests/test_playlist_tree_delete.py), [sparse ACK](../tests/test_playlist_sparse_state.py), [membership](../tests/test_playlist_batches.py) |
| Native cues and grids | [PrepEditController](../desktop/prep_controller.py), `pending_prep_edits`; [GridEditController](../desktop/grid_controller.py) and shared tag lane | Independently admitted [cue](../soundbox/rekordbox_cue_store.py) and [grid](../soundbox/rekordbox_grid_store.py) stores; exact requested component digest/native receipt readback | [PrepStateLane](../desktop/prep_state.py) retains accepted queue state; cue/grid formats keep separate recovery and companion-read rules | [cues](../tests/test_rekordbox_cue_store.py), [grid](../tests/test_rekordbox_grid_store.py), [request state](../tests/test_prep_request_state.py), [async lifecycle](../tests/test_prep_async_pipeline.py) |
| Direct import registration and native membership | [SharedImportController](../desktop/shared_imports.py), `shared_import_jobs`; shared registration lane | [RegistrationStore](../soundbox/rekordbox_registration_store.py), then independently admitted PlaylistStore; exact committed bindings and native membership readback | Canonical progress/binding ACK; retained native receipts make retry idempotent; cancellation has its own reconciliation | [registration store](../tests/test_registration_store.py), [controller](../tests/test_native_registration.py), [import/analysis](../tests/test_import_analysis_flow.py) |
| Initial WAV / CBR MP3 analysis publication | [NativeAnalysisController](../desktop/native_analysis.py), `native_analysis_jobs`; shared analysis lane | Separate [WAV](../soundbox/rekordbox_analysis_store.py) / [CBR](../soundbox/rekordbox_cbr_analysis_store.py) profiles, prepared DB/XML/ANLZ plan and exact source-song readback | Canonical publication receipt and queue ACK; prepared-plan recovery/retirement remain profile-specific | [WAV store](../tests/test_analysis_store.py), [CBR store](../tests/test_cbr_analysis_store.py), [readback](../tests/test_native_analysis_readback.py), [controller](../tests/test_native_analysis_controller.py) |
| Conversion and native locator change | [MediaConversionController](../desktop/media_conversion.py), `media_conversion_jobs`; shared media lane | [ConversionStore](../soundbox/media_conversion_store.py) preserves originals; independent [LocatorStore](../soundbox/rekordbox_locator_store.py) updates native location | [jobs.acknowledge](../soundbox/media_conversion_jobs.py), then canonical save; per-store recovery exists, while independent startup discovery and rebinding remain GAP-005 audit work | [conversion](../tests/test_media_conversion_store.py), [locator](../tests/test_locator_store.py), [ACK](../tests/test_conversion_acknowledgement.py) |
| Confirmed Collection/native removal and Trash disposal | [RemovalController](../desktop/removal.py), `graveyard_delete_intent`; shared removal lane | [RemovalStore](../soundbox/rekordbox_removal_store.py), exact review/confirmation, DB/XML/resource edges and tagged audio; native-only route verifies absent native IDs | Exact committed receipt then canonical hidden/disposal ACK; `_recover_locked` owns crash reconciliation. **GAP-002 reproduced below.** | [store/recovery](../tests/test_removal_store.py), [controller](../tests/test_collection_removal_controller.py), [native-only](../tests/test_collection_native_removal.py) |
| CDJ USB Export/Overwrite, Configure, settings and selected-playlist Delete | [ExportWorker](../desktop/workers.py), [USB setup](../desktop/usb_setup.py), MainWindow device actions; mount-owned device lane | [CDJ planner](../soundbox/cdj_export.py), [Delete planner](../soundbox/cdj_delete.py), [transaction commit/recover](../soundbox/cdj_transaction.py); candidate hashes, pinned catalogs and manifest-last publication | Device journal plus exact local receipt; joined result and owned cleanup. **GAP-001 publication/recovery races reproduced below.** | [transaction](../tests/test_cdj_transaction.py), [Delete](../tests/test_cdj_playlist_delete.py), [setup](../tests/test_cdj_setup.py), [device lifetime](../tests/test_cdj_cancellation_lifecycle.py) |
| Quiet automatic USB preference gestures | [USB settings controller](../desktop/usb_settings.py), shared canonical intent admission/ACK, attachment/source/repository capture and physical device lane | Existing CDJ settings planner/transaction, fresh source separation and native field readback; no source DB publication | Durable `usb_settings_edits`; joined readback before ACK, newer drafts retained, inline failure/Retry/Discard, Quit/maintenance and Eject ownership. Existing GAP-001 remains open. | [controller](../tests/test_usb_auto_settings.py), [actual UI routing](../tests/test_usb_settings_ui.py) |
| Stock iPod Export/Sync, selected Delete and playlist order | [catalog Sync](../desktop/stock_catalog_sync.py), [order](../desktop/stock_playlist_order.py), device workers; selected saved-generation capture | [stock plan/commit/delete](../soundbox/stock_ipod.py); exact captured catalogs, verified copies/artwork, validated rendered iTunesDB published last | Current-operation recovery/outcome, shadow/witness completion; no-op acceptance is separate. **GAP-001 acknowledged-copy compensation remains unsafe.** | [stock commit](../tests/test_stock_ipod.py), [Delete recovery](../tests/test_stock_delete_recovery_status.py), [selected generation](../tests/test_stock_export_sync_batch.py), [no-op guards](../tests/test_stock_noop_acceptance_guards.py) |
| Rockbox / dual stock+Rockbox | Device workers and selected capture; same physical mount lane as stock/USB roles | [dual planner/commit](../soundbox/dual_ipod.py), [sync filesystem session](../soundbox/sync.py), profile-specific media/art policies | Outer transaction owns inner stock result and exact current-operation recovery; composite rollback and shadow truth require their own oracles | [dual](../tests/test_dual_ipod.py), [cancellation](../tests/test_dual_cancel_recovery.py), [Rockbox capture](../tests/test_rockbox_session_capture.py) |

This first pass does not enumerate every gesture, supported field, request variant,
writer SQL table or syscall. Canonical state/backup/restore, artwork/provider writes,
remaining exporter sibling primitives, every family's failure boundaries and
source/Restore/Quit matrix still need individual ledger rows and independent
oracles. No RB6/7 native-app reopen or physical-device result follows from this map.

## GAP-001 and GAP-002 — current disposition, November26

All four initial probes below ran against installed180's production baseline with
existing generated TEMP builders. No runtime/test code changed for those probes; no real AppData, native library,
network, media collection, mounted device or real app was accessed. The private
reproducer injects an arrival at the actual final publication/rollback boundary,
then asserts its loss; this confirms a defect rather than testing a proposed fix.
Each fixture and its DB/audio payloads retired through TemporaryDirectory cleanup.
Descriptions below retain the initial baseline trigger; dispositions distinguish
the current source fixes from still-open families.

| Checklist gap | Current reproduction and production boundary | Disposition / next required oracle |
| --- | --- | --- |
| GAP-001: CDJ expected-absent publication | Actual `make_plan`/`commit`; create foreign media after `_check_baseline` but before `CdjSession.atomic_copy`. `_publish` uses `device_staging.staged_output` → replacing `os.replace`. Commit succeeds, foreign bytes disappear and the journal retires. | **Confirmed open, P1.** Atomic no-replace admission for expected-absent leaves; exact ownership for intended replacements. Existing pre-plan conflict tests do not cover this late arrival. |
| GAP-001: CDJ recovery | Crash an actual commit after a native catalog publication; whole-set `recover` preflight succeeds; inject foreign bytes immediately before saved backup restoration. `CdjSession.rename` replaces them and the journal retires. | **Confirmed open, P1.** Claim/verify exact current occupants, no-replace restore and retain conflicting evidence. A whole-set hash preflight alone leaves this race. |
| GAP-001: stock acknowledged copy | Actual two-song stock plan/commit verifies and accepts a new copy; `on_track_committed` installs a different foreign inode; a later iTunesDB write fails. The `copied` list contains only paths; rollback unlinks the replacement and reports `rolled back`. Original synthetic iTunesDB stays exact. | **Confirmed open, P1.** Acknowledged-copy compensation needs current ownership and catalog-reference proof, plus claim-before-disposal. `NewFilePublication` protects unaccepted live descriptors only. The older externally adopted unchanged-copy sibling remains documented but was not freshly replayed in this pass. |
| GAP-002: native removal restoration | Actual generated encrypted `RemovalStore.apply`; Trash move succeeds then raises; another audio file arrives after `_recover_locked` vacancy preflight and before `os.rename(trashed,path)`. Rollback overwrites it and retires its journal; original synthetic DB stays exact. | **Confirmed open, P1.** Atomic no-replace audio restoration with pinned parents; collision retains both audio entities and actionable recovery. Audit resource/XML restoration and sibling rollback primitives separately. |

Subsequent source disposition: stock acknowledged-copy rollback now captures an
exact entity inside the verified copy receipt and retires it through a no-replace
claim with fresh catalog-reference barriers. Replaced, changed or externally
adopted media retains recovery attention. Removal adds no-replace audio/resource
restoration and durable XML entity claims. Follow-up original-parent/audio-entity
guards and equal-byte foreign-resource claim retention pass51 affected cases,
including five actual cold subprocess regressions. Resource republication without
an original claim and final claim retirement remain open boundaries. These are
source changes with fixture coverage, not installed or native UI acceptance.

CDJ's attempted ownership fix failed independent review and the generated exFAT
positive publication test. It was quarantined privately; the transaction module
and tests are restored to baseline. Both CDJ rows above remain current open P1
failures. Atomic no-replace support cannot be assumed across filesystems; an
unsupported primitive, equal-byte digest match or a fresh preflight does not
complete the portable publication/recovery protocol. No support was dropped and
the positive FAT32/exFAT oracle remains intact.

The six nearby existing controls all passed once: CDJ pre-plan unowned conflict
and crash recovery; removal pre-admission drift/shared-file retention and error
after atomic Trash rename; stock database-failure cleanup; device copy/source-drift
publication. These controls omit the final-boundary foreign arrivals above, so
their success and the earlier complete release gate do not close either gap.

Compact evidence: `~/Library/Logs/Soundbox/rc-20261126/gap-001-002-results.json`;
private reproducer SHA256 `f8e5f5ff2342d7b85b3da917406e288f7bf56e6ca56a1b88e8985ee12080bfb8`.
Initial reproduction: `PYTHONPATH=. .venv/bin/python` plus that directory's
`prove_gap_001_002.py`. It creates only owned temporary fixtures and asserts the
baseline loss; it is diagnostic evidence, not a fix-acceptance test. Its recorded
result was four preservation failures plus six passing existing controls. Current
stock/removal fixes use the dedicated regression modules above; do not replay a
loss-asserting script and treat its changed outcome as acceptance. Installation of180 and
its preserved native curation do not certify the RC's destructive operations.

See the [critical ledger](critical-audit-2026-11.md)
for the original findings and broader unfixed families. The [RC checklist](rc-beta-checklist.md)
retains both gap checkboxes until focused fixes and independent recovery/ownership
regressions actually pass.

[November26 RC results](rc-beta-results-2026-11-26.md) record accepted local delivery
and the separately confirmed display/manual-ACK and installer-preflight defects.

## Follow-up recovery and Restore reproductions — November26

Six generated process-loss conversion/locator journals demonstrate that startup
recovery discovery omits their independent authorities. Actual RefreshWorker
preflight admits its reader with the journal still present; the existing common
dispatcher correctly recovers every fixture when called. The pending source fix
adds only the existing authority loaders to discovery, retaining fresh exact-source
permission and each domain’s recovery/SQL limits. Unknown-journal preservation
and foreign-source refusal are separate required controls.

A real accepted folder TaskOwner also outlives the current Restore maintenance
boundary: releasing expansion after successful Restore durably resurrects an old
playlist/song. The pending fix refuses Backup/Restore while that owner is pending,
including queued finished delivery/join, preserving accepted import work before
a retry. The actual source-switch caller already blocks this owner; forced Library
replacement is not a demonstrated A→B→A bypass. These are owned-fixture findings,
not live-source tests or installed acceptance. Compact probe receipts are under
`rc-followup-20261126/` in SoundBox’s private logs.
