"""MongoDB collection information panel uses real object-list statistics."""
from opennavicat.ui.workspace import Inspector


def test_mongo_collection_inspector_displays_all_observed_statistics(qtbot):
    inspector = Inspector()
    qtbot.addWidget(inspector)
    inspector.show_object({
        "name": "documents", "schema": "sample", "kind": "mongo_collection",
        "stats": {"count": 1234, "capped": False, "size": 4096, "avgObjSize": 512,
                  "storageSize": 8192, "nindexes": 3, "totalIndexSize": 2048},
    })
    text = inspector.details.text()
    assert "文档数\n1,234" in text
    assert "已固定\n否" in text
    assert "内存中的总大小\n4 KB (4,096)" in text
    assert "对象的平均大小\n512 B" in text
    assert "存储大小\n8 KB (8,192)" in text
    assert "索引数\n3" in text
    assert "索引的总大小\n2 KB (2,048)" in text


def test_mongo_timeseries_missing_optional_statistics_are_unknown(qtbot):
    inspector = Inspector()
    qtbot.addWidget(inspector)
    inspector.show_object({"name": "measurements", "schema": "sample", "kind": "mongo_timeseries", "stats": {}})
    text = inspector.details.text()
    for label in ("文档数", "已固定", "内存中的总大小", "对象的平均大小", "存储大小", "索引数", "索引的总大小"):
        assert label in text
    assert text.count("—") >= 7
