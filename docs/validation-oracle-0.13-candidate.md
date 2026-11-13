# Oracle 0.13 candidate: qualification and remaining gates

This is an **unpublished development candidate**. The public release remains
v0.12.0 at `b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`. The complete independent
Navicat 18 UI and database-tool goal stays active; this slice does not close
Oracle parity or the six-target release gate.

The retained UI baseline is the actual macOS **18.0.5 build 321**
[OR18-01 observation](ui-reference-18-oracle.md). See also the
[connection-form scope](validation-oracle-connection-ui.md) and the
[complete parity matrix](parity-matrix.md).

## Current checkpoint: 2026-11-14

The current integrated application source is
`211fef0ec0c7edbca2c016d0fd887e4acd977c90`, pushed to main. It includes the
async-delete, IPv6-scope and test-host fixes below, plus the independent
[Navicat18 icon work](validation-icons-navicat18.md). Earlier checkpoint
results and failures remain historical; their pending root/local gates are
superseded only by the separately measured results here.

- Frozen default source: **2,633 passed / 662 conditional skips / zero failures
  or errors**, 139.274s; exact 3,295-case collection/execution/JUnit multiset and
  240 runtime/test/build/workflow inputs equal before/after and Git211. This
  passes the new aggregate without proving the old GridFS timeout's cause.
- Fresh owned native ARM64 Oracle, actual **Cocoa** Window: **3 passed / zero
  skips**, real Test/save-reopen/Open/catalog/typed table-view data and both
  CPU-query Stop/reuse/Disconnect-or-Shutdown cases. The runner's 24 declared
  inputs match Git211 before/after. Independent 106 structure/resource checks,
  required/JUnit Counter, exact container/PID/PGID absence, zero volumes and app
  sessions pass. All 28 phase PNGs / 16 unique images were reviewed; no privacy
  issue or missing icon, with the connection diagram's low contrast still open.
- New macOS ARM64 app build and isolated packaged smoke exit0. SQLite 6×4 and
  all four Oracle26.1 native/thin flags pass; complete 79 own modules match
  source, 61 Mach-O images are ARM64 and 95 symlinks are valid. The frozen smoke
  process does not self-report its QPA: Cocoa was the single explicit request.
  This is a local bundle, not six installers or Oracle endpoint execution.
- A separate 240-input source bracket remains identical from default-suite end,
  through the generated demonstration and actual Oracle Cocoa run, to Git211.
  This does not silently expand the Oracle runner's own 24-input declaration.
- New main CI [43473238808](https://github.com/shubolab/opennavicat/actions/runs/43473238808)
  completed with nine required jobs successful and publisher skipped. All15
  artifacts and six actual installer bytes were independently audited, including
  separate native Linux backend14+Window3 Oracle scopes on each architecture.
  This is source211 preflight; the exact formal tag/public assets still require
  new qualification. See [0.13 delivery validation](validation-0.13.md).

Local receipts live under `artifacts/icon-parity-18/{default-source-211,
native-211,oracle-cocoa-211,oracle-cocoa-211-independent}/`; raw screenshots and
reference assets remain outside version control. The current resume index is
[2026-11-14 snapshot](snapshot/2026-11-14-goal-resume.md).

## Executable slice and safety boundaries

The dedicated `OracleSession` uses pinned **python-oracledb 26.1.0**, in its
native Thin mode. The executable connection path is Basic, service name,
Default role, password authentication and explicit plain TCP. TNS, SID,
SYSDBA/SYSOPER, OS authentication, TLS, SSH and active advanced settings are
retained drafts that fail support validation before prompting or network I/O.
The adapter does not load Instant Client, force a process-wide Thin/Thick
configuration, read an existing wallet/TNS configuration or reinterpret the
service identifier as a selected schema.

The five-page form follows the observed hierarchy and preserves settings.
Save does not open a connection. With Save Password off, Test and each Open
request a temporary password; it is not written back to the form, saved
profile or Store password cache. Existing OS-keyring credential storage remains
the only persistent credential path.

Owner-qualified catalog and bounded table/view reads are connected to the
window's serial worker queue. Table and View lists have the observed 28 and
13 columns; unavailable metadata is explicit. Oracle data pages request columns
and a bounded fetch, with unknown total row count. Query navigation and the
remaining tool surfaces stay visible but unavailable. Selecting an owner does
not change server `CURRENT_SCHEMA`, so it cannot yet establish an arbitrary
query tab's unqualified-name semantics.

Oracle NUMBER delivery uses the driver's per-statement `fetch_decimals=True`
API. The actual-server scope checks 38 digits, fractions, negative scale and
changed decimal separators; it is not acceptance of every Oracle type.
LOB/LONG and unsupported object/JSON/vector/cursor delivery fail closed.
Delivery limits do not claim to bound server work or all native-driver memory.
Filters, writes, imports, DDL, transactions, designers and comparison application
remain unavailable. SELECT support is not a server-enforced read-only role.

Cancellation captures an exact operation marker and a per-job Event. Network
break runs on a separate worker, outside the marker lock. Owner retirement
waits for accepted breaks before a new operation can use the session; a stale
marker cannot cancel a later operation. Close runs on the serial owner.
Driver exceptions expose bounded safe error codes, not raw credential-bearing
messages.

## Separately measured scopes

| Scope | Bound revision | Result and limit |
| --- | --- | --- |
| Backend unit/runner checkpoint | `b8f3f8a8415c054481c37cd9c3684e589b445405` | 93 passed, zero skips. Nine declared source hashes match Git blobs before/after. |
| Owned native ARM64 Oracle backend | same `b8f3f8a` | 14 passed, zero skips, 3.47 s. Catalog, columns, table/view delivery, NUMBER, access denial, safe errors, bounds, exact live cancellation and reuse. No Qt window acceptance. |
| Dedicated form and related source scope | `45e57e3e5a129e72c03f748e08ce0b1f3490ab92` | 246 passed, zero skips, 4.44 s; 27 form cases and 18 actual MainWindow cases with strict fake Oracle sessions, plus related regressions. |
| Full default source suite | same `45e57e3` | 2,382 passed, 659 conditional skips, zero failures/errors, 89.59 s; 217 declared inputs unchanged and matched to Git. Conditional skips are not real database execution. |
| Later complete default source attempt | `6af3109664ef291e8de608d9d2e2bf0814aae3f5` | 2,470 passed, 659 conditional skips, **1 failure**, zero errors, 113.17 s; exact 3,130-case multiset and 223 runtime/test/build inputs unchanged and matched to Git. A GridFS preview-layout case timed out waiting for ready after toggling preview. The cause is under controlled investigation; this is not a passing aggregate candidate qualification. WIP workflows/docs and the unmerged real-Window runner are outside this measured input scope. |
| Native Oracle probe and installer/publisher contracts | `1cb6bc12159a1b1d4c3989f50491183cbafd5d1c` | 192 passed, zero skips, independently repeated. Exact 26.1.0 metadata, actual native extension imports and public parameter roundtrip without a network connection. |
| Local packaged macOS ARM64 smoke | same `1cb6bc1` | Actual app exits 0; asynchronous isolated SQLite 6 rows × 4 columns, existing driver/crypto/JS probes and all four new Oracle flags pass. Offscreen; no Oracle endpoint. |
| Local native app static audit | app built at `1cb6bc1`, unchanged app-source bytes rebound to `65abe89278462ee62ec31db07207a298c9d44b66` | 77 own modules and entry code match source; main executable and four Oracle extensions are ARM64; extension machine code and driver metadata/licenses match the verified official wheel. No installer or six-target claim. |
| Actual Cocoa Qt source cases | `65abe89278462ee62ec31db07207a298c9d44b66` | 45 passed, zero skips/errors/failures; exact collected/JUnit multiset, 224 tracked inputs unchanged and matched to Git. Real Cocoa widgets, strict fake database sessions. |
| Packaged native CUA form check | app built at `1cb6bc1` | Fresh workspace, all five tabs, Save Password state, save/Edit reopen, separate Test/Open password prompts, both cancelled, normal Quit and exact launched PID absent. No database connection or query acceptance. |

The backend uses a fresh, immutable **gvenzl Oracle Free 26ai** image on its
native architecture. This is a public third-party test fixture, not an official
Oracle server image redistributed with the app. The final backend observer binds
the executing SQL to its SQL_ID/child, SID and generated username before issuing
Stop. The interrupted CPU query returns promptly, the same session reads again,
and old-marker/cold-start gaps are tested. Eleven backend-owned historical/final
container IDs were independently absent after cleanup, with zero data volumes
and zero remaining application sessions in the final fixture.

Earlier server-side `DBMS_SESSION.SLEEP(10)` SELECT attempts did **not** stop
promptly; the direct official driver also reproduced that limit. Successful CPU
query cancellation does not fix or close this SLEEP gate. A directory named
`nls-red` actually contains passing cases: it is not evidence of an old NUMBER
precision failure. The canonical Decimal API change was qualified separately.

The first complete UI source attempt retained two failures in tests using
Oracle as a historical unsupported-driver sentinel. They now use the still
unsupported Snowflake driver; the nine-family and imported-profile review
protections remain tested. No import review gate was removed.

## Native database CI

The required Oracle job matrix now selects immutable native Linux amd64 and
arm64 child/config image digests, verifies engine/image/container architecture
and rejects emulation. The runner records the exact collection manifest and
requires a nonempty, duplicate-free, exact JUnit multiset with no skips. Its
intent ledger and interrupted cleanup identify only the newly owned fixture;
cleanup does not start a server or connect to a database.

Three diagnostic runs remain failures:

- `43411174075` and `43412653289` failed during test collection before Docker or
  database allocation. Their missing collection stderr prevents a precise root
  cause claim.
- `43413750180` retained the bounded collection trace: pytest-qt could not import
  QtGui because `libEGL.so.1` was missing, on both native runners.

The fix reuses the existing Linux database-security dependency profile, sets
offscreen Qt and writes an explicit required-case JSON manifest. It does not
disable pytest-qt or reduce the database case list.

New run [43415040482](https://github.com/shubolab/opennavicat/actions/runs/43415040482)
at `64226493cdf7feb87b7a6b53482b93003c865e79` completed successfully. Independent
artifact audit verified **14 passed, zero skips on each architecture**, exact
required-case/JUnit multisets and 13 declared source paths against that Git
revision. The two artifact ZIPs' actual sizes and SHA256 match GitHub's API
digests and were independently rehashed again by the root agent. The native
Docker/image/uname evidence, pinned child/config identities, actual driver and
server versions, generated users, CPU observer binding and session reuse cases
were checked. Each job's normal finalizer and subsequent cleanup-only step
reported the same exact container ID absent, with zero application sessions.
The local Docker daemon cannot independently inspect those disposed CI hosts.

The same 14-case backend scope was repeated on two architectures; this is not
28 distinct accepted functions. The audit receipt is
`oracle-ci-native/opennavicat/artifacts/oracle-ci-native/ci-43415040482-independent-audit.json`.
It is backend qualification, not actual Oracle Window, six desktop installers
or a public release.

## Open acceptance gates

The later default-suite GridFS failure is retained at
`artifacts/oracle-integration/default-source-6af/`. Its 15-second wait must be
diagnosed; a wider timeout or an unexamined rerun does not close the gate.
The investigation did not reproduce that timeout in 200 isolated attempts,
the original module or the original 938-case prefix. It independently proved
that the fake serial host retained completed Task/TaskSignals objects instead
of using the production Window's GUI parent and deferred deletion. Test-only
fix `859a6980cd631b7030045a83cf034a17f7846dba` mirrors those two production lines
and adds image/non-image preview regressions with a real runnable tail and
worker garbage collection. All three native signal objects are destroyed on
the GUI thread and weak references retire. The module passed 24 cases with
four database opt-in skips; the unchanged original prefix passed 651 with 287
skips. Its 223 declared inputs match Git. Product GridFS code is unchanged;
the original timeout's causal link to the host defect remains unproved. A new
aggregate run at Git211 now passes as recorded above; the original timeout cause remains unproved.

Separate backend fix `f63d16dd3e1b3388d6ac90edc5540695f977b3e6` restricts the
IPv6 scope identifier to ordinary ASCII interface/index characters without
adding a length cap. `ipaddress` had accepted descriptor delimiters inside
scope text, and a pure public `ConnectParams` descriptor roundtrip changed its
host value. No actual endpoint change or network exploit was demonstrated.
The same new cases gave 14 failures / 17 passes against old source; the fixed
Oracle unit module passed 111 cases without skips. Valid IPv4, DNS, IPv6 and
zone values retain exact public parameter roundtrips; malformed values fail
before the driver connection call. Five inputs and an independent review bind
that source. Root Cocoa, app-bundle and CI qualifications must use the new
backend source rather than inheriting the earlier backend hash.

Independent source review reproduced a shared Window defect: removing a
connection treats an asynchronously queued disconnect as a completed close,
so a later close failure can leave a live session after its profile was
deleted. The narrow fix `5de453e873f75b39ad2bde94d8b25074a64b709e` now commits
deletion only after successful close. It preserves the profile and credential
flags on failure and rejects callbacks whose configuration, Store state,
remembered-credential choice or session binding has changed. The same final
12-case module produced **9 failures / 3 passes against old source**, then
**30 passes / zero skips** with the fix and the existing 18 Oracle-window cases.
No actual database or real keychain is used in these regressions.

After merging the fix at `3351083cf7cc4fe71f8f7273e1b8f22e8a199662`, root ran
the 27 form, 18 window and 12 delete-lifecycle cases using actual **Cocoa**:
**57 passed, zero skips/failures/errors**, exact collection/JUnit multiset and
83 tracked runtime/test/dependency paths unchanged and matched to Git. The
receipt is `artifacts/oracle-integration/native-cocoa-tests-post-close-fix/verification.json`.
These cases use strict fake driver sessions. The earlier app/CUA receipts remain
bound to their earlier source. Root then rebuilt the actual ARM64 app at
`3351083` and ran its isolated asynchronous 6×4 SQLite smoke with Cocoa selected,
all driver/crypto/JS and four Oracle native probe flags passing. Its sample
image was visually reviewed. A separate static audit again passed all 21
checks: 77 own modules including the changed Window match source, 61 Mach-O
images support ARM64 and four Oracle extensions match the official wheel's
ARM64 machine code and metadata/license payloads. These are local app-bundle
checks; at that checkpoint actual Oracle Window, aggregate source and installer gates remained open. Current local/Window results are recorded above; installer gates remain separate.
The new receipts are
`artifacts/oracle-integration/local-native-post-close-fix/packaged-cocoa-verification.json`
and `artifacts/oracle-packaging-review/native-app-post-close-fix/verification.json`.

Real Window checkpoint `9d488349988dc1d6b6c13d6e1ae5994857cc766a` adds the owned
Dialog Test/save/Edit/Open/schema/28-column table/13-column view/typed-data path
and two live compute-view Stop/reuse/Disconnect-or-Shutdown cases. Its final
offscreen scope passed three cases with zero skips; 24 inputs match Git and
six historical exact container IDs were independently absent. Its independent
review includes server SQL_ID/child/SID/SERIAL#/username ownership, serial and
control threads, unchanged rows, zero remaining app sessions, native image,
secret scans and process/FD cleanup. This checkpoint predates root's close
fix and IPv6 fix; fresh root Cocoa acceptance is separately measured at Git211 above.

The runner's 14 offline contracts include real owned fork cases where the
leader exits before a descendant that holds, or closes, inherited pipes. It
uses bounded nonblocking reads, preserves the unreaped leader identity through
group cleanup and requires actual ESRCH in post-reap read-only checks. XNU's
EPERM for groups without non-zombie members is recorded as pending, never as
absence. The old harness failures and historical UI attempts remain retained.
One final qualification attempt failed while seeding a materialized view,
before pytest/cases; its DatabaseError code was not captured, so the cause
remains unknown. The diagnostic follow-up passed without changing the shared
fixture or dropping that object. Root source/GUI/packaging qualification is not
inherited from that successful follow-up.

The remaining Basic/TNS/SID/roles/OS/Thick/TLS/SSH/advanced settings, query
semantics, transactions, all types, writes and conflict handling, designers,
materialized-view tools, 15 Other categories, Data Pump, users/grants and every
other applicable Oracle tool stay open. Windows/Linux reference observations
and all three platforms' complete UI/tool/human acceptance remain open.

No new tag, public release or six Oracle-capable installers is claimed here.
The Git211 demonstration is recorded separately and must pass every-phase privacy
review before replacing the single README GIF. A usable release must independently
qualify all six native installers, exact checksums and public bytes.
