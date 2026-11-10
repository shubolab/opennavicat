# Native MongoDB JavaScript process prototype

This independent `work/mongodb-js-engine` development branch starts at reviewed
main `165d491`. It retains that base's version and does not change main, existing
releases or the retained Dell Windows GUI fixture. The first foundation
checkpoint is `20e1118`. Subsequent checkpoints integrate the native query page,
language migration and helper packaging only on this feature branch; see
`mongodb-js-integration-plan.md`. It is not merged into the main application.

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
Final expressions returning a managed find/aggregate cursor are also executed
and materialized; an explicit `.toArray()` is not required. A terminal frame is
accepted only if the child subsequently exits successfully. A late native
failure retains confirmed command receipts and is not returned as query success.

The tagged bridge distinguishes Int32, Int64, Double (including negative zero),
Decimal128, ObjectId, binary subtype, millisecond dates beyond JS Date's range,
Regex, Timestamp, Code/scope, DBRef and Min/Max. Literal `$date` and `__proto__`
fields remain data. Every database response must re-encode to identical BSON
bytes through both native BSON and the tagged bridge before delivery. Unsupported
raw BSON, duplicate keys and lossy representations fail closed at the native
codec boundary. Actual Navicat18 own-probe wire evidence now establishes that
fresh ordinary JS Number is BSON Double, including integral and already-rounded
unsafe literals. Explicit Int32 and Long stay distinct. Only known command/API
control positions (e.g. find limit/batchSize) receive integral conversion; user
document fields named limit/batchSize/writeConcern are never recursively coerced.
ECMAScript Number/Date rounding already in the script cannot be recovered.

Ordinary JS integer-looking object keys follow the real reference's JS order:
an existing BSON `['2','1','a']` object became `['1','2','a']` after original-UI
findOne/replaceOne. This is an explicit limit to any blanket raw-BSON preservation
claim. Original Int32 fields read as `typeof number` and `===1`, but a whole host
document read/copy/insert still retained Int32 on the wire. The current prototype
reads Int32/Double as primitive numbers and retains private original BSON type
metadata only through a marked host-document root. A second controlled
original-UI round established that field extraction into a fresh root, embedding
the whole nested/array component in a fresh root, slice and spread all promote
read Int32 values to Double. Only the unchanged original host root retains them.
Assignments invalidate the affected path even if the new value is numerically
equal: same-value assignment, changed value, delete/re-add, component replacement
and delete/re-add components all serialized plain Numbers as Double. Explicit
Long stays Long; plain replacement Number becomes Double. These are actual wire
observations, not evidence for a particular private implementation mechanism.
The independent implementation uses private WeakMaps and proxy traps to record
successful set/define/delete operations. Original metadata flows only through
an unchanged path below a real host root. A same-value assignment invalidates
that path; replacing a component disables the old subtree context. Fresh root
objects do not inherit metadata merely by referencing a nested object or array.
Explicit BSON wrapper values remain typed objects. Metadata is never an added
document field or an authorization token.

Only managed cursor documents are currently marked as host roots. Introspection
matches primitive number values, ordinary writable/enumerable/configurable data
descriptors, plain nested/array prototypes and a distinct opaque host prototype.
The original private prototype member `forwardToCustomFunction` was not invoked
or specified, and is deliberately not fabricated here. Full private prototype
API behavior, other command-specific document-return shapes, and adversarial
proxy/primordial interactions remain separate compatibility/audit gates.
The actual original exposed neither public Double nor DateMS, so both are private
codec constructors here. Original `String(Long(...))` was `Long("...")`, now
matched; the full Long arithmetic/constructor behavior is not yet qualified.

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

An important retained counterexample corrects the earlier offline POC's scope.
QuickJS-NG's Error construction falls back to JS null if allocating the Error
itself fails. A newer bootstrap with the same preserved official engine helper
made `try { allocate until OOM } catch {} 42` actually succeed. The original 39
cases passed their fixed corpus; they did not prove a universal uncatchable-OOM
guarantee. The ignored counterexample receipt preserves exact helper/bootstrap
hashes, source text and successful 42 result, and the old report has an addendum
while its original bytes/logs/binary are preserved.

The controlled helper therefore uses the official `JS_NewRuntime2` allocator
interface. Its allocation quota, aligned block headers and irreversible failure
bit live outside JS. The first failure emits a nonce-bound resource notification
without allocating a JS Error. The parent marks the execution denied and refuses
any further database operation; the native bridge also checks the quota flag.
The final result cannot turn resource exhaustion into success even if JS catches
the null fallback. Real tests prove that an earlier confirmed insert remains,
later reads/writes never dispatch, and the helper is reaped. The quota includes
the allocator headers and engine allocation arenas, not total OS RSS or parent
memory. Official engine source is unchanged; no unmerged engine patch is used.

The native process layer now has Windows binary pipe mode/QPC and exact Popen
kill/reap with CREATE_NO_WINDOW; POSIX uses monotonic clocks and an isolated child
session. Killing targets the exact Popen child on all platforms: group lookup
showed a deadline/natural-exit race on macOS and is not needed by a helper with
no process-spawn API. A bounded reader thread handles pipes on all systems. Temporary
directories are removed only after child reaping/handle close, including Windows.
Only the local macOS ARM64 execution is established until the independent
six-target feature-branch qualification job actually passes; none is packaging
or installed-app acceptance. The helper runs without a DB via `engine.evaluate`
for native syntax/typed-IPC/cancellation testing with only PyMongo/sqlparse/pytest.
The prior checkpoint `e485e15` actually passed all six source/helper targets in
[run 42849579454](https://github.com/shubolab/opennavicat/actions/runs/42849579454):
52 cases per target with zero skips and native PE/ELF/Mach-O architecture checks,
including Windows no-console state, all-byte binary pipes and frame boundaries.
Its Ubuntu22 fresh MongoDB job passed 87 cases with verified exact cleanup. That
historical success does not qualify the subsequent allocator/runtime changes;
each new source checkpoint runs the independent complete qualification again.
After the allocator alignment fix, exact checkpoint `9b6ea99` passed
[run 42854765118](https://github.com/shubolab/opennavicat/actions/runs/42854765118):
55 zero-skip cases on each native target and 94 real protocol cases, including
irreversible resource denial and preservation of already confirmed writes. That
source qualification is retained separately from the subsequent host-metadata
change and does not claim installed-app or full-JavaScript parity.
Runtime allocation limits cover QuickJS memory, not total process/parent RSS.
No host APIs is not an OS sandbox or proof against C-engine vulnerabilities.
The JS wrapper fails closed for several mutated intrinsics, but is not a complete
hostile-proxy/primordial security audit. Complete shell globals, prototype
semantics, lazy cursors, generated ObjectId, transactions, streaming, full
Navicat helper/async behavior and six-platform installed-app paths remain open.
The standalone engine has a public Promise, unlike the observed original's
`typeof Promise === 'undefined'`; async completion/global parity is not closed.

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

`test_js_host_reference.py` adds synthetic real-server regressions derived from
both original-UI own-probe rounds. A second independently owned container covers
the host metadata cases; seed BSON remains unchanged after every copy/edit.
The VM-only six-target lane also exercises three metadata variants through real
native framed IPC with a synthetic parent response. Those tests explicitly have
no database/network and never stand in for fresh MongoDB write acceptance.

The separate earlier 39-case offline qualification evidence remains untouched in
the Mongo development worktree's `artifacts/quickjs-qualification` directory.
Windows GUI write/query/GridFS acceptance is deferred by the user; the remote
app/desktop/WSL fixture remains retained and is not used here.
