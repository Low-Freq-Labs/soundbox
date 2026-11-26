# First launch and metadata choices

A fresh installation prepares three pages before opening the library browser:
Library, Cleanup and Artwork. Library locates the current user's rekordbox DB or
lets them choose another location. Its preview runs in the joined native reader
helper without resolving media, opening audio or changing the source. Existing
and recoverable SoundBox state takes the ordinary recovery path, never fresh setup.

Cleanup defaults to **Keep my titles and artists as they are**. Selecting cleanup
shows examples derived from that whole library. Supported changes save to the
shared rekordbox DB through the existing native writer; the choice does not grant
write permission. A different library or unsupported native version stays guarded
until its independent compatibility acceptance is complete. A successful SQLite
copy test is not native rekordbox acceptance. Keep original still allows explicit
manual edits and aliases within their independently supported write domains.

Artwork is an independent, initially unchecked option. No automatic cover search
starts before the saved setup choices. Turning it off leaves explicit Fetch and
Choose artwork actions available. Settings → Cover Art can change the automatic
choice later. A provider-free build explains that local artwork remains available.

Setup saves choices before constructing the ordinary main window. Failed storage
keeps the page and choices open. Cancel joins its read worker and publishes no
partial library or setup state. The choices bind to the actual library identity;
another selected source does not inherit permission or an unrelated cleanup choice.
Existing installations retain their saved appearance, columns, artwork and policy.
Fresh appearance and accent Reset use white; the standard browser's final Format
column includes its overlay scrollbar without an extra empty strip.

## Cleanup rules and preservation

Featured credits (`feat.`, `ft.`, `featuring`) belong in the title, with the primary
artist in Artist. Direct collaborations stay in Artist. Cleanup retains the source
pair before deriving either field, so a partial save or restart cannot discard a
featured credit. Explicit manual titles remain authoritative.

Within automatic cleanup, each track contributes one chosen artist credit: manual
assignment first, otherwise its derived credit. Each constituent votes once on
that track. The most common case spelling wins, with deterministic ties; trusted
names supply confidence rather than duplicate votes. Collaboration identity,
order and separators remain intact. Ambiguous group names stay whole unless there
is independent constituent evidence. Explicit aliases take priority. This is case
normalization, not a global guess about who made a song.

Existing curation and fulfilled manual acknowledgements remain inputs to planning,
not disposable display caches. Keep original bypasses automatic tidying and case
normalization while preserving explicit edits and aliases. Browser, Artists,
artwork queries and detached exports use the same policy. No source audio tags or
audio bytes are rewritten by metadata cleanup.

## Implementation and checks

`soundbox.onboarding` prepares pure choices/examples; `desktop.onboarding` owns the
three-page dialog and joined read-only worker. `metadata_cleanup_policy` binds the
choice; `source_truth` freezes it for each plan. `metadata_provenance` retains raw
pairs and manual intent; `metadata_controller` owns durable native publication.
Bounded readback groups share complete readback and retain per-field native
receipts, restart recovery and exact-library permissions.

Run the metadata and UI integration suites plus native recovery coverage for writer
changes. `test_onboarding` covers independent options, failed saves, cancellation,
recovery detection, provider gates and explicit versus automatic artwork. Native
reader tests exercise preview against generated encrypted databases. Inline input
and column tests use isolated Cocoa windows. No check operates a real user app.
See [native prep](rekordbox-prep.md), [runtime contracts](runtime-contracts.md),
[installer](macos-installer.md) and [handoff](HANDOFF.md) for their separate scopes.
