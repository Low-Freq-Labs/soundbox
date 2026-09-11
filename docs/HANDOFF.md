# SoundBox current handoff

Updated: 2026-09-10 (America/Chicago)

## Release and runtime

- Branch `main` contains the maintenance release, followed by the documentation
  completion commit `Record verified maintenance release and curated state`.
  The final app is rebuilt from that clean completion revision so its signed
  `SoundboxSourceRevision` matches published HEAD. Check with
  `./.venv/bin/python scripts/app_provenance.py /Applications/Soundbox.app`.
- Version 1.0.0. Installed path: `/Applications/Soundbox.app`. Native
  CoreAudio/Metal startup and live load of 3,397 tracks / 75 playlists were
  verified. Preparation reports zero analysis/grid/cue tasks; cover-art startup
  fetched zero images. Empty-device poll logs no longer repeat every 3 seconds.
- The first installed maintenance revision was `2a1a7a2`; the completion commit
  changes documentation only. Runtime validation must also be repeated after
  its rebuild/install before declaring the session complete.
- Git SSH authentication failed on this machine. Authenticated HTTPS through
  `gh auth git-credential` successfully published the release without changing
  persistent Git credentials or the configured origin URL.

## Curated state and rollback

- Sonny specifically required preserving cover art and Graveyard work. One
  recovery directory is retained at `/private/tmp/soundbox-install-rollback.GAq8sU/`.
  It contains the original August `Soundbox.app`, `library-before.json`, the
  exact post-quit `library-closed.json`, and the complete `art/` backup including
  indexes/default-photo pool. `art-index-before.json` preserves the earlier
  index before Sonny's final manual confirmation.
- Every one of 3,298 backed-up artwork files was SHA-256 compared with live
  AppData after installation. All match. All 513 global Graveyard entries and
  dates, one playlist-removal group, 10 custom playlists, 84 imports, 466 artist
  overrides, 1,077 title overrides, and 24 aliases remain intact.
- The old app saved one final user title override and promoted the same track's
  existing image from automatic to manually confirmed before it exited. No
  title override or artwork assignment was removed. The backup includes those
  latest edits; do not restore the earlier snapshot over them.
- Post-quit and post-install `library.json` were byte-identical at SHA-256
  `4b7d697e383e889d2a657a2037c38adce7be473b5de9794bee9d69bd6ac0cc98`.
  Subsequent intentional user edits can legitimately change this value.
- No source audio or curated data was deleted. No orphan cleanup was performed.
  The previous August rollback directory no longer existed at session start.

## Shipped corrections and verification

- Background cloud guards cover cache misses, late waveform promotion, stat
  failures, and eviction during playback/storage waits. Identity adoption
  separately rejects dataless sources. Metadata-only cue/grid work remains
  available; explicit forced recooks may hydrate audio.
- The audit distinguishes invalid analysis, deferred sources, outdated V5, and
  verified playable V4. Global V4 retirement requires verified current coverage
  of all active tracks and no orphan V4. A full read-only audit found 2,892
  verified current records out of 2,893 active tracks, one deferred placeholder
  (`120484616`), no missing/invalid records, no V4, and 31 orphan V5 records
  (59.2 MiB). Retirement correctly remains blocked by the deferred source.
- Idle no-device polling is 30 seconds; discovered mounts retain 3-second
  checks and `/Volumes` event discovery. Expanded lint and four security-fix
  dependency pins are included. Build metadata checks validate the signature,
  clean checkout, and declared revision/version/build; they do not claim
  independent source-to-binary proof. Both signing paths run after plist edits.
- Bare `./check.sh` passed with native build, pyflakes, mypy, and 863 tests
  (four expected skips). Shellcheck, pip check, and whitespace checks passed.
  The packaged native dylib also passed a separate real Cocoa silent-output
  load/play/seek/gain-ramp/teardown test with unchanged synthetic audio.
- Independent extraction/comparison of all 45 packaged project Python modules
  matched the relevant Git/working sources after filename normalization. This
  excludes native/dependency provenance; signed metadata is not a substitute.

## Future work

- No cache deletion or forced hydration is required. Retain the art/state/app
  rollback until Sonny is comfortable with the release.
- Architecture follow-up remains the roadmap's decomposition of MainWindow by
  real ownership. It was not attempted as part of this bounded hardening pass.
