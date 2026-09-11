# SoundBox current handoff

Updated: 2026-09-10 (America/Chicago)

## Repository and runtime

- `main` / published GitHub main remain at
  `578982e7deab5d44f09e3f8a54768fb3a7af8857`. This session's maintenance
  changes are being released with Sonny's explicit commit/push/install approval.
- Installed `/Applications/Soundbox.app` remains the August release, executable
  SHA-256 `f8d25feaa3b2e287e654e2722e34fc138d75a8e3d1ab581c7d64c68f0eed86a3`.
  Signature passed. Independent extraction/comparison of 45 packaged project
  Python modules matches code compiled from HEAD after normalizing filenames.
  This does not independently prove native/dependency source provenance.
- `dist/Soundbox.app` is now the September maintenance candidate, successfully
  built and signed. Its plist records version 1.0.0, build 101, HEAD above,
  and dirty=true. Its 45 packaged project Python modules match the working
  sources. It is not installed and must not be represented as published Git.
- No source audio, curated state, or orphan cache cleanup was performed.
  The old documented rollback at `/private/tmp/soundbox-install-rollback.Czp1P7/`
  is no longer present. No new installation rollback was created this session;
  the currently installed August app remains available for the next rollback.

## Candidate changes and validation

- Background analysis defers dataless cache misses and late-waveform recooks,
  rechecks queued sources after waits, and defers stat errors. Identity
  adoption independently rejects dataless sources. Metadata-only cue/grid
  refresh remains available; explicit forced recooks may hydrate audio.
- Audit excludes hidden tracks, separates invalid analysis from playable V4,
  reports deferred sources, and permits V4 retirement only with full current
  coverage of all active tracks and no orphan V4. No deletion is performed.
- Empty-device polling backs off to 30 seconds; active mounts retain 3-second
  polling and the mount watcher remains immediate. This is not yet measured
  in the installed runtime.
- Expanded Python lint, four updated dependency pins, and signed build metadata
  are included. Provenance verification checks signature/clean checkout and
  matching declared metadata; it explicitly does not prove packaged source.
  Ad-hoc fallback now re-signs after plist edits too.
- Bare `./check.sh` passed: native build, pyflakes, mypy, 863 tests with four
  expected skips. Shellcheck, pip check, and diff whitespace checks passed.
- Candidate native dylib passed a separate real Cocoa load/play/seek/gain-ramp/
  teardown test with silent output and an unchanged synthetic WAV. This is a
  native component smoke test, not installed-app curated-state acceptance.
- Read-only full real-state audit: 2,893 active tracks, 504 hidden excluded;
  2,892 current verified caches, one deferred dataless track (`120484616`),
  zero missing sources/analysis or invalid records, zero V4, and 31 orphan
  V5 records (previously measured 59.2 MiB). Retirement correctly BLOCKED.
  Counts reflect the snapshot at audit time; the live app may continue updating.

## Release work remaining

- Commit intended changes and rebuild from the clean revision under Sonny's
  explicit release approval. Keep the installed version aligned with Git.
- Follow the project install contract: back up/hash `library.json`, gracefully
  quit, keep one proportional rollback, install with ditto, verify signature,
  relaunch, inspect native startup/preparation, and compare curated state.
- Publish the final passing revision and verify clean/published status. Update
  this handoff with actual installed revision, state checks, and rollback facts.
