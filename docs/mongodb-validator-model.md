# MongoDB validator draft foundation

`opennavicat.mongodb_validator` is a detached BSON editing model, not the visual
JSON Schema/Query tool. It performs no database, filesystem, UI or JavaScript
operation. The existing collection backend remains responsible for sealed
previews, UUID/catalog checks, writes, cancellation and acknowledgements.

`ValidatorDraft` accepts a complete validator document or original BSON bytes.
Only an exact single `{'$jsonSchema': {...}}` document has a visual projection;
mixed query/schema rules remain complete Query data. It never extracts one
schema from a compound query or converts between the reference's separate
mode buffers. Accessors return detached copies.

Patches use explicit tuple paths and retain user-supplied BSON types. A batch
applies to a copy, checks its bounds/encoding after each edit, and commits only
after every patch succeeds. No Extended JSON roundtrip, implicit numeric cast
or reconstruction from visible columns is involved. The default bounds are
1 MiB BSON, depth 64, 100,000 nodes and 1,024 patches; full large-document
reference limits remain an open gate.

- `SetKeyword` / `RemoveKeyword` edit explicit keyword values.
- `AddProperty` preserves property order and allows an explicit insertion point
  and optional parent `required` membership edit.
- `RenameProperty` / `RemoveProperty` update only the corresponding parent
  `required` entries, preserving unrelated names and omitted/empty states.
- Schema traversal distinguishes a property named `properties` from the
  actual schema map keyword. Unknown name-dependent rules stop automatic
  rename/removal rather than silently changing their meaning. Known scalar
  constraints and annotations remain intact. This conservative foundation
  boundary does not mark those complete-tool functions out of scope.

Structural property changes use the dedicated operations rather than generic
keyword patches. Explicit complete raw Query replacement remains a separate
caller operation; this module neither evaluates text nor supplies that UI.
Unknown subtrees, order, Int64/Decimal128/Binary/Code values and literal
`$date`/`$oid` keys remain BSON data. Original bytes that cannot decode/re-encode
exactly are retained read-only, and edits or lossy document export are refused.
User-visible issue labels are bounded while the full original bytes are kept.

The **68 pure BSON tests** passed on macOS ARM64 / Python 3.13.11 with
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -W error tests/test_mongodb_validator.py`.
They exercise real codec bytes, atomic failure, type/order/required preservation,
dependency refusal, literal EJSON-looking keys, raw duplicate-field retention
and resource/error-label bounds. They are not native-reference UI, actual
server validation, a complete builder or six-platform installer acceptance.
The source JUnit is retained under `artifacts/collection-storage/validator-foundation.xml`.

Next, wire the actual observed type/property/root-schema controls and nested
object/array modals, separate Query buffer, selection/enablement states and
save/cancel/error/reopen workflow. The complete U23/O14 gates stay open until
those behaviors and the corresponding platform/reference tests are qualified.
