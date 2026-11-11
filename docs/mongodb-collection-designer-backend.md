# MongoDB ordinary and time-series collection designer checkpoint

This branch starts from the released 0.9.0 product tree and adds both the sealed
backend and an embedded MainWindow collection designer. It does not change the
published 0.9.0 installers. **The original Navicat collection designer pages and
save behavior still have not been observed.** M18-02/03 in [the existing reference
record](ui-reference-18-mongodb.md) establish the ordinary/time-series, design and
delete menu entries; the focused NIB report supplies control names and hierarchy.
MongoDB command documentation and those static resources are not substituted for
missing runtime UI evidence.

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

## Embedded designer

`opennavicat.ui.mongo_collection_designer` provides the first ordinary-collection
UI slice backed only by capabilities in this module:

- New and existing ordinary collections open in a dedicated tab with collection
  options, validation, collation, raw server BSON and script preview.
- Name, capped/size/max, validator, validation level/action and collation are
  edited as a detached draft. Capped size is bounded to whole 256-byte units.
- Canonical Extended JSON parsing retains BSON integer and nested value types.
  Invalid JSON, non-document values and integer overflow remain dirty input but
  cannot produce a plan, enable Save or emit a save request.
- The save sheet requires the exact reviewed plan before dispatch. It can show
  the UUID-guarded execution script or raw BSON command; a display-only base64
  BSON envelope is never presented as executable JavaScript.
- Unsupported existing options, non-UUID ordinary collections, views,
  time-series and GridFS-managed namespaces display the retained raw catalog BSON
  read-only. No-op inspection remains available.
- MainWindow owns every `load_collection`, `new_collection`, `plan_collection`
  and `apply_plan` call on its serial database worker. A separate cancel pool sets
  cooperative cancellation and, while a command is active, uses the captured
  Mongo cancellation marker.
- Collection design tabs participate in the shared dirty-close/disconnect flow.
  Saved plans are always revalidated against the current session and snapshot;
  a confirmed plan is not reused after the draft or connection changes.
- The object page enables New/Design Collection, the tree right-click exposes
  Design Collection, and named open designers deduplicate by connection, schema
  and collection. Named existing designers are restorable; unsaved option values
  are not persisted across process restart.
- Drop Collection and Clear Collection are available from the object page and
  tree menu. Both require a permanent-operation checkbox and show the exact
  UUID-guarded command before dispatch. Drop removes only the sealed collection
  identity; a same-name namespace recreated by a peer is not deleted.
- Clear uses `deleteMany({})` semantics with the sealed collection UUID. It
  preserves the collection, options and indexes, warns that concurrent writes
  after the final check are also deleted, and never claims transactionality or
  rollback.
- Rename uses the admin `renameCollection` command with `dropTarget: false` and
  the source `collectionUUID`. The target must be absent during planning; a final
  race can still create it, and no existing target is intentionally dropped.
  Successful rename preserves documents, indexes, collection options and UUID.

- Collection maintenance exposes fast/full `validate` for ordinary and
  time-series collections and `reIndex` for ordinary collections. Every command
  checks the relevant UUID before and after, returns a bounded Canonical Extended
  JSON report, and uses the owner queue/cancel marker. Full validation and
  re-index require explicit confirmation; time-series `reIndex` is rejected by
  the server and remains unavailable in the UI.

- Structure+data and structure-only copy use a random staging collection with
  source options and indexes. Data mode copies with `$out` into staging, then all
  modes use `renameCollection` with `dropTarget: false` and the staging UUID.
  Existing targets are never intentionally overwritten. Copy is not a snapshot
  or transaction, and a failure after staging creation leaves staging for manual
  inspection instead of automatically dropping a collection whose ownership is
  not server-proven.

- Time-series creation and design are implemented separately in
  `opennavicat.mongodb_timeseries` and `ui/mongo_timeseries_designer.py`. Supported
  controls are `timeField`, optional `metaField`, granularity or equal custom
  `bucketRoundingSeconds`/`bucketMaxSpanSeconds`, and `expireAfterSeconds`.
  Bucketing and expiry changes use separate non-transactional `collMod` commands.
  The public time-series namespace is a view without a UUID and MongoDB rejects
  `collectionUUID` on its `collMod`; the designer binds the underlying
  `system.buckets.<name>` UUID, checks it before and after, and explicitly does
  not claim atomic CAS.
- Dirty collection data editors and dirty design drafts block drop/clear before a
  destructive plan is opened. Drop closes matching open collection/designer tabs;
  clear refreshes the open data page and object statistics.

The current tabs and labels are an implementation scaffold informed by static
resources, not a claim that the original runtime order, defaults, enablement or
save workflow matches Navicat. Fields, index, foreign-key and virtual-relation
pages from the resource report are not yet implemented because the current sealed
backend cannot execute those complete workflows.

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

The runner dynamically collects every case in
`tests/test_mongodb_collections.py` plus the real MainWindow collection-designer
test, requires the exact complete set with zero skips/errors/failures, creates
two new authenticated loopback-only MongoDB containers (one per test module) and
verifies native image/daemon architecture, readiness and exact identity/cleanup.
It reuses the receipt-backed, run-label-specific emergency cleanup for
timeout/interrupt. It never accepts a reference endpoint. Logs/receipts exclude
Docker environment dumps and private certificate/key/URI files.

The current Linux WSL amd64 proof passed **94 cases with zero skips** and five
independently cleaned MongoDB 8.0.34 fixtures. The shared database-security CI job
executes this runner. The earlier macOS ARM64 backend-only proof remains
historical and does not cover this UI.

Shared CI [43012789248](https://github.com/shubolab/opennavicat/actions/runs/43012789248)
on main commit `4d4f3312a9908fb615cc551ce9e20116b7cec936` completed all **7 jobs
successfully**: six native desktop targets and the database-security job. The
collection/time-series/copy runner passed inside that job with create/design,
drop/clear/rename, time-series and staged-copy scope, and each desktop job completed source tests, helper qualification,
packaging, installation, launch and uninstall for its target. This is a
main-branch preflight, not a tag, public Release or six-installer download
acceptance. macOS amd64 runs exposed existing JS output/null tests racing helper
startup; both now allow enough startup time to reach their real semantic failure,
while the complete native VM scope passed 101/101 and JS protocol rerun passed
193/193 without skips.

The proof does not qualify Windows/macOS UI against the reference application,
public release assets, or the retained Dell environment. Existing fixtures,
user workspaces and Navicat configuration are not used.

Additional Linux WSL amd64 checks in this checkpoint:

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -W error`:
  **1486 passed, 504 conditionally skipped, 0 failed/error**. Default-service
  skips are not counted as real database execution; the owned runner above
  covers this feature's Mongo scope without skips.
- `.venv/bin/python -m opennavicat --smoke-test`: **passed**, 6 rows / 4 columns,
  Qt 6.12.0, PyMongo adapter/BSON/TLS construction and the pinned native
  JavaScript helper probes all succeeded.
- The new five offline designer tests cover option parsing, 256-byte size gate,
  invalid JSON isolation, preview/save confirmation, unsupported raw read-only
  state and typed error projection.

These source checks do not replace the six native installers, Windows/macOS GUI
acceptance, or the original Navicat runtime comparison.

Before integrating a designer, collect the original page/default/enablement,
preview/save/cancel/error/reopen evidence on an owned fixture. Then wire a
worker-only page, explicit preview confirmation, dirty-close/disconnect,
captured cancellation and namespace-bound restoration. History-profile review
must remain in force; no imported source or unsupported auth/topology is
automatically unblocked by this backend.
