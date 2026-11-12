# MongoDB aggregation-pipeline workspace

OpenNavicat now has an independent MongoDB pipeline page rather than routing
aggregation work through an SQL-style input. The page uses a bounded
`PipelineDefinition` with ordered stages, source collection, preview sample mode,
sample size and local persistence.

## Implemented workflow

- Main MongoDB toolbar `管道` and the `聚合管道` object category open the native
  Qt workspace.
- The operator combo contains all 42 stage names observed from Navicat 18.0.5.
- Users can add/delete and drag or button-move stages, enable or disable a
  stage, choose the source collection and edit Canonical Extended JSON stage
  bodies.
- Reference create-tool switching is limited to `$match`, `$project`, `$sort`,
  `$limit` and `$skip`. `$match` has the observed AND/OR
  field/operator/value rows; projection and sort tools keep typed expressions.
  Every one of the 42 stage names now opens the Stage Editor with the exact
  Navicat help template observed for that operator. Other stages use that
  Canonical-EJSON editor rather than non-reference structured controls.
- A graph represents `source → ordered stages → output`.
- The source preview popup now matches the observed `全部`, `最初`, `随机` order.
  `全部` disables the sample-size field; saved mode ids are `0`, `1` and `2`.
- The `选项` pane follows the observed form for disk use, cursor batch size,
  timeout, document-validation bypass, read concern, collation, hint, comment,
  optional write concern and `let`. The mapped known options enter preview,
  execute and Explain commands; unknown Option fields remain preserved.
- `预览全部` runs each enabled prefix against an isolated source sample and shows
  the selected stage's input/output plus the final result.
- `运行` executes the unsaved pipeline without applying preview sample options.
  `$out` and `$merge` require explicit confirmation.
- `解释` runs an aggregate command inside `explain` with `executionStats` and
  opens a native `解释` workspace matching the observed five-view family:
  visual plan, Canonical EJSON, grid, grouped node statistics and run
  information.
- The visual plan is a bounded `QTreeWidget` with stage, elapsed time, returned
  documents, examined documents and index columns. It supports threshold
  highlighting, search, zoom, node detail and PNG export. The raw server reply
  remains available without flattening unknown fields or BSON values.
- Result export writes Canonical Extended JSON without converting Int64,
  Decimal128, dates, binaries or ObjectIds through Python `str()`.
- Saved pipelines appear in a seven-column object list matching the observed
  reference metadata family: name, file size, creator, created, modifier,
  modified and access time.
- The workspace stores pipeline definitions in its existing protected local
  state file. `导入查询` accepts Navicat `Version 0.0` `.nagg` files, legacy
  OpenNavicat `.nagg` files, or a JSON/Canonical EJSON stage array.
- The README GIF now includes the Options pane, stage preview, script preview,
  visual Explain and real execution results from the generated fixture.

## `.nagg` format boundary

The reference format was observed directly from Navicat 18.0.5. It is compact
JSON with top-level `Version`, `Collection`, `PreviewSourceOption`, `Stages` and
`Option`. Each stage carries `Op`, an escaped `MQL` Canonical-EJSON string and
boolean `Enabled`. OpenNavicat now writes this `Version 0.0` contract, preserves
unknown top-level, option and stage fields, and imports both the reference
format and the earlier `opennavicat-nagg-v1` format. A generated 588-byte
`$lookup` fixture is byte-identical, including SHA-256
`bde5e0bc931dcdab5d922fa5cf0f19f4da148639d23001c52fb414838900dd0a`.

Reference `MQL` text may contain JavaScript block or line comments around the
Canonical EJSON value. The editor retains the original `MQL` text byte-for-byte
when it is untouched, including help comments; execution and preview parse a
copy with comments removed only outside quoted strings. Editing the stage
exports the new text verbatim instead of restoring the old template.

The reference file does not store the pipeline name or selected editor stage;
the name remains external object-list metadata, and reopening selects the first
stage. OpenNavicat preserves the reference `Option` document, including unknown
fields, and applies the observed disk, batch, timeout, read concern, collation,
hint, comment, write concern and `let` ids. Server/topology-specific semantics
for unknown or future options remain open. The observed reference format is not
claimed to cover every later `.nagg` version or every complex BSON value.

## Visual Explain boundary

The worker consumes the execution-stage tree with a fallback to
`queryPlanner.winningPlan`. The complete reply is validated as bounded exact
Canonical EJSON before the GUI receives it. Parsing is capped at 512 nodes and
32 levels; exceeding either limit produces an explicit error. Server-shaped
unknown fields remain in the node detail and JSON view, while absent or
unexpected plan shapes still open the raw response instead of manufacturing a
plan node.

The current native plan tree follows the observed information architecture but
is not yet a pixel-for-pixel node canvas. Every MongoDB operator, shard/replica
plan shape, profiler output, cost-colouring rule and Windows/Linux reference
layout remains a parity gate.

## Safety and execution boundary

Preview never runs `$out` or `$merge` and never executes `$changeStream` as an
unbounded stream. Runs are bounded to 1000 rows and 1 MiB of delivered BSON.
Cancellation uses the MongoDB connection's exact operation marker through the
existing separate cancellation worker. Read-only connections reject write and
streaming stages.

## Verification boundary

Unit tests cover the 42-entry catalog, nagg roundtrip with Int64 and Decimal128,
stage import, persistence, UI graph operations, preview rendering, save-sheet
naming, nested visual plan projection, unknown-field retention and visual-view
search. Owned MongoDB tests cover per-stage preview, final aggregation,
`executionStats` Explain, `$out` confirmation boundaries and a full MainWindow
save/reopen path. The expanded collection runner passed **288 cases with zero
skips/failures/errors across eight exact-owned fixtures**; focused Cocoa pipeline
checks passed **14/14**, the full source suite passed **1820 cases with 562
conditionally skipped**, and source smoke returned 6 rows × 4 columns. The README
GIF is 25 phases, 323 encoded frames and 53.84 seconds. Runtime reference observations are recorded in
[the Navicat observation](ui-reference-18-mongodb-pipeline.md).

All 42 operator-specific editor templates were enumerated from the reference;
only the five stages above expose a create-tool switch. Unknown/future Option
semantics, exact visual Explain canvas and all operator shapes, automatic-task
references, change-stream UI, all MongoDB versions/topologies and Windows/Linux
visual acceptance remain open.
