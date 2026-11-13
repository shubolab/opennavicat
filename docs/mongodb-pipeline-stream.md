# Finite MongoDB change-stream backend: development checkpoint

This 2026-11-13 checkpoint describes the development branch's internal finite
stream backend. **The published v0.11.0 release does not contain it.** It is
not a completed stream workspace, a new release acceptance record or closure of
the UI/function gates in the [parity matrix](parity-matrix.md).

The reference basis is [AP18-10 and its follow-up](ui-reference-18-mongodb-pipeline.md#ap18-10-follow-up-event-presentation-and-preview-actions).
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
`partial_result`. UI-facing driver failures use fixed messages and a server
code where available; authentication rejection keeps its typed flag. Raw driver
text, credentials, endpoints and server-echoed document values are not copied
into the displayed message. The original exception remains the cause for
internal classification.

The undelivered counters describe only a document already obtained by this
invocation but not delivered. They do **not** count remaining driver-buffered
or server events. In particular, zero does not establish that no events remain.
The compatibility `rows`/`truncated` iteration is insufficient to present all
completion and partial-failure facts; caller/UI handling remains an open gate.

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

## Separate evidence scopes

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

## Frozen local backend checkpoint

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

Normal reference-shaped text-only Messages/Output behavior, complete partial
event/failure presentation, successful Stage Preview/Preview All, supported
refresh/resume recovery, all raw stage/options variants, other permissions and
topologies, and Windows/Linux/macOS UI acceptance remain open. The generic
result-grid compatibility path is not proof of the AP18-10 observed UI. The
full [original parity scope](parity-matrix.md) remains unchanged.
In particular, interactive and Batch callers must inspect `row_limit` and
`deadline` completions instead of treating a normally returned bounded result
as unconditional full success. Byte-limit failures already raise and the Batch
error/log contract is tested; returned row/time-boundary handling remains part
of the pending end-to-end integration.
