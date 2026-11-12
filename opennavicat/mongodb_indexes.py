"""BSON index drafts and explicitly nontransactional, UUID-bound native DDL.

Collection UUID is a namespace guard, not an index-spec compare-and-swap.
Existing indexes are checked immediately before DDL; another client can still
change an index between that check and dropIndexes. A rebuild is two commands,
with no automatic rollback or retry. Failures expose acknowledged/unknown steps
and a bounded fresh observation instead of pretending the original index exists.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import uuid

from bson import BSON, Binary, Int64
from pymongo import timeout as mongo_timeout
from pymongo.command_cursor import CommandCursor
from pymongo.errors import OperationFailure

from .database import DatabaseError
from .mongodb_session import BSON_CODEC, RAW_CODEC, MongoConflictError, canonical_dumps
from .mongodb_index_script import UnsupportedScriptValue, helper_script, protected_command_script


_ORDERS = (1, -1, "2d", "2dsphere", "hashed", "text")
_OPTIONS = {
    "v", "background", "unique", "sparse", "hidden", "expireAfterSeconds",
    "partialFilterExpression", "storageEngine", "collation", "wildcardProjection",
    "weights", "default_language", "language_override", "textIndexVersion",
    "2dsphereIndexVersion", "bits", "min", "max", "bucketSize",
}


@dataclass
class IndexKey:
    field: str = ""
    order: int | str = 1


@dataclass(eq=False)
class IndexDefinition:
    name: str = ""
    collection: str = ""
    keys: list[IndexKey] = field(default_factory=list)
    options: dict = field(default_factory=dict)

    @property
    def table(self):
        return self.collection

    @table.setter
    def table(self, value):
        self.collection = value

    def __eq__(self, other):
        try:
            return isinstance(other, IndexDefinition) and _encoded(_definition(self)) == _encoded(_definition(other))
        except Exception:
            return False


@dataclass
class IndexSnapshot:
    schema: str
    collection: str
    name: str
    definition: IndexDefinition
    raw_spec: bytes = field(default=b"", repr=False)
    namespace_uuid: object = field(default=None, repr=False)
    fingerprint: str = ""
    token: bytes = field(default=b"", repr=False)
    exists: bool = True
    read_only: bool = False
    issues: list[str] = field(default_factory=list)
    ddl: str = ""
    kind: str = "mongo_index"

    @property
    def command_text(self):
        if not self.raw_spec:
            return ""
        decoded = BSON(self.raw_spec).decode(codec_options=BSON_CODEC)
        if _encoded(decoded) != self.raw_spec:
            return canonical_dumps({"originalRawBSON": Binary(self.raw_spec)})
        return _script(self.schema, [{"createIndexes": self.collection, "indexes": [decoded], "collectionUUID": self.namespace_uuid, "writeConcern": {"w": 1}}])

    @property
    def command_script(self):
        if not self.raw_spec:
            return ""
        decoded = BSON(self.raw_spec).decode(codec_options=BSON_CODEC)
        if _encoded(decoded) != self.raw_spec:
            return "// 原始 BSON 已保留；不能生成无损执行脚本。"
        try:
            return protected_command_script(self.schema, [{"createIndexes": self.collection, "indexes": [decoded], "collectionUUID": self.namespace_uuid, "writeConcern": {"w": 1}}])
        except UnsupportedScriptValue as error:
            return "// " + str(error)


@dataclass
class IndexPlan:
    snapshot: IndexSnapshot
    definition: IndexDefinition
    commands: list[dict]
    sql: str
    ddl: str
    changed: bool
    warnings: list[str] = field(default_factory=list)
    destructive: bool = False
    delete: bool = False
    token: bytes = field(default=b"", repr=False)

    @property
    def command_text(self):
        return _script(self.snapshot.schema, self.commands)

    @property
    def command_script(self):
        return protected_command_script(self.snapshot.schema, self.commands)


@dataclass
class IndexStep:
    operation: str
    collection: str
    name: str
    status: str = "not_started"


@dataclass
class IndexOutcome:
    steps: list[IndexStep]
    observation: dict = field(default_factory=dict)


class IndexApplyError(DatabaseError):
    def __init__(self, message, outcome):
        super().__init__(message)
        self.outcome = outcome


def _encoded(value):
    return BSON.encode(value, codec_options=BSON_CODEC)


def _definition(value):
    return {"name": value.name, "collection": value.collection,
            "keys": [{"field": key.field, "order": key.order} for key in value.keys], "options": value.options}


def _snapshot_payload(snapshot):
    return _encoded({"schema": snapshot.schema, "collection": snapshot.collection, "name": snapshot.name,
        "definition": _definition(snapshot.definition), "raw": Binary(snapshot.raw_spec),
        "uuid": snapshot.namespace_uuid, "exists": snapshot.exists, "read_only": snapshot.read_only,
        "issues": snapshot.issues, "ddl": snapshot.ddl, "kind": snapshot.kind})


def _sign(session, schema, collection, purpose, payload, namespace_uuid=None):
    return session._token(schema, collection, purpose + b"\0" + payload, False, namespace_uuid)


def _seal_snapshot(session, snapshot):
    payload = _snapshot_payload(snapshot)
    snapshot.fingerprint = hashlib.sha256(payload).hexdigest()
    snapshot.token = _sign(session, snapshot.schema, snapshot.collection, b"index-snapshot", payload, snapshot.namespace_uuid)
    return snapshot


def _verify_snapshot(session, snapshot):
    try:
        payload = _snapshot_payload(snapshot)
        expected = _sign(session, snapshot.schema, snapshot.collection, b"index-snapshot", payload, snapshot.namespace_uuid)
        if snapshot.kind != "mongo_index" or snapshot.fingerprint != hashlib.sha256(payload).hexdigest() or not hmac.compare_digest(expected, snapshot.token):
            raise ValueError()
    except Exception:
        raise DatabaseError("The MongoDB index snapshot is invalid or belongs to another session") from None


def _plan_payload(plan):
    return _encoded({"snapshot": Binary(_snapshot_payload(plan.snapshot)), "definition": _definition(plan.definition),
        "commands": plan.commands, "sql": plan.sql, "ddl": plan.ddl, "changed": plan.changed,
        "warnings": plan.warnings, "destructive": plan.destructive, "delete": plan.delete})


def _script(schema, commands):
    # This is the exact ordered BSON command representation, not a promise that
    # Navicat's JavaScript runtime implements the Extended JSON EJSON helper.
    return "\n\n".join("// database: " + json.dumps(schema, ensure_ascii=True) + "\n" + canonical_dumps(command) for command in commands)


def _collection(session, schema, collection):
    info = session.collection_info(schema, collection)
    if info.get("type") != "collection" or not session._collection_uuid(info):
        raise DatabaseError("This designer currently requires an existing regular MongoDB collection")
    return session._collection_uuid(info)


def _rows(session, schema, collection):
    with session._operation() as comment:
        database = session.connection[schema]
        owner = database[collection].with_options(codec_options=RAW_CODEC)
        with session.connection.start_session() as logical:
            reply = database.command({"listIndexes": collection, "cursor": {"batchSize": 100}, "comment": comment}, codec_options=RAW_CODEC, session=logical)
            with CommandCursor(owner, reply["cursor"], session.connection.address, session=logical, comment=comment) as cursor:
                result, total = [], 0
                for raw in cursor:
                    total += len(raw.raw)
                    if total > 16 * 1024 * 1024 or len(result) >= 1024:
                        raise DatabaseError("The index catalog exceeds the safe designer metadata bound")
                    row = _IndexRow(BSON(raw.raw).decode(codec_options=BSON_CODEC))
                    row.raw_bson = raw.raw
                    result.append(row)
                return result


class _IndexRow(dict):
    raw_bson: bytes


def _row_bytes(row):
    return row.raw_bson if isinstance(row, _IndexRow) else _encoded(dict(row))


def _logical_keys(spec):
    result = []
    internal_text = spec["key"].get("_fts") == "text"
    for name, order in spec["key"].items():
        if name == "_fts" and order == "text":
            result.extend(IndexKey(field, "text") for field in spec.get("weights", {}))
        elif name != "_ftsx" or not internal_text:
            result.append(IndexKey(name, order))
    return result


def _snapshot(session, schema, collection, name, spec, namespace_uuid):
    options = {k: deepcopy(v) for k, v in (spec or {}).items() if k not in {"key", "name", "ns"}}
    definition = IndexDefinition(name, collection, _logical_keys(spec) if spec else [], options)
    issues = []
    if spec:
        unknown = set(options) - _OPTIONS
        if unknown:
            issues.append("此索引含尚未支持的选项；完整原始定义已保留，只读显示。")
        if any(key.order not in _ORDERS for key in definition.keys):
            issues.append("此索引使用尚未支持的键类型，原始定义已保留。")
        if name in {"_id_", "*"} or collection.startswith("system."):
            issues.append("系统维护的索引不能通过此设计器修改。")
    raw_spec = _row_bytes(spec) if spec is not None else b""
    lossless = not spec or _encoded(dict(spec)) == raw_spec
    if not lossless:
        issues.append("此索引含不能无损解码的原始 BSON 类型；完整原始字节已保留，只读显示。")
    try:
        if not lossless:
            raise UnsupportedScriptValue("原始 BSON 不能转换为已验证的辅助脚本；请使用原始字节视图。")
        ddl = helper_script(schema, [{"createIndexes": collection, "indexes": [dict(spec)]}]) if spec else ""
    except UnsupportedScriptValue as error:
        issues.append(str(error))
        ddl = "// " + str(error)
    snapshot = IndexSnapshot(schema, collection, name, definition,
        raw_spec, namespace_uuid, exists=spec is not None,
        read_only=bool(issues) or session.config.read_only, issues=issues, ddl=ddl)
    return _seal_snapshot(session, snapshot)


def new_index(session, schema, collection=""):
    session._validate_namespace(schema, collection or None)
    namespace_uuid = _collection(session, schema, collection) if collection else None
    return _snapshot(session, schema, collection, "", None, namespace_uuid)


def load_index(session, schema, collection, name):
    namespace_uuid = _collection(session, schema, collection)
    row = next((r for r in _rows(session, schema, collection) if r.get("name") == name), None)
    if row is None:
        raise MongoConflictError("The MongoDB index no longer exists; refresh the object list")
    if _collection(session, schema, collection) != namespace_uuid:
        raise MongoConflictError("The MongoDB collection changed while loading the index")
    return _snapshot(session, schema, collection, name, row, namespace_uuid)


def _integer(value):
    return isinstance(value, (int, Int64)) and not isinstance(value, bool)


def validate_definition(definition):
    if not isinstance(definition.name, str) or not definition.name or definition.name in {"*", "_id_"} or "\0" in definition.name:
        raise DatabaseError("Enter a nonempty, non-system index name")
    if not isinstance(definition.collection, str) or not definition.collection or "\0" in definition.collection:
        raise DatabaseError("Select a collection")
    if not definition.keys:
        raise DatabaseError("Add at least one index field")
    names = []
    for key in definition.keys:
        if not isinstance(key.field, str) or not key.field or "\0" in key.field:
            raise DatabaseError("Every index field needs a nonempty name")
        if (type(key.order) is not int and not isinstance(key.order, str)) or key.order not in _ORDERS:
            raise DatabaseError("Unsupported MongoDB index key type")
        if key.field in names:
            raise DatabaseError("An index field cannot appear twice")
        names.append(key.field)
    options = definition.options
    if not isinstance(options, dict) or set(options) - _OPTIONS:
        raise DatabaseError("Unsupported MongoDB index options cannot be edited lossily")
    for option in {"unique", "sparse", "background", "hidden"} & options.keys():
        if type(options[option]) is not bool:
            raise DatabaseError(option + " must be a boolean")
    for option in {"v", "textIndexVersion", "2dsphereIndexVersion", "bits", "expireAfterSeconds"} & options.keys():
        if not _integer(options[option]):
            raise DatabaseError(option + " must be an integer")
    if "expireAfterSeconds" in options:
        if not 0 <= options["expireAfterSeconds"] <= 2147483647 or len(definition.keys) != 1 or definition.keys[0].order not in (1, -1):
            raise DatabaseError("TTL requires one ascending/descending field and 0–2147483647 seconds")
    for option in {"min", "max", "bucketSize"} & options.keys():
        if isinstance(options[option], bool) or not isinstance(options[option], (int, float)) or not math.isfinite(options[option]):
            raise DatabaseError(option + " must be a finite number")
    if "bucketSize" in options and options["bucketSize"] <= 0:
        raise DatabaseError("bucketSize must be positive")
    if "min" in options and "max" in options and options["min"] >= options["max"]:
        raise DatabaseError("The 2D minimum must be less than its maximum")
    if "bits" in options and not 1 <= options["bits"] <= 32:
        raise DatabaseError("2D bit precision must be between 1 and 32")
    for option in {"partialFilterExpression", "storageEngine", "collation", "wildcardProjection", "weights"} & options.keys():
        if not isinstance(options[option], dict):
            raise DatabaseError(option + " must be a document")
    for option in {"default_language", "language_override"} & options.keys():
        if not isinstance(options[option], str) or not options[option] or "\0" in options[option]:
            raise DatabaseError(option + " must be a nonempty string")
    if any(not isinstance(name, str) or not name or not _integer(weight) or not 1 <= weight <= 99999 for name, weight in options.get("weights", {}).items()):
        raise DatabaseError("Text weights must be integers between 1 and 99999")
    if options.get("sparse") and "partialFilterExpression" in options:
        raise DatabaseError("Sparse and partial-filter options cannot be combined")
    if options.get("unique") and any(key.order in {"hashed", "text"} for key in definition.keys):
        raise DatabaseError("Hashed/text indexes cannot be unique")
    text_fields = {key.field for key in definition.keys if key.order == "text"}
    if set(options.get("weights", {})) - text_fields:
        raise DatabaseError("Every weighted field must be a Text index key")
    if not text_fields and set(options) & {"weights", "default_language", "language_override", "textIndexVersion"}:
        raise DatabaseError("Text options require at least one Text key")
    orders = {key.order for key in definition.keys}
    if set(options) & {"bits", "min", "max"} and "2d" not in orders:
        raise DatabaseError("2D options require a 2D key")
    if "2dsphereIndexVersion" in options and "2dsphere" not in orders:
        raise DatabaseError("2DSphere options require a 2DSphere key")
    try:
        if len(_encoded(_definition(definition))) >= 16 * 1024 * 1024:
            raise ValueError()
    except Exception:
        raise DatabaseError("The index definition cannot be encoded losslessly as BSON") from None
    helper_script("preview", [{"createIndexes": definition.collection,
        "indexes": [{"name": definition.name, "key": {key.field: key.order for key in definition.keys}, **definition.options}]}])


def _spec(definition, snapshot):
    key = {item.field: item.order for item in definition.keys}
    if snapshot.exists and definition.keys == snapshot.definition.keys:
        # Internal text _fts/_ftsx ordering plus weights replays losslessly on
        # MongoDB 8.0.34. The order of original individual text fields was not
        # retained by the server and is not invented from the UI display order.
        key = BSON(snapshot.raw_spec).decode(codec_options=BSON_CODEC)["key"]
    options = deepcopy(definition.options)
    text_fields = [item.field for item in definition.keys if item.order == "text"]
    if text_fields:
        options["weights"] = {name: options.get("weights", {}).get(name, 1) for name in text_fields}
    return {"key": key, "name": definition.name, **options}


def _current(session, snapshot):
    if snapshot.collection and _collection(session, snapshot.schema, snapshot.collection) != snapshot.namespace_uuid:
        raise MongoConflictError("The MongoDB collection was replaced; reload before editing its indexes")
    if snapshot.exists:
        row = next((r for r in _rows(session, snapshot.schema, snapshot.collection) if r.get("name") == snapshot.name), None)
        if row is None or _row_bytes(row) != snapshot.raw_spec:
            raise MongoConflictError("The MongoDB index changed; reload it before saving")


def _server_version(session):
    with mongo_timeout(3), session._operation() as comment:
        return session.connection.admin.command({"buildInfo": 1, "comment": comment})["version"]


def _require_command_guard(session, schema, collection, operation):
    # Empty selectors select no index and do not create a missing collection.
    # This has been exercised independently on native 7.0.45/8.0.34/8.2.12.
    # The two commands need separate qualification: legacy createIndexes checks
    # its empty array before UUID, while dropIndexes and 8.2's createIndexes
    # perform the UUID check first. Never infer one command from the other.
    wrong_uuid = Binary(uuid.uuid4().bytes, 4)
    command = ({"createIndexes": collection, "indexes": []} if operation == "create"
               else {"dropIndexes": collection, "index": []})
    command["collectionUUID"] = wrong_uuid
    with mongo_timeout(3), session._operation() as comment:
        try:
            session.connection[schema].command({**command, "comment": comment})
        except OperationFailure as error:
            if error.code == 361 and (error.details or {}).get("codeName") == "CollectionUUIDMismatch":
                return
            if operation == "create" and error.code == 2 and _server_version(session) in {"7.0.45", "8.0.34"}:
                # These exact legacy builds also have separate nonempty-command
                # namespace-replacement evidence. BadValue alone is NOT proof.
                return
            if error.code == 13:
                raise DatabaseError("The server denied the required index permission; no index changes were applied") from error
            raise DatabaseError("Could not verify " + operation + " index namespace protection; no index changes were applied") from error
    raise DatabaseError("This server ignored the " + operation + " index namespace probe; index changes are blocked")


def _seal_plan(session, plan):
    plan.token = _sign(session, plan.snapshot.schema, plan.snapshot.collection, b"index-plan", _plan_payload(plan), plan.snapshot.namespace_uuid)
    return plan


def plan_index(session, snapshot, definition):
    snapshot, definition = deepcopy(snapshot), deepcopy(definition)
    _verify_snapshot(session, snapshot)
    changed = not snapshot.exists or definition != snapshot.definition
    if not changed:
        return _seal_plan(session, IndexPlan(snapshot, definition, [], "", snapshot.ddl, False))
    if session.config.read_only or snapshot.read_only:
        raise DatabaseError("This MongoDB index is read-only")
    validate_definition(definition)
    _current(session, snapshot)
    target_uuid = _collection(session, snapshot.schema, definition.collection)
    _require_command_guard(session, snapshot.schema, definition.collection, "create")
    if snapshot.exists:
        _require_command_guard(session, snapshot.schema, snapshot.collection, "drop")
    target = next((r for r in _rows(session, snapshot.schema, definition.collection) if r.get("name") == definition.name), None)
    if target and not (snapshot.exists and snapshot.collection == definition.collection and snapshot.name == definition.name):
        raise MongoConflictError("An index with this name already exists on the selected collection")
    commands = []
    warnings = []
    if snapshot.exists:
        commands.append({"dropIndexes": snapshot.collection, "index": [snapshot.name], "collectionUUID": snapshot.namespace_uuid})
        warnings.append("保存将先删除原索引，再创建新索引；两步不是事务。失败后不会自动恢复或重试。其它客户端仍可在目录检查后修改索引。")
    spec = _spec(definition, snapshot)
    commands.append({"createIndexes": definition.collection, "indexes": [spec], "collectionUUID": target_uuid})
    for command in commands:
        command["writeConcern"] = {"w": 1}
    ddl = helper_script(snapshot.schema, [commands[-1]])
    return _seal_plan(session, IndexPlan(snapshot, definition, commands, helper_script(snapshot.schema, commands), ddl, True, warnings, snapshot.exists))


def plan_drop(session, snapshot):
    snapshot = deepcopy(snapshot)
    _verify_snapshot(session, snapshot)
    if not snapshot.exists or snapshot.read_only or session.config.read_only:
        raise DatabaseError("This MongoDB index cannot be deleted")
    _current(session, snapshot)
    _require_command_guard(session, snapshot.schema, snapshot.collection, "drop")
    commands = [{"dropIndexes": snapshot.collection, "index": [snapshot.name], "collectionUUID": snapshot.namespace_uuid, "writeConcern": {"w": 1}}]
    return _seal_plan(session, IndexPlan(snapshot, deepcopy(snapshot.definition), commands,
        helper_script(snapshot.schema, commands), "", True, ["删除是永久性的且无法撤消。目录检查不是索引定义的原子比较交换。"], True, True))


@dataclass
class InlineIndexDraft:
    """One staged row from the collection designer's embedded index table."""

    definition: IndexDefinition
    original_name: str = ""
    deleted: bool = False


@dataclass
class InlineIndexPlanBundle:
    """Ordered, independently UUID-guarded index plans; never transactional."""

    schema: str
    collection: str
    drafts: list[InlineIndexDraft]
    plans: list[IndexPlan]
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self):
        return bool(self.plans)

    @property
    def command_text(self):
        return '\n\n'.join(plan.command_text for plan in self.plans) if self.plans else '无更改'

    @property
    def command_script(self):
        return '\n\n'.join(plan.command_script for plan in self.plans) if self.plans else '无更改'


class InlineIndexApplyError(DatabaseError):
    """A sequential embedded-index save stopped at its first failed plan."""

    def __init__(self, message, completed, failed_original, failed_definition, cause):
        super().__init__(message)
        self.completed = list(completed)
        self.failed_original = failed_original
        self.failed_definition = deepcopy(failed_definition)
        self.cause = cause


def plan_inline_changes(session, schema, collection, drafts):
    """Plan embedded-table drafts without making them transactional."""
    if not isinstance(schema, str) or not schema or not isinstance(collection, str) or not collection:
        raise DatabaseError("An embedded index save requires one collection")
    staged = deepcopy(list(drafts))
    if not staged:
        raise DatabaseError("There are no staged index changes")
    if len(staged) > 256:
        raise DatabaseError("An embedded index save is limited to 256 rows")
    if any(not isinstance(draft, InlineIndexDraft) for draft in staged):
        raise DatabaseError("Invalid embedded index draft")
    for draft in staged:
        if draft.deleted:
            if draft.original_name == "_id_":
                raise DatabaseError("The default _id index cannot be edited here")
            continue
        definition = draft.definition
        if definition.collection != collection:
            raise DatabaseError("Every embedded index must belong to the selected collection")
        validate_definition(definition)
        if draft.original_name == "_id_":
            raise DatabaseError("The default _id index cannot be edited here")
    target_names = [draft.definition.name for draft in staged if not draft.deleted]
    if len(target_names) != len(set(target_names)):
        raise DatabaseError("Embedded index names must be unique")
    plans = []
    for draft in staged:
        try:
            if draft.deleted:
                snapshot = load_index(session, schema, collection, draft.original_name)
                plan = plan_drop(session, snapshot)
            else:
                snapshot = (load_index(session, schema, collection, draft.original_name)
                            if draft.original_name else new_index(session, schema, collection))
                plan = plan_index(session, snapshot, draft.definition)
        except MongoConflictError as error:
            label = draft.original_name or draft.definition.name
            raise DatabaseError("Embedded index '" + label + "' could not be planned: " + str(error)) from None
        if plan.changed:
            plans.append(plan)
    if not plans:
        return InlineIndexPlanBundle(schema, collection, staged, [], [])
    warnings = []
    if any(plan.delete for plan in plans):
        warnings.append("删除的索引无法恢复；所有变更会按表格顺序逐个执行，整批不是事务。")
    if any(not plan.delete and plan.snapshot.exists and plan.snapshot.name != plan.definition.name for plan in plans):
        warnings.append("重命名索引会先删除原索引再创建新索引；中途失败可能只留下部分步骤。")
    if len(plans) > 1:
        warnings.append(f"本次包含 {len(plans)} 个索引操作；后续操作失败时，前面已确认的步骤不会自动回滚或重试。")
    return InlineIndexPlanBundle(schema, collection, staged, plans, warnings)


def apply_inline_plans(session, bundle, *, cancelled=None):
    """Apply already-confirmed per-index plans in display order."""
    bundle = deepcopy(bundle)
    if not isinstance(bundle, InlineIndexPlanBundle):
        raise DatabaseError("Invalid embedded index plan")
    completed = []
    for plan in bundle.plans:
        if cancelled is not None and cancelled():
            raise DatabaseError("Embedded index save was cancelled; confirmed earlier steps are not rolled back")
        try:
            updated = apply_plan(session, plan, cancelled=cancelled)
        except Exception as error:
            message = ("Embedded index save stopped after " + str(len(completed)) +
                       " completed operation(s); no automatic rollback was attempted")
            raise InlineIndexApplyError(message, completed, plan.snapshot.name,
                plan.definition, error) from error
        completed.append(updated)
    return completed

def _observe(session, plan):
    result = {}
    for collection in {plan.snapshot.collection, plan.definition.collection} - {""}:
        try:
            with mongo_timeout(3):
                namespace_uuid = _collection(session, plan.snapshot.schema, collection)
                rows = _rows(session, plan.snapshot.schema, collection)
            names = {plan.snapshot.name, plan.definition.name}
            selected = [row for row in rows if row.get("name") in names]
            result[collection] = {"available": True, "namespace_uuid": namespace_uuid,
                                  "indexes": [dict(row) for row in selected],
                                  "raw_indexes": [Binary(_row_bytes(row)) for row in selected],
                                  "lossless": all(_encoded(dict(row)) == _row_bytes(row) for row in selected)}
        except Exception:
            result[collection] = {"available": False}
    return result


def _readback_matches(expected, actual):
    expected_key = expected["key"]
    text_fields = [name for name, order in expected_key.items() if order == "text" and name != "_fts"]
    if text_fields:
        internal = {}
        for name, order in expected_key.items():
            if order == "text":
                if "_fts" not in internal:
                    internal.update({"_fts": "text", "_ftsx": 1})
            else:
                internal[name] = order
        if _encoded({"key": actual["key"]}) != _encoded({"key": internal}):
            return False
        weights = {name: expected.get("weights", {}).get(name, 1) for name in text_fields}
        if dict(actual.get("weights", {})) != weights:
            return False
    elif _encoded({"key": actual["key"]}) != _encoded({"key": expected_key}):
        return False
    for name, value in expected.items():
        if name == "key":
            continue
        if name in {"unique", "sparse", "hidden", "background"}:
            if actual.get(name, False) != value:
                return False
        elif name == "collation":
            if any(actual.get(name, {}).get(key) != item for key, item in value.items()):
                return False
        elif actual.get(name) != value:
            return False
    return True


def apply_plan(session, plan, *, cancelled=None):
    plan = deepcopy(plan)
    _verify_snapshot(session, plan.snapshot)
    expected = _sign(session, plan.snapshot.schema, plan.snapshot.collection, b"index-plan", _plan_payload(plan), plan.snapshot.namespace_uuid)
    if not hmac.compare_digest(expected, plan.token):
        raise DatabaseError("The confirmed MongoDB index plan was changed or belongs to another session")
    if not plan.changed:
        _current(session, plan.snapshot)
        return plan.snapshot
    if session.config.read_only or plan.snapshot.read_only:
        raise DatabaseError("This MongoDB connection or index is read-only")
    steps = [IndexStep("drop" if "dropIndexes" in c else "create", c.get("dropIndexes", c.get("createIndexes")),
              plan.snapshot.name if "dropIndexes" in c else plan.definition.name) for c in plan.commands]
    outcome = IndexOutcome(steps)
    if cancelled is not None and cancelled():
        raise IndexApplyError("Index save was cancelled before any database operation", outcome)
    with session._operation() as comment:
        _current(session, plan.snapshot)
        for command in plan.commands:
            _require_command_guard(session, plan.snapshot.schema,
                command.get("dropIndexes", command.get("createIndexes")), "drop" if "dropIndexes" in command else "create")
        if not plan.delete:
            if _collection(session, plan.snapshot.schema, plan.definition.collection) != plan.commands[-1]["collectionUUID"]:
                raise MongoConflictError("The target collection was replaced after preview; nothing was changed")
            target = next((r for r in _rows(session, plan.snapshot.schema, plan.definition.collection) if r.get("name") == plan.definition.name), None)
            if target and not (plan.snapshot.exists and plan.snapshot.collection == plan.definition.collection and plan.snapshot.name == plan.definition.name):
                raise MongoConflictError("An index with this name appeared after the preview; nothing was changed")
        for index, command in enumerate(plan.commands):
            step = steps[index]
            if (cancelled is not None and cancelled()) or session._cancel_requested.is_set():
                outcome.observation = _observe(session, plan)
                raise IndexApplyError("Index save stopped before the next command; completed steps remain applied", outcome)
            step.status = "unknown"
            try:
                with mongo_timeout(120):
                    reply = session.connection[plan.snapshot.schema].command({**command, "comment": comment, "writeConcern": {"w": 1}})
                if reply.get("writeConcernError") or reply.get("writeErrors"):
                    raise DatabaseError("The server did not acknowledge the index command completely")
                step.status = "acknowledged"
            except Exception as error:
                if isinstance(error, OperationFailure) and not (error.details or {}).get("writeConcernError"):
                    step.status = "server_error"
                outcome.observation = _observe(session, plan)
                raise IndexApplyError(str(error) + "\n已完成的步骤不会自动恢复；请核对实际索引状态。", outcome) from error
        try:
            if plan.delete:
                namespace_uuid = _collection(session, plan.snapshot.schema, plan.snapshot.collection)
                if namespace_uuid != plan.snapshot.namespace_uuid:
                    raise MongoConflictError("The collection changed after index deletion")
                if any(r.get("name") == plan.snapshot.name for r in _rows(session, plan.snapshot.schema, plan.snapshot.collection)):
                    raise MongoConflictError("The deleted index name is present again; refresh its actual definition")
                return _snapshot(session, plan.snapshot.schema, plan.snapshot.collection, plan.snapshot.name, None, namespace_uuid)
            result = load_index(session, plan.snapshot.schema, plan.definition.collection, plan.definition.name)
            if result.namespace_uuid != plan.commands[-1]["collectionUUID"]:
                raise MongoConflictError("The collection changed after index creation")
            expected_spec = plan.commands[-1]["indexes"][0]
            actual_spec = BSON(result.raw_spec).decode(codec_options=BSON_CODEC)
            if not _readback_matches(expected_spec, actual_spec):
                raise MongoConflictError("The index readback differs from the confirmed definition")
            return result
        except Exception as error:
            outcome.observation = _observe(session, plan)
            raise IndexApplyError("The commands were acknowledged, but final index state could not be verified: " + str(error), outcome) from error
