# SoundBox current handoff

Updated: 2026-08-25 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical product evidence stays in the roadmap and
topic documents.

## Repository and runtime

- Branch `main` is clean and published at the wrap-up commit titled
  `Follow moved imports without cache churn`. The change adds persistent
  identity relocation for direct imports, cloud-provider cache identity
  reconciliation, and the provenance-gated legacy SoundBox RGB repair.
- Final bare `./check.sh` passed: native build, pyflakes, mypy, and 851 tests in
  11.517 seconds with four expected skips. `./build_app.sh` then produced the
  installed runtime at `/Applications/Soundbox.app`; strict deep signature
  verification passed and installed/dist executable SHA-256 values match at
  `f8d25feaa3b2e287e654e2722e34fc138d75a8e3d1ab581c7d64c68f0eed86a3`.
- The installed app is running as PID 77373 with the native CoreAudio/Metal
  deck active. It instant-painted and freshly loaded 3,401 tracks / 75
  playlists. Its steady-state preparation pass reports zero analysis, grid,
  or cue tasks.
- One proportional install rollback remains at
  `/private/tmp/soundbox-install-rollback.Czp1P7/`: the untouched 235 MiB
  pre-install `Soundbox.app`, exact 118,716-byte `library-before.json` snapshot
  (SHA-256
  `ffbf13f0c641fd1ab0eed320448380f152904160d8ab6ed3eae463d131d655f7`),
  and exact 282,166-byte post-QA `library-final.json` snapshot (SHA-256
  `6477b25621c2a8d0cac719fc0e4b87759f993b190c158f7cc54890e09663f6c6`).
  Two obsolete candidate bundles were deleted after final verification,
  reclaiming about 470 MiB.

## Installed behavior and real-state proof

- Every direct import records an opaque Core Foundation bookmark. Startup does
  one healthy saved-path check, resolves only missing paths/bookmark-less legacy
  records in a background worker, and never walks a directory tree. Bookmarks
  follow file/parent-folder moves; the legacy/cross-volume fallback is one
  batched exact filename+recorded-size Spotlight lookup whose ambiguity fails
  closed. Manual repair scans only the folder the user explicitly chooses.
- The live move of the SoundBox-only `Sets` folder repaired all 83 missing
  records automatically in 0.507 seconds and backfilled all 84 direct-import
  bookmarks. All 84 sources exist at their recorded sizes, the stable `sb-`
  IDs remain attached. Every pre-install curated field remains canonically
  identical; live QA additionally added the preserved title override `Pop Off`
  for track `101429231`, bringing the title-override count to 1,076. Current
  `library.json` SHA-256 is
  `6477b25621c2a8d0cac719fc0e4b87759f993b190c158f7cc54890e09663f6c6`;
  the other changes are the intended import paths/bookmarks and Qt's saved
  column geometry.
- SeaDrive normalized nanosecond mtimes without changing the audio. The final
  build content-verified and adopted 1,777 existing V5 records from their
  first/last-128-KiB fingerprints with zero audio recooks, then recorded tiny
  cache-bound xattr proofs. Matching-size `SF_DATALESS` placeholders retain
  their existing cache without startup hydration; deliberate access enables a
  later content proof. The first rejected candidate was stopped after it had
  unnecessarily replaced 28 disposable non-Set records; no Set cache or source
  was written, and every corrected/follow-up preparation pass was zero-task.
- All 83 existing Set records pass V5 readiness and complete overview/detail
  loading. Their old green-only close RGB was a compact-code packing bug, not a
  missing cache. New cooks pack PWV5 correctly; old `soundbox-analysis` streams
  reconstruct only display color bits from their same-clock cached overview at
  native upload. This measured 1.4–2.3 ms for a 538,655-row hour-long Set,
  preserved every cached height/control bit, activated all RGB channels on all
  83 records, and changed neither source nor canonical cache bytes. A real AHEE
  Set committed its full overview/detail to the installed RGB native deck.
- Cache inventories remain 3,297 curated-art, 7,609 stock-artwork, one stock
  shadow, one Rockbox-media, 20,127 Rockbox-artwork, and 2,921 track-analysis
  files. The final installed launch wrote zero track-analysis files.

## Next action

- No implementation, validation, packaging, installation, runtime check,
  documentation update, commit, or push is pending. At resume, verify
  `git status -sb` is clean and `main` still matches `origin/main`; there is no
  deferred recook or visual acceptance requirement.
