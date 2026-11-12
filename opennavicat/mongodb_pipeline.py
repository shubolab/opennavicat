"""Bounded MongoDB aggregation-pipeline drafts, previews and .nagg persistence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import uuid

from bson import BSON
from pymongo import timeout

from .database import DatabaseError
from .mongodb_session import BSON_CODEC, RAW_CODEC, _MAX_PAGE_BYTES, canonical_dumps, canonical_loads


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


@dataclass(eq=False)
class PipelineStage:
    operator: str = "$match"
    body: object = field(default_factory=dict, repr=False)
    enabled: bool = True
    stage_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __eq__(self, other):
        return (isinstance(other, PipelineStage) and self.operator == other.operator
                and self.enabled == other.enabled
                and _encoded(self.body) == _encoded(other.body))


@dataclass(eq=False)
class PipelineDefinition:
    name: str = ""
    source: str = ""
    stages: list[PipelineStage] = field(default_factory=list)
    sample_mode: str = "random"
    sample_size: int = 1000
    selected_stage: int | None = 0

    def __eq__(self, other):
        return (isinstance(other, PipelineDefinition) and self.name == other.name
                and self.source == other.source and self.sample_mode == other.sample_mode
                and self.sample_size == other.sample_size and self.stages == other.stages)


@dataclass
class PipelinePreview:
    source_rows: list[str]
    stages: list[dict]
    final_rows: list[str]
    truncated: bool = False


def _encoded(value):
    return BSON.encode({"value": value}, codec_options=BSON_CODEC)


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
    if definition.sample_mode not in {"random", "first"}:
        raise DatabaseError("Preview sample mode must be random or first")
    if (isinstance(definition.sample_size, bool) or not isinstance(definition.sample_size, int)
            or not 1 <= definition.sample_size <= 100000):
        raise DatabaseError("Preview sample size must be between 1 and 100000")
    for stage in definition.stages:
        if not isinstance(stage, PipelineStage) or stage.operator not in STAGE_NAMES:
            raise DatabaseError("Unsupported aggregation stage")
        try:
            raw = BSON.encode({"body": stage.body}, codec_options=BSON_CODEC)
            decoded = BSON(raw).decode(codec_options=BSON_CODEC)
            if (len(raw) > _MAX_PAGE_BYTES
                    or BSON.encode(decoded, codec_options=BSON_CODEC) != raw):
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


def pipeline_script(definition):
    validate_pipeline(definition)
    stages = pipeline_documents(definition)
    lines = [f"db.getCollection({json.dumps(definition.source)}).aggregate(["]
    for index, stage in enumerate(stages):
        operation, body = next(iter(stage.items()))
        text = canonical_dumps(body).replace("\n", "\n  ")
        comma = "," if index + 1 < len(stages) else ""
        lines.append(f"  {{{operation}: {text}}}{comma}")
    lines.append("]);")
    return "\n".join(lines)


def to_nagg(definition):
    validate_pipeline(definition)
    document = {
        "format": "opennavicat-nagg-v1",
        "name": definition.name,
        "source": definition.source,
        "sampleMode": definition.sample_mode,
        "sampleSize": definition.sample_size,
        "selectedStage": definition.selected_stage,
        "stages": [{"operator": stage.operator, "body": deepcopy(stage.body),
                    "enabled": stage.enabled, "stageId": stage.stage_id}
                   for stage in definition.stages],
    }
    return canonical_dumps(document) + "\n"


def from_nagg(text):
    if not isinstance(text, str) or not text.strip():
        raise DatabaseError("The .nagg document is empty")
    try:
        document = canonical_loads(text)
    except Exception:
        raise DatabaseError("The .nagg document is not valid Canonical Extended JSON") from None
    if not isinstance(document, dict) or document.get("format") != "opennavicat-nagg-v1":
        raise DatabaseError("This file is not an OpenNavicat aggregation pipeline")
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
    return [{"$limit": definition.sample_size}]


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
    with session._operation() as comment:
        cursor = session.connection.get_database(database, codec_options=RAW_CODEC)[definition.source].aggregate(
            bounded, batchSize=min(limit + 1, 100), comment=comment)
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
    command = {"explain": {"aggregate": definition.source, "pipeline": pipeline_documents(definition), "cursor": {}},
               "verbosity": verbosity}
    session.use_schema(database)
    with timeout(30), session._operation() as comment:
        reply = session.connection[database].command({**command, "comment": comment})
    return canonical_dumps(reply)
