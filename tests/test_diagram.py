"""Visual relationships must use actual metadata and stay connected when moved."""
from contextlib import closing
import sqlite3
import xml.etree.ElementTree as ET

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.ui.diagram import SchemaDiagram


def test_real_foreign_key_diagram_movement_and_native_exports(qtbot, tmp_path):
    path = tmp_path / "diagram.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE books (id INTEGER PRIMARY KEY, author_id INTEGER REFERENCES authors(id), title TEXT);
        ''')
    session = DatabaseSession(ConnectionConfig("diagram", "Diagram fixture", database=str(path)))
    session.connect()
    try:
        tables = [{"name": item["name"], "columns": session.columns("main", item["name"]),
                   "foreign_keys": session.foreign_keys("main", item["name"])}
                  for item in session.objects("main")]
    finally:
        session.close()
    diagram = SchemaDiagram("Diagram fixture", "main", tables)
    qtbot.addWidget(diagram)
    diagram.resize(1000, 650)
    diagram.show()
    qtbot.waitUntil(lambda: diagram.view.isVisible())
    assert diagram.node_count == 2
    assert diagram.edge_count == 1
    assert diagram.unresolved_relationships == 0
    edge = diagram.edges[0]
    assert edge.source.name == "books"
    assert edge.target.name == "authors"
    before = edge.path().boundingRect()
    authors = diagram.nodes["authors"]
    position = authors.pos()
    start = diagram.view.mapFromScene(authors.mapToScene(QPointF(70, 18)))
    qtbot.mousePress(diagram.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(diagram.view.viewport(), pos=start + QPoint(90, 60))
    qtbot.mouseRelease(diagram.view.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(90, 60))
    assert authors.pos() != position
    assert edge.path().boundingRect() != before
    assert authors.sceneBoundingRect().adjusted(-2, -2, 2, 2).contains(edge.end)
    assert diagram.nodes["books"].sceneBoundingRect().adjusted(-2, -2, 2, 2).contains(edge.start)
    png, svg = tmp_path / "schema.png", tmp_path / "schema.svg"
    diagram.export_to(png)
    image = QImage(str(png))
    assert not image.isNull()
    assert image.width() > 400 and image.height() > 100
    # More than a background rectangle was painted into the actual image.
    colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 7)
              for y in range(0, image.height(), 7)}
    assert len(colors) > 10
    diagram.export_to(svg)
    tree = ET.parse(svg)
    assert tree.getroot().tag.endswith("svg")
    text = svg.read_text("utf-8")
    assert "authors" in text and "books" in text and "author_id" in text
    assert "<path" in text


def test_qualified_names_and_external_schema_references(qtbot):
    columns = [{"name": "id", "type": "INTEGER", "pk": True}]
    diagram = SchemaDiagram("Test", "sales", [
        {"name": "order.items", "columns": columns},
        {"name": "events", "columns": columns, "foreign_keys": [
            {"columns": ["id"], "referenced_table": '"sales"."order.items"', "referenced_columns": ["id"]},
            {"columns": ["id"], "referenced_schema": "other", "referenced_table": "order.items", "referenced_columns": ["id"]},
        ]},
    ])
    qtbot.addWidget(diagram)
    assert diagram.node_count == 2
    assert diagram.edge_count == 1
    assert diagram.unresolved_relationships == 1
    assert diagram.edges[0].target.name == "order.items"


def test_empty_schema_still_renders_and_exports(qtbot, tmp_path):
    diagram = SchemaDiagram("Empty", "main", [])
    qtbot.addWidget(diagram)
    assert diagram.node_count == 0 and diagram.edge_count == 0
    diagram.fit()
    diagram.export_to(tmp_path / "empty.png")
    assert not QImage(str(tmp_path / "empty.png")).isNull()
