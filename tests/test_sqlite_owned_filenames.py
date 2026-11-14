"""Actual generated SQLite fixtures must also be valid Windows file names."""
from contextlib import closing
import importlib
import inspect
import ntpath
import sqlite3

import pytest


@pytest.mark.parametrize("module_name, fixture_name", [
    ("test_sqlite_connection_databases_ui", "files"),
    ("test_sqlite_database_execution", "files"),
    ("test_sqlite_backup_binding", "backup_source"),
])
def test_owned_sqlite_fixture_files_are_portable_real_databases(tmp_path, module_name, fixture_name):
    fixture = getattr(importlib.import_module(module_name), fixture_name).__wrapped__
    result = fixture(tmp_path)
    if inspect.isgenerator(result):
        try:
            _session, paths_by_alias = next(result)
            paths = tuple(paths_by_alias.values())
        finally:
            result.close()
    else:
        paths = result
    assert len(paths) == 3
    for path in paths:
        # ntpath uses Windows rules on every host, including AUX.sqlite.
        assert not ntpath.isreserved(path.name), path.name
        assert path.is_file() and path.suffix == ".sqlite"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            table = "things" if fixture_name == "backup_source" else "items"
            assert connection.execute('SELECT count(*) FROM "' + table + '"').fetchone() == (2,)
