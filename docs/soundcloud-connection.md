# SoundCloud connection (personal build)

The personal provider can prioritize a user's public liked tracks when choosing cover
art. Connections shows rekordbox on the left and SoundCloud on the right.
Public keeps the rekordbox card and does not import or show SoundCloud code.

## Setup

1. Keep the existing SoundCloud application's website and registered redirect URI.
2. Open **Settings → Connections → SoundCloud → Connect**.
3. Enter the app's Client ID, Client Secret, and your public profile URL (for
   example, `https://soundcloud.com/your-name`), then click **Save**.
4. SoundBox reads the profile's public likes and shows its name and indexed count.
   It never opens browser authorization or waits for a callback.

The Client ID and Secret identify the registered application. SoundBox obtains
an application token with HTTP Basic authentication and the `client_credentials`
grant, resolves the public profile, then reads `/users/{user_urn}/likes/tracks`.
This flow does not use `/me`, private account access, or the website's session.
For application registration and access requirements, see SoundCloud’s
[registration guide](https://developers.soundcloud.com/docs/api/register-app) and
[OAuth guide](https://developers.soundcloud.com/docs/api/guide).

**Edit…** reopens the credentials and profile fields; Cancel preserves the
existing connection. Saving reuses a valid application token when the credentials
match, including a still-valid application token without a refresh token;
profile or credential changes discard the previous profile's index before
scanning. **Refresh likes** refreshes the local index; an already connected profile
also refreshes at application launch. **Disconnect** removes SoundBox's saved
credentials, application tokens, and disposable index locally. It sends no
revocation or sign-out request, so it does not revoke the website's authorization.

All Settings tabs are prepared before the main window first appears. Automatic
connected-account refresh runs on one window-owned worker; the ready Connections
section attaches before that worker starts and does not schedule a second launch
refresh. Cancel remains available until retirement, and completion or failure
updates that same section. Opening Settings later never repeats the initial
attempt. Cached likes for the same account remain available throughout, while
manual Refresh stays repeatable. Credential/token operations remain serialized.

The earlier browser/loopback connection remains backend compatibility for an
already-authorized legacy account and its token refresh/revocation. New connections
always use public likes; the UI exposes no browser/loopback setup. No SoundCloud
app registration, redirect URI, or website code is modified by SoundBox.

## Matching and persistence

The Client Secret, Client ID and OAuth access/refresh tokens live only in a
SoundBox generic-password item in macOS Keychain, scoped to the AppData root.
They never enter `library.json`, logs, build output, source control, or library
backup ZIPs. The secret field is masked. Keychain operations and network work
run outside the UI thread under a TaskOwner retained through actual completion.

When macOS asks for access to SoundBox's SoundCloud Keychain item, **Always Allow**
remembers approval for SoundBox. **Allow** grants one access and can cause another
prompt on the next read. This does not grant every application access to the item.
See [Apple's Keychain access guidance](https://support.apple.com/en-gb/guide/mac-help/kychn002/26/mac/26).
The personal app uses its stable `com.lowfreqlabs.soundbox` signing identifier and
the existing Soundbox Self-Signed certificate. Repeated prompts after an actual
Always Allow choice warrant a separate identity/Keychain investigation; never
work around them by exposing credentials or allowing all applications.

`connections/soundcloud/connection.json` holds nonsecret account/status facts.
`connections/soundcloud/likes.json` is a disposable versioned index of liked
track metadata, artwork URLs and source links; it contains no audio or tokens.
These files are independent of canonical curation and excluded from library
backup/restore. Deleting the index does not delete curated artwork.

Likes pages are fetched only from `https://api.soundcloud.com`, with redirects
rejected. Profile resolution alone may follow one exact HTTPS API `/users/…`
redirect; credentials are never automatically forwarded across redirects.
A complete scan replaces the index atomically; a failed/cancelled scan
retains the previous complete same-account index. Account changes clear old likes
before scanning. Rotating refresh tokens are persisted immediately, before any
subsequent request; their single-use semantics require serialized account jobs.
Metadata GETs check cancellation between receives and retain the existing
per-receive timeout. Connect/header waits can still delay cancellation. Token
POST responses finish reading and persist any rotated credentials before the
caller honors cancellation; aborting that response could lose the usable token.

A confident, unambiguous liked upload whose uploader name matches the artist is
tried before general search. Other accountable likes remain fallback evidence.
The picker merges general search and confident likes, ranking eligible artist
uploads first, then likes within that tier, then match score. An absent,
ambiguous or unusable liked match falls back to general automatic search.
Cached accountable likes remain usable if online search fails. The likes index
is an optional ranking aid, never the complete search
universe. A like is supporting evidence, not permission to ignore song,
artist, named remix or VIP mismatches. Metadata is read only; SoundBox never
likes/unlikes, reposts or edits tracks through this connection.

Connecting does not clear misses, replace manually chosen artwork, clear blocks,
or force a library-wide fetch. Existing per-track Fetch/Choose commands remain
explicit. New uncached tracks use the connected index automatically.

Dubplate seller/dealer/shop/trader name and handle patterns are excluded from
likes, general results, artist suggestions, previews, direct URL fetching and
new cache publication. A song title mentioning dubplates does not trigger that
rule. This is a source preference, not complete reseller detection; additional
accounts can be excluded in the artwork review. Custom exclusions live in
`art/source-policy.json`, inside ordinary backups. Historical images remain recoverable, but excluded sources are hidden and
automatically default to strictly matched artist-profile artwork. Missing
profiles stay hidden; no guessed public-track cover is substituted. Excerpts do not qualify as full-track
automatic matches. Uploader-name matching does not certify an official account.

## Acceptance

Offline tests cover public client-credentials authentication without a browser or
callback, profile validation and resolution redirects, token reuse/rotation,
pagination, account changes, failed scans, local public disconnect, credential
privacy, editing/cancelling credentials, worker retirement, ready Settings with
same-account cached likes, exactly one shared launch refresh/cancellation/completion,
receive-bound metadata cancellation, general-search
fallback and mixed manual results. Legacy OAuth callback/PKCE tests remain.
Hermetic UI renders never use live Keychain, account sessions or devices.
Real Keychain consent, application-token exchange and the user's actual public-likes scan
are checked through the installed application; automated mocks do
not certify those external steps. Current installation status: [HANDOFF](HANDOFF.md).

Artist/edition matching shares the metadata credit parser. Named editions use
their creator as effective artist, Unicode accent forms agree, and original or
different edits do not replace the requested version. Review can show labeled
possibilities from artist-branded alternate accounts and close song wording;
unattended fetching keeps its stronger threshold. No account-specific alias map.
