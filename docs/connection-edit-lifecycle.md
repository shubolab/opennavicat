# Accepted connection settings and asynchronous document saves

This isolated change is based on `d948f24` for a subsequent milestone. It is not
part of the frozen 0.8.1 combo-layout patch or a claim of completed Navicat parity.

An owned SQLite reproduction confirmed a settings-loss bug. The connection
settings dialog had already returned **Accepted** and was no longer visible,
with its result explicitly selecting a second SQLite file and read-only mode.
When an existing dirty table designer then required Save and its DDL preview was
confirmed, the actual table change completed and the connection disconnected,
but the accepted connection settings were abandoned. The existing after-save
callback resumed only `disconnect()`, after `new_connection()` had returned.

The original suspicion of a normal same-ID settings edit retaining a writable
old session was not reproduced. An ordinary edit already closed the first
SQLite session, connected to the second file, enabled `PRAGMA query_only`, and
rejected an INSERT. Source discovery also already pins the active session's
configuration until explicit disconnect; that existing regression remains green.

The corrected flow captures the accepted configuration as an immutable in-memory
snapshot and captures all four applicable credential-persistence choices once on
the GUI thread. It passes completion and retry callbacks through the existing
disconnect/document-save path, retaining the same payload across the DDL sheet
and worker completion. It adopts and persists the new settings only after the
exact old session is closed. Manual profiles reconnect as before; reviewed
Navicat imports remain disconnected until the user opens them.

Cancellation, invalid document input, save failure, close failure, replacement
by a newer edit, or a closed window withdraws the continuation. A later manual
document save cannot revive a cancelled connection edit. The settings dialog is
not reopened and no additional confirmation flow is introduced. Acknowledged DDL
is not automatically undone if a later settings/keychain operation fails.

`disconnect(connection_id)` retains its existing boolean contract for callers.
Its optional `after` callback now runs after actual completion, including the
MongoDB owner-worker close; `retry` carries the same guarded settings operation
through existing dirty-document Save choices. Worker tasks never read settings
widgets. The in-memory snapshot hides credentials from its representation, and
durable credential handling remains in `Store`.

Validation uses generated profiles, two temporary SQLite files, actual Qt
settings/DDL dialogs, and the actual background queue. MongoDB close and reconnect
contracts use controlled clients and a native query page without opening any
network endpoint. The four-secret test uses a process-local fake keyring and
asserts the workspace contains none of the generated secrets. It does not claim
fresh TLS, SSH, native JS VM, installer, Windows/Linux desktop, or reference-app
acceptance.

The focused test module covers:

- Physical SQLite file switching and database-enforced read-only mode.
- Cancellation of dirty table data and of the actual settings dialog.
- Accepted/closed settings surviving a real SQLite designer save, for both
  manual and imported profiles.
- DDL preview cancellation and a real independent SQLite schema-conflict failure.
- Busy-queue delivery after mutation and deletion of the original Qt dialog,
  preserving the original settings and each applicable remember choice.
- MongoDB close-in-progress blocking native JS, then using the new configuration;
  a failed close instead preserves the old settings and cancels the edit.
- A late queued callback after actual window close performing no save or reopen.

Reproduce the focused tests with:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -W error tests/test_connection_lifecycle.py
QT_QPA_PLATFORM=cocoa .venv/bin/python -m pytest -q -W error tests/test_connection_lifecycle.py
```

Related existing coverage is in `test_table_designer_integration.py`,
`test_navicat_profile_window.py`, `test_mongo_stop_marker_window.py`, `test_ui.py`,
and `test_connection_manager.py`. Local JUnit evidence is stored under the
ignored `artifacts/connection-lifecycle/` directory; these focused suites are not
an aggregate full-source or release result.
