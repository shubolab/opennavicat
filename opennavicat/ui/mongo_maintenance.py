"""Native confirmation and bounded result dialogs for MongoDB maintenance."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from opennavicat.mongodb_session import canonical_dumps

from .editor import SqlEditor


class MongoMaintenanceConfirmDialog(QDialog):
    def __init__(self, title, description, command, warning, button_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(700, 430)
        layout = QVBoxLayout(self)
        label = QLabel(description); label.setWordWrap(True); layout.addWidget(label)
        warning_label = QLabel(warning); warning_label.setWordWrap(True); layout.addWidget(warning_label)
        self.preview = SqlEditor(); self.preview.set_language('text'); self.preview.setReadOnly(True)
        self.preview.setPlainText(command); layout.addWidget(self.preview, 1)
        self.ack = QCheckBox('我了解该维护操作可能耗时、阻塞写入或重建索引；服务器回执必须保留')
        layout.addWidget(self.ack)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText(button_text); self.confirm.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.ack.toggled.connect(self.confirm.setEnabled)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if self.ack.isChecked(): super().accept()


class MongoMaintenanceResultDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        title = '完整集合验证' if result.operation == 'validate-full' else '集合验证' if result.operation == 'validate' else '重建集合索引'
        self.setWindowTitle(title + '结果')
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        values = [f'操作：{title}', f'耗时：{result.duration_ms:.3f} ms']
        if result.document_count is not None: values.append(f'验证文档数：{result.document_count}')
        if result.index_count is not None: values.append(f'索引数：{result.index_count}')
        values.append('集合 UUID：' + str(result.source_uuid))
        summary = QLabel('\n'.join(values)); summary.setWordWrap(True); layout.addWidget(summary)
        self.report = SqlEditor(); self.report.set_language('text'); self.report.setReadOnly(True)
        self.report.setPlainText(canonical_dumps(result.report)); layout.addWidget(self.report, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(self.reject); buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
