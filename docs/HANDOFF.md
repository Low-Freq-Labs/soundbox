# SoundBox current handoff

Updated: 2026-09-10 (America/Chicago)

## Release and runtime

- Branch `main`: main-waveform visual refinement after maintenance revision
  `712e435`. Sonny explicitly authorized commit, push, and installation.
  The release app must be built from the clean commit containing this handoff;
  its signed `SoundboxSourceRevision` must match HEAD. Verify with
  `./.venv/bin/python scripts/app_provenance.py /Applications/Soundbox.app`.
- Version 1.0.0, `/Applications/Soundbox.app`. The installed candidate from this
  source activated its packaged CoreAudio/Metal deck and loaded 3,397 tracks /
  75 playlists. Preparation found zero analysis/grid/cue tasks; art fetched zero
  images. The final clean-revision rebuild uses the same tested app code.
- Git SSH authentication is unavailable here. Publish over authenticated HTTPS
  using the session-only `gh auth git-credential` helper; do not change the
  persistent origin or credentials merely to work around it.

## Waveform change and validation

- Only the large waveform's rendering changed. The thirteen zoom stops,
  overview, cues, transport/audio clocks, and accepted close signed-carrier
  reconstruction remain unchanged.
- Wide BLUE/RGB stops 24/32/48 select finer existing V5 extrema and integrate
  the exact fixed-lattice profile. No recook, cache-format change, source write,
  or persistent waveform-storage increase. The cache still contains 2,924 V5s.
- Grid timing is unchanged. Small paired edge ticks and red downbeat triangles
  replace the opaque fence; thin interior guides appear only as local beat
  spacing grows from 32 to 80 points. Both native analysis-upload paths use
  one helper; overview/cue vertices bypass the grid style.
- Bare `./check.sh`: native build, lint, mypy, 865 tests passed (four skips).
  Separate real-Cocoa native-deck suite: 34 tests passed.
- “Turn Up (Final v2)”, track `84590640`, bar 18.1 / 28,137 ms: all thirteen
  RGB stops rendered, plus BLUE/3BAND checks. Supplied Rekordbox comparison at
  fixed registration improved outline correlation 0.613 → 0.713. The small
  overview/ruler capture was pixel-identical. The old six-image private corpus
  directory is absent, so it was not rerun; the newly supplied references and
  current real track were used instead.
- Baseline/candidate each captured 540 silent native playback drawables across
  six zooms: zero audio underruns or GPU deadline misses, respectively two/one
  dropped presentations, maximum actual gap 16.67 ms. A short phase sweep
  retained near-rigid integer-pixel motion, but its fractional-image-resampling
  residual increased with the finer contours. Do not claim these measurements
  guarantee perceptual smoothness; Sonny's live visual acceptance remains the
  next check. Exact evidence and PNGs: `/private/tmp/soundbox-waveform.iNVKNn/`
  (about 30 MiB). `docs/PLAYER.md` records the tradeoff and reference dimensions.

## Curated-state protection and rollback

- Recovery remains in the single directory
  `/private/tmp/soundbox-install-rollback.GAq8sU/`. Preserve it until Sonny is
  comfortable with this visual change. `maintenance-712e435.app` is the
  immediately preceding clean release; `Soundbox.app` is the older August
  recovery app. Earlier maintenance/state recovery material is also retained.
- The complete `art/` backup contains 3,298 files, including indexes/default
  photos. Every file was SHA-256 compared with live AppData before and after
  the candidate install; all match. No cover art was removed or reassigned.
- `library-waveform-before.json` snapshots the latest live state before this
  installation; `library-waveform-closed.json` captures the graceful final save.
  Candidate relaunch preserved that closed state exactly. All 513 Graveyard
  entries/dates, one playlist-removal group, 10 custom playlists, 84 imports,
  466 artist overrides, 1,077 title overrides, and 24 aliases remain intact.
  Do not restore an earlier snapshot over subsequent intentional user edits.
- Never wipe AppData or regenerate art to troubleshoot a visual issue. No
  source audio, Rekordbox analysis, library data, or cache record was deleted.

## Next checks

- Sonny should review “Turn Up” at wide and close zoom during playback. If
  the finer wide contour appears shimmery on the physical panel, revisit only
  that reconstruction; do not change the accepted audio clock or close family.
- No implementation work or cache cleanup is queued for this release. The
  broader roadmap's MainWindow ownership decomposition and grid/cue editing
  remain separate future work.
