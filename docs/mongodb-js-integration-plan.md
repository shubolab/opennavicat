# Native MongoDB JavaScript integration plan

This is a reviewable development checkpoint on `work/mongodb-js-engine`, not a
change to main, version, existing installers or published tags. Main 0.7.1
remains independent. The earlier engine/bridge source qualification at
`d669a6a` passed all six native targets (58 cases each, zero skips) and fresh
MongoDB protocol qualification (113 cases, zero skips), run `42860895091`.
That evidence does not qualify a packaged application or this newer UI layer.

## Source changes in this checkpoint

`NativeMongoQueryPage` in `ui/mongo_native_query.py` subclasses the existing dense
`QueryPage`, retaining its editor, file/save actions, result tabs, current-result
export API and script-modified handling. The feature branch now registers it for
new MongoDB queries in the main window. Run, Ctrl/Command+Enter and Stop use the
new execution path, so component tests do not accidentally exercise the old
literal parser. The connection selector contains MongoDB profiles only.

Before submission, the GUI captures script selection, connection identity,
database, row bound and a fresh cancellation Event. Helper lookup, script
execution, BSON conversion and database work all run in `window.submit` on the
session's serial owner. A running page remains busy until its task terminates;
Stop sets only that run's Event. It neither queues an unbound session cancel nor
enables new execution while the old worker cleans up. Forced Qt deletion sets
the same Event; late replies check page validity and job identity.

The watchdog now snapshots `MongoSession.cancellation_marker()` and passes it to
`cancel(expected_comment=...)`. Delayed cancellation cannot set a new
operation's cooperative flag or target a new nonce. The small public API fix is
independently available as `6f46795`; the JS branch contains equivalent cherry
`fb28edc`. Existing immediate `cancel()` callers retain their current behavior.

`JavaScriptResult.value_tree` retains the tagged completion as well as the
decoded Python value. This preserves distinctions that Python `int` alone
cannot express, such as small JS BigInt versus BSON Int32. A terminal frame from
a failed native exit is never presented as a successful value.

`js_engine/presentation.py` has no Qt or database capability. It renders BSON to
Canonical Extended JSON only after re-decoding it to identical BSON bytes. A
literal `$date` or invalid literal `$oid` field that collides with EJSON gets an
explicit base64 BSON wrapper envelope instead of a changed value. JS-only
BigInt/undefined inside a value use an explicitly named tagged representation;
they are not relabeled as BSON. Every exported grid cell is already a string,
avoiding generic `json.dumps(default=str)` conversion of Int64/Decimal/Binary.
These result CSV/JSON exports contain encoding and text columns; they are not
silently advertised as direct MongoDB import files.

Undefined completion has no fabricated null row. A real null completion does
have a null value. A materialized array of documents becomes document rows; an
empty array has zero rows and an explicit empty-array message. The summary
shows each command's read/write classification and actual acknowledgement,
including `acknowledged_error` and `uncertain`. Missing affected counts remain
`unknown`, including terminal `$out`; server-reported zero remains zero. Earlier
acknowledged writes stay visible after cancellation, duplicate errors or lost
acknowledgement. No rollback is claimed.

## Owning-window routing and language migration

The branch now implements the following hooks. Main has separate index-routing
work, so these small routing changes require deliberate integration there:

1. New MongoDB queries construct `NativeMongoQueryPage`. Existing `QueryPage`
   remains the interpreter for legacy Canonical EJSON commands and older saved
   queries. `query_language` is persisted in drafts, favorites and history. The
   SQLite history migration adds a column with `legacy` default; JSON records
   without a language or with null get `legacy` too. Unknown language identifiers
   are refused rather than reinterpreted. A native favorite cannot silently run
   under an unrelated SQL connection.
2. In `MainWindow.run_query`, dispatch native pages to `page.run_native()` before
   calling `session.execute`. This also covers the main toolbar and menu Run.
   The component's own toolbar and editor shortcut already use its native path.
3. In `MainWindow.stop`, call `page.request_cancel()` for native pages, then skip
   the generic queued `session.cancel` path. App-close cancellation must take
   this branch too. Existing serial task completion remains the authority for
   allowing close, disconnect or another operation.
4. Language survives saving/reopening drafts and favorites/history. External
   files without metadata present a meaningful choice: legacy command/EJSON
   semantics, or JavaScript with ordinary Number stored as Double. Cancel opens
   and executes nothing. The selection is stored in the workspace bound to the
   file path and SHA of its exact text; edited/replaced files require a fresh
   choice. Neither file extensions nor a leading JSON object choose JS silently.
   Current dirty-script Save/Discard/Cancel behavior is retained. Script Save saves a
   file; it must never execute the script or imply a database rollback.

Component tests capture actual worker thread IDs and lifecycle completion. A
fresh-owned MongoDB case exercises variables/loops, three typed inserts and
find results, a later duplicate error with one earlier confirmed write, refusal
of read-only delete, and the real post-failure server state. Separate actual
native-Qt tests exercise BigInt and CPU-loop Stop. This is local native macOS
source evidence. Actual main-window tests also prove new-loop typed writes,
legacy Int32 versus JS Double, history tagging, file-language Cancel and choices,
draft restoration and global Stop with real helper cleanup.

The independent qualification workflow now adds four Qt component cases on each
native target: three controlled-engine handoff/lifecycle contracts and one
actual-helper VM-only execution/CPU cancellation. That last case intentionally
has no database capability. It loads the real component and engine; no Python
module/import replacement is used to impersonate database transport. Only the
Linux `qt-protocol` lane includes the fifth case with actual `MongoSession` and
`NativeJavaScript.execute` against a fresh owned MongoDB server. Each lane
records this exact scope, dynamically collected node IDs and zero-skip JUnit.
Neither scope substitutes for full main-window, all-driver or installed-app
qualification. Windows ARM crypto/native SDK acceptance is not inferred from a
VM-only Qt component test.

## Helper payload preparation

`scripts/prepare-js-payload.py --output <independent-artifact-directory>` stages
only the verified native helper, pinned-source build receipt, retained MIT
license and a payload manifest. It does not compile at query time, write release
dist, alter the release Spec or modify a publisher. `bootstrap.js` must also be
included as package data. The manifest binds its SHA alongside all staged files.

`js_engine/payload.py` resolves source execution only from the project's explicit
artifact helper directory. Frozen applications resolve only beneath
`sys._MEIPASS/native/js-engine`. There is no PATH, test environment variable,
developer checkout or SDK fallback. All manifest assets and the bootstrap must
match; resolved paths cannot escape the application. `NativeJavaScript` then
checks official source revision/archive SHA, helper bytes, operating system and
native machine. A missing helper is a concrete error, never a silent fallback
to the safe-literal parser or Python evaluation.

These hashes detect incomplete or mismatched payload construction. They are not
a signature, attacker-resistant filesystem trust root or an operating-system
sandbox. Helper build receipts and application signing serve different roles.

## Bundle work and release gates still required

The branch Spec and `build.py` now build/stage the helper. The reused installer
workflow can be dispatched on this branch; its publisher explicitly requires a
tag **push**, so a workflow dispatch cannot create or alter a Release. Versions,
main and existing assets remain untouched. The branch must still pass its exact
HEAD's complete six-platform build and installation run before integration:

* Build the fixed official QuickJS-NG source independently on each native
  target, retaining source archive SHA, toolchain log, real PE/ELF/Mach-O machine
  and helper SHA. Do not borrow a host helper or a cross-architecture executable.
* Bundle helper, manifest, receipt, bootstrap and MIT license. Include the
  third-party notice in the existing license inventory. No source compiler or
  network download is needed on an end-user machine.
* The implemented Spec processes the helper for its final native target path
  first, then writes a receipt retaining both `source_helper_sha256` and the
  processed `helper_sha256`. The payload manifest binds the final receipt and
  bootstrap. It appends executable DATA after Analysis classification, preventing
  another binary rewrite during collection. The outer bundle is then signed.
  This sequence has passed a real local ARM64 Mac BUNDLE, Cocoa smoke, DMG
  install/launch/uninstall, with final bytes checked at runtime. This local proof
  is not substituted for the six-target hosted installer run.
* Add strict frozen and installed smoke probes that launch the bundled helper,
  execute BigInt and exact BSON IPC, verify native machine/bytes, reap the child
  and verify no Windows console popup. `--smoke-test` now runs these actual probes
  and exposes strict boolean fields; packaging and publishing reject missing,
  false or merely truthy values. Remove developer helper/SDK environment
  paths during these probes. Existing crypto, SQL, BSON-C and TLS probes remain
  mandatory; a new optional/false value cannot count as pass.
* Run actual Qt component/main-window worker/cancel/draft/partial-result tests
  on the six native source targets, and a fresh isolated MongoDB protocol lane.
  Download the final installer assets, independently check their hashes and
  native payloads, and execute the installed app with its own helper. Source
  qualification does not substitute for these installed checks.

## Compatibility and capability boundaries remain open

The current bootstrap has an eager, bounded cursor model (256 KiB command/result
BSON, 1,000 default rows, at most 64 bridge commands). Exceeding a delivery bound
is an error, not silently truncated successful output. The native engine
supports broad JavaScript syntax, but that does not implement every shell API or
Navicat global. Native reference `Promise` is undefined while this engine still
exposes it; `print`, `printjson`, `ISODate`, ObjectId generation, all constructor
overloads, lazy cursors, persistence across runs and transactions remain open.

Host-root type metadata matches the two controlled reference corpora; it is not
proof of every return shape, private prototype method, hostile Proxy interaction
or BSON representation. Ordinary JS Number rounding and integer-key ordering
follow the observed reference and cannot be reversed. Raw U+2028/U+2029 script
file fidelity through Qt's plain-text editor needs an explicit preserved-source
policy before broad file-import qualification. Escaped forms are already
supported. Helper API removal and bounded child execution are not a claim of a
complete C-engine or operating-system security audit.

The retained Dell 0.6.1 Windows GUI fixture and application are untouched. Their
remaining write/query/GridFS GUI acceptance stays deferred by the user.
