# Local Batch Job and saved-pipeline checkpoint

This source checkpoint begins the reference-shaped local automation workspace.
It is **partial**: a local document model, four-page Qt component, serial
saved-MongoDB-pipeline runtime and bounded local history are implemented.
MainWindow routing and three owned-database Window cases cover repeated
execution, latest saved references, error policy, cancellation and persistent
history. This is a bounded checkpoint, not full automation or reference parity;
current release qualification is recorded in [0.11 validation](validation-0.11.md).

The observed reference contract is [AU18-01–AU18-06](ui-reference-18-automation.md),
using Navicat Premium **18.0.5 build 321** on macOS and owned MongoDB **8.0.34**.
Those observations establish the local Batch surface and one task type, not
complete automation, all topology behavior or Windows/Linux reference parity.

## Documents and exact reference binding

`batch_jobs.py` implements the observed numeric `Version: 1.4`, `General` and
`Jobs` contract. Non-empty Jobs is an ordered array, preserving duplicates.
The observed empty-object `{}` form is supported, along with imported empty
arrays; the imported empty shape is retained until an explicit editing operation
changes it. Removing the last task uses the observed empty object.

The parser checks UTF-8, duplicate JSON fields, finite numbers and known-field
types. Documents are bounded to **1 MiB**, depth **64**, **100,000** nodes and
**1,000** task references. Unknown non-secret fields and absent imported fields
are retained. A file can be retained without being executable: unsupported
Version values, task types, product-cloud references or unqualified execution
parameters are rejected before database execution.

The generated UUID/Hash strings are local metadata. Their text shapes are
compatible with the observed fields; this implementation does not reproduce or
authenticate the reference's Hash algorithm and does not claim byte-identical
serialization of arbitrary `.nbatpremium` files.

The qualified local task envelope is `TypeName=aggregationpipeline`,
`ServerType=MONGODB`, `Server=<connection name>`, `Schema=<database>`,
`ReferenceName=<saved pipeline name>`, with the observed empty optional fields.
It refers to a saved pipeline rather than embedding its `.nagg` body. Resolution
requires one exact connection name/driver/identity and one saved pipeline with
the exact database/name and matching connection identity. Missing, ambiguous or
renamed bindings fail; a stable internal ID never silently follows a renamed
connection or pipeline. This follows the observed reference rename failure.

At Start preparation, the current **latest saved** pipeline and reviewed
connection configuration are copied into a detached one-run plan. Unsaved
pipeline editor text is not substituted. The worker freezes the plan again and
does not read the Store or Qt widgets. Mid-run profile or saved-file changes are
not reread. This is the current implementation boundary; reference behavior for
such concurrent changes remains unobserved and is not declared compatible.

## Qt surface and persistence

The component has the observed `常规`, `高级`, `消息日志` and `历史` pages.
General retains all **16** observed work categories and the selected/available
work layout, including the ten selected-work columns. Currently only saved
MongoDB pipelines populate executable available-work rows. Other task categories
remain visible but are not implemented execution paths.

The component supports appending selected/all visible pipelines, repeated
references and removing selected/all references. A first-save name sheet has
`批处理作业名称:`, Save and Cancel, with blank-name Save disabled and no script
preview. It retains logging and continue-on-error options. Schedule controls
and SMTP execution remain disabled; preserved metadata does not enable either.

MainWindow integration provides a global five-column job list
(`名称`, `文件大小`, `创建日期`, `修改日期`, `访问时间`), local save/reopen,
permanent-delete confirmation, dirty-document lifecycle and event delivery.
These routes have owned-database Window evidence. Full job copying, grouping, renaming, file chooser
import/export, every toolbar state and layout geometry remain acceptance gaps.

Definitions are stored in the existing local workspace state. Run summaries are
stored separately in local SQLite history, with independent execution numbers
per job, newest-first retrieval and **200** retained runs per job. A history
write is transactional; definition flush failures restore the prior in-memory
definition. History is bounded by event and byte limits and is not a copy of the
reference application's internal history database.

## Execution, cancellation and safety

The runtime executes references in order, opening its **own session** for each
attempt and closing it in `finally`. It does not borrow or mutate an interactive
workspace session. Duplicates are separate executions. With ContinueOnError=true,
later tasks continue after failure; false marks the remaining tasks skipped.
The runtime emits bounded task events and final success/failure/skip/cancel
counts. Native MongoDB error codes/codeNames are retained in safe summaries;
exception text, connection endpoints, credentials, SQL and result documents are
not copied into batch history.

Database work belongs on the MainWindow serial queue. A run-specific cancellation
control identifies its current session and exact operation marker; a separate
cancel worker invokes cancellation. A delayed cancel without a matching marker
must not kill a later operation. Cancellation before dispatch avoids execution;
later tasks are skipped. An operation that completed before a cancellation
request remains completed. Successful or uncertain effects are never
automatically undone or retried, and the multi-task sequence is not a transaction.
The run's independent cancellation Event is also passed into pipeline execution,
so a request made before the session establishes its operation marker survives
that session's flag reset and is checked before dispatch.

The controller prepares a warning/confirmation for saved `$out` or
`$merge` stages before dispatch. Their write execution/confirmation paths are not covered by the three read-task Batch Window cases. Ordinary preview continues
to refuse write stages and change-stream execution. The latter refusal is a
safety boundary, **not** completion of change-stream functionality.

The document model refuses non-empty credential-like fields, including the
reference's `EmailServerPassword`; it provides no plaintext secret fallback.
Database credentials remain in the existing OS-keyring/process-memory path.
Product-cloud/team references are not treated as ordinary local references;
cloud-hosted database authentication and connection tools remain in full project
scope. No product-account exclusion removes database or AI tools.

## Verification recorded at this checkpoint

The component checkpoints and the combined offline run overlap; do not add their
counts into a new full-source result.

| Scope | Recorded result | Boundary |
| --- | --- | --- |
| Local Version 1.4 document model | **85 passed** | Pure document/binding/serialization safety, including both empty Jobs forms; no server execution. |
| Four-page Qt component | **18 passed** | Widget state, drafts, controls, bounded display and emitted requests; no MainWindow database route acceptance. |
| Independent-session serial runtime | **18 passed** | Ordered ownership, error policy, cancellation race and safe result contracts; not an OS scheduler or full reference comparison. |
| Store/history mixed checkpoint | **19 passed** | Local state/history behavior and included regression cases; not 19 distinct reference workflows. |
| Combined offline integration | **141 passed** | Model/runtime/Qt/Store/lifecycle scope; not real-database Batch Window execution. |
| MainWindow + owned MongoDB batch cases | **3 passed / zero skips** | A fresh native ARM64 MongoDB 8.0.34 fixture; independent-session execution, latest saved references, error policy, rename failure, real cancellation and history reopen. Exact fixture ID independently absent afterward. |

The preceding pipeline repair in **`3bc020f`** had a separate scoped result:
**41** model/script cases, **21** Qt cases and **one** real owned-database tree
route. It corrected typed BSON script generation, preview safety and pipeline
navigation. It does not qualify the newly integrated Batch Window paths, all
pipeline operators or a continuous change-stream workspace.

The actual reference's None-authentication batch was independently observed to
execute while its main connection tree remained Off. Its success/failure history,
duplicate references and missing-after-rename behavior were also observed.
Those reference runs are different evidence from the OpenNavicat tests above.

## Remaining gates

All **16** reference task categories must ultimately have their applicable
profiles, parameters, execution and error behavior. Only the saved-pipeline
slice has begun implementation. Complete copy/group/rename and object/file
management, dependencies, SMTP/attachments and templates, OS scheduling,
triggers, timezone/credentials, execution while closed/offline, recovery after
restart and all three platform reference layouts remain open.

AU18 did not establish a Batch Stop action: its finite long read run retained a
triangular Start control and Escape did not cancel it. OpenNavicat's cancellation
contract therefore remains a safety implementation with a reference UI/behavior
gate to finish. The [AP18-09 stream probe](ui-reference-18-mongodb-pipeline.md#ap18-09-change-stream-stage-and-batch-probe)
also did not establish continuous event delivery, resume or stream cancellation.

Follow [parity-matrix.md](parity-matrix.md), particularly U13/U22, A01/A02 and
Q12. Source tests, reference observations, native packaging, published installers
and complete UI/tool parity remain separate gates. A new usable release still
requires all six native installer and checksum checks; this local checkpoint
does not publish one or complete the goal.

The integrated default source checkpoint passed **1986 cases / 566 conditional skips**,
and the expanded collection runner passed **458 / zero skips** across eight new owned fixtures.
Exact fixture IDs were independently checked absent and all listed source hashes matched that run.
Those measurements precede the final 0.11 metadata, two additional unknown-execution-metadata checks,
pipeline save-title correction and demo extension. The later model/runtime/controller scope passed
**111 cases / zero skips**, including85 model,18 runtime and8 controller cases; scopes overlap.
These are development checks, not final release or complete reference acceptance.

Before the execution-budget and startup-cancellation repair, local 0.11 working-tree checks were **2011 passed / 567 conditional skips**
(default source), **460 passed / zero skips** (the exact collection/task runner),
and **51 passed / zero skips** on native Cocoa (page/controller/pipeline plus
four actual MongoDB MainWindow routes). Eight fixture IDs were independently
checked absent; all48 qualified source-file hashes matched the runner receipt.
Those JUnit remain under `artifacts/release-011/`; they do not qualify later
source changes. Final budget/cancellation, installer and public-asset evidence is
tracked separately in [0.11 validation](validation-0.11.md); complete reference
UI acceptance remains open.
