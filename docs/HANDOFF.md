# SoundBox current handoff

Updated October 26, 2026, America/Chicago.

## Published source and installed runtime

Sonny explicitly requested the full wrap-up. Runtime release commit:
**81c0d6c4bada0a48712dddcfe948384aa62a1eb1**, on `main`. The later repository HEAD is a
documentation-only installation/cleanup record; it does not change the installed
runtime revision or require restamping/rebuilding the unchanged binary.

Installed `/Applications/Soundbox.app`: personal **build 129**, built from
that clean source commit. Signed runtime fingerprint:
**610e7d2891b94dc028d2a504138446a896e132ba5e3f0c5d87280e2e46fffb83**.
Installed **2026-10-26T01:56:28.955915-05:00**, launched
**2026-10-26T01:56:29.092884-05:00**, verified
**2026-10-26T01:57:44.599174-05:00**, final post-cleanup state verified
**2026-10-26T02:00:48.270276-05:00**. PID **57517** at verification.
Evidence: `~/Library/Logs/Soundbox/wrap-2026-10-26/` (installed-build.json,
Info.plist, build manifest, post-quit/postlaunch snapshots, startup log,
isolated smoke, exact artwork SHA inventory, queue snapshot and cleanup record).
Signature and executable/plist/native hashes match the tested package. Packaged
PlayerBar contains the live-cue refresh method; isolated smoke has native active,
zero devices, no real library or artwork worker.

## Completed behavior

- Settings uses one centered header: General, Keyboard, Graveyard, Connections;
  Back at left, no Settings label. General balances its columns. Connections has
  read-only rekordbox source/status/location/selection at left, optional personal
  SoundCloud public likes at right. Failed DB reads/settings saves retain the
  previous source. Source snapshots and ANLZ lookup use the selected DB root.
- Keyboard uses active-page cue slots 1–8, pages 9/0 and zoom =/-. Old direct
  Hot/Memory binding IDs remain preserved but inactive.
- Public likes use app credentials plus public profile, with no browser sign-in
  or website callback changes. Credentials/tokens stay in Keychain under stable
  bundle/signing identity. A 6,057-track scan succeeded October 25. Always Allow
  was chosen by Sonny; no Keychain ACL workaround or credential extraction.
  Valid application tokens without refresh tokens now reuse until expiry.
- Artwork review checks every active cover, including manual/legacy art, and
  restores exact saved contexts before online work. Suggested contains ready
  different comparisons; All supports individual browsing. Loading/progress,
  resumable metadata and bounded previews avoid repeating completed requests.
  Keep/Replace persist exact cover SHA/entry plus artist/title; Next is session
  only. Actions advance down from the selection. Searches never change images.
  More has artist-profile preview/approval, manual choices, one persistent
  Exclude Uploader manager, refresh controls and no Undo item. Exact original
  recovery remains in art/review; seller-policy originals in art/source-recovery.
- Approved artwork stays protected from background races. The explicitly
  authorized excluded-source policy hides seller sources and uses only strictly
  matched artist-profile art. An unavailable profile stays hidden; unrelated
  manual/unrecorded art remains protected. Version creators, Unicode identities,
  alternate-account possibilities, source/pixel deduplication and seller rules
  are shared rather than per-song exceptions. Cross-track learning is not shipped;
  decision findings and intentional cross-song artwork reuse are in IDEAS.
- Review audition shares the native deck, with pause/scrub/time and no playlist
  autoplay. Actual playback keeps Pause working after transient state reports;
  quick resume cancels a pause fade. Selection/close cancels pending playback.
- BLUE/RGB overview fills quantized-zero compact passages using the resident
  precise audio envelope and authored hue. Real silence stays background; PWV7
  remains independent. Cash Out, Depth, Machaki Remix and Voartyx Flip before/after
  cache renders verify the reported cases; main-lane interior pixels and all four
  canonical cache hashes stayed identical. Evidence: build/overview-evidence-oct26.
- Successful library refresh now publishes fresh authoritative cues immediately
  to an already-loaded matching track/source, including deletions, without audio
  reload/seek. Queued old manifests cannot restore stale cues. Beatgrid changes
  keep the fingerprinted metadata-only background refresh with waveform payload
  bytes preserved. This closes the loaded-track caveat reported before wrap-up.

## Validation and final curated state

October 26 final source gate: **./check.sh --render**, all **1,221 tests** passed
(1,168 offscreen + 53 Cocoa), zero skips/expected failures, reconciled inventories,
lint/types/native build. Final Settings, review/menu/profile/block-manager renders
were inspected: /tmp/soundbox-wrap-*.png. Harnesses suppress real source/device/
Keychain discovery and use isolated AppData; the separately invoked real-cache
renderer reads only the four explicit canonical records, never source audio.
Instruction parity, current relative Markdown links and diff whitespace passed.

Final startup: native deck 01:56:31; **3,740 tracks / 75 playlists** loaded at
01:56:35; **3,237 unchanged analysis receipts**, zero preparation tasks. Both
stock-art passes reused **3,106** images, zero preparation/failures. Art fill
fetched nothing; one unavailable excluded-source profile stayed hidden, zero
policy replacements. Nonsecret connection status retains **6,055 public likes**,
last successful scan 01:26:41 on October 26; this is separate from the final
startup/isolated smoke proof and does not certify a new live authorization.

Post-quit, postlaunch and post-cleanup state match exactly: all **413 saved
review decisions**, **3,092 saved checks** and **4,040 art files** preserved, with
**3,277 analysis / 8,957 stock-art files**. All artwork bytes/metadata and prior
queue results were checked. Library SHA:
**8ade9a52673dac7529a42b06d0ceb9755fa09165e96a9bb404b3544ef7bb3ba7**.
Counts: **488 artist overrides / 1,094 titles**, 10 custom playlists, 84 imports,
517 hidden tracks, 24 aliases. Compared with the preceding installation, only
`settings.waveform_zoom` changed (864 → 256) before quitting; curation fields
match. Exact delta: preceding-library-delta.json in the evidence root.
Do not restore an older baseline to discard later user choices.

## Retention, backup and remaining acceptance

Cleanup verified the exact paths, fingerprints and signatures before retiring
the two prior temporary app rollbacks, duplicate dist app/collection and obsolete
build/personal packaging intermediates. Only the current installed app and active
source checkout remain; build/native, QA evidence and small state/provenance
records remain available. The temporary rollback directory is gone. No AppData
or protected fixture/recovery material was part of cleanup. No active app rollback
remains; prior release code is in Git and can be rebuilt. cleanup.json records
all retired paths. Main is published with a clean worktree after the final
documentation-only follow-up; HTTPS uses the existing gh credential helper since
the SSH agent has no loaded identity. Credentials never enter Git or output.

The sole full ZIP remains the deliberately created October 25 backup:
`/Users/sonnypiccolo/Desktop/{SUBYATA.music}/Tools/SoundBox Backup/soundbox-backup-2026-10-25.zip`,
283,779,506 bytes, SHA
`462132f816d40301bcf932e6247b593d352b0748531c0f2cdbecbbde9ea9f3c1`.
It predates later review choices; fresh small release snapshots hold latest
metadata, and exact original recovery remains under art/review and
art/source-recovery. No duplicate full artwork cache or replacement ZIP was
created merely to install the app.

No implementation/release work from this session remains. The artwork audit is
still a human process: unknown alternate accounts are possibilities, not certified
official accounts; intentional cross-song artwork reuse is not song identity.
The unavailable artist profile may need manual correction. Selecting a different
real rekordbox DB and manually editing/saving grids/cues remain physical acceptance
checks; fixtures verify their source/cache/player behavior without writing rekordbox.

Preserve generation-58 recovery **20260926-152340-47477cfa4a49**, offline capture
soundbox-ipod-ref/offline-lifecycle/20260926-163945, frozen waveform/CDJ fixtures
and curated backups. No source audio, rekordbox/ANLZ, device payload or /PIONEER
writes were part of this release. Wider device/hardware acceptance, cloud-source
availability and public service/distribution clearance remain separate. Two older
native timing misses remain under build/review-controls-validation; later full
gates pass without weaker thresholds, but that earlier flakiness is not certified
fixed. Existing device recovery/acceptance work stays on ROADMAP.
