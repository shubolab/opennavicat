# MongoDB JavaScript index helpers

These helpers were developed in the independent `work/mongodb-js-index-helpers`
checkpoint based on curated `36071be` and then integrated for the 0.9 milestone.
The small constructor compatibility fix `e7e894e` already entered 0.8.1; the
eight collection helpers described here did not. Their final integrated source,
installer qualification and remaining gates are tracked separately in
[the 0.9 validation record](validation-0.9.md). Earlier engine installer evidence
does not qualify these newer helpers; the retained Dell fixture remains untouched.

The native collection object now provides these helpers:

| Helper | Successful completion |
| --- | --- |
| `createIndex(keys, options, commitQuorum)` | One index name |
| `createIndexes(keyPatterns, options, commitQuorum)` | Index-name array |
| `ensureIndex(keys, options, commitQuorum)` | Index-name array, matching the tested mongosh alias |
| `getIndexes()` / `getIndexSpecs()` | Materialized index-document array |
| `getIndexKeys()` | Index-key-pattern array |
| `dropIndex(nameOrKeyPattern)` | Actual server result document |
| `dropIndexes(selection)` | Actual server result document; omission explicitly selects all removable indexes |

These return shapes were checked against an actual fixture-owned **mongosh
2.14.0 / MongoDB 8.0.34**, not a mock shell. The corresponding official shell
implementation is pinned for review at [mongosh v2.14.0,
collection.ts](https://github.com/mongodb-js/mongosh/blob/b3cedd7573ce3e7954699be46fe490a25048092e/packages/shell-api/src/collection.ts).
The comparison checks logical control values and shapes. It does not claim that
mongosh's EJSON encoding of ordinary Numbers has Navicat's observed BSON Double
semantics. Native preview filters preserve explicit Int32/Long, dates and binary
subtypes; fresh ordinary filter Numbers remain Double. Only index directions
are converted in their explicit control context.

## Authority and outcomes

Every helper sends a structured command through the existing parent authority.
No database client, credentials or transport reaches the JavaScript process.
Read-only connections reject each write helper before a driver command. Listing
uses the managed native cursor/session path and its explicit delivery bound;
overflow fails rather than returning a false complete array. Server permissions
still apply to the underlying commands. The [official getIndexes
documentation](https://www.mongodb.com/docs/manual/reference/method/db.collection.getindexes/)
identifies `listIndexes` as its required privilege and explains that these are
database indexes, not Atlas Search indexes.

Known server error replies now carry bounded numeric `code` and `codeName` to
the JS `MongoServerError`, including through adapter exception wrappers. The
receipt distinguishes a received error response (`acknowledged_error`) from a
lost network acknowledgement or write-concern uncertainty (`uncertain`). Missing
affected counts remain unknown. A failed index command is not counted as a
confirmed change merely because its error reply arrived. Earlier successful
commands remain acknowledged; no DDL rollback is implied. Tests verify a real
successful first index followed by a unique-index error, unchanged documents,
and no execution of the following statement. A separately labelled injected
lost acknowledgement occurs only after the server actually creates an index;
the result stays uncertain and the real index remains present.
An explicitly labelled injected concern-error reply after a real index creation
also stays uncertain; this is not presented as replica-set acceptance.

## Options and version boundaries

The [createIndex reference](https://www.mongodb.com/docs/manual/reference/method/db.collection.createindex/)
defines keys, index options and optional commit quorum. Supported options include
name/version, unique/sparse/hidden/background, TTL, partial filters, collation,
wildcard projection, text weights/languages, geospatial settings and storage
engine settings. Command-level concern, timeout, comment and quorum are kept out
of the index specification. Typed comments retain the parent's cancellation
nonce. Unsupported options fail before I/O; they are not silently discarded.
This deliberately differs from tested mongosh behavior for an unknown option.
Null options are an empty options document, matching the tested shell.

`dropIndex('*')` and array arguments are refused; callers must explicitly use
`dropIndexes` for multiple/all indexes. The server protects the default `_id`
index. See the [official dropIndex
contract](https://www.mongodb.com/docs/manual/reference/method/db.collection.dropindex/).
Missing index errors and missing-collection listing errors propagate with their
server codes. On the tested MongoDB 8.0.34, an array containing an absent index
returns `IndexNotFound` and retains the existing selection. Current documentation
describes newer-server behavior too; the implementation delegates that behavior
to the server rather than synthesizing success or retries.

The server rejects explicit commit quorum and numeric write concern above one
on the tested standalone fixture before creating the index. These actual
`BadValue` replies are distinguished from the injected concern uncertainty. No
replica-set quorum, sharded deployment, in-progress build, Atlas Search or hidden
Navicat helper behavior is inferred from this result. Ordinary data documents
are supported; argument accessors/unsupported prototypes are rejected before
serialization. Full proxy/primordial compatibility remains a separate gate.

## Reproduction

```sh
python scripts/build-quickjs.py
python scripts/test-js-engine.py --mode protocol --evidence artifacts/js-index-qualification
```

The runner dynamically collects the required nodes, requires zero skips, and
creates three fresh, loopback-only MongoDB fixtures with exact identity/cleanup
receipts. The extra helper test module is part of that declared scope. It never
uses a manual reference endpoint, Navicat configuration or an existing database.
The tests also invoke the actual `mongosh` shipped in the verified official
MongoDB image with `--norc`, only against a new synthetic namespace in that owned
fixture, and remove it afterwards.

These helpers have not yet been observed in the original Navicat UI/runtime.
Its return values, constructor overloads and complete option behavior remain
explicit parity gates; official mongosh comparisons are not substituted for
that missing evidence.
