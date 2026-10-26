# SoundBox current handoff

Updated October 26, 2026, America/Chicago.

## Release in progress

Sonny explicitly requested a **full wrap-up**, including final validation,
commit/push, safe installation, runtime/state verification and scoped cleanup.
`main` starts from `17da99e5aecd74fac7a264ff3243be412ee79c68`.
The complete Settings/connections/artwork/player follow-up is ready for its
release commit. Final installation evidence will be a documentation-only follow-up;
never relabel an older binary as that follow-up revision.

Current installed app is still personal build **128**, dirty source fingerprint
`1d2e9bf257181c970decb97637446a49802ef3c3b0e8a43f69aa1088e87cb94d`,
based on the old HEAD above. Installed 01:25:06, launched 01:25:07 and verified
01:25:51 on October 26, PID 55000 then. Evidence:
`~/Library/Logs/Soundbox/artwork-review-2026-10-25/review-overview-fix/`.
Do not confuse this preceding installed app with the upcoming release commit.

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

## Validation and preceding state baseline

October 26 final source gate: **./check.sh --render**, all **1,221 tests** passed
(1,168 offscreen + 53 Cocoa), zero skips/expected failures, reconciled inventories,
lint/types/native build. Final Settings, review/menu/profile/block-manager renders
were inspected: /tmp/soundbox-wrap-*.png. Harnesses suppress real source/device/
Keychain discovery and use isolated AppData; the separately invoked real-cache
renderer reads only the four explicit canonical records, never source audio.

The preceding install preserved all **413 decisions**, **3,092 checks**, and
**4,040 art files**, with **3,277 analysis / 8,957 stock-art files**. Library SHA:
`43c866f73f97fe25afec5dde23cd1616c646acbf0313b985db313b54e27f5f3e`.
Counts: 488 artist overrides, 1,094 titles, 10 custom playlists, 84 imports,
517 hidden tracks, 24 aliases. Two user-added title overrides explain its change
from the earlier install baseline. Capture fresh post-quit/postlaunch state for
the release; never restore old state to erase subsequent user choices.

## Remaining release steps and protected material

Commit the reviewed source/docs, build that committed revision, safely install
with ditto after graceful quit and a fresh library/hash/queue baseline, verify
signature/native startup/zero unnecessary preparation and curation, then update
this handoff with actual installed provenance. Remove scoped prior app/dist/
packaging duplicates after verification. Commit installation evidence, push main,
and verify remote HEAD and a clean worktree. HTTPS via the existing gh credential
helper works; SSH has no loaded agent identity. No credentials belong in output.

The sole full ZIP remains the deliberately created October 25 backup:
`/Users/sonnypiccolo/Desktop/{SUBYATA.music}/Tools/SoundBox Backup/soundbox-backup-2026-10-25.zip`,
283,779,506 bytes, SHA
`462132f816d40301bcf932e6247b593d352b0748531c0f2cdbecbbde9ea9f3c1`.
It predates later review choices; fresh small install snapshots and exact original
recovery are separate. Do not create duplicate full artwork backups casually.
Current temporary app rollback: the shared temporary rollback directory's
Soundbox-before-overview-fix.app (fingerprint d5371173…); retire only after the
release verifies. Keep all state snapshots and provenance records.

Preserve generation-58 recovery **20260926-152340-47477cfa4a49**, offline capture
soundbox-ipod-ref/offline-lifecycle/20260926-163945, frozen waveform/CDJ fixtures
and curated backups. No source audio, rekordbox/ANLZ, device payload or /PIONEER
writes are part of this release. Wider device/hardware acceptance, cloud-source
availability, public service/distribution clearance and per-cover human review
remain distinct from automated validation. Two earlier native timing misses
remain recorded under build/review-controls-validation; later full gates pass
without weaker thresholds, but the earlier flakiness is not certified fixed.
