# Engine / UI contract

`opennavicat.database` exports `ConnectionConfig`, `QueryResult`, `DatabaseSession`.

`ConnectionConfig` is the shared dataclass in `opennavicat/database.py`. Its core fields include `id`, `name`, `driver`, `database`, `host`, `port`, `username`, `password`, `ssl_mode` and `read_only`; the source also defines driver-specific authentication, TLS and SSH fields. Implemented core adapters are SQLite, PostgreSQL, MySQL, MariaDB, SQL Server, Redis and MongoDB. Oracle and Snowflake may be visible in the connection chooser but have no adapter. Driver identity must never silently fall back to another adapter.

`QueryResult` dataclass fields: `columns: list[str]`, `rows: list[list[Any]]`, `affected: int`, `elapsed_ms: float`, `truncated: bool = False`, `sql: str = ''`.

`DatabaseSession(config)` constructs the actual adapter and connects lazily. Common lifecycle: `connect() -> None`, `close() -> None`, `cancel() -> None`. `execute(text: str, limit: int = 1000)` uses that adapter's language: relational SQL, native Redis commands or the implemented MongoDB BSON/shell subset. It does not imply arbitrary JavaScript support. The engine is used from a serial background worker; cancel may be requested from the UI thread. Report actual errors and confirmed partial outcomes rather than claiming a failed or unacknowledged operation succeeded.

The relational APIs below apply only to supported relational adapters. Statements run sequentially; explicit transactions use the same session. Redis and MongoDB use separate typed workspaces and explicitly reject unsupported relational operations. Their standalone implementations do not provide relational transactions.

`schemas() -> list[str]` (SQLite: main; PostgreSQL namespaces; MySQL databases).
`objects(schema: str) -> list[dict]` dict: name, kind ('table'/'view'), schema.
`columns(schema: str, table: str) -> list[dict]` dict: name, type, nullable (bool), default, pk (bool).
`table_ddl(schema: str, table: str) -> str`.
`fetch_table(schema: str, table: str, offset: int = 0, limit: int = 200, order_by: str | None = None, descending: bool = False, filter_text: str = '') -> QueryResult`. Literal substring search all columns, bound parameters, identifier quoting; no raw WHERE UI.
`count_rows(schema: str, table: str, filter_text: str = '') -> int`.
`update_row(schema, table, original: dict, changes: dict) -> int`, `delete_row(schema, table, original: dict) -> int`, `insert_row(schema, table, values: dict) -> int`: primary key required for update/delete and stale original-value check to avoid lost updates; bound values; composite PK supported. Affected count must be one or fail/rollback. UI supplies all fetched columns as original.
`import_rows(schema: str, table: str, columns: list[str], rows: list[list[Any]]) -> int`: atomic batch, use bound parameters. `quote_identifier(name: str) -> str` and `qualified_name(schema, table) -> str` exported via session.

Extra capability allowed; communicate contract changes to root immediately. No UI imports in database module. All schema SQL needs portable quoting.

## MongoDB typed workspace

`MongoSession` in `opennavicat/mongodb_session.py` implements standalone MongoDB using PyMongo. `fetch_documents` returns a bounded `MongoDocumentPage`; each `MongoDocument` retains raw BSON and a session-bound sealed token for editable snapshots. `replace_document` and `delete_document` require that original snapshot and verify BSON identity, preimage and collection UUID before writing. Unsupported or non-roundtrippable BSON remains read-only; Canonical Extended JSON is not a complete JavaScript editor.

GridFS APIs are `list_files`, `upload_file`, `download_file` and `delete_file`. They retain bounded previews, progress/cancellation, collection identity and file snapshot checks. Downloads commit only after content validation and do not overwrite an existing destination by default. Standalone cleanup can be partially applied; its real outcome must remain visible.

`opennavicat/mongodb_query.py` prevalidates the whole script, executes supported native commands through the parent session, and uses official cursors with explicit delivery limits and cancellation. The separate native JavaScript prototype is not part of 0.7.0 or its installers. A future engine must preserve typed BSON and confirmed write outcomes without exposing Python objects or host filesystem/process/network APIs to scripts.

## Imported connection metadata

`ImportedProfile` describes source identity, configuration completeness, group completeness and any currently proven aliases. Source refresh must preserve local edits and secret storage, incomplete snapshots must preserve known configuration/groups, and missing sources must not delete local records. Ordinary startup may discover supported metadata but must not connect automatically. Windows native candidates remain blocked until their actual authentication and security settings are reviewed; smoke, demo and screenshot modes do not read user Navicat sources.
