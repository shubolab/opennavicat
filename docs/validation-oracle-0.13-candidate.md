# Oracle 0.13 candidate: qualification and remaining gates

This is an **unpublished development candidate**. The public release remains
v0.12.0 at `b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`. The complete independent
Navicat 18 UI and database-tool goal stays active; this slice does not close
Oracle parity or the six-target release gate.

The retained UI baseline is the actual macOS **18.0.5 build 321**
[OR18-01 observation](ui-reference-18-oracle.md). See also the
[connection-form scope](validation-oracle-connection-ui.md) and the
[complete parity matrix](parity-matrix.md).

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
at `64226493cdf7feb87b7a6b53482b93003c865e79` reports both native jobs successful.
Independent artifact/source/JUnit/image/cleanup audit is tracked separately;
job status alone is not its completion. This is backend qualification, not
actual Oracle Window, six desktop installers or a public release.

## Open acceptance gates

Independent source review reproduced a shared Window defect: removing a
connection treats an asynchronously queued disconnect as a completed close,
so a later close failure can leave a live session after its profile was
deleted. A separate, narrow fix is in progress. This candidate must not reach
main/release qualification until deletion commits only after successful close,
failures retain the profile, and stale callbacks cannot delete a replacement
profile. The earlier app/Cocoa receipts remain bound to their stated sources.

Actual GUI-to-Oracle Test/save/reopen/Open/schema/table/view/Stop acceptance is
being added with an owned generated fixture; the earlier fake-session GUI and
driver-only scopes cannot substitute for it. Actual Cocoa database acceptance
remains separate from offscreen execution.

The remaining Basic/TNS/SID/roles/OS/Thick/TLS/SSH/advanced settings, query
semantics, transactions, all types, writes and conflict handling, designers,
materialized-view tools, 15 Other categories, Data Pump, users/grants and every
other applicable Oracle tool stay open. Windows/Linux reference observations
and all three platforms' complete UI/tool/human acceptance remain open.

No new tag, public release, six Oracle-capable installers or refreshed demo GIF
is claimed here. A usable release must independently qualify all six native
installers, exact checksums and public bytes and refresh the single actual-app
GIF when its demonstration changes.
