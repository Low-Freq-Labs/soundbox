# SoundBox exploratory ideas

Reviewed October 9, 2026. These are concepts requiring a UX workshop with Sonny,
not active tasks or approved changes. Committed sequencing belongs in
[ROADMAP](ROADMAP.md); current player behavior belongs in [PLAYER](PLAYER.md).
The original July 22 proposal is preserved in Git history.

Keep the existing interface focused. Choose small, useful additions rather than
implementing this list wholesale. Preserve source audio and the read-only
rekordbox boundary.

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

Sonny wants SoundBox-curated playlists available in rekordbox for DJ use,
potentially through explicit database writing. Workshop the transfer UX,
supported integration route, conflicts and recovery before implementation.
This idea grants no write authority: the current rekordbox database remains
strictly read-only, and it is separate from iPod playlist reconciliation.
