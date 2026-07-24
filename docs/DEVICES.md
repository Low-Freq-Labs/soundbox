# SoundBox device layouts

Current as of 2026-07-24. Read this with `GOTCHAS.md` and the write contract at
the top of `soundbox/sync.py` before changing device detection or export.

## The simple model

SoundBox classifies a mounted external volume by the folders already present.
Detection is read-only. The profile describes what SoundBox may safely do; it
does not make a device writable by itself.

| Profile | Existing layout | SoundBox behavior |
|---|---|---|
| Rockbox | `/.rockbox/` | Export `/Music`, `/Playlists`, and `/.soundbox` only when the filesystem is FAT32 |
| Stock iPod | `/iPod_Control/` with an iTunes database area | Preview compatibility only; write nothing |
| Dual boot | Both layouts | Rockbox-style export on FAT32; never touch the stock database |
| Generic USB | Neither layout | Use the same Rockbox-style folder/playlist export on FAT32 |

Non-FAT volumes remain browseable but do not accept the Rockbox-style drop.
Unknown or failed probes never grant extra capability.

## Rockbox layout

The current export is ordinary files, so it does not depend on the Rockbox
tag database:

```text
/
├── .rockbox/                 existing firmware, never edited by export
├── Music/Artist/Track/       copied audio plus cover.jpg
├── Playlists/*.m3u8          UTF-8, drive-root-absolute audio paths
└── .soundbox/manifest.json   v2 path ownership, hashes, membership, generation
```

The official Rockbox iPod manual describes a FAT32 iPod partition and the
firmware at the volume root. Its playlist catalogue setting defaults to
`/Playlists`. Users may play exported playlists through the file browser
without initializing Rockbox's optional tag database; database users can run
Rockbox's update/initialize workflow afterward.

Reference:
[Rockbox Manual for iPod Video](https://download.rockbox.org/daily/manual/rockbox-ipodvideo/rockbox-build.html).

## Stock iPod layout

Apple's classic database layout is not a folder-copy target. Typical stock
iPods store renamed media below `iPod_Control/Music/Fxx` and index it through
`iPod_Control/iTunes/iTunesDB` or a related database. Database generations and
some models require model-specific checksums. Copying a normally named song
into that tree without a correct database update does not create an honest,
usable stock-iPod export.

SoundBox therefore does not write stock iPods yet. The preview reports detected
model metadata, present source formats, missing files, and the explicit blocker.
It never creates a folder or changes a byte on the device.

Research references:

- [libgpod overview](https://sources.debian.org/src/libgpod/0.8.3-17/README.overview)
- [libgpod file-layout API](https://tmz.fedorapeople.org/docs/libgpod/libgpod-File-handling-functions.html)
- [Apple iTunes Library framework](https://developer.apple.com/documentation/ituneslibrary)

The Apple framework is useful for reading a Mac's iTunes/Music library. It is
not a classic-iPod database writer, so it does not remove the need for a
separately validated `iTunesDB` implementation.

## Safety and validation

- Resolve the mount and confirm it is external before every write/delete.
- Pin a `DeviceSession` to the resolved mount identity and reject a disappear,
  remount, symlink escape, source overlap, or case/Unicode destination collision.
- Refuse any target that overlaps a source file or source-library parent.
- Never write below `iPod_Control`, even on a dual-boot iPod.
- Existing unknown target files are conflicts and never claimed. A differing
  manifest-owned file is required repair work and is atomically replaced.
- Copies land as `.sbpart` and atomically rename only after completion.
- Device cleanup is limited to SoundBox's `/Music` and `/Playlists` trees.
- Normal Verify checks managed sizes/references; Deep Verify rehashes every
  managed track, cover, and playlist.
- Eject flushes and rereads managed references after writers finish, requests
  normal macOS eject, and asks before any force-unmount.

Unknown devices also expose a threaded import action. Selected audio or M3U8
entries copy in exact playlist order to
`~/Music/SoundBox Media/Unknown Artist/` by default, then register those owned
copies in SoundBox. The device and original Finder/Downloads files are never
moved, modified, or automatically removed.

The hermetic profile tests build all four layouts and compare every pre-existing
byte before and after detection/stock preview. The removable-volume fixture test
additionally snapshots device content, exports generated audio, verifies every
playlist path, and removes only the fixture trees. Volatile `.Spotlight-V100`
and `.fseventsd` indexes are expected to update whenever macOS observes a
filesystem write; they are not edited or cleaned by SoundBox. Every
nonvolatile pre-existing entry—including the full Pioneer tree in this
fixture—is hash-compared before normal eject.

## What remains hardware-gated

A real Rockbox iPod still needs checks for playback, theme artwork, playlist
order/folder nesting, database refresh, and full-library scale. A real stock
iPod writer remains future work: collect user-owned database fixtures, identify
model/hash rules, make round-trips byte-auditable, and validate recovery before
the UI can ever offer a write action.
