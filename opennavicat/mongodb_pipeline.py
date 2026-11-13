"""Bounded MongoDB aggregation-pipeline drafts, previews and .nagg persistence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any
import uuid

from bson import BSON, json_util
from pymongo import timeout

from .database import DatabaseError
from .mongodb_index_script import bson_literal
from .mongodb_session import (
    BSON_CODEC, JSON_OPTIONS, RAW_CODEC, _MAX_PAGE_BYTES,
    canonical_dumps, canonical_loads,
)


STAGE_NAMES = (
    "$addFields", "$bucket", "$bucketAuto", "$changeStream",
    "$changeStreamSplitLargeEvent", "$collStats", "$count", "$currentOp",
    "$densify", "$documents", "$facet", "$fill", "$geoNear",
    "$graphLookup", "$group", "$indexStats", "$limit", "$listLocalSessions",
    "$listSampledQueries", "$listSearchIndexes", "$listSessions", "$lookup",
    "$match", "$merge", "$out", "$planCacheStats", "$project", "$redact",
    "$replaceRoot", "$replaceWith", "$sample", "$search", "$searchMeta",
    "$set", "$setWindowFields", "$sharedDataDistribution", "$skip",
    "$sort", "$sortByCount", "$unionWith", "$unset", "$unwind",
)
WRITE_STAGES = frozenset({"$merge", "$out"})
STREAM_STAGES = frozenset({"$changeStream", "$changeStreamSplitLargeEvent"})
MAX_STAGES = 200
MAX_PREVIEW_ROWS = 200
MAX_EXECUTION_ROWS = 1000
MAX_EXPLAIN_NODES = 512
MAX_EXPLAIN_DEPTH = 32
REFERENCE_NAGG_VERSION = "0.0"
REFERENCE_SAMPLE_MODES = {"all": 0, "first": 1, "random": 2}
REFERENCE_SAMPLE_NAMES = {value: key for key, value in REFERENCE_SAMPLE_MODES.items()}
REFERENCE_OPTION_DEFAULTS = {
    "allowDiskUse": 0,
    "cursorBatchSize": -1,
    "maxTimeMS": -1,
    "bypassDocumentValidation": 0,
    "readConcernLevel": 0,
    "locale": "",
    "caseLevel": 0,
    "caseFirst": 0,
    "strength": 0,
    "numericOrdering": 0,
    "alternate": 0,
    "maxVariable": 0,
    "backwards": 0,
    "normalization": 0,
    "hint": "",
    "comment": "",
    "enableWriteConcern": False,
    "w": "",
    "j": 0,
    "wtimeout": 0,
    "let": "",
}
REFERENCE_TRISTATE = {-1: False, 0: None, 1: True}
REFERENCE_READ_CONCERNS = {0: None, 1: "local", 2: "available", 3: "majority", 4: "linearizable"}
REFERENCE_CASE_FIRST = {0: None, 1: "upper", 2: "lower", 3: "off"}
REFERENCE_ALTERNATE = {0: None, 1: "non-ignorable", 2: "shifted"}
REFERENCE_MAX_VARIABLE = {0: None, 1: "punct", 2: "space"}


@dataclass(eq=False)
class PipelineStage:
    operator: str = "$match"
    body: object = field(default_factory=dict, repr=False)
    enabled: bool = True
    stage_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    reference_extra: dict[str, Any] = field(default_factory=dict, repr=False)
    reference_mql: str | None = field(default=None, repr=False)

    def __eq__(self, other):
        return (isinstance(other, PipelineStage) and self.operator == other.operator
                and self.enabled == other.enabled
                and _encoded(self.body) == _encoded(other.body)
                and _encoded(self.reference_extra) == _encoded(other.reference_extra))


@dataclass(eq=False)
class PipelineDefinition:
    name: str = ""
    source: str = ""
    stages: list[PipelineStage] = field(default_factory=list)
    sample_mode: str = "random"
    sample_size: int = 1000
    selected_stage: int | None = 0
    reference_version: str = REFERENCE_NAGG_VERSION
    reference_options: dict[str, Any] = field(default_factory=dict, repr=False)
    reference_extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __eq__(self, other):
        return (isinstance(other, PipelineDefinition) and self.name == other.name
                and self.source == other.source and self.sample_mode == other.sample_mode
                and self.sample_size == other.sample_size and self.stages == other.stages
                and self.reference_version == other.reference_version
                and _encoded(self.reference_options) == _encoded(other.reference_options)
                and _encoded(self.reference_extra) == _encoded(other.reference_extra))


@dataclass
class PipelinePreview:
    source_rows: list[str]
    stages: list[dict]
    final_rows: list[str]
    truncated: bool = False


@dataclass(eq=False)
class ExplainPlanNode:
    """One bounded node from a MongoDB execution or query plan tree."""

    stage: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    children: list["ExplainPlanNode"] = field(default_factory=list)

    @property
    def elapsed_ms(self):
        for key in ("executionTimeMillisEstimate", "executionTimeMillis", "elapsedMillis"):
            value = self.raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return None

    @property
    def total_elapsed_ms(self):
        for key in ("executionTimeMillis", "executionTimeMillisEstimate", "elapsedMillis"):
            value = self.raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return None

    @property
    def stage_elapsed_ms(self):
        value = self.raw.get("stageExecutionTimeMillis")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return self.elapsed_ms

    @property
    def returned(self):
        value = self.raw.get("nReturned")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def docs_examined(self):
        for key in ("docsExamined", "totalDocsExamined"):
            value = self.raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    @property
    def keys_examined(self):
        for key in ("keysExamined", "totalKeysExamined"):
            value = self.raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    @property
    def index_name(self):
        value = self.raw.get("indexName")
        return value if isinstance(value, str) and value else None


@dataclass(eq=False)
class PipelineExplain:
    """Canonical server reply plus a bounded native visual-plan projection."""

    raw: dict[str, Any]
    raw_text: str
    roots: list[ExplainPlanNode] = field(default_factory=list)

    @property
    def root(self):
        return self.roots[0] if self.roots else None

    @property
    def command(self):
        value = self.raw.get("command")
        return value if isinstance(value, dict) else {}

    @property
    def server_info(self):
        value = self.raw.get("serverInfo")
        return value if isinstance(value, dict) else {}

    @property
    def execution_stats(self):
        value = self.raw.get("executionStats")
        return value if isinstance(value, dict) else {}

    def nodes(self):
        result, stack = [], list(reversed(self.roots))
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(reversed(node.children))
        return result

    def stats_rows(self):
        grouped = {}
        for node in self.nodes():
            row = grouped.setdefault(node.stage, {"stage": node.stage, "count": 0, "elapsed_ms": 0.0})
            row["count"] += 1
            row["elapsed_ms"] += node.stage_elapsed_ms or 0.0
        root_elapsed = self.root.total_elapsed_ms if self.root is not None else None
        denominator = root_elapsed if root_elapsed and root_elapsed > 0 else sum(
            row["elapsed_ms"] for row in grouped.values())
        return tuple({
            **row,
            "elapsed_percent": (100.0 * row["elapsed_ms"] / denominator) if denominator > 0 else 0.0,
        } for row in grouped.values())


_PLAN_CHILD_KEYS = (
    "inputStage", "inputStages", "queryPlan", "innerStage", "outerStage",
    "thenStage", "executionStages", "winningPlan", "rejectedPlans", "plans",
    "shards", "stages", "children",
)


def _plan_documents(value):
    if isinstance(value, dict):
        stage = value.get("stage")
        if isinstance(stage, str) and stage:
            yield value
            return
        for child in value.values():
            yield from _plan_documents(child)
    elif isinstance(value, list):
        for child in value:
            yield from _plan_documents(child)


def _parse_plan_node(raw, *, depth=0, counter=None):
    if counter is None:
        counter = [0]
    if not isinstance(raw, dict):
        return None
    stage = raw.get("stage")
    if not isinstance(stage, str) or not stage:
        return None
    if depth > MAX_EXPLAIN_DEPTH:
        raise DatabaseError("MongoDB Explain plan exceeds the bounded tree depth")
    counter[0] += 1
    if counter[0] > MAX_EXPLAIN_NODES:
        raise DatabaseError("MongoDB Explain plan exceeds the bounded node count")
    node = ExplainPlanNode(stage=stage, raw=deepcopy(raw))
    seen = set()
    for key in _PLAN_CHILD_KEYS:
        if key not in raw:
            continue
        for child_raw in _plan_documents(raw[key]):
            identity = id(child_raw)
            if identity in seen:
                continue
            seen.add(identity)
            child = _parse_plan_node(child_raw, depth=depth + 1, counter=counter)
            if child is not None:
                node.children.append(child)
    return node


def parse_explain_reply(reply):
    """Build a visual plan while retaining the complete bounded server reply."""
    if not isinstance(reply, dict):
        raise DatabaseError("MongoDB Explain did not return a document")
    try:
        encoded = BSON.encode({"reply": reply}, codec_options=BSON_CODEC)
        if len(encoded) > _MAX_PAGE_BYTES:
            raise ValueError()
        raw_text = canonical_dumps(reply)
        raw = canonical_loads(raw_text)
    except DatabaseError:
        raise
    except Exception:
        raise DatabaseError("MongoDB Explain reply cannot be preserved as exact bounded Canonical EJSON") from None

    candidates = []
    execution = raw.get("executionStats")
    if isinstance(execution, dict):
        candidates.extend(_plan_documents(execution.get("executionStages")))
        if not candidates:
            candidates.extend(_plan_documents(execution.get("shards")))
    query = raw.get("queryPlanner")
    if not candidates and isinstance(query, dict):
        candidates.extend(_plan_documents(query.get("winningPlan")))
    if not candidates:
        candidates.extend(_plan_documents(raw.get("stages")))
    counter = [0]
    roots = []
    for candidate in candidates:
        node = _parse_plan_node(candidate, counter=counter)
        if node is not None:
            roots.append(node)
    return PipelineExplain(raw=raw, raw_text=raw_text, roots=roots)


def _encoded(value):
    return BSON.encode({"value": value}, codec_options=BSON_CODEC)


def _option_choice(options, key, choices):
    value = options.get(key, REFERENCE_OPTION_DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int) or value not in choices:
        raise DatabaseError(f"Reference .nagg option {key} has an unsupported value")
    return value


def _option_integer(options, key, *, minimum, blank=-1):
    value = options.get(key, REFERENCE_OPTION_DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int):
        raise DatabaseError(f"Reference .nagg option {key} must be an integer")
    if value != blank and value < minimum:
        raise DatabaseError(f"Reference .nagg option {key} is below its supported minimum")
    return value


def _validate_pipeline_options(options):
    if not isinstance(options, dict):
        raise DatabaseError("Reference .nagg options must be a document")
    for key in ("allowDiskUse", "bypassDocumentValidation", "caseLevel", "numericOrdering", "backwards", "normalization"):
        _option_choice(options, key, REFERENCE_TRISTATE)
    _option_choice(options, "readConcernLevel", REFERENCE_READ_CONCERNS)
    _option_choice(options, "caseFirst", REFERENCE_CASE_FIRST)
    _option_choice(options, "alternate", REFERENCE_ALTERNATE)
    _option_choice(options, "maxVariable", REFERENCE_MAX_VARIABLE)
    strength = options.get("strength", 0)
    if isinstance(strength, bool) or not isinstance(strength, int) or not 0 <= strength <= 5:
        raise DatabaseError("Reference .nagg option strength must be between 0 and 5")
    _option_integer(options, "cursorBatchSize", minimum=0)
    _option_integer(options, "maxTimeMS", minimum=0)
    _option_integer(options, "wtimeout", minimum=0, blank=0)
    _option_choice(options, "j", REFERENCE_TRISTATE)
    for key in ("locale", "hint", "comment", "let"):
        if not isinstance(options.get(key, REFERENCE_OPTION_DEFAULTS[key]), str):
            raise DatabaseError(f"Reference .nagg option {key} must be text")
    if not isinstance(options.get("enableWriteConcern", False), bool):
        raise DatabaseError("Reference .nagg option enableWriteConcern must be boolean")
    if not isinstance(options.get("w", ""), (str, int)) or isinstance(options.get("w", ""), bool):
        raise DatabaseError("Reference .nagg option w must be text or integer")


def validate_pipeline(definition):
    if not isinstance(definition, PipelineDefinition):
        raise DatabaseError("An aggregation pipeline definition is required")
    if not isinstance(definition.name, str):
        raise DatabaseError("Pipeline name must be text")
    if not isinstance(definition.source, str) or not definition.source:
        raise DatabaseError("Select a source collection")
    if not isinstance(definition.stages, list) or len(definition.stages) > MAX_STAGES:
        raise DatabaseError("Pipeline stages exceed the bounded designer limit")
    stage_ids = [stage.stage_id for stage in definition.stages if isinstance(stage, PipelineStage)]
    if len(stage_ids) != len(set(stage_ids)) or any(not isinstance(stage_id, str) or not stage_id for stage_id in stage_ids):
        raise DatabaseError("Pipeline stage identifiers must be unique and nonempty")
    if (definition.selected_stage is not None
            and (isinstance(definition.selected_stage, bool) or not isinstance(definition.selected_stage, int)
                 or not 0 <= definition.selected_stage < len(definition.stages))):
        raise DatabaseError("Selected pipeline stage is invalid")
    if definition.sample_mode not in REFERENCE_SAMPLE_MODES:
        raise DatabaseError("Preview sample mode must be all, first or random")
    if (isinstance(definition.sample_size, bool) or not isinstance(definition.sample_size, int)
            or not 1 <= definition.sample_size <= 100000):
        raise DatabaseError("Preview sample size must be between 1 and 100000")
    if not isinstance(definition.reference_version, str) or not definition.reference_version:
        raise DatabaseError("Reference .nagg version must be nonempty text")
    for label, value in (("options", definition.reference_options), ("top-level fields", definition.reference_extra)):
        if not isinstance(value, dict):
            raise DatabaseError(f"Reference .nagg {label} must be a document")
        try:
            encoded = _encoded(value)
        except Exception:
            raise DatabaseError(f"Reference .nagg {label} cannot be encoded as exact BSON") from None
        if len(encoded) > _MAX_PAGE_BYTES:
            raise DatabaseError(f"Reference .nagg {label} exceed the bounded metadata size")
    _validate_pipeline_options(definition.reference_options)
    for stage in definition.stages:
        if not isinstance(stage, PipelineStage) or stage.operator not in STAGE_NAMES:
            raise DatabaseError("Unsupported aggregation stage")
        if not isinstance(stage.reference_extra, dict):
            raise DatabaseError("Reference .nagg stage extensions must be a document")
        try:
            raw = BSON.encode({"body": stage.body}, codec_options=BSON_CODEC)
            decoded = BSON(raw).decode(codec_options=BSON_CODEC)
            if (len(raw) > _MAX_PAGE_BYTES
                    or BSON.encode(decoded, codec_options=BSON_CODEC) != raw):
                raise ValueError()
            extra = _encoded(stage.reference_extra)
            if len(extra) > 64 * 1024:
                raise ValueError()
        except Exception:
            raise DatabaseError("Stage body cannot be encoded as exact bounded BSON") from None
    return definition


def enabled_stages(definition):
    validate_pipeline(definition)
    return [{"stage": deepcopy(stage.operator), "body": deepcopy(stage.body), "id": stage.stage_id}
            for stage in definition.stages if stage.enabled]


def pipeline_documents(definition):
    validate_pipeline(definition)
    return [{stage.operator: deepcopy(stage.body)} for stage in definition.stages if stage.enabled]


def pipeline_aggregate_options(definition):
    """Map the observed Version 0.0 Option document to aggregate arguments."""
    validate_pipeline(definition)
    options = deepcopy(REFERENCE_OPTION_DEFAULTS)
    options.update(deepcopy(definition.reference_options))
    result = {}
    allow_disk = REFERENCE_TRISTATE[options["allowDiskUse"]]
    if allow_disk is not None:
        result["allowDiskUse"] = allow_disk
    bypass = REFERENCE_TRISTATE[options["bypassDocumentValidation"]]
    if bypass is not None:
        result["bypassDocumentValidation"] = bypass
    batch_size = options["cursorBatchSize"]
    if batch_size >= 0:
        result["batchSize"] = batch_size
    if options["maxTimeMS"] >= 0:
        result["maxTimeMS"] = options["maxTimeMS"]
    read_concern = REFERENCE_READ_CONCERNS[options["readConcernLevel"]]
    if read_concern is not None:
        result["readConcern"] = {"level": read_concern}
    if options["locale"]:
        collation = {"locale": options["locale"]}
        for key, mapping in (
                ("caseLevel", REFERENCE_TRISTATE),
                ("caseFirst", REFERENCE_CASE_FIRST),
                ("alternate", REFERENCE_ALTERNATE),
                ("maxVariable", REFERENCE_MAX_VARIABLE),
                ("numericOrdering", REFERENCE_TRISTATE),
                ("backwards", REFERENCE_TRISTATE),
                ("normalization", REFERENCE_TRISTATE)):
            value = mapping[options[key]]
            if value is not None:
                collation[key] = value
        if options["strength"]:
            collation["strength"] = options["strength"]
        result["collation"] = collation
    if options["hint"]:
        result["hint"] = options["hint"]
    if options["comment"]:
        result["comment"] = options["comment"]
    if options["let"]:
        let = _stage_value_loads(options["let"])
        if not isinstance(let, dict):
            raise DatabaseError("Reference .nagg option let must be a Canonical EJSON document")
        result["let"] = let
    if options["enableWriteConcern"]:
        w = options["w"]
        if isinstance(w, str) and w.strip().isdigit():
            w = int(w.strip())
        write_concern = {"w": w or 1}
        journal = REFERENCE_TRISTATE[options["j"]]
        if journal is not None:
            write_concern["j"] = journal
        if options["wtimeout"] > 0:
            write_concern["wtimeout"] = options["wtimeout"]
        result["writeConcern"] = write_concern
    return result


def pipeline_script(definition):
    validate_pipeline(definition)
    stages = pipeline_documents(definition)
    # Extended JSON is a data interchange format, not executable shell BSON:
    # e.g. {$numberLong: "5"} is an ordinary object in JavaScript. Reuse the
    # qualified constructors and refuse values the shell cannot express exactly.
    if stages:
        rendered = []
        for stage in stages:
            operation, body = next(iter(stage.items()))
            rendered.append(f"  {{{operation}: {bson_literal(body, 1)}}}")
        stage_text = "[\n" + ",\n".join(rendered) + "\n]"
    else:
        stage_text = "[]"
    text = f"db.getCollection({json.dumps(definition.source)}).aggregate({stage_text}"
    options = pipeline_aggregate_options(definition)
    if options:
        text += ",\n" + bson_literal(options)
    return text + ");"


def _stage_value_dumps(value):
    try:
        raw = _encoded(value)
        text = json_util.dumps(
            value, json_options=JSON_OPTIONS, ensure_ascii=True, separators=(",", ":"))
        decoded = json_util.loads(text, json_options=JSON_OPTIONS)
        if _encoded(decoded) != raw:
            raise ValueError()
        return text
    except Exception:
        raise DatabaseError("Stage body cannot be represented as exact compact Canonical EJSON") from None


def _strip_mql_comments(text):
    """Remove JavaScript comments outside quoted strings while keeping offsets readable."""
    result, index, quote, escaped = [], 0, None, False
    while index < len(text):
        char = text[index]
        if quote is not None:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            result.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                raise DatabaseError("Reference .nagg MQL contains an unterminated block comment")
            result.extend("\n" for value in text[index:end + 2] if value == "\n")
            index = end + 2
            continue
        result.append(char)
        index += 1
    if quote is not None:
        raise DatabaseError("Reference .nagg MQL contains an unterminated string")
    return "".join(result)


def _stage_value_loads(text):
    if not isinstance(text, str):
        raise DatabaseError("Reference .nagg MQL must be text")
    try:
        return canonical_loads('{"value":' + _strip_mql_comments(text) + '}')["value"]
    except Exception:
        raise DatabaseError("Reference .nagg MQL is not valid Canonical Extended JSON") from None


parse_mql = _stage_value_loads
format_mql = _stage_value_dumps


def to_nagg(definition):
    """Write the Navicat 18.0.5 Version 0.0 .nagg contract."""
    validate_pipeline(definition)
    options = deepcopy(REFERENCE_OPTION_DEFAULTS)
    options.update(deepcopy(definition.reference_options))
    document = {
        "Version": definition.reference_version,
        "Collection": definition.source,
        "PreviewSourceOption": {
            "SampleMode": REFERENCE_SAMPLE_MODES[definition.sample_mode],
            "SampleSize": definition.sample_size,
        },
        "Stages": [],
        "Option": options,
    }
    for stage in definition.stages:
        item = {
            "Op": stage.operator,
            "MQL": stage.reference_mql if stage.reference_mql is not None else _stage_value_dumps(stage.body),
            "Enabled": bool(stage.enabled),
        }
        item.update({key: deepcopy(value) for key, value in stage.reference_extra.items()
                     if key not in {"Op", "MQL", "Enabled"}})
        document["Stages"].append(item)
    document.update({key: deepcopy(value) for key, value in definition.reference_extra.items()
                     if key not in {"Version", "Collection", "PreviewSourceOption", "Stages", "Option"}})
    try:
        return json.dumps(document, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise DatabaseError("Reference .nagg metadata cannot be represented as plain JSON") from None


def _from_legacy_nagg(document):
    raw_stages = document.get("stages")
    if not isinstance(raw_stages, list):
        raise DatabaseError("The .nagg stages must be an array")
    stages = []
    for item in raw_stages:
        if not isinstance(item, dict):
            raise DatabaseError("Each .nagg stage must be a document")
        stage_id = item.get("stageId")
        stage_id = stage_id if isinstance(stage_id, str) and stage_id else uuid.uuid4().hex
        stages.append(PipelineStage(
            operator=str(item.get("operator", "")), body=deepcopy(item.get("body", {})),
            enabled=item.get("enabled") is not False, stage_id=stage_id))
    definition = PipelineDefinition(
        name=str(document.get("name", "")), source=str(document.get("source", "")),
        stages=stages, sample_mode=str(document.get("sampleMode", "random")),
        sample_size=document.get("sampleSize", 1000),
        selected_stage=document.get("selectedStage") if isinstance(document.get("selectedStage"), int) else 0,
    )
    return validate_pipeline(definition)


def _from_reference_nagg(document):
    version = document.get("Version")
    source = document.get("Collection")
    preview = document.get("PreviewSourceOption")
    options = document.get("Option", {})
    raw_stages = document.get("Stages")
    if not isinstance(version, str) or not version:
        raise DatabaseError("Reference .nagg Version must be nonempty text")
    if not isinstance(source, str) or not source:
        raise DatabaseError("Reference .nagg Collection must be nonempty text")
    if not isinstance(preview, dict) or not isinstance(options, dict) or not isinstance(raw_stages, list):
        raise DatabaseError("Reference .nagg preview, options or stages have an invalid shape")
    mode_value = preview.get("SampleMode")
    sample_size = preview.get("SampleSize")
    if isinstance(mode_value, bool) or not isinstance(mode_value, int) or mode_value not in REFERENCE_SAMPLE_NAMES:
        raise DatabaseError("Reference .nagg SampleMode must be 0, 1 or 2")
    stages = []
    for item in raw_stages:
        if not isinstance(item, dict):
            raise DatabaseError("Each reference .nagg stage must be a document")
        operator, mql, enabled = item.get("Op"), item.get("MQL"), item.get("Enabled")
        if not isinstance(enabled, bool):
            raise DatabaseError("Reference .nagg Enabled must be boolean")
        stages.append(PipelineStage(
            operator=operator,
            body=_stage_value_loads(mql),
            enabled=enabled,
            reference_mql=mql,
            reference_extra={key: deepcopy(value) for key, value in item.items()
                             if key not in {"Op", "MQL", "Enabled"}},
        ))
    return validate_pipeline(PipelineDefinition(
        source=source,
        stages=stages,
        sample_mode=REFERENCE_SAMPLE_NAMES[mode_value],
        sample_size=sample_size,
        selected_stage=0 if stages else None,
        reference_version=version,
        reference_options={
            key: deepcopy(value) for key, value in options.items()
            if key not in REFERENCE_OPTION_DEFAULTS or value != REFERENCE_OPTION_DEFAULTS[key]
        },
        reference_extra={key: deepcopy(value) for key, value in document.items()
                         if key not in {"Version", "Collection", "PreviewSourceOption", "Stages", "Option"}},
    ))


def from_nagg(text):
    if not isinstance(text, str) or not text.strip():
        raise DatabaseError("The .nagg document is empty")
    try:
        document = canonical_loads(text)
    except Exception:
        raise DatabaseError("The .nagg document is not valid Canonical Extended JSON") from None
    if not isinstance(document, dict):
        raise DatabaseError("This file is not an aggregation pipeline")
    if document.get("format") == "opennavicat-nagg-v1":
        return _from_legacy_nagg(document)
    if "Version" in document and "Stages" in document:
        return _from_reference_nagg(document)
    raise DatabaseError("This file is not an OpenNavicat or Navicat Version 0.0 aggregation pipeline")


def parse_query_pipeline(source, text):
    """Parse a JSON/Canonical-EJSON stage array imported into a pipeline draft."""
    try:
        document = canonical_loads('{"value":' + text + '}')
        stages = document["value"]
    except Exception:
        raise DatabaseError("Import a JSON or Canonical Extended JSON aggregation stage array") from None
    if not isinstance(stages, list) or any(not isinstance(stage, dict) or len(stage) != 1 for stage in stages):
        raise DatabaseError("Imported pipeline stages must be one-field documents")
    return PipelineDefinition(source=source, stages=[
        PipelineStage(next(iter(stage)), deepcopy(next(iter(stage.values())))) for stage in stages])


def _sample_prefix(definition):
    if definition.sample_mode == "random":
        return [{"$sample": {"size": definition.sample_size}}]
    if definition.sample_mode == "first":
        return [{"$limit": definition.sample_size}]
    return []


def _aggregate_rows(session, database, definition, pipeline, limit, *, allow_write=False):
    collect_limit = MAX_EXECUTION_ROWS if allow_write else MAX_PREVIEW_ROWS
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= collect_limit:
        raise DatabaseError("Aggregation row limit is invalid")
    session._validate_namespace(database, definition.source)
    writes = any(next(iter(stage)) in WRITE_STAGES
                 for stage in pipeline if isinstance(stage, dict) and stage)
    if writes and not allow_write:
        raise DatabaseError("Preview does not execute $out or $merge stages")
    if session.config.read_only and any(
            next(iter(stage)) in WRITE_STAGES | STREAM_STAGES
            for stage in pipeline if isinstance(stage, dict) and stage):
        raise DatabaseError("This aggregation pipeline contains a write or streaming stage")
    bounded = list(pipeline) if writes else [
        *pipeline, {"$limit": min(limit + 1, collect_limit + 1)}]
    rows, size, truncated = [], 0, False
    aggregate_options = pipeline_aggregate_options(definition)
    user_comment = aggregate_options.pop("comment", None)
    aggregate_options.setdefault("batchSize", min(limit + 1, 100))
    operation = session._operation(user_comment) if user_comment is not None else session._operation()
    with operation as comment:
        aggregate_options["comment"] = comment
        cursor = session.connection.get_database(database, codec_options=RAW_CODEC)[definition.source].aggregate(
            bounded, **aggregate_options)
        try:
            for document in cursor:
                if len(rows) >= limit or (rows and size + len(document.raw) > _MAX_PAGE_BYTES):
                    truncated = True
                    break
                value = BSON(document.raw).decode(codec_options=BSON_CODEC)
                rows.append(canonical_dumps(value))
                size += len(document.raw)
        finally:
            cursor.close()
    return rows, truncated


def preview_pipeline(session, database, definition, *, limit=50):
    validate_pipeline(definition)
    # A $limit bounds delivered events, not the time spent waiting for one.
    # Reject the complete enabled stream before even fetching source previews.
    if any(stage.operator in STREAM_STAGES for stage in definition.stages if stage.enabled):
        raise DatabaseError("Preview does not execute change-stream stages; use a dedicated streaming workflow")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PREVIEW_ROWS:
        raise DatabaseError("Pipeline preview row limit is invalid")
    sample = _sample_prefix(definition)
    source_rows, source_truncated = _aggregate_rows(session, database, definition, sample, limit)
    available = pipeline_documents(definition)
    stage_rows = []
    truncated = source_truncated
    for index, stage in enumerate(available):
        rows, more = _aggregate_rows(
            session, database, definition, [*sample, *available[:index + 1]], limit)
        stage_rows.append({"index": index, "operator": next(iter(stage)),
                           "rows": rows, "truncated": more})
        truncated = truncated or more
    return PipelinePreview(
        source_rows, stage_rows, stage_rows[-1]["rows"] if stage_rows else source_rows, truncated)


def execute_pipeline(session, database, definition, *, limit=1000):
    validate_pipeline(definition)
    if any(stage.operator in STREAM_STAGES for stage in definition.stages if stage.enabled):
        raise DatabaseError("Change streams remain a dedicated streaming workflow")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EXECUTION_ROWS:
        raise DatabaseError("Aggregation execution limit is invalid")
    if session.config.read_only and any(
            stage.operator in WRITE_STAGES | STREAM_STAGES
            for stage in definition.stages if stage.enabled):
        raise DatabaseError("This aggregation pipeline requires a writable session")
    documents = pipeline_documents(definition)
    return _aggregate_rows(
        session, database, definition, documents, limit, allow_write=True)


def explain_pipeline(session, database, definition, *, verbosity="executionStats"):
    validate_pipeline(definition)
    if any(stage.operator in WRITE_STAGES or stage.operator in STREAM_STAGES
           for stage in definition.stages if stage.enabled):
        raise DatabaseError("Write and change-stream stages cannot be explained in this workspace")
    aggregate = {"aggregate": definition.source, "pipeline": pipeline_documents(definition), "cursor": {}}
    aggregate_options = pipeline_aggregate_options(definition)
    user_comment = aggregate_options.pop("comment", None)
    batch_size = aggregate_options.pop("batchSize", None)
    if batch_size is not None:
        aggregate["cursor"]["batchSize"] = batch_size
    aggregate.update(aggregate_options)
    command = {"explain": aggregate, "verbosity": verbosity}
    session.use_schema(database)
    operation = session._operation(user_comment) if user_comment is not None else session._operation()
    with timeout(30), operation as comment:
        aggregate["comment"] = comment
        reply = session.connection[database].command(command)
    return parse_explain_reply(reply)
