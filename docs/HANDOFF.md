# SoundBox current handoff

Updated October 24, 2026, America/Chicago.

## Current release

Sonny accepted the Settings presentation, requested removal of redundant apps and
old diagnostic outputs, then explicitly requested full wrap-up with commit/push.
The completed artwork/Settings/keyboard release is on `main`, tracking
`origin/main`; the containing release commit identifies this source revision.
The installed app's signed build revision is recorded separately below. No app
rollback or duplicate dist build remains; preserve that retention preference.

**Next user setup:** Settings → Connections → SoundCloud → Connect. Enter the
Client ID/Secret locally, Save, and complete browser authorization. The SoundCloud
app must register exactly `http://127.0.0.1:53927/soundcloud/callback`.
Actual Keychain consent, browser authorization and the user's first likes scan
remain external acceptance, not something the offline mocks certify.
[Connection setup](soundcloud-connection.md), [keyboard behavior](KEYBOARD.md),
and [ROADMAP](ROADMAP.md) hold the implementation/remaining acceptance details.

Sonny is updating macOS and expects more space after reboot. Eleven tracks lacked
analysis caches **before** installation; existing caches were preserved. Normal
preparation remains paused by the existing low-space guard (13.6 GiB observed,
20 GiB required). Do not lower the guard or manually rebuild the library.

## Installed runtime and curation

Installed **personal build 126** at **2026-10-24T07:33:57.057121-05:00**; relaunched
at **07:43:56-05:00**, finally verified **07:45:24-05:00**, PID 12556 then.
Signed source base: **e08a9b0b4fe9910a3d6c81e08a31f898e290e65d**, dirty source.
Runtime fingerprint:
**57a01e2e24df64f49b534abadfd23a26c841310cb2e5b1eafdbc121142f55dac**.
It matches the released runtime sources; later documentation/Git commits do not
restamp that older signed revision or require rebuilding unchanged runtime code.
Strict/deep signature, executable and native hashes match the built artifact:

- Executable: `cdabe5a05220706a23bbc51ede895a202a595138bfab93cc2f8efa4ae86416ef`
- Native: `b7c394ec50f0be62c9b9db9be3a2e856de216cdb18573ebc794761da863c826e`
- Preinstall library: `8173cd0289f3560fcc367d686ef7552d2295ce2a7605f54369a6b4385555a455`
- Post-quit/postlaunch library: `648f9adddfdd6c6856158af4a4c0a0848333417ff26e827e7dd092f1a478f335`

The only library difference is `/settings/columns_state`, inspected explicitly;
curation is unchanged: 10 custom playlists, 84 imported records, 487 artist
and 1,090 title overrides, 517 hidden tracks and 24 aliases. Artwork/default index
bytes and all **3,643 art files** are unchanged. Analysis/stock inventories retain
**3,266 / 8,459 files**; only their two disposable preparation-index files changed.
No waveform/art payload was deleted or rebuilt by this installation.

Final real startup: native deck active 07:43:57; 3,740 tracks / 75 playlists loaded
07:44:00; 3,226 unchanged analysis receipts reused; 11 genuinely uncached tracks
paused for space. Stock prep reused all 3,109 curated inputs with zero work; art
fill had zero eligible tracks. Logs: `build/artwork-review-oct24/real-startup.log`.

## Validation

October 24 **bare `./check.sh` and `./check.sh --render` passed all 1,138 tests**:
1,087 offscreen + 51 Cocoa, zero skips/expected failures, reconciled inventories,
native build, lint/types and visually inspected render. General, Keyboard,
Connections/credentials and expanded Graveyard renders were also inspected.
A separate native Cocoa smoke test using only a generated silent WAV passed
numeric Hot/Memory preview, original-page release, Space latch, remap/save/reload,
and source-byte immutability. Packaged isolated check confirmed personal actions,
native deck active, no library/device discovery and no art worker.

Initial compilation was blocked by the new Xcode license; Sonny accepted it and
both complete gates subsequently passed. An earlier broad attempt also exposed
a keyboard shutdown assumption on a minimal test host; it is guarded correctly
and the real worker-drain regression passes. UI harnesses now mock per-track
online discovery and retain unrelated worker ownership during pruning tests.
Current harnesses use synthetic state, suppress real device discovery, and never
access live Keychain/account credentials. No tests or assertions were weakened.
Instruction-file byte parity, relative Markdown file/anchor checks and diff
whitespace checks also pass. Current evidence: `build/artwork-review-oct24/`.

## Cleanup and retained evidence

Sonny explicitly approved deleting the old 3.5-GB diagnostic archive in addition
to redundant app/build rollbacks. Removed **4,549,925,705 logical bytes**, including
the preceding build-122 rollback, 36 empty app shells, duplicate dist app,
`build/personal/` intermediates and the old external diagnostic archive. Available
space rose from about 10.03 to 14.61 billion bytes; APFS availability can vary.
Only `/Applications/Soundbox.app` remains among inspected app copies. Active source,
venv, current small validation/native outputs and required fixtures remain.

Small install records and preinstall/postquit library snapshots now live at
`~/Library/Logs/Soundbox/install-evidence-2026-10-24/`. The former lifecycle rollback
and empty sync-performance rollback directories were removed. Do not refer to
them as live recovery locations or retain new zombie app/source copies after
accepted future installs. [PROJECT_LAYOUT](PROJECT_LAYOUT.md) records the cleanup;
old dated build-evidence references now identify intentionally deleted history.

Preserve generation-58 recovery **20260926-152340-47477cfa4a49** and offline capture
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/`, frozen waveform fixtures,
CDJ research fixtures and curated library backups. No source audio, rekordbox/ANLZ,
device payload or `/PIONEER` was written. Prior October 9 resize/terminal timing
observations remain unresolved, though unchanged reruns passed; their old diagnostic
archive was deleted at Sonny's request. This release does not newly certify wider
waveform equivalence, cloud availability, physical device lifecycle or public
service/distribution clearance.
