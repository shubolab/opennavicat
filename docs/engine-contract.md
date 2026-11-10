# Engine / UI contract

`opennavicat.database` exports `ConnectionConfig`, `QueryResult`, `DatabaseSession`.

`ConnectionConfig` dataclass fields: `id: str`, `name: str`, `driver: str = 'sqlite'`, `database: str = ''`, `host: str = 'localhost'`, `port: int = 0`, `username: str = ''`, `password: str = ''`, `ssl_mode: str = 'prefer'`, `read_only: bool = False`. Drivers: sqlite, postgresql, mysql, mariadb.

`QueryResult` dataclass fields: `columns: list[str]`, `rows: list[list[Any]]`, `affected: int`, `elapsed_ms: float`, `truncated: bool = False`, `sql: str = ''`.

`DatabaseSession(config)` lazy connected. `connect() -> None`, `close() -> None`, `execute(sql: str, limit: int = 1000) -> list[QueryResult]`, `cancel() -> None`. Statements run sequentially and autocommit by default; explicit BEGIN/COMMIT/ROLLBACK work on same session. Engine used from a single serial background thread; cancel may be called from UI thread. On error emit real error, never fake success. Preserve partial results if possible.

`schemas() -> list[str]` (SQLite: main; PostgreSQL namespaces; MySQL databases).
`objects(schema: str) -> list[dict]` dict: name, kind ('table'/'view'), schema.
`columns(schema: str, table: str) -> list[dict]` dict: name, type, nullable (bool), default, pk (bool).
`table_ddl(schema: str, table: str) -> str`.
`fetch_table(schema: str, table: str, offset: int = 0, limit: int = 200, order_by: str | None = None, descending: bool = False, filter_text: str = '') -> QueryResult`. Literal substring search all columns, bound parameters, identifier quoting; no raw WHERE UI.
`count_rows(schema: str, table: str, filter_text: str = '') -> int`.
`update_row(schema, table, original: dict, changes: dict) -> int`, `delete_row(schema, table, original: dict) -> int`, `insert_row(schema, table, values: dict) -> int`: primary key required for update/delete and stale original-value check to avoid lost updates; bound values; composite PK supported. Affected count must be one or fail/rollback. UI supplies all fetched columns as original.
`import_rows(schema: str, table: str, columns: list[str], rows: list[list[Any]]) -> int`: atomic batch, use bound parameters. `quote_identifier(name: str) -> str` and `qualified_name(schema, table) -> str` exported via session.

Extra capability allowed; communicate contract changes to root immediately. No UI imports in database module. All schema SQL needs portable quoting.
