# Navicat 18 MongoDB aggregation-pipeline runtime observation

Reference: `/Users/wangshubo/Downloads/Navicat Premium.app`, verified as
**18.0.5 build 321**. The observation used an owned, no-auth, loopback-only
MongoDB **8.0.34** container and the generated `pipeline_reference.sales`
collection with four synthetic documents. No existing Navicat profile, saved
password or business database was opened.

## AP18-01 Entry, source and graph

Selecting the generated database and its `聚合管道` category exposed a
`新建聚合管道` list action. The main MongoDB toolbar also contains `管道`.

A new pipeline opens as a main-workspace tab with:

- `管道` and `脚本预览` top-level tabs.
- `保存`, `导入查询`, `预览全部`, `运行`, `解释` and focus-mode toolbar actions.
- A left-to-right `源 → 阶段 1 → 输出` graph.
- A default `$match` stage and sample guidance below the graph.
- `源集合:` selector; the owned fixture listed `sales`.
- `随机` preview mode, `文档` sample size 1000, and a note that preview sample
  options do not apply to real execution.
- Separate sample input and output preview panes.

## AP18-02 Stage catalog and editor

Selecting the default stage exposed a `运算符:` combo, `创建工具` and `编辑器`
radio choices, input/output previews, and a stage preview action. The
`$match` create tool starts with a `点击“+”以添加匹配条件` row. The raw editor
shows a stage-specific help comment.

The actual operator list contained 42 entries in this order:

```text
$addFields $bucket $bucketAuto $changeStream $changeStreamSplitLargeEvent
$collStats $count $currentOp $densify $documents $facet $fill $geoNear
$graphLookup $group $indexStats $limit $listLocalSessions
$listSampledQueries $listSearchIndexes $listSessions $lookup $match $merge
$out $planCacheStats $project $redact $replaceRoot $replaceWith $sample
$search $searchMeta $set $setWindowFields $sharedDataDistribution $skip
$sort $sortByCount $unionWith $unset $unwind
```

The stage `+` control adds a new default `$match` stage. Right-clicking a stage
offers `删除` and requires separate confirmation. The selected stage's editor
accepted `{"region":"north"}` and `预览全部` returned two matching documents;
the graph showed green completion indicators on source, stage and output.

The input preview showed all four generated documents. The selected-stage output
showed the two north documents. Script preview generated:

```javascript
db.getCollection("sales").aggregate([
  {$match: {"region":"north"}}
]);
```

## AP18-03 Run, result and save sheet

`运行` left the pipeline mode and opened `消息` and `结果 1` tabs. The result grid
showed the two matching synthetic documents with generated ObjectIds and fields.

The save action opened a Name sheet with:

- `名称:`.
- read-only `脚本预览:`.
- `不再显示脚本预览`.
- disabled Save until a non-empty name was entered.

The sheet was cancelled. The generated unsaved pipeline was then closed and
discarded. This observation does not claim compatibility with the reference's
serialized `.nagg` bytes.

## AP18-04 Visual Explain

AP18-04 used the same exact-owned fixture with four synthetic `sales`
documents. The pipeline contained one `$match` stage with
`{"active":true}`. Activating `解释` left pipeline mode and opened a top-level
`解释 1` tab containing five inner switches: `可视化`, `JSON`, `网格`,
`统计信息` and `信息`.

The Explain toolbar exposed `耗时阈值(%): 50`, `缩小`, `放大`, search and
`导出图像`. The visual view initially showed a `COLLSCAN` node labelled `10`.
Selecting the node displayed its server metrics and unknown plan fields:

```text
stage: COLLSCAN
executionTimeMillis: 10
stageExecutionTimeMillis: 10 (100.00%)
nReturned: 3
isCached: false
namespace: pipeline_reference.sales
filter: { active: { $eq: true } }
works: 5
advanced: 3
needTime: 1
docsExamined: 4
```

The `JSON` view retained the complete server response, including
`queryPlanner`, `executionStats`, `queryShapeHash`, `command`, `serverInfo`,
`serverParameters` and `ok`. The `网格` view presented the top-level response
fields as columns with document/field counts. The `统计信息` view contained a
grouped `节点类型 / 计数 / 耗时 / 耗时 (%)` table and showed `COLLSCAN`, `1`,
`10.00`, `100.00%`. The `信息` view showed query timing, generated connection
and database names, a retrieval timestamp and the generated aggregate script.

OpenNavicat consumes this observed contract while retaining the complete raw
reply. This observation does not claim that every MongoDB operator, shard tree,
profiler response or visual cost rule has already matched the reference.

## AP18-05 Saved `.nagg` and preview sample modes

AP18-05 used a new exact-owned MongoDB 8.0.34 fixture with synthetic `sales`
and `customers` collections. A `$lookup` joined `sales.customer` to
`customers.customer` and produced `customer_docs`. Saving it through the normal
Name sheet created a **588-byte JSON** file with a `.nagg` extension. Its
compact top-level order was:

```text
Version
Collection
PreviewSourceOption
Stages
Option
```

`Version` was `"0.0"`. `Collection` stored the source name. Each stage stored:

```json
{"Op":"$lookup","MQL":"{\"from\":\"customers\",\"localField\":\"customer\",\"foreignField\":\"customer\",\"as\":\"customer_docs\"}","Enabled":true}
```

Thus `MQL` is an escaped Canonical-EJSON-capable string rather than a nested
object. `Option` contained the observed plain-JSON defaults:
`allowDiskUse=0`, `cursorBatchSize=-1`, `maxTimeMS=-1`,
`bypassDocumentValidation=0`, the collation/read/write fields, and empty
`hint`, `comment`, `w`, and `let` values.

The reference also saved stage-editor help text as JavaScript comments inside
`MQL`. A default `$match` could therefore contain a `/* ... */` description
around its `{}` value. OpenNavicat now preserves that text unchanged on import
and writes the same text back while it remains unedited; runtime parsing strips
comments only outside quoted strings.

The source-stage popup offered `全部`, `最初` and `随机` in that order. With the
sample size disabled for `全部`, saving each mode established the exact mapping
`全部=0`, `最初=1`, `随机=2`; the saved size remained `1000`.

Selecting `$lookup` did not expose a `创建工具` radio control. It opened the
stage editor with a detailed `$lookup` help template instead. The same result
was observed for the checked `$bucket`, `$bucketAuto`, `$facet`,
`$graphLookup`, `$unionWith`, `$sortByCount`, `$redact`, `$replaceRoot`,
`$replaceWith`, `$geoNear`, `$fill` and `$densify` stages. This corrects the
earlier assumption that each complex stage required a separate structured
create tool.

The generated saved object was removed after recording its structure. The
container and its anonymous volumes were also removed after checking the exact
ID, owner label and loopback port.

For the reverse direction, OpenNavicat generated the same 588-byte contract for
a new synthetic `$lookup` draft. Navicat 18.0.5 discovered it in the generated
aggregation-pipeline object list and opened it as a normal pipeline: the graph
showed `源 → 阶段 1 $lookup → 输出`, the source was `sales`, and the preview mode
was `全部` with the sample-size input disabled. This proves bidirectional
semantic import for the observed Version 0.0 shape, in addition to exact bytes
for the generated fixture. The transferred file, generated profile subfolder,
container and anonymous volumes were removed after verification.

## AP18-06 Pipeline Options

AP18-06 used another exact-owned MongoDB 8.0.34 fixture and opened the
reference's `选项` pane from the pipeline canvas. The form order was:

- `允许使用磁盘`
- `光标批处理大小`
- `最大时间（毫秒 ms）`
- `绕过文档验证`
- `读关注级别`
- `区域设置`
- `强度`
- `大小写级别`
- `大小写优先`
- `数值排序`
- `替代`
- `最大变量`
- `逆向`
- `规范化`
- `提示`
- `注释`
- `写关注` with `w`, `j` and `w 超时`
- `Let`

The boolean-style popups used blank/`true`/`false`; their saved ids were
`0`/`1`/`-1`. `读关注级别` saved blank/local/available/majority/linearizable as
`0` through `4`. The collation controls used the same ordered locale values as
the collection collation page. In the checked fixture, `strength=Tertiary`,
`caseLevel=true`, `caseFirst=lower`, `numericOrdering=true`,
`alternate=shifted`, `maxVariable=space`, `backwards=true` and
`normalization=true` saved as `3`, `1`, `2`, `1`, `2`, `2`, `1`, `1`.

The Name sheet's script preview showed these options as a second aggregate
argument, including `allowDiskUse`, cursor `batchSize`, `maxTimeMS`,
`bypassDocumentValidation`, `readConcern`, `hint`, `comment`, `writeConcern`
and `let`. The saved Version 0.0 `Option` document retained `w` and `let` as
strings rather than nested JSON values.

OpenNavicat's Options pane now edits this reference-shaped surface and applies
the mapped disk, batch, timeout, read concern, collation, hint, comment, write
concern and `let` values to real preview, execute and Explain paths. Unknown
Option fields remain intact. The generated reference object, exact-owned
container and anonymous volumes were removed after verification. Exact
server-specific behavior for every topology and every unknown/future option
remains open.

## AP18-07 Stage editor templates and create-tool availability

AP18-07 selected every one of the 42 operators in a fresh exact-owned pipeline
and switched to the reference's `编辑器` control when it existed. Every operator
provided an operator-specific Help template in `MQL`; the templates are now
recorded in the application's stage-template module and displayed for new
stages.

Only these five operators exposed a `创建工具` switch:

```text
$match
$project
$sort
$limit
$skip
```

The other checked operators used the editor template directly. This corrects the
earlier implementation assumption that `$group`, `$addFields`, `$set`, `$unset`,
`$unwind`, `$count` and `$sample` also required separate create-tool toggles.
OpenNavicat retains the raw editor and underlying functionality for every stage
while matching the reference's five-tool availability.

The generated pipeline was discarded without saving. The exact-owned
container and anonymous volumes were verified by identity and removed.

## AP18-08 Multi-node visual Explain canvas

AP18-08 used a fresh exact-owned fixture with 12 synthetic documents and a
`$match(active=true) + $group` pipeline. The visual Explain view rendered a
left-to-right node canvas rather than a table:

```text
project → project → group → project → filter → scan
```

Each node used an icon card, a small elapsed-time badge, a lowercase operator
label and a thin connection to its child. `filter` and `scan` were highlighted
at the default 50% cost threshold while zero-stage-time nodes remained neutral.
Selecting `group` outlined the node, showed a small floating summary and opened
a bordered detail pane to its right containing the exact server fields:

```text
stage: group
executionTimeMillis: 2
stageExecutionTimeMillis: 0
nReturned: 3
planNodeId: 3
opens/closes/saveState/restoreState/isEOF
groupBySlots, expressions, mergingExprs
usedDisk, spills and spill byte counters
```

The detail pane kept all unknown BSON values through Canonical EJSON. The
canvas toolbar retained the observed threshold, zoom, search, export-image and
pan behaviour.

OpenNavicat now uses a native `QGraphicsView` canvas with the same left-to-right
stage ordering, curved parent/child connectors, elapsed badges, threshold
highlighting, search dimming and a floating server-detail panel. The generated
pipeline was discarded, and the exact-owned container and volumes were removed
after identity verification.

## Boundaries

This round did not complete an automated reference drag operation, every
stage's editor template and option execution, every visual Explain operator/shape,
automatic-task references, export of every result type, saved-pipeline
reopening, concurrent peer changes, all server/topology variants, or
Windows/Linux reference layouts. OpenNavicat's
corresponding stage drag and `$match` create tool are implementation results,
not proof of unobserved reference behavior. The fixture was disconnected and removed
after verifying its exact container ID, owner label and loopback port.
