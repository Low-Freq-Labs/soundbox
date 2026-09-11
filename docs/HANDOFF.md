# SoundBox current handoff

Updated: 2026-09-10 (America/Chicago)

## Release and runtime

- Branch `main`: grid/framing follow-up to waveform revision `d3be5e7`.
  Sonny authorized commit, push, and installation for this waveform work.
  The release app must be built from the clean commit containing this handoff;
  its signed `SoundboxSourceRevision` must match HEAD. Verify with
  `./.venv/bin/python scripts/app_provenance.py /Applications/Soundbox.app`.
- Version 1.0.0, `/Applications/Soundbox.app`. The preceding installed waveform
  release activated its packaged CoreAudio/Metal deck and loaded 3,397 tracks /
  75 playlists. Preparation found zero analysis/grid/cue tasks; art fetched zero
  images. Repeat these checks on the clean follow-up build during installation.
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
- Grid timing is unchanged. Following Sonny's review, full-height guides now
  stay visible at every zoom. Fixed ordinary/downbeat widths are 0.9/1.1 points;
  interior alpha ranges 0.28/0.58 to 0.45/0.75 with local beat spacing. The
  paired edge markers remain. Both upload paths use one helper.
- Large-wave padding is two points through 64 points/s, increasing smoothly to
  four points at 184 and above (previously seven everywhere). No amplitude-curve, horizontal
  reconstruction, cue, overview, or clock change.
- Follow-up bare `./check.sh`: native build, lint, mypy, 866 tests passed (four
  skips). Follow-up real-Cocoa suite passed 34 tests on rerun; two earlier runs
  missed the resize test's 120 ms display-callback assertion. That test passed
  in isolation on both installed baseline and candidate; root cause of the
  intermittent suite timing failure is not established. Follow-up visual
  captures cover 24/64/256 at the same Turn Up section under `full-grid-*` in
  the evidence directory below; final close framing is under `full-lane-*`.
  A final 180-drawable trace across 24/256/1536 had zero audio underruns/GPU
  deadline misses, six dropped presentations, and a maximum actual gap of
  16.67 ms. Do not describe it as a zero-drop run.
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
  comfortable with this visual change. The follow-up installer retains
  `waveform-d3be5e7.app` as the preceding waveform release;
  `maintenance-712e435.app` is the pre-waveform maintenance release.
  `Soundbox.app` is the older August
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

- Sonny reports the detail is getting much better; the follow-up restores the
  through-wave grid he wanted and tightens wide framing. Review wide/close
  views in the installed follow-up. If
  the finer wide contour appears shimmery on the physical panel, revisit only
  that reconstruction; do not change the accepted audio clock or close family.
- No implementation work or cache cleanup is queued for this release. The
  broader roadmap's MainWindow ownership decomposition and grid/cue editing
  remain separate future work.
