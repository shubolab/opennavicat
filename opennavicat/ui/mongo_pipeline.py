"""Native Qt workspace for MongoDB aggregation pipelines."""
from __future__ import annotations

from copy import deepcopy
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from opennavicat.database import DatabaseError
from opennavicat.mongodb_pipeline import (
    STAGE_NAMES, PipelineDefinition, PipelinePreview, PipelineStage,
    explain_pipeline, from_nagg, pipeline_script, preview_pipeline,
    to_nagg, validate_pipeline,
)
from opennavicat.mongodb_session import canonical_dumps, canonical_loads

from .icons import tool_icon


class MongoPipelineSaveDialog(QDialog):
    def __init__(self, definition, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存聚合管道")
        self.setMinimumSize(720, 520)
        self._definition = deepcopy(definition)
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        form.addWidget(QLabel("名称:"))
        self.name_edit = QLineEdit(definition.name)
        self.name_edit.setObjectName("mongoPipelineName")
        form.addWidget(self.name_edit, 1)
        layout.addLayout(form)
        layout.addWidget(QLabel("脚本预览:"))
        self.preview = QPlainTextEdit(pipeline_script(definition))
        self.preview.setReadOnly(True)
        self.preview.setObjectName("mongoPipelineSavePreview")
        layout.addWidget(self.preview, 1)
        self.suppress_check = QCheckBox("不再显示脚本预览")
        layout.addWidget(self.suppress_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.name_edit.textChanged.connect(self._name_changed)
        layout.addWidget(buttons)
        self._name_changed(self.name_edit.text())

    def _name_changed(self, text):
        self.save_button.setEnabled(bool(text.strip()))

    def definition(self):
        value = deepcopy(self._definition)
        value.name = self.name_edit.text().strip()
        return value


class MongoPipelinePage(QWidget):
    titleChanged = Signal(str)
    dirtyChanged = Signal(bool)
    previewRequested = Signal(object)
    runRequested = Signal(object)
    explainRequested = Signal(object)
    saveRequested = Signal(object)
    importRequested = Signal()
    exportRequested = Signal()
    focusRequested = Signal(bool)

    def __init__(self, connection_id, schema, name="", parent=None):
        super().__init__(parent)
        self.setObjectName("mongoPipelinePage")
        self.kind = "mongo_pipeline"
        self.connection_id = connection_id
        self.schema = schema
        self.pipeline_id = None
        self.definition = PipelineDefinition(name=name)
        self.dirty = False
        self.loading = False
        self.running = False
        self.suppress_save_preview = False
        self._invalid = None
        self._preview = None
        self._sources = []
        self.last_rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("mongoPipelineToolbar")
        layout.addWidget(self.toolbar)
        self.save_action = self._action("保存", "save", self.request_save)
        self.import_action = self._action("导入查询", "import", self.importRequested.emit)
        self.preview_all_action = self._action("预览全部", "run", self.request_preview_all)
        self.run_action = self._action("运行", "run", self.request_run)
        self.explain_action = self._action("解释", "query", self.request_explain)
        self.export_action = self._action("导出结果", "export", self.exportRequested.emit)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.focus_action = self._action("进入专注模式", "design", None)
        self.focus_action.setCheckable(True)
        self.focus_action.toggled.connect(self.focusRequested)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)
        self.pipeline_tab = QWidget()
        pipeline_layout = QVBoxLayout(self.pipeline_tab)
        pipeline_layout.setContentsMargins(8, 8, 8, 8)
        source_controls = QHBoxLayout()
        source_controls.addWidget(QLabel("源集合:"))
        self.source_combo = QComboBox()
        self.source_combo.setObjectName("mongoPipelineSourceCombo")
        self.source_combo.setEditable(True)
        source_controls.addWidget(self.source_combo, 1)
        source_controls.addWidget(QLabel("预览样本:"))
        self.sample_mode = QComboBox()
        self.sample_mode.addItem("随机", "random")
        self.sample_mode.addItem("前 N 条", "first")
        source_controls.addWidget(self.sample_mode)
        self.sample_size = QSpinBox()
        self.sample_size.setRange(1, 100000)
        self.sample_size.setValue(1000)
        source_controls.addWidget(self.sample_size)
        source_controls.addWidget(QLabel("文档"))
        pipeline_layout.addLayout(source_controls)
        self.graph_scroll = QScrollArea()
        self.graph_scroll.setWidgetResizable(True)
        self.graph_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.graph_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.graph_scroll.setMinimumHeight(230)
        self.graph_scroll.setMaximumHeight(310)
        self.graph_widget = QWidget()
        self.graph_row = QHBoxLayout(self.graph_widget)
        self.graph_row.setContentsMargins(16, 10, 16, 10)
        self.graph_row.setSpacing(12)
        self.graph_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.graph_widget.setStyleSheet("background: #202124; color: #e8eaed;")
        self.graph_scroll.setWidget(self.graph_widget)
        pipeline_layout.addWidget(self.graph_scroll)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.input_panel = self._preview_panel("输入预览")
        self.editor_panel = QWidget()
        editor_layout = QVBoxLayout(self.editor_panel)
        editor_layout.setContentsMargins(6, 4, 6, 4)
        stage_header = QHBoxLayout()
        stage_header.addWidget(QLabel("运算符:"))
        self.operator_combo = QComboBox()
        self.operator_combo.setObjectName("mongoPipelineOperator")
        self.operator_combo.addItems(STAGE_NAMES)
        stage_header.addWidget(self.operator_combo)
        stage_header.addStretch()
        self.enabled_check = QCheckBox("启用")
        self.enabled_check.setChecked(True)
        stage_header.addWidget(self.enabled_check)
        editor_layout.addLayout(stage_header)
        self.stage_editor = QPlainTextEdit()
        self.stage_editor.setObjectName("mongoPipelineStageEditor")
        editor_layout.addWidget(self.stage_editor, 1)
        stage_actions = QHBoxLayout()
        self.add_stage_button = QPushButton("添加阶段")
        self.stage_preview_button = QPushButton("预览")
        self.delete_stage_button = QPushButton("删除")
        self.up_button = QPushButton("上移")
        self.down_button = QPushButton("下移")
        stage_actions.addWidget(self.add_stage_button)
        stage_actions.addWidget(self.stage_preview_button)
        stage_actions.addWidget(self.delete_stage_button)
        stage_actions.addWidget(self.up_button)
        stage_actions.addWidget(self.down_button)
        stage_actions.addStretch()
        editor_layout.addLayout(stage_actions)
        self.output_panel = self._preview_panel("输出预览")
        self.splitter.addWidget(self.input_panel)
        self.splitter.addWidget(self.editor_panel)
        self.splitter.addWidget(self.output_panel)
        self.splitter.setSizes([320, 480, 320])
        pipeline_layout.addWidget(self.splitter, 1)
        self.tabs.addTab(self.pipeline_tab, "管道")

        self.script_tab = QWidget()
        script_layout = QVBoxLayout(self.script_tab)
        self.script_mode = QTabWidget()
        self.script_editor = QPlainTextEdit()
        self.script_editor.setReadOnly(True)
        self.script_editor.setObjectName("mongoPipelineScript")
        self.ddl_editor = QPlainTextEdit()
        self.ddl_editor.setReadOnly(True)
        self.ddl_editor.setObjectName("mongoPipelineDDL")
        self.script_mode.addTab(self.script_editor, "更改")
        self.script_mode.addTab(self.ddl_editor, "DDL")
        script_layout.addWidget(self.script_mode)
        self.tabs.addTab(self.script_tab, "脚本预览")

        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setObjectName("mongoPipelineMessages")
        self.tabs.addTab(self.messages, "消息")
        self.results = QTableWidget(0, 1)
        self.results.setObjectName("mongoPipelineResults")
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setHorizontalHeaderLabels(["Canonical Extended JSON"])
        self.tabs.addTab(self.results, "结果")

        self.operator_combo.currentTextChanged.connect(self._operator_changed)
        self.stage_editor.textChanged.connect(self._body_changed)
        self.enabled_check.toggled.connect(self._enabled_changed)
        self.add_stage_button.clicked.connect(self.add_stage)
        self.stage_preview_button.clicked.connect(self.request_preview_all)
        self.delete_stage_button.clicked.connect(self.delete_stage)
        self.up_button.clicked.connect(lambda: self.move_stage(-1))
        self.down_button.clicked.connect(lambda: self.move_stage(1))
        self.source_combo.currentTextChanged.connect(self._source_changed)
        self.sample_mode.currentIndexChanged.connect(self._sample_changed)
        self.sample_size.valueChanged.connect(self._sample_changed)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.load_definition(self.definition)

    @property
    def title(self):
        return (self.definition.name or "无标题") + (" — 已编辑" if self.dirty else "")

    def _action(self, text, symbol, callback):
        action = QAction(tool_icon(symbol, 18), text, self)
        if callback is not None:
            action.triggered.connect(callback)
        self.toolbar.addAction(action)
        self.addAction(action)
        return action

    def _preview_panel(self, title):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel(title))
        table = QTableWidget(0, 1)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setHorizontalHeaderLabels(["Canonical Extended JSON"])
        layout.addWidget(table, 1)
        panel.table = table
        return panel

    def set_sources(self, sources):
        current = self.source_combo.currentText()
        self._sources = [str(value) for value in sources]
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems(self._sources)
        if current:
            self.source_combo.setCurrentText(current)
        elif self.definition.source:
            self.source_combo.setCurrentText(self.definition.source)
        self.source_combo.blockSignals(False)
        self._source_changed(self.source_combo.currentText())

    def load_definition(self, definition, *, pipeline_id=None):
        self.definition = deepcopy(definition)
        if not self.definition.stages:
            self.definition.stages = [PipelineStage()]
        self.definition.selected_stage = min(
            max(0, self.definition.selected_stage or 0), len(self.definition.stages) - 1)
        self.pipeline_id = pipeline_id
        self._invalid = None
        self.dirty = False
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentText(self.definition.source)
        self.source_combo.blockSignals(False)
        self.sample_mode.blockSignals(True)
        self.sample_mode.setCurrentIndex(self.sample_mode.findData(self.definition.sample_mode))
        self.sample_mode.blockSignals(False)
        self.sample_size.blockSignals(True)
        self.sample_size.setValue(self.definition.sample_size)
        self.sample_size.blockSignals(False)
        self._render_all()
        self.dirtyChanged.emit(False)
        self.titleChanged.emit(self.title)

    def load_nagg(self, text, *, pipeline_id=None):
        self.load_definition(from_nagg(text), pipeline_id=pipeline_id)

    def refresh(self):
        self._render_all()

    def can_discard(self, retry=None):
        if not self.dirty:
            return True
        choice = QMessageBox.question(
            self, "聚合管道未保存",
            "聚合管道有未保存的更改。是否保存？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if choice == QMessageBox.StandardButton.Save:
            self.request_save()
            return False
        return choice == QMessageBox.StandardButton.Discard

    def draft(self):
        self._commit_stage_editor()
        if self._invalid:
            raise DatabaseError(self._invalid)
        validate_pipeline(self.definition)
        return deepcopy(self.definition)

    def _edited(self):
        self.dirty = True
        self.dirtyChanged.emit(True)
        self.titleChanged.emit(self.title)
        self._render_script()
        self._update_controls()

    def _render_all(self):
        self._render_sources()
        self._render_graph()
        self._render_stage_editor()
        self._render_script()
        self._update_controls()

    def _render_sources(self):
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentText(self.definition.source)
        self.source_combo.blockSignals(False)

    def _render_graph(self):
        while self.graph_row.count():
            item = self.graph_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        source = QLabel("源\n" + (self.definition.source or "选择集合"))
        source.setAlignment(Qt.AlignmentFlag.AlignCenter)
        source.setObjectName("mongoPipelineSourceCard")
        source.setFixedSize(105, 105)
        source.setStyleSheet("QLabel { border: 2px solid #36b879; border-radius: 52px; padding: 12px; }")
        self.graph_row.addWidget(source)
        for index, stage in enumerate(self.definition.stages):
            self.graph_row.addWidget(QLabel("→"))
            card = QPushButton(f"阶段 {index + 1}\n{stage.operator}")
            card.setObjectName(f"mongoPipelineStageCard{index}")
            card.setCheckable(True)
            card.setChecked(index == self.definition.selected_stage)
            card.setFixedSize(115, 105)
            card.clicked.connect(lambda checked=False, value=index: self.select_stage(value))
            self.graph_row.addWidget(card)
            plus = QPushButton("+")
            plus.setObjectName(f"mongoPipelineAddAfter{index}")
            plus.setToolTip("添加下一阶段")
            plus.setFixedSize(28, 28)
            plus.clicked.connect(lambda checked=False, value=index: self.add_stage_after(value))
            self.graph_row.addWidget(plus)
        self.graph_row.addWidget(QLabel("→"))
        output = QLabel("输出")
        output.setAlignment(Qt.AlignmentFlag.AlignCenter)
        output.setObjectName("mongoPipelineOutputCard")
        output.setFixedSize(105, 105)
        output.setStyleSheet("QLabel { border: 2px solid #36b879; border-radius: 52px; padding: 12px; }")
        self.graph_row.addWidget(output)
        self.graph_row.addStretch()

    def _render_stage_editor(self):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            self.stage_editor.setPlainText("")
            return
        stage = self.definition.stages[index]
        self.operator_combo.blockSignals(True)
        self.operator_combo.setCurrentText(stage.operator)
        self.operator_combo.blockSignals(False)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(stage.enabled)
        self.enabled_check.blockSignals(False)
        self.stage_editor.blockSignals(True)
        self.stage_editor.setPlainText(canonical_dumps(stage.body))
        self.stage_editor.blockSignals(False)

    def _render_script(self):
        try:
            text = pipeline_script(self.definition)
        except DatabaseError as error:
            text = str(error)
        self.script_editor.setPlainText(text)
        self.ddl_editor.setPlainText(text)

    def _tab_changed(self, index):
        if self.tabs.widget(index) is self.script_tab:
            self._render_script()

    def select_stage(self, index):
        self._commit_stage_editor()
        self.definition.selected_stage = index
        self._render_graph()
        self._render_stage_editor()

    def _commit_stage_editor(self):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            return
        try:
            body = canonical_loads(self.stage_editor.toPlainText())
            canonical_dumps(body)
        except Exception as error:
            self._invalid = "Stage body is not valid Canonical Extended JSON: " + str(error)
            return
        self._invalid = None
        self.definition.stages[index].body = body

    def _operator_changed(self, value):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            return
        if self.definition.stages[index].operator == value:
            return
        self.definition.stages[index].operator = value
        self._render_graph()
        self._edited()

    def _body_changed(self):
        self._commit_stage_editor()
        if self._invalid:
            self.messages.setPlainText(self._invalid)
            return
        self._edited()

    def _enabled_changed(self, checked):
        index = self.definition.selected_stage
        if index is None:
            return
        self.definition.stages[index].enabled = bool(checked)
        self._render_graph()
        self._edited()

    def _source_changed(self, value):
        value = str(value).strip()
        if self.definition.source == value:
            return
        self.definition.source = value
        self._render_graph()
        self._edited()

    def _sample_changed(self, *_):
        mode = self.sample_mode.currentData()
        size = self.sample_size.value()
        if self.definition.sample_mode == mode and self.definition.sample_size == size:
            return
        self.definition.sample_mode, self.definition.sample_size = mode, size
        self._edited()

    def add_stage(self):
        self.add_stage_after(self.definition.selected_stage if self.definition.selected_stage is not None else len(self.definition.stages) - 1)

    def add_stage_after(self, index):
        self._commit_stage_editor()
        insert = min(len(self.definition.stages), max(0, index + 1))
        self.definition.stages.insert(insert, PipelineStage())
        self.definition.selected_stage = insert
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def delete_stage(self):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            return
        if QMessageBox.question(self, "删除阶段", f"确定删除阶段 {index + 1}？") != QMessageBox.StandardButton.Yes:
            return
        self.definition.stages.pop(index)
        if not self.definition.stages:
            self.definition.stages.append(PipelineStage())
        self.definition.selected_stage = min(index, len(self.definition.stages) - 1)
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def move_stage(self, offset):
        index = self.definition.selected_stage
        if index is None:
            return
        target = index + offset
        if not 0 <= target < len(self.definition.stages):
            return
        self._commit_stage_editor()
        self.definition.stages[index], self.definition.stages[target] = (
            self.definition.stages[target], self.definition.stages[index])
        self.definition.selected_stage = target
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def show_rows(self, table, rows, title=None):
        table.setRowCount(0)
        parsed = []
        keys = []
        for row in rows:
            try:
                value = canonical_loads(row)
            except Exception:
                value = {"$raw": row}
            if isinstance(value, dict):
                for key in value:
                    if key not in keys:
                        keys.append(str(key))
            parsed.append(value)
        if not keys:
            keys = ["Canonical Extended JSON"]
        table.setColumnCount(len(keys))
        table.setHorizontalHeaderLabels(keys)
        table.setRowCount(len(parsed))
        for row_index, value in enumerate(parsed):
            if isinstance(value, dict) and value and keys != ["Canonical Extended JSON"]:
                for column, key in enumerate(keys):
                    item = value.get(key, "")
                    if isinstance(item, dict):
                        shown = canonical_dumps(item)
                    elif isinstance(item, list):
                        shown = canonical_dumps({"value": item})
                    else:
                        shown = str(item)
                    table.setItem(row_index, column, QTableWidgetItem(shown))
            else:
                table.setItem(row_index, 0, QTableWidgetItem(rows[row_index]))
        if title:
            table.setToolTip(title)

    def show_preview(self, preview):
        self._preview = preview
        index = self.definition.selected_stage
        enabled = [i for i, stage in enumerate(self.definition.stages) if stage.enabled]
        try:
            position = enabled.index(index)
        except ValueError:
            position = len(preview.stages) - 1
        if position < 0:
            self.show_rows(self.input_panel.table, preview.source_rows)
            self.show_rows(self.output_panel.table, preview.source_rows)
        else:
            rows = preview.stages[position]["rows"]
            before = preview.source_rows if position == 0 else preview.stages[position - 1]["rows"]
            self.show_rows(self.input_panel.table, before)
            self.show_rows(self.output_panel.table, rows)
        self.show_rows(self.results, preview.final_rows)
        self.messages.setPlainText(
            f"源预览 {len(preview.source_rows)} 行；阶段预览 {len(preview.stages)} 个；"
            f"最终 {len(preview.final_rows)} 行" + ("；结果已截断" if preview.truncated else ""))

    def show_execution(self, rows, truncated=False):
        self.last_rows = list(rows)
        self.show_rows(self.results, rows)
        self.tabs.setCurrentWidget(self.results)
        self.messages.setPlainText(f"运行完成：{len(rows)} 行" + ("；结果已截断" if truncated else ""))
        self._update_controls()

    def show_explain(self, text):
        self.messages.setPlainText(text)
        self.tabs.setCurrentWidget(self.messages)

    def request_preview_all(self):
        try:
            self.previewRequested.emit(self.draft())
        except DatabaseError as error:
            self.show_error(error)

    def request_run(self):
        try:
            self.runRequested.emit(self.draft())
        except DatabaseError as error:
            self.show_error(error)

    def request_explain(self):
        try:
            self.explainRequested.emit(self.draft())
        except DatabaseError as error:
            self.show_error(error)

    def request_save(self):
        try:
            definition = self.draft()
        except DatabaseError as error:
            self.show_error(error)
            return
        if self.suppress_save_preview and definition.name:
            self.saveRequested.emit(definition)
            return
        dialog = MongoPipelineSaveDialog(definition, self)
        dialog._definition = definition
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.suppress_save_preview = dialog.suppress_check.isChecked()
            self.saveRequested.emit(dialog.definition())

    def mark_saved(self, definition, pipeline_id):
        self.load_definition(definition, pipeline_id=pipeline_id)

    def show_error(self, error):
        self.messages.setPlainText(str(error))
        self.tabs.setCurrentWidget(self.messages)

    def set_running(self, value):
        self.running = bool(value)
        self._update_controls()

    def _update_controls(self):
        self.run_action.setEnabled(not self.running and not self.loading)
        self.export_action.setEnabled(bool(self.last_rows) and not self.running)
        self.preview_all_action.setEnabled(not self.running and not self.loading)
        self.explain_action.setEnabled(not self.running and not self.loading)
        self.save_action.setEnabled(not self.loading and (self.dirty or not self.pipeline_id))
        self.delete_stage_button.setEnabled(
            not self.running and len(self.definition.stages) > 1)
        self.add_stage_button.setEnabled(not self.running)
        self.stage_preview_button.setEnabled(not self.running and not self.loading)
        self.up_button.setEnabled(not self.running and bool(self.definition.selected_stage))
        self.down_button.setEnabled(
            not self.running and self.definition.selected_stage is not None
            and self.definition.selected_stage + 1 < len(self.definition.stages))
        self.titleChanged.emit(self.title)
