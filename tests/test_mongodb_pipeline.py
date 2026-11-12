"""Aggregation pipeline model and real-server qualification on owned MongoDB."""
from __future__ import annotations

from bson import Decimal128, Int64
import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_pipeline import (
    STAGE_NAMES, PipelineDefinition, PipelineStage, execute_pipeline,
    explain_pipeline, from_nagg, parse_query_pipeline, pipeline_script,
    parse_explain_reply, preview_pipeline, to_nagg, validate_pipeline,
)
from test_mongodb_session import mongodb_service, session  # noqa: F401 - pytest fixtures


def test_stage_catalog_matches_reference_order():
    assert len(STAGE_NAMES) == 42
    assert STAGE_NAMES[:6] == (
        "$addFields", "$bucket", "$bucketAuto", "$changeStream",
        "$changeStreamSplitLargeEvent", "$collStats")
    assert STAGE_NAMES[-8:] == (
        "$setWindowFields", "$sharedDataDistribution", "$skip", "$sort",
        "$sortByCount", "$unionWith", "$unset", "$unwind")


def test_nagg_roundtrip_preserves_bson_and_disabled_stages():
    definition = PipelineDefinition(
        name="generated_pipeline", source="docs", sample_mode="first", sample_size=25,
        stages=[
            PipelineStage("$match", {"_id": Int64(5)}, True, "stage-one"),
            PipelineStage("$project", {"amount": Decimal128("1.25")}, False, "stage-two"),
        ], selected_stage=1)
    text = to_nagg(definition)
    restored = from_nagg(text)
    assert restored == definition
    assert restored.stages[0].body["_id"] == Int64(5)
    assert restored.stages[1].body["amount"] == Decimal128("1.25")
    assert restored.stages[1].enabled is False
    assert '"$numberLong": "5"' in pipeline_script(definition)


def test_nagg_and_query_import_reject_partial_or_ambiguous_stages():
    with pytest.raises(DatabaseError, match="OpenNavicat"):
        from_nagg('{"format":"other","stages":[]}')
    with pytest.raises(DatabaseError, match="one-field"):
        parse_query_pipeline("docs", '[{"$match":{},"$limit":1}]')
    with pytest.raises(DatabaseError, match="between"):
        validate_pipeline(PipelineDefinition(source="docs", sample_size=0,
            stages=[PipelineStage("$match", {})]))


def test_explain_projection_builds_nested_plan_and_preserves_unknown_fields():
    reply = {
        "explainVersion": "1",
        "queryPlanner": {"namespace": "demo.sales", "winningPlan": {"stage": "COLLSCAN"}},
        "executionStats": {
            "executionTimeMillis": 17,
            "executionStages": {
                "stage": "GROUP",
                "nReturned": 1,
                "executionTimeMillisEstimate": 12,
                "customNodeMetric": {"$numberLong": 7},
                "inputStage": {
                    "stage": "COLLSCAN",
                    "nReturned": 3,
                    "docsExamined": 4,
                    "executionTimeMillisEstimate": 10,
                    "unknownNodeFlag": True,
                },
            },
        },
        "unknownTopLevel": {"preserved": True},
        "ok": 1,
    }
    explain = parse_explain_reply(reply)
    assert explain.root is not None and explain.root.stage == "GROUP"
    assert [node.stage for node in explain.nodes()] == ["GROUP", "COLLSCAN"]
    assert explain.root.children[0].docs_examined == 4
    assert explain.raw["unknownTopLevel"] == {"preserved": True}
    assert explain.root.raw["customNodeMetric"] == Int64(7)
    assert "unknownNodeFlag" in explain.root.children[0].raw
    assert explain.stats_rows()[1]["stage"] == "COLLSCAN"
    assert explain.stats_rows()[1]["elapsed_percent"] > 80
    assert '"unknownTopLevel"' in explain.raw_text


def test_real_pipeline_preview_execute_and_explain(session):
    database = session.connection[session.config.database]
    database.pipeline_source.insert_many([
        {"group": "north", "amount": Int64(10)},
        {"group": "north", "amount": Int64(20)},
        {"group": "south", "amount": Int64(30)},
    ])
    definition = PipelineDefinition(
        name="generated_pipeline", source="pipeline_source", sample_mode="first", sample_size=100,
        stages=[
            PipelineStage("$match", {"group": "north"}),
            PipelineStage("$group", {"_id": "$group", "total": {"$sum": "$amount"}}),
        ])
    preview = preview_pipeline(session, session.config.database, definition, limit=20)
    assert len(preview.source_rows) == 3
    assert len(preview.stages[0]["rows"]) == 2
    assert len(preview.final_rows) == 1
    rows, truncated = execute_pipeline(session, session.config.database, definition)
    assert rows == preview.final_rows and not truncated
    plan = explain_pipeline(session, session.config.database, definition)
    assert plan.root is not None
    assert "executionStats" in plan.raw
    assert any(node.stage for node in plan.nodes())


def test_preview_refuses_out_but_execute_allows_it_only_on_writable_session(session):
    database = session.connection[session.config.database]
    database.pipeline_source.insert_one({"group": "north", "amount": 1})
    definition = PipelineDefinition(
        name="write_pipeline", source="pipeline_source",
        stages=[PipelineStage("$out", "pipeline_output")])
    with pytest.raises(DatabaseError, match="Preview does not execute"):
        preview_pipeline(session, session.config.database, definition)
    rows, _truncated = execute_pipeline(session, session.config.database, definition)
    assert rows == []
    assert database.pipeline_output.count_documents({}) == 1


def test_store_persists_pipeline_per_connection_and_schema(tmp_path):
    from opennavicat.storage import Store

    definition = PipelineDefinition(
        name="generated_pipeline", source="docs",
        stages=[PipelineStage("$match", {"generated": True})])
    store = Store(tmp_path / "workspace")
    entry = store.save_mongo_pipeline("connection-a", "catalog", "generated_pipeline", to_nagg(definition))
    assert store.mongo_pipeline(entry["id"])["source"] == "docs"
    assert store.mongo_pipelines("connection-a", "catalog")[0]["name"] == "generated_pipeline"
    assert store.mongo_pipelines("connection-b", "catalog") == []

    definition.name = "renamed_pipeline"
    updated = store.save_mongo_pipeline(
        "connection-a", "catalog", definition.name, to_nagg(definition), pipeline_id=entry["id"])
    assert updated["id"] == entry["id"] and updated["created"] == entry["created"]
    restarted = Store(tmp_path / "workspace")
    restored = restarted.mongo_pipeline(entry["id"])
    assert restored["name"] == "renamed_pipeline"
    assert from_nagg(restored["nagg"]).name == "renamed_pipeline"
    restarted.delete_mongo_pipeline(entry["id"])
    assert Store(tmp_path / "workspace").mongo_pipeline(entry["id"]) is None
