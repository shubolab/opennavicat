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
- `$match` has the observed structured create tool with AND/OR groups and
  field/operator/value rows. Complex expressions automatically fall back to the
  exact EJSON editor instead of being flattened lossily.
- A graph represents `source → ordered stages → output`.
- `预览全部` runs each enabled prefix against an isolated source sample and shows
  the selected stage's input/output plus the final result.
- `运行` executes the unsaved pipeline without applying preview sample options.
  `$out` and `$merge` require explicit confirmation.
- `解释` runs an aggregate command inside `explain` with `executionStats`.
- Result export writes Canonical Extended JSON without converting Int64,
  Decimal128, dates, binaries or ObjectIds through Python `str()`.
- Saved pipelines appear in a seven-column object list matching the observed
  reference metadata family: name, file size, creator, created, modifier,
  modified and access time.
- The workspace stores pipeline definitions in its existing protected local
  state file. `导入查询` accepts OpenNavicat `.nagg` documents or a JSON/Canonical
  EJSON stage array.
- The README GIF now includes pipeline stage preview, script preview and real
  execution results from the generated MongoDB fixture.

## `.nagg` format boundary

OpenNavicat uses a versioned, Canonical-EJSON-backed document with
`format: "opennavicat-nagg-v1"` and preserves exact BSON values, stage ordering,
enabled state and stable in-workspace stage IDs. The reference's actual `.nagg`
serialization has not been reconciled byte-for-byte or semantically, so no
reference-file compatibility is claimed.

## Safety and execution boundary

Preview never runs `$out` or `$merge` and never executes `$changeStream` as an
unbounded stream. Runs are bounded to 1000 rows and 1 MiB of delivered BSON.
Cancellation uses the MongoDB connection's exact operation marker through the
existing separate cancellation worker. Read-only connections reject write and
streaming stages.

## Verification boundary

Unit tests cover the 42-entry catalog, nagg roundtrip with Int64 and Decimal128,
stage import, persistence, UI graph operations, preview rendering and save-sheet
naming. Owned MongoDB tests cover per-stage preview, final aggregation,
`executionStats` Explain, `$out` confirmation boundaries and a full MainWindow
save/reopen path. The expanded collection runner passed **270 cases with zero
skips/failures/errors across eight exact-owned fixtures**; focused Cocoa pipeline
checks passed **7/7**, the full source suite passed **1803 cases with 561
conditionally skipped**, and source smoke returned 6 rows × 4 columns. The README
GIF is 22 phases, 291 encoded frames and 48.50 seconds. Runtime reference observations are recorded in
[the Navicat observation](ui-reference-18-mongodb-pipeline.md).

Specialized visual create tools for stages other than `$match`, visual Explain
trees, automatic-task references, reference `.nagg` compatibility,
change-stream UI, all MongoDB versions/topologies and Windows/Linux visual
acceptance remain open.
