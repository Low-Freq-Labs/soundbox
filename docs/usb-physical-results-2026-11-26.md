# TEST-2 USB execution — November26,2026

Sonny explicitly authorized destructive testing and reformatting of the scrap
TEST-2 USB. SoundBox182 was quit normally by Sonny before physical tests. The
coordinator binds the actual USB hardware/session, whole disk, partition, volume
UUID and mount before physical operations. A disk number or volume label alone
never authorizes a write. Private generated fixtures, scripts and receipts are
under `~/Library/Logs/Soundbox/rc-test2-20261126/`.

All source SQL, audio, artwork and analysis used here are generated. The encrypted
source fixture has37 ORM tables, three32-second PCM files, Unicode metadata,
duplicate/shared playlist entries, tags, cues and structural ANLZ packets. Source
audio hashes must remain exact. No live source SQL, Keychain, user music, other
device or real-app input/termination belongs to this campaign.

## Physical backend evidence

| Exercise | FAT32 | exFAT | Oracle / limit |
| --- | --- | --- | --- |
| Production physical formatting | PASS | PASS | Requested full-capacity single first MBR partition; fresh hardware/volume readback. Production exFAT formatting also restores the temporary two-partition test layout to one full-capacity partition. |
| Same-filesystem quick erase | PASS | PASS | Both eligible layouts use production `eraseVolume`; filesystem/map changes use `eraseDisk`. |
| Format refusals | PASS | Initial exFAT guards PASS | Invalid name, cancellation, stale UUID and source overlap refuse before erase; generated stock/Rockbox markers also refuse and are removed afterward. |
| Initialize / selected export / unchanged repeat | PASS | PASS | Actual Classic/Plus pair, duplicate/shared order, exact exported audio/art, playback-reference preparation, deep verify and unmanaged sentinels. |
|44 My Settings fields /168 choices | PASS | PASS |18 production settings generations per filesystem; independent offsets/CRC and preserved unselected bytes; original logical profile restored. |
| Native name / browse / display settings | PASS | PASS | Both native catalogs agree; actual macOS volume name remains TEST-2. |
| Selected title / cue / artwork update | PASS | PASS | Generated selected17 changes in ANLZ/Plus SQL; unselected standalone19 rows/assets remain exact. |
| Membership pruning / playlist deletion | PASS | PASS | Shared18 retained until its last playlist reference; selected playlist deletion retains shared17, then standalone19; unrelated sentinels and source audio exact. |
| Read-only / normal remount | PASS | PASS | Actual read-only mount returns EROFS30; native/profile/manifest hashes exact, no pending USB journal; normal writable mount restored. |
| Cancellation / controlled loss / refused recovery |15 PASS /2 FAIL |15 PASS /2 FAIL |17 generated cases per filesystem:15 preservation/recovery passes and two genuine known foreign-loss failures; child process exit is distinct from hardware power loss. |
| Actual ENOSPC / corrected retry | PASS | PASS | Temporary256MiB physical first partition; real allocated filler leaves under5MiB, audio copy returns ENOSPC28, exact old generation/no staging leak/no journal, low-space plan refusal, then successful fresh retry. |
| Legacy XML CLI |12 positive combinations /2 findings |12 positive combinations /2 findings | Tree/flat/both × derive on/off × cleanup on/off, exact M3U/audio/read-only/source/sentinel oracles. Conflicts below remain failures. |
| Normal eject / reconnect | PASS | Read-only remount PASS | Final native empty FAT32 volume normally ejected, then physically replugged by Sonny; new hardware session and saved native state verified read-only. No force fallback. |

Filler bytes are reclaimed through the retained original descriptor. No
check-then-unlink of a pathname grants deletion authority. The generated empty
leaf remains until the explicitly authorized whole-device reformat. Native
partition preparation succeeded; one harness assertion initially mixed GB/GiB
and was corrected against the existing verified layout without repartitioning.
The first read-only probe used the wrong diskutil plist key; its finally block
restored writable mounting, and the corrected probe passed unchanged byte oracles.

The physical initial exFAT volume repeats ENOTSUP45 for a vacant atomic
no-replace rename, while an occupied destination returns EEXIST17 and preserves
its bytes. This confirms the filesystem limitation behind the rejected CDJ
ownership prototype. It does not waive either open P1 overwrite defect.

macOS reused the mount device/inode and volume UUID during the read-only remount.
Those observations alone therefore do not prove a new attachment session. The
application's coordinator epoch and fresh writability/physical guards remain
independent requirements.

Legacy CLI preserves different unowned audio but returns0, and its new M3U can
reference those wrong existing bytes. It likewise returns0 when an unowned
playlist conflict prevents the requested playlist from being published. The
CLI's `validate` command deliberately checks reference existence, not audio
identity; its0 result cannot certify those bytes. Both findings repeat on both
physical filesystems and remain open. Where physical FAT/exFAT cannot create a
symlink, the harness records that capability limit; it does not claim a performed
physical symlink-refusal test. The separate local APFS refusal control passes.

After all cases, a final authorized quick erase removes every generated USB
payload and test subtree. Full-capacity single first MBR/FAT32 partition and fresh
UUID are verified; production initialization creates an empty Classic/Plus pair
and44 actual saved logical Global My Settings values. Canonical state is only
read. Deep verify is clean, the journal is absent, and normal OS eject succeeds.
Sonny physically unplugged/reinserted the stick while SoundBox stayed closed.
Fresh hardware/host/session binding proves the same authorized static hardware,
same volume UUID, empty native pair and all44 saved logical settings. The original
private guard intentionally keeps its initial session binding; future device
writes must explicitly validate the new reconnect binding, never reuse that
stale lease. The reconnect verification itself performs no device writes.

## Source and installed UI boundaries

The uncommitted Overview **Reformat USB…** footer restores a caller for the
existing guarded format dialog/backend. The music menu remains Export, Delete
playlists and Eject. Detached widget/source checks initially passed42 offscreen
and4 Cocoa cases. Review then reproduced source-separation races with Library
refresh and imported-path publication. Final source binds full active/saved path
inventory, waits for existing path owners, refuses new path changes while erase
owns the device, and resumes queued refresh/startup repair after joined cleanup.
The footer updates immediately at worker admission/retirement. Final focused
checks pass27 format/entrypoint,7 import/relink/first-frame and15 shared-fixture
ordering cases, plus2 final Cocoa footer controls; earlier7 Cocoa entrypoint
controls also pass. The complete final gate is running separately on1064 frozen
non-Markdown inputs. One intermediate full run was explicitly interrupted when
the follow-up ownership fix required a new tree; it is not a pass.

The personal format candidate is built, strictly signed/inspected and passes
isolated native/process/SQLCipher/ORM/AES checks. Synthetic3299-row complete
desktop readiness passes without device backends/surfaces. Its two launches run
alongside source tests, so their4917ms cold/4389ms warm timings are not a speed
comparison or a pass against the1500ms target. Source fingerprint:
`37d69e572cef6b149bc803d03069ffb8e9a36112420d442d64b01822ae95ebe6`.
Build provenance truthfully remains dirty personal182/1.0.0 from Git`86351c3`;
this is a different binary from the previously accepted182, not a restamp.
That binary is installed and normally running; Sonny confirmed normal appearance
and explicitly identified his newer column-width resize. Passive proof retains
that user change and proves canonical curation/other settings,11540 protected
files and OS preference bytes exact. Derived provider refresh changes15 uploader
fields and one permalink; account/all6156 IDs/order and all other fields remain
exact. The complete bare gate passes5978 tests with zero skips/expected failures.

Sonny's actual format test worked nicely. Independent read-only capture proves
the changed FAT32 UUID, empty native pair,44 saved logical settings and no pending
journal. This closes installed Overview format acceptance for this lane. The
previous app, full capture, duplicate package/scratch, completed generated physical
fixtures and captures are retired; small receipts/generators remain. Later
draggable/per-playlist column layouts and the requested context-menu placement
are separate source work and require their own update.

Backend calls on a real filesystem prove the recorded backend behavior. They do
not prove real-app button/drop/confirmation interactions, global-profile state,
browser/Info first frames, listening or CDJ firmware rendering. Those require
separate installed/manual acceptance. Generated ANLZ is structural and cannot
certify measured waveforms or beat grids. See the [RC checklist](rc-beta-checklist.md)
and [current results](rc-beta-results-2026-11-26.md).
