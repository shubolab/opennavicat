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

## AP18-09 Change-stream stage and batch probe

On 2026-11-13, a new exact-owned native ARM64 MongoDB **8.0.34** standalone
fixture contained four synthetic `pipeline_task_reference.sales` documents.
Changing a generated pipeline's operator to `$changeStream` left a raw editor
containing its existing `{}` body; no Create Tool switch was exposed. Run
entered the Messages view and displayed the aggregate script plus:

```text
The $changeStream stage is only supported on replica sets
at line 1, column 27
```

The edited pipeline title carried `— 已编辑`. This was an actual execution
error, not a resource-label declaration or successful stream observation.

A second new exact-owned fixture was initialized as a single-node replica set
with the same four synthetic documents. The generated reference connection
used Direct/Standalone connection settings to that new loopback endpoint.
The saved `$changeStream: {}` pipeline was referenced twice by a generated
Batch Job. With no new events, both job entries reported success after about
one second each. The native server profiler independently recorded two
`getMore` operations with an originating aggregate pipeline containing
`$changeStream`; each returned zero documents. After the batch reported
completion, an independent `$currentOp` observation still found those two
idle tailable/awaitData cursors. The reference application was normally exited
and the entire exact-owned service was then removed; release of individual
cursors on application exit was not independently measured before teardown.

This bounded probe does **not** qualify a continuous change-stream workspace,
event delivery, resume tokens, cursor lifetime guarantees, stream cancellation,
permissions, sharded/replica-set connection configuration or future batches.
It also does not justify copying the observed idle-cursor retention into an
implementation. The normal finite pipeline path cannot be declared a complete
stream implementation merely by permitting the operator.

The Batch Job reference, file and error-policy observations are recorded
separately as [AU18-01–AU18-06](ui-reference-18-automation.md). Both database
containers and their four anonymous volumes were verified absent after
identity-bound cleanup. Generated local connection/pipeline and empty-job
metadata remain explicitly disclosed in AU18-06; no permanent metadata
deletion, schedule or email action was performed. This is CUA runtime
observation AP18-09, not a REA Evidence ID. No REA session was opened.

## AP18-10 Independent change-stream execution and Stop

On 2026-11-13, a fresh exact-owned native ARM64 MongoDB **8.0.34** single-node
replica set contained four generated `pipeline_stream_reference.sales`
documents. Only the previously generated, password-free
`OpenNavicat Task Reference 20261113` profile was repointed to its new loopback
endpoint. All observations below used an independent, unsaved Pipeline tab;
they do not reuse the Batch Job cancellation conclusions in AU18.

Selecting `$changeStream` in a new stage exposed a raw editor and an
operator-specific Help template. The template named `fullDocumentBeforeChange`,
`resumeAfter`, `showExpandedEvents`, `startAfter`, `startAtOperationTime`,
`allChangesForCluster` and `fullDocument`. No separate Create Tool switch or
resume-token management control was exposed in this inspected stage. The
presence of a template option does not establish successful execution of every
option.

With `$changeStream: {}` and no new events, Run switched to `消息` and completed
with `> OK` and a **1.016 s** query time. No event-document result grid was
observed. The owned server profiler recorded one `getMore` returning zero
documents; `$currentOp` with `idleCursors` then showed its tailable/awaitData
cursor. Inserting one generated document after this finite return and checking
again after three seconds produced no further `getMore` or automatic UI
refresh. This observed execution was finite; it is not evidence of an ongoing
watch workspace.

A second Run used an independent native client that waited until the reference's
`getMore` became active before inserting one additional generated document.
The profiler recorded one event returned at **03:37:05.020 UTC**, followed by an
empty `getMore` at **03:37:06.029 UTC**. The reference again completed finitely,
leaving an idle tailable cursor. An `输出` page was present, but the inspected
page first appeared blank and later displayed execution text; no event-document
rows or paging controls were observed. Wire delivery of the generated event is
therefore established separately from useful UI event presentation.

For a third Run, a separate native client obtained a resume token from only
this generated fixture and closed its own probe cursor. Entering that token
through the raw editor with `resumeAfter` and `fullDocument: "updateLookup"`
was accepted by the server. The reference's complete message showed the exact
requested options, `> OK` and **1.010 s**. The new reference cursor had returned
one historical insert event in its initial batch, then an empty `getMore` at
**03:39:01.793 UTC**. No event-document result grid was observed. This verifies
a finite resume request on this fixture; it does not verify update lookup
semantics, a saved resume workflow or UI-managed token recovery. The native
probe's cursor and its explicit `killCursors` were distinguished from reference
cursors in the private receipt.

Immediately after Run or Stop, an initial accessibility read could still contain
the previous execution's message. The conclusions above use the later completed
message together with the independent server checkpoint, rather than assigning
the old message to the newly requested script.

Adding a second `$limit: 1` stage through the normal graph and running the chain
displayed the server error `$limit is not permitted in a $changeStream pipeline`,
with a line/column location and **0.005 s** query time. The UI allowed the chain
to be constructed and reported the execution error; it did not silently remove
the stage.

For a bounded independent cancellation probe, the second stage was changed to
`$addFields` with a generated JavaScript `$function` that waited at most ten
seconds for the one resumed event. During Run, the Pipeline graph remained
visible, the stage operator was disabled, a busy indicator appeared and the
toolbar exposed a separate red-square **`停止`** button. Clicking it eventually
displayed **`[Error] Stopped by user`**, with a **10.267 s** query time. This
does not establish immediate cancellation latency or behavior of other stage
types.

The owned service log independently recorded the same cancellation sequence at
**03:45:24 UTC**: the session killer interrupted the active aggregate, killed
the reference session and its three earlier idle cursors, and the aggregate
reported code **11601**, `JavaScript execution interrupted` (server duration
**10.252 s**). The three earlier cursor IDs were
`3827203912959439979`, `8984674585197847101` and `1767425126170645087`;
their retained state before this Stop and absence afterwards are separate
observations. This is evidence of the observed session-wide Stop, not a cursor
lifetime guarantee for other runs or a reason to reproduce idle-cursor retention.

After completion, closing the dirty unsaved tab displayed `是否保存更改？`,
with `保存`, `不保存` and `取消`. Choosing `不保存` returned to an empty Pipeline
object list for this new database. The reference was normally quit and confirmed
not running. A native checkpoint after exit found no active operations or idle
cursors for the owned collection. The exact-owned container and its two
anonymous volumes were then removed and independently verified absent. The
prior harmless generated profile and empty-job metadata remain as disclosed in
AU18-06; this round saved no new pipeline or job, performed no permanent
metadata deletion and created no OS schedule or email action.

AP18-10 is CUA runtime observation, not a REA Evidence ID; no REA session was
opened. It narrows the independent Run, raw-option, finite-return and Stop
unknowns from AP18-09. Event rendering, any supported ongoing-refresh workflow,
pagination, fullDocument update variants, resume persistence/recovery, different
permissions/topologies and Windows/Linux reference behavior remain open. The
OpenNavicat finite-preview rejection of `$changeStream` is a safety boundary,
not completion of these stream-tool gates.

### AP18-10 follow-up: event presentation and preview actions

A separate follow-up on 2026-11-13 used the same **18.0.5 build 321** reference
with a fresh exact-owned native ARM64 MongoDB **8.0.34** single-node replica
set. Its `pipeline_stream_ui_reference.sales` started with four generated
documents. Only the existing generated, password-free Task Reference profile
was repointed to this loopback fixture; no existing saved pipeline or user
database was opened. Both inspected Pipeline tabs were new and unsaved.

After selecting `sales`, the source sample defaults were `随机` and **1,000**
documents. A selected `$changeStream` stage initially showed `无预览` in both
input and output preview panes. The stage's `预览` and toolbar `预览全部`
actions remained available: the stream operator did not automatically disable
them. The toolbar dropdown contained `预览全部` and
`重新加载并预览全部`. The reload variant was enumerated but not executed.
Script Preview for the generated body `{}` showed:

```javascript
db.getCollection("sales").aggregate([
  {$changeStream: {}}]);
```

For Run, an independent native producer inserted one generated document only
after observing the reference's active `getMore` for this exact namespace.
The profiler recorded an event-bearing `getMore` at **04:47:11.979 UTC**, then
an empty one at **04:47:12.980 UTC**; the resulting idle tailable/awaitData
cursor reported one document returned. The reference completed in **1.069 s**
and selected `消息`. Both that page and the subsequently explicitly selected
`输出` page displayed the aggregate script, `> OK` and query time in a text
area. Neither the screenshots nor accessibility trees exposed event fields,
an event grid, a `结果 1` page or paging controls. This confirms the inspected
presentation after actual wire delivery; it does not establish useful event
rendering or justify treating OpenNavicat's generic result grid as UI parity.

Stage Preview and Preview All were then tested separately:

| Action | Independently observed wire sequence | Actual UI outcome |
| --- | --- | --- |
| Selected stage `预览`, no event injected | At **04:47:43.048 UTC**, an independent `sales` aggregate containing only `$sample: {size: 1000}` returned five source documents. A separate cursor whose originating pipeline was only `$changeStream: {}` returned an empty `getMore` at **04:47:44.057 UTC**. | The input preview displayed the five ordinary source documents; the stage operator became disabled while busy. Before a completed stream output was observed, the reference crashed at **12:47:44.214 +0800**. |
| Toolbar `预览全部`, fresh unsaved tab after relaunch | At **04:49:20.103 UTC**, an independent `$sample: {size: 1000}` aggregate returned the five existing source documents. A producer then inserted a sixth document only during the new reference stream's active `getMore`; it returned one event at **04:49:20.137 UTC**, then zero at **04:49:21.143 UTC**. | The input preview again showed the ordinary source sample, with the stream stage busy and output still `无预览`. The reference crashed at **12:49:21.280 +0800**, before useful stream output was observed. |

The sample read was a **separate aggregate**, not a `$sample` stage prepended
to the `$changeStream` pipeline. In particular, the generated event document
appearing in the source input sample in a later action was an ordinary stored
document, not evidence that a change-event envelope had been rendered.

Both macOS diagnostic reports identify reference **18.0.5 / 321**,
`EXC_BAD_ACCESS` / `SIGSEGV` and faulting thread 0. The first report's leading
frame was `CCNavicat::beginReadAccess(CMObject*)`; raw diagnostics, full stacks,
paths and server receipts remain ignored private evidence. These are two
bounded reference failures, not a desired behavior to reproduce. Normal
successful stream preview/output, other options and platforms remain open;
the failures do not remove the preview acceptance gates.

After the second relaunch, the generated profile was confirmed `Off`, and
the reference was normally quit and confirmed not running. The native
checkpoint still found **three idle cursors and no active operation** from
the earlier completed/crashed runs; normal exit of the newly relaunched,
disconnected application is not evidence that it released those earlier
sessions. The exact-owned container and both recorded anonymous volumes were
then removed and individually verified absent. No new pipeline was saved.
The prior harmless generated profile remains disconnected with this now-removed
fixture endpoint; no permanent local metadata deletion was attempted. This
follow-up remains CUA observation under AP18-10, not a REA Evidence ID.

## Boundaries of AP18-01–AP18-08

This round did not complete an automated reference drag operation, every
stage's editor template and option execution, every visual Explain operator/shape,
automatic-task references, export of every result type, saved-pipeline
reopening, concurrent peer changes, all server/topology variants, or
Windows/Linux reference layouts. OpenNavicat's
corresponding stage drag and `$match` create tool are implementation results,
not proof of unobserved reference behavior. The fixture was disconnected and removed
after verifying its exact container ID, owner label and loopback port. The later
AU18/AP18-09 observations above narrow some unknowns without closing the
remaining automation, stream or three-platform gates.
