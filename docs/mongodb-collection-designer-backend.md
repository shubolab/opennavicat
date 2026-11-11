# Ordinary MongoDB collection designer backend checkpoint

This independent branch starts at `f9c492362afe750510b3c01057bc794991d35ad1`.
It adds a backend and fresh-service qualification, without changing main, the
shared 0.9 tree, versions, UI, packaging or published installers. **The original
Navicat collection designer pages and save behavior have not been observed.**
M18-02/03 in [the existing reference record](ui-reference-18-mongodb.md) establish
the presence of new ordinary/time-series, design and delete menu entries only.
MongoDB command documentation is not substituted for that missing UI evidence.

## Contract

`opennavicat.mongodb_collections` provides:

```python
new_collection(session, database, name="") -> CollectionSnapshot
load_collection(session, database, name) -> CollectionSnapshot
plan_collection(session, snapshot, definition) -> CollectionPlan
apply_plan(session, plan, *, cancelled=None) -> CollectionApplyResult
```

- `CollectionDefinition(name, options)` is a detached draft. Equality compares
  BSON representations, preserving integer types, nested field order and values.
- `CollectionSnapshot` retains complete catalog BSON, the collection type and
  UUID, a detached definition, issues/read-only status and a session/config-bound
  fingerprint and token. Raw bytes and option values do not appear in its repr.
- `CollectionPlan` binds the original snapshot, exact draft, command list and
  warnings. `command_text` is Canonical Extended JSON only after a byte-exact
  BSON roundtrip proof. An EJSON-wrapper collision instead yields an explicitly
  labelled base64 BSON envelope; neither representation is executable JS.
  `command_script` uses the existing verified BSON constructor formatter and
  raises `UnsupportedScriptValue` when it cannot express the command exactly;
  it never silently changes BSON to obtain a script.
- `CollectionApplyResult` carries a fresh snapshot and `CollectionOutcome`.
  `CollectionApplyError.outcome` preserves status, an available raw reply,
  cancellation and a bounded fresh observation even when saving fails.

The tokens cover complete snapshot/plan contents, the Mongo session generation
and configuration binding. Changing a command, warning, draft, snapshot,
endpoint, read-only flag or session invalidates it before network access. Raw
commands cannot be submitted through `apply_plan` without a valid reviewed plan.
Recovery reads also revalidate this binding before and after their catalog
access; an endpoint/session change after acknowledgement cannot retarget the
observation or attach a new session's snapshot to the old request.
Callers must run all catalog/plan/apply work on their serial database worker;
this module does not add a second connection owner or perform any GUI I/O.

## Supported first scope

New **ordinary** collections accept `capped`, `size`, `max`, `collation`,
`validator`, `validationLevel` and `validationAction`. Capped size is currently
restricted to whole 256-byte units; the API does not pretend a server-rounded
value is the exact original input. BSON integer controls reject bools and
overflow. Definitions are limited to 1 MiB, reject unsupported values and
submillisecond datetimes, and must decode/re-encode to identical BSON.

Existing ordinary collections support changing validation rules, level and
action using one `collMod` command. Removing a field explicitly resets it to
the documented default: empty validator, strict level or error action. A no-op
does not issue DDL. Reordering top-level control options alone is a no-op; nested
validator representation remains exact. Renaming, changing capped/collation or
other immutable options never invokes drop/recreate.

Collections with unqualified additional options, non-reversible BSON, missing
UUIDs, views, time-series, internal namespaces and GridFS-managed names remain
read-only with complete original bytes retained. No-op inspection stays
available. This is an explicit checkpoint boundary, not a claim those required
database tools are out of scope permanently. Advanced storage/encryption,
clustered collections, time-series/view design, rename/drop/copy/maintenance and
the original UI remain open.

The first qualification uses MongoDB **8.0.34**, not the current unversioned
manual's newer features. `validationLevel=constraint` is not silently enabled.
See the [8.0 create command](https://www.mongodb.com/docs/v8.0/reference/command/create/)
and [8.0 collMod command](https://www.mongodb.com/docs/v8.0/reference/command/collmod/).

## Identity, concurrency and create idempotency

Loading reads only the exact requested catalog row through PyMongo's public
`cursor_command` with an explicit raw BSON codec. Its cursor is closed in all
paths and retains the driver's serving connection/session management. Planning
performs metadata reads; it does not execute DDL or a save probe.

Apply checks the complete original catalog again. Existing-collection writes
then verify the server's UUID protection with a wrong UUID and an empty
`collMod` that changes no option, and recheck the catalog before the actual
command. A server that ignores/rejects the required guard is blocked without
falling back to UUID-free DDL. The actual command carries `collectionUUID`.
Its presence is confirmed in the fixed
[MongoDB 8.0.34 IDL](https://github.com/mongodb/mongo/blob/r8.0.34/src/mongo/db/coll_mod.idl#L182-L186)
and exercised by a real namespace replacement between final check and command.

**UUID is not options compare-and-swap.** A peer change observed before apply
is rejected. A peer can still change the same property after the last catalog
read; the explicitly selected value can overwrite it. A real race regression
records this limitation. Only edited options are sent, so unrelated concurrent
options are not rewritten. Readback verifies only explicitly changed options,
retains the complete new catalog, and does not advertise atomic CAS.

For create, a peer collection observed before dispatch is rejected. A peer can
create an identical collection in the final race window, however, and MongoDB
can acknowledge our create idempotently. Therefore `created_by_this_plan` is
**always unknown (`None`) for create**, including ordinary successful readback.
Neither the absence precheck nor the command acknowledgement proves exclusive
creation. A real race test inserts a peer marker before the idempotent command
and proves that data survives. Different peer options produce the actual server
error and a fresh observation. No failure, cancel, stale plan or readback path
ever deletes a collection, retries a write, or claims DDL rollback.

## Acknowledgement and cancellation

| Status | Meaning |
| --- | --- |
| `not_started` | No requested mutation was dispatched; metadata/guard may have run |
| `acknowledged` | The requested command reply arrived successfully; final observation can still fail |
| `server_error` | A definite server error reply arrived; this is not a guarantee of zero effects |
| `unknown` | Transport acknowledgement or write concern is uncertain |
| `no_change` | Snapshot was current and no DDL was needed |

The reply and fresh observation are separate facts. An acknowledged command
whose postcondition cannot be checked raises an error while retaining
`acknowledged`; a successful observation after a lost reply does not upgrade
`unknown`. Labelled injected network/concern errors occur only **after real
fixture creation**, so these tests do not claim replica-set failure acceptance.

A queued `cancelled()` callback prevents all database/control access. At command
boundaries it stops the next mutation. An acknowledgement arriving before cancel
stays acknowledged and is not rolled back. Inflight server cancellation uses
the existing captured `cancellation_marker` / `cancel(expected_comment=...)`
contract. A real lock-wait/killOp test checks completion, preservation of the
pre-save options and subsequent connection reuse. The outcome captures the
cooperative cancel flag before fresh observation starts another operation.

## Qualification and remaining gates

```sh
PYTHONPATH=. python scripts/test-mongodb-collections.py \
  --evidence artifacts/mongo-collections/a-new-run
```

This standalone runner dynamically collects every parametrized case in
`tests/test_mongodb_collections.py`, requires the exact complete set with zero
skips/errors/failures, creates one new authenticated loopback-only MongoDB
container and verifies native image/daemon architecture, readiness and exact
identity/cleanup. It reuses the existing receipt-backed, run-label-specific
emergency cleanup for timeout/interrupt. It never accepts a reference endpoint.
Logs/receipts exclude Docker environment dumps and private certificate/key/URI
files. The existing shared release protocol runner is unchanged; this new scope
is not claimed to be part of released CI until explicitly integrated.

The local proof is native macOS ARM64 Python/PyMongo with an ARM64 MongoDB
container. It does not qualify Windows/Linux helpers, packaged integration or
six native installers. Existing fixtures/reference data, user workspaces,
Navicat configuration and the retained Dell environment are not used.

Before integrating a designer, collect the original page/default/enablement,
preview/save/cancel/error/reopen evidence on an owned fixture. Then wire a
worker-only page, explicit preview confirmation, dirty-close/disconnect,
captured cancellation and namespace-bound restoration. History-profile review
must remain in force; no imported source or unsupported auth/topology is
automatically unblocked by this backend.
