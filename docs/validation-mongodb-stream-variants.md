# Finite-stream full-document regression checkpoint

This 2026-11-13 development checkpoint adds tests to an isolated branch based
on `b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`. Application code, package
metadata, CI, public assets and the frozen release source are unchanged. These
results do not establish a new installer, native UI acceptance on three
platforms, or complete MongoDB stream parity.

The three cases follow the per-cursor default/update-lookup distinction observed
in the separately recorded AP18-12 reference slice. They do not strengthen that
reference observation's unknown initial-aggregate call count. Here the tested
application's own public PyMongo `CommandListener` observes its initial commands
directly, so the regression can independently require exactly one initial
aggregate for each application Run.

## Actual window execution

[`test_real_toolbar_full_document_update_variants_keep_typed_events_private`](../tests/test_mongo_stream_window.py)
clicks the actual pipeline toolbar Run button and uses the MainWindow serial
task queue. The application authenticates using SCRAM-SHA-256 as a newly
generated user whose only role is `read` on that case's generated database.
An independent privileged fixture client supplies the writes; it waits for an
active `getMore` matching this Run's namespace and exact operation marker.

The producer makes one conditional update with majority write concern, verifies
exactly one document changed, and then inserts a unique typed barrier document.
The barrier must arrive even when the update is filtered out. Every invocation
requests an initial cursor batch of zero and `maxTimeMS: 5000`, drains through
the first empty continuation batch, and closes its cursor.

| Case | Stream body and update filter | Private events |
| --- | --- | --- |
| U0 | Default body; match the target update or barrier insert | Update, then barrier: **2**. The update has `updateDescription` and no `fullDocument`. |
| U1 | Default body; require `fullDocument.counter` on the update, or match the barrier insert | Barrier only: **1**. |
| U2 | `fullDocument: "updateLookup"`; the same filter as U1 | Update with the expected full document, then barrier: **2**. |

The barrier in all three cases includes `ObjectId`, large `Int64`,
`Decimal128`, binary subtype 128 and `Timestamp` values. The lookup document
contains these types as well. Each private event's Canonical Extended JSON must
re-encode to its exact captured raw BSON; the expected barrier and lookup
document are also compared as BSON bytes. This controlled update has no later
concurrent mutation. MongoDB documents `updateLookup` as returning the current
majority-committed document, which is not a general historical post-image
guarantee. See the official [MongoDB 8.0 `$changeStream` documentation](https://www.mongodb.com/docs/v8.0/reference/operator/aggregation/changestream/).

The regression also checks the frozen stage bodies and script, one initial
aggregate, an unchanged explicit operation comment, no injected `resumeAfter`
or `startAfter`, and the effective positive initial server limit. Messages and
Output contain the same script and OK status, with four visible tabs and no
business-event values, result rows or enabled event export. After completion,
the original application session reads the updated document on its original
serial owner and reports only the generated database's `read` role. A separate
client verifies the updated target, barrier, unchanged control document, exact
document count, and absence of active or idle cursors for the owned namespace
and marker across users.

## Existing resume coverage and measured scopes

The existing backend case
`test_real_manual_resume_update_lookup_and_fresh_run_do_not_share_tokens` already
executes an explicit `resumeAfter` token, obtains an insert and update with
lookup documents, repeats that explicit request with identical raw event BSON,
and proves a fresh request does not inherit the token. The Window suite also
retains its frozen-dispatch and fresh-tab/run isolation cases. No application
change was needed for this slice.

On macOS ARM64, with a separately built pinned native QuickJS helper:

| Strict scope | Result | Time |
| --- | --- | --- |
| `scripts/test-mongodb-stream.py --scope window` | **19 passed, 0 skipped, 0 failed/errors**, including the three new cases | 23.29 s |
| `scripts/test-mongodb-stream.py --scope backend` | **46 passed, 0 skipped, 0 failed/errors**, including the existing explicit-resume/lookup case | 10.30 s |

Both scopes dynamically collected their complete module and checked exact JUnit
membership and stable source hashes. Each used its own new pinned native
MongoDB 8.0.34 authenticated single-node replica set bound to loopback. For each
scope, fixture finalizers and a separate post-run inspection confirmed the
exact container and its two exclusive anonymous volumes were absent. The
application used only an isolated workspace, generated data and memory-backed
test keyring. No existing profile or user database was read.

Private receipts remain ignored under `artifacts/stream-variants/window-r1/`
and `artifacts/stream-variants/backend-r1/`; the environment/helper and independent
resource checks are recorded beside them. The earlier three-case development
probe preceded the final typed-barrier assertion and is not the final source
qualification. These local scopes are not six-target CI or published-release
evidence.

## Integration with the current main documentation

The branch subsequently merged main `33b0df7` as `3012d3e`. All eleven
recorded Window/backend source paths remained byte-identical to the qualified
`94de386` inputs; their Git blobs and current files were checked again. This
merge carries the verified v0.12.0 publication receipts and AP18-12 notes without
changing the application, test cases, runner or workflow.

The merged branch's default source suite passed **2,197 cases / 639 conditional
skips** in 96.92 seconds. Its 215 tracked runtime, test, build, packaging and
workflow inputs were unchanged before/after the run and individually matched
the `3012d3e` Git blobs. No external database opt-in was enabled; the three
additional skips are the new service-dependent cases, not executed acceptance.
Actual Window/backend service qualification remains the separately recorded
19/46 scopes above. Private integration receipts are
`artifacts/stream-variants/merged-source-binding.json` and
`artifacts/stream-variants/full-merged/verification.json`.

This is a test/documentation integration after the published v0.12.0 source
`b6e1da4`; it does not replace that tag, installer bytes or release evidence.
The integration was pushed to main as `dfbbf5e`. Its separate
[CI run 43395784419](https://github.com/shubolab/opennavicat/actions/runs/43395784419)
failed the stopped-container cleanup test. The downloaded Window **19/0** and
backend **46/0** receipts independently match that commit's exact Git blobs,
collected nodes, JUnit cases and owned fixture cleanup. These successful scopes
do not make the complete run successful.

The cleanup failure was an order-sensitive test assertion: two successive
Docker inspections returned the same container and the same two anonymous
volumes in opposite orders. The runner already checks volume identity as a set;
the test additionally assumed a stable `Mounts` order. All thirteen case ledgers
completed cleanup, and an independent exact-ID audit confirmed absence of all
fifteen new stopped containers and thirty volumes. The failure remains recorded.

The correction changes only that assertion to require one exact result record,
the exact container ID and the full `Counter` of volume IDs. Existing ownership,
refusal, exact absence and idempotence checks remain in place. A private
deterministic reproducer permuted only the runner's second Docker inspection of
a fresh owned container: the original assertion failed and the corrected one
passed. Both runs finalized their own resources. The full stopped-cleanup scope
then passed **13/0**, starting zero containers and connecting to zero databases;
its fifteen container and thirty volume IDs were independently confirmed absent.
The application and cleanup runner are unchanged. Private receipts are under
`artifacts/stream-variants/cleanup-order-fix/`; a new CI run is required for the
corrected test source.

That correction was pushed as `5fcf99c`. Its distinct
[run 43397137741](https://github.com/shubolab/opennavicat/actions/runs/43397137741)
passed the database-security job and five native-target jobs. The selected
security artifact independently qualified Window **19/0**, backend **46/0**,
ordinary TLS **24/0**, stream TLS **2/0**, stopped cleanup **13/0** and ordinary
collections **511/0**, each with its own exact manifest, Git blobs and cleanup
receipts. The complete run still failed: macOS Intel's source suite had
**2,195 passed / 1 failed / 640 conditional skips**. Its pending-Promise test
expected a semantic diagnostic under a 100 ms total execution budget but
actually received the safe cancellation diagnostic. The original result is
retained, not relabelled as a transient success.

The parent deadline begins before native child creation and bootstrap. A
controlled 180 ms startup delay before executing the same verified native
helper reproduced the classification/deadline race without a database or UI:
the original pending test failed, and the corrected pending test passed.
Semantic diagnostics now use the existing 1,500 ms total budget; the infinite
loop and separate 100 ms watchdog/blocked-host tests keep their short limits.
The elapsed-time, exact-child cleanup and memory assertions are unchanged;
neither the runtime nor its cancellation deadline was relaxed. All **seven**
actual native error cases passed with that startup delay. A separate native-VM
scope passed **103/0**; that scope does not include the modified
`test_js_engine.py` module. Private verification and an independent source/JUnit
check are under `artifacts/stream-variants/js-diagnostic-budget-fix/`.
The original CI did not record startup phases, so the controlled reproduction
does not establish which phase was slow in that particular CI failure.

The later [AP18-13 reference slice](ui-reference-18-mongodb-pipeline.md#ap18-13-collection-images-offon-and-required-image-errors)
separately observes six image OFF/ON cases and two zero-position error sentinels.
It does not establish complete initial-call cardinality or resolve its blank
Output states. Full `fullDocumentBeforeChange`, `whenAvailable`, `required`,
image enablement and retention, unavailable/deleted lookup documents, broader resume and invalidation
behavior, permission failures, other topologies, successful Preview workflows,
and reference-backed acceptance on Windows/Linux/macOS remain open. The
existing [parity matrix](parity-matrix.md) remains authoritative for the complete
goal.
