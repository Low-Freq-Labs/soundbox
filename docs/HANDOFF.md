# SoundBox current handoff

Updated September 28, 2026, America/Chicago.

## Release complete

Local `main` contains runtime release **`f6013a966e3ccaf59ad1460fc1e8c1aed835b54d`**
and the subsequent documentation-only wrap-up evidence commit. The installed app
is the runtime release, not the later documentation revision. Wrap-up includes
commit/push and remote-HEAD/clean-worktree verification; no implementation or
installation work from this release remains pending.

The prior session access blocker is resolved: this session successfully wrote and
removed temporary probes in `/Applications` and `.git`, then installed normally.
`Soundbox Self-Signed` is available (self-signed identities do not appear in
`security find-identity -v`). GitHub SSH authentication failed; the existing `gh`
login works over HTTPS using its credential helper. Origin remains the SSH URL;
no global Git/authentication configuration was changed.

## Installed runtime and state

`/Applications/Soundbox.app`: **build 114**, source **`f6013a9`**, **clean**,
signed with **Soundbox Self-Signed**; deep/strict signature verification passed.
Final verified launch: **02:15:35 local**. Native CoreAudio/Metal activated from
the installed bundle; **3397 tracks / 75 playlists** loaded. Preparation reported
**zero analysis/grid/cue tasks** and zero artwork fetched. Six uncached records
had no eligible fetch; stock-art warming checked 2768 existing curated images.
App is left running with playback stopped and no track loaded.

Executable SHA-256:
`17b8fd19d041668f4e1b532e270bbe2be28bb2143284e01183dc7d86808a85db`.
Native dylib SHA-256:
`fb3ddc6869fdc853742251a480aa831e426ca35cb67a2934a1c34d00d2a06df2`.
Installed and candidate binaries matched exactly. Build log:
`/tmp/soundbox-release-build.log`.

Final post-quit and postlaunch library SHA-256, identical:
`0d3022feafcbf3fbc0dff2e19ae3a147adc238c80d804750837d4b4db83fc338`.
**202 Favorites**, all curation, **3298 artwork file hashes**, and **2924 analysis
size/mtime baselines** remain intact. Only `settings.columns_state` changed from
the pre-install library: Qt reordered the same three hidden-column index/width
records in its 249-byte serialization. No column dimensions or curated fields
changed. Earlier evidence remains preserved; no baseline was silently replaced.
No active/retired state journal exists.

Preserve the existing rollback directory
`/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`:

- `before-release-114.app` is the immediate predecessor; `before-persistence.app`
  and earlier recovery material remain untouched.
- `library-persistence-release-*` / `persistence-release-*-state.json` contain
  pre-install, post-quit, postlaunch and final quit/relaunch evidence.
- `release-installed-provenance.json`, `release-source-files.json`,
  `release-final-launch.log` and `release-autoplay*.json` bind the validation.
  Source evidence SHA-256:
  `a70fcfd542bdb9e98e943c1f064429c2fad44daea5654e602818e241941373a9`.

Also retain `/private/tmp/soundbox-sync-performance-rollback.vlodpruy/` and the
previous persistence evidence. App replacement used a move plus `ditto`; AppData
was not replaced. Both application quits returned macOS Apple-event “User canceled”
while actually completing; process exit was independently verified before each
next step. No forced termination was used.

## Shipped work and validation

- A2/A3/A8: serialized library/artwork publication, consistent backup snapshots,
  staged validated restore, exact rollback and interrupted-transaction recovery.
  Restore drains existing UI writers/results before final save and reloads only
  after finished/join. Stale area generations cannot write. Damaged library state
  without a valid backup stays write-disabled; save failures remain visible.
- A4 storage slice: `soundbox.artwork` owns local art/results/defaults with
  compatibility aliases in `artfetch`. Online UI/startup/providers and packaging
  are still coupled. `_safe_copy` fsyncs payloads before publication.
- Device rows have no loading suffix; bottom status retains Loading iPod and
  readiness gates remain. Terminal native flags override stale PLAYING snapshots;
  Play/Space-latched cues retain ordinary autoplay eligibility after release.
- UI tests/render use fresh temporary AppData and suppress real discovery before
  MainWindow construction and throughout execution. Fixtures never mutate live
  sources/devices or pristine captures.

Fresh final gate **02:09 local**: native build, pyflakes, mypy (39 engine modules),
**966 tests / four skips**, and hermetic render. Command:
`SOUNDBOX_IPOD_CAPTURE="$PWD/soundbox-ipod-ref/offline-lifecycle/20260926-163945" ./check.sh --render`.
`/tmp/soundbox-check.png` and `/tmp/soundbox-wrapup-loading-row.png` were visually
inspected. Complete inherited code/docs diff reviewed; runtime instruction byte
parity, relative Markdown links and staged diff whitespace passed. A trailing blank
line was removed from artwork.py after validation; no runtime logic changed.

Installed UI verification used ordinary mouse/Space controls with Random already
selected. Seeking near each track's end produced two native terminal transitions:
**Turn Up → KISS ME THRU THE PHONE → Baddadan** at 02:14–02:15. The next track's
waveform continued advancing; playback was then stopped. This is a bounded live
smoke test, not proof of the earlier intermittent stop's cause or long-duration
acceptance. The iPod remained disconnected. Reconnection/physical sync acceptance
is still open and does not block publishing this tested app release.

## iPod: disconnected, zero settings changes

Sonny reports no recent Playlist OOM and explicitly requires **zero iPod settings
changes**. No sync, config/firmware/media write, restore or new firmware experiment
is authorized by this wrap-up. Continue normal playback; root cause is unproven.
The earlier user-observed stability with auto-update off and preferences saved by
normal shutdown does not justify another settings change.

Last read-only review confirmed unchanged generation **60**, manifest SHA-256
`63844c8b231d77d7794a42252ebe038b1c2a46f624cab0599eea944e4ef7fdd5`.
Historical identity: UUID `F83BEF4A-47F1-31F7-96EA-E2BA53049BE1`, `/Volumes/IPOD`,
`disk4s2`; revalidate after reconnect. Last connected Sync-needed evidence was real:

- VOLATIVE track `93738342`: device `e`, current `E [FINAL]`.
- FINNUH track `213412723`: device `Knock2 -Dashstar (Finnuh Remix)`, current
  `Knock2 - Dashstar (Finnuh Remix)`. Titles affect DUBSTEP, TRAP and Collection.
- OG M3U8 grew 5279 → 5319 bytes. Original prefix matches its manifest hash;
  Rockbox appended `/Music/STAYNS/FUNQ DANSE/FUNQ DANSE.wav`, an owned track.
  A separately confirmed sync can adopt the custom-playlist addition. Do not
  suppress the badge or discard the appended membership.
- Other cached-library/saved-curation membership/order and source size/mtime
  matched. Theme art sizes 130/30 are already covered by 160/130/116/30. This
  bounded review did not audit every payload, recovery entry or clutter file.

Evidence: `/tmp/soundbox-connected-manifest.json`, `/tmp/soundbox-drift-review.py`.
Earlier OOM checks and their limits: [OOM report](rockbox-playlist-oom-2026-09.md),
ignored evidence `soundbox-ipod-ref/playlist-oom/20260927-223541/`.
Preserve generation-58 recovery, including `20260926-152340-47477cfa4a49`, until
separately reviewed physical acceptance. Complete pristine offline capture:
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/`; disposable copies only.
Earlier `20260926-163557/` is incomplete. Never restore captures to hardware
without a reviewed plan. `/PIONEER`, rekordbox and source audio remain untouched.

## Next work

Follow [ROADMAP.md](ROADMAP.md): A5 desktop queue integration; A7/A9/A10 worker
probes and measured performance; optional provider wiring/packaging; remaining
controller extraction. Next physical checks are normal Random use/reconnection,
then separately confirmed no-op I/O, cancel/retry, overlapping drops and eject
acceptance. Do not turn these into a new device write/settings experiment without
the separate authorization described above.
