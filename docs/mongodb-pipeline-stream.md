# Finite MongoDB change-stream backend: development checkpoint

This 2026-11-13 checkpoint describes the development branch's internal finite
stream backend. **The published v0.11.0 release does not contain it.** It is
not a completed stream workspace, a new release acceptance record or closure of
the UI/function gates in the [parity matrix](parity-matrix.md).

The reference basis is [AP18-10 and its follow-up](ui-reference-18-mongodb-pipeline.md#ap18-10-follow-up-event-presentation-and-preview-actions)
and the later same-tab [AP18-11 observation](ui-reference-18-mongodb-pipeline.md#ap18-11-same-tab-run-presentation-and-busy-controls).
Navicat 18.0.5 build 321 returned finitely after an empty `getMore`; a generated
event was independently observed on the wire, while its Messages and Output
pages showed script, OK and timing text. Stage Preview and Preview All remained
available, performed separate source sampling and stream requests, then crashed
in the two bounded follow-up probes. Those failures are observations to retain,
not behavior to reproduce or a reason to remove successful-preview gates.
The applicability and behavior of any other ongoing-refresh workflow remain
unknown; this checkpoint does not invent a continuous-watch reference contract.

## Internal API and result contract

[`execute_finite_stream`](../opennavicat/mongodb_pipeline_stream.py) accepts a
connected `MongoSession`, database name and validated `PipelineDefinition`:

```python
execute_finite_stream(
    session, database, definition,
    limit=1000, await_ms=1000, timeout_seconds=120,
    max_bytes=16 * 1024 * 1024, cancel_event=None,
)
```

Limits may be reduced. The maximums are 1,000 delivered events, a 1,000 ms
per-`getMore` await, a 120-second cooperative elapsed budget, and 16 MiB each
for cumulative raw BSON and rendered Canonical Extended JSON. Boolean values
are rejected where a numeric limit is required. These are implementation
bounds, not complete reference specifications or guarantees about server memory
and execution work.

An enabled `$changeStream` is required. The development branch's ordinary
`execute_pipeline` entry routes enabled stream stages to this backend. A lone
`$changeStreamSplitLargeEvent` without `$changeStream` is not a valid finite
stream request. Server-invalid operator ordering or options still produce an
execution error; the backend does not silently rewrite the user's stage chain.

`FiniteStreamResult.events` is an ordered tuple of `StreamEvent` values. Each
event retains immutable `raw_bson` bytes and `canonical_json` text. Before
delivery, decoding and re-encoding that text must reproduce the exact BSON
bytes, including BSON types. An unrepresentable event is an explicit failure,
not a conversion through Python `str()` or ordinary JSON. The result also
contains `completion`, `elapsed_seconds` and three known-undelivered fields:
`undelivered_event_count`, `undelivered_raw_bytes` and
`undelivered_rendered_bytes`.

| Completion | Meaning |
| --- | --- |
| `empty_batch` | Cached documents and nonempty continuation batches were drained, then one `try_next()` returned no document. No further polling is scheduled. |
| `row_limit` | The delivery cap was reached; the backend stops without fetching an extra event to prove whether more exist. |
| `deadline` | The cooperative elapsed budget was reached. A budget check can return this bounded result; a failed in-flight I/O or dispatch can instead raise an error carrying it. |
| `byte_limit` | A newly obtained event would exceed a cumulative byte bound. This raises `StreamExecutionError` with the delivered prefix and the known undelivered event's count and sizes. |
| `cancelled` | Cancellation was observed; an error carries the delivered prefix and any known obtained-but-undelivered event. |
| `error` | A driver/server, event-size or exact-BSON conversion failure occurred. Oversized first events fail explicitly. |
| `cleanup_failed` | Cursor release could not be confirmed. This is an error even if reading had otherwise finished. |

`StreamExecutionError` derives from `DatabaseError` and exposes
`partial_result`. It also carries optional owner-produced `server_message`,
`error_phase` (`aggregate` / `get_more`) and `script_location` metadata. Only
the bounded single-line `errmsg` field is extracted from a real server error;
it is limited to 2,048 characters / 8,192 UTF-8 bytes, excludes control/bidi/
malformed Unicode, and has complete URI tokens and known owner-profile secrets
redacted. Raw `str(error)`, a complete response, arbitrary codeName and event
documents are not display-text sources. The original exception remains the
cause for internal classification; authentication rejection keeps its flag.

An actual typed error with completion `error`, a valid phase and bounded
message can display that message. An aggregate's optional position is checked
against the frozen dispatched script using one-based UTF-16 columns. A valid
message without a position remains visible; no position is invented for
`get_more` or an unavailable script formatter. Malformed metadata falls back
to a validated numeric server code. Cancellation, cleanup failure, row/byte/
time limits and authentication retain their static status priority. Full
multiline diagnostics and all server-error variants remain open.

The undelivered counters describe only a document already obtained by this
invocation but not delivered. They do **not** count remaining driver-buffered
or server events. In particular, zero does not establish that no events remain.
The compatibility `rows`/`truncated` iteration is insufficient to present all
completion and partial-failure facts. The candidate Window now routes the typed
result/error explicitly; complete partial-data presentation and export remain
separate gates.

## Finite cursor and budget behavior

The backend uses a raw `CommandCursor`, not PyMongo's automatically resumable
`watch()` wrapper. It drains cached events and subsequent nonempty batches,
then stops on the first empty continuation batch or an explicit bound. An
initial empty batch, including requested `cursorBatchSize: 0`, still permits
one bounded `getMore`; initial emptiness is not mistaken for a completed read.
Continuation batch sizes are positive and capped at 100 and the row limit.

No resume token is injected, saved, reused or advanced automatically. An
explicit user-provided `resumeAfter` remains part of that invocation's stage
body. A later fresh invocation receives a new operation nonce and starts from
its requested body. A network failure is not transparently reopened at a new
stream position. In particular, the separately cached finite-stream client has
`retryReads=False`, preventing an ambiguous initial aggregate response from
silently causing another initial aggregate. Ordinary query/pipeline clients
retain their configured retry settings.

| Budget | Implemented boundary |
| --- | --- |
| Aggregate `maxTimeMS` | A positive user limit is preserved or reduced, never increased. An unset/zero limit receives a positive finite application ceiling for this backend. Cold preparation is charged to the same elapsed deadline, which is rechecked before dispatch. |
| `maxAwaitTimeMS` | Separately controls each `getMore` wait; PyMongo sends it as that command's `maxTimeMS`. It is not substituted for the aggregate limit. |
| Network and elapsed time | The separate client has finite selection, connection and socket timeouts. A public bounded ping warms cold authentication/selection; each continuation I/O receives the remaining application budget. |

As with the existing positive pipeline-budget path, initial aggregate I/O
temporarily disables ambient CSOT so it cannot overwrite the positive server
limit or round a small value down to unlimited zero. The API's explicit budget
and finite network settings apply instead; an arbitrary caller's shorter
ambient CSOT is not inherited. Neither `maxTimeMS` nor `maxAwaitTimeMS` is a
promise of exact CPU or wall-clock preemption. Cancellation/deadline checks
occur around dispatch, cold preparation and event delivery; active I/O and
server work still require their separate cancellation/timeout behavior.

## Ownership, cancellation and cleanup

Database I/O and cursor lifetime belong to the existing serial owner worker;
this API reads or changes no Qt widgets. A separate cancellation worker can
target the captured operation marker. Both the session cancellation signal and
the job's external event are checked, including cancellation before a session
marker is established and after a `getMore` returns.

Cleanup runs in the owner thread under its own three-second budget. It closes
the raw cursor, requires positive `cursorsKilled`/`cursorsNotFound`
acknowledgement for known cursor IDs, and inspects bounded active/idle metadata
for the invocation's exact nonce. That nonce also permits recovery when the
server created a cursor but its initial response was lost before the caller
received an ID. Only matching operations/cursors are targeted; unrelated
cursors and the main/control sessions are not a session-wide cleanup target.
Existing user comment text is retained inside the nonce-bearing comment.
The unknown-ID test injects a failure at the public API boundary after a real
server cursor is created. It establishes cleanup without a returned cursor
handle; it does not claim to drop the initial response packet on the network.

Cleanup denial, an ambiguous acknowledgement, remaining matching work or an
unconfirmed deadline becomes `cleanup_failed`; success is not reported on an
unverified release. The finite-stream client is discarded on failure, while
main/control clients remain available. `MongoSession.close()` clears the cache
and closes clients before releasing their reused SSH tunnel and temporary
client-certificate material. Cleanup metadata privileges remain a tested
permission boundary, not a reason to grant broader database access silently.

Finite stream reads are allowed with `config.read_only=True`. Enabled `$out`
and `$merge` are rejected before dispatch even for writable sessions. A scoped
database `read` role was tested for this stream/own-cursor cleanup path; this
does not qualify every authentication mechanism, role or MongoDB deployment.

## Historical backend-only evidence scopes

The following are independent checkpoints. They are not added into a combined
test total, and their recorded source hashes remain attached to each receipt.
All services and resources were newly generated and ownership-bound; no
reference or user database endpoint was reused.

| Scope | Recorded result | What it establishes |
| --- | --- | --- |
| [`test-mongodb-stream.py`](../scripts/test-mongodb-stream.py), [`test_mongodb_pipeline_stream.py`](../tests/test_mongodb_pipeline_stream.py) | **26 passed, zero skipped**: 12 pure cases and 14 real single-node replica-set cases | MongoDB 8.0.34 protocol/BSON behavior, initial-zero batch, independent aggregate/await limits, manual resume/update lookup, fresh-run isolation, bounded row/byte failures, cancellation, lost-response and no-retry behavior, read-role cleanup and exact owned-service teardown. The runner and independent check matched their recorded source hashes. |
| [`test-mongodb-security.py --scope stream`](../scripts/test-mongodb-security.py), [`test_mongodb_stream_security.py`](../tests/test_mongodb_stream_security.py) | **2 passed, zero skipped**, separate evidence directory | Real verify-full original-hostname TLS over SSH, mutual TLS/encrypted client key, finite/no-retry client settings, legal ping/aggregate I/O, cache/failure and client-before-tunnel/PEM lifetime. Its standalone servers also actually rejected the production `$changeStream` command with code 40573. This is **not** replica-set event acceptance. Two containers, four anonymous volumes and generated certificate/SSH-key material were verified removed. |
| [`test_mongodb_stream_runner.py`](../tests/test_mongodb_stream_runner.py) | **13 passed, zero skipped**, opt-in stopped-resource scope | Exact fallback cleanup and refusal boundaries, including named volumes that resemble anonymous IDs. **Zero container starts and zero database connections.** Default non-opted collection invoked no Docker process. This is cleanup-runner acceptance, not stream execution. |

The TLS checkpoint's stream-source hash predates the final backend cleanup
strengthening measured by the 26-case checkpoint; it must not be relabelled as
a whole-source final qualification. The earlier helper-built default suite's
**2,063 passed / 606 skipped** is also historical: it preceded collection of
the new 13-case cleanup module. The newer frozen local checkpoint below
supersedes it; the next exact-source CI still remains to run. No installer,
native-platform smoke or published release claim follows from these scopes.

## Historical frozen backend checkpoint before Qt integration

With the pinned native ARM64 JS helper built, the complete default suite passed
**2,073 cases / 619 conditional skips** in 74.50 seconds. The 619 skips do not
stand for actual service execution. Four separate fresh qualification scopes
also passed: the replica-set module **26/0 skips**, ordinary MongoDB TLS/SSH
**24/0**, stream transport **2/0**, and stopped-resource cleanup **13/0**.
These scopes overlap the default collection and are not summed together.
Ten offline strict-cleanup-gate tests, including a real slow child receiving
SIGINT and running its finalizer, are included in the default suite.

The cleanup gate collects every required case, checks exact zero-skip JUnit and
one ownership ledger per case, then independently inspects the recorded IDs.
The named-hex-volume regression first demonstrated container removal before
refusal; the fixed implementation rejects explicit or inherited mounts before
any removal and accepts only the fixture's implicit image volumes. A matching
volume-name shape alone is insufficient evidence of anonymous ownership.

Private receipts are under `artifacts/finite-stream-freeze-20261113/`, with
separate `replica/`, `security-regular/`, `security-stream/` and `cleanup/`
directories. `independent-check.json` confirms every scope's listed source
hashes still match, and independently confirms absence of 20 exact container
IDs and 36 recorded volume IDs. The ordinary TLS fixture separately confirms
its own `rm --volumes` cleanup but does not record individual volume IDs;
those volumes are not included in the independent-ID count. These are local
working-tree measurements before the WIP commit, not six native installers,
published behavior or complete reference acceptance.

## 0.12 candidate Qt and Window checkpoint

The later candidate implements the observed finite text path. Busy stream
execution leaves two tabs (`管道`, `脚本预览`). Completion shows three tabs for
an empty result or error, and four for an event-bearing successful result by
adding `输出`; another empty Run removes Output again. Messages and stable
Output show the frozen script, status and actual elapsed time. Events remain
in the private typed result: no ordinary event grid, `last_rows` delivery or
unqualified event export is enabled. Ordinary preview/run/Explain restores its
own existing views and clears the stream presentation state.

The lower detail workspace has the observed busy overlay; source collection,
sampling mode/count and operator are disabled while busy. The page-local Stop
uses its captured job/session/nonce through the independent cancellation queue;
the application tab's busy ring is restored to its prior icon afterwards.
Completed stream Messages/Output keep Save, grey Run and focus; returning to
Pipeline restores its action group. Busy Import and other unexecuted graph/raw
editor mutations are not claimed as accepted merely from their visible state.

The finite UI/GIF checkpoint is committed as `979d052`; the subsequent
`479a811` parents TaskSignals to MainWindow and defers their deletion after the
existing finished cleanup. Four new real Qt regressions plus the prior eighteen
lifecycle cases pass. The post-fix measurements below remain separate scopes:

| Scope | Result | Qualification boundary |
| --- | --- | --- |
| Native Window runner | **16 passed / zero skipped** | Eleven declared source hashes match the repaired source. Real toolbar-to-wire Run, local/queued/cold/late Stop, exact private BSON, invalid-stage message/location and two real Batch workflows. One owned RS container and two volumes independently absent. |
| Native Cocoa components + Window + new lifecycle regressions | **98 passed / zero skipped**, 28.60 seconds (stdout) | 16 Window +78 components +4 new lifecycle cases; actual Cocoa, Darwin arm64, v0.12.0, fourteen source hashes unchanged. One owned RS container and two volumes independently absent. Scoped UI checks do not establish complete platform parity. |
| Default source suite | **2,184 passed / 636 conditional skips**, 84.67 seconds (stdout) | Built native helper; 201 runtime/test/runner/native/workflow/project paths unchanged and independently matched to immutable `479a811` blobs. The old HEAD at test start is not the tested-source identity. |
| Ordinary collection runner | **511 passed / zero skipped** | Strict manifest, 198 outer-observed source hashes unchanged, 48 declared source hashes matched; eight containers and sixteen volumes independently absent. Includes the complete 22-case lifecycle module. |
| Recorder qualification | **79 passed / 3 conditional skips** offline; separately **1 passed / zero skipped** own-RS | Each scope binds 79 unchanged source files to `479a811`. Real toolbar Run delivered two typed events with exact BSON/UUID and preserved original session; owned service and both volumes independently absent. No GUI capture was made by these offscreen scopes. |
| Unchanged declared-source qualifications | Backend **46 / zero skipped**; ordinary TLS **24 / zero skipped**; stream transport **2 / zero skipped**; stopped cleanup **13 / zero skipped** | Rebound by matching each scope's declared source bytes, not represented as post-fix re-executions. Backend is 32 pure +14 RS. Stream transport is standalone TLS/SSH with 40573 rejection; cleanup starts no containers and connects to no DB. Ordinary TLS's incomplete outer volume-ID record remains a limit. |

The post-fix receipts are under `artifacts/release-012/`, notably
`final-default-ownership/verification.json`,
`final-native-cocoa-ownership/verification.json`,
`final-window-owner-fix/summary.json`, `final-collections-owner-fix/summary.json`
and `owner-fix-validation.json`. Recorder receipts are in the separate
`final-recorder-ownership-{offline,own-rs}-20261113T065926Z-9dc0cd/` directories.
The earlier **2,180 / 636**, **94 Cocoa** and serial **507** collection results
remain the pre-ownership checkpoint; their old source hashes are not relabelled
as repaired-runtime measurements. The 78 component cases comprise 57 stream and
21 existing pipeline cases. Counts across scopes are not added.
The [0.12 validation record](validation-0.12.md) retains all checkpoint and demo
source ownership. Exact final-candidate six-target CI/installers remain pending.

An initial parallel collection run ended in SIGSEGV; the separate fresh serial
retry passed 507 cases with eight containers and sixteen volumes independently
absent. That retry does not establish the crash's cause. A separate real
`MainWindow.submit` experiment confirmed completed Task/TaskSignals Python
objects retained by native callback closures. The `479a811` ownership fix and
post-fix regression/qualification above address that confirmed defect. Its
relationship to the original SIGSEGV remains unproven; successful reruns do not
establish causality or close the broader resource/performance gate.

Interactive and Batch callers now inspect returned `row_limit` and `deadline`
instead of recording unconditional full success. Byte overflow remains an
explicit typed partial failure. Batch history/logs classify these outcomes
without using server-message text, and cleanup failure retains priority over
cancellation. The two real Window Batch cases verify independent sessions after
the main connection is Off, persisted history, and row-limit failure/skip behavior.
This does not complete the other task categories, scheduler or automation UI.

Complete partial-event workflows, successful Stage Preview/Preview All,
supported refresh/resume recovery, remaining raw stage/options variants, other
permissions/topologies and all three platforms' full reference UI acceptance
remain open. No exact-source six-installer or published 0.12 qualification is
claimed. The full [original parity scope](parity-matrix.md) remains unchanged.
