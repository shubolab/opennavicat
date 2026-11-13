# Oracle connection form and schema browser candidate

This candidate follows the actual macOS Navicat **18.0.5 build 321**
[OR18-01 observation](ui-reference-18-oracle.md). It adds the dedicated Oracle
connection form and an owner-qualified table/view reading path. It does not
close Oracle UI or tool parity, native platform acceptance or a release gate.
The measurements below belong to checkpoint `45e57e3`; later integration,
packaged native probes and separate Cocoa state checks are recorded in the
[unpublished 0.13 candidate ledger](validation-oracle-0.13-candidate.md).

## Connection form and retained drafts

The form has the observed profile pane and five pages in order: **常规 / 高级 /
数据库 / SSH / 备注**. It retains Basic/TNS, service/SID, Default/SYSDBA/SYSOPER,
OS authentication, the 247 named character-set choices plus Automatic,
session/keepalive settings, Initial Query, ordered custom-database entries,
the three SSH method forms and Notes. It does not inherit another driver's
SSL or HTTP page. Multiple connection profiles, URI editing and custom-list
retrieval remain unavailable controls.

Only **Basic / service / Default / password / TCP** is executable in this
slice. Other authentication, TLS, SSH and active advanced settings remain
saveable drafts and fail support validation before Test or Open can prompt for
credentials or start database I/O. Inactive settings retain their values;
unsupported enums
retain their original data with a bounded visible label. The service has its
own field; `ConnectionConfig.database` remains empty and is not used as a
schema alias.

New presets preserve the new-form Back and Save Password state. Back returns
to the selector. Saving Oracle settings does not implicitly open a database
connection. When Save Password is off, Test and each subsequent Open obtain a
temporary password on the GUI thread. That password belongs only to the
worker's session configuration: it is not copied back to the form, saved
profiles or the Store's reusable password cache. Remembered credentials use the existing
OS-keyring path; workspace JSON remains redacted. Editing a retained TLS draft
also preserves its existing hidden keyring choice without enabling TLS.

Test uses `OracleSession.test_connection`, not the generic driver's probe.
The successful direct diagram line and separate full-width bottom result bar
follow the observed successful reference state, using original artwork. The
bar displays the adapter's safe `Oracle` plus version text; the reference's
longer product-edition description has not been reproduced or inferred.
Failure presentation and exact native styling remain unaccepted.

## Browser and worker boundary

The schema tree retains **表 / 视图 / 实体化视图 / 函数 / 查询**. Table and View
object lists use the observed 28 and 13 paired columns. Unavailable metadata
uses an explicit unavailable value, not a false boolean, zero count or an
invented empty catalog result. Materialized-view, Data Pump and all 15 observed
Other categories remain visible tool surfaces with unavailable execution.

Table/view data pages call only the supported column and bounded fetch APIs.
They do not request unsupported DDL, indexes, foreign keys or a full count.
Total rows remain explicitly unknown; Next depends on the bounded page's
truncation state, and Last remains unavailable. Grid models are not editable;
mutation, transaction, designer, import, comparison and diagram handlers have
execution guards as well as disabled controls.

Query navigation remains visible but unavailable. The backend currently
validates and remembers a selected owner without `ALTER SESSION`; an arbitrary
query tab must not imply that unqualified SQL is bound to that owner. Backend
SELECT support is not acceptance of the application's query workflow or a
replacement for the missing Oracle tools.

Connect, schema/catalog reads, columns, data and close run on the window's
serial owner. Its worker does not expire between operations. Widget values and
the session are captured before queuing. Each job has an Event and an
owner-finished boundary; cancellation uses its exact cached marker on the
independent cancellation pool. A queued or completed job cannot cancel a later
job. The backend's documented limitation for promptly interrupting an owned
server-side SLEEP call remains open.

Test tasks retain GUI-owned signals and deferred deletion. Completion now
also releases the stored exception reference, whose traceback otherwise kept
the completed runnable and its configuration alive. Existing SSH approval and
retry ordering still occurs after the worker's finished signal.

## Measured qualification

On macOS ARM64, using the worktree's physical Python 3.13 environment and its
locally built pinned native JavaScript helper:

- The final focused scope passed **246 cases, zero skips/failures/errors**. It
  includes 27 dedicated Oracle-form cases, 18 actual MainWindow cases using a
  strict fake Oracle session, existing generic dialogs/selector/import/storage
  regressions, and the Oracle backend unit/runner cases. Collected node IDs and
  JUnit testcase IDs match exactly.
- The full source suite passed **2,382 cases**, with **659 conditional skips**,
  zero failures and zero errors, in **89.59 seconds**. The focused scope took
  **4.44 seconds**. An independent full collection also matched the JUnit
  testcase multiset exactly, including its conditional skips.
- Both scopes record the same 217 declared source/dependency paths before and
  after execution. The native helper hash is recorded separately. No native
  GUI or configured Oracle endpoint was opened by these scopes; the generic
  regression suite uses its owned temporary SQLite fixtures.

The first full-source attempt retained two failures in older import-storage
tests that hard-coded Oracle as an unimplemented driver. Those tests now use
the still-unimplemented Snowflake sentinel, while the nine-family case retains
Oracle and the explicit unsupported-field blocks. An additional MainWindow
case proves that a registered Oracle adapter does not bypass imported-profile
review. No import protection was removed from the product.

The shared backend checkpoint is `b8f3f8a8415c054481c37cd9c3684e589b445405`.
Its separately qualified 14 owned-Oracle cases and exact resource-cleanup
evidence belong to the backend scope; fake-session GUI tests do not convert
that evidence into an actual GUI-to-Oracle workflow acceptance.

Actual Cocoa Test/save/reopen/schema/table/view acceptance, remaining Oracle
authentication and topology modes, all unimplemented tools and designers,
Windows/Linux reference acceptance, six native installers and public release
verification remain separate gates. This UI checkpoint did not change the
version, tag, GIF or release assets; the later integration's 0.13 candidate
does not change that checkpoint's qualification scope.
