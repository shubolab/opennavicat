"""A saved new SQLite profile stays off until the user opens it."""
from contextlib import closing
import hashlib
import sqlite3

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from opennavicat.storage import Store
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.window import MainWindow


def test_actual_new_sqlite_save_stays_disconnected_until_explicit_open(qtbot, tmp_path, monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, 'delete_password', lambda *_args: None)
    path = tmp_path / 'generated.sqlite'
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('CREATE TABLE owned (id INTEGER PRIMARY KEY, label TEXT)')
        connection.executemany('INSERT INTO owned VALUES (?, ?)', [(1, 'generated one'), (2, 'generated two')])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    store = Store(tmp_path / 'workspace')
    window = MainWindow(store)
    finished = False

    def idle():
        qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount()
                       and not window.cancel_pool.activeThreadCount(), timeout=10000)

    def finish(_widget):
        nonlocal finished
        if finished:
            return
        idle()
        window.close()
        qtbot.waitUntil(lambda: window._closed, timeout=10000)
        idle()
        finished = True

    qtbot.addWidget(window, before_close_func=finish)
    window.show()
    failures, dialogs = [], []

    def accept():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, ConnectionDialog)
            dialogs.append(dialog)
            dialog.name_edit.setText('Generated saved SQLite')
            dialog.database_edit.setText(str(path))
            qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
        except BaseException as error:
            failures.append(error)
            if isinstance(dialog, ConnectionDialog):
                dialog.reject()

    try:
        QTimer.singleShot(0, accept)
        window.new_connection(driver='sqlite')
        if failures:
            raise failures[0]
        assert len(dialogs) == 1
        idle()
        saved, = store.connections()
        assert saved.name == 'Generated saved SQLite' and saved.database == str(path)
        assert window.active_id == saved.id
        assert saved.id not in window.sessions and saved.id not in window._connecting
        window.connect_id(saved.id)
        idle()
        session = window.sessions[saved.id]
        results = []
        window.submit(lambda: session.execute('SELECT id, label FROM main.owned ORDER BY id'), results.extend)
        idle()
        assert results[0].rows == [[1, 'generated one'], [2, 'generated two']]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    finally:
        finish(window)
