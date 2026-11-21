# Historical handoff snapshot — November20, 2026, before final wrap

This is dated delivery/evidence history, preserved from the former long handoff.
All uses of “current,” unfinished status, app paths, retention and permission below
refer to their recorded checkpoint. They are superseded by the
[current handoff](../HANDOFF.md) and [runtime contracts](../runtime-contracts.md).
Old app/source approvals, captures and installer helpers are evidence, never
permission to replay, restore, activate authorities or overwrite newer curation.
Obsolete generated payloads may already have been retired under bounded retention;
consult current cleanup receipts before relying on any historical path.

---

# SoundBox current handoff

## November20 developer testing workflow follow-up

Sonny explicitly changed the workflow: small local fixes use focused relevant
regressions plus affected integration/lifecycle/error paths, static checks and
Cocoa/render when needed. Local delivery keeps strict package/isolated startup
and the guarded state-preserving install/reopen. The full bare zero-skip gate is
mandatory before commit/push or wrap-up, not after every small correction.
AGENTS/CLAUDE, runtime contracts and current test docs agree. No commit/push asked.

`./check.sh --suite AREA... --level quick|integration|recovery` adds16 named
areas with cumulative depths. USB/CDJ and iPod are separate core suites. The
registry covers every complete current test module (393 at this checkpoint),
with static reference/completeness guards. Source writer/SQL/journal/ACK changes
need affected recovery coverage; quick RB DB is reader/guard smoke. `--list-suites`
lists options; `--plan` previews exact selectors/platforms without importing Qt or
tests. Areas combine with each other or exact `--focus` selectors. Named runs
use private offscreen then Cocoa subprocesses, automatically routing native-deck
checks and deduplicating actual IDs. Explicit `--cocoa` can force UI coverage.

Reports retain focused scope, requested areas/depth, planned coverage and actual
phase outcomes. Invalid selections clear stale proof; failed/missing/mismatched
children cannot yield green or pass complete-release verification. Twenty-one
runner/planner regressions pass4.152s; the strengthened whole-module catalog
check passes separately. Real USB+RB quick131 pass3.593s; player+waveform quick84
pass3.861s (79 offscreen then5 Cocoa). Earlier sidebar7 and tag15 command checks
also passed. Independent static/execution design reviews found no blocker.
Shell/lint/layout/parity checks pass. Scheduling hints remain from the last full
report for391 measured modules; the estimated~140s scheduling improvement remains
an estimate, not a newly measured full gate. No fixtures or recovery cases removed.

A fresh installed-build check at23:31UTC confirms the installed88b6/5e45 app's
233 runtime modules/startup scripts match the tested current source, with all four
native text sections matching the tested build and the installed signature valid.
The actual installed executable also passed `--isolated-check` with native
CoreAudio/Metal, process guard/query, SQL/ORM and AES active; zero devices, no live
library loaded and no artwork worker. This explicitly bridges focused source/native
results to the installed binary. No real-app input/termination or source/device
writes occurred. Compact proof: `named-areas/installed-build-parity.json` plus
isolated runtime/logs. This is installed parity/startup evidence, not a newly run
complete suite or physical-device certification.

These are developer-harness/documentation changes only. Installed application
runtime below remains88b6/5e45; no app rebuild/reinstall, source/device access or
curated-state write occurred. The earlier5069-case full proof stays historical
for that installed checkpoint; a fresh complete gate is pending before publishing
these tooling/test changes. Compact evidence:
`~/Library/Logs/Soundbox/testing-workflow-20261120/` (named-area extension under
`named-areas/`). See
[test workflow](../test-release-gate.md) and [dated audit](../testing-runtime-audit.md).


## Installed November20 literal tags, immediate use, smart icons and thin divider

Current `/Applications/Soundbox.app` is dirty personal173 at Git base
`15ba4552dd31a91c9278a8f7a7f4b587a2081c92`, source
`88b6e0389f727a2d2680ba8e0a7e720ce7f1e8d202ca8c65ca61288fb7e8d921`, executable
`5e45667b337c9630cd3efa2812b20845e39fad921e13131b767fd0115acd3070`.
Installed2026-11-20T09:13:12.433230UTC and ordinarily reopened09:13:27.070147UTC.
Passive09:13:48 verification proved complete logical equality,6433 retained records,
217 protected authority/art files unchanged,3298 tracks/85 playlists, native
CoreAudio/Metal, zero prep tasks, zero artwork fetched/failed and no startup errors.
PID659/start03:13:27local is historical verification identity, not future control
permission. SoundBox is open; rekordbox was closed for delivery. Saved white
Finder/Dock icon and every current edit were retained. No app input/termination,
source restoration, permanent music deletion, native writer activation or commit occurred.

The R&B native name was already correct; Qt checkbox mnemonic rendering hid its
ampersand only in My Tags. Display escaping now follows raw-text elision, retaining
native/tooltip/accessibility identity. Independent six-name pixel checks and
focused offscreen/Cocoa tests pass. Newly created tags appear and accept rapid
assignment/smart-playlist gestures immediately. Exact source/library/tag/category/
creation-op pins remain attached; durable parent intent precedes child admission,
and native creation plus verified ACK/publication precedes child source dispatch.
General ownership retains work while yielding only prerequisite dispatch. Healthy
and failed ACKs, unchanged queued successors, publication retry without source
replay, restore/source switches and normal Quit retain their distinct guards.
Uncreated definitions cannot be renamed/deleted/reordered. Verified Library/export
truth and native authorities are unchanged. Prepared-art reuse during a pending
playlist-only refresh removes the reproduced current-row art reannotation lock;
unknown art retains ordinary preparation.

Smart playlists add a clean vector gear to the chosen base icon using exact
native kind4, preserving labels, selected backgrounds, hit areas and custom icons.
Three new pixel/identity/HiDPI cases plus eight existing icon cases pass on both
platforms; actual Retina/white-accent previews were inspected. The browser divider
is one plain pixel with no native grip/dot. Qt retains its wider invisible mouse
grab and resize cursor; the seven existing sidebar cases and Cocoa pixel preview
pass without changing sizing/restoration behavior.

The deleted-FLAVORS audit matches exact old ContentID/source-bound UUID sets:
RAP303, POP84, CLASSICS→CLASSIC33, INDIE37, R&B30, REGGAE11, with no missing/extra
or duplicate old entries. ROCK remains regular and was not deleted. Old association
order is evidence, not a claim about smart sorting. Original05:20:03UTC/counter381498
receipt `flavors-membership-audit.json` SHA
c1ebcfb728da2136ea336241043dbc575452e802247104273fe20929f93fd4bc binds exact committed
delete before-rows and independent current SQL/production ORM evaluation.
Fresh06:02:44UTC/counter381530 recheck after both apps closed confirms all six sets;
`flavors-final-recheck.json` binds the later snapshot. Both audits were read-only;
no full diagnostic source copy was retained.

Fresh bare `./check.sh --render` passed5069 cases:5001 offscreen/906.825s and
68 Cocoa/83.009s, zero skips/xfails, all920 frozen inputs exact and render inspected.
Strict personal package233-module/startup-script parity, four native text sections
and isolated native/process/SQL/AES runtime passed; packaged render was inspected.
First-gate evidence retains eleven legacy-fixture API errors and two real lifecycle
assertions. Fixtures bind the actual new projection method with every assertion
retained; assignment ownership again uses original verified/current-source/restore/
shutdown gates separately from prerequisite reservation. All32 assignment/pending
cases pass both platforms. A second run spanned repeated machine sleep/dark wakes
and timed out four import cases plus one unification case; all37 import cases and
the unification case pass unchanged after wake. Complete failed-run evidence stays
under `first-gate/` and `suspended-gate/`. The final uninterrupted full run passed
without changing waits/oracles. Its temporary idle/display assertion was stopped.

Sonny normally quit at “okay you are good!” after reopening the old app during
validation. Fresh09:12:50 capture preserved his newer `columns_state` and
`smart_playlist_recency`,6433 records/11,659,530 portable bytes and all settings.
Counts100 imports/331 title overrides/491 artist overrides/10 custom playlists/
16 analysis results remain exact. The signed install and passive reopen comparison
used that latest complete payload, never the earlier settings. All native engine/
publisher bytes remain unchanged froma09d; the previously accepted exact-library
playlist authority remainsd38da8f91f6c24d3dcd70bb115602ba89723dbc48cdd2ff549ee15eca00dcfd0.

Evidence/helpers are under `~/Library/Logs/Soundbox/ui-followup-20261118/`
`corrections/ampersand-tag-label-final/`. The sole active previous app is a09d at
`ampersand-tag-label-final/previous/Soundbox.app`; older1e96 was reversibly OS-trashed
with signature/executable/inode proof. Keep that one previous app pending Sonny’s
use of this update and the required acceptance/recovery payloads. Never restore
older source order, counters, failed queues or curation. No commit/push authorized.

## Previous November19 collapsed-folder and creation follow-up — personal173

At that delivery, `/Applications/Soundbox.app` was dirty personal173 at Git base
`15ba4552dd31a91c9278a8f7a7f4b587a2081c92`, source
`a09d53ea0feec86d63abbdbbacd0235c453094367a0bdd62a90f852243507d16`, executable
`49f5be918847559b1c2685419450e36bfb9aa5f18e5d743809f427500fa852ab`.
Installed2026-11-20T04:59:16.717674UTC and ordinarily reopened04:59:46.758761UTC.
Passive05:00:16 verification proved complete logical equality,6433 retained records,
217 protected authority/art files unchanged,3298 tracks/86 playlists, native
CoreAudio/Metal, zero prep tasks, zero artwork fetched/failed and no startup errors.
PID89474/start22:59:46local is historical verification identity, not future control
permission. SoundBox was left open; rekordbox was normally closed for delivery. Saved
white Finder/Dock icon and every current edit were retained. No app input,
automated termination, source restoration, permanent music deletion or commit occurred.

Real folder summaries now carry native attributes and reused items reconcile
those kinds. Folder rows have before/inside/after zones. Inside draws both row
edge accent lines without filling the label and appends regular/smart playlists
or folders at the bottom, collapsed or expanded; expanded children also allow
precise placement. Right-click creation defaults below the clicked sibling for
normal/smart playlists and folders. A durable placement sidecar transfers to an
accepted Move in the same verified creation ACK; failed ACK/restart preserves it.
Pending smart creations immediately show their projected count/members while
native/export truth waits for ACK. Future-created constructor rows and clocks
are handled only through exact saved identities and actual causal transitions.

INDIES exposed legacy constructor guards after moving POPS left a root sequence
gap. One user-operated native reference proved maxSeq+1 smart construction plus
normalization. Original source pilot manifest6d5812d9… completed once under actual
call_2d7aabafeb194b279f48b9e53ff9c1fd approval: op717b7ca5609148b89d7a7c43a98b2727
created INDIES2869332571/8574e43f-86c0-47aa-a0f5-95b5eb6b74ff with existing Indie
2538875640/54ddd6d1-edc1-4169-ad08-51d27bab1016; op1b2762e1f1584a4aa2ce38ef04741709
created one disposable empty GENRES folder2447309579. Counter381403→381409;
exact queue upgrade/ACK and full source preservation passed. Never replay it.

Sonny correctly expected37 Indie songs. Original receipt9f1b3aeb… requested only30
and wrote all30; seven valid targets were absent before native dispatch. Existing
logs cannot recover the exact frontend cause. Actual-window37-row publication
regressions preserve all37; deliberate filtering retains30. New bounded logs
record visible/full/selected counts, target hashes and at most eight lost IDs.
No speculative fallback adds invisible tracks. All three new cases pass both
platforms and95 related cases pass. Private bulk-indie-selection-audit.json
records the exact historical limitation and missing targets.

Sonny's37-song instruction and closed confirmationcall_1649f732… authorized the
separate seven-assignment completion. Manifestfe1561be…/helpera560b18c… were
independently reviewed before new op29952141dac24a33a3b2c08f0c9ba506 completed once,
counter381409→381416. Result060be044… and reviewfa94de0e… prove only seven links/+7
counter, original SQL/XML preserved and exact sparse ACK/6433records/nonqueue
curation. Original30 receipt/baseline and all ten portable backup bytes remain
retained; one oldest live backup underwent normal rotation. Never replay this repair.

Sonny confirmed native INDIES37 below ALL LOCAL and the empty test folder last
in GENRES at call_02073a5a…. The first capture refused after rekordbox exited;
he reopened/kept it open at call_45d3e9b…. Native-open proof7615413c… passed under
the actual kernel/process and full SQL/XML guards. He deleted only the empty test
folder and quit at call_281e37c1…. Final proof8f3d6773… proves only that folder's
removal, last verified counter381417, with INDIES/rules/all37 links retained.

Immutable bridge468eccff… preserves the originala98d/915-input source30 proof
separately from the newa09d/916-input UI/test refinement and actual seven-link
repair. Every publisher byte stays unchanged. Derivative plan afa15349… inherits
only original2d conditional consent for the same four constructor fields.
Independent adapter review21fa7ac9… and nine refusal checks98a44324… pass.
Authority-only activation completed04:58:07.074318UTC:4076456e…→d38da8f9…,
smart_normalized_create_accepted/profile and normalized_folder_create_accepted/profile
only. All other permissions/source/canonical data stayed exact. These profiles
remain independently default-off for other libraries; old profile meanings and
unobserved deletion/history guards are unchanged.

Fresh bare full gate passed5037 cases:4969 offscreen/841.354s and68 Cocoa/78.152s,
zero skips/xfails,916 frozen inputs and inspected render. Strict package233-module/
startup-bytecode parity, four native text sections and isolated native/process/
SQL/AES startup passed. Failed first-gate evidence remains separately retained:
six lifetime fixture errors and two creation fixture races. Corrected fixtures
use the real unified tree/source-admission pause without weakening any oracle;
focused lifetime3 and creation24+24 cases pass. Documentation-only handoff changes
are separate from the true installed runtime identity above.

Evidence/helpers: `~/Library/Logs/Soundbox/ui-followup-20261118/corrections/`
`collapsed-folder-drop-final/`, with source/native/activation under
`native-pilot/repair-seven/`. Complete preinstall snapshot is6433records and
11,659,528portable bytes; protected authority receipt was refreshed after activation.
Current counts100imports/331titles/491artists/10custom/16analysis remain exact.
At that delivery the previous app was1e96 in
`collapsed-folder-drop-final/previous/Soundbox.app`; the November20 install above
subsequently rotated it to OS Trash with exact identity proof. Olderad05 had
already been OS-trashed. Required acceptance/recovery payloads remain protected;
never restore earlier source order, counters, failed queues or curation.

## Previous November19 generic node-placement follow-up — personal173

Private `corrections/all-playlist-moves-final/` holds the current work. The first
owned isolated gate was deliberately interrupted for the deep-smart routing fix.
The second ran4916 offscreen cases and found one old identity-drag fixture missing
RB_NODE_KIND_ROLE; only its ordinary-kind metadata/import changed, preserving all
five test bodies. Both focused platforms pass. Fresh bare full gate passed4984:
4916 offscreen/720.2547s and68 Cocoa/82.7172s, zero skips/xfails,910 exact frozen
inputs and inspected render. Strict package232-module/startup parity, four native
text sections and isolated native/process/SQL/AES checks passed. Failed/incomplete
evidence remains separately retained.

Sonny approved six exact disposable source operations and conditional enablement
at call_758a70a38bc746d3a6f5ec7e7308992c. ManifestSHA
`cd902a1cca539891653a6e70dfb5dac4684d86f14b5ad6c305508554a4bacd4a` completed once,
counters381274→381283: created4203156571/2871714202/1062324488 and nested them under
GENRES. Complete recovery pairs and all-original-data/canonical preservation pass.
Never replay this completed pilot. User native confirmation and supplied screenshot
show the exact nested tree; native-open proofSHA
`0de0bc3e5ee1b7f24795cf92ece1d530b44ce44021ea5fba24619134087da0f8` preserves all
rows/XML apart from seven known hash-only native-session registry changes. Actual
rekordbox6.8.7.0303 was PID76792/start03:02:35.520373UTC (historical identity only).

Sonny deleted only the test tree and quit at call_f33962c681d34fdfa31d276dc784f860.
Trusted cleanup verification passed: only the three disposable rows were removed,
counter381284, retained XML exact and every original row/membership unchanged.
Final proofSHA `8a0a7f00b79d7a2031b35503aab4f57b2688a9888fcc4499fff1f4f4e9939efc`;
both apps were closed for the completed activation/install.
Conditional planSHA `f2cede464abfdf4e031fb75fb2421a3fa3c6a6e1a4c138276ed3c5470083fd93`
permitted only node_move_accepted/profile. It activated03:16:54.619104UTC,
authority9db4→4076456eeb847f6b99da05e967b162e3b6ff8998d2c34cb08324f5e857344407.
All other authority bytes/values and complete source/canonical data were verified.
The original approved
a82 plan is retained; refinement changed only the frozen fixture-proof digest,
with identical runtime, manifest, permission bytes and actual user approval.
Full preinstall capture passed:6433 records/11,659,528 portable bytes with all
current roots/settings,217 protected files and saved white icon retained. It
was refreshed after the approved authority-only activation using the same complete
payload. Install/reopen/passive proof is recorded in the current identity above.
The sole active previous app is ad05 in `all-playlist-moves-final/previous/Soundbox.app`;
the older ACE rollback was reversibly OS-trashed with exact identity proof.
No app input/termination, automatic restoration or commit occurred.

## Previous November19 organization follow-up — personal173

The previous installed source was
`ad05b04f10d8f27baa29da8499596b640982aa764864884356e722fcf7b2b96f`, executable
`fbdedbcceb86a0cbdea073b9f0f410545af164f990fe5406f7b1b3dd25fdac47`, dirty at
Git base `15ba4552dd31a91c9278a8f7a7f4b587a2081c92`. Library → Select all defaults
to Command-A while Sonny's saved Set First Beat binding remains plain A. Text
editors and Graveyard retain native selection. Right-click My Tag rename keeps
native identity/assignments/rules. Smart playlists drag into populated/empty/
nested ordinary folders or back to root; their source identity/rule is retained.
A separate normalized ordinary-create profile handles native position gaps;
legacy delete/reorder gap guards remain unchanged and are recorded for followup.

Tag failure/retry now saves an exact error-head through sparse ACK while retaining
its staged successors; stale submission callbacks cannot adopt a restored
repository or replacement controller. The actual212-track fixture establishes
its source-curation baseline before asserting no GUI full save. The first bare
gate ran all4890 offscreen cases but seven bare-QMainWindow sidebar fixtures
omitted the new drag handler. Its complete failed evidence/907-input freeze lives
under `tag-rename-smart-move-final/first-gate-sidebar-fixture/`. Only that fixture
callback binding changed, preserving all seven layout/resize/capacity oracles.
Fresh bare `./check.sh --render` passed4958 cases:4890 offscreen/710.80s and68
Cocoa/84.18s, zero skips/xfails,907 exact inputs and inspected render. Strict
package signing,232 first-party/startup bytecode comparisons, four native text
checks and isolated native/process/SQL/AES startup passed.

Six native tag rename/restore references and complete encrypted TEMP replays
prove local synced/unsynced and historical257/usn shapes. Smart move references
prove root→GENRES, GENRES→empty nested folder and historical DUBSTEP→root.
The separately gated constructor/placement/name composition matches both actual
contiguous root and gapped native insertion windows. Each authority stays
default-off for other libraries; no old permission silently gains a capability.

Sonny approved three exact source operations and conditional enablement at
call_cee1c8cc08804884bfbdb65e6f530000. Manifest
`074b55ebe78652278cc21ffd7c2e93af14820be7d6007a066cc2381b95c9ef98` completed once,
counters381227→381232: tag476bd761eaa54912a0ff9acd8fd29dcc temporarily renamed
Zyko698726429; movef829d18d1b964772a639f8229b8b92b2 moved RAP4156294071 into
GENRES2240136408; createaf48ba89bb8740b288af0b592e7e79e9 created empty root
672457817/6b9bc35d-a76b-47c8-96b9-d6e403d2db7f. Full recovery pairs and independent
all-table/XML preservation are retained;6433 canonical records and21855 JSON
files stayed exact. Do not replay this completed pilot.

Native UI acceptance at01:52:02UTC preserves the complete result except seven
known registry-session rows, retained as hashes only. Actual kernel identity is
rekordbox6.8.7.0303 PID70535/start01:46:28.400786UTC. A temporarily retracted visual
hierarchy report was resolved by collapsing the empty folder: RAP remained a
GENRES sibling, as both DB/XML proved. The actual confirmations and resolution
are retained separately in private receipts. Native restoration of DUBSTEP left
an ambiguous order tie; Sonny explicitly moved RAP above BROSTEP and back to the
bottom, then a fresh strict capture proved unique order and all four test items
empty before he deleted only those items and quit normally.

Final closed capture `organization-final-closed` at02:04:18UTC has113 native
playlist rows/counter381265: Zyko's original label restored, DUBSTEP directly
before RIDDIM, RAP last in GENRES, ROOT12/GENRES10, all four temporary items gone.
RAP remains4156294071/0493b9c3-bd23-4295-9cc6-b6872233fe69; never substitute the
archived regular RAP binding. All unrelated tags/links/songs/cues/rules/data
remain exact. XML retains native deletion history. The explicitly authorized RAP
reorders changed only its XML timestamp1795139968620 beyond the original cleanup
clock list. A separately reviewed proof-only v2 adapter binds that exact observed
clock and original approval;2 positive/11 refusal checks passed. Original
helpers/plan/scope remain immutable, authority bytes/capabilities are unchanged,
and no new user approval is claimed. No SQL or automatic restoration belongs to
activation. Final verificationSHA
`3fc1e4fbaa03a7e0b3cd9b8663656fdc8b2d59809bc3bfb51b198fb4009144ad` binds this proof.

Conditional activation completed02:08:22UTC: new TagRename authority SHA
`006a06a31579d8ed87823c9a422506809c271bd1172f200e9f0553197d48a264` and Playlist
SHA`9db4da443ea98b69b12b4e3538e7567dd7537c5d2ea35087140227ee798d4749`, changing only
its four move/normalized-create fields. Derivative planSHA
`d8a7af869560ff54fc614a4e456d5168ceedf06486fe7d1f27f3035763c1ab96` retains original
plan18c170…/scope provenance and exact package/gate/executor bindings.

Fresh preinstall complete state:6433 referenced records/11,659,528 portable bytes,
100 imports,331 titles,491 artists,10 recovery custom playlists,16 analyses and
217 protected authority/art metadata files. Installed02:09:21.626141UTC, then
ordinary LaunchServices reopen02:10:08.357361UTC created installed main
PID71752/start20:10:08local. Passive02:10:25UTC verification proves complete logical
state equality, no changed roots/settings, all217 protected files exact, native
CoreAudio/Metal active,3298 tracks/86 playlists, zero prep tasks,0 artwork fetched/
failed and no startup errors. Saved white icon/root attributes remain preserved.

The sole active previous app is ACE in
`tag-rename-smart-move-final/previous/Soundbox.app`, pending user acceptance. The
older0407 rollback was reversibly OS-trashed with identity proof; no permanent
musical, recovery or app deletion occurred. Release/gate/package/install receipts
are in `corrections/tag-rename-smart-move-final/`; native/activation receipts are
in `corrections/smart-metadata-candidate/`. No commit/push occurred. Historical
chronology below never overrides these current source/install/retention facts.

## Previous November19 name-reuse and write-efficiency delivery — personal173

The previous dirty personal173 artifact was source
`aceea60d582893e2dbc596d5ece9ca79b2cf0401fa4fc1074256217697acef5f`, executable
`606cd596a9e7630c8993b6a8e1252b842d15865f1d8cf45f56c03a6c139851a3`,
at Git base `15ba4552dd31a91c9278a8f7a7f4b587a2081c92`. The earlier0407
artifact below was the earlier rollback; current retention is recorded above.

Deleted names owned only by completed native-migration archives are available
again; active native owners and queued names still reserve their names. Recovery
custom memberships remain intact. Sparse playlist CRUD/membership/smart-edit
admission and ACKs preserve Collection retention, paired migration/removal state,
failed intentions and exact retry custody. A fresh playlist commit returns its
already verified causal edge; historical replay still reconstructs its proof.
Artists publication updates only affected rendered rows. Native cue preparation
avoids repeated DAT/EXT reads while retaining final stale-cache reconciliation;
audio-identity renewal uses the sparse state lane and preserves newer cues.
Import membership readback uses the prepared native-reader process, and smart
membership queries fetch IDs instead of remapping complete content repeatedly.

Every production rekordbox writer was audited: tag definitions/assignments/order,
Hot/Memory cues, beatgrids/BPM, ordinary/smart playlists and membership, title/
artist metadata, registration, conversion locators, both analysis publishers,
removal and their recovery owners. The private
`native-write-efficiency-20261119/engine-writer-audit.md` and dated followup
separate measured costs and fixed paths from remaining full-state checkpoints
in metadata admission, import, conversion, analysis and removal. This delivery
does not claim every write now has identical latency or remove fresh recovery
hashes, independent source readback, media identity or authority guards.

Sonny approved exact append pilot `a427fc5e2aa74e8190fbed1351d02c4f` and conditional
enablement after native verification. It recreated ZYKO under PROJECTS after
SONNY: native ID3867865458/UUIDdb0e6e19-734d-4640-8dae-51c8d4caa999, using the
existing Zyko tag with9 current songs. Counter380758→380761 and one new row/XML
append are exact; every original row/link/table is preserved, with full recovery.
Manifest SHA1f36aa015a73a305784b692f82e8363388dc6ee8577bd178caaa030a4340cf15
and `zyko-append-manifest-source-result.json` bind the operation. Do not recreate
it again or restore the originally deleted3107002670 identity.

Sonny confirmed the result in rekordbox6.8.7.0303, PID56496/start17:05:53. The
23:06:46UTC native capture preserves all non-registry tables, counter, rule,
matching IDs and XML. Seven known native-session rows have hash-only exceptions;
credential values were not serialized. After his normal Quit, the23:09:45UTC
closed capture preserved that entire state with zero further session changes.
The accepted append profile was then activated only after the successful current
gate/package proof: playlist authority SHA
`a52fd976dbb8dabdc6287df0dcf8ec876ccf554fa7f50b27159755390ea38eab`.
Only its two separately marked append fields changed; source SQL was not part
of activation. Existing root/front creation and other authorities remain exact.
Empty-folder creation remains unverified; nonempty local mixed folders now have
the separately accepted append path. Closed and native captures, authority
before/after bytes and activation proof remain in
`smart-metadata-candidate/zyko-append-activation-final/` and `native-reference/`.

The first gate stopped at a missing annotation before discovery. Gate2 was
failed/incomplete: two partitions finished3251 cases, while a fixture's retained
fake busy flag hung cleanup after an old synchronous assertion. Only that
identified isolated worker was stopped after a retained sample; no real app was
terminated. Gate3 completed all4813 offscreen cases and found9 further old
presentation assumptions; Cocoa did not start. Their complete failure evidence
is retained under `name-reuse-final/first-gate-types/`,
`second-gate-fixture-contracts/` and `third-gate-presentation-contracts/`.
Five fixture files now exercise real sparse publication, actual fault injection,
failed RAM-versus-canonical custody, durable proof waits or the newly requested
blank-album changes. Original source/overlay/large-collection/export oracles are
preserved, independently reviewed and covered by focused passing cohorts.

Fresh bare Gate4 `./check.sh --render` passed4882 unique cases:4814 offscreen in
722.63s and68 Cocoa in85.04s, zero skips/expected failures,897 exact frozen inputs
and inspected render. Runtime remainedaceea… throughout those fixture fixes.
The personal package passed strict signing,229 bundled first-party modules,
startup bytecode parity, four native text-section comparisons and isolated
native/process/SQL/AES startup. Source validation and installed identity have
separate receipts; no older binary was restamped as new.

Fresh23:39:38UTC preinstall capture contains6433 referenced records and an
11,642,833-byte portable snapshot:100 imports,331 title overrides,491 artist
overrides,10 recovery custom playlists and16 analyses. All216 protected metadata
files include the newly accepted authority. Preserve this complete current state
and its custom white icon/root attributes.

Installed into `/Applications/Soundbox.app` at23:40:06.989022UTC with all2107 signed
Contents entries exact and the custom icon preserved. Ordinary LaunchServices
reopen at23:40:34.024089UTC created main PID61444/start17:40:34 at the installed
executable. Passive23:41:17UTC verification proves complete logical state equality,
no changed roots/settings and all216 protected metadata files exact. Native
CoreAudio/Metal is active;3298 tracks/85 playlists loaded, preparation finished
with zero tasks, artwork finished0 fetched/0 failed and no startup errors.
Installation/relaunch used no app input/termination or source/device tests.

The sole active previous0407 app is `name-reuse-final/previous/Soundbox.app`,
pending physical acceptance. The older522641… app was reversibly OS-trashed with
identity proof; no permanent deletion or musical/recovery cleanup occurred.
Delivery receipts, scripts, package proof and final gate live under
`corrections/name-reuse-final/`. No commit or push occurred.

The latest actual iPod Sync was16:34 local:44.466s planning and6.523s execution,
3 copies/927 reused,21 pruned files/608.4MB. Its saved930-row catalog contains
874 blank albums and56 real names; all930 rows are in the six selected linked
playlists. The earlier literal Unknown Artist-only projection missed blanks.
iPod-only projection now covers blank/Unknown Album/Unknown Artist placeholders,
including existing linked songs, without changing originals or library metadata.
Negative-only unsupported-WAV observations and fixed planning phase/hash metrics
remove repeated rejection probes and expose the next Sync's remaining costs;
the prior44s cannot be completely attributed from its older log. The device was
ejected during this audit; no new iPod operation or physical album verification
was performed. The next user Sync applies the catalog-only album changes.

## Preceding November19 rapid-tag delivery — personal173

The preceding dirty source added immediate bulk/multi-tag RAM admission, sparse
durable source admission/ACKs, native tag ordering under a separate default-off
profile, stock playlist order, selection-safe intermediate smart-tag ACKs,
source album projection and ordinary Close/Cmd+Q with verified failed-intent
retention. The fresh complete gate, personal package, guarded installation and
ordinary-relaunch passive verification passed.
The4699-case gate below describes only the preceding522641… artifact.

Both SoundBox and rekordbox were closed before guarded installation. Sonny deleted ONLY
the ZYKO smart playlist, native ID3107002670; its tag and songs remain. His native
reference session created root regular `yerb` ID1830832767 and TEMP's Extra1,
Regular2, Pilot3 and Other4. He verified the approved pilots in two native
reopens, restored the complete Genre tag order and deleted TEMP plus its remaining
children. All five TEMP IDs are now absent; `yerb` remains and the deliberate
ZYKO playlist deletion remains. Preserve its tag and songs.
Preserve these current user edits; never replay an older source/AppData snapshot.

Sonny approved exact native tag-order pilot operation
`c5a1fa510afd40deb55f82b850656f52` and conditional enablement after native
verification. The exact approved source operation completed with independent
readback and retained full recovery under `tag-order-source-recovery-c5…` in the
private evidence root. Native verification now confirms Favorites above Zyko,
renamed Pilot above Regular with212 songs, then Pilot absent while Extra,
Regular and Other remained. The independent native delete verification proves
all non-registry source rows, XML and counter exactly. Runtime provenance is
`0407ba1eede9949b45d6a94080a64a33441f82b882a4c1144c664f8cd15ea589`;
the approved operation, SQL, clocks and before/after proofs were preserved.
The final closed cleanup capture is10:25:43UTC. Root's10:28:14UTC
`final-native-cleanup-verification.json` proves all five TEMP IDs absent, the
entire original Genre tag order restored, both deliberate user changes preserved
and the other39 native tables exact. No older DB/AppData snapshot was restored.

The separately approved local tag-deletion pilot created disposable tag3416486697,
assigned only Welcome to Jamrock, then removed that tag and link. Exact readback
preserved all original tables/XML except the expected local counter380750→380754.
Sonny's native verification confirmed all12 original tags in their original order,
the disposable tag absent and the song retaining only Dubstep. The fresh native
capture at10:54:04UTC binds rekordbox6.8.7.0303 PID43348/start04:52:13; after his normal
Quit, a final closed full capture completed at10:55:37UTC.

Gate1 completed every offscreen case with11 confirmed fixture-contract errors;
its logs/plans/reports and891-input snapshot are retained in
`rapid-tags-final/first-gate-fixture-contracts/`. Only the four confirmed fixture
files changed; all58 focused cases pass and runtime0407ba1… is unchanged.
Fresh bare Gate2 `./check.sh --render` passed4836 cases:4768 offscreen in713.01s
and68 Cocoa/native in82.65s, zero skips/expected failures,891 exact frozen inputs
and inspected render. Package verification passed strict signing,228 bundled
Python modules/startup parity, four native text sections and isolated
native/process/SQL/AES startup. The installed package is dirty personal173 at base
`15ba4552dd31a91c9278a8f7a7f4b587a2081c92`, source0407ba1… above, executable
`d2b9b4879174dfc3a5c01197216171a9b225a8f767cc725b70138a62ebbb5d2d`.

After those checks and the separately approved native confirmations, exact-library
profiles were activated: native tag order at10:56:53UTC, intelligent inline rename/
mixed sibling order/local unsynced deletion, then local tag deletion at10:57:38UTC.
The tag-order authority hash begins07347f06…, playlist authority6f059fa0… and local
tag-delete authority9d14aea2…. These enable only their accepted profiles in this
rekordbox6.8.7.0303 library. Older synced tag/assignment deletion and historical
smart-list deletion profiles remain OFF; isolated reference/replay evidence is
not source/native acceptance. Other authorities and source content were preserved.

The fresh10:57:53UTC preinstall baseline contains6433 canonical records and an
11,642,780-byte portable snapshot, with100 imports,331 title overrides,491 artist
overrides,10 custom playlists and16 analyses. Its216 protected metadata files
include the two newly accepted authority files. Preserve this complete current
state and its icon/root attributes.

Installed at10:58:20.872106UTC into `/Applications/Soundbox.app`; all2107 signed
Contents entries exactly match the verified package and the custom white icon
is preserved. Ordinary LaunchServices reopen at10:58:38.612915UTC created fresh
main PID43717/start04:58:38 with the exact installed executable. Passive verification
at10:59:14.588294UTC proved strict complete logical equality with the fresh baseline,
no changed roots/settings and all216 protected metadata files exact. Curated
counts remain100 imports,331 title overrides,491 artist overrides,10 custom playlists
and16 analyses. Native CoreAudio/Metal is active;3298 tracks/85 playlists loaded,
preparation finished with zero tasks, artwork completed with zero fetched/failed
and no startup errors. An earlier short observation had not yet seen preparation
finish; the final positive receipt establishes completion. No older state was
restored and no real-app input or termination was automated.

Private native evidence/proposals live under
`~/Library/Logs/Soundbox/ui-followup-20261118/corrections/smart-metadata-candidate/`.
New delivery receipts/scripts are under `corrections/rapid-tags-final/`:
`validation-completed.json`, `package-verification.json`, `install-completed.json`,
`relaunch.json` and `postlaunch-observation.json`. The immediate previous522641…
app is the sole active rollback at `rapid-tags-final/previous/Soundbox.app`, pending
Sonny's physical acceptance. Its older predecessor was reversibly moved through
macOS Trash with an identity receipt; no permanent deletion occurred. Do not
accumulate another rollback. Physical rapid-tag/bulk/DND and device-order acceptance
remain user workflow checks. No commit/push occurred.

After verification,20 completed native-reference DB copies were retired under
the current bounded-diagnostic retention rule:1,324,974,080bytes freed. Their
32.55MiB of compact tag/category/association rows, existing playlist/counter/XML
and complete-table hashes remain. The guarded manifest and completion receipt
are under `smart-metadata-candidate/reference-retirement-prepared/`; manifest v2
SHA begins804d1998…. All seven direct current proof baselines, domain recovery
pairs, complete canonical baseline and pending-acceptance previous app remain.
No live source, AppData, device or immutable offline fixture was targeted.

## Preceding installed November19 corrections — personal173

The preceding installed app includes the earlier correction set: mirrored
Export/Sync primary actions without a hidden current-row target, full My Tags
popup with exact-tag context deletion, independent iPod/source edit lanes,
unique leaf playlist names, safe owned-name migration and retained unchanged
source proofs after partial stock updates. Playback/external-content observation,
shared decimal-GB storage and stationary progress/Cancel are included too.

Historical preceding identity: dirty base `15ba4552dd31a91c9278a8f7a7f4b587a2081c92`,
source `5226411779e33731f3bdd2c88ecb4e2683326a27f705038acf610d16dffb1c08`,
executable `46d077b875e69fdce7754ce64cd908c742d172084a4a640b8c3efec9182ec415`.
The unchanged build number reflects the same uncommitted history base; the new
source fingerprint and signed payload distinguish this correction from the
previous personal173. No commit/push occurred.

The fresh bare render gate passed 4699 unique cases
(4631 offscreen/68 Cocoa),
zero skips/expected failures, with 875 exact frozen inputs.
Render inspection, strict package signing, 220 bundled
Python modules/startup parity, four native text sections and isolated native,
process-query, SQL/AES startup passed separately.

Installed 2026-11-19T08:05:32.171945+00:00; ordinary reopen 2026-11-19T08:05:42.469645+00:00.
Passive verification 2026-11-19T08:06:09.074586+00:00 observed native CoreAudio/Metal active,
3298 raw tracks/85 playlists,
zero preparation tasks and zero artwork fetched/failed. The fresh complete
6433-record baseline remains logically exact after launch;
all 214 protected metadata hashes match.
Preserved counts: 100 imports, 331 title overrides,
491 artist overrides, 10 custom playlists,
16 analyses. No older state was restored.

Historical preceding handoff: Sonny's install-when-ready authorization covered it.
He quit normally; no real-app input or termination was automated. The exact old
personal172 bundle was reversibly moved with macOS's Trash API (identity retained,
no permanent deletion); the immediate prior personal173 was the sole active
rollback under `ui-followup-20261118/corrections/previous/` at that time; it has
since been rotated as recorded above. My Tags physical acceptance, later
metadata/export coexistence and Rockbox/dual firmware acceptance remain user
workflow checks; do not automate a live Sync or source/device experiment.

Private source/gate/package/state/install receipts:
`~/Library/Logs/Soundbox/ui-followup-20261118/corrections/`.
The first failed later-corrections gate is retained separately and is not a pass.
See [the scoped follow-up](../ui-followup-2026-11.md) for behavior and monitored Sync
findings. Historical installed evidence below describes the preceding artifact.

## Earlier installed follow-up — November18, first personal173

The earlier personal173 was installed and ordinarily reopened: dirty base
`15ba4552dd31a91c9278a8f7a7f4b587a2081c92`, source
`c1a147cfb55827128c1237e1248fdda6d58f4b9cce30fe30bde83999f1e66960`, executable
`422c8d69a84c0a6fc84e0217b817bd0c2b0d6cc929fe70b4d8f2e67cb0389057`.
The complete bare render gate passed4664 cases (4596 offscreen,68 Cocoa), zero
skips/expected failures;868 frozen inputs and the render are verified. Strict
package signature,217 bundled Python modules/startup parity, four native text
sections and isolated native/SQL/AES smoke checks passed. Installed signed
Contents and the saved white Finder/Dock icon match the package.

Installation05:29:19UTC and ordinary reopen05:29:49UTC were authorized by
Sonny's current install-when-ready instruction. Passive verification05:30:27UTC
observed native CoreAudio/Metal active,3298 raw tracks/85 playlists,0 preparation
tasks,0 artwork fetched/failed and exact complete saved state/214 protected
metadata files. Counts remain100 imports,331 title overrides,491 artist overrides,
10 custom playlists,16 analyses. A fresh6433-record complete capture preserved
Sonny's newer columns_state/device_views rather than restoring the earlier copy.

Historical first-correction status (superseded by the current delivery above):
Sonny's subsequent corrections were implemented in source pending a fresh full
gate/package/install. Both primary actions follow Export/Sync pending state,
without an implicit focused-row update. Delete Tag belongs to the exact tag's
context menu. Native tag/cue/grid, metadata and playlist saves progress alongside iPod device IO under
the existing shared source writer. Device playlist names use the leaf alone;
new normalized duplicate names are rejected across native/custom/queued claims.
Partial stock updates preserve unchanged exact source proofs rather than losing
the unselected inventory. The installed artifact above does not include these
later corrections. No automated real-app input or termination is authorized.

Sonny explicitly delegated passive monitoring of the latest Sync. Its plan read
814 sources/817 attempts (23.533GB) in79.25s, while device execution took3.824s:
2 new tracks,854 reused,1 metadata update and2 unused file removals. Two cloud
sources consumed49.6s in retries. The43 sources retained by a preceding partial
Sync were exactly the43 not reacquired;813 other accepted sources and one omitted
cloud set were re-read. This evidence identifies incomplete source-proof retention,
not excessive full-device copying. The fix keeps old proof epochs/digests verbatim
only after exact shadow/native matching and rechecks changed selected sources.
Private compact monitoring receipts are under`latest-sync-monitor/` in the base
below. No live source/device file reads or app input were used for monitoring.

At that earlier handoff, personal172 was the sole rollback. Current rotation and
retention are recorded at the top of this file; do not infer another rollback. No commit/push occurred for this follow-up. My Tags
installed acceptance and broader metadata/external-exporter acceptance remain open.
See [the scoped review](../ui-followup-2026-11.md) for source proof and limitations.

## Previous delivery — personal172 (historical)

`/Applications/Soundbox.app`: personal172, signed dirty base
`d6b504e07a358483a0f56880e15a412c5e414803`, source
`30bb10761455a1a3822b78e76e7817ddfe654bde4ab341cc85a52f43d547f2b1`, executable
`0dc6e5c832574b95686a55b4cf2928f7ea7f53b97f02b0c6286e6c332b4a627e`.
Installed2026-11-19T03:12:01UTC; approved ordinary reopen03:12:42UTC.
Sonny quit normally and approved installation/reopen/library verification
(`call_f61d6a72b8ea4c1ea4f70156aa83b374`). Strict package verification, installed
signed Contents, deep signature and the saved white Finder/Dock icon pass.
No automatic real-app input or termination was used. Startup main47623/cache47626/
native-read47627PIDs are historical only; read fresh identity before any control.
Never restamp this artifact as a later repository commit.

Fresh preinstall state:11,641,499portable bytes and6433referenced records.
Postlaunch03:13:13UTC: complete logical state EXACT; no changed roots/settings;
all214protected authority/artwork metadata hashes match. Counts:100imports,
331title overrides,491artist overrides,10custom playlists,16analyses.
Native CoreAudio/Metal is active;3298raw tracks/85playlists,0preparation tasks,
0art fetched/failed. Metadata migration3121/3121is complete and unpaused, with no
pending native jobs. No Pause/Resume is needed for the completed backlog.
One absent bound import and two unresolved locations are preexisting state;
never restore them or intentionally deleted iPod playlists from an old checkpoint.
Accepted player behavior remains protected:18source files and20app player methods
are exact, and the signed package matches tested Python/startup/native code.

## Deferred UX requests — source follow-up in progress

Sonny accepted the current speeds at the previous wrap and has now reopened
these presentation fixes. Installation/runtime handoff authorization persists;
he reopened personal172, so verify its normal Quit before replacing it. The earlier delegated export follow-ups
finished with no pending edits; the current independent UI review is separate.

1. Footer source now anchors progress and Cancel across Planning, Copying,
   Verifying and Finalizing before both variable labels. Regression coverage
   includes hidden/large counts, long text and narrow footers. iPod and generic
   USB share the change; measured progress, truthful finalization, cancellation
   and quiet successful outcomes retain their existing owners.
2. Superseded by Sonny's November19 correction: the lower-left primary and
   context action now mirror the header's Export/Sync state. There is no separate
   update action or implicit focused-row target.
3. Sonny plans to tag songs and test a larger metadata Sync. The one-added-song
   reconnect/Sync passed; metadata-heavy physical acceptance remains unobserved.
4. My Tags source rechecks its content-derived bounds on opening. Cocoa surface
   regressions cover hidden startup and reopened catalogs. The supplied clipping
   sequence was not conclusively reproduced in ordinary isolated Cocoa; installed
   acceptance remains pending.

Carry the existing critical audit forward rather than treating this wrap as fixing
every queued finding. The latest throughput reviews retain the preflight hash:
its early batch refusal cannot safely be removed into the known later rollback
ownership gap for an unmeasured saving. Timestamp preservation is a possible later
measurement, not a shipped speedup. Keep accepted player loading/cues untouched.

## Physical workflow acceptance — current iTunes path accepted

Sonny reconnected SONNY and requested real Export/Delete/Sync testing, including
ordinary device playlist/media deletion. Fresh OS observation03:14:47UTC verified
the same intended external writable FAT32 volume. Exact UUID/BSD/mount evidence
is private in`connected-ipod-workflow/reconnected-target.json` under the base below.
Remembered model/UUID facts grant no app write permission; current observed format,
mount, writability and signing guards remain required.

Sonny intentionally deleted device playlists himself, then completed a large
export. Root made ZERO live mutating AX calls or concurrent device/audio reads.
The last stock plan copied587new files:15,560,911,583bytes written and the same
bytes verified;533unique covers linked to587tracks. Its21:15:23–21:27:53local
commit interval was750s, about20.75MB/s including setup/artwork. Local source
capture took14.375s with no retry. Passive OS samples showed19–22MiB/s disk IO;
this suggests device IO dominates, not an independently measured hardware limit.
Stock-only export has no fixed per-song sleep. Timestamp preservation may wait
for queued writes; its sampled residence is not proof of avoidable overhead.

Completion was nonmodal. Sonny explicitly confirmed Collection AND his selected
song stayed intact; bounded AX reads separately retained TrackTable and the
footer returned to Ready. His ordinary Eject finished21:28:43local in1.106s.
SONNY was then absent from macOS. Sonny reconnected it and personally tested Sync.
Fresh03:32:19UTC OS facts verified the same UUID/writable FAT32disk4s1. He added
one song to Favorites, confirmed reconnect offered Sync, completed it, and saw
the header return to Export. His screenshot shows Favorites157, GENRES/DUBSTEP519,
LIVE SETS1 and Videos0. He reports everything feels snappy and is happy with the
speeds. These are his actual UI observations, not automated firmware inspection.
Preserve his current exports and intentional deletions. Compact metrics/code review are private under
`connected-ipod-workflow/`; no new application behavior was changed for profiling.

Sonny explicitly deferred the small gripes above to the next session and started
another export. No additional test run, app input, quit/relaunch or device write
is authorized by this documentation wrap. Broader firmware, Rockbox/dual and
large metadata Sync acceptance remain separate; no need to repeat his accepted
stock path merely to close this wrap.
Do not infer whole-collection erase, raw database replay, restoration, live fault
injection, source music changes or general control of other apps. No firmware
playback or physical throughput claim is established by generated tests.
The private AX helper was reviewed and policy-tested; every action still needs
fresh installed PID/launch/window/element and exact target guards. The separate
right-click event fallback has not been approved by root and is unnecessary.

## Delivered behavior

- Metadata/canonical work stays off the interaction path and quiet in the footer;
  native acknowledgements preserve current curation, navigation and loaded cues.
- iPod exports capture durable selected effective metadata independently of a
  paused/running bulk metadata queue. Actual selected source transitions settle;
  dormant CDJ policy does not choose iPod eligibility. Generic USB stays separate.
- Export/Sync/Apply Order proceeds from the original gesture. Successful Cancel,
  success, intentional format omissions and optional association notices are
  nonmodal. Delete asks one short question before planning. Real failures retain
  concise cause/outcome/recovery details; an export batch shows them once.
- Separate exports preserve exact surviving playlist associations, rendered
  folder labels and complete source occurrence baselines. Intentional omissions
  do not create perpetual pending additions. Explicit Sync reevaluates eligibility;
  definite unsupported WAVs use bounded reject-only headers, never admission proof.
- Managed iTunes metadata status compares seven effective fields on the worker.
  Cold witness and changed small DB/art catalogs converge through one owned native
  view read. Unchanged views update scalar status only. Clean headers offer Export;
  observed pending linked changes offer Sync from both primary buttons.
- Completion refreshes the target's cached native view even offpage, without
  navigation. Delete renews proven surviving associations/fast baselines after
  readback, or retires only its exact unprovable old cache generation.
- Stock progress uses checked entries, actual within-file copy/readback bytes,
  artwork/database/cleanup work, and joined final readback. Only completion owns100%.
  New unindexed FAT32 media uses exclusive publication and one actual descriptor
  readback before acknowledgement/iTunesDB-last. Existing rewrites and generic
  USB keep separate contracts; this does not fix transaction-wide later rollback.

## Validation and private evidence

Base:`~/Library/Logs/Soundbox/ipod-export-checkpoint-gate-20261118/`.
Current gate/package:`connected-workflow-final/`; installation/state evidence:
`connected-workflow-final/runtime-handoff/`.
Bare`./check.sh --render` passed4648unique cases:4581offscreen674.83s plus67Cocoa
82.29s, zero skips/expected failures and all912non-document inputs unchanged.
Both fresh renders were inspected. Strict signature/full Python/startup/native
TEXT parity, generated3298-row/native SQL/AES helpers and isolated native startup
pass:Ready,0devices/no library/art worker. Actual installed startup and complete
canonical/protected-state comparison pass separately.

The first e8f candidate's full offscreen run failed two cases after4578tests:
a header fixture still expected Sync without an observed pending change, and an
unstarted wait gained unnecessary unknown-recovery text. Its complete evidence
remains at`connected-workflow-header-wait-refusal/`; no render/Cocoa ran there.
Precise strengthened header tests and a proven-pre-admission exception corrected
those paths; generic backend recovery remains explicit and deferred Cancel quiet.
The clean full run above followed. Earlier9de/14576/c9d1proofs remain in their dated
private areas; failed or phase-recovered commands are never relabeled successful.

`post-export-9de-audit/` contains independent content/oracle reviews, real generated
rollback-fault controls and final Native294/UI129/Root58focused cohorts. The final
header23/wait26corrections have separate exact receipts. `wrap-up/` reconciles all
245intended non-document files, including the inherited review and final deltas.
The earlier full142-entry documentation audit remains its basis; the final
delivery delta supplement and independent prose review are in `wrap-up/`.

## Wrap, publication and retention

Sonny requested the full wrap: validated delivery, actual workflow verification,
repository-wide documentation review, permitted cleanup, commit/push and clean
published worktree. No new publication approval is needed. He confirmed the public
`Low-Freq-Labs/soundbox` destination; current origin is that same repository,
immutable ID1312418737, and existing GitHub login has push permission. No new key
or credential export is needed. This wrap publishes the validated intended changes
on `main`; read the final repository commit with `git log -1` and compare
`origin/main`. The private `wrap-up/publication-completed.json` records the exact
final local/remote commit. Installed source/executable and its dirty d6b504e base
above remain unchanged; publication does not rebuild or restamp the running app.

After Sonny's acceptance, guarded cleanup retired1,612,310,155distinct regular-file
bytes from `dist/personal/` (both generated distributions), `build/personal/`,
`build/cdj/debug/` and the sole old f502/7178 previous-app bundle. No app input,
termination or source/device write occurred. Its reviewed manifests/receipt are
`wrap-up/connected-wrap-cleanup-{prepared,completed}.json`. There is no retained
application fallback or duplicate candidate from this delivery. Current installed
app, AppData, source originals/conversions, native/device recovery, private fixtures,
`.venv`, `build/native`, `build/check` and CDJ release outputs remain protected.
Physical space gain was not measured. Earlier2.6GB,1,242,462,326-byte and
1,242,524,360-byte cleanups retain their separate private receipts.

Current new-copy iTunes support remains MP3 or PCM16/44.1kHz stereo WAV, narrower
than Video/Classic AAC/ALAC/AIFF hardware capability. Codec-aware native fields,
classification versioning and retained dual promotion are a separate feature;
the new read-only plan is`post-export-9de-audit/later-codec-plan/`. Do not claim
all M4A hardware unsupported or complete this feature by widening suffixes.
Queued acknowledged-copy rollback/reference ownership, CDJ recovery races, USB
view state, player and artwork findings remain in[the critical audit](../critical-audit-2026-11.md).
Two new generated ownership controls extend existing P1s; they are not extra bugs
or shipped fixes. Broader native metadata/analysis/deletion domains remain in
[the shared-library audit](../shared-library-audit-2026-11.md).
