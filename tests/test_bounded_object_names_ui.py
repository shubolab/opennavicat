"""Long object names stay recoverable and paint an explicit bounded ellipsis."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPushButton, QStyle, QStyleOptionButton
import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage
from opennavicat.ui.mongo_pipeline import MongoPipelinePage
from opennavicat.ui.theme import apply_theme
from opennavicat.ui.workspace import Inspector


@pytest.fixture(autouse=True)
def isolated_appearance(qapp):
    style, palette, font = qapp.styleSheet(), qapp.palette(), qapp.font()
    theme = qapp.property("workbenchTheme")
    qapp.setStyleSheet("")
    yield
    qapp.setPalette(palette)
    qapp.setFont(font)
    qapp.setStyleSheet(style)
    qapp.setProperty("workbenchTheme", theme)


def settle(qapp):
    for _ in range(4):
        qapp.processEvents()


@pytest.mark.parametrize("theme,pixels", [(None, 12), (None, 24), ("dark", 12), ("light", 12)])
def test_pipeline_cards_paint_complete_short_names_or_explicit_ellipsis(qtbot, qapp, theme, pixels):
    if theme:
        apply_theme(qapp, theme)
    page = MongoPipelinePage("generated", "generated_catalog")
    qtbot.addWidget(page)
    font = QFont(page.font()); font.setPixelSize(pixels); page.setFont(font)
    page.resize(1100, 750)
    page.show()
    for name in ("docs", "generated_stream_events_with_an_unbroken_long_name"):
        page.load_definition(PipelineDefinition(source=name, stages=[PipelineStage("$setWindowFields", {})]))
        settle(qapp)
        source = page.findChild(QLabel, "mongoPipelineSourceCard")
        card = page.findChild(QPushButton, "mongoPipelineStageCard0")
        assert source.size().toTuple() == (105, 105)
        assert card.size().toTuple() == (115, 105)
        assert page.draft().source == name
        assert page.draft().stages[0].operator == "$setWindowFields"

        # Compare the real rendered widgets with ordinary Qt controls containing
        # the expected bounded text. This catches raw clipping and altered borders.
        width = source.contentsRect().width() - 2 * source.margin()
        shown = source.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, width)
        if name == "docs":
            assert shown == name
        else:
            assert shown != name and shown.endswith("…")
        expected = QLabel("源\n" + shown, source.parentWidget())
        expected.setTextFormat(Qt.TextFormat.PlainText)
        expected.setAlignment(source.alignment())
        expected.setFont(source.font())
        expected.setStyleSheet(source.styleSheet())
        expected.setFixedSize(source.size())
        expected.show(); settle(qapp)
        assert source.grab().toImage() == expected.grab().toImage()
        expected.hide(); expected.deleteLater()

        option = QStyleOptionButton(); card.initStyleOption(option)
        content = card.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, card)
        shown = "\n".join(card.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight,
            content.width()) for line in card.text().split("\n"))
        expected_button = QPushButton(shown, card.parentWidget())
        expected_button.setCheckable(True)
        expected_button.setChecked(card.isChecked())
        expected_button.setFont(card.font())
        expected_button.setStyleSheet(card.styleSheet())
        expected_button.setFixedSize(card.size())
        expected_button.show(); settle(qapp)
        assert card.grab().toImage() == expected_button.grab().toImage()
        expected_button.hide(); expected_button.deleteLater()
        assert source.text() == source.toolTip() == source.accessibleName() == "源\n" + name
        assert card.text() == card.toolTip() == card.accessibleName() == "阶段 1\n$setWindowFields"


def test_inspector_exposes_full_names_even_when_narrow(qtbot, qapp):
    inspector = Inspector()
    qtbot.addWidget(inspector)
    title = "generated_table_name_that_exceeds_the_narrow_inspector"
    connection = "Generated connection with a long display name"
    schema = "generated_schema_name_with_no_breaks"
    inspector.show_object({"name": title, "schema": schema, "kind": "table"},
        ConnectionConfig("generated", connection))
    inspector.resize(200, 600)
    inspector.show(); settle(qapp)
    assert inspector.width() == 200
    assert inspector.title.text() == inspector.title.toolTip() == inspector.title.accessibleName() == title
    assert inspector.connection.text() == inspector.connection.toolTip() == connection
    assert inspector.schema.text() == inspector.schema.toolTip() == schema
    inspector.show_object({"name": "next", "schema": "main", "kind": "table"})
    assert inspector.title.toolTip() == inspector.title.accessibleName() == "next"
    assert inspector.connection.toolTip() == ""
    assert inspector.schema.toolTip() == "main"
