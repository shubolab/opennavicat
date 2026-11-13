# Required-image diagnostics and finite-stream regression checkpoint

This development slice is based on
`5fcf99ceb9cb2c66e350c24e2d64eb070cd093e5`. It adds six owned Window
regressions motivated by the separately recorded AP18-13 reference observation,
and implements its two narrowly observed zero-position error sentinels.
It does not change a version, release, public GIF, parity matrix or snapshot.

## Actual application execution

The new parameterized case in
[`test_mongo_stream_window.py`](../tests/test_mongo_stream_window.py)
uses the real pipeline toolbar Run action and MainWindow serial task queue.
The application authenticates with SCRAM-SHA-256 as a generated user's sole
`read` role on that case's generated database. An independent fixture client
configures only this generated collection, saves its actual image setting and
UUID, and waits for the exact Run marker, namespace and active `getMore` before
performing one conditional majority-acknowledged update and a separate barrier
insert. No global image-retention setting is changed.

| Case | Stream body and update condition | Captured private result |
| --- | --- | --- |
| Images OFF, both image fields `whenAvailable` | Require both image documents' `counter` fields, or the barrier | Barrier only: **1** event. This does not distinguish each unavailable field's null/absence value. |
| Images ON, both image fields `whenAvailable` | Same joint existence predicate | Update and barrier: **2** events; both expected image documents match exact BSON. |
| Images OFF, `fullDocumentBeforeChange: "required"` | Update or barrier | Actual `get_more` failure, code **47**, missing pre-image. |
| Images ON, `fullDocumentBeforeChange: "required"` | Require the pre-image's counter, or barrier | **2** events; exact expected pre-image BSON. |
| Images OFF, `fullDocument: "required"` | Update or barrier | Actual `get_more` failure, code **47**, missing post-image. |
| Images ON, `fullDocument: "required"` | Require the post-image's counter, or barrier | **2** events; exact expected post-image BSON. |

Event documents remain private typed results. Canonical Extended JSON must
re-encode to each event's exact raw BSON; expected image and barrier documents
include large Int64, Decimal128, Binary subtype 128, Timestamp and ObjectId
values. The app's public listener checks one initial aggregate, the frozen
pipeline, initial zero batch, positive server budget and unchanged operation
marker. This direct application listener evidence does not strengthen the
reference profiler's unknown exhaustive initial-call count.

After success and failure alike, the original application session reads on its
original serial owner and confirms its actual `read` role. Independent reads
check the expected updated document, barrier, unchanged control document,
document count, unchanged collection UUID and image setting. Cross-user
`currentOp` finds **zero active and zero idle cursors** for the generated
namespace/marker after backend cleanup. The application never borrows the
privileged fixture client's login.

## Display sentinel contract

`StreamExecutionError.reference_sentinel` is an optional
`StreamReferenceSentinel` enum with separate missing-required-pre-image and
missing-required-post-image members. The owner emits it only for a continuation
`OperationFailure` whose error and response codes are integer 47, whose
dispatched first stream stage explicitly requested that corresponding literal
`required` string, and whose owner-redacted bounded message matches the
observed MongoDB prefix and update-event marker. Other event kinds, messages,
codes, phases and unrequested/disabled required options acquire no sentinel.

The UI rechecks the typed enum, safe matching message, continuation phase,
underlying server code and absence of actual script coordinates. It then
renders the static line `at line 0, column 0` below the bounded message for
these two failures. The real `script_location` remains **None**. Zero values
are never accepted as real locations; positive one-based UTF-16 aggregate
location validation is unchanged. Passing any script coordinates alongside the
sentinel, including malformed zero coordinates, cannot reinterpret them as this
display marker.

Authentication, cancellation, cleanup failures and row/byte/time limits retain
their existing status priority. Their structured text/sentinel is discarded.
The owner still rejects malformed, oversized, multiline, control/bidi and
malformed-Unicode server messages, redacts all four profile secrets and encoded
forms plus URI tokens, and never falls back to raw driver exception text.
Other continuation errors remain without a fabricated location. The error
cases show Messages with the current frozen script, diagnostic, static sentinel
and elapsed time, while Output and event export remain unavailable.

The successful ON Output pages seen blank in AP18-13 still have an unresolved
cause. This change does not alter successful Output presentation or claim that
those blank states are implemented or explained.

## Measured local scopes

The six new real cases first produced **4 passed / 2 failed** in 20.01 seconds
against the original runtime: the two OFF required-image errors lacked the
observed zero-position line. The red evidence and exact-resource cleanup were
retained before the runtime fix.

After the source was frozen, macOS ARM64/offscreen validation produced:

| Scope | Result | Time |
| --- | --- | --- |
| Strict backend runner | **74 passed, 0 skipped, 0 failures/errors** | 9.17 s |
| Strict Window runner | **25 passed, 0 skipped, 0 failures/errors** | 28.53 s |
| Combined backend/Qt offline invocation | **135 passed, 14 conditional service skips** | 2.09 s |
| Complete default source suite | **2243 passed, 645 conditional skips, 0 failures/errors** | 94.07 s |

The strict runners dynamically collected their exact complete modules and
checked JUnit membership with counters. Each used a separate pinned native
ARM64 MongoDB 8.0.34 authenticated replica set bound to loopback. The 14 real
backend cases skipped in the offline invocation are actually exercised by the
strict backend scope; the scopes overlap and are not summed as acceptance.
Default skips do not count as executed database/platform acceptance.

All **2888** default-suite JUnit cases match its collection. A private reader
initially used the wrong module-name normalization; its false verification was
retained, and an independent reader corrected only that mapping and checked the
same saved results without rerunning tests. All **296** previously tracked
files and the native helper matched their pre/post-run hashes. The final strict
source hashes matched as well. Independent post-run Docker inspections confirmed
the exact two final containers and four anonymous volumes absent; the earlier
red and development-green resources were independently checked absent too.

Ignored receipts are under `artifacts/image-modes/`, including
`red-six-r1/`, `backend-final-r1/`, `window-final-r1/`, `full-final-r1/` and
`final-verification.json`. The latter binds source hashes and exact owned
resource identities. No raw reference evidence or credentials are packaged.
No native Cocoa run, six-target CI, installer or release qualification is
claimed for this slice. Retention expiry, deleted/concurrent images, other
topologies and permissions, broader recovery, successful Preview/Explain and
complete reference-backed parity remain open.
