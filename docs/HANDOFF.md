# SoundBox current handoff

Updated: 2026-08-05 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical product evidence stays in the roadmap and
topic documents.

## Repository and runtime

- Branch `main` is clean and published to `origin/main` at the tip titled
  `Harden rekordbox reads and device transaction lifecycle`. That commit
  contains the rekordbox-read completeness work plus the device connection/
  transaction-QoL work in the engine, desktop, tests, and docs.
- The completed build is installed at `/Applications/Soundbox.app` and running
  as PID 82609. The installed and `dist` executable SHA-256 values match at
  `f1f42576ea564046039f4f9b6f0f4afb8d53826349640c641b4fcb86f85717c4`;
  strict deep signature verification passed. The packaged native CoreAudio/
  Metal deck activated.
- Bare `./check.sh` passed after the behavior changes: native build, pyflakes,
  mypy, and 809 tests with four expected skips. `./build_app.sh` passed and
  produced the installed signed bundle.
- Startup instant-painted 3,414 tracks, then the fresh read loaded 3,414 tracks
  and 81 playlists. The curated `library.json` SHA-256 after clean quit,
  installation, and relaunch is
  `7c1f1258c23cc6efd9c6403da8e4b01f37933bef711832169a043ffe1c8c85f7`.
  State remains intact: 84 imports, 24 aliases, 463 artist overrides, 1,049
  title overrides, 10 custom playlists with 688 references, 513 hidden tracks,
  one removal layer with two references, and no addition layer. Startup
  prepared one missing cue-metadata record and verified all 2,786 curated stock
  artwork inputs with zero failures.
- The single rollback area remains
  `/private/tmp/soundbox-mytag-fix-OQnpqA/`. The immediately previous app is
  `Soundbox.pre-progress-segment-correction.app`; exact running and clean-quit
  state copies are `library.pre-progress-segment-correction.json` and
  `library.pre-progress-segment-correction.clean-quit.json`. The clean-quit
  copy matches the current authoritative state hash. AppData was not removed
  or rebuilt.

## Rekordbox read contract

- `Jump Bros!` (ContentID `13778874`) has one active Trap My Tag association
  and one soft-deleted historical DnB association. SoundBox now requires the
  My Tag definition, association, and content row all to be active, so the
  installed live library places it in `GENERES / TRAP` and not `GENERES / DnB`.
- Signed smart-list tag IDs, `contains` / `not contains`, and compound ALL/ANY
  criteria are preserved. Active tracks, playlist nodes, ordinary membership,
  metadata relationships, and smart endpoints are filtered and validated;
  evaluator or relationship ambiguity fails refresh visibly.
- The read-only snapshot accepts `master.db` plus WAL only when device, inode,
  size, mtime, and ctime identities remain unchanged across the complete copy.
  A checkpoint/update retries the pair; persistent churn fails visibly. Refresh
  and instant-paint cache ownership are serialized so an older result cannot
  overwrite a newer library.
- Independent raw-table and disposable-mutated-schema audits matched all 81
  visible playlists, all 71 ordinary playlist orders, all 10 active smart
  playlists, hierarchy/order, and metadata relationships. The live database
  was never opened for write. Nineteen playlist-referenced source paths remain
  pre-existing missing files; this work did not modify source audio.

## Device terminal contract

- `/Volumes` events start discovery immediately. No new generic or iPod row is
  clickable until the discovery QThread has actually returned and released its
  owning attribute. A stock/dual iPod additionally remains hidden while the
  lower-left status owns `Loading iPod...`, preloads the complete immutable
  Device/Songs/Playlists view, and completes the follow-up drift scan. Clicking
  the published row is presentation-only.
- Device workers store their result but UI handling occurs only from built-in
  `QThread.finished`, after ownership is cleared. This rule now covers export,
  generic/stock/composite Sync, stock/composite/file Delete, dashboard reads,
  verify, import, erase/eject verification, and connection scans.
- Eject and Sync are visibly disabled while a real transaction owns the
  device. A recurring read-only poll is the sole exception: Eject queues behind
  its `finished` boundary instead of showing a busy modal. Starting another
  mutation while discovery is active likewise queues behind that reader.
- Progress is one monotonic transaction meter: planning is 0–15%, guarded
  device work is 15–80%, and required dashboard/sidebar/drift refresh plus
  ownership release is 80–100%. `100%`, success text/modal, and enabled Eject
  occur only after the final readers and worker attributes are gone. Multi-
  target exports finalize every touched volume before one completion. A failed
  required final refresh reports `Final refresh failed`, keeps Eject available,
  and cannot invoke the success callback.
- Post-write snapshot and sidebar reads remain serialized on classic USB. The
  terminal eject-ready latch is installed before operation ownership is
  released and before a modal opens, so recurring polling stays suppressed
  without falsely keeping Eject disabled.

## Exact next check and boundaries

- Source, packaging, install, relaunch, signature, logs, and curated state are
  complete. Physical acceptance still requires the next real iPod connection:
  expect one uninterrupted lower-left `Loading iPod...`, no sidebar row during
  preload/drift, then one ready row that opens instantly. During a real Sync or
  Delete, expect 15% planning, up to 80% device work, 80–100% finalization, and
  immediately usable Eject at completion. The installed runtime currently
  reports zero mounted devices; no removable volume was inspected or mutated by
  validation or installation.
- No in-scope source, packaging, install, documentation, commit, or push work
  remains. At resume, verify `git status -sb` is clean and `main` still matches
  `origin/main`; the physical connection/operation timing check above is the
  only outstanding acceptance evidence.
