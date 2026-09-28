# Rockbox playback OOM investigation — September 27, 2026

The inspection below describes the pre-fix installed build. Subsequent user
request removes all sync preference writes; see the follow-up at the end.

## Conclusion and scope

The mounted generation-60 export shows no malformed/stale playlist references,
missing managed files, oversized managed covers, embedded artwork or reported FAT
corruption. This does **not** establish that playback is leak-free or that every
audio payload is intact. USB mass storage provides files, not live Rockbox RAM.
No OOM was reproduced or allocator trace captured in this investigation.

The reported symptom is a mid-song stop with “Playlist OOM”; repeated Play attempts
eventually resume at the same position without reboot. The most actionable lead
is background database work: the saved config enables it although the user does
not use Database. SoundBox forces that setting and a minimum 10,000-entry playlist
allocation. Both policies need review. Background allocation contention is a
hypothesis, not a diagnosis.

Evidence is private and ignored under
`soundbox-ipod-ref/playlist-oom/20260927-223541/`: scoped inventory, copied firmware/
state/playlists/manifest, hashes, export/image/media reports, official packages,
upstream source and filesystem-check output. Personal media names stay there.
No export, deletion, firmware replacement, config edit or filesystem repair was
performed. `diskutil verifyVolume` unmounted/remounted the volume for a read-only
`fsck_msdos -n`. SoundBox was closed to eliminate competing polling.

## Playlist and ownership results

- 18 top-level M3U8 files, 313,179 bytes total. All are valid UTF-8 without BOM,
  use LF, and have no NULs or malformed paths. Every reference resolves.
- Largest: 2,883 entries / 136,071 bytes; longest path across all playlists is
  182 UTF-8 bytes. This is not an unusually large text playlist.
- Two playlists each repeat two device paths once. The largest playlist maps
  2,883 entries to 2,881 unique audio files. This small duplication is a membership
  cleanup finding, not evidence of runaway playlist growth or the OOM cause.
- All 18 playlist hashes match the current manifest. No extra active playlist
  copies were found. Compared with September 26's capture, the largest playlist
  decreased from 136,152 bytes; two snapshots cannot exclude transient rewrites.
- All 17,304 managed files exist with expected sizes: 2,881 audio, 14,405 artwork,
  18 playlists. Twelve path spelling differences resolve correctly on FAT's
  case-insensitive namespace; they are not missing files.
- No unowned regular media/art/playlist files were found in the active managed
  trees. Historical recovery remains separate and must be preserved.

## Artwork and audio results

All 14,405 managed covers fully decoded and matched their manifest SHA-256.
They are uncompressed 8-bit paletted BMPs, at 160, 130, 116 and 30 pixels square,
plus the dimensionless 160-pixel fallback. Maximum file size is 26,678 bytes.
Five covers per track are intentional retained profiles, not evidence of repeated
resync accumulation. No managed JPEG/PNG exists, so progressive JPEG or JPEG
metadata is not involved in the current exported cover set.

NightPod requests 130-pixel WPS and 30-pixel SBS art; both exact variants exist.
Its 24 bitmap assets total 451,744 bytes. Other installed themes include sprite
strips up to 2,400 pixels tall and a 307,338-byte bitmap. Two inactive themes'
`vol.bmp` files fail Pillow decoding; this may be a decoder compatibility issue
and does not establish Rockbox corruption. No active NightPod bitmap failed.

Bounded metadata inspection of all 2,881 managed audio files succeeded: 2,303 WAV,
480 MP3, 82 MP4-container and 16 AIFF. No embedded pictures were found. The scan
read about 5.63 MB of headers/tags, not all audio bytes; it does not exclude damaged
audio frames or intermittent storage errors. No canonical Mac source was read.
Per-track sibling covers avoid folder-art collisions.

## Filesystem results and limits

`diskutil verifyVolume disk4s2` completed FAT, directory and orphan-cluster checks
with exit code 0 and restored the mount. Approximately 140.94 GB is free on the
256.13 GB volume. A separate warning says the partition map does not occupy the
whole disk; it is not a reported FAT consistency failure. No repair was run.

Scoped enumeration found no symlinks, scan errors, `.sbpart` files or `.DS_Store`.
Largest active directory: 678 entries; largest enumerated directory: 710 entries
in recovery. There were 5,080 AppleDouble `._*` files, primarily in historical
recovery (3,741) and `.rockbox` (1,266), not duplicate active audio. Do not bulk
clean recovery to address this symptom.

There are also 66 audio files under `/Contents` and two in recovery, outside the
managed playback set. Their audio tags were not included in the managed scan;
no claim is made about who created them. `.soundbox` contains roughly 220.84 MB
of manifests/recovery. Protected `/PIONEER` content was excluded from application
inspection, as were macOS service-tree contents. Whole-filesystem checking is a
separate metadata operation, not a SoundBox content scan of that reserved tree.

Direct raw-device reads were permission-denied. Free-space extent fragmentation
was not measured; plenty of free space and a clean FAT check do not prove low
fragmentation. SMART health is unavailable on this USB device.

## Firmware, state and upstream comparison

The installed firmware is Rockbox 4.0 for `ipodvideo`. All 381 regular files in the
[official 4.0 package](https://download.rockbox.org/release/4.0/rockbox-ipodvideo-4.0.zip)
match the installed counterparts byte-for-byte, including firmware, codecs and
bundled plugins. Firmware is also identical to the September 26 capture. A clean
4.0 reference was extracted on the Mac only; clean-install playback has not been
tested. The build's `Memory: 64` does not establish physical RAM: this target's
[core allocator](https://github.com/Rockbox/rockbox/blob/v4.0-final/firmware/core_alloc.c)
adjusts for a probed 32 MB device.

The saved config says playlist limit **8,000**, browser limit 1,000, directory
cache off, database auto-update **on**, and NightPod. The database-in-RAM setting
is absent; the official default is off. Saved config does not prove an unsaved
runtime setting, so this does not contradict the user's observed 5,000 selection.
`.playlist_control` is 46 bytes with a playlist selection and shuffle record;
its old copy is 45 bytes. Resume files are 146–151 bytes. No oversized control
state was found. A newer `.resume.cfg.new` is staged state, not by itself evidence
of corruption. Binary database semantic integrity was not independently proven.

The September 26 config did not explicitly enable auto-update; the current one
does. Rockbox's [tag-cache worker](https://github.com/Rockbox/rockbox/blob/v4.0-final/apps/tagcache.c)
can scan without entering the Database UI and uses a maximum temporary allocation
for certain operations. The [playback allocator callback](https://github.com/Rockbox/rockbox/blob/v4.0-final/apps/playback.c)
can stop/restart playback while preserving position when memory is reclaimed.
This is a plausible mechanism to test, not proof that it caused this stop.

The [September 27 daily package](https://download.rockbox.org/daily/ipodvideo/rockbox-ipodvideo-20260927.zip)
was downloaded, not installed. Its version is `a51adaac4f-260926`; reported static
RAM is 1,675,632 bytes versus 1,255,776 in 4.0. Newer is not automatically leaner.
Upstream has a [playlist-resume allocation/locking correction](https://github.com/Rockbox/rockbox/commit/3892773bd7d9b070d2a07795c77e42c83bae3240)
and a later [OOM-cleanup correction](https://github.com/Rockbox/rockbox/commit/ea49358cc685a18a127176838c7706573b9682aa).
The latter fixes a regression introduced after 4.0; it must not be presented as
proof of a 4.0 leak or a guaranteed fix for this report.

The exact isolated literal “Playlist OOM” was not found in the examined 4.0 source/
firmware. Playlist allocation paths format function-name OOM messages; capacity
exhaustion has a separate buffer-full message. Preserve the user's reported text
and capture the complete screen when possible rather than assigning a call site
from recollection alone.

## SoundBox findings at the September 27 investigation baseline

1. `soundbox/rockbox_config.py` forces `tagcache_autoupdate: on` and at least
   10,000 playlist entries. A subsequent sync can undo this user's diagnostic
   settings. Review config ownership, required capacity and user preferences;
   do not silently treat database indexing as required for playlist playback.
   Current behavior remains unchanged in this documentation-only investigation.
2. `_safe_copy` in `soundbox/sync.py` hashes the staged destination before rename
   but does not explicitly fsync the copied payload before publication. Other
   byte/range/manifest writes do fsync. Normal macOS eject supplies the OS flush
   boundary, but “every write is explicitly flushed before publication” would be
   false. Add and test the missing durability step separately; this audit found
   no leftover partial file or demonstrated corruption from it.
   September 28 follow-up: the payload is now explicitly fsynced before
   verification/rename, with a flush-failure regression. Normal OS eject remains
   required; this does not establish the OOM cause or hardware durability acceptance.
3. Incremental ownership/art/playlist evidence is consistent, and the latest
   generation is smaller than the previous capture. A clean/full hardware sync
   was not performed: it would replace the evidence and require substantial
   writes. Source review is not a measured clean-versus-incremental A/B result.
4. Device eject uses normal `diskutil eject` with worker ownership checks, not a
   forced unmount. Successful verification/remount here is not an export/eject
   durability stress test.

## Controlled playback plan — still pending physical execution

Keep SoundBox closed and retain the captured baseline. Do not sync between tests,
because current policy would restore auto-update and the larger playlist limit.
Record exact error screen, elapsed playback time, track/format, battery, whether
a plugin ran, and whether the stop followed an automatic or manual transition.

1. Establish the existing setup's reproduction and use System → Debug →
   View buffering thread to record PCM/buffer usage, useful data, remaining data,
   track/handle counts and watermark at comparable points and after failure.
   Do not change limits further while testing this hypothesis.
2. Change only database auto-update to off, save and reboot. Replay the same
   playlist/theme/settings for a comparable session. A clean boot matters;
   changing a preference need not cancel work already in progress.
3. If it persists, compare the stock theme with the same 4.0 firmware and library.
4. Separately test a clean 4.0 `.rockbox` state using a reversible, reviewed swap
   with the captured old directory preserved. Then test the pinned daily with
   the same controlled settings; never mix daily codecs with the old firmware.
5. If still reproducible, prepare an instrumented build tracing allocation
   requests/failures, largest free block, live allocation totals, tag-cache
   activity and playback shrink events. Sample at repeatable points across track
   changes. Rising retained allocation at equivalent states supports a leak;
   insufficient contiguous space with stable live totals supports fragmentation.
   Ordinary audio-buffer occupancy alone proves neither.

The installed build exposes buffering diagnostics but not full allocator/logf
tracing; those cannot simply be enabled by adding a config key. USB cannot capture
RAM during ordinary standalone playback. No clean-install comparison, live
memory trace, leak diagnosis or regression identification is claimed complete.

## Follow-up: settings persistence blocks the comparison

User reports that after reboot the playlist limit returned to 8,000 and database
auto-update needed disabling again. The intended off-after-reboot trial therefore
has not been established. At this follow-up the iPod is not USB-mounted.

In the captured 4.0 source, `settings_save()` schedules a storage-idle callback;
changed settings are written to `config.cfg.new`, promoted to `config.cfg` on the
next settings load, then `fixed.cfg` is loaded last. The original capture contains
`config.cfg` and `.old`, but no `config.cfg.new` or `fixed.cfg`. This does not
establish whether a later save failed, was interrupted, or was overridden.
Determine whether reboot used normal shutdown or Menu+Select reset. Reconnect
without Sync and capture current config/new/old/fixed files before changing any
of them. Returning to 8,000 is not the 10,000 minimum SoundBox would enforce;
do not assign this specific reversion to SoundBox without evidence.

## Follow-up: user-owned preferences

The user confirmed Menu+Select was used, then verified settings persisted after
normal Play/Pause shutdown. Database-off playback testing is now underway; usual
failure window was reported as a couple of songs. No OOM outcome yet.

At the user's explicit request, SoundBox's sync preference writer, config
transaction/rollback baseline and settings-only drift check have been removed.
The dashboard observes auto-update without requiring it. Theme-size discovery
remains read-only. Historical findings above describe the inspected old build,
not the new policy. Runtime installation/validation is recorded in HANDOFF.md.

The user subsequently reports normal playback with no OOM so far and asked to
resume architecture work. This supports continued observation with auto-update
off; session duration and allocator behavior remain unmeasured, so it is not a
confirmed leak diagnosis or permanent-resolution claim.
