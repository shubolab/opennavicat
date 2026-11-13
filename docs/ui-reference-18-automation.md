# Navicat 18 automation and pipeline-reference runtime observations

Observed on 2026-11-13 with Navicat Premium **18.0.5 build 321** from the
selected local application. These are CUA runtime observations, identified as
**AU18-01–AU18-06**, not REA Evidence IDs or complete cross-platform acceptance.
No REA analysis session was opened for this investigation.

The investigation created its own `OpenNavicat Task Reference 20261113`
connection, `pipeline_task_reference.sales` database/collection, pipeline and
batch jobs. The database contained four synthetic documents. Both MongoDB
services were native ARM64 **8.0.34**, pinned to the repository fixture image,
and published exclusively on new loopback ports. No pre-existing profile was
opened and no business database, saved password, product account or email
recipient was used.

## AU18-01 Global automation list

The main toolbar's `自动运行` robot opens a global object list. The new-job
action is `新建批处理作业`; an empty list also offers refresh and search. The
five list columns are, in order:

1. `名称`
2. `文件大小`
3. `创建日期`
4. `修改日期`
5. `访问时间`

Selecting a saved row exposes `设计批处理作业`, `新建批处理作业`,
`删除批处理作业`, `设置计划`, `删除计划` and `刷新`. Its context menu also
contains `复制批处理作业`, group management and `重命名`. The information pane
shows the batch-job file, exact bytes, creation date and modification date.

The first saved generated job appeared in this list. Copying it produced
`generated_pipeline_batch_copy1` without a separate naming sheet. The copy
retained the job sequence and other General options but received different
`General.UUID` and `General.Hash` values. With those two generated rows, clicking
the actual Name header reversed their name order; clicking again restored it.
Searching `copy1` retained only the copy and displayed `找到 1 个项目`.
Sorting every other column and additional filter semantics remain unobserved.

Some AX button-click operations returned no state change even though coordinate
clicks on the same toolbar/header succeeded. Those unsuccessful AX attempts do
not establish that a control was disabled.

## AU18-02 Batch editor and available-work categories

A new batch job opens a normal workspace tab. Its toolbar contains:

`开始`, `保存`, `设置任务计划`, `删除任务计划`, `添加工作`,
`添加所有工作`, `移除工作`, `移除所有工作`, `进入专注模式`.

The four pages are `常规`, `高级`, `消息日志`, `历史`. General has a selected-work
table above three lower panes: work category, connection/database tree, and
available work. The selected-work columns are:

`已选择的工作`, `类型`, `附加到电子邮件`, `连接类型`, `连接`, `云名称`,
`项目名称`, `项目所有者用户 ID`, `数据库`, `模式`.

The lower-left category order is:

```text
数据传输
数据生成
数据同步
数据字典
数据脱敏
备份
导出
导入
查询
聚合管道
MongoDump
MongoExport
MongoImport
MapReduce
模型
BI
```

Selecting `聚合管道`, then the generated connection and database, listed the
previously saved pipeline with name/type columns. Selecting it and pressing
`添加工作` appended its reference above. Repeating Add retained two references
to the same pipeline; the list is not deduplicated. `移除所有工作` immediately
emptied the selected-work table and restored the drag-work guidance. Saving
that empty job was accepted.

## AU18-03 Name sheet and saved `.nbatpremium` contract

The first Save opens a sheet containing only `批处理作业名称:`, one text field,
`保存` and `取消`. Save is disabled while the name is blank. There is no script
preview or suppress-preview checkbox in this sheet. Subsequent Save writes the
existing object without reopening this naming sheet.

The generated file was stored in the reference's Profiles directory with the
`.nbatpremium` extension. Its JSON top-level structure was `Version`, `General`
and `Jobs`; `Version` was the number **1.4**. General included generated UUID
and Hash fields, logging/continue settings, email settings and their defaults.
One observed pipeline-reference item contained these fields:

```text
TypeName = aggregationpipeline
ServerType = MONGODB
Server = generated connection name
Catalog = empty string
Schema = generated database name
DatabaseBrandName = MongoDB
ReferenceName = saved pipeline name without its extension
Param1 = empty string
Param2 = empty string
ExtraParams = empty object
UseAttachment = false
CommandSwitchOptions = empty array
CloudInstanceID / ProjectOwnerNavicatID / Project = empty strings
```

The job references the saved pipeline rather than embedding its `.nagg` body.
Non-empty `Jobs` was an ordered array. After the two generated jobs were cleared
through `移除所有工作` and saved, **empty `Jobs` was an empty object `{}`** in
both 606-byte files, not an empty array. This observed empty shape must not be
rejected merely because populated jobs use an array.

Changing the saved pipeline affected the next batch run without re-adding its
reference. Renaming the pipeline through the reference UI did not rewrite the
batch file's `ReferenceName`; reopening the batch retained the old name. Running
it produced `does not exist. Failed to locate object`. Restoring the original
pipeline name restored resolution. Deletion was not required to establish the
missing-reference case.

The isolated original file and a reformatted, checked copy are retained only in
ignored local evidence. The reformatted copy is not claimed byte-identical.
No profile archive, reference artwork or raw reference evidence is added to an
application package.

## AU18-04 Advanced defaults and retention

The Advanced page begins with `常规`, then `遇到错误时继续` and
`启用批处理作业历史记录`; both were checked for the new job. Their saved keys
were `ContinueOnError` and `EnableLogging`.

The email section contained `发送邮件` (unchecked), `当:`, `成功` and `失败`
(both checked but disabled), then sender, recipient, CC, subject and body. The
default subject was `[%F] Batch Job %J Finished %R`; the body was `%L`.
The format guidance also named `%FirstFailedJobName`, `%SuccessfulJobCount`,
`%FailedJobCount` and `%ProcessedJobCount`. The SMTP host/port, security choices
`永不`/`TLS`/`SSL`, authentication checkbox, username/password and test-email
button followed. Email remained disabled, with no recipient or SMTP credential;
no message was sent and email execution is not qualified.

The duplicate references, ContinueOnError=false and history survived closing and
reopening the object. History was also retained across a normal application
exit/restart. Exact history-file storage and every future General field remain
open.

## AU18-05 Execution, error policy, disconnected sessions and history

Start switches to the message-log page. A generated read pipeline logged a
Batch Job start, AGG start, successful completion and Total/Success/Fail counts.
An independent native MongoDB profiler recorded the actual aggregate command
`[{$match:{}}]`, returned four documents and confirmed the source still held
four documents.

Saving a valid BSON but invalid server predicate in that referenced pipeline
caused real server error code **2**. With two identical references and
ContinueOnError=true, both started and failed: Total=2, Success=0, Fail=2.
With ContinueOnError=false, the first failed and the second logged `has been
skipped`: Total=2, Success=0, Fail=1, Skip=1. The observed AGG `[ERR]` lines
omitted the server text in this case; the profiler separately retained the
actual server error. That omission is an observation, not proof of complete
error handling. A missing renamed reference did have a detailed missing-object
message and the same fail/skip policy.

After explicitly disconnecting only the generated None-authentication
connection and verifying its Off state, opening the global batch and pressing
Start ran both valid references successfully without a connection sheet. The
independent profiler recorded both executions, while the main connection tree
remained Off. Thus the observed job used its own connection/session. This does
not qualify credentials, password retrieval or arbitrary automatic connection
policies. A subsequent attempt to edit the generated endpoint was rejected as
`Connection is being used.` until the reference application was restarted.

History showed `执行编号`, `开始时间`, `结束时间`, `期间`, `执行`, `结果`.
Rows used `Manual` and success/failure states. Selecting one restored that
execution's complete log in the lower pane. Success, server failure and skipped
work were recorded and remained after reopen.

A controlled finite read-only pipeline ran for about 20 seconds per job. During
that run, the toolbar remained a triangular `开始` control in the screenshot
and AX state. No separate Batch Stop button was observed. Escape did not stop
the run; both tasks completed after roughly 40 seconds. A supported Batch
cancellation action and its history state remain **unqualified**; do not infer
a Start/Stop toggle from the ordinary query/pipeline toolbar.

## AU18-06 Plans, permanent-deletion boundary and cleanup

The schedule controls were enumerated but were not activated: this investigation
did not create an OS schedule, calendar event or future task. The available
categories do not establish successful execution of every tool type.

Deleting the generated copy opened a warning naming that object, with an
unchecked `我了解此操作是永久性的且无法撤消` and disabled Delete until checked.
The action was cancelled. No permanent metadata deletion was performed.
Both generated batch jobs were instead cleared through the ordinary editor and
saved with empty `Jobs` objects and SendEmail=false. Their own connection,
pipeline and harmless empty-job metadata remain explicitly identified as
generated reference artifacts; they are not claimed removed.

The reference application was normally exited. Both exact-owned database
containers and their four anonymous volumes were then removed after verifying
full IDs, owner labels, loopback bindings and exclusive volume users. Independent
container/volume inspections confirmed absence. Private fixture and server
observations are under `artifacts/reference-pipeline-task-20261113/`.
No pre-existing user profile or user database was connected, edited or deleted.
