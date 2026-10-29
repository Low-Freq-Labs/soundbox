# Next session: analysis, track menu and rekordbox playlists

Sonny requested these changes October29 and then asked to checkpoint the session
and continue in a fresh context. This is committed scope, not shipped behavior.
Completed UI/export work and installed provenance are in [HANDOFF](HANDOFF.md).

## Track menu

Add a sandwich button immediately right of My Tags. Menu order:

1. Change waveform color: use the existing Blue/RGB/3Band display setting.
2. Beat Count Display: Current Position (Bars), Count to next MEMORY CUE (Bars),
   Count to next MEMORY CUE (Beats). Use the loaded RAM grid/cues and existing
   render updates, without waveform recooking, transport delays or new polling.
3. Export to USB: export the selected song into the USB collection with no
   synthetic visible playlist. Pin the chosen device mount/UUID through deferral;
   preserve current native songs/playlists and make repeated exports no-ops.
4. Analyze Track.

## Analysis

Add Analysis immediately right of General in Settings. BPM/Grid and Key are
functional options; Phrase is a disabled placeholder. Do not expose confusing
Normal/Dynamic modes or cloud controls. Auto analysis is off by default. Offer
an unchecked option to set a Memory Cue on the first analyzed beat, and a
Performance/Normal/Power saving process preference.

BPM ranges: 70–180 default, 48–95, 58–115, 68–135, 78–155, 88–175, 98–195,
108–215, 118–235 and 128–255. Reuse the pinned rbxport constant-tempo and key
analysis, rather than implementing another DSP engine. Handle bounded local
audio acquisition, process cancellation, identity binding and worker shutdown.

Decide and implement persistence/export semantics explicitly. Existing native
grid authority does not authorize arbitrary detected grids or source Key writes.
Preserve manual grids, source cues, Analysis Lock and the accepted source edit
protocol. A SoundBox-owned result must never masquerade as verified source state.
Do not clear waveform caches or slow track loading to accelerate Info.

## Ordinary source playlists

Create regular rekordbox playlists, add/remove songs, and delete regular
playlists. Membership removal never removes Collection tracks or source audio.
Keep intelligent playlists behind their separate existing gate.

Use [rbxport](https://github.com/chrisle/rbxport), already pinned under
`native/cdj/vendor/rbxport`. Its `rbl-db` writer is a useful reference, not live
write permission. Compare exact native metadata/counters/XML before publishing.
Keep planning on copies, then use exact library/schema/version authority,
encrypted verified backup, closed rekordbox/agent, shared Tag/Cue/Grid lock and
recovery journal, one desktop worker lane and exact target identities. Add
interrupted recovery and unrelated-row preservation checks before acceptance.

Sonny operates the reference UI. Captures and pending actions are in HANDOFF.
Initial comparison found rbxport uses tombstones for membership removal while
the closed native sample physically removed the membership row and renumbered
the survivor. Do not treat upstream defaults as proven native parity.

## Preserved work

Private evidence root:
`~/Library/Logs/Soundbox/playlist-analysis-ui-2026-10-29/`.
`unfinished-prototypes/resume.patch` preserves the analysis helper, standalone
planner inputs and native/Python beat-count prototype removed from the release
surface at this checkpoint. Review/apply selectively; it is untested and not
accepted. Full file copies are recovery evidence, not an instruction to overwrite
newer source. The patch expects the runtime files at this checkpoint.

Known prototype work still needed: convert Rust beat tuple order to SoundBox's
`[beat, milliseconds, BPM]`, bound decoding before allocation, test off-grid cue
countdowns and no-next-cue display, add native diagnostics/ABI handling and menu
wiring, and verify standalone initial/incremental/no-op exports. No new source
write API or authority was enabled.
