"""Native preview-first data comparison interface."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QPlainTextEdit, QAbstractItemView,
)

from ..compare import apply_plan
from ..transfer import json_value


class CompareTargetDialog(QDialog):
    def __init__(self, window, source):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Compare table data")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        heading = QLabel("Choose the target table")
        heading.setStyleSheet("font-size:20px; font-weight:600; padding:8px 0;")
        layout.addWidget(heading)
        intro = QLabel(f"Source: {window.configs[source.connection_id].name} / {source.schema}.{source.table}")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.connection = QComboBox()
        for connection_id in window.sessions:
            self.connection.addItem(window.configs[connection_id].name, connection_id)
        form.addRow("Target connection", self.connection)
        self.schema = QComboBox()
        self.schema.setEditable(True)
        form.addRow("Target schema / database", self.schema)
        self.table = QLineEdit(source.table)
        form.addRow("Target table", self.table)
        layout.addLayout(form)
        self.connection.currentIndexChanged.connect(self.update_schemas)
        self.update_schemas()
        note = QLabel("Preview reads up to 10,000 rows per table and requires matching column names and primary keys. Only connected profiles appear here. No data is written during comparison.")
        note.setWordWrap(True)
        note.setObjectName("pageStatus")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Compare")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_schemas(self):
        self.schema.clear()
        root = self.window.connection_item(self.connection.currentData())
        if root:
            for i in range(root.childCount()):
                info = root.child(i).data(0, Qt.ItemDataRole.UserRole)
                if info and info["type"] == "schema":
                    self.schema.addItem(info["schema"])
        config = self.window.configs.get(self.connection.currentData())
        if config:
            preferred = "public" if config.driver == "postgresql" else ("main" if config.driver == "sqlite" else config.database)
            found = self.schema.findText(preferred)
            self.schema.setCurrentIndex(max(0, found))

    def accept(self):
        if not self.schema.currentText().strip() or not self.table.text().strip():
            QMessageBox.warning(self, "Target table", "Choose a target schema and table.")
            return
        super().accept()

    def target(self):
        return self.connection.currentData(), self.schema.currentText().strip(), self.table.text().strip()


class ComparisonPage(QWidget):
    def __init__(self, window, plan):
        super().__init__()
        self.window, self.plan = window, plan
        self.connection_id = plan.target_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 12)
        heading = QLabel("Data comparison")
        heading.setStyleSheet("font-size:23px; font-weight:600;")
        layout.addWidget(heading)
        direction = QLabel(f"{plan.source}\n↓\n{plan.target}")
        direction.setWordWrap(True)
        layout.addWidget(direction)
        summary = QLabel(f"{len(plan.inserts):,} inserts     {len(plan.updates):,} updates     {len(plan.deletes):,} target-only rows     {plan.unchanged:,} unchanged")
        summary.setStyleSheet("color:#3675db; font-size:14px; font-weight:600; padding:12px 0;")
        layout.addWidget(summary)
        split = QSplitter(Qt.Orientation.Vertical)
        self.grid = QTableWidget(0, 3)
        self.grid.setHorizontalHeaderLabels(["Action", "Primary key", "Details"])
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid.setAlternatingRowColors(True)
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.records = []
        for row in plan.inserts:
            self.records.append(("INSERT", row, {"insert": row}))
        for original, changes in plan.updates:
            self.records.append(("UPDATE", original, {"original": original, "changes": changes}))
        for row in plan.deletes:
            self.records.append(("DELETE (optional)", row, {"target_only": row}))
        self.grid.setRowCount(len(self.records))
        for i, (action, row, detail) in enumerate(self.records):
            key = ", ".join(f"{k}={row[k]}" for k in plan.primary_keys)
            text = json.dumps(detail, ensure_ascii=False, default=json_value)
            for j, value in enumerate([action, key, text]):
                self.grid.setItem(i, j, QTableWidgetItem(value))
        self.grid.setColumnWidth(0, 150)
        self.grid.setColumnWidth(1, 200)
        split.addWidget(self.grid)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split.addWidget(self.detail)
        split.setSizes([400, 180])
        self.grid.currentCellChanged.connect(self.show_detail)
        layout.addWidget(split)
        controls = QHBoxLayout()
        self.deletes = QCheckBox("Also delete target-only rows")
        controls.addWidget(self.deletes)
        controls.addStretch()
        self.apply_button = QPushButton("Apply to target…")
        self.apply_button.setObjectName("primary")
        config = window.configs[plan.target_id]
        def update_enabled():
            selected = bool(plan.inserts or plan.updates or (plan.deletes and self.deletes.isChecked()))
            self.apply_button.setEnabled(not plan.applied and not config.read_only and selected)
        self.deletes.toggled.connect(update_enabled)
        update_enabled()
        self.apply_button.clicked.connect(self.apply)
        controls.addWidget(self.apply_button)
        layout.addLayout(controls)
        self.note = QLabel("Preview captured " + plan.created_at + ". Changed and deleted rows are checked against their original values. All writes run in one transaction; constraint or conflict errors roll it back.")
        self.note.setWordWrap(True)
        self.note.setObjectName("pageStatus")
        layout.addWidget(self.note)
        if self.records:
            self.grid.setCurrentCell(0, 0)

    def show_detail(self, row, *_):
        if 0 <= row < len(self.records):
            self.detail.setPlainText(json.dumps(self.records[row][2], ensure_ascii=False, indent=2, default=json_value))

    def apply(self):
        if self.window.tasks:
            return
        session = self.window.sessions.get(self.plan.target_id)
        if session is None:
            QMessageBox.information(self, "Target disconnected", "Reconnect the target profile before applying this preview.")
            return
        deleting = self.deletes.isChecked()
        count = len(self.plan.inserts) + len(self.plan.updates) + (len(self.plan.deletes) if deleting else 0)
        if not count:
            return
        message = f"Apply {count:,} row change(s) to:\n{self.plan.target}\n\n{len(self.plan.inserts):,} inserts, {len(self.plan.updates):,} updates, {len(self.plan.deletes) if deleting else 0:,} deletes."
        if QMessageBox.question(self, "Apply data changes", message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.apply_button.setEnabled(False)
        self.deletes.setEnabled(False)
        def done(result):
            self.note.setText(f"✓ Applied successfully: {result['inserted']} inserted, {result['updated']} updated, {result['deleted']} deleted. Compare again for a fresh preview.")
        def failed(exc):
            self.apply_button.setEnabled(True)
            self.deletes.setEnabled(True)
            self.window.error(exc)
        self.window.submit(lambda: apply_plan(session, self.plan, deleting), done, failed, "Applying data comparison")
