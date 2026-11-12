# MongoDB ordinary-collection options

The `集合选项` tab follows the controls observed in Navicat Premium 18.0.5
build 321 for a new ordinary collection:

- `固定集合`
- `最大大小（字节）` and `最大文档数量`
- `聚集集合`
- `聚集索引名称`
- `在此之后过期（秒）`
- `更改流前图像和后图像`
- `自动索引 ID`
- `使用 2 的次方的大小`
- `无填充`

Fixed and clustered collections are mutually exclusive. A fixed collection
requires a positive size in 256-byte units. A clustered collection uses the
required MongoDB shape `{key: {_id: 1}, unique: true}` with an optional name and
can carry `expireAfterSeconds`. Existing collections show capped, clustered,
expiry and automatic-ID controls disabled; change-stream images remain mutable.

## Server-backed behavior

The regression uses the pinned MongoDB 8.0.34 owned fixture. A MainWindow save
created a clustered collection with a named cluster key, 60-second expiry,
change-stream pre/post images and `autoIndexId=true`. An independent
`listCollections` read confirmed the normalized catalog. Switching images off
through `collMod` removed the stored option, preserved the collection UUID and
kept the generated document.

The reference also exposes two legacy MMAPv1 controls. OpenNavicat preserves
`usePowerOf2Sizes` and `noPadding` as explicit draft options and sends them only
when the user changes the observed defaults. MongoDB 8.0.34 rejects both as
unknown `create` fields. `autoIndexId=false` is also sent only when selected and
is rejected by the server for replicable collections. These are retained as
real server-error workflows rather than being silently dropped or presented as
accepted. The same rule applies to later server versions whose supported options
may differ.

The defaults `autoIndexId=true`, `usePowerOf2Sizes=true` and `noPadding=false`
are not emitted while unchanged. This follows the reference's empty script
preview after restoring its initial controls.

## Verification boundary

Unit and Cocoa tests cover labels, defaults, mutual exclusion, generated option
documents, existing-collection enablement and server-error retention. The
expanded collection runner passed **257 cases with zero skips/failures/errors**
across seven exact-owned fixtures; the focused Cocoa designer/MainWindow run
passed **34 cases**. The full source suite passed **1792 cases with 558
conditionally skipped**, and source smoke returned 6 rows × 4 columns.
Windows/Linux reference screenshots, additional MongoDB versions and
cluster/topology-specific behavior remain open gates.
