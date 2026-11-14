"""Optional real SQLite3MC contracts, using only new owned database files."""
import hashlib
from importlib import metadata
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from opennavicat import sqlite_cipher_provider as provider


@pytest.fixture
def native():
    apsw = pytest.importorskip("apsw", reason="Optional encrypted SQLite provider is not installed")
    if getattr(apsw, "mc_version", None) != provider.MC_VERSION:
        pytest.skip("The installed APSW is not the tested SQLite3MC provider")
    return provider._provider()


@pytest.fixture
def connection(tmp_path, native):
    db = provider.connect(tmp_path / "generated-owned.sqlite", key="generated-owned-key", cipher="aes256cbc", create=True)
    try:
        yield db
    finally:
        db.close()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_import_does_not_eagerly_load_an_optional_native_provider():
    child = subprocess.run([sys.executable, "-c",
        "import sys;import opennavicat.sqlite_cipher_provider;assert 'apsw' not in sys.modules"],
        text=True, capture_output=True, timeout=10)
    assert child.returncode == 0, child.stderr


def test_missing_provider_fails_before_creating_any_database(tmp_path, monkeypatch):
    def missing(_name):
        raise metadata.PackageNotFoundError("generated missing provider")
    monkeypatch.setattr(provider.metadata, "version", missing)
    path = tmp_path / "must-not-be-created.sqlite"
    with pytest.raises(provider.ProviderUnavailable) as caught:
        provider.connect(path, key="generated-owned-key", create=True)
    assert not path.exists() and caught.value.__context__ is None


def test_overlapping_regular_apsw_is_rejected_before_file_creation(tmp_path, monkeypatch, native):
    actual = provider.metadata.version
    monkeypatch.setattr(provider.metadata, "version", lambda name: "generated-overlapping" if name == "apsw" else actual(name))
    path = tmp_path / "must-not-be-created.sqlite"
    with pytest.raises(provider.ProviderUnavailable):
        provider.connect(path, key="generated-owned-key", create=True)
    assert not path.exists()


VARIANTS = (
    ("aes128cbc", None), ("aes256cbc", None), ("chacha20", None), ("sqlcipher", None),
    ("rc4", None), ("ascon128", None), ("aes128cbc", 1), ("aes256cbc", 1),
    ("chacha20", 1), ("rc4", 1), ("sqlcipher", 1), ("sqlcipher", 2), ("sqlcipher", 3), ("sqlcipher", 4),
)
LEGACY_VARIANTS = tuple((cipher, legacy) for cipher, legacy in VARIANTS if legacy is not None)


@pytest.mark.parametrize("cipher,legacy", LEGACY_VARIANTS)
def test_explicit_legacy_page_size_applies_to_bound_encrypted_attachment(tmp_path, native, cipher, legacy):
    path = tmp_path / "generated-legacy-attachment.sqlite"
    options = dict(cipher=cipher, legacy=legacy, page_size=8192)
    keyed = provider.connect(path, key="generated-page-attachment-key", create=True, **options)
    keyed.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY)").close()
    keyed.execute("INSERT INTO generated_facts VALUES (?)", (7,)).close()
    keyed.close()
    before = sha(path)
    plain = provider.connect_plain_main(":memory:")
    try:
        plain.configure_attachment(**options)
        plain.execute("ATTACH DATABASE ? AS generated_aux KEY ?", (str(path), "generated-page-attachment-key")).close()
        assert plain.execute("PRAGMA generated_aux.page_size").fetchone() == (8192,)
        assert plain.execute("SELECT * FROM generated_aux.generated_facts").fetchall() == [(7,)]
    finally:
        plain.close()
    assert sha(path) == before


@pytest.mark.parametrize("cipher,legacy,page_size", [
    (cipher, legacy, size) for cipher, legacy in LEGACY_VARIANTS
    for size in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
    if not (cipher == "sqlcipher" and legacy in (2, 3, 4) and size == 512)])
def test_explicit_legacy_page_size_create_reopen_backup_and_wrong_key(tmp_path, native, cipher, legacy, page_size):
    source, target = tmp_path / "generated-page-source.sqlite", tmp_path / "generated-page-target.sqlite"
    options = dict(cipher=cipher, legacy=legacy, page_size=page_size)
    key, backup_key = "generated-page-source-key", "generated-page-backup-key"
    db = provider.connect(source, key=key, create=True, **options)
    try:
        assert db.execute("PRAGMA page_size").fetchone() == (page_size,)
        db.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
        db.executemany("INSERT INTO generated_facts VALUES (?,?)", [(1, "generated one"), (2, "generated two")]).close()
        copied = provider.connect(target, key=backup_key, create=True, **options)
        try:
            db.backup(copied)
        finally:
            copied.close()
    finally:
        db.close()
    for path, correct in ((source, key), (target, backup_key)):
        before = sha(path)
        opened = provider.connect(path, key=correct, read_only=True, **options)
        try:
            assert opened.execute("PRAGMA page_size").fetchone() == (page_size,)
            assert opened.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert opened.execute("SELECT * FROM generated_facts ORDER BY id").fetchall() == [(1, "generated one"), (2, "generated two")]
        finally:
            opened.close()
        with pytest.raises(sqlite3.DatabaseError):
            provider.connect(path, key=correct + "-wrong", read_only=True, **options)
        ordinary = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                ordinary.execute("SELECT * FROM sqlite_schema").fetchall()
        finally:
            ordinary.close()
        assert sha(path) == before


@pytest.mark.parametrize("options", [dict(cipher="ascon128", legacy=1, page_size=8192),
                                     dict(cipher="aes128cbc", page_size=8192),
                                     dict(cipher="sqlcipher", legacy=4, page_size=True),
                                     dict(cipher="sqlcipher", legacy=4, page_size=513),
                                     *(dict(cipher="sqlcipher", legacy=version, page_size=512) for version in (2, 3, 4))])
def test_invalid_explicit_page_configuration_never_opens_target(tmp_path, options):
    path = tmp_path / "generated-invalid-page.sqlite"
    with pytest.raises(sqlite3.NotSupportedError):
        provider.connect(path, key="generated-page-key", create=True, **options)
    assert not path.exists()


@pytest.mark.parametrize("cipher,legacy", VARIANTS)
def test_native_create_wrong_key_readonly_and_closed_reopen_preserve_file(tmp_path, native, cipher, legacy):
    path = tmp_path / "generated quoted ' file.sqlite"
    key = "generated ' owned key 样例"
    db = provider.connect(path, key=key, cipher=cipher, legacy=legacy, create=True)
    assert path.read_bytes()[:16] != b"SQLite format 3\0"
    db.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
    db.execute("INSERT INTO generated_facts VALUES (?,?)", (1, "generated ' value 样例")).close()
    db.close()
    before = sha(path)
    with pytest.raises(sqlite3.DatabaseError):
        provider.connect(path, key=key + "-wrong", cipher=cipher, legacy=legacy, read_only=True)
    db = provider.connect(path, key=key, cipher=cipher, legacy=legacy, read_only=True)
    try:
        assert db.execute("SELECT * FROM generated_facts").fetchall() == [(1, "generated ' value 样例")]
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT INTO generated_facts VALUES (?,?)", (2, "denied"))
    finally:
        db.close()
    ordinary = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            ordinary.execute("SELECT * FROM sqlite_schema").fetchall()
    finally:
        ordinary.close()
    assert sha(path) == before


@pytest.mark.parametrize("cipher,legacy", [("ascon128", 1), ("rc4", 0), ("not-a-cipher", None), ("sqlcipher", 5)])
def test_unknown_configuration_never_creates_a_plain_or_zero_byte_file(tmp_path, cipher, legacy):
    path = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(sqlite3.NotSupportedError):
        provider.connect(path, key="generated-owned-key", cipher=cipher, legacy=legacy, create=True)
    assert not path.exists()


def test_empty_select_description_survives_completion_and_cursor_reuse(connection):
    db = connection
    db.execute("CREATE TABLE generated_facts(id INTEGER,label TEXT)").close()
    cursor = db.execute("SELECT id AS \"generated; name\",label FROM generated_facts WHERE 0")
    expected = (("generated; name", None, None, None, None, None, None), ("label", None, None, None, None, None, None))
    assert cursor.description == expected and cursor.fetchone() is None
    assert cursor.fetchall() == [] and cursor.description == expected and cursor.rowcount == -1
    cursor.execute("INSERT INTO generated_facts VALUES (?,?)", (1, "generated"))
    assert cursor.description is None and cursor.rowcount == 1
    cursor.execute("SELECT id,label FROM generated_facts")
    assert cursor.fetchmany(0) == [] and cursor.fetchone() == (1, "generated")
    assert cursor.fetchmany(3) == [] and cursor.description[0][0] == "id"
    cursor.close()
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()


@pytest.mark.parametrize("tail", ["SELECT 99", "PRAGMA foreign_keys=OFF", "PRAGMA unknown_generated=1", "DROP TABLE generated_facts"])
def test_real_second_statement_is_rejected_before_the_first_mutation(connection, tail):
    db = connection
    db.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY)").close()
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("INSERT INTO generated_facts VALUES (1); " + tail)
    assert db.execute("SELECT * FROM generated_facts").fetchall() == []


def test_multiple_statements_cannot_change_prepare_time_pragma_before_rejection(connection):
    connection.execute("PRAGMA foreign_keys=ON").close()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("PRAGMA foreign_keys=OFF; SELECT 99")
    assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)


@pytest.mark.parametrize("text", ["';'", "'a'';b'", '"generated;column"', "`generated;column`", "[generated;column]",
                                      "1 /* generated ;;;; */", "1 -- generated ;;;;\n"])
def test_candidate_filter_keeps_native_quoted_comment_and_tail_rules(connection, text):
    if "column" in text:
        sql = f"SELECT 1 AS {text};"
    else:
        sql = f"SELECT {text};"
    assert connection.execute(sql).fetchone() is not None
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute(sql + " PRAGMA foreign_keys=OFF;")


@pytest.mark.parametrize("kind", ["quoted", "comment", "leading-noops"])
def test_semicolon_dense_input_only_scans_real_statement_prefix_once(connection, monkeypatch, kind):
    module = connection._module
    original, lengths = module.complete, []
    def counted(sql):
        lengths.append(len(sql))
        return original(sql)
    monkeypatch.setattr(module, "complete", counted)
    dense = ";" * 100000
    sql = ("SELECT '" + dense + "';" if kind == "quoted" else
           "SELECT 1 /*" + dense + "*/;" if kind == "comment" else dense + "SELECT 1;")
    assert connection.execute(sql).fetchone() is not None
    assert len(lengths) == 1 and sum(lengths) <= len(sql)


def test_incomplete_trigger_prefix_budget_rejects_without_mutation_or_truncation(connection):
    module, raw = connection._module, connection._raw
    connection.execute("CREATE TABLE generated_facts(id INTEGER)").close()
    old = raw.limit(module.SQLITE_LIMIT_SQL_LENGTH, 100000)
    sql = "CREATE TRIGGER generated_dense AFTER INSERT ON generated_facts BEGIN " + "SELECT 1;" * 500 + " END;"
    assert len(sql) < 100000
    try:
        with pytest.raises(sqlite3.DataError):
            connection.execute(sql)
        assert connection.execute("SELECT name FROM sqlite_schema WHERE type='trigger'").fetchall() == []
    finally:
        raw.limit(module.SQLITE_LIMIT_SQL_LENGTH, old)
    assert sql.endswith(" END;") and len(sql) > 4000


def test_interrupt_during_python_preflight_is_cooperative_and_next_query_reuses_connection(connection, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = connection._preflight_checkpoint
    first, errors = [True], []
    def checkpoint():
        if first[0]:
            first[0] = False
            entered.set()
            assert release.wait(2)
        original()
    monkeypatch.setattr(connection, "_preflight_checkpoint", checkpoint)
    def query():
        try:
            connection.execute("SELECT '" + ";" * 100000 + "';").fetchall()
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=query)
    worker.start()
    assert entered.wait(2)
    started = time.monotonic()
    connection.interrupt()
    release.set()
    worker.join(2)
    assert not worker.is_alive() and time.monotonic() - started < 2
    assert len(errors) == 1 and isinstance(errors[0], sqlite3.OperationalError)
    assert errors[0].sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
    assert connection.execute("SELECT 7").fetchone() == (7,)


def test_registered_progress_handler_can_cancel_before_native_prepare(connection):
    connection.set_progress_handler(lambda: 1, 1000)
    with pytest.raises(sqlite3.OperationalError) as caught:
        connection.execute("PRAGMA foreign_keys=OFF")
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
    connection.set_progress_handler(None)
    assert connection.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.parametrize("suffix", [" -- SELECT 99;", " /* SELECT 99; */", "; /* extra empty statement */", " /* unclosed comment"])
def test_engine_parsed_semicolons_and_trailing_comments_remain_one_statement(connection, suffix):
    cursor = connection.execute("SELECT '; generated literal' AS \"generated; name\";" + suffix)
    assert cursor.description[0][0] == "generated; name"
    assert cursor.fetchall() == [("; generated literal",)]
    assert cursor.description[0][0] == "generated; name"


def test_trigger_body_semicolons_and_dml_counts_use_sqlite_parser_and_changes(connection):
    db = connection
    db.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY)").close()
    db.execute("CREATE TABLE generated_audit(id INTEGER)").close()
    db.execute("CREATE TRIGGER generated_trigger AFTER INSERT ON generated_facts BEGIN "
               "INSERT INTO generated_audit VALUES (new.id); INSERT INTO generated_audit VALUES (new.id+10); END;").close()
    assert db.execute("INSERT INTO generated_facts VALUES (?)", (1,)).rowcount == 1
    assert db.execute("UPDATE generated_facts SET id=id WHERE 0").rowcount == 0
    assert db.execute("SELECT * FROM generated_audit").fetchall() == [(1,), (11,)]
    assert db.execute("CREATE TABLE generated_other(id INTEGER)").rowcount == -1


def test_transaction_and_executemany_constraint_failure_can_roll_back_atomically(connection):
    db = connection
    db.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
    db.execute("BEGIN").close()
    assert db.in_transaction
    with pytest.raises(sqlite3.IntegrityError):
        db.executemany("INSERT INTO generated_facts VALUES (?,?)", [(1, "first"), (1, "duplicate")])
    db.rollback()
    assert not db.in_transaction and db.execute("SELECT * FROM generated_facts").fetchall() == []
    db.execute("BEGIN").close()
    cursor = db.executemany("INSERT INTO generated_facts VALUES (?,?)", [(1, "first"), (2, "second")])
    assert cursor.rowcount == 2
    db.commit(); db.commit()
    assert not db.in_transaction and db.execute("SELECT COUNT(*) FROM generated_facts").fetchone() == (2,)


def test_fixed_rollback_cleanup_works_while_progress_cancellation_remains_requested(connection):
    connection.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY)").close()
    connection.execute("BEGIN").close()
    connection.execute("INSERT INTO generated_facts VALUES (1)").close()
    connection.set_progress_handler(lambda: True, 1000)
    connection.interrupt()
    connection.rollback()
    assert not connection.in_transaction
    # Cleanup restores, rather than silently discards, the caller's callback.
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("SELECT 1")
    connection.set_progress_handler(None)
    assert connection.execute("SELECT * FROM generated_facts").fetchall() == []


def test_fixed_savepoint_cleanup_retains_real_outer_changes_after_cancellation(connection):
    connection.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY)").close()
    connection.execute("BEGIN").close()
    connection.execute("INSERT INTO generated_facts VALUES (1)").close()
    connection.execute("SAVEPOINT generated_batch").close()
    connection.execute("INSERT INTO generated_facts VALUES (2)").close()
    connection.set_progress_handler(lambda: True, 1000)
    connection.rollback(savepoint="generated_batch")
    assert connection.in_transaction
    connection.set_progress_handler(None)
    assert connection.execute("SELECT * FROM generated_facts").fetchall() == [(1,)]
    connection.rollback()
    assert not connection.in_transaction


def test_executemany_rejects_returning_before_the_first_row_is_written(connection):
    connection.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY)").close()
    with pytest.raises(sqlite3.NotSupportedError):
        connection.executemany("INSERT INTO generated_facts VALUES (?) RETURNING id", [(1,), (2,)])
    assert connection.execute("SELECT * FROM generated_facts").fetchall() == []


def test_public_rekey_cannot_turn_a_keyed_backup_target_into_plaintext(tmp_path, native):
    path = tmp_path / "generated-keyed-target.sqlite"
    target = provider.connect(path, key="generated-owned-key", cipher="aes256cbc", create=True)
    try:
        before = sha(path)
        with pytest.raises(sqlite3.NotSupportedError):
            target.execute("PRAGMA rekey=''")
        assert sha(path) == before and path.read_bytes()[:16] != b"SQLite format 3\0"
    finally:
        target.close()


def test_existing_authorizer_is_respected_and_restored_after_rejected_query(connection, native):
    connection.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY)").close()
    def policy(action, table, _column, _schema, _source):
        return native.SQLITE_DENY if action == native.SQLITE_READ and table == "generated_facts" else native.SQLITE_OK
    connection._raw.set_authorizer(policy)
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("SELECT * FROM generated_facts")
    assert connection._raw.authorizer is policy
    assert connection.execute("SELECT 42").fetchone() == (42,)
    assert connection._raw.authorizer is policy
    connection._raw.set_authorizer(None)


def test_fetchmany_is_incremental_and_limits_progress_interrupt_can_reuse_connection(connection):
    db = connection
    assert db.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) > 0
    cursor = db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT x FROM n")
    assert cursor.fetchmany(2) == [(1,), (2,)] and cursor.fetchmany(2) == [(3,), (4,)]
    cursor.close()
    db.set_progress_handler(lambda: True, 100)
    with pytest.raises(sqlite3.OperationalError) as caught:
        db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT SUM(x) FROM n")
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
    db.set_progress_handler(None, 0)
    assert db.execute("SELECT 7").fetchone() == (7,)


def test_separate_cancellation_thread_interrupts_real_native_query_then_reuses(connection):
    db = connection
    entered, done, errors = threading.Event(), threading.Event(), []
    db.set_progress_handler(lambda: entered.set() or False, 1000)
    def query():
        try:
            db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT SUM(x) FROM n").fetchall()
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    worker = threading.Thread(target=query, daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        db.interrupt()
        assert done.wait(5)
        worker.join(timeout=1)
        assert not worker.is_alive() and len(errors) == 1
        assert isinstance(errors[0], sqlite3.OperationalError) and errors[0].sqlite_errorcode == sqlite3.SQLITE_INTERRUPT
        db.set_progress_handler(None, 0)
        assert db.execute("SELECT 8").fetchone() == (8,)
    finally:
        if worker.is_alive():
            db.interrupt(); worker.join(timeout=5)


def test_errors_do_not_export_sql_key_bindings_notes_or_raw_provider_context(connection):
    secret = "generated-sensitive-value"
    for sql, parameters in [("SELECT " + secret, ()), ("SELECT ?", (secret, secret))]:
        with pytest.raises(sqlite3.Error) as caught:
            connection.execute(sql, parameters)
        error = caught.value
        assert secret not in str(error) + repr(error) + repr(getattr(error, "__notes__", None))
        assert error.__cause__ is None and error.__context__ is None


def test_backup_requires_a_keyed_provider_target_and_preserves_encryption(tmp_path, connection):
    source = connection
    source.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
    source.execute("INSERT INTO generated_facts VALUES (?,?)", (1, "generated backup row")).close()
    target_path = tmp_path / "generated-keyed-backup.sqlite"
    target = provider.connect(target_path, key="generated-target-key", cipher="aes256cbc", create=True)
    try:
        source.backup(target, pages=1)
    finally:
        target.close()
    target = provider.connect(target_path, key="generated-target-key", cipher="aes256cbc", read_only=True)
    try:
        assert target.execute("SELECT * FROM generated_facts").fetchall() == [(1, "generated backup row")]
    finally:
        target.close()
    with pytest.raises(sqlite3.DatabaseError):
        provider.connect(target_path, key="generated-owned-key", cipher="aes256cbc", read_only=True)
    plain_path = tmp_path / "generated-plain-target.sqlite"
    plain = sqlite3.connect(plain_path)
    try:
        before = sha(plain_path)
        with pytest.raises(sqlite3.NotSupportedError):
            source.backup(plain)
        assert sha(plain_path) == before
    finally:
        plain.close()


def test_backup_progress_failure_rolls_back_existing_keyed_target(tmp_path, connection):
    source = connection
    source.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,payload BLOB)").close()
    source.executemany("INSERT INTO generated_facts VALUES (?,?)", [(i, b'x' * 4000) for i in range(60)]).close()
    target = provider.connect(tmp_path / "generated-target.sqlite", key="generated-target-key", cipher="aes256cbc", create=True)
    target.execute("CREATE TABLE generated_original(id INTEGER PRIMARY KEY)").close()
    target.execute("INSERT INTO generated_original VALUES (42)").close()
    def cancel(*_args):
        raise RuntimeError("generated-sensitive-cancellation-detail")
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            source.backup(target, pages=1, progress=cancel)
        assert "sensitive" not in str(caught.value) and caught.value.__context__ is None
        assert target.execute("SELECT * FROM generated_original").fetchall() == [(42,)]
        assert target.execute("SELECT name FROM sqlite_schema WHERE name='generated_facts'").fetchall() == []
    finally:
        target.close()


def test_explicit_plain_main_attaches_encrypted_file_and_unknown_backup_schema_is_rejected(tmp_path, native):
    encrypted_path, plain_path = tmp_path / "generated encrypted ' file.sqlite", tmp_path / "generated-plain.sqlite"
    encrypted = provider.connect(encrypted_path, key="generated-attachment-key", cipher="aes256cbc", create=True)
    encrypted.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
    encrypted.execute("INSERT INTO generated_facts VALUES (?,?)", (1, "generated attachment row")).close()
    encrypted.close()
    plain = provider.connect_plain_main(plain_path, create=True)
    plain.execute("CREATE TABLE generated_main(id INTEGER PRIMARY KEY)").close()
    plain.close()
    before = sha(encrypted_path), sha(plain_path)
    plain = provider.connect_plain_main(plain_path, read_only=True)
    target_path = tmp_path / "generated-attachment-backup.sqlite"
    target = provider.connect(target_path, key="generated-backup-key", cipher="aes256cbc", create=True)
    try:
        plain.configure_attachment(cipher="aes256cbc")
        plain.execute("ATTACH DATABASE ? AS generated_aux KEY ?", (str(encrypted_path), "generated-attachment-key")).close()
        assert plain.execute("SELECT * FROM generated_aux.generated_facts").fetchall() == [(1, "generated attachment row")]
        target_before = sha(target_path)
        with pytest.raises(sqlite3.NotSupportedError):
            plain.backup(target, name="generated_aux", pages=1)
        assert sha(target_path) == target_before
        assert target.execute("SELECT name FROM sqlite_schema WHERE name='generated_facts'").fetchall() == []
        with pytest.raises(sqlite3.NotSupportedError):
            target.backup(plain)
        plain.execute("DETACH DATABASE generated_aux").close()
        plain.configure_attachment(cipher="aes256cbc")
        with pytest.raises(sqlite3.DatabaseError):
            plain.execute("ATTACH DATABASE ? AS generated_aux KEY ?", (str(encrypted_path), "generated-wrong-key"))
    finally:
        target.close(); plain.close()
    assert (sha(encrypted_path), sha(plain_path)) == before


@pytest.mark.parametrize("cipher,legacy", VARIANTS)
def test_matching_cipher_backup_reopens_two_rows_with_target_key(tmp_path, native, cipher, legacy):
    source_path, target_path = tmp_path / "generated-source.sqlite", tmp_path / "generated-target.sqlite"
    source = provider.connect(source_path, key="generated-source-key", cipher=cipher, legacy=legacy, create=True)
    target = provider.connect(target_path, key="generated-target-key", cipher=cipher, legacy=legacy, create=True)
    try:
        source.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
        source.executemany("INSERT INTO generated_facts VALUES (?,?)", [(1, "first"), (2, "second")]).close()
        source.backup(target, pages=1)
    finally:
        target.close(); source.close()
    before = sha(source_path), sha(target_path)
    with pytest.raises(sqlite3.DatabaseError):
        provider.connect(target_path, key="generated-source-key", cipher=cipher, legacy=legacy, read_only=True)
    target = provider.connect(target_path, key="generated-target-key", cipher=cipher, legacy=legacy, read_only=True)
    try:
        assert target.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert target.execute("SELECT * FROM generated_facts ORDER BY id").fetchall() == [(1, "first"), (2, "second")]
    finally:
        target.close()
    ordinary = sqlite3.connect(target_path.as_uri() + "?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            ordinary.execute("SELECT * FROM sqlite_schema").fetchall()
    finally:
        ordinary.close()
    assert before == (sha(source_path), sha(target_path))


@pytest.mark.parametrize("cipher,legacy", VARIANTS)
def test_reserved_empty_backup_target_uses_certified_source_format_without_early_write(tmp_path, native, cipher, legacy):
    source_path, target_path = tmp_path / "generated-source.sqlite", tmp_path / "generated-reserved.sqlite"
    source = provider.connect(source_path, key="generated-source-key", cipher=cipher, legacy=legacy, create=True)
    target = None
    try:
        source.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
        source.executemany("INSERT INTO generated_facts VALUES (?,?)", [(1, "first"), (2, "second")]).close()
        with target_path.open("xb"):
            pass
        target = source.open_backup_target(target_path, key="generated-target-key")
        assert target_path.read_bytes() == b""
        assert target._backup_configuration() == source._backup_configuration()
        source.backup(target)
    finally:
        if target is not None:
            target.close()
        source.close()
    reopened = provider.connect(target_path, key="generated-target-key", cipher=cipher, legacy=legacy, read_only=True)
    try:
        assert reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert reopened.execute("SELECT * FROM generated_facts ORDER BY id").fetchall() == [(1, "first"), (2, "second")]
    finally:
        reopened.close()
    with pytest.raises(sqlite3.DatabaseError):
        provider.connect(target_path, key="generated-source-key", cipher=cipher, legacy=legacy, read_only=True)
    with sqlite3.connect(target_path.as_uri() + "?mode=ro", uri=True) as ordinary:
        with pytest.raises(sqlite3.DatabaseError):
            ordinary.execute("SELECT * FROM sqlite_schema").fetchall()
    ordinary.close()


@pytest.mark.parametrize("cipher", ["aes128cbc", "aes256cbc", "chacha20", "sqlcipher", "ascon128"])
def test_backup_target_preserves_actual_nondefault_page_size_for_normal_cipher(tmp_path, native, cipher):
    source_path, target_path = tmp_path / "generated-8192.sqlite", tmp_path / "generated-copy.sqlite"
    raw = native.Connection(str(source_path))
    raw.pragma("page_size", 8192)
    raw.pragma("cipher", cipher)
    raw.pragma("key", "generated-source-key")
    raw.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)")
    raw.executemany("INSERT INTO generated_facts VALUES (?,?)", [(1, "first"), (2, "second")])
    raw.close()
    source = provider.connect(source_path, key="generated-source-key", cipher=cipher)
    target = None
    try:
        assert source.execute("PRAGMA page_size").fetchone() == (8192,)
        with target_path.open("xb"):
            pass
        target = source.open_backup_target(target_path, key="generated-target-key")
        assert target_path.read_bytes() == b"" and target.execute("PRAGMA page_size").fetchone() == (8192,)
        source.backup(target)
    finally:
        if target is not None:
            target.close()
        source.close()
    reopened = provider.connect(target_path, key="generated-target-key", cipher=cipher, read_only=True)
    try:
        assert reopened.execute("PRAGMA page_size").fetchone() == (8192,)
        assert reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert reopened.execute("SELECT * FROM generated_facts ORDER BY id").fetchall() == [(1, "first"), (2, "second")]
    finally:
        reopened.close()


def test_backup_target_requires_reserved_empty_file_and_never_changes_existing_database(tmp_path, connection):
    missing = tmp_path / "generated-missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        connection.open_backup_target(missing, key="generated-target-key")
    assert not missing.exists()
    existing = tmp_path / "generated-existing.sqlite"
    with sqlite3.connect(existing) as ordinary:
        ordinary.execute("CREATE TABLE generated_original(id INTEGER)")
        ordinary.execute("PRAGMA user_version=73")
    ordinary.close()
    before = sha(existing)
    with pytest.raises(sqlite3.NotSupportedError):
        connection.open_backup_target(existing, key="generated-target-key")
    assert sha(existing) == before


@pytest.mark.parametrize("cipher,legacy", (
    ("aes128cbc", None), ("chacha20", None), ("sqlcipher", None), ("rc4", None), ("ascon128", None),
    ("aes128cbc", 1), ("aes256cbc", 1), ("chacha20", 1), ("rc4", 1),
    ("sqlcipher", 1), ("sqlcipher", 2), ("sqlcipher", 3), ("sqlcipher", 4),
))
def test_cross_codec_backup_is_rejected_before_existing_target_changes(tmp_path, connection, cipher, legacy):
    path = tmp_path / "generated-existing-target.sqlite"
    target = provider.connect(path, key="generated-target-key", cipher=cipher, legacy=legacy, create=True)
    try:
        target.execute("CREATE TABLE generated_original(id INTEGER PRIMARY KEY)").close()
        target.execute("INSERT INTO generated_original VALUES (42)").close()
        before = sha(path)
        with pytest.raises(sqlite3.NotSupportedError):
            connection.backup(target)
        assert sha(path) == before
        assert target.execute("SELECT * FROM generated_original").fetchall() == [(42,)]
    finally:
        target.close()


@pytest.mark.parametrize("statement", ["PRAGMA cipher='chacha20'", "PRAGMA legacy=1", "PRAGMA kdf_iter=1000",
    "SELECT sqlite3mc_config('cipher','chacha20')", "SELECT sqlite3mc_config('aes256cbc','kdf_iter',1000)"])
def test_public_cipher_configuration_cannot_invalidate_backup_metadata(connection, statement):
    before = connection._codec
    with pytest.raises(sqlite3.NotSupportedError):
        connection.execute(statement)
    assert connection._codec == before
    assert connection._raw.pragma("cipher") == "aes256cbc"


def test_matching_cipher_with_different_actual_page_size_is_rejected_before_backup(tmp_path, connection, native):
    path = tmp_path / "generated-eight-kib-target.sqlite"
    raw = native.Connection(str(path))
    raw.pragma("page_size", 8192)
    raw.pragma("cipher", "aes256cbc")
    raw.pragma("key", "generated-target-key")
    raw.execute("CREATE TABLE generated_original(id INTEGER PRIMARY KEY)").close()
    raw.execute("INSERT INTO generated_original VALUES (42)").close()
    raw.close()
    target = provider.connect(path, key="generated-target-key", cipher="aes256cbc")
    try:
        assert target._raw.pragma("page_size") == 8192
        assert connection._raw.pragma("page_size") == 4096
        before = sha(path)
        with pytest.raises(sqlite3.NotSupportedError):
            connection.backup(target)
        assert sha(path) == before
        assert target.execute("SELECT * FROM generated_original").fetchall() == [(42,)]
    finally:
        target.close()


@pytest.mark.parametrize("key", [None, "", "generated\0invalid"])
def test_key_absence_never_selects_plain_factory_implicitly(tmp_path, native, key):
    path = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(sqlite3.ProgrammingError):
        provider.connect(path, key=key, create=True)
    assert not path.exists()


def test_uri_mode_does_not_allow_a_key_or_cipher_in_a_filename(tmp_path, native):
    path = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(sqlite3.ProgrammingError):
        provider.connect_plain_main(path.as_uri() + "?mode=rwc&key=generated-owned-key", create=True, uri=True)
    assert not path.exists()


@pytest.mark.parametrize("plain_main", [False, True])
def test_default_existing_file_mode_never_creates_a_missing_database(tmp_path, native, plain_main):
    path = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        if plain_main:
            provider.connect_plain_main(path)
        else:
            provider.connect(path, key="generated-owned-key")
    assert not path.exists()


@pytest.mark.parametrize("options", [{"create": "False"}, {"read_only": 1}, {"uri": 1}, {"timeout": True}])
def test_invalid_open_flags_fail_before_creating_any_file(tmp_path, native, options):
    path = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(sqlite3.ProgrammingError):
        provider.connect(path, key="generated-owned-key", **options)
    assert not path.exists()


@pytest.mark.parametrize("filename", [None, b"generated-bytes.sqlite", "", "generated\0invalid.sqlite"])
def test_invalid_database_filename_never_becomes_an_accidental_created_path(tmp_path, native, monkeypatch, filename):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(sqlite3.ProgrammingError):
        provider.connect(filename, key="generated-owned-key", create=True)
    assert list(tmp_path.iterdir()) == []
