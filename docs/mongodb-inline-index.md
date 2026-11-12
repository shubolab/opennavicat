# MongoDB embedded index editor

The ordinary collection designer now uses the reference's embedded index table
for existing collections. The table has a leading index icon plus `名称` and
`键`; selecting a row enables the observed `唯一键`, `背景`, `稀疏`,
`在此之后过期（秒）` and `存储桶大小` controls. `新建索引` adds an unsaved row,
while `删除索引` removes a new row or stages an existing index for deletion.

The key button opens a nested `名称 / 类型` table with `添加`, `移除`, `好` and
`取消`. Supported directions are ASC, DESC, 2D, 2DSphere, Hashed and Text.
Duplicate or empty keys are rejected in the dialog instead of leaking into an
empty-direction index command.

## Live reference observation

The owned macOS/MongoDB 8.0.34 fixture confirmed that an existing inline index
can be selected and edited. Renaming `label_1` to `label_edited` produced:

```javascript
db.getCollection("items").dropIndex("label_1")

db.getCollection("items").createIndex({
    label: Int32("1")
}, {
    name: "label_edited"
})
```

Save closed the designer and independent PyMongo confirmed only `_id_` and
`label_edited`, with all 20 generated documents retained. The script is two
non-transactional commands. A separate empty-direction draft was retained after
the server returned `[67][CannotCreateIndex]`, and independent PyMongo confirmed
neither an invalid nor the earlier cancelled draft index was written.

CUA accessibility changes to the type combo could report a selected value while
the original application's script model still contained an empty direction.
The implementation therefore commits keys from explicit combo data, not from
ambiguous accessibility text.

## Implementation and safety boundary

- Additions, renames and deletions are represented as explicit model rows.
  Existing unsupported options remain visible and make lossy editing read-only.
- Save first generates the full ordered command sequence. Each plan is still
  sealed with its own collection UUID guard and current index fingerprint.
- Embedded plans are applied in table order. They are **not** a transaction:
  the first confirmed steps remain if a later index fails. The UI reports the
  actual completed count and preserves the draft for reviewed retry.
- A rename remains drop-then-create. There is no automatic rollback, retry or
  hidden drop/recreate fallback beyond the command sequence shown in the sheet.
- Collection option changes and index changes use separate reviewed saves.
  A new collection must be created and reopened before its index table can be
  saved.
- All catalog reads and writes remain in MainWindow's serial background queue.
  No widget opens a connection or executes a database command.

The complete U23/O14 and three-platform UI gates remain open while advanced
index options, inline collection-creation sequencing, unsupported existing
options, other server versions/topologies and Windows/Linux reference behavior
remain to be qualified.

## Current verification

On the macOS ARM64 development checkpoint, the full source suite passed
**1768 cases with 551 conditionally skipped and zero failures/errors**, and the source smoke passed
6 rows × 4 columns. The expanded owned MongoDB collection runner passed **234 cases with zero skips/failures/errors**, including the new
inline UI, create/rename/options/delete MainWindow path, six exact fixture
cleanup proofs and the pre-existing collection/time-series/view/copy/maintenance
scope. The protocol/index/query/MainWindow runner passed **301 cases with zero
skips**, including both sequential-success and partial-failure backend cases.
Native Cocoa passed **38/38** for the embedded UI, collection designer and real
MainWindow save path. These are source/runtime checks, not a new six-target
release or Windows/Linux reference acceptance.
