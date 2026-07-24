# SoundBox roadmap

Living to-do. Work top-down. The Rockbox iPod remains the hardware priority;
player work may continue in parallel when it is isolated from export safety.
For the live test count, trust `./check.sh` output—not any number written here.

## Current status (2026-07-24)

- **CORE LIBRARY/EXPORT COMPLETE + CURATION MONTH COMPLETE.** Every track title
  cut/cleaned, every cover art eye-verified. Engine is mypy-clean,
  `./check.sh` is the one-command gate, and the installed `Soundbox.app` is
  signed + current.
- **NATIVE PLAYER CAMPAIGN COMPLETE.** CoreAudio sample-clock playback and a
  120 Hz Metal deck now provide RGB overview/detail waveforms, Rekordbox beat
  grid, persistent zoom/jump controls, CUE/play/track navigation, precision
  scrub, clean gain ramps, and cache-first rapid loading. Full handoff:
  `docs/PLAYER.md`.
- **WAITING ON HARDWARE** for Phase 3 (iPod, ~end July).
- New frontier (2026-07-08): **CDJ USB export** → `docs/cdj-usb-export.md`.
- Read-only live-DB memory/hot cues now occupy a dedicated two-page 2×4 panel;
  V4 retains overflow cues beyond the eight visible slots.
- **V4 ANALYSIS FOUNDATION SHIPPED.** SoundBox now owns checksummed per-track
  grid/cue/RGB/24-band/ultra-waveform records, refreshes read-only cue metadata
  by fingerprint, imports valid Rekordbox analysis as a seed/fallback, natively
  analyzes missing data, and labels AAC/ALAC as player-only.
  Rekordbox is no longer a prepared-playback dependency.
- **SOUNDBOX RGB FILTERBANK SHIPPED.** Display color now comes from the
  dedicated overlapping 100 Hz low / 300–3000 Hz mid / 1500 Hz high analysis
  with frequency-dependent peak holds and local nonlinear RGB transfer.
  Renderers no longer remix the independently normalized 24-band master.
  Existing V4 records upgrade atomically one track at a time, preserving
  SoundBox-owned grid/cue rows and the established ultra silhouette.
- **PERFORMANCE/POLISH PASS SHIPPED.** Long-track preparation is bounded,
  cooperatively interruptible, and automatically battery-aware; playback and
  foreground interaction preempt it inside the current file. Static Metal
  rendering and native polling now sleep; waveform host, AppKit child, and
  Retina drawable resize in the same UI turn without generic child-resize wake
  storms; focus return re-synchronizes transport from the native snapshot. No
  cache schema or analysis-quality change was required.
- **UNIFIED ZOOM/SCROLL RENDERER SHIPPED.** Thirteen truthful scales now span
  24–1536 px/s. Overview, scrolling geometry, and RGB use one continuous,
  song-anchored LOD path; amplitude-weighted color mips remove far-zoom chroma
  fighting while the established maximum-zoom appearance remains intact.
  Scrub math consumes the native-applied scale, detail promotion no longer
  re-uploads color, render encoding does not hold the analysis mutex, and
  display cadence follows the active screen through resize/screen changes.
  The moving lane now rasterizes each final Retina pixel directly from one
  continuous song coordinate; there is no translated intermediate surface,
  second coverage/color filter, or texel-step cadence. Overview and ultra
  buffers make one atomic first-visible commit, so long sets do not inflate
  during detail promotion. A playing waveform grab captures the last drawable
  already presented, preserves its exact source frame through
  fractional-millisecond drag math, and hard-mutes/pauses synchronously on
  mouse-down; a stationary release resumes from exactly that held frame.
  Four midpoint taps now integrate both continuous-LOD levels across every
  physical pixel, closing the center-sample aliasing regression without
  reintroducing zoom branches. Ordinary Qt position
  publication remains idempotent and cannot wake the Metal run loop through an
  inactive preview.
- **PLAYLIST AUTOPLAY SHIPPED.** Header up/down modes walk the playlist snapshot
  that deliberately started the track; random uses a no-repeat shuffle bag.
  Browsing is independent, a new deliberate source replaces the snapshot, and
  only a true native end-of-media edge advances.
- **LOADED-TRACK QUALITY PREEMPTS BULK WORK.** A loaded cache miss keeps its
  UI responsive and immediately runs the exact canonical RGB/ultra analysis in
  a persistent foreground lane. Battery,
  Low Power Mode, playback, and interaction policy apply only to bulk backlog;
  below 50% a large backlog becomes gentler without changing one stored or
  rendered sample. Grid metadata now decodes DAT/PQTZ rows directly and never
  opens Rekordbox EXT/2EX data.
- **NATIVE-ONLY + V4-ONLY CLEANUP SHIPPED.** The Qt playback setting/path and
  schema-1/schema-3 waveform caches are gone. Qt Multimedia remains only for
  AAC/ALAC analysis. A strict read-only audit validated every reachable library
  track before the three retired cache directories were removed. The audit
  separately reports valid/playable V4 coverage and current ultra/RGB coverage,
  so safe legacy retirement never hides a still-running quality upgrade.
- **DEVICE PROFILES SHIPPED.** Read-only probes distinguish Rockbox, stock iPod,
  dual-boot, and generic USB layouts. FAT32 Rockbox/generic exports retain the
  guarded existing workflow. Stock iPod support is an honest no-write preview
  until an independently validated database writer exists. See `DEVICES.md`.
- **ROCKET-LOAD + RELIABILITY PASS SHIPPED.** Prepared V4 loads now stage
  manifest/packed overview/ultra detail off the UI thread, native buffer work is
  generation-guarded, track replacement clears every old renderer channel
  atomically, and the 0:00 RGB delay wedge is repaired without a cache recook.
  Session CUE uses five explicit transport states.
- **SYNC MANIFEST V2 SHIPPED.** A pinned device session revalidates every write;
  normalized path ownership, hashes, playlist membership, artwork, and
  completed generations make corruption repair and prune honest. Deep Verify,
  verified eject, FAT collision handling, and ordered device import into
  SoundBox Media are included. Normal Sync never prunes unknown content. An
  explicitly confirmed whole-device erase removes all children and the
  manifest last, then verifies and normally ejects; stock/dual-boot iTunes
  volumes remain read-only.

## Scorecard (~9.4/10, honest, not curved)

The core library/export system is mature; the residue is hardware-gated (iPod),
phase-gated (rekordbox 7 / Windows / notarization), or explicitly listed player
follow-up.

| Area | Score | Note |
|------|-------|------|
| Export (copy engine) | 9.5 | pinned target, atomic writes, hashes, FAT collisions, free-space/cancel honesty; last point is USB physics |
| Sync / re-sync | 9.7 | ownership-aware repair/prune, Deep Verify, completed generations, shared-file safe |
| Cover art | 8.5 | accuracy-first auto (0 wrong art), manual picker, derivation; ceiling = SoundCloud search + unreleased tracks with no art anywhere |
| Device detect & safety | 9.5 | off-thread scan, hardened guards, FAT32 warning |
| Live rekordbox read | 9 | read-only temp copy, sacred order, smart playlists; validate vs rb7 in Windows phase |
| UI/UX | 9.7 | collection/artist browsing, curation tools, persistent layout, native DJ-style player |
| Persistence | 10 | portable library.json by track ID + rotating backups + one-click full backup/restore |
| Tests | 10 | engine + hermetic UI + property-based (hypothesis) + live-DB mocks; `./check.sh` |
| Packaging/signing | 8 | stable self-signed; notarization for public (Phase 5) |
| iPod readiness | 7 | software side done; the rest is genuinely hardware-gated |
| Code cleanliness | 9 | monolith split into engine + desktop mixins; engine mypy-clean |

## Phases

### Phase 1 — Re-sync engine ✅ DONE (validated on USB)
Playlist `pid` on the model; per-device `manifest.json`; drift diff
(renamed/moved/changed/removed); `sync.resync` (idempotent re-export, stale
m3u8 cleanup, track-folder-level prune that keeps shared files, forget deleted);
drift UI ("⚠ N out of date" badge → Sync). Sonny's rename-v#/move workflow
"just works" because pid is stable.

### Phase 2 — Settings / polish ✅ DONE
Scoped FINALIZING (clean only written dirs); diff-driven resync; configurable
cue/placeholder hiding; cover-art workshop (accuracy-first matcher, ART column,
manual picker, artist/title derivation incl. trusted-gated reversed flip,
filename-stem fallback, alias/merge window, uploader-consensus suggester);
app.py split into modules; UI-layer tests; rotating + full .zip backup/restore;
graveyard; title-cleanup named rules; baseline-JPEG art enforcement.

### Phase 2.5 — Native player ✅ DONE

- [x] Two-row layout: metadata/search/settings above a full-width player.
- [x] Previous/next track, selectable beat/bar jumps, momentary CUE, play/pause.
- [x] Read-only Rekordbox PQTZ beat grid and PWV5/PWV7 RGB analysis.
- [x] Unzoomed whole-track overview plus thirteen truthful detail zoom levels
      from 24 through 1536 px/s; persistent zoom and jump selection.
- [x] Authoritative Rekordbox first-grid-beat loading, zero-anchored fallback
      when no valid grid exists, beat-aware jumps, and thresholded drag-only
      main-wave seeking with pause/live preview/resume semantics.
- [x] SoundBox-owned background caches for instant loads and high-detail
      all-zoom rendering; preparation pauses during playback.
- [x] Native arm64 macOS deck: AVAudioFile decode thread, lock-free ring,
      CoreAudio render callback, CAMetalDisplayLink, coverage-filtered Metal
      waveform, continuous visual PLL.
- [x] Demand-driven Metal frames, adaptive native snapshot polling, bounded
      PCM analysis chunks, automatic AC/battery/Low-Power policy, and
      interaction-level preparation suspension.
- [x] Native-only playback; native failure disables the deck with a diagnostic
      while library management remains available.
- [x] Window geometry, table columns, sidebar pane sizes, zoom, and jump mode
      persist across clean closes.
- [x] Read-only `djmdCue` memory/hot cues from the live DB snapshot, with
      PCOB/PCO2 fallback, in a HOT CUE / MEM CUE panel: eight visible slots per
      page, overflow retention, label/loop/color preservation, and clickless
      seeking.

### Player follow-up — cues and additive grid

- [x] **Rekordbox memory/hot-cue import:** read `djmdCue` from the temporary
      read-only DB snapshot, preserve position/type/label/loop/color and every
      hot slot, use PCOB/PCO2 only as fallback, and sync V4 metadata without an
      audio recook. Never write cues or colors back to Rekordbox.
- [x] Exact live-DB hot-slot decoding: preserve Rekordbox's reserved Kind=4
      gap (A/B/C=1/2/3, D–P=5–17), reject unknown values instead of guessing,
      and regression-pin all sixteen identities.
- [x] Live cue precedence at track load: inject the read-only DB rows directly
      into the deck so stale/missing V4 or ANLZ sidecars can never delay or
      override the authoritative hot/memory identities.
- [x] Cue density/selection workshop: keep the waveform clean and place eight
      hot plus eight memory slots in a compact two-page panel.
- [x] Cue waveform identity: fixed-palette, black-outlined A–H hot-cue boxes
      with flush stems and red-only memory-cue arrows appear in both the
      whole-track overview and scrolling waveform/beat grid in Metal; imported
      color metadata remains preserved.
- [x] Gated cue transport: session CUE, hot cues, and memory cues play while
      held and return on release; Space/play while held latches playback until
      Space/play or another cue action.
- [x] One-shot session-CUE placement: a paused manual playhead move arms exactly
      the next CUE press to relocate the ephemeral orange marker without audio;
      following presses return to normal gated preview.
- [x] Metadata-only legacy-grid migration: valid read-only Rekordbox rows win
      exactly; otherwise existing fallback spacing is shifted to beat 1 at
      `0:00` without recooking or rewriting ultra waveform payloads. Migration
      reads DAT/PQTZ only, never EXT/2EX waveform/color sections.
- [x] Canonical V4 persistence and precedence boundary: SoundBox-owned grid,
      every imported memory cue, RGB/24-band analysis, ultra contour, atomic
      user-data updates, and no Rekordbox runtime dependency.
- [x] Export-field bridge for PQTZ, PCOB/PCO2, PWV5, and PWV7. It is pure and
      non-writing; actual ANLZ/USB assembly remains hardware-gated R&D.
- [ ] **SoundBox-owned beat grid (later):** additive grid state beginning at
      `0:00`, with a manual adjustment workflow and explicit precedence over the
      imported Rekordbox grid. Store only in SoundBox state/cache; never modify
      ANLZ files, `master.db`, or audio.
- [ ] Decide migration/export semantics for a custom SoundBox grid before
      building the editor. Initial scope is player display/transport, not
      writing it into Rekordbox or CDJ analysis.

### Phase 3 — iPod validation 🟨 (USB fixture validated; hardware still gated)
Rockbox iPod ≈ a USB stick; getting music written + readable is 80% done. To
validate on-device:
- [ ] Music writes to + reads from the real Rockbox iPod.
- [ ] m3u8 works on-device (drive-root-absolute paths; expect maybe a tweak).
- [ ] `cover.jpg`-per-folder renders (baseline-JPEG already enforced + cache
      migrated — pure on-device check; theme/WPS must support album art).
- [ ] iPod is FAT32 (Rockbox does NOT support HFS+ — restore if needed).
- [ ] Rockbox DB refresh workflow after dumping files.
- [ ] Exact playlist order + folder nesting hold.
- [ ] Scale test (~2900 tracks) — no choke.
- [x] Read-only layout classifier and capability boundary for Rockbox,
      stock-only, dual-boot, and generic USB.
- [x] Stock-iPod compatibility preview that cannot write `iTunesDB`.
- [x] Guarded FAT32 dummy-volume export/verify/eject with pre-existing hidden
      data preserved.

**Manual-sourced facts** (official Rockbox manuals): baseline-JPEG required
(no progressive/multi-scan/RLE); art search order prefers EMBEDDED over
`cover.jpg` (accepted — see decisions); Max Playlist Size default 10k; on-device
perf ritual = Load to RAM + Directory Cache + DB init; Playlist Catalogue dir =
`/Playlists`.

### Phase 4 — Windows port ⬜ (a real port, not a recompile)
- [ ] `pyrekordbox` vs **rekordbox 7** (Sonny runs 6.8.5 on Mac; validate v7 on PC).
- [ ] Windows rekordbox DB path.
- [ ] Replace `diskutil` + `/Volumes/` detection/guards with drive-letter logic.
- [ ] Eject on Windows.
- [ ] Windows PyInstaller `.exe`.

### Phase 5 — Public fork ⬜ (gated on iPod validation)
- [ ] Public build distinct from Sonny's personal build.
- [ ] Do NOT bundle yt-dlp — detect a user-installed one; opt-in, no silent scrape.
- [ ] Embedded-art reader (MP3/AIFF/FLAC/M4A) — 100% clean, ships to everyone.
- [ ] `features` flag + `build_app.sh --public`.
- [ ] Distinguish iPod/MP3 device vs plain USB; block plain USBs.
- [ ] Code-sign + notarize.

### Frontier — CDJ USB export
Reverse-engineer rekordbox's `export.pdb` so SoundBox can write CDJ-readable
USB sticks (Sonny performs on 4× CDJ-3000). Full plan + status + prototype:
**`docs/cdj-usb-export.md`**.

### Someday / v2
- [ ] Native Apple/iTunesDB iPod writer, only after model/hash research,
      fixture round-trips, and real-hardware validation.

## Product decisions (don't re-litigate)

- **Embedded MP3/M4A art MAY win over `cover.jpg`** — accepted by design; don't
  nag. `cover.jpg` is the universal floor.
- **Personal-edit tags are NOT stripped** from search — a coverless own-edit is
  Sonny's visual cue for cleanup.
- **Two-plane deletes:** Delete-from-Collection (global hide) vs
  Remove-from-Playlist (per-pid veto). Both drift devices; rekordbox never changes.
- **Unknowns-suggester rows UNCHECKED by default** (streaming list must never arm
  an accidental accept); merge-dialog rows stay pre-checked.
- **Imported audio referenced in place** — never copied or re-tagged.
- **Device filenames = SoundBox effective titles**, always.
- **Rekordbox analysis is read-only.** Imported grids/waveforms seed V4;
  live-DB cues refresh into V4 until SoundBox claims cue ownership. Rekordbox
  files are never edited in place and SoundBox-owned edits affect only V4.
- **Load starts at the first valid imported beat, else `0:00`.** Double-click
  is the only browser load gesture and loads paused; Space only toggles the
  loaded deck; C is momentary CUE. Session CUE remains ephemeral until a
  separate persistence design is explicitly approved.
