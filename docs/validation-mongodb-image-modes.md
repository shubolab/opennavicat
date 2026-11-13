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
At this branch checkpoint, no native Cocoa run, six-target CI, installer or
release qualification had been obtained. Retention expiry, deleted/concurrent images, other
topologies and permissions, broader recovery, successful Preview/Explain and
complete reference-backed parity remain open.

## Main integration

The six-path checkpoint was committed and pushed as
`b53f60e7e7792c1c6af326c77bce637f52ea46a9`. Independent review verified its
296 earlier tracked Git blobs, separate new documentation hash, all scope
manifests/results and the exact four containers/eight anonymous volumes from
the red, development and final fixture runs as absent.

It was merged with the later reference documentation and the separate native-JS
semantic-test budget correction as `53cce32`. All eleven declared Window/backend
source paths still match the qualified image checkpoint, including their Git
blobs; all application and native-helper sources remain identical to it. Six
earlier tracked files differ: five documentation records and the independently
qualified `test_js_engine.py` budget correction. The 2243/645 full-source result
belongs to the image checkpoint; it was not rerun under the merged test budget.
The seven actual delayed-native semantic-error cases separately qualify that
test change. Qualification of the combined source is recorded separately below;
neither earlier failed CI run nor the published v0.12.0 tag supplies it.

The version, README GIF, public tag and eight release assets remain unchanged.
This is unreleased development. Private integration binding is
`artifacts/stream-variants/image-integration/source-binding.json` in the
`mongodb-stream-variants` tree. The complete parity goal remains active.

## Combined main CI and actual Cocoa

The combined source was pushed to main as
`fab0419fd47ad49378f3c2481bee0dca46fe7a43`.
[Run 43399815386](https://github.com/shubolab/opennavicat/actions/runs/43399815386)
completed successfully: six native-target jobs plus database security passed;
the main-branch publisher was skipped. Independent review bound all thirteen
artifact inventory entries to this run/source and downloaded the seven
validation artifacts. Native build, architecture, bundled helper, packaged
smoke and installed launch/uninstall receipts passed for all six targets.

| Native source suite | Passed | Conditional skips | Failures/errors |
| --- | ---: | ---: | ---: |
| Linux amd64 and arm64, each | 2224 | 646 | 0 |
| macOS amd64 and arm64, each | 2242 | 646 | 0 |
| Windows amd64 and arm64, each | 2134 | 772 | 0 |

The seven native semantic-error cases from the separate JS-test correction
actually passed on every target. Each target also passed native VM 103/0 and
Qt/helper 4/0 scopes. Security separately qualified backend **74/0**, Window
**25/0**, ordinary TLS **24/0**, stream TLS **2/0**, cleanup **13/0** and
collections **511/0** against exact source/AST/collected nodes/JUnit and owned
fixture receipts. These overlapping scopes are not added together.

This audit did **not** independently download/rehash the six installer bytes or
artifact ZIP bytes; it is a CI-receipt audit, not a new published-installer
qualification. CI database scopes ran on owned Linux amd64 fixtures. The two
earlier failed runs and their actual failures remain in the variant validation
record. No tag, version or public release changed. Private CI audit is
`artifacts/stream-variants/main-ci-43399815386/verification.json` in the
`mongodb-finite-stream` tree.

The exact combined source was then tested locally through actual **Cocoa** on
Darwin ARM64: the complete Window module passed **25/0** in **36.43 s** against
another fresh authenticated native ARM64 MongoDB 8.0.34 replica set. The runner
observed `QApplication.platformName() == "cocoa"` during the test calls, verified
all eleven declared source paths against the `fab0419` Git blobs and matched
the exact collected/JUnit case multiset. Six captures came from the new image
cases; visual review of both OFF-required cases confirmed the static `0/0`
line, the correct frozen script and three tabs without Output. The exact
container and two anonymous volumes were independently confirmed absent.
Receipt and independent check are under
`artifacts/stream-variants/image-integration/native-cocoa-window/` in the
`mongodb-stream-variants` tree. No personal workspace or reference screenshots
were used. This remains bounded macOS source execution, not complete UI parity
or Windows/Linux human/reference acceptance.
