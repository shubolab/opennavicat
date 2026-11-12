# MongoDB validator visual editor

The ordinary collection designer now embeds a detached JSON Schema/Query editor
on the observed reference **Validation** page. The implementation is backed by
the lossless `ValidatorDraft`; Qt widgets never open a database or evaluate
JavaScript, and the collection page continues to use the window's serial
background queue for catalog reads, sealed previews, writes and reopens.

## Reference-shaped surface

- `验证级别` exposes blank/`off`/`strict`/`moderate`; `验证动作` exposes
  blank/`error`/`warn`. A new collection starts with a blank validator and does
  not treat the server's implicit default as a displayed value.
- `验证符类型` separates `JSON Schema` and `Query`. Switching modes retains
  each independent draft; the last mode used for an existing validator is
  remembered.
- JSON Schema has the observed `属性` and `根模式` subpages. The property table
  uses the primary icon column followed by Name, BSON Type, Required, Title and
  Description. Add/Delete toolbar actions track the current mode and subpage.
- The picker contains all 22 observed BSON aliases plus `(Multiple)`. Selecting
  `Multiple` opens the bounded multi-type picker and retains the original order
  of untouched existing aliases.
- Numeric, array, object and string properties expose their type-specific
  controls. Existing enums, nested object/array rules, root
  pattern/dependency values and unknown siblings are edited through a modal
  BSON-literal surface that changes only the explicitly selected keyword.
- Query mode edits one complete BSON object. Ordinary numeric literals become
  BSON Double as observed; explicit `Int32`, `Long`, `Decimal128`, `Date`,
  `Timestamp`, `BinData`, `ObjectId` and other supported constructors retain
  their types. Expressions and trailing statements are rejected.
- Values that cannot be projected, parsed or losslessly encoded remain visible
  read-only. The editor does not replace an external validator with an empty
  schema or silently convert between Schema and Query.

Every property/replacement batch is applied to a copy and committed only after
all bounds and BSON encoding checks pass. Save still requires the collection
page's existing snapshot, namespace/UUID, stale-catalog and sealed-preview
checks. Cancel, server error and reopen retain the draft and preserve existing
documents and indexes.

## Verified behavior

The exact development checkpoint completed:

- Full source suite on macOS ARM64 after the fixture pin: **1761 passed, 548 conditionally skipped,
  0 failed/error**, 70.96 seconds. The skips require external services or
  platforms and are not claimed as execution.
- Targeted model/widget/lifecycle scope: **233 passed, 3 conditionally skipped**.
- Pinned real MongoDB protocol/index/MainWindow qualification: **298 passed, zero skipped/failed/error**; seven exact fixture IDs were independently confirmed absent after cleanup.
- Pinned collection/timeseries/view/copy/maintenance qualification: **226 passed, zero skipped/failed/error**; all six exact fixtures matched the digest/version and were independently confirmed absent.
- Native JavaScript parent-authoritative protocol: **195 passed, zero skipped/failed/error**; Qt protocol: **12 passed, zero skipped**. All five exact fixture IDs matched the pin and were independently confirmed absent.
- Fresh owned loopback MongoDB 8.0.34 pinned by multi-architecture digest
`sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142`, offscreen Qt: **3/3** complete
  MainWindow paths passed, including visual score Schema save and boundary
  enforcement, Query cancel/error/success/collMod/reopen with UUID and document
  preservation, all 22 BSON types, nested fragments, `(Multiple)` and isolated
  string boundaries.
- Native Cocoa: **38/38** validator widget and real MainWindow tests passed.
- Each owned MongoDB fixture was disposed after the run; no reference profile or
  production endpoint was used.

These are source/runtime checks, not six-platform packaged acceptance or full
reference parity.

## Still open

The external validator conversion rules, every unknown/name-dependent keyword,
all root-schema combinations, special-character escaping, every value editor and
the complete enablement/error behavior still need reference-backed closure in
addition to the inline collection index/key editor and structured collation.
Windows and Linux reference UI/workflows also remain open. Therefore MV18, U23
and O14 remain partial gates in the parity matrix.
