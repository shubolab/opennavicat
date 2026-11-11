# Ordinary MongoDB collection storage checkpoint

This checkpoint adds backend support for two creation options, without claiming
complete storage-engine support or reference UI acceptance. The existing
`CollectionDefinition.options` API is retained:

```python
{
    'storageEngine': {'wiredTiger': {'configString': 'block_compressor=zlib'}},
    'indexOptionDefaults': {
        'storageEngine': {'wiredTiger': {'configString': 'prefix_compression=false'}}
    },
}
```

Only the shown WiredTiger `configString` envelopes are qualified. The string is
passed verbatim, including an explicit empty string. The server validates its
engine-specific grammar. New drafts with unknown engines, extra nested fields,
non-document envelopes or non-string configuration values are rejected locally.
Existing catalog entries with those shapes remain read-only, retaining exact raw
BSON and decoded options; the implementation does not remove unknown fields.
Empty documents and other engine configuration forms still need qualification.

Both options are create-only in this designer. Existing collection validation
changes continue to use UUID-guarded `collMod`, changing only the explicitly
edited validation fields. Attempted storage changes, including removing these
options, are rejected; there is no drop/recreate fallback. Existing snapshot,
profile binding, plan sealing, stale-catalog, cancellation and acknowledgement
contracts remain in force. An acknowledged create still does not prove exclusive
ownership and is never automatically undone.

MongoDB 8 documents these as collection creation options; they are outside Stable
API V1, and collection-level WiredTiger encryption options are disallowed from
7.2. See the official [create command](https://www.mongodb.com/docs/v8.0/reference/command/create/).
The [time-series creation procedure](https://www.mongodb.com/docs/v8.0/core/timeseries/timeseries-procedures/)
also permits both options. That separate designer remains an open gate; this
ordinary-collection checkpoint does not enable its storage configuration.

## Owned-service verification

On macOS ARM64 / Python 3.13.11, the two complete modules ran with
`OPENNAVICAT_TEST_MONGODB=1` and `QT_QPA_PLATFORM=offscreen`:

```sh
.venv/bin/python -m pytest -q -W error \
  tests/test_mongodb_collections.py tests/test_mongodb_collection_copy.py
```

Result: **87 passed, zero failures/errors/skips**, 42.46 seconds. Each module
created a distinct authenticated, loopback-only native ARM64 MongoDB **8.0.34**
fixture. Ownership, native daemon/image architecture, readiness and cleanup were
verified; both complete container IDs were independently checked absent afterward.
Ignored evidence lives under `artifacts/collection-storage/`: JUnit,
`initial-fixtures/` receipts and `backend-checkpoint.json` with exact source hashes.
These results apply to the modified backend sources, not to a prior commit's CI.

Coverage includes:

- Creation and reopening with both storage settings; exact catalog BSON options.
- Actual collection `wiredTiger.creationString`, default index
  `indexDetails.*.creationString`, and an explicit per-index override. These
  are independent native-server reads beyond an echoed create request.
- Validation-only `collMod`, preserving UUID, documents, indexes and storage
  settings; rejection of storage edits without rebuilding.
- Explicit empty configuration strings; server rejection of invalid collection
  and index-default configuration with `server_error`, preserving the server
  code and creating no collection in those observed cases.
- Both structure-only and structure/data staged copies, preserving collection
  storage, index defaults, explicit index overrides, source documents and
  defaults on a subsequently created target index.
- Unknown nested catalog shapes retained read-only, with exact original bytes.

The server's storage diagnostics are internal and version dependent. The observed
strings qualify only the tested MongoDB version and options. There is no claim
of all WiredTiger settings, encryption, other engines/topologies, time-series
storage, original Navicat default/enablement behavior, six-target installers or
full 1:1 UI acceptance. Those gates remain in the parity matrix.

## Integrated page checkpoint

The [actual ordinary-collection observation](ui-reference-18-mongodb-collections.md)
now backs the seven-tab order and default index tab. Two script pages bind
`storageEngine` and `indexOptionDefaults` to the sealed collection save workflow.
They accept one bounded object literal with quoted/unquoted keys, single/double
quoted strings and comments using the existing non-evaluating BSON grammar.
Expressions, query calls, duplicate fields, trailing statements and code-valued
configuration strings are rejected; no JavaScript VM or host evaluation runs.
Blank text omits the option, while an explicit empty config string is retained.

Existing collection storage pages are read-only; validation edits retain their
original storage values. Unknown options retain raw BSON in an additional
read-only fallback dialog rather than a normal designer tab. This fallback has
not been accepted as reference UI for unknown-catalog states.

New collection names are entered in the observed `表名称:` Save sheet. An unnamed
draft has a text-only `db.createCollection("Untitled", ...)` preview; it never
sets a real name, seals a plan or dispatches a database operation. The real Save
still requires the explicitly entered sheet name and confirmed backend plan.
The embedded collection index list omits `_id_` as observed; the server catalog,
metadata counts and standalone index tool continue to retain that index.

Integrated macOS ARM64 checks: **1573 passed / 544 conditionally skipped** in the
default full-source suite and **226 passed / zero skipped/failed/error** in the
collection runner. The latter used six distinct owned native loopback MongoDB
8.0.34 fixtures, independently confirmed absent after cleanup. Evidence is in
`artifacts/collection-storage/integrated/` and `final-source.xml`; qualified source
file hashes match this checkpoint. The full-source service/platform skips are
not counted as live execution, and the overlapping runs are not summed.
The collection/shared-designer modules and two real MainWindow storage paths
also passed **51 tests with zero skips** on the native Cocoa platform. Source
Cocoa smoke passed all driver, crypto and native-JS probes with the isolated
6-row/4-column sample. These are source checks, not packaged or cross-platform
installer acceptance.

The actual reference still differs in the inline index/key editor, JSON Schema
property/root-schema subpages, advanced collection options, structured collation
controls, toolbar states and full preview/DDL modes. These pages and functions,
other engines/object types/versions and Windows/Linux UI remain open. Neither the
seven tab labels nor the two implemented storage workflows close U23 or O14.
