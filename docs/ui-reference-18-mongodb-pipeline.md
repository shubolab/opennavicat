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

## Boundaries

This round did not complete an automated reference drag operation, every
stage's specialized create tool, visual Explain tree, automatic-task references,
export of every result type, saved-pipeline reopening, concurrent peer changes,
all server/topology variants, or Windows/Linux reference layouts. OpenNavicat's
corresponding stage drag and `$match` create tool are implementation results,
not proof of unobserved reference behavior. The fixture was disconnected and removed
after verifying its exact container ID, owner label and loopback port.
