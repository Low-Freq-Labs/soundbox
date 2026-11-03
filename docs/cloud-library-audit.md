# Native library and cloud audio audit — November 2, 2026

This audit separates three identities: rekordbox owns native song/playlist IDs,
SoundBox owns its curation and durable edit intentions, and SeaDrive supplies the
audio behind a filesystem path. A visible Finder entry, a populated waveform,
or a running tray application does not prove that audio can currently be read.
The fixes described here are installed in the November2 delivery. Exact binary,
curation and validation provenance belongs in [HANDOFF](HANDOFF.md).

## Completed migration and dated source proof

The requested upgrade uses the existing accepted registration and playlist APIs.
It does not introduce another native writer or a recurring release-user merge.
All83 visible legacy SoundBox-only imports have committed native registrations.
The04:21 read-only DB/WAL snapshot independently verified3290 visible local songs, each
with native ID/UUID, and all ten original custom playlists' complete membership,
order and duplicates. FLAVORS is a native folder. LIVE SETS contains
83 visible songs; its archived84th song stays hidden/disposed.

Each of the83 committed registration claims has exactly the SHA-256 captured
at preflight. This verifies the bytes registered, without downloading10.15GB
again merely to inspect progress. That native snapshot had5533active content rows:3290local
entries plus2243 pre-existing streaming entries. The streaming entries remain
preserved; SoundBox's local Collection is not a claim of support for their audio.

The later user correction keeps **Artists virtual in SoundBox**. It must not
create a native folder, a playlist for every artist, or a recurring DB maintenance
queue. Earlier native-artist progress belongs to a discontinued experiment and
is not an upgrade-completion target. The user explicitly requested removing those
temporary generated records through the existing accepted playlist deletion
domain, bound to their recorded generated root/IDs. This grants no song/audio,
real custom-playlist or unrelated native-playlist deletion. Current cleanup
readback and installed status belong in [HANDOFF](HANDOFF.md).
The03:55 accepted cleanup removed exactly1527generated playlists, their root and
3163owned memberships. The04:21 strict read-only policy audit computes1575virtual
groups covering all3290visible songs, verifies all ten native custom playlists,
and requires zero of the1528archived generated native identities to remain active.
Saved bindings remain recovery evidence. No native artist job exists for new or
upgraded users.

At that migration proof, original playlist/membership/key/content-file rows and
unrelated native tables matched the captured reference. The migration preserved
Sonny's native ALL LOCAL/COOKING reorder; later user reorders supersede its sequence. His subsequent five Gorilla Glue
cues change that song's cue-update fields and add its cue rows/aggregate; those
current user edits must survive. Counter increases and those recorded user edits
are reported explicitly, rather than asserting false whole-row equality.
The nine analyses then present, title/artist overrides, imported records, hidden
songs and disposal records matched that preinstall baseline. All nine authorities were
verified; only the independently accepted reorder flag intentionally changed.
The accepted native reopen also rotates session-registry fields, recorded by
names/hashes rather than credential values. Exact playlist rows/XML/unrelated
domains passed readback before restoring original order through the writer.

The subsequent Jantsen import raised the visible local Collection to3291,
imports to94, saved analyses to10 and native bindings to92. Its actual native
readback verifies all449saved grid rows, including the155→150BPM transition,
headline155BPM, key and177083ms endpoint. The dated3290-song audit must never be
used as a rollback target. The evening install preserved that newer curation and
all nine authority files exactly.

Detailed evidence stays outside the repository under the dated private
playlist-unification evidence root. It contains hash-only native comparisons,
exact existing encrypted reference pairs, registration receipts and redacted
provider summaries. No new full-library pair is retained for each progress check.
Registry credentials, account tokens and private acquisition workflows do not
belong in this audit or its output.

## Provider behavior

The locally installed SeaDrive is3.0.27 (`com.seafile.seadrive`). SeaDrive3 uses
macOS File Provider; opening its Finder root can start the extension. Reading
placeholder data requests a download, and local bytes may later be evicted.
These are separate states from the tray application's transfer counters.
[Seafile's macOS guide](https://help.seafile.com/drive_client/drive_client_for_macos/)
describes that architecture and the provider-managed cache.

The official changelog adds macOS “Always keep downloaded” in3.0.23 and records
a kept-offline rename fix in3.0.26. The older manual FAQ therefore should not be
used to claim that current macOS pinning is unavailable. Pinning intended working
music is an available user choice, not an app permission to alter provider state.
[Official SeaDrive changelog](https://github.com/haiwen/seafile-admin-docs/blob/master/manual/changelog/drive-client-changelog.md)

The actual failure had two parts. The GUI remained running while its File Provider
was absent, with repeated local IPC connection failures. Opening the existing
Finder root woke the provider; no account, cache or configuration reset was used.
The same song then streamed with the exact expected SHA-256, but its ctime changed
roughly ten seconds afterward while device/inode, logical size and mtime stayed
the same. That observation supports delayed provider metadata churn for this
incident; it does not prove the cause of every future timestamp change.

## Friction points and boundaries

| Boundary | Finding | Handling and remaining limit |
|---|---|---|
| Finder/tray → readable audio | A connected-looking GUI can coexist with an unavailable provider; zero transfer speed says little about a particular file. | Check the actual requested read. Existing error messages retain source context. Do not delete provider state to make a readiness indicator green. |
| Hydration → captured identity | A successful stream can be followed by ctime-only changes, causing an otherwise valid registration to stop. | Source now allows one fresh capture pinned to the original full SHA-256. It renews the exact epoch/alias claims and durably saves the replacement request before SQL. A persisted0/1 budget prevents endless automatic renewals across restarts. |
| Changed bytes → retry | Preserving size/mtime does not establish identical content. | The renewed capture must match the original SHA-256 and technical facts. Changed bytes or repeated drift fail closed; old native registrations are not recreated. |
| Header probing → cloud files | Seeking around a canonical M4A/WAVE can trigger provider work repeatedly. | Conversion classification uses one immutable local acquisition. Header parsing and any rendering use those captured bytes; they do not reopen the canonical cloud file for classification. Background format scans still defer dataless paths. |
| Warm launch → technical format scan | The controller wrote a media-format cache but never loaded it; compatible headers were reopened after every launch. | Source now reads a versioned, checksummed, source/library-bound cache in its worker. Only compatible facts with an exact canonical path and all five matching epoch fields are reused. Membership/native IDs come from the current library, never the cache. Corrupt, foreign, stale or missing caches fall back to inspection. |
| Provider metadata → display cache | Ignoring ctime everywhere could display stale technical facts or authorize the wrong file. | Technical hints retain exact epochs. Conversion/registration/export each preserve their independent byte/identity checks. Cache reuse never creates native or device write authority. |
| Cold audio → ready waveform | A resident waveform can appear immediately while its source remains cloud-only. | V5 permits provisional, matching-size placeholder previews without hydration. Selected load and export remain explicit audio-read boundaries. A waveform cache is not a certificate that playback or export will work offline. |
| Cached AAC/ALAC sets → deck display | A transient import state bypassed valid caches; the later compact-first preview visibly resized when full detail arrived. | Reuse valid canonical data, then publish overview, native mode streams and signed contour together. Manifest/grid/cues remain ready first. Partial caches wait for repair; selected-load workers save their own valid-cache upgrades for the next load. Separate preparation and explicit reanalysis retain targeted refresh paths. Interactive reads skip only unused spectral_24; every displayed section keeps its checksum. |
| Hydration → loaded cue/grid prep | Delayed ctime-only changes invalidated a loaded proof and a transient refusal stayed latched. | Capture the exact gesture durably against its original SHA, then renew identity in the existing source worker after a pinned byte proof. New gestures stay usable while checking; changed bytes retain intentions with an error. Late results recheck current library/track binding. |
| Adaptive grid → initial native analysis | A valid155→150BPM saved grid was rejected by an extra constant-tempo assumption after exact row equality had passed. | Compare every packet beat number, tempo and timestamp to the complete saved grid, including its endpoint. Preserve the existing supported-WAV authority and SQL scope. |
| Failed song → later imports | The first failed analysis job blocked later healthy songs. | Select and retire jobs by operation ID; retain each failure while healthy imports continue. A background analysis failure does not veto durable cue/grid capture. |
| Quit → captured saves | Teardown could stop unstarted durable work while the user expected saving to finish. | Normal UI Quit keeps SoundBox open and usable while captured imports/detection/source saves remain. Pumps continue; the next Quit proceeds after completion. Ordinary read-only preparation does not block Quit. |
| Sets → automatic musical analysis | Long fallback records could manufacture a constant grid/BPM and briefly project it into the browser. | Automatic BPM/key detection and automatic fallback beat extraction stop above15minutes. Cached automatic zero-anchor grids are omitted for long sets; native/manual/detected grids and saved user work remain preserved. |
| Native save → browsing/player | Per-playlist full table/tree rebuilds and repeated artwork/cache invalidation made otherwise background writes disruptive. | The accompanying UI work preserves stable item/track identities and avoids unrelated row resets. Source transaction ownership remains serialized; it must not become transport ownership. |
| Normal export → cloud timestamps | Hydration/eviction could make retained media appear to need another complete Sync. | Ordinary composite Sync preserves unchanged committed path/size/mtime/media-policy contracts across ctime-only drift. That is inventory reuse, not a fresh integrity proof. New, relocated and changed sources retain exact-byte verification. |
| Conversion → native locator | A new WAV path can otherwise strand an existing native record at its old FLAC locator. | The separately accepted locator domain finishes the verified path change on the shared source lane while preserving IDs, musical data, cues and memberships. Registration permission is not reused for this update. |

The cached-format benchmark used3287 saved entries:3195 compatible,88 deferred,
three errors and one conversion candidate. All3195 compatible entries passed
the new structural/checksum admission in47.511ms for2.43MB of local cache data,
off the UI thread. Each path still requires its current stat check before reuse;
this is not an end-to-end cold-cloud benchmark. Cache serialization/publication
also moved into the scan worker, so it no longer serializes the entire Collection
inside the GUI completion callback. Cache failures do not prevent current facts
or legitimate conversion intents from being processed.

The discontinued native-artist experiment also exposed repeated work independent
of cloud audio. The last
100 installed playlist operations took roughly four seconds to create their
committed receipt, followed by about two more seconds before the next intention;
creating and filling1575 groups separately therefore scales poorly. The final
virtual Artists policy removes that recurring workload entirely. Source also
composes at most64 existing accepted create/append commands into one durable
envelope on the same writer, paired recovery and closed-app guards. This changes
transaction overhead, not the allowed SQL tables or source authority.
Batching does not reactivate native artist generation.

An actual-catalog TEMP proof creates32 private playlists and64 memberships from
64 commands, without opening audio or publishing to the native source. Its full
logical database and XML exactly match sequential execution of the same child
plans. Every original playlist/member and all content, cues and unrelated tables
remain unchanged. Initial implementation took58.493s with profiling, dominated
by repeated pure recovery-plan reconstruction, XML parsing and full-catalog
hashing. Bounded exact-input reuse reduced that to4.631s on a slightly larger
current catalog; committed replay took0.165s. These are private proof timings,
not a promise of observed live completion time. Payload hashes, journal/archive
equality, source identity and app-closed checks remain fresh at guard boundaries;
only already-proven pure work is reused within its bounded lifetime.

## Retry and cancellation limits

Python acquisition streams one source into disposable local staging while hashing,
then inspects or transforms that local snapshot. With an expected digest, exact
captured bytes remain authoritative across metadata drift. Without one, acquisition
requires a stable device/inode/size/mtime/ctime observation, with one unstable-capture
retry. Partial or failed captures are discarded. Local staging space is checked
before capture; the configured budget allows one source and working derivative.

Only macOS CloudStorage ENEEDAUTH receives the two cancellable three-second
readiness waits. Permission, missing-file and generic I/O failures do not get
silently retried. The native decode worker uses the corresponding readiness
handling before AVAudioFile, preserves a ready waveform on failure and lets Play
retry the source. These waits are opportunities for a waking provider, not a
sign-in repair or permission to accept unread bytes.

An OS/provider `open` or `read` call can itself block before returning an error.
The six seconds of retry waits are therefore **not a total download deadline**.
Existing cancellation checks operate between returned reads/waits; shutdown must
retain and drain the worker, not destroy a blocked QThread. A hard cancellable
wall-clock limit would require isolating the blocking acquisition boundary and
testing cleanup/recovery independently. This remains a real external readiness
limit; the application cannot guarantee instant cold playback from an offline
provider. No cache/account reset, automatic pinning or forced worker termination
is part of the fixes.

## Validation

Existing tests cover exact-digest metadata drift, one unstable-capture retry,
persistent failures, discarded partial reads, byte changes with original size/mtime,
durable renewal-budget exhaustion, duplicate-registration prevention, source-only
header classification, placeholder cache reuse and native readiness cancellation.
These exercise private files and injected provider behavior; they do not log into
a cloud account or replace the real provider's implementation.

The technical-cache controller's nine cases pass in69.344s, including new tests
for current-ID rebinding without header reads, mismatch in each epoch field,
checksum corruption, foreign source/library, matching-cache dataless deferral,
and cache-write failure without blocking current facts. They verify unchanged
source audio/database bytes and absence of native conversion work for reuse.
The final November2 complete gate passed2144cases with zero skips or expected
failures, followed by package/native smoke and installed curation verification.
Exact current provenance is recorded in [HANDOFF](HANDOFF.md).
