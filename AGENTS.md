# OpenNavicat development

Build an independent native Qt implementation for Windows, Linux and macOS
with **1:1 Navicat UI and the complete Navicat database-tool functionality**.
The user's corrected scope is authoritative: the locally installed Navicat
Premium is the reference to reproduce, not merely inspiration for a custom
workbench. A usable core, a different UI, a feature demo, passing tests or native
packages do not satisfy the goal. Track every remaining gate in
`docs/parity-matrix.md`; implementation order must not narrow completion scope.

Exclude Navicat product accounts, product login/registration, activation,
subscription/licensing flows and Navicat cloud-account/team-account collaboration.
Retain database authentication, database users/roles/grants, and cloud-hosted
database connections/aliases as database-tool functions. Do not confuse a
database login dialog with an excluded product-account login.
Reference-supported database authentication remains in scope even when its
endpoint is hosted by a cloud provider. Excluded product-account areas may be
removed; that exception does not permit redesigning the retained tool UI.

- Reproduce the reference's visual hierarchy, navigation categories, toolbar
  groups, object-list columns, panes, dialogs, wizards, shortcuts, state changes
  and complete functional workflows. The existing custom dark sidebar and
  simplified toolbar are a partial prototype, not an accepted alternative UI.
- Inspect the actual reference application with owned temporary fixtures before
  claiming runtime/UI parity. Static labels/resources establish declarations,
  not executed behavior. Keep static Evidence IDs and live observations distinct.
- Keep UI parity and tool-function parity as separate acceptance gates. A button
  without its complete behavior, a generic SQL workaround for a missing tool,
  a screenshot, or a successful connection is not complete feature acceptance.
- Enumerate every applicable reference submenu, dialog tab, wizard option and
  driver-specific object/function; update the matrix when new surfaces are found.
  Unknown applicability stays open until the reference is observed; do not drop
  a function merely because the current implementation lacks it.
- Completion requires reference-backed acceptance on Windows, Linux and macOS.
  OS-specific reference behavior must be verified rather than assumed from the
  macOS binary; native platform conventions do not justify an unrelated layout.

The reference identity and evidence limitations are recorded in
`docs/navicat-reference.md`; `docs/roadmap.md` defines sequencing, not a smaller
definition of success.

The user has authorized regular commits and pushes to
`git@github.com:shubolab/opennavicat.git`. Keep reviewable working milestones in
that repository. Every usable release milestone must publish an actual installer
for **Windows, Linux and macOS, each on amd64 and arm64** (six native targets).
Build and smoke-test on each target OS/architecture. A ZIP, source archive,
workflow declaration or emulated x64 executable is not an arm64 installer.
Publish a release only after all six installer assets and checksums are verified;
clearly describe the implemented milestone and remaining full-parity gaps.

The user subsequently requested pausing this goal **after the first usable
six-target installer Release is published and verified downloadable**. Finish
that release first, then pause without starting another feature milestone. The
user plans to update the local Navicat installation. On an explicit resume,
inspect the then-installed version and its actual UI again, and use that latest
reference to continue the original complete-function/UI goal. Do not assume the
current 16.1.1 observations establish parity with the future updated reference.

- Use `.venv/bin/python` on macOS/Linux; the workspace environment uses Python 3.13.
- Keep all Qt widget reads and writes on the GUI thread. Database work goes
  through the window's serial background task queue. Cancellation has a separate
  worker so an active query cannot block its cancellation.
- Bound result delivery and label truncation. Do not claim that PostgreSQL's
  ordinary cursor bounds libpq memory or server execution work.
- Quote database identifiers by dialect; bind values. Never concatenate a value
  from a filter, imported row or record editor into SQL.
- Require primary keys and original-row conflict checks for grid mutations.
  Multi-row grid edits, imports and comparison application must be atomic.
- Data comparison is preview-first, 10,000 rows per table, exact column/PK
  mapping. Target-only deletion is opt-in. Reject stale profile or schema bindings.
- Store credentials only in the OS keyring or process memory. Do not add plaintext
  fallbacks. SQL drafts/history are local and preserve the user's SQL verbatim.
- Test destructive behavior only with temporary SQLite files or owned disposable
  database containers. Never import existing Navicat profiles or access a user's
  production databases to test the application.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` runs the native UI
  tests. Real database integration opts in through the documented test port envs.
- `.venv/bin/python -m opennavicat --smoke-test` exercises the async application
  path using an isolated sample database. Run the same command on packaged output.
- Build each target on its own OS. CI configuration, source tests, package build,
  packaged smoke and human acceptance are separate evidence; report them accurately.
- REA is for shipped-reference artifacts, not routine source analysis. Retain
  Evidence IDs and limitations, and close sessions after exporting needed evidence.
- Do not redistribute Navicat code, artwork, interface archives or raw reference
  evidence inside application packages. The app has no runtime dependency on it.
