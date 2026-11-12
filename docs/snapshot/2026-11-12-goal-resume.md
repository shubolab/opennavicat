# OpenNavicat goal resume snapshot

日期：2026-11-12，Asia/Shanghai

## Resume state

- Repository: `/Users/wangshubo/develop/opennavicat`
- Branch: `main`
- Snapshot base commit: `bcf91edf0e8c9d5a3d09cc2b6ac20a8d9d42b426`
- This snapshot itself is committed immediately after the base commit; use the
  current `git log -1` as the authoritative resume HEAD.
- Remote: `git@github.com:shubolab/opennavicat.git`
- Local and `origin/main` must match when this snapshot is consumed.
- Reference: `/Users/wangshubo/Downloads/Navicat Premium.app`, Navicat Premium
  **18.0.5 build 321**.
- Latest public installer remains **v0.10.0**. No new six-target Release was
  published from the current development commits.
- The full target remains open. This is a pause/handoff file, not a completion
  claim.

## Recently completed

### MongoDB aggregation pipeline

- Reference-backed 42-stage catalog and all 42 Stage Editor help templates.
- Reference create-tool availability is limited to `$match`, `$project`,
  `$sort`, `$limit` and `$skip`.
- `$match`, `$project`, `$sort`, `$limit`, `$skip` structured create tools.
- Source preview modes `全部 / 最初 / 随机` (`0 / 1 / 2`).
- Navicat `Version 0.0` `.nagg` import/export, legacy OpenNavicat `.nagg`
  import, unknown-field retention and exact BSON stage values.
- Observed 588-byte `$lookup` file is byte-identical with SHA-256
  `bde5e0bc931dcdab5d922fa5cf0f19f4da148639d23001c52fb414838900dd0a`.
- `MQL` JavaScript comments are preserved while untouched and replaced when the
  user edits the stage.
- Options pane maps disk use, batch size, timeout, bypass validation, read
  concern, collation, hint, comment, write concern and `let` into preview,
  execute and Explain.
- Visual Explain is a native left-to-right `QGraphicsView` canvas with elapsed
  badges, edges, threshold highlighting, search dimming, zoom/pan and a floating
  Canonical-EJSON node detail pane.

### MongoDB validator

- 22 BSON types plus `(Multiple)`, properties/root schema, typed constraints,
  ordered properties, independent Schema/Query drafts and lossless fragments.
- Strict/moderate/off, warn/error, clearing, real collMod, UUID protection,
  stale catalog checks, cancellation and reopen paths are tested.
- Exact `$jsonSchema` documents that cannot be safely projected now open in the
  raw Query editor with all attributes and BSON types preserved.
- A real owned MongoDB 8.0.34 test created a 1,001-property Schema, edited its
  title through raw Query, saved and independently read back the full result.

### README demonstration

The workspace contains one actual OpenNavicat GIF with no reference screenshots:

- Phases: **25**
- Encoded frames: **326**
- Duration: **54.34 seconds**
- SHA-256:
  `56d3f7add673e20c7e4e6caab5b9ee6b10d725223dab0129edcaaffb8127caa6`
- Receipt: `docs/assets/opennavicat-demo.json`
- GIF: `docs/assets/opennavicat-demo.gif`

## Latest verification

- Full source suite: **1,821 passed / 563 conditionally skipped**
- Exact-owned MongoDB collection runner: **290 passed / 0 skipped**, eight
  exact-owned Mongo 8.0.34 fixtures
- Cocoa collection-designer UI: **34 passed**
- Cocoa pipeline UI: **14 passed**
- Source smoke: **6 rows × 4 columns**
- CUA was exercised on Navicat 18.0.5 and remains available.
- REA MCP **6.5.0** is reachable; Ghidra **12.1.4** is installed and available.

The owned reference containers, anonymous volumes and temporary reference
objects used for the observations were removed after checking exact container
IDs, labels, loopback ports and cleanup absence.

## Important files

- `docs/parity-matrix.md` — authoritative open/partial/verified ledger.
- `docs/roadmap.md` — sequencing; it does not narrow completion scope.
- `docs/ui-reference-18-mongodb-pipeline.md` — AP18-01–AP18-08 observations.
- `docs/ui-reference-18-mongodb-validation.md` — MV18-01–MV18-06 observations.
- `docs/snapshot/2026-11-12-progress.md` — chronological implementation record.
- `opennavicat/mongodb_pipeline.py` — bounded pipeline model and `.nagg`.
- `opennavicat/mongodb_stage_templates.py` — 42 reference editor templates.
- `opennavicat/ui/mongo_pipeline.py` — pipeline page, Options and Explain canvas.
- `opennavicat/mongodb_validator.py` — lossless validator patch model.
- `opennavicat/ui/mongo_validator.py` — Schema/Query visual editor.
- `opennavicat/ui/window.py` — serial background database task routing.
- `scripts/record-demo.py` — isolated six-scenario GIF recorder.
- `scripts/test-mongodb-collections.py` — pinned owned MongoDB runner.

## Next work, in order

1. MongoDB pipeline automatic-task references and change-stream workflow.
2. Larger branched, sharded and replica-set Explain plan shapes; exact cost
   highlighting formula and the remaining Windows/Linux reference layouts.
3. Validator lossless conversion rules for external/non-projectable validators,
   all unknown/root-schema combinations and isolated boundary semantics.
4. Continue the remaining MongoDB object tools and the other database drivers;
   do not treat the current Mongo checkpoints as full OpenNavicat parity.
5. Resume the complete six-target release process only after all gates required
   by the repository instructions and parity matrix pass.

## Resume commands

```bash
cd /Users/wangshubo/develop/opennavicat
git status --short --branch
git log -3 --oneline
git ls-remote origin refs/heads/main
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python scripts/test-mongodb-collections.py \
  --evidence /tmp/opennavicat-mongodb-resume-$(date +%s) --timeout 480
QT_QPA_PLATFORM=offscreen .venv/bin/python -m opennavicat --smoke-test
```

Reuse only new exact-owned loopback fixtures. Never use an existing Navicat
profile, production database, credential, reference screenshot or raw reference
asset in application packages or committed demo material.

## Suggested next goal text

> 继续并完成 OpenNavicat 全目标：从 `docs/snapshot/2026-11-12-goal-resume.md`
> 和当前 `main` 恢复，基于 Navicat Premium 18.0.5 build 321 的实际行为继续实现
> 完整三平台原生 Qt UI 与数据库工具能力。优先处理 Mongo Pipeline 自动任务、
> change stream、大型/分片/副本集视觉计划，继续关闭验证器外部转换与未知规则，
> 随后扩展其余数据库与对象工具。保持完整 parity ledger、CUA/REA 可用、六目标
> 安装器门禁和所有安全边界；持续提交推送，不以单个里程碑替代完整目标。

