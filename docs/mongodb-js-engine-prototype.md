# Native MongoDB JavaScript process prototype

This independent `work/mongodb-js-engine` development branch starts at reviewed
main `165d491`. It does not change the application version, released 0.6.1,
registry, UI query routing, dependencies, packaging or the retained Dell Windows
GUI fixture. The first foundation checkpoint is `20e1118`. The implementation is
not merged or enabled in the main application.

## API and ownership

```python
from opennavicat.js_engine.runtime import NativeJavaScript, ExecutionLimits, JavaScriptError

engine = NativeJavaScript("artifacts/js-engine/opennavicat-js")
result = engine.execute(
    connected_mongo_session, source,
    database="explicit_selected_database",
    limits=ExecutionLimits(timeout_ms=5000, max_rows=1000),
    cancel_event=execution_specific_threading_event,
)
```

The caller must run `execute` on the session's serial worker, never the GUI
thread. The parent retains the existing `MongoSession`, transport, credentials,
selected database and authority. The helper receives only a fresh per-run nonce,
source, trusted bootstrap and bounded tagged data. It has no Python object,
filesystem/process/network API or `std/os` module loader. JavaScript source is
evaluated only by the official native engine, never by Python `eval`.

Every child request carries the C-held run nonce and increasing request number;
the parent validates both. Every BSON command is decoded again, bounded and
checked by the existing `_validate_command` read-only/routing/session policy.
JavaScript wrappers are convenience syntax, not authorization. The pinned build
receipt and helper SHA must match before launch. Source archive, notices and
native architecture details are described in `native/quickjs/README.md`.

Results contain an exact typed value, command receipts and cleanup state.
`JavaScriptError.result` retains those receipts when the script fails. Ordinary
JS completion `undefined` is an explicit `JS_UNDEFINED` sentinel, separate from
BSON null. An undefined value inside a database command remains a rejection.

## Implemented development subset

The actual QuickJS runtime accepts variables, loops, functions, class/arrow
syntax, arbitrary-precision BigInt and async/await with explicit job draining.
The bootstrap provides `db.getCollection(name)` / `db.collectionName`,
`runCommand`, `find`/`findOne`, `aggregate`, and cursor `sort`/`skip`/`limit`/
`batchSize`/`maxTimeMS`/`hasNext`/`next`/`toArray`/`forEach`/`map`/`close`.
Basic `insertOne`/`insertMany`, `updateOne`/`updateMany`, `deleteOne`/`deleteMany`
and `countDocuments` are backed by real native commands. Insert helpers currently
require explicit `_id`; unknown helper options are rejected rather than ignored.
General acknowledged commands can use `runCommand` under the parent policy.

Cursor execution is **eager and explicitly bounded** in this prototype. The
parent drains the native `CommandCursor` with the same logical session/comment
and closes it before returning. JS iteration traverses the resulting exact BSON
documents in memory. Managed `runCommand` cursor replies contain that materialized
`firstBatch` and id zero because the wire cursor is already exhausted/closed.
Exceeding row/BSON delivery bounds produces an error with no fake empty/end or
successful silently truncated result. This is not full lazy/streaming mongosh
cursor behavior. Terminal `$out`/`$merge` writes do not receive an injected result
limit that would alter the requested write.

The tagged bridge distinguishes Int32, Int64, Double (including negative zero),
Decimal128, ObjectId, binary subtype, millisecond dates beyond JS Date's range,
Regex, Timestamp, Code/scope, DBRef and Min/Max. Literal `$date` and `__proto__`
fields remain data. Every database response must re-encode to identical BSON
bytes through both native BSON and the tagged bridge before delivery. Unsupported
raw BSON, duplicate keys and lossy representations fail closed. Default unsafe
integral JS Number is rejected; use Long/BigInt or explicit Double. ECMAScript
Number/Date rounding that already occurred in the script cannot be recovered.

## Partial outcomes and cancellation

Each dispatched command records `acknowledged`, `acknowledged_error`, or
`uncertain`; policy rejection records `rejected`. An unordered duplicate insert
can really write several documents and then fail. Those acknowledged `n` counts
remain in the result and no later unhandled statement runs. A network error after
a write is dispatched, or a write concern failure, is uncertainty. It never means
the write was rolled back. Script `try/catch` may explicitly handle an error and
continue; the receipts retain the earlier error too.

The VM's monotonic deadline, heap cap, stack cap, pending-job budget and output
caps bound engine work. A distinct parent watchdog thread can kill **and reap**
the child even while the host worker is blocked in a DB call. The existing
separate Mongo control client targets only the active nonce comment; PyMongo CSOT
bounds the parent operation. The caller waits for cancellation-thread cleanup
before returning the serial session to another operation. Child cancellation
cannot undo confirmed writes. Cleanup time may extend beyond the execution
deadline, and server cancellation can be denied; outcome receipts must remain
authoritative in those cases.

The current native process layer is POSIX and qualified only on macOS ARM64.
Runtime allocation limits cover QuickJS memory, not total process/parent RSS.
No host APIs is not an OS sandbox or proof against C-engine vulnerabilities.
The JS wrapper fails closed for several mutated intrinsics, but is not a complete
hostile-proxy/primordial security audit. Complete shell globals, prototype
semantics, lazy cursors, generated ObjectId, transactions, streaming, full
Navicat helper/async behavior and all six native build/package paths remain open.

## Reproducing actual tests safely

```sh
python scripts/build-quickjs.py
OPENNAVICAT_TEST_MONGODB=1 \
OPENNAVICAT_MONGODB_PROTOCOL_RUN_ID=your_unique_run_id \
OPENNAVICAT_MONGODB_PROTOCOL_REPORT_DIR="$PWD/artifacts/js-engine/mongo-protocol" \
PYTHONPATH="$PWD" python -m pytest tests/test_js_codec.py tests/test_js_engine.py \
  -q -Werror --junitxml=artifacts/js-engine/results.xml
```

The existing backend fixture creates a **fresh** owned MongoDB 8.0 container on
an ephemeral loopback-only port, verifies exact ID/name/token/task/run labels,
assigns a random database to each test, then removes only those exact resources.
It cannot accept a user/reference endpoint. Fixture receipts and bounded startup
diagnostics are redacted. Native helper tests are explicitly skipped unless the
verified helper is built; real DB tests explicitly require their owned-fixture
opt-in. A skipped run is not native/protocol acceptance.

Local actual tests include typed BSON reads and writes, native getMore/killCursors
with the same lsid/typed nonce comment, duplicate partial writes, read-only
rejection before driver I/O, terminal `$out` preserving all documents, delivery
limits and server-cursor cleanup, VM limits, and cancellation of a real server
sleep query followed by a successful new query. A lost-write-ack test performs a
real insert and deliberately raises a transport error afterward; this proves the
uncertain-outcome branch, not a naturally occurring network failure. Official
engine source and native Mach-O build receipts live under ignored artifacts.

The separate earlier 39-case offline qualification evidence remains untouched in
the Mongo development worktree's `artifacts/quickjs-qualification` directory.
Windows GUI write/query/GridFS acceptance is deferred by the user; the remote
app/desktop/WSL fixture remains retained and is not used here.
