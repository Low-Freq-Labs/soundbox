# SoundCloud connection (personal build)

The personal provider can prioritize a user's liked tracks when choosing cover
art. Settings has General, Keyboard, Connections, and Graveyard tabs; public
omits Connections and does not import SoundCloud code.

## Setup

1. In SoundCloud's [Your Apps](https://soundcloud.com/you/apps) settings, set the
   app's redirect URI to exactly `http://127.0.0.1:53927/soundcloud/callback`.
2. Open **Settings → Connections → SoundCloud → Connect**.
3. Enter the app's Client ID and Client Secret and click **Save**.
4. Finish signing in and authorizing SoundBox in the browser. Return to SoundBox
   to see the account name and complete liked-track count.

The Client ID and Secret identify the registered application. Browser consent
supplies account access; credentials alone do not authorize reading `/me/likes/tracks`.
SoundCloud currently requires Artist Pro to register an application. See its
[registration guide](https://developers.soundcloud.com/docs/api/register-app) and
[OAuth guide](https://developers.soundcloud.com/docs/api/guide).

**Edit…** reopens the two prefilled credential fields; Cancel preserves the
existing connection. Saving changes the credentials and starts authorization
again. **Refresh likes** refreshes the local index; an already connected account
also refreshes at application launch. **Disconnect** revokes this app's access
and removes its saved credentials and disposable index. A network failure during
revocation reports an error and retains the connection for retry; already-invalid
tokens can be cleared locally. It never signs out the browser's SoundCloud session.

The callback listener exists only during sign-in and binds loopback, checks
state and PKCE, suppresses request logging, and closes on cancellation or timeout.
If port 53927 is occupied, close the other sign-in and retry. The redirect URI
must match the SoundCloud app settings exactly.

## Matching and persistence

The Client Secret, Client ID and OAuth access/refresh tokens live only in a
SoundBox generic-password item in macOS Keychain, scoped to the AppData root.
They never enter `library.json`, logs, build output, source control, or library
backup ZIPs. The secret field is masked. Keychain operations and network work
run outside the UI thread under a TaskOwner retained through actual completion.

`connections/soundcloud/connection.json` holds nonsecret account/status facts.
`connections/soundcloud/likes.json` is a disposable versioned index of liked
track metadata, artwork URLs and source links; it contains no audio or tokens.
These files are independent of canonical curation and excluded from library
backup/restore. Deleting the index does not delete curated artwork.

Likes pages are fetched only from `https://api.soundcloud.com`, with redirects
rejected. A complete scan replaces the index atomically; a failed/cancelled scan
retains the previous complete same-account index. Account changes clear old likes
before scanning. Rotating refresh tokens are persisted immediately, before any
subsequent request; their single-use semantics require serialized account jobs.

A confident, unambiguous artist-accountable liked match is tried before general
search. The manual picker shows relevant liked candidates first when they meet
the confidence floor and identifies them in the tooltip. General search remains
the fallback. A like is supporting evidence, not permission to ignore song,
artist, named remix or VIP mismatches. Metadata is read only; SoundBox never
likes/unlikes, reposts or edits tracks through this connection.

Connecting does not clear misses, replace manually chosen artwork, clear blocks,
or force a library-wide fetch. Existing per-track Fetch/Choose commands remain
explicit. New uncached tracks use the connected index automatically.

## Acceptance

Offline tests cover OAuth callback validation/cancellation, PKCE, pagination,
rotating-token persistence, account changes, failed scans, credential privacy,
editing/cancelling credentials, worker retirement, and likes-first selection.
Hermetic UI renders never use live Keychain, account sessions or devices.
Real Keychain consent, SoundCloud authorization and the user's actual likes scan
still require acceptance through the installed application; automated mocks do
not certify those external steps. Current installation status: [HANDOFF](HANDOFF.md).
