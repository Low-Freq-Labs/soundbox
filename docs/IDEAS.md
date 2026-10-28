# SoundBox exploratory ideas

Reviewed October 25, 2026. These are concepts requiring a UX workshop with Sonny,
not active tasks or approved changes. Committed sequencing belongs in
[ROADMAP](ROADMAP.md); current player behavior belongs in [PLAYER](PLAYER.md).
The original July 22 proposal is preserved in Git history.

Keep the existing interface focused. Choose small, useful additions rather than
implementing this list wholesale. Preserve source audio and the read-only
rekordbox boundary.

## Artwork decisions as ranking evidence — October 25 findings

Sonny asked whether Keeps and replacements can improve future selection. A local
read-only audit of saved decisions and prepared candidates found useful patterns:
existing artist/label art often beats a repost; matching artist prefixes can
inflate the score for a different song; nested or informal edit credits can be
lost; stripping years can equate distinct live-event recordings; genuine
profile-to-track-cover upgrades are useful. Some replacements
intentionally use another song's artwork, as Sonny explicitly confirmed. Keep
means the current image is preferred in this context, not that every alternate
is bad or the uploader should be excluded.
The reported SPASS candidate came from the explicitly named Plate Selling
account; the seller-role rules now include bare plate plus selling/trading forms.

Shipped per-track decisions protect approved covers. Duplicate tracking links
and equivalent resized/recompressed images are separately filtered from review.
Cross-track learning is not implemented. Possible future work: retain a small
local decision record with the exact displayed candidate and action, distinguish
artwork preference from recording identity, compare candidate evidence against
the existing source before suggesting a downgrade, and evaluate ranking changes
against both accepted and rejected examples. Never infer blanket publisher trust,
new artist aliases or song equivalence from an artwork choice. Keep broad manual
choices available and preserve explicit decisions across matcher changes.

These are evidence-led design findings, not authorization for automatic cover
replacement or an implemented training system.

| Idea | Existing foundation | Possible future addition |
| --- | --- | --- |
| Sync report | Backend reports and completion dialogs expose transaction outcomes | A more detailed summary of additions, removals, bytes, durations and warnings |
| Dry-run preview | Guarded planning and confirmation already exist | A designed itemized preview of the proposed device changes |
| Device health | Stock-iPod tabs show identity, storage, music, playlists and playback facts | Reliable battery/filesystem facts or equivalent Rockbox diagnostics where available |
| Incremental-sync feedback | Manifest reconciliation already avoids unchanged transfers | A concise unchanged/modified/new breakdown without replacing the sync engine |
| Sync history | Transaction/recovery evidence exists | A user-facing history of device, date, changes, bytes and duration |
| Duplicate inspector | Existing identity/source checks protect sync correctness | Review alternate files or masters and select a preferred version explicitly |
| Artwork dashboard | ART column, artist catalog, missing-art workflow and sticky blocks exist | One consolidated coverage view with explicit repair actions |
| Smart collections | Curated playlists exist | Small automatic views such as Recently Added, Never Played or Needs Cue Points |
| Library health | Missing-link repair, artwork status and playlist checks exist | A consolidated factual view of unresolved issues; no unexplained quality score |
| Verification summary | Explicit Verify/Deep Verify/Repair and transaction results exist | A clearer report of exactly what was checked, without gating ordinary idle eject |
| DJ readiness | Individual metadata and source checks exist | An explicit checklist for a chosen destination, without implying unsupported CDJ export |

Recovery counts and database digests remain backend diagnostics, not stock-device
dashboard rows. Embedded-art extraction is not shipped; its precedence needs a
separate design protecting manual choices and sticky removals. Stock, Rockbox,
generic USB and future CDJ capabilities remain distinct.

## SoundBox playlists in rekordbox — September 26 concept

The October 26 [approved prep plan](rekordbox-prep.md) now includes creating real
single-My-Tag intelligent playlists, behind explicit validation/review gates.
Transferring ordinary SoundBox playlists remains the separate concept below.

Sonny wants SoundBox-curated playlists available in rekordbox for DJ use,
potentially through explicit database writing. Workshop the transfer UX,
supported integration route, conflicts and recovery before implementation.
This idea grants no write authority: the current rekordbox database remains
strictly read-only, and it is separate from iPod playlist reconciliation.
