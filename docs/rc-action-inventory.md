# Personal beta action and state-write inventory

Static audit begun November26,2026 for RC-001 and WRITE-002, against runtime code
`d8ce06cb850b15147a2dfb49f778f58526538d37` (repository HEAD `86351c3` adds handoff
prose). This is an initial production registration map, not a completed UI walk
or acceptance pass. No app, live state, library, media, network or device was
accessed by this audit. Source registration does not prove native persistence.
Use the [checklist](rc-beta-checklist.md), [native inventory](shared-library-audit-2026-11.md)
and [critical findings](critical-audit-2026-11.md) for independent acceptance.

## Visible actions and gesture owners

Entries name actual constructors, menu builders or signal connections. `MainWindow`
means [desktop/app.py](../desktop/app.py); conditional entries retain their actual
source/profile/selection/provider guards. Export-target rows are generated from
current targets rather than hard-coded device names.

| Surface and registered actions | Production registration → owner | RC sections |
| --- | --- | --- |
| Search/clear, show playing track, Collection/Artists/playlist navigation, selection, sorting and resize | `MainWindow._build_ui`, `_on_search`, `_focus_now_playing`; [model](../desktop/model.py), [trees](../desktop/trees.py), [Artists browser](../desktop/artists_browser.py) | UI-001–006, META-013 |
| Inline Title/Artist edit; click-away, Enter/Escape, Tab/Backtab; table/Artists `Edit` | `_table_context`, `_artists_menu`, `_table_menu_edit`, `_artists_menu_edit`; [inline draft ownership](../desktop/inline_drafts.py), [track menu binding](../desktop/track_menu.py) | META-013/017, UI-003/005 |
| Track information; Summary/Info; Change artwork, Save, Close | `_open_track_info` → [TrackInfoDialog](../desktop/track_info.py) | META-014–018, ART-023/024 |
| Track-menu Blue/RGB/3Band; Beat Count Display; selected-song Export to USB; Analyze Track | [TrackMenuMixin._track_menu](../desktop/track_menu.py) → `_on_waveform_mode_changed`, `_set_beat_count_mode`, `_begin_cdj_export`, `_analyze_tracks` | UI-008/012, PREP-005–009, USB-006 |
| Previous/next; Play/pause; held Session CUE; held beat jumps and jump-length choices; waveform seek/zoom gestures | [PlayerBar.__init__](../desktop/player.py), cue/transport callbacks; `MainWindow` connects previous/next and track-ended | UI-004/006–012 |
| Autoplay upward/random/downward and off | `MainWindow._build_ui`, `_toggle_autoplay_mode`, `_advance_autoplay` | UI-008/009 |
| Bar start, grid left/right, double/halve, typed BPM and Quantize | `_build_ui` → `_edit_grid`, `_set_quantize`; [grid controller](../desktop/grid_controller.py), [prep grid](../desktop/prep_grid.py) | PREP-001–009, UI-010/011 |
| Hot/Memory page tabs and eight cue slots per page; set/delete/held preview | [CuePanel](../desktop/player.py), `empty_pressed` → `_edit_cue`; [cue session](../desktop/cue_session.py), [prep controller](../desktop/prep_controller.py) | PREP-001–004, UI-010/011 |
| My Tags checkboxes, Create New Tag, tag drag ordering, Rename Tag, Delete Tag | [MyTagsButton](../desktop/prep_tags.py) → `_assign_tag`, `_new_tag`, `_delete_tag`, rename signal, [save_order](../desktop/tag_order.py) | ORG-001–004/011 |
| New Playlist/Smart Playlist/Folder, Rename, Delete, node movement and song drag/drop | `_new_playlist_menu`; [RekordboxPlaylistsMixin._native_menu_actions](../desktop/rekordbox_playlists.py), [local folder menus](../desktop/playlist_folders.py); native controller or preserved local source path | ORG-005–014 |
| Retry/discard pending playlist, tag-definition, tag-assignment and import work; retry cue/grid/analysis/metadata saves; pause/resume background song updates | [RekordboxPlaylistsMixin._native_menu_actions](../desktop/rekordbox_playlists.py), [intent review](../desktop/intent_review.py) → each owning controller | WRITE-012–019, UI-015 |
| Open XML, Refresh from rekordbox; start/stop cover fetching; Rescan missing covers/avatars; Suggest artists for Unknowns; Suggest artist merges | `_rb_header_menu` → named refresh/art/suggestion methods; provider admission remains conditional | META-001–012, ART-001–003/028, STATE-006 |
| Open in Finder; Fix Missing Links; Analyze one/many; Remove from Playlist; Remove from Collection | `_table_context`, `_add_track_file_actions`, `_add_track_reanalysis_action`, `_add_track_removal_actions` | PREP-010–023, ORG-010/012/013 |
| Choose cover; Fetch cover now; Set cover to artist profile; Remove cover; artist merge and playlist-add choices | `_table_context`, `_artists_menu` → source-bound [track menu callbacks](../desktop/track_menu.py) | ART-001/018–025, META-006/007 |
| Cover picker URL/file/candidate/Cancel | [ArtPickerDialog](../desktop/artpicker.py) → `_use_url`, `_use_file`, `_use_thumb`; [ArtworkRequests](../desktop/artwork_requests.py) publishes owned result | ART-014–024/028 |
| Review Prepare suggestions/Stop/Check again, Keep, Replace, Undo, Next; choose another cover/profile; refresh/resume/recheck; exclusions | [ArtReviewDialog](../desktop/artreview.py) action connections, review workers and current cache binding | ART-004–020/024/027/028 |
| Artist picture Rename, URL/file, Delete avatar; default photo Add/Delete; uploader Add/Remove | [artist catalog](../desktop/artistcatalog.py), [default photos](../desktop/defaultphotos.py), [exclusions](../desktop/uploader_exclusions.py) | ART-021/025–028/031 |
| Suggestion Stop/accept checked/Cancel; merge Apply/Cancel/Unmerge | [suggestion dialogs](../desktop/suggest.py) → current captured callback; metadata controller owns native intentions | META-006/007/012/017, ART-025 |
| Browser column visibility menu, header drag/width and per-playlist layout | `_header_menu`, `_set_column_hidden`, header connections and [layout owner](../desktop/browser_columns.py); visibility/order/width persist per exact playlist while Collection/Artists share the default. Installed; Sonny confirms movement persists across normal Quit/reopen and device headers look consistent. Device frames stay independent. | UI-001–003 |
| Export dialog playlist/target checks, Export/Cancel; transfer Cancel | [ExportDialog](../desktop/export_dialog.py), `_show_export_dialog`, `_cancel_export`; serialized target batch | USB-003/006–015, IPOD-002–007/012/014 |
| Generic USB menu Export, Delete playlists, Reformat USB, Eject; companion iPod USB keeps Export/Delete/Eject | `_device_menu` generic-role early return; Reformat is installed below Delete with exact mount/role/source guards. [USB playlist footer/context](../desktop/usb_device_view.py) now has source-only explicit folder subtree/empty-root checks; nested export roots preserve canonical source ancestry. | USB-001–005/008/017/018/022/023 |
| Stock/Rockbox Browse, Export; stock preview; Verify/Deep Verify; dual Repair; eligible non-stock Erase; Sync and Eject | `_device_menu` later profile-specific branch → each operation; [stock playlist context/footer](../desktop/stock_device_view.py) | IPOD-001–016, USB-005/022 |
| USB Overview/Playlists/Category/Sort/Column/Color/My Settings; automatic quiet field application; load/promote global My Settings | Source follow-up removes Save to USB. [USB view](../desktop/usb_device_view.py), [USB preferences](../desktop/usb_preferences.py), [quiet durable controller](../desktop/usb_settings.py) and [CDJ mixin](../desktop/cdj.py) retain failed drafts/Retry, source/mount fences and joined acknowledgement. First entry after launch opens Overview for USB/iPod. | USB-004/018–023 |
| New USB Configure/Not now; fresh Library/Cleanup/Artwork pages; Choose library/Back/Next/Cancel | [USB setup](../desktop/usb_setup.py), [OnboardingDialog](../desktop/onboarding.py), [pure onboarding](../soundbox/onboarding.py) | USB-002, INSTALL-005–009 |

The actual player-layout selector contains **1 Player**, **2 Player**, **Simple**
and **Browser**, with every entry after 1 Player disabled in `PlayerBar.__init__`.
Phrase analysis is likewise visible but disabled. This audit does not present
these entries as working additional layouts or analysis capabilities.

## Settings controls

[SettingsPageMixin._settings_page](../desktop/settings_page.py) registers six tabs:
General, Analysis, My Settings, Keyboard, Graveyard and Connections. The back
button calls `_close_settings`; tab changes refresh connection presentation.

| Tab/control inventory | Owning action/commit path | RC sections |
| --- | --- | --- |
| General appearance: accent picker/hex/Reset; White/Black/Custom text and custom picker | `_apply_theme_color`, `_apply_theme_text`; [theme controller](../desktop/theme_controller.py), saved [OS icon](../desktop/app_icon.py) | UI-001, STATE-002/008 |
| General visibility: cue/placeholder hiding, keyless hiding, short-track toggle and ± duration | `_on_hide_toggled`, `_on_sub_toggled`, `_step_secs` → `_apply_and_refresh` | UI-003, META-009, STATE-002 |
| General cleanup: generated named rule toggles; add word/regex; Remove word | `_on_rule_toggled`, `_add_drop_word`, `_remove_drop_word` → `_commit_title_policy` → metadata controller | META-009–012, WRITE-011/016 |
| General artwork: automatic missing covers; Review covers; Open artist catalog; Manage photos | `_on_auto_artwork_changed`, `_open_art_review`, `_open_artist_catalog`, `_open_default_photos` | ART-001/002/025/028/035 |
| General backup/update: Back Up, Restore, Check for Updates → Download installer | `_backup_library`, `_restore_from_backup`, `_check_for_updates` | STATE-001–005/010, INSTALL-012–016 |
| Analysis: BPM/Grid, Key, disabled Phrase, BPM range, Memory Cue at first beat, automatic new imports, Performance/Normal/Power saving | [AnalysisControls](../desktop/analysis.py) → `_save_analysis_preferences`; explicit analysis uses captured options | PREP-005–009/011, WRITE-012, UI-013 |
| My Settings: player DJ/display/indicator and mixer DJ/brightness groups; Save Global | [MySettingsEditor](../desktop/usb_preferences.py) → global-profile save; USB view separately saves selected device changes | USB-018/020/021, WRITE-002/003 |
| Keyboard: add/change/unbind per action; conflict move, per-action reset and Reset all; capture Save/Cancel | [KeyboardPage](../desktop/keyboard_page.py) → `_save_keyboard`, then replace live router after successful save | UI-010/011/015, STATE-002 |
| Graveyard: filter/sort/multiselect, Load Track, Restore, Delete from Collection and disk | `_grave_menu`, `_grave_restore_selected`, removal controller; [GraveyardController](../desktop/graveyard.py) | PREP-019–023, STATE-003/006 |
| Connections: choose DB/use default/Open XML/Refresh; personal Connect/Edit/Refresh likes/Disconnect/Cancel | [RekordboxSection](../desktop/connections.py), [SoundCloudSection](../desktop/connections.py) → `_ConnectionJob`/TaskOwner and provider service | INSTALL-006–009/019, ART-003, STATE-006/009 |

My Settings' 44 registered keys come from `GROUPS` and the versioned
[field catalogue](../soundbox/my_settings_fields.json), including model-specific
variants. A manual pass must exercise every offered value and unknown preserved
value on its actual model. Backend existence is not a visible-action proof:
`CdjMixin._import_usb_my_settings` and `_reformat_usb` exist, but this pass found no
production menu/button caller to either in that initial snapshot. The later
personal182 delivery restores **Reformat USB…** in the Overview footer and, at
Sonny’s subsequent request, below Delete playlists in the generic USB context
menu. The installed Overview flow has actual user/readback acceptance; menu
placement has focused coverage and its separate manual check remains unclaimed.
`_import_usb_my_settings` still has no offered caller; USB Save to Global /
Load Global are the actual profile actions. The separate
[physical campaign](usb-physical-results-2026-11-26.md) records backend checks and
the remaining installed/manual acceptance for USB-018/023.

## Keyboard catalog

[COMMANDS](../desktop/keyboard.py) defines 50 actions after expanding its slot/mode
comprehensions. [MainWindow._dispatch_keyboard_action](../desktop/app.py) routes
them; `KeyboardRouter` owns physical press/release, focus boundaries and held
aliases. The settings editor consumes that same catalog.

| Group | Exact catalog IDs | RC sections |
| --- | --- | --- |
| Transport | `transport.play`, `.cue`, `.previous`, `.next`, `.back`, `.forward` | UI-008–011 |
| Beatgrid | `grid.downbeat`, `.left`, `.right`, `.double`, `.halve`, `.quantize` | PREP-003/004, UI-011 |
| Cue pages | `cues.hot_page`, `.memory_page`, `.toggle_page`, `.set`; `cues.slot.0`–`.7`; `cues.delete.0`–`.7` | PREP-001/002, UI-010/011 |
| Library/browser | `browser.select_all`, `.rename`, `.load`, `.search`, `.playing`; `library.refresh`, `.playlist`, `.import` | META-013, ORG-007/012, PREP-010, UI-003/005 |
| Application/waveform | `app.settings`; `wave.zoom_in`, `.zoom_out`, `.BLUE`, `.RGB`, `.3BAND` | UI-011/012 |
| Autoplay | `autoplay.up`, `.random`, `.down`, `.off` | UI-008/009 |

Ordinary inline text editing, browser arrow navigation, drag/drop and header
input are separate gesture paths, not extra keyboard-catalog actions. Native
macOS app/menu shortcuts and focus/modal interactions still need a real manual
walkthrough; this count does not certify their complete inventory.

## Background services and durable state families

These are ownership entry points to expand into the per-boundary write ledger.
They describe the current code, without asserting successful failure/restart or
native acceptance for every path.

| Trigger/family | Current owner, mutation and publication boundary | Readback/recovery/remaining scope | RC sections |
| --- | --- | --- | --- |
| Ordinary curation save, per-song state, import records, aliases and source-bound queues | `MainWindow._save_state` → [StateRepository.save/commit_patch](../soundbox/state_repository.py); [state records](../soundbox/state_records.py); shared [StateArea.lock/publish](../soundbox/state_io.py) | Manifest/record validation; `.state-transaction` and `.state-retired`; native ACK remains an independent source-controller step | WRITE-002/014/018/019, STATE-001–007 |
| Small UI preferences and ordered canonical deltas | `_schedule_settings_save`/`_flush_settings_preferences` → [StateCommitter.queue_preferences/_submit](../desktop/state_commit.py), owned `_CommitTask` | Repository generation/revision and exact pending-value checks; joined continuation; errors retain unsaved work; Quit flushes preferences | WRITE-009/011/019, STATE-002/008 |
| Alias/cleanup/metadata successor generations | [MetadataController](../desktop/metadata_controller.py), [source truth](../soundbox/source_truth.py), [alias intentions](../soundbox/alias_intents.py), [provenance](../soundbox/metadata_provenance.py) | Canonical intention precedes the separate native writer/readback/ACK; masks and newer gestures retain their identities | META-003/006–012/017, WRITE-011/014/016 |
| Imported paths and automatic locator repair | `_repair_import_locations`, `_on_import_locations_repaired`, explicit `_fix_links`; [imports](../soundbox/imports.py), [shared import controller](../desktop/shared_imports.py) | Canonical imported records and native bindings are separate; stale completion/save-failure/uniqueness findings remain open | PREP-010–018, GAP-004–006 |
| Manual/automatic cover bytes, index, source ownership, blocks and defaults | [ArtworkRequests](../desktop/artwork_requests.py) and worker tokens → [ArtCache._art_mutation/store_ok/store_many/block](../soundbox/artwork.py); `_area.lock`, generation/art revision, `StateArea.publish` | Exact source/song/manual reservations before publication; index/image/default changes share state transaction; backend native cover publisher is still a separate gap | ART-001/018–031/035, STATE-003/004 |
| Cover review Keep/Replace/Undo and original ownership | [ArtReviewDialog](../desktop/artreview.py) → ArtCache review/snapshot methods | Current selection/snapshot guard and recoverable review data; completed review payload retirement needs its own lifecycle check | ART-020/024/027/028/031 |
| Artist pictures and default-photo pool | [ArtistCatalogDialog](../desktop/artistcatalog.py), [DefaultPhotosDialog](../desktop/defaultphotos.py) → ArtCache group/default publication | Source ownership, manual priority and shared state lock; rename/save refusal truth remains independent audit work | ART-021/025/027/031, GAP-007 |
| Disposable artwork comparisons and thumbnails | [ReviewScanStore](../soundbox/art_review_scan.py) writes SQLite checks/previews and LRU use; [image decoding](../desktop/image_decode.py), [thumbnails](../desktop/thumbnails.py) prepare display | Context stamps/epoch; bounded previews; cache rows grant no curated replacement or native authority | ART-008/011/016/022/026 |
| Browser/native-library/waveform/media derivative caches | `_prepare_library_processes`, [CacheProcess](../soundbox/cache_process.py), [NativeReaderProcess](../soundbox/native_reader_process.py); [atomic_cache_bytes](../soundbox/cache_io.py); [browser cache](../soundbox/browser_cache.py), [libcache](../soundbox/libcache.py), [track analysis](../soundbox/track_analysis.py) | Derived checksums/versions and per-source byte/epoch fences; no writer authority; per-cache invalidation and no-op writes need separate coverage | WRITE-002/004/006/008, PREP-008, UI-004/013 |
| Selected and library-wide preparation/analysis | [PlayerBar](../desktop/player.py) selected waveform/decode ownership; `_maybe_fill_waveforms` and [analysis jobs](../desktop/analysis.py); [NativeAnalysisController](../desktop/native_analysis.py) separately publishes accepted profiles | Complete selected surface/audio readiness, path/digest/epoch and generation checks; local results and native analysis ACKs are separate durable targets | PREP-004–009/011, UI-004/006/012/013 |
| Compatibility observations and accepted conversion/locator work | [MediaConversionController.discover/scan](../desktop/media_conversion.py), source-bound authority and common source lane; `media-format-cache.json` remains disposable | Actual original replacement, retained originals and locator SQL have separate accepted domains; inventory grants no conversion or native source authority | PREP-013–015, WRITE-002/018/019 |
| Graveyard identity archive refresh | [GraveyardController.prepare_library/scan](../desktop/graveyard.py) → [source-bound archive/snapshot](../soundbox/graveyard.py), joined TaskOwner | Historical rows are review evidence; archive source identity, backup inclusion and save-failure truth need their own tests, never source-write authority | PREP-021/022, STATE-003/006, GAP-008 |
| Optional account startup/refresh/connect/disconnect | `_prepare_connections` and [SoundCloudSection](../desktop/connections.py) own joined jobs; [SoundCloudConnection._operations/_lock](../soundbox_personal/connection.py); `_publish` replaces metadata/index JSON | Account-bound disposable index; no shared state transaction; old cache bytes/hash need their own observation proof, distinct from canonical curation | ART-003/010/012/028, STATE-009, INSTALL-019 |
| Credentials and refreshed account tokens | Provider [Keychain.write/delete](../soundbox_personal/connection.py), with system Security API | Keychain is separate from AppData/backups; no credential copying, logs or recipient-state reuse; failure/cancel tests remain required | ART-003, STATE-009, INSTALL-017 |
| Backup/Restore | Settings maintenance/drain → [export_backup/restore_backup](../soundbox/backup.py), area lock and staged validation/publication | Portable state plus selected art/history items; source/native DB/audio are not backed up; Graveyard-identity and accepted-import-drain gaps need explicit closure | STATE-001/003–005/010, GAP-008 |
| Explicit update check and installer download | `_check_for_updates` owns OneShotTask → [latest_release/parse_release](../soundbox/release_updates.py); approved URL opens through `QDesktopServices` | Application does not download/replace an app itself; browser/Installer own download/install. No startup updater, auto-quit or native source authority | INSTALL-003/004/012–016 |
| Theme preview/commit and saved icon | [SettingsPageMixin](../desktop/settings_page.py), [ThemeController](../desktop/theme_controller.py), [apply_saved_icon](../desktop/app_icon.py) | Commit saves canonical appearance; NSWorkspace owns root Finder/icon metadata, separate from signed Contents; OS metadata readback is separate evidence | UI-001, STATE-008, INSTALL-012 |

`MainWindow.__init__` additionally prewarms ordinary services, schedules imported
locator repair and optional account preparation, and installs timers for browser
warming, preferences, artwork status, waveform preparation, stock-art preparation,
mount notifications and device polling. Source controllers own pending queues and
fair scheduling on the shared native lane; [TaskOwner](../desktop/task_owner.py),
[Quit saves](../desktop/quit_saves.py) and each operation's controller retain joins.
USB catalog loading stays on demand; device polling is not generic USB Sync.

## Open inventory work

RC-001 and WRITE-002 remain open. Complete a fresh personal-build manual walk of
every dialog, dynamic submenu, disabled/conditional capability, native app menu,
parameter value, error/retry/cancel path and source/device role. Attach exact test
IDs and success/no-op/before-commit/after-commit/restart/Restore/Quit results to each
write boundary. The tables group generated controls; they are not one independently
accepted test result per control. No performance, provider-service, native-version,
clean-account or hardware acceptance follows from this static audit.

## Next bounded source-safe cases — November26 follow-up audit

Read-only inspection queued three owned-fixture cases for a later update: startup
conversion/locator recovery before native reads; Restore while a real folder
expansion worker is held; and portable historical Graveyard identity recovery
into an empty state root. These are static concerns, not reproduced defects or
completed acceptances. Existing adjacent recovery/maintenance/backup tests supply
the starting oracles. Exact entry points and reasoning are retained privately in
`rc-20261126/next-source-safe-cases-static.json` under SoundBox’s logs. They do not
broaden the current header/menu delivery or authorize live source/device tests.
