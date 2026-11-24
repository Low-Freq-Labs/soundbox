# RBXport reference audit — October30, 2026

Sonny requested tuning of existing analysis and waveform behavior against Chris
Le's public work, preserving the accepted appearance and avoiding new product
modes. All117 repository descriptions were inventoried. Five relevant source trees
were downloaded at immutable revisions into private audit evidence; no upstream
app/test script was run against the live library and no new dependency was added.
Installed provenance and release validation are in [HANDOFF](HANDOFF.md).

Rechecked November5 against responsiveness build167/runtime `3fb0ff60…`.
Build165/runtime `7ffde716…` and build163/tested source `cf2ef5b` are earlier
deliveries. Algorithm and validation
statements below describe the October30 audit; later component scheduling,
bounded saved-analysis reuse and tempo refinements are documented in
[PLAYER](PLAYER.md) and [the performance audit](performance-audit.md). They do not
change the pinned upstream source or broaden native source-write acceptance.
The current caller records `rbxport-b739d369-whole-v4`: it retains the bounded
head/tail contract, whole-v3 integer/attack fit and opening-beat restoration from
measured early attacks. BPM-only execution skips the key detector. Legacy engine
records and independent grid/key provenance remain supported. The adaptive-v2
identifier below records the original October30 checkpoint, not today's engine.

## Sources and useful boundaries

| Repository | Revision examined | Finding / decision |
|---|---|---|
| [rbxport](https://github.com/chrisle/rbxport/tree/b739d369665b492534dc2eae0733322c040a26d5) | `b739d369` | This is also SoundBox's existing vendor pin and upstream main at audit time. Improve our caller and compositor; no vendor replacement is needed. |
| [onelibrary-connect](https://github.com/chrisle/onelibrary-connect/tree/ac3edd3b8a18f7649d336ebd56d28b15923a05d6) | `ac3edd3b` | Reads `exportLibrary.db`, exposes track/cue/playlist/tag/settings rows and SQLCipher4 connections. Existing SoundBox Plus schema/units already cover these. Its cue-kind comments explicitly include assumptions; retain our native-fixture proofs. |
| [rekordbox-connect](https://github.com/chrisle/rekordbox-connect/tree/ff5b312993c70f958cfb7eccdb8fdc412305babb) | `ff5b3129` | Provides live DB polling/change events and bounded history. SoundBox already uses safer stable DB/WAL snapshots and explicit identities. Do not copy its live-open approach, row limits or add unsolicited polling. |
| [onelibrary-sync](https://github.com/chrisle/onelibrary-sync/tree/1cc5462c7474d488e8b85cb912a29f81988f018f) | `1cc5462c` | Converts OneLibrary to classic Device Library with decoded-result checks. SoundBox already prepares both catalogs, merges retained native inventory and verifies publication. Useful independent reference, without adding a second exporter. |
| [alphatheta-connect-rs](https://github.com/chrisle/alphatheta-connect-rs/tree/74e8a9193aff81af2b7e3b49d2dc0b9bbfb87769) | `74e8a919` | Network PRO DJ LINK protocols are a different feature area. Keep as reference; no network integration was introduced. |

At the October30 audit, OneLibrary was the name used for Device Library Plus by the
[official rekordbox FAQ](https://rekordbox.com/en/support/faq/devicelibraryplus-6/).
SoundBox already emits that `exportLibrary.db` alongside classic `export.pdb`.
The name alone neither changes our exact-schema authority nor establishes new
CDJ-3000X hardware acceptance. The [official firmware notice](https://www.pioneerdj.com/en/news/2026/cdj-3000-firmware-ver330-important-notice/)
also distinguishes CDJ-3000 and CDJ-3000X behavior; retain model/firmware-specific
acceptance rather than extrapolating from a repository's name.

## Analysis caller: concrete gaps fixed

The [analysis overview](https://rbxport.com/analysis/) describes attack placement,
arrangement-based downbeats, tempo transitions and key rules. The implementation
is authoritative when a web description and code differ:

- [Preset and pipeline](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/crates/rbl-analysis/src/lib.rs): `Rekordbox` disables tempo following; `Rbxport` enables it. Both call the same stages. SoundBox previously selected the constant preset. Its default now selects `Rbxport` and explicitly keeps attack placement. No genre switch or second engine is exposed.
- [Application caller](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/src-tauri/src/analysis.rs): the app retains a thirty-minute head, streams a longer tail and extends the last measured tempo. SoundBox previously stopped decoding at five minutes, then trusted the declared EOF. It now keeps up to thirty minutes/96M mono samples and streams the entire tail. Frame declarations, finite samples and format stability are checked even after the head fills. `analyzed_frames`, `decoded_frames` and `analysis_scope` describe the actual work. Beyond the bounded head, tempo is extrapolated; no unmeasured late transition is claimed.
- [Tempo](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/crates/rbl-analysis/src/tempo.rs): the existing onset, kick/phase and tempo-change rules are reused unchanged. Attack options retain the upstream900–9000Hz band,1ms RMS,±15ms search and steady-tempo snap. The selected BPM range still applies.
- [Key](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/crates/rbl-analysis/src/key.rs): the existing default rule is the0.1 minor preference. Key-only uses the same upstream key detector and preserves grids. Do not enable optional experimental rules merely because they exist.
- Inconclusive/unchecked components preserve previous results for the same audio digest. Different bytes never inherit the old grid/key. Legacy `constant-v1` records remain readable and playable. `grid_engine`/`key_engine` preserve component provenance through partial reanalysis, and playback reports the saved grid's engine.

The October30 default was `rbxport-b739d369-adaptive-v2`. Performance/Normal/Power saving
continue to select child scheduling priority, not different musical algorithms.
Audio capture, cancellation/reaping, Analysis Lock, native/manual-grid precedence,
existing cues and source-file immutability remain intact. This tuning does not
itself grant publication authority. Initial WAV and narrow CBR MP3 publication
subsequently passed their independent exact-library authorities; other codecs,
replacement and broader metadata remain gated.

## Waveform values and rendering

This section records the October30 implementation decision, not the current
palette/mini contract. The accepted November21 follow-ups replace BLUE's radial
palette and the derived 3BAND overlap mini: BLUE uses native palette families and
optional PWV4 metadata; 3BAND's mini uses dedicated PWV6 stacked-preview metadata.
Main 3BAND retains independent PWV7 geometry with gain 1.35 and joint seven-color
coverage. Current rendering and approximation limits are in [PLAYER](PLAYER.md).

The [upstream canvas renderer](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/src/canvas/waveform.ts)
and [theme tokens](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/src/styles/theme.ts)
were compared with SoundBox's packed decoder and Metal compositor.

| Item | Upstream | SoundBox finding / decision |
|---|---|---|
| BLUE PWV3 | Five height bits, three whiteness bits | Packed decoding already matches. Retain our recorded-cyan/radial signed-interval compositor; upstream's simple column ramp is not evidence that replacing it improves our captures. |
| RGB PWV5 | Three bits per channel, five height bits | Our red extraction yields even0–14 values and divides by14, exactly equivalent to upstream's0–7 extraction/division. No red-scale bug exists. |
| RGB intensity | Upstream normalizes each row's strongest channel to full | This is a display policy, not different bytes. Preserve our accepted authored-intensity/interval-occupancy/encoded-edge behavior; no validated improvement justified replacing it. |
| 3BAND PWV7 | Independent seven-bit band heights, with seven overlap colors | All seven palette values already match. Preserve our calibrated main-lane height transfers and full-rate streams. |
| 3BAND overview | Each band's inner/outer membership is retained | Our overview reduced the data to one dominant color. It now resolves all three existing layer profiles and their overlap colors, preserving brown/cream cores instead of flattening them to blue. No cache recook or main-lane change is needed. |
| Close zoom | Upstream documents conditional attack interpolation and separate PCM windows | These include rendering heuristics. Preserve SoundBox's accepted signed detail, complete physical-pixel profile integration, stable LOD and continuous translation. |

The main waveform's retained seven-color table is:

| Membership | Display RGB |
|---|---|
| Low | `0,85,225` |
| Mid | `255,166,0` |
| High | `255,255,255` |
| Low + Mid | `180,105,10` |
| Low + High | `210,220,250` |
| Mid + High | `255,240,215` |
| All | `245,235,215` |

The upstream [overview calibration notes](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/docs/reference/waveform-analysis.md)
distinguish authored PWV6 overviews from detail reduction and explicitly describe
empirical DSP and track-level residual error. Its half-height stacked preview uses
128/256/128 band scales; that is not the same surface as our centered mini strip.
Do not transfer these numbers blindly into the main waveform or claim universal
pixel parity. Exact source overviews are a useful further reference; this change
keeps current geometry/cache contracts and fixes the demonstrated lost layers.
Native PWV3/PWV5/PWV7 remain authoritative when available. Our existing derived
waveform fallback is a separate DSP path; no unvalidated filter swap or broad
cache rebuild was introduced by this audit.

## Evidence and practical limits

Private evidence root: `~/Library/Logs/Soundbox/rbxport-audit-2026-10-30/`.
The immutable repository catalog/revisions, downloaded source, baseline native
library/source, analysis tests, GPU tests and drawable captures are retained.

- A generated seven-minute track changes from120 to150BPM after310seconds. The default engine detects both tempos and analyzes all frames; canonical bytes remain exact.
- A truncated tail beyond the previous300-second cutoff is rejected. Legacy records survive state round-trip; partial/inconclusive reanalysis retains component data and provenance.
- Headless Metal tests require all seven overview palette masks, distinct cream/brown/blue layers, and silent background. BLUE palette/gamut and existing shader/math checks pass.
- Before/after native capture at50seconds of the frozen Nuclear source checks RGB/BLUE/3BAND at all13 zooms. All39 main drawables are byte-identical. This is scoped evidence for unchanged main rendering, not a universal visual-parity claim or new hardware acceptance.
- Final full-gate/build/install/restart results belong in HANDOFF. Source library,
  audio, curated artwork and existing waveform caches are not diagnostic outputs.
