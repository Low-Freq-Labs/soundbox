# SoundBox current handoff

Updated: 2026-09-23 (America/Chicago)

## Release and runtime

- Branch `main`; release revision is the commit containing this handoff
  (autoplay fix after `b13384d`). Sonny confirmed the installed fix is working
  and explicitly authorized wrap-up, commit, and push.
- The clean release must be built from that commit, installed at
  `/Applications/Soundbox.app`, and pass
  `./.venv/bin/python scripts/app_provenance.py /Applications/Soundbox.app`.
  This is the final release gate; the initially tested app had dirty-source
  provenance. The clean rebuild contains the same accepted player code.
- Runtime verified: packaged CoreAudio/Metal deck active, 3,397 tracks /
  75 playlists, zero analysis/grid/cue preparation tasks, zero art fetched.
- Git SSH authentication was unavailable in the preceding release session;
  authenticated HTTPS with the session-only `gh auth git-credential` helper
  was used then. Do not change persistent origin/credentials for that workaround.

## Autoplay fix and validation

- `PlayerBar` now retains ordinary-playback eligibility separately from the
  polled PLAY button state. Temporary native PAUSED reports during seek/refill
  or activation, including a PAUSED report preceding the terminal flag, cannot
  silently disable up/random/down advancement.
- Explicit pause/stop, source replacement, and errors cancel eligibility;
  ordinary play/resume arms it again. Cue preview remains excluded. Terminal
  consumption is one-shot and deferred advancement checks transport epoch,
  source generation, and track identity.
- Bare `./check.sh` passed: 868 tests, four skips, native build, pyflakes, mypy.
  The new all-mode regression fails all six mode/resume combinations with the
  previous handler and passes with the fix.
- A separate real-Cocoa/CoreAudio test passed: silent playback, clean
  pause/resume, injected transient PAUSED report, delayed terminal polling,
  activation resynchronization, and exactly one end notification. Physical
  laptop sleep/wake was not performed by the agent. Sonny subsequently
  confirmed the installed fix has been working reliably in normal use.
- Initial build log: `/tmp/soundbox-autoplay-build.log`; clean release build
  log: `/tmp/soundbox-autoplay-release-build.log`. Signature verified after build
  and installation. No native rendering, audio-clock, cache-format, or playlist
  ordering changes. The accepted waveform framing remains from `b13384d`.

## Curated state and rollback

- Reused the single rollback directory
  `/private/tmp/soundbox-install-rollback.GAq8sU/`.
  `autoplay-tested-dirty.app` retains the user-accepted pre-release build;
  `pre-autoplay-b13384d.app` retains the app before the fix.
  Preserve the earlier waveform/maintenance recovery bundles and art backup
  already there until Sonny authorizes their removal.
- Clean release protection uses `library-autoplay-release-before.json`,
  `library-autoplay-release-closed.json`, and
  `autoplay-release-cache-baseline.json` in that directory. Final release
  verification is recorded in `autoplay-release-verification.txt`.
- Initial fix installation: `library-autoplay-before.json` is the running-app snapshot;
  `library-autoplay-closed.json` captures its graceful final save. Only settings
  differed at shutdown; all curated fields matched. The installed app's
  library remained byte-identical to the closed snapshot after startup:
  SHA-256 `30bf85049ebd9473db865bfa7b2a511e8cc4732a43617ce3ccb1cc14d0c6f13f`.
- All 3,298 art files retained identical SHA-256 hashes; all 2,924 analysis
  records remained in place. Baseline inventory/hashes are in
  `autoplay-cache-baseline.json` in the rollback directory.
- Curated counts: 513 hidden tracks, one playlist-removal group, 10 custom
  playlists, 84 imports, 467 artist overrides, 1,077 title overrides, 24 aliases.
  Never restore these snapshots over subsequent intentional user edits.

## Next checks

- No implementation work remains; Sonny accepted the fix. Final wrap-up
  gates are clean-build provenance, startup/curated-state verification,
  a published `main`, and a clean worktree.
- If another stall occurs, capture the loaded track, active mode, and whether
  PLAY still shows active before changing the playhead. Directional playlist
  boundaries and unavailable-source handling retain their existing semantics.
- MainWindow decomposition and grid/cue editing remain separate future work.
