"""Native connection, table definition and record editing dialogs."""

from __future__ import annotations

import json
import base64
import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QRect, QThreadPool, QTimer, QSize, Signal, QRegularExpression
from PySide6.QtGui import QFont, QFontMetricsF, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QStyle, QStyleOptionFrame, QStyleOptionSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from opennavicat.database import ConnectionConfig
from .connection_test_banner import ConnectionTestBanner
from .icons import tool_icon, toggle_icon
from .sqlite_connection_databases import SQLiteConnectionDatabases


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #738096; font-size: 11px;")
    return label


class _PortSpinBox(QSpinBox):
    """Fit the complete port in the *editor*, including native stepper chrome.

    Some native styles' CT_SpinBox size hints do not reserve the space that
    their SC_SpinBoxEditField subsequently takes away at large font sizes.
    Invert the latter geometry and the line edit's actual content rectangle.
    No fixed pixel width or cached font measurement survives a style change.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(1, 65535)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.lineEdit().installEventFilter(self)

    def sizeHint(self):
        hint = super().sizeHint()
        line = self.lineEdit()
        metrics = QFontMetricsF(line.font())
        # Port digits can have unequal advances in user-selected fonts. Cover
        # every five-digit port, not only today's value or the string 65535.
        digit_width = max(metrics.horizontalAdvance(str(n)) for n in range(10))
        text_width = math.ceil(digit_width * len(str(self.maximum())))
        # Leave font-scaled editing space at both ends as well as the native
        # cursor. QLineEdit has internal horizontal scrolling padding beyond
        # SE_LineEditContents; its minimumSizeHint can itself underestimate the
        # native Windows 11 + stylesheet frame at fractional DPI.
        text_width += math.ceil(metrics.horizontalAdvance("  "))
        cursor = max(1, line.style().pixelMetric(QStyle.PixelMetric.PM_TextCursorWidth, None, line))
        text_width += cursor
        hint.setHeight(max(hint.height(), line.minimumSizeHint().height()))
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        option.subControls = QStyle.SubControl.SC_SpinBoxEditField
        frame = QStyleOptionFrame()
        line.initStyleOption(frame)
        margins = line.textMargins()
        for _ in range(8):
            option.rect = QRect(0, 0, hint.width(), hint.height())
            edit = self.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox, option,
                QStyle.SubControl.SC_SpinBoxEditField, self,
            )
            frame.rect = QRect(0, 0, edit.width(), edit.height())
            content = line.style().subElementRect(QStyle.SubElement.SE_LineEditContents, frame, line)
            available = content.width() - margins.left() - margins.right()
            deficit = text_width - available
            if deficit <= 0:
                break
            hint.setWidth(hint.width() + deficit)
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()

    def eventFilter(self, watched, event):
        if event.type() in (
            QEvent.Type.FontChange, QEvent.Type.StyleChange,
            QEvent.Type.ApplicationFontChange, QEvent.Type.Polish,
            QEvent.Type.ContentsRectChange, QEvent.Type.DevicePixelRatioChange,
        ):
            self.updateGeometry()
        return super().eventFilter(watched, event)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.ScreenChangeInternal, QEvent.Type.DevicePixelRatioChange):
            self.updateGeometry()
        return result


def _pin_fingerprint(pin: str) -> str:
    try:
        payload = base64.b64decode(pin.split()[1], validate=True)
        digest = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii").rstrip("=")
        return "SHA256:" + digest
    except (ValueError, IndexError):
        return "无法解析已保存的主机密钥"


def confirm_ssh_host_key(parent, error) -> bool:
    """Ask about an unknown SSH key on the GUI thread; never accept a mismatch."""
    endpoint = f"{error.hostname}:{error.port}"
    if error.expected:
        QMessageBox.critical(parent, "SSH 主机密钥不匹配",
            f"{endpoint} 的主机密钥与已保存的密钥不一致，连接已拒绝。\n\n"
            f"已保存的指纹：{_pin_fingerprint(error.expected)}\n"
            f"收到的密钥类型：{error.key_type}\n收到的指纹：{error.fingerprint}")
        return False
    message = (
        f"尚未信任 SSH 主机 {endpoint} 的主机密钥。\n\n"
        f"密钥类型：{error.key_type}\nSHA256 指纹：{error.fingerprint}\n\n"
        "请核对服务器提供的指纹。是否信任此密钥并继续连接？\n"
        "确认后，此连接配置将固定使用该主机密钥。"
    )
    return QMessageBox.question(parent, "确认 SSH 主机密钥", message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


class _ConnectionDiagram(QWidget):
    """Original neutral artwork painted with the current platform palette."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.driver = "SQLite"
        self.tunnel_enabled = False
        self.ssl_enabled = False
        # Separate from Oracle's dedicated connection_succeeded overlay.
        self.sqlite_connection_succeeded = False
        self.setFixedHeight(140)
        self.setObjectName("connectionDiagram")
        self.setAccessibleName("OpenNavicat 与数据库连接示意图")

    def paintEvent(self, event):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.setPen(QPen(palette.mid().color(), 1.5))
        painter.setBrush(palette.base())
        left = self.width() * (0.15 if self.tunnel_enabled else 0.24)
        right = self.width() * (0.85 if self.tunnel_enabled else 0.76)
        y = 35
        # Original workbench artwork sits on the left, database on the right.
        painter.drawRoundedRect(QRectF(left - 29, y + 3, 58, 39), 3, 3)
        painter.drawLine(round(left), y + 43, round(left), y + 51)
        painter.drawLine(round(left - 17), y + 52, round(left + 17), y + 52)
        if self.tunnel_enabled:
            center = self.width() * 0.5
            painter.drawLine(round(left + 40), y + 28, round(center - 33), y + 28)
            painter.drawLine(round(center + 33), y + 28, round(right - 40), y + 28)
            painter.drawRoundedRect(QRectF(center - 26, y + 1, 52, 51), 2, 2)
            for offset in (12, 26, 40):
                painter.drawLine(round(center - 18), y + offset, round(center + 18), y + offset)
        else:
            painter.save()
            if self.sqlite_connection_succeeded and self.driver == "SQLite":
                painter.setPen(QPen(QColor('#6bc859'), 1.5))
            painter.drawLine(round(left + 40), y + 28, round(right - 40), y + 28)
            painter.restore()
        body = QPainterPath()
        body.moveTo(right - 26, y + 8)
        body.lineTo(right - 26, y + 42)
        body.cubicTo(right - 26, y + 56, right + 26, y + 56, right + 26, y + 42)
        body.lineTo(right + 26, y + 8)
        painter.drawPath(body)
        painter.drawEllipse(QRectF(right - 26, y, 52, 17))
        painter.drawArc(QRectF(right - 26, y + 18, 52, 17), 180 * 16, 180 * 16)
        if self.ssl_enabled:
            lock_x = self.width() * (0.675 if self.tunnel_enabled else 0.5)
            painter.drawArc(QRectF(lock_x - 4, y + 6, 8, 10), 0, 180 * 16)
            painter.drawRoundedRect(QRectF(lock_x - 6, y + 11, 12, 10), 1, 1)
        painter.setPen(palette.text().color())
        painter.drawText(QRectF(left - 75, 104, 150, 24), Qt.AlignmentFlag.AlignCenter, "OpenNavicat")
        painter.drawText(QRectF(right - 75, 104, 150, 24), Qt.AlignmentFlag.AlignCenter, "数据库")
        if self.tunnel_enabled:
            painter.drawText(QRectF(center - 65, 104, 130, 24), Qt.AlignmentFlag.AlignCenter, "SSH Server")


@dataclass(frozen=True)
class _ConnectionProbeResult:
    message: str
    server_version: str = ''


class ConnectionDialog(QDialog):
    """Native connection profile editor with an explicit asynchronous test action.

    Profile collection never opens a connection. Only clicking Test Connection
    starts a short-lived session; that session is always closed by its worker.
    """

    test_finished = Signal(bool, str)

    def __init__(self, parent=None, config: ConnectionConfig | None = None):
        super().__init__(parent)
        self._original = config
        self._config = None
        self._testing = False
        self._tested_driver = None
        self._test_server_version = ''
        self._test_task = None
        self._test_pool = QThreadPool(self)
        self._test_pool.setMaxThreadCount(1)
        self._reference_frame_size = QSize(749, 668)
        self.setObjectName("connectionDialog")
        self.resize(749, 668)
        self.setMinimumSize(520, 520)
        font = self.font()
        font.setPixelSize(12)
        self.setFont(font)
        outer = QVBoxLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.content = QWidget()
        content = QHBoxLayout(self.content)
        content.setContentsMargins(20, 20, 20, 0)
        content.setSpacing(20)
        outer.addWidget(self.content, 1)

        self.profile_pane = QFrame()
        self.profile_pane.setObjectName("connectionProfilePane")
        self.profile_pane.setFixedWidth(200)
        rail = QVBoxLayout(self.profile_pane)
        self._profile_rail = rail
        rail.setContentsMargins(0, 0, 0, 0)
        rail.setSpacing(0)
        self.profiles = QListWidget()
        self.profiles.setObjectName("connectionProfiles")
        self.profiles.addItem("Main Profile（有效）")
        self.profiles.setCurrentRow(0)
        rail.addWidget(self.profiles, 1)
        # Drivers are selected by the main window's connection-type menu. Keep
        # the original widget API hidden for callers that select one in code.
        self.driver_combo = QComboBox(self)
        self.driver_combo.setObjectName("connectionDriver")
        for label, driver in [("SQLite", "sqlite"), ("PostgreSQL", "postgresql"),
                              ("MySQL", "mysql"), ("MariaDB", "mariadb"),
                              ("SQL Server", "sqlserver"), ("Redis", "redis"), ("MongoDB", "mongodb")]:
            self.driver_combo.addItem(label, driver)
        self.driver_combo.hide()
        self.new_profile_button = QPushButton("新建连接配置文件")
        self.new_profile_button.setIcon(tool_icon("profile_add", 16))
        self.new_profile_button.setIconSize(QSize(16, 16))
        self.new_profile_button.setObjectName("newConnectionProfile")
        self.new_profile_button.setEnabled(False)
        self.new_profile_button.setToolTip("多个连接配置文件尚未实现；当前连接使用 Main Profile。")
        rail.addWidget(self.new_profile_button)
        content.addWidget(self.profile_pane)
        self.new_profile_button.ensurePolished()
        self._profile_button_defaults = dict(
            text=self.new_profile_button.text(), tooltip=self.new_profile_button.toolTip(),
            accessible_name=self.new_profile_button.accessibleName(),
            minimum=QSize(self.new_profile_button.minimumSize()),
            maximum=QSize(self.new_profile_button.maximumSize()),
            size_policy=QSizePolicy(self.new_profile_button.sizePolicy()),
            stylesheet=self.new_profile_button.styleSheet(), flat=self.new_profile_button.isFlat(),
            auto_default=self.new_profile_button.autoDefault(), font=QFont(self.new_profile_button.font()),
            font_explicit=self.new_profile_button.testAttribute(Qt.WidgetAttribute.WA_SetFont),
        )
        self.profile_header = QWidget(self.profile_pane)
        self.profile_header.setObjectName('sqliteConnectionProfileHeader')
        header = QHBoxLayout(self.profile_header)
        header.setContentsMargins(0, 0, 0, 6)
        header.setSpacing(8)
        self.profile_header_title = QLabel('连接配置文件')
        self.profile_header_title.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.profile_header_title, 1)
        self.profile_header.hide()

        right = QWidget()
        self.settings_pane = right
        right.setObjectName("connectionSettingsPane")
        right_layout = QVBoxLayout(right)
        self._settings_layout = right_layout
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("connectionTabs")
        self.tabs.setDocumentMode(False)
        right_layout.addWidget(self.tabs, 1)
        content.addWidget(right, 1)

        self.general_tab = QWidget()
        general = QVBoxLayout(self.general_tab)
        general.setContentsMargins(10, 6, 10, 12)
        general.setSpacing(12)
        self.diagram = _ConnectionDiagram()
        general.addWidget(self.diagram)
        general.addSpacing(12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(13)
        self.name_edit = QLineEdit(config.name if config else "")
        self.name_edit.setObjectName("connectionName")
        self.name_edit.setPlaceholderText("连接名称")
        form.addRow("连接名", self.name_edit)
        self.sqlite_mode_combo = QComboBox(self)
        self.sqlite_mode_combo.addItem("现有 SQLite 3 数据库", "existing")
        self.sqlite_mode_combo.addItem("新建 SQLite 3 数据库", "create")
        self.sqlite_mode_combo.hide()
        self.sqlite_type_widget = QWidget()
        sqlite_types = QVBoxLayout(self.sqlite_type_widget)
        sqlite_types.setContentsMargins(0, 0, 0, 0)
        sqlite_types.setSpacing(4)
        self.sqlite_type_group = QButtonGroup(self)
        self.sqlite_existing_radio = QRadioButton("现有数据库文件")
        self.sqlite_create_radio = QRadioButton("新建 SQLite 3 数据库")
        self.sqlite_legacy_radio = QRadioButton("新建 SQLite 2 数据库")
        for index, radio in enumerate((self.sqlite_existing_radio, self.sqlite_create_radio, self.sqlite_legacy_radio)):
            self.sqlite_type_group.addButton(radio, index)
            sqlite_types.addWidget(radio)
        self.sqlite_existing_radio.setChecked(True)
        self.sqlite_legacy_radio.setEnabled(False)
        self.sqlite_legacy_radio.setToolTip("SQLite 2 格式的创建与连接尚未实现。")
        self.sqlite_type_group.idClicked.connect(lambda index: self.sqlite_mode_combo.setCurrentIndex(index) if index < 2 else None)
        self.sqlite_mode_combo.currentIndexChanged.connect(lambda index: self.sqlite_type_group.button(index).setChecked(True))
        self.sqlite_type_label = QLabel("类型")
        form.addRow(self.sqlite_type_label, self.sqlite_type_widget)
        self.database_edit = QLineEdit(config.database if config else "")
        self.database_edit.setObjectName("connectionDatabase")
        self.database_row = QWidget()
        database_layout = QHBoxLayout(self.database_row)
        database_layout.setContentsMargins(0, 0, 0, 0)
        database_layout.setSpacing(6)
        database_layout.addWidget(self.database_edit, 1)
        self.browse_button = QPushButton("…")
        self.browse_button.setFixedWidth(30)
        self.browse_button.setToolTip("选择数据库文件")
        database_layout.addWidget(self.browse_button)
        # Retained for callers using the original public widget attribute. The
        # SQLite type selector now exposes the create-file workflow.
        self.create_button = QPushButton("新建…", self)
        self.create_button.hide()
        self.database_label = QLabel("数据库文件")
        form.addRow(self.database_label, self.database_row)
        self.browse_button.clicked.connect(self._browse)
        self.create_button.clicked.connect(self._new_file)
        self.host_edit = QLineEdit(config.host if config else "localhost")
        self.port_spin = _PortSpinBox()
        self.user_edit = QLineEdit(config.username if config else "")
        self.password_edit = QLineEdit(config.password if config else "")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._network_rows = []
        for label, field in [("主机", self.host_edit), ("端口", self.port_spin)]:
            label_widget = QLabel(label)
            form.addRow(label_widget, field)
            self._network_rows.append((label_widget, field))
        form.addRow("用户名", self.user_edit)
        form.addRow("密码", self.password_edit)
        self.general_form = form
        self.remember_check = QCheckBox("保存密码")
        self.remember_check.setChecked(bool(config and config.password))
        self.remember_check.setToolTip("密码仅保存在本机系统钥匙串中。")
        form.addRow("", self.remember_check)
        general.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("connectionHint")
        self.note.hide()
        general.addWidget(self.note)
        general.addStretch(1)

        self.advanced_tab = QWidget()
        advanced = QFormLayout(self.advanced_tab)
        advanced.setContentsMargins(20, 28, 20, 20)
        advanced.setSpacing(16)
        self.read_only_check = QCheckBox("以只读方式打开连接")
        self.read_only_check.setChecked(bool(config and config.read_only))
        self.read_only_check.toggled.connect(self._read_only_driver_state)
        advanced.addRow(self.read_only_check)
        self.foreign_keys_check = QCheckBox("启用 SQLite 外键约束")
        self.foreign_keys_check.setChecked(True)
        self.foreign_keys_check.setEnabled(False)
        self.foreign_keys_check.setToolTip("SQLite 连接始终启用外键约束；当前版本不可关闭。")
        advanced.addRow(self.foreign_keys_check)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 31536000)
        self.timeout_spin.setValue(getattr(config, "connect_timeout", 10) if config else 10)
        advanced.addRow("连接超时（秒）", self.timeout_spin)

        self.databases_tab = QWidget()
        databases = QVBoxLayout(self.databases_tab)
        databases.setContentsMargins(20, 28, 20, 20)
        self.remote_database_edit = QLineEdit(self.database_edit.text())
        self.remote_database_edit.setPlaceholderText("数据库名称")
        self.remote_database_label = QLabel("初始数据库")
        form.insertRow(5, self.remote_database_label, self.remote_database_edit)
        self.remote_database_edit.textChanged.connect(self.database_edit.setText)
        self.database_edit.textChanged.connect(self.remote_database_edit.setText)
        self.redis_type_label = QLabel("类型")
        self.redis_mode_combo = QComboBox()
        for label, value in [("单机", "standalone"), ("集群", "cluster"), ("哨兵", "sentinel")]:
            self.redis_mode_combo.addItem(label, value)
            if value != "standalone":
                item = self.redis_mode_combo.model().item(self.redis_mode_combo.count() - 1)
                item.setEnabled(False)
                item.setToolTip("此连接类型尚未实现；当前仅支持 Redis 单机。")
        form.insertRow(1, self.redis_type_label, self.redis_mode_combo)
        self.sqlserver_auth_label = QLabel("身份验证")
        self.sqlserver_auth_combo = QComboBox()
        self.sqlserver_auth_combo.addItem("SQL Server 身份验证", "sql")
        self.sqlserver_auth_combo.addItem("Windows 集成身份验证（尚未实现）", "integrated")
        integrated = self.sqlserver_auth_combo.model().item(1)
        integrated.setEnabled(False)
        integrated.setToolTip("Windows 集成身份验证尚未实现，请使用 SQL Server 用户名和密码。")
        form.insertRow(form.rowCount() - 3, self.sqlserver_auth_label, self.sqlserver_auth_combo)
        self.mongo_type_label = QLabel("连接类型")
        self.mongo_topology_combo = QComboBox()
        for label, value in (("Standalone", "standalone"), ("Shard Cluster（尚未实现）", "sharded"), ("Replica Set（尚未实现）", "replica_set")):
            self.mongo_topology_combo.addItem(label, value)
            if value != "standalone":self.mongo_topology_combo.model().item(self.mongo_topology_combo.count()-1).setEnabled(False)
        original_mongo = config if config and config.driver == "mongodb" else None
        topology = getattr(original_mongo, "mongo_connection_type", "standalone")
        if str(topology).lower() == "standalone":topology = "standalone"
        self._restore_mongo_choice(self.mongo_topology_combo, topology)
        form.insertRow(1, self.mongo_type_label, self.mongo_topology_combo)
        self.mongo_srv_check = QCheckBox("使用 SRV 记录（尚未实现）")
        self.mongo_srv_check.setChecked(bool(getattr(original_mongo, "mongo_srv", False)))
        self.mongo_srv_check.setToolTip("SRV 发现尚未完成支持；请使用 Standalone 的单一主机与端口。")
        form.insertRow(2, "", self.mongo_srv_check)
        self.mongo_auth_label = QLabel("验证")
        self.mongo_auth_combo = QComboBox()
        for label, value in (("无", "NONE"), ("密码", "PASSWORD"), ("LDAP（尚未实现）", "PLAIN"),
                             ("Kerberos（尚未实现）", "GSSAPI"), ("X.509（尚未完成接入）", "MONGODB-X509")):
            self.mongo_auth_combo.addItem(label, value)
            if value not in {"NONE", "PASSWORD"}:self.mongo_auth_combo.model().item(self.mongo_auth_combo.count()-1).setEnabled(False)
        mechanism = str(getattr(original_mongo, "mongo_auth_mechanism", "NONE")).upper()
        password_auth = mechanism in {"SCRAM-SHA-1", "SCRAM-SHA-256"} or (mechanism == "DEFAULT" and bool(config and (config.username or config.password)))
        auth = "PASSWORD" if password_auth else "NONE" if mechanism in {"DEFAULT", "NONE"} else mechanism
        self._restore_mongo_choice(self.mongo_auth_combo, auth)
        row, _role = form.getWidgetPosition(self.user_edit)
        form.insertRow(row, self.mongo_auth_label, self.mongo_auth_combo)
        source = getattr(original_mongo, "mongo_auth_source", "admin")
        source_visible = bool(original_mongo and (original_mongo.username or source != "admin" or mechanism in {"SCRAM-SHA-1", "SCRAM-SHA-256"}))
        self.mongo_auth_source_edit = QLineEdit(source if source_visible else "")
        self.mongo_auth_source_edit.setPlaceholderText("admin")
        self.mongo_auth_source_edit.setToolTip("密码验证使用的数据库；留空时使用 admin。")
        self.mongo_auth_source_label = QLabel("验证数据库")
        form.insertRow(row+1, self.mongo_auth_source_label, self.mongo_auth_source_edit)
        self.mongo_mechanism_combo = QComboBox()
        for label,value in (("默认（SCRAM 协商）", "DEFAULT"), ("SCRAM-SHA-1", "SCRAM-SHA-1"), ("SCRAM-SHA-256", "SCRAM-SHA-256")):
            self.mongo_mechanism_combo.addItem(label, value)
        self._restore_mongo_choice(self.mongo_mechanism_combo, mechanism if password_auth else "DEFAULT")
        self.mongo_mechanism_label = QLabel("密码验证机制")
        advanced.addRow(self.mongo_mechanism_label, self.mongo_mechanism_combo)
        self.mongo_read_preference_combo = QComboBox()
        for value in ("primary", "primaryPreferred", "secondary", "secondaryPreferred", "nearest"):
            self.mongo_read_preference_combo.addItem(value, value)
            if value != "primary":self.mongo_read_preference_combo.model().item(self.mongo_read_preference_combo.count()-1).setEnabled(False)
        self._restore_mongo_choice(self.mongo_read_preference_combo, getattr(original_mongo, "mongo_read_preference", "primary"))
        self.mongo_read_preference_label = QLabel("读取偏好")
        advanced.addRow(self.mongo_read_preference_label, self.mongo_read_preference_combo)
        self.mongo_replica_set_edit = QLineEdit(getattr(original_mongo, "mongo_replica_set", ""))
        self.mongo_members_edit = QLineEdit(getattr(original_mongo, "mongo_members", ""))
        self._mongo_unsupported_rows = []
        for title, field in (("副本集（尚未实现）", self.mongo_replica_set_edit), ("成员列表（尚未实现）", self.mongo_members_edit)):
            label_widget = QLabel(title)
            field.setClearButtonEnabled(True)
            field.setToolTip("当前尚不支持此设置；已有值需明确清除后才能保存 Standalone 连接。")
            advanced.addRow(label_widget, field)
            self._mongo_unsupported_rows.append((label_widget, field))
            field.textChanged.connect(self._mongo_state_changed)
        self.mongo_auth_combo.currentIndexChanged.connect(self._mongo_state_changed)
        self.mongo_srv_check.toggled.connect(self._mongo_state_changed)
        self.mongo_remarks_tab = QWidget()
        remarks = QVBoxLayout(self.mongo_remarks_tab)
        self.mongo_remarks_edit = QPlainTextEdit()
        self.mongo_remarks_edit.setPlaceholderText("连接备注持久化尚未实现。")
        self.mongo_remarks_edit.setEnabled(False)
        remarks.addWidget(self.mongo_remarks_edit)
        self._redis_database_validator = QRegularExpressionValidator(QRegularExpression("[0-9]*"), self)
        self.sqlite_database_diagram = _ConnectionDiagram()
        self.sqlite_databases_page = SQLiteConnectionDatabases(self.sqlite_database_diagram, config)
        databases.addWidget(self.sqlite_databases_page, 1)
        self.network_database_note = QLabel("初始数据库在“常规”页中设置。可用数据库列表由连接后的服务器提供。")
        self.network_database_note.setWordWrap(True)
        databases.addWidget(self.network_database_note)
        databases.addStretch()
        self._database_note_stretch = databases.count() - 1
        self._database_note_spacing = databases.spacing()

        self.ssl_tab = QWidget()
        ssl_layout = QVBoxLayout(self.ssl_tab)
        ssl_layout.setContentsMargins(10, 6, 10, 12)
        ssl_layout.setSpacing(10)
        self.ssl_diagram = _ConnectionDiagram()
        ssl_layout.addWidget(self.ssl_diagram)
        ssl_form = QFormLayout()
        ssl_form.setSpacing(8)
        ssl_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ssl_enabled_check = QCheckBox("使用 SSL")
        self.ssl_enabled_check.setChecked(bool(config and config.ssl_mode != "disable"))
        ssl_form.addRow(self.ssl_enabled_check)
        self.ssl_combo = QComboBox()
        for mode in ("allow", "prefer", "require", "verify-ca", "verify-full"):
            self.ssl_combo.addItem(mode, mode)
        self.ssl_combo.setCurrentIndex(self.ssl_combo.findData("prefer"))
        ssl_form.addRow("SSL 模式", self.ssl_combo)
        self.ssl_verify_check = QCheckBox("使用验证")
        self.ssl_verify_check.setChecked(bool(config and any(getattr(config, name, "") for name in
            ("ssl_ca", "ssl_cert", "ssl_key", "ssl_crl", "ssl_key_password"))))
        self.ssl_verify_check.setToolTip("启用自定义证书文件。服务器证书验证策略由 SSL 模式决定。")
        ssl_form.addRow(self.ssl_verify_check)
        self._ssl_file_widgets = []
        self._ssl_file_rows = {}
        for name, attribute in [("客户端密钥", "ssl_key"), ("客户端证书", "ssl_cert"),
                                ("根证书", "ssl_ca"), ("证书撤消列表", "ssl_crl")]:
            field, edit, browse = self._file_field(getattr(config, attribute, "") if config else "", name)
            setattr(self, attribute + "_edit", edit)
            setattr(self, attribute + "_browse", browse)
            self._ssl_file_widgets.append(field)
            ssl_form.addRow(name, field)
            self._ssl_file_rows[attribute] = (ssl_form.labelForField(field), field)
        self.ssl_key_password_edit = QLineEdit(getattr(config, "ssl_key_password", "") if config else "")
        self.ssl_key_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        ssl_form.addRow("密钥通行短语", self.ssl_key_password_edit)
        self.remember_ssl_check = QCheckBox("保存密钥通行短语")
        self.ssl_remember_check = self.remember_ssl_check
        self.remember_ssl_check.setChecked(bool(config and getattr(config, "ssl_key_password", "")))
        self.remember_ssl_check.setToolTip("通行短语仅保存在本机系统钥匙串中。")
        ssl_form.addRow("", self.remember_ssl_check)
        ssl_layout.addLayout(ssl_form)
        ssl_layout.addStretch()
        self.ssl_enabled_check.toggled.connect(self._ssl_state_changed)
        self.ssl_verify_check.toggled.connect(self._ssl_state_changed)
        self.ssl_combo.currentIndexChanged.connect(self._ssl_state_changed)
        self.ssl_key_edit.textChanged.connect(self._ssl_state_changed)

        self.ssh_tab = QWidget()
        ssh = QVBoxLayout(self.ssh_tab)
        ssh.setContentsMargins(10, 6, 10, 12)
        ssh.setSpacing(10)
        self.ssh_diagram = _ConnectionDiagram()
        ssh.addWidget(self.ssh_diagram)
        self.ssh_enabled_check = QCheckBox("使用 SSH 隧道")
        self.ssh_enabled_check.setChecked(bool(config and getattr(config, "ssh_enabled", False)))
        ssh.addWidget(self.ssh_enabled_check)
        self.ssh_form = QFormLayout()
        self.ssh_form.setSpacing(8)
        self.ssh_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ssh_host_edit = QLineEdit(getattr(config, "ssh_host", "") if config else "")
        self.ssh_port_spin = _PortSpinBox()
        self.ssh_port_spin.setValue(getattr(config, "ssh_port", 22) if config else 22)
        self.ssh_user_edit = QLineEdit(getattr(config, "ssh_username", "") if config else "")
        self.ssh_auth_combo = QComboBox()
        for label, method in [("密码", "password"), ("公钥", "key"),
                              ("密码和公钥", "password+key"), ("SSH Agent", "agent")]:
            self.ssh_auth_combo.addItem(label, method)
        self.ssh_auth_combo.setCurrentIndex(max(0, self.ssh_auth_combo.findData(
            getattr(config, "ssh_auth_method", "password") if config else "password")))
        for label, field in [("主机", self.ssh_host_edit), ("端口", self.ssh_port_spin),
                             ("用户名", self.ssh_user_edit), ("验证方法", self.ssh_auth_combo)]:
            self.ssh_form.addRow(label, field)
        self.ssh_password_edit = QLineEdit(getattr(config, "ssh_password", "") if config else "")
        self.ssh_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_ssh_check = QCheckBox("保存密码")
        self.ssh_remember_check = self.remember_ssh_check
        self.remember_ssh_check.setChecked(bool(config and getattr(config, "ssh_password", "")))
        self.remember_ssh_check.setToolTip("SSH 密码仅保存在本机系统钥匙串中。")
        self._ssh_password_rows = []
        for text, field in [("密码", self.ssh_password_edit), ("", self.remember_ssh_check)]:
            label = QLabel(text)
            self.ssh_form.addRow(label, field)
            self._ssh_password_rows.append((label, field))
        self.ssh_key_field, self.ssh_key_edit, self.ssh_key_browse = self._file_field(
            getattr(config, "ssh_key_file", "") if config else "", "SSH 私钥")
        self.ssh_key_passphrase_edit = QLineEdit(getattr(config, "ssh_key_passphrase", "") if config else "")
        self.ssh_key_passphrase_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_ssh_key_check = QCheckBox("保存通行短语")
        self.ssh_key_remember_check = self.remember_ssh_key_check
        self.remember_ssh_key_check.setChecked(bool(config and getattr(config, "ssh_key_passphrase", "")))
        self.remember_ssh_key_check.setToolTip("私钥通行短语仅保存在本机系统钥匙串中。")
        self._ssh_key_rows = []
        for text, field in [("私钥", self.ssh_key_field), ("通行短语", self.ssh_key_passphrase_edit),
                            ("", self.remember_ssh_key_check)]:
            label = QLabel(text)
            self.ssh_form.addRow(label, field)
            self._ssh_key_rows.append((label, field))
        self.ssh_compression_check = QCheckBox("使用压缩")
        self.ssh_compression_check.setChecked(bool(config and getattr(config, "ssh_compression", False)))
        self.ssh_form.addRow("", self.ssh_compression_check)
        ssh.addLayout(self.ssh_form)
        self.ssh_pin_label = QLabel()
        self.ssh_pin_label.setWordWrap(True)
        ssh.addWidget(self.ssh_pin_label)
        ssh.addStretch()
        self._ssh_host_key = getattr(config, "ssh_host_key", "") if config else ""
        self._ssh_pin_endpoint = self._ssh_endpoint()
        self._test_error = None
        self._test_success_message = ""
        self.ssh_enabled_check.toggled.connect(self._ssh_state_changed)
        self.ssh_auth_combo.currentIndexChanged.connect(self._ssh_state_changed)
        self.ssh_host_edit.textChanged.connect(self._ssh_endpoint_changed)
        self.ssh_port_spin.valueChanged.connect(self._ssh_endpoint_changed)
        self.http_tab = self._unavailable_tab("使用 HTTP 通道", "HTTP 隧道尚未实现；此选项当前不可用。")

        # In the observed SQLite form the result strip and footer belong to
        # the settings column; the profile rail continues to the bottom.
        self.sqlite_test_banner = ConnectionTestBanner(right)
        self.sqlite_test_banner.status_label.setObjectName('sqliteConnectionTestStatus')
        right_layout.addWidget(self.sqlite_test_banner)
        self.test_finished.connect(self._display_test_result)

        footer = QFrame()
        self.footer = footer
        footer.setObjectName("connectionDialogFooter")
        footer.setFixedHeight(60)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 12, 20, 14)
        footer_layout.setSpacing(12)
        self.profile_toggle = QPushButton()
        self.profile_toggle.setIcon(toggle_icon("profile_pane", 16))
        self.profile_toggle.setIconSize(QSize(16, 16))
        self.profile_toggle.setStyleSheet("QPushButton { padding: 0px; }")
        self.profile_toggle.setAccessibleName("显示或隐藏连接配置文件")
        self.profile_toggle.setFixedWidth(26)
        self.profile_toggle.setCheckable(True)
        self.profile_toggle.setAutoDefault(False)
        self.profile_toggle.setToolTip("显示或隐藏连接配置文件")
        self.profile_toggle.toggled.connect(self._toggle_profiles)
        footer_layout.addWidget(self.profile_toggle)
        self.test_button = QPushButton("测试连接")
        self.test_button.setAutoDefault(False)
        self.test_button.clicked.connect(self.test_connection)
        footer_layout.addWidget(self.test_button)
        self.test_status = QLabel()
        self.test_status.setObjectName("connectionTestStatus")
        self.test_status.setTextFormat(Qt.TextFormat.PlainText)
        footer_layout.addWidget(self.test_status, 1)
        # When SQLite success moves into its strip, keep the trailing actions
        # at the far edge even though the former expanding label is hidden.
        footer_layout.addStretch(0)
        self._test_footer_gap_index = footer_layout.count()-1
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setDefault(True)
        self.buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer_layout.addWidget(self.buttons)
        outer.addWidget(footer)
        self.driver_combo.currentIndexChanged.connect(self._driver_changed)
        if config:
            self.driver_combo.setCurrentIndex(max(0, self.driver_combo.findData(config.driver)))
            self.ssl_combo.setCurrentIndex(max(0, self.ssl_combo.findData(config.ssl_mode)))
            if config.driver == "sqlite":
                mode = "create" if config.sqlite_open_mode == "create" else "existing"
                self.sqlite_mode_combo.setCurrentIndex(self.sqlite_mode_combo.findData(mode))
        self._driver_changed()
        self._ssl_state_changed()
        self._ssh_state_changed()
        if config and config.port:
            self.port_spin.setValue(config.port)
        self.database_edit.textChanged.connect(self._sqlite_profile_changed)
        self.sqlite_mode_combo.currentIndexChanged.connect(self._sqlite_profile_changed)
        self.read_only_check.toggled.connect(self._sqlite_profile_changed)
        self.timeout_spin.valueChanged.connect(self._sqlite_profile_changed)
        self.sqlite_databases_page.changed.connect(self._sqlite_profile_changed)

    @staticmethod
    def _restore_mongo_choice(combo, value):
        index = combo.findData(value)
        if index < 0:
            combo.addItem(str(value) + "（不支持）", value)
            index = combo.count()-1
            combo.model().item(index).setEnabled(False)
        combo.setCurrentIndex(index)

    def _mongo_state_changed(self, *_):
        if not hasattr(self, "mongo_auth_combo"):
            return
        active = self.driver_combo.currentData() == "mongodb"
        password = active and self.mongo_auth_combo.currentData() == "PASSWORD"
        self.general_form.setRowVisible(self.mongo_type_label, active)
        self.general_form.setRowVisible(self.mongo_srv_check, active)
        self.general_form.setRowVisible(self.mongo_auth_label, active)
        self.general_form.setRowVisible(self.mongo_auth_source_label, password)
        self.mongo_srv_check.setEnabled(self.mongo_srv_check.isChecked())
        for field in (self.user_edit, self.password_edit, self.remember_check):
            self.general_form.setRowVisible(field, not active or password)
        for label, field in ((self.mongo_mechanism_label,self.mongo_mechanism_combo),
                             (self.mongo_read_preference_label,self.mongo_read_preference_combo), *self._mongo_unsupported_rows):
            label.setVisible(active); field.setVisible(active)
        self.mongo_mechanism_combo.setEnabled(password)
        for _label, field in self._mongo_unsupported_rows:
            field.setEnabled(bool(field.text()))
        if self.isVisible():self._fit_reference_frame()

    @staticmethod
    def _unavailable_tab(label, message):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 28, 20, 20)
        checkbox = QCheckBox(label)
        checkbox.setEnabled(False)
        checkbox.setToolTip(message)
        layout.addWidget(checkbox)
        note = QLabel(message)
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return widget

    @property
    def remember_password(self) -> bool:
        return (self.remember_check.isChecked() and self.driver_combo.currentData() != "sqlite"
                and (self.driver_combo.currentData() != "mongodb" or self.mongo_auth_combo.currentData() == "PASSWORD"))

    @property
    def remember_ssh_password(self) -> bool:
        return (self.driver_combo.currentData() != "sqlite" and self.ssh_enabled_check.isChecked()
                and self.ssh_auth_combo.currentData() in ("password", "password+key")
                and self.remember_ssh_check.isChecked())

    @property
    def remember_ssh_key_passphrase(self) -> bool:
        return (self.driver_combo.currentData() != "sqlite" and self.ssh_enabled_check.isChecked()
                and self.ssh_auth_combo.currentData() in ("key", "password+key")
                and self.remember_ssh_key_check.isChecked())

    @property
    def remember_ssl_password(self) -> bool:
        return (self.driver_combo.currentData() not in {"sqlite", "sqlserver"} and self.ssl_enabled_check.isChecked()
                and self.ssl_verify_check.isChecked() and self.remember_ssl_check.isChecked())

    def _file_field(self, value, title):
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        edit, browse = QLineEdit(value), QPushButton("…")
        browse.setFixedWidth(30)
        browse.setAutoDefault(False)
        browse.setToolTip(f"选择{title}")
        browse.clicked.connect(lambda: self._browse_file(edit, title))
        row.addWidget(edit, 1)
        row.addWidget(browse)
        return field, edit, browse

    def _browse_file(self, edit, title):
        path, _ = QFileDialog.getOpenFileName(self, f"选择{title}", edit.text(), "所有文件 (*)")
        if path:
            edit.setText(path)

    def _ssl_state_changed(self, *_):
        enabled = self.ssl_enabled_check.isChecked()
        sqlserver = self.driver_combo.currentData() == "sqlserver"
        self.ssl_combo.setEnabled(enabled)
        self.ssl_verify_check.setEnabled(enabled)
        self.ssl_verify_check.setText("使用自定义根证书" if sqlserver else "使用验证")
        self.ssl_verify_check.setToolTip(
            "SQL Server 的所有 TLS 模式均验证证书链。未选择自定义根证书时使用 certifi 信任库；自签证书必须提供根证书。"
            if sqlserver else "启用自定义证书文件。服务器证书验证策略由 SSL 模式决定。")
        custom_files = enabled and self.ssl_verify_check.isChecked()
        for attribute, (label, field) in self._ssl_file_rows.items():
            supported = not sqlserver or attribute == "ssl_ca"
            field.setEnabled(custom_files and supported)
            label.setEnabled(custom_files and supported)
            unsupported_tip = "SQL Server 当前不支持客户端证书、客户端密钥或证书撤消列表。" if not supported else ""
            field.setToolTip(unsupported_tip)
            label.setToolTip(unsupported_tip)
        key_password_supported = not sqlserver and custom_files and bool(self.ssl_key_edit.text().strip())
        self.ssl_key_password_edit.setEnabled(key_password_supported)
        self.remember_ssl_check.setEnabled(key_password_supported)
        self.ssl_key_password_edit.setToolTip("SQL Server 当前不支持客户端密钥及其通行短语。" if sqlserver else "")
        self.ssl_diagram.ssl_enabled = enabled
        self.ssl_diagram.update()
        if hasattr(self, "ssh_diagram"):
            self.ssh_diagram.ssl_enabled = enabled
            self.ssh_diagram.update()

    def _ssh_endpoint(self):
        return self.ssh_host_edit.text().strip().casefold(), self.ssh_port_spin.value()

    def _ssh_endpoint_changed(self, *_):
        endpoint = self._ssh_endpoint()
        if endpoint != self._ssh_pin_endpoint:
            self._ssh_host_key = ""
            self._ssh_pin_endpoint = endpoint
        self._update_ssh_pin_label()

    def _update_ssh_pin_label(self):
        self.ssh_pin_label.setText("主机密钥：" + _pin_fingerprint(self._ssh_host_key)
                                   if self._ssh_host_key else "主机密钥将在首次连接时确认。")

    def _ssh_state_changed(self, *_):
        enabled = self.ssh_enabled_check.isChecked()
        method = self.ssh_auth_combo.currentData()
        for field in (self.ssh_host_edit, self.ssh_port_spin, self.ssh_user_edit,
                      self.ssh_auth_combo, self.ssh_compression_check):
            field.setEnabled(enabled)
        for label, field in self._ssh_password_rows:
            visible = method in ("password", "password+key")
            label.setVisible(visible)
            field.setVisible(visible)
            field.setEnabled(enabled)
        for label, field in self._ssh_key_rows:
            visible = method in ("key", "password+key")
            label.setVisible(visible)
            field.setVisible(visible)
            field.setEnabled(enabled)
        self.ssh_diagram.tunnel_enabled = enabled
        self.ssh_diagram.update()
        self._update_ssh_pin_label()

    def _driver_changed(self, *_):
        driver = self.driver_combo.currentData()
        previous_driver = getattr(self, "_active_driver", None)
        local = driver == "sqlite"
        self._set_sqlite_profile_header(local)
        # macOS already places Reject before Accept. Mirroring that native
        # layout moves Save to the left; Fusion's WinLayout needs the mirror.
        mac_buttons = self.buttons.style().styleHint(QStyle.StyleHint.SH_DialogButtonLayout) == QDialogButtonBox.ButtonLayout.MacLayout.value
        self.buttons.setLayoutDirection(Qt.LayoutDirection.LeftToRight if local and mac_buttons
                                        else Qt.LayoutDirection.RightToLeft)
        if driver != previous_driver:
            self._reset_sqlite_test_diagram()
            self.sqlite_test_banner.clear_result()
            self.test_status.clear()
            self.test_status.setToolTip('')
            self.test_status.show()
            self.footer.layout().setStretch(self._test_footer_gap_index, 0)
        self.footer.setFixedHeight(44 if local else 60)
        self.footer.layout().setContentsMargins(18, 7 if local else 12, 20, 7 if local else 14)
        if local != (self.footer.parentWidget() is self.settings_pane):
            # Preserve the existing footer hierarchy for unobserved generic
            # drivers while matching SQLite's observed right-column footer.
            source = self._outer_layout if local else self._settings_layout
            target = self._settings_layout if local else self._outer_layout
            source.removeWidget(self.footer)
            target.addWidget(self.footer)
            self.footer.show()
        label = self.driver_combo.currentText()
        self.setWindowTitle(f"{label} - {'编辑连接' if self._original else '新建连接'}")
        self.diagram.driver = label
        self.diagram.update()
        if driver != previous_driver and (self._original is None or self._original.driver != driver):
            if driver == "sqlserver":
                self.ssl_enabled_check.setChecked(True)
            elif driver in {"redis", "mongodb"}:
                self.ssl_enabled_check.setChecked(False)
        self._set_ssl_modes(driver)
        self.tabs.clear()
        for widget, name in [(self.general_tab, "常规"), (self.advanced_tab, "高级"),
                             (self.databases_tab, "数据库")]:
            self.tabs.addTab(widget, name)
        if not local:
            self.tabs.addTab(self.ssl_tab, "SSL")
            index = self.tabs.addTab(self.ssh_tab, "SSH")
            self.tabs.setTabToolTip(index, "使用 SSH 隧道连接数据库。")
        if driver == "mongodb":
            self.tabs.addTab(self.mongo_remarks_tab, "备注")
        else:
            index = self.tabs.addTab(self.http_tab, "HTTP")
            self.tabs.setTabToolTip(index, "HTTP 隧道尚未实现。")
        self.sqlite_type_label.setVisible(local)
        self.sqlite_type_widget.setVisible(local)
        self.redis_type_label.setVisible(driver == "redis")
        self.redis_mode_combo.setVisible(driver == "redis")
        self.sqlserver_auth_label.setVisible(driver == "sqlserver")
        self.sqlserver_auth_combo.setVisible(driver == "sqlserver")
        self.database_label.setVisible(local)
        self.database_row.setVisible(local)
        self.remote_database_label.setVisible(not local and driver != "mongodb")
        self.remote_database_edit.setVisible(not local and driver != "mongodb")
        self.remote_database_label.setText("数据库编号" if driver == "redis" else "初始数据库")
        self.remote_database_edit.setPlaceholderText("0" if driver == "redis" else "数据库名称")
        self.remote_database_edit.setValidator(self._redis_database_validator if driver == "redis" else None)
        self.sqlite_databases_page.setVisible(local)
        database_layout = self.databases_tab.layout()
        database_layout.setContentsMargins(*(0, 0, 0, 0) if local else (20, 28, 20, 20))
        database_layout.setSpacing(0 if local else self._database_note_spacing)
        database_layout.setStretch(self._database_note_stretch, 0 if local else 1)
        self.network_database_note.setVisible(not local)
        self.network_database_note.setText("数据库列表将在连接后由服务器提供。" if driver == "mongodb" else "初始数据库在“常规”页中设置。可用数据库列表由连接后的服务器提供。")
        self.foreign_keys_check.setVisible(local)
        for row_label, field in self._network_rows:
            row_label.setVisible(not local)
            field.setVisible(not local)
        for field in (self.user_edit, self.password_edit, self.remember_check):
            field.setEnabled(not local)
            field.setToolTip("SQLite 3 直连不使用用户名及密码；加密扩展尚未实现。" if local else "")
        self.remember_check.setToolTip("SQLite 3 直连不使用用户名及密码；加密扩展尚未实现。" if local else "密码仅保存在本机系统钥匙串中。")
        self.port_spin.setValue({"postgresql": 5432, "mysql": 3306, "mariadb": 3306,
                                 "sqlserver": 1433, "redis": 6379, "mongodb": 27017}.get(driver, 3306))
        if not local:
            defaults = {"postgresql": "postgres", "mysql": "", "mariadb": "", "sqlserver": "master", "redis": "0", "mongodb": ""}
            user_defaults = {"postgresql": "postgres", "mysql": "root", "mariadb": "root", "sqlserver": "", "redis": "", "mongodb": ""}
            if not self.database_edit.text() or (previous_driver and self.database_edit.text() == defaults.get(previous_driver, "")):
                self.database_edit.setText(defaults.get(driver, ""))
            if not self.user_edit.text() or (previous_driver and self.user_edit.text() == user_defaults.get(previous_driver, "")):
                self.user_edit.setText(user_defaults.get(driver, ""))
        self.user_edit.setPlaceholderText("ACL 用户名（可留空）" if driver == "redis" else "SQL Server 登录名" if driver == "sqlserver" else "")
        self._read_only_driver_state()
        self.database_edit.setPlaceholderText("数据库文件路径，或 :memory:" if local else "数据库名称")
        self.note.setText("当前支持 Redis 单机连接；集群与哨兵尚未实现。" if driver == "redis" else "当前支持 MongoDB Standalone；副本集、分片集群与 SRV 尚未实现。" if driver == "mongodb" else "")
        self.note.setVisible(driver in {"redis", "mongodb"})
        self._mongo_state_changed()
        self._active_driver = driver
        self._ssl_state_changed()
        self.profile_toggle.blockSignals(True)
        self.profile_toggle.setChecked(local)
        self.profile_toggle.blockSignals(False)
        self._toggle_profiles(local)

    def _set_sqlite_profile_header(self, local):
        button = self.new_profile_button
        in_header = button.parentWidget() is self.profile_header
        if local != in_header:
            if local:
                self._profile_rail.removeWidget(button)
                self._profile_rail.insertWidget(0, self.profile_header)
                self.profile_header.layout().addWidget(button)
                button.setText('')
                button.setAccessibleName('新建连接配置文件')
                button.setToolTip('多个连接配置文件尚未实现；当前连接使用主要配置文件。')
                button.setFlat(True)
                button.setAutoDefault(False)
                button.setStyleSheet('QPushButton { padding: 0; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; border: 0; background: transparent; }')
                button.setFixedSize(26, 26)
            else:
                self.profile_header.layout().removeWidget(button)
                self._profile_rail.removeWidget(self.profile_header)
                self._profile_rail.addWidget(button)
                original = self._profile_button_defaults
                button.setText(original['text'])
                button.setAccessibleName(original['accessible_name'])
                button.setToolTip(original['tooltip'])
                button.setStyleSheet(original['stylesheet'])
                button.setMinimumSize(original['minimum'])
                button.setMaximumSize(original['maximum'])
                button.setSizePolicy(original['size_policy'])
                button.setFlat(original['flat'])
                button.setAutoDefault(original['auto_default'])
                # font() includes resolved attributes inherited from the old
                # parent. Reapplying that value would pin an inherited font.
                button.setFont(original['font'] if original['font_explicit'] else QFont())
                # Resetting font ownership also removes QStyleSheet's resolved
                # font. Let the CURRENT application/parent style supply it,
                # exactly as it does for a newly created native button.
                button.style().unpolish(button)
                button.style().polish(button)
                button.updateGeometry()
            button.show()
        self.profile_header.setVisible(local)
        # Only the built-in placeholder profile is localized. Do not rebuild
        # the model, change selection, or rename any externally supplied row.
        main = self.profiles.item(0)
        if main is not None and main.text() in ('Main Profile（有效）', '主要配置文件（有效）'):
            main.setText('主要配置文件（有效）' if local else 'Main Profile（有效）')

    def _read_only_driver_state(self, *_):
        unsupported = self.driver_combo.currentData() == "sqlserver"
        # A previously requested protection must never be silently cleared.
        # Let the user explicitly remove that unsupported setting, then prevent
        # re-enabling it until a real enforcement strategy is available.
        self.read_only_check.setEnabled(not unsupported or self.read_only_check.isChecked())
        self.read_only_check.setToolTip("此版本尚不能保证 SQL Server 的只读连接；请取消此设置后保存。" if unsupported else "")

    def _set_ssl_modes(self, driver):
        modes = (["allow", "prefer", "require", "verify-ca", "verify-full"] if driver == "postgresql"
                 else ["require", "verify-ca", "verify-full"] if driver in {"redis", "sqlserver", "mongodb"}
                 else ["prefer", "require", "verify-ca", "verify-full"])
        current = self.ssl_combo.currentData()
        original = self._original
        if original and original.driver == driver and not getattr(self, "_original_ssl_restored", False):
            current = original.ssl_mode if original.ssl_mode != "disable" else None
            self._original_ssl_restored = True
        default = "verify-full" if driver == "sqlserver" else "require" if driver in {"redis", "mongodb"} else "prefer"
        self.ssl_combo.blockSignals(True)
        self.ssl_combo.clear()
        for mode in modes:
            self.ssl_combo.addItem(mode, mode)
            if driver == "sqlserver":
                self.ssl_combo.setItemData(self.ssl_combo.count() - 1,
                    "验证证书链，不提供跳过验证。verify-full 还验证原始服务器主机名。", Qt.ItemDataRole.ToolTipRole)
        if current not in modes and original and original.driver == driver and original.ssl_mode not in ("disable", *modes):
            self.ssl_combo.addItem(str(original.ssl_mode) + "（不支持）", original.ssl_mode)
            item = self.ssl_combo.model().item(self.ssl_combo.count() - 1)
            item.setEnabled(False)
            item.setToolTip("此驱动不支持该 TLS 模式；请选择支持的模式。")
            current = original.ssl_mode
        selected = self.ssl_combo.findData(current)
        self.ssl_combo.setCurrentIndex(selected if selected >= 0 else self.ssl_combo.findData(default))
        self.ssl_combo.blockSignals(False)

    def _toggle_profiles(self, visible):
        self.profile_pane.setVisible(visible)
        self._reference_frame_size = QSize(749 if visible else 574, 668)
        if self.isVisible():
            self._fit_reference_frame()
        else:
            self.resize(self._reference_frame_size)

    def _fit_reference_frame(self):
        """Match measured outer size after the platform supplies frame margins."""
        if not self.isVisible():
            return
        frame = self.frameGeometry().size()
        client = self.size()
        margins = QSize(max(0, frame.width() - client.width()),
                        max(0, frame.height() - client.height()))
        target = self._reference_frame_size - margins
        # Longer authentication forms may need more space on a platform whose
        # native controls are taller. Keep the measured baseline, but never
        # shrink content through the footer to force an outer-size assertion.
        target = target.expandedTo(self.layout().minimumSize())
        if target != client:
            self.resize(target)

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_reference_frame()
        # Some window systems expose their titlebar metrics only after mapping.
        QTimer.singleShot(0, self._fit_reference_frame)

    def _browse(self):
        if self.sqlite_mode_combo.currentData() == "create":
            self._new_file()
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 SQLite 数据库", self.database_edit.text(),
                                            "SQLite 数据库 (*.sqlite *.sqlite3 *.db *.db3);;所有文件 (*)")
        if path:
            self.database_edit.setText(path)
            if not self.name_edit.text():
                self.name_edit.setText(Path(path).stem)

    def _new_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "新建 SQLite 数据库", self.database_edit.text() or "database.sqlite",
                                            "SQLite 数据库 (*.sqlite);;所有文件 (*)")
        if path:
            self.sqlite_mode_combo.setCurrentIndex(self.sqlite_mode_combo.findData("create"))
            self.database_edit.setText(path)
            if not self.name_edit.text():
                self.name_edit.setText(Path(path).stem)

    def _collect_config(self, *, testing=False):
        name = self.name_edit.text().strip()
        database = self.database_edit.text().strip()
        driver = self.driver_combo.currentData()
        host = self.host_edit.text().strip()
        mongo_fields = {}
        if driver == "mongodb":
            if self.mongo_topology_combo.currentData() != "standalone" or self.mongo_srv_check.isChecked():
                raise ValueError("MongoDB 当前仅支持 Standalone；SRV、副本集及分片集群尚未实现。")
            if self.mongo_replica_set_edit.text().strip() or self.mongo_members_edit.text().strip():
                raise ValueError("MongoDB 副本集名称及成员列表尚未实现，请明确移除这些设置。")
            auth = self.mongo_auth_combo.currentData()
            if auth not in {"NONE", "PASSWORD"}:
                raise ValueError("此 MongoDB 验证方式尚未完成支持；请选择无验证或密码 SCRAM 验证。")
            mechanism = self.mongo_mechanism_combo.currentData() if auth == "PASSWORD" else "NONE"
            if auth == "PASSWORD" and mechanism not in {"DEFAULT", "SCRAM-SHA-1", "SCRAM-SHA-256"}:
                raise ValueError("此 MongoDB 密码验证机制尚未支持。")
            if auth == "PASSWORD" and not self.user_edit.text().strip():
                raise ValueError("请输入 MongoDB 密码验证用户名。")
            if self.mongo_read_preference_combo.currentData() != "primary":
                raise ValueError("MongoDB 当前仅支持 primary 读取偏好。")
            original = self._original if self._original and self._original.driver == "mongodb" else None
            mongo_fields = dict(mongo_auth_source=self.mongo_auth_source_edit.text().strip() or "admin",
                mongo_auth_mechanism=mechanism, mongo_connection_type="standalone", mongo_srv=False,
                mongo_replica_set="", mongo_members="", mongo_read_preference="primary",
                mongo_retry_reads=getattr(original,"mongo_retry_reads",True), mongo_retry_writes=getattr(original,"mongo_retry_writes",True))
        if driver == "redis":
            if self.redis_mode_combo.currentData() != "standalone":
                raise ValueError("Redis 集群和哨兵尚未实现，请选择单机连接。")
            if not database or not database.isascii() or not database.isdecimal():
                raise ValueError("Redis 数据库编号必须是大于或等于 0 的整数。")
            database = database.lstrip("0") or "0"
        if driver == "sqlserver":
            if self.sqlserver_auth_combo.currentData() != "sql":
                raise ValueError("Windows 集成身份验证尚未实现，请使用 SQL Server 用户名和密码。")
            if not self.user_edit.text().strip():
                raise ValueError("请输入 SQL Server 登录名。")
            if self.read_only_check.isChecked():
                raise ValueError("此版本尚不能保证 SQL Server 的只读连接，请关闭只读选项。")
            if any(getattr(self, field).text().strip() for field in
                   ("ssl_cert_edit", "ssl_key_edit", "ssl_crl_edit", "ssl_key_password_edit")):
                raise ValueError("SQL Server 当前不支持客户端证书、客户端密钥、证书撤消列表及密钥通行短语；请移除这些设置。")
        if (not database and driver != "mongodb") or (not name and not testing):
            raise ValueError("请输入连接名及数据库。" if not testing else "请先选择数据库。")
        if driver != "sqlite" and not host:
            raise ValueError("请输入数据库服务器主机名。")
        if driver == "sqlite" and database != ":memory:":
            path = Path(database).expanduser().absolute()
            database = str(path)
            if not path.parent.is_dir():
                raise ValueError("数据库所在文件夹不存在。")
            if self.sqlite_mode_combo.currentData() == "existing" and not path.is_file():
                raise ValueError("数据库文件不存在。若要创建文件，请将类型改为“新建 SQLite 3 数据库”。")
            if path.exists() and not path.is_file():
                raise ValueError("请选择数据库文件，不能选择文件夹。")
            if self.read_only_check.isChecked() and not path.is_file():
                raise ValueError("只读连接不能创建新的数据库文件。")
        ssl_mode = self.ssl_combo.currentData() if self.ssl_enabled_check.isChecked() else "disable"
        if driver == "redis" and ssl_mode not in {"disable", "require", "verify-ca", "verify-full"}:
            raise ValueError("Redis 不支持此 SSL 模式；请选择 require、verify-ca 或 verify-full。")
        if driver == "mongodb" and ssl_mode not in {"disable", "require", "verify-ca", "verify-full"}:
            raise ValueError("MongoDB 不支持此 SSL 模式；请选择 require、verify-ca 或 verify-full。")
        if driver == "sqlserver" and ssl_mode not in {"disable", "require", "verify-ca", "verify-full"}:
            raise ValueError("SQL Server 不支持此 SSL 模式；请选择 require、verify-ca 或 verify-full。")
        custom_ssl = driver != "sqlite" and ssl_mode != "disable" and self.ssl_verify_check.isChecked()
        ssl_files = {name: "" for name in ("ssl_ca", "ssl_cert", "ssl_key", "ssl_crl")}
        ssl_password = ""
        if custom_ssl:
            if driver == "mongodb" and ssl_mode == "require" and any(
                    field.text().strip() for field in (self.ssl_ca_edit, self.ssl_crl_edit)):
                raise ValueError("MongoDB 根证书及证书撤消列表需要 verify-ca 或 verify-full，不能被静默忽略。")
            if bool(self.ssl_cert_edit.text().strip()) != bool(self.ssl_key_edit.text().strip()):
                raise ValueError("客户端证书与客户端密钥必须同时设置。")
            for attribute, label in [("ssl_ca", "根证书"), ("ssl_cert", "客户端证书"),
                                     ("ssl_key", "客户端密钥"), ("ssl_crl", "证书撤消列表")]:
                value = getattr(self, attribute + "_edit").text().strip()
                if value:
                    ssl_files[attribute] = self._readable_file(value, label)
            ssl_password = self.ssl_key_password_edit.text()
            if ssl_password and not ssl_files["ssl_key"]:
                raise ValueError("设置密钥通行短语前，请选择客户端密钥。")
        ssh_enabled = driver != "sqlite" and self.ssh_enabled_check.isChecked()
        ssh_host = self.ssh_host_edit.text().strip()
        ssh_user = self.ssh_user_edit.text().strip()
        ssh_method = self.ssh_auth_combo.currentData()
        ssh_key = self.ssh_key_edit.text().strip()
        if ssh_enabled:
            if not ssh_host or not ssh_user:
                raise ValueError("请输入 SSH 主机及用户名。")
            if ssh_method in ("key", "password+key"):
                if not ssh_key:
                    raise ValueError("请选择 SSH 私钥文件。")
                ssh_key = self._readable_file(ssh_key, "SSH 私钥")
        config = ConnectionConfig(
            id=self.sqlite_databases_page.connection_id if driver == "sqlite" else self._original.id if self._original else uuid.uuid4().hex,
            name=name or "测试连接", driver=driver, database=database, host=host or "localhost",
            port=self.port_spin.value() if driver != "sqlite" else 0,
            username=self.user_edit.text() if driver != "sqlite" and (driver != "mongodb" or mongo_fields["mongo_auth_mechanism"] != "NONE") else "",
            password=self.password_edit.text() if driver != "sqlite" and (driver != "mongodb" or mongo_fields["mongo_auth_mechanism"] != "NONE") else "",
            ssl_mode=ssl_mode,
            read_only=self.read_only_check.isChecked(),
            connect_timeout=self.timeout_spin.value(),
            **ssl_files, ssl_key_password=ssl_password,
            ssh_enabled=ssh_enabled, ssh_host=ssh_host, ssh_port=self.ssh_port_spin.value(),
            ssh_username=ssh_user, ssh_auth_method=ssh_method,
            ssh_password=self.ssh_password_edit.text(), ssh_key_file=ssh_key,
            ssh_key_passphrase=self.ssh_key_passphrase_edit.text(),
            ssh_compression=self.ssh_compression_check.isChecked(),
            ssh_host_key=self._ssh_host_key,
            sqlite_open_mode=self.sqlite_mode_combo.currentData() if driver == "sqlite" else "legacy",
            **mongo_fields,
        )
        return self.sqlite_databases_page.apply_to(config) if driver == "sqlite" else config

    @staticmethod
    def _readable_file(value, label):
        path = Path(value).expanduser().absolute()
        try:
            if not path.is_file():
                raise OSError("不是普通文件")
            with path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            raise ValueError(f"无法读取{label}：{path}\n{exc}") from exc
        return str(path)

    def accept(self):
        if self._testing:
            return
        try:
            self._config = self._collect_config()
        except ValueError as exc:
            QMessageBox.warning(self, "连接设置", str(exc))
            return
        super().accept()

    def get_config(self) -> ConnectionConfig | None:
        return self._config

    def test_connection(self):
        if self._testing:
            return
        self._sqlite_profile_changed()
        try:
            config = self._collect_config(testing=True)
        except ValueError as exc:
            QMessageBox.warning(self, "测试连接", str(exc))
            return
        self._start_connection_test(config)

    def _reset_sqlite_test_diagram(self):
        for diagram in (self.diagram, getattr(self, "sqlite_database_diagram", None)):
            if diagram is not None:
                diagram.sqlite_connection_succeeded = False
                diagram.update()

    def _sqlite_profile_changed(self, *_):
        if self.driver_combo.currentData() != 'sqlite':
            return
        self._reset_sqlite_test_diagram()
        self.sqlite_test_banner.clear_result()
        self.test_status.clear()
        self.test_status.setToolTip('')
        self.test_status.show()
        self.footer.layout().setStretch(self._test_footer_gap_index, 0)

    @staticmethod
    def _probe_session(session, config):
        session.connect()
        if config.driver != "mongodb":
            session.execute("PING" if config.driver == "redis" else "SELECT 1", limit=1)
        # MongoSession.connect performs its authenticated ping directly.
        if config.driver == "sqlite":
            session.objects("main")
            # Read the actual tested endpoint in its worker. No Qt object or
            # live session crosses back into the GUI result presentation.
            version = session.execute("SELECT sqlite_version()", limit=1)[0].rows[0][0]
            return _ConnectionProbeResult("连接成功", str(version)[:512])
        return "连接成功"

    def _start_connection_test(self, config):
        """Run an already captured profile using the dialog's serial owner."""
        # Oracle has its own initializer and result strip. Its override already
        # resets that strip before delegating to this shared worker owner.
        if hasattr(self, 'sqlite_test_banner'):
            self._reset_sqlite_test_diagram()
            self.sqlite_test_banner.clear_result()
            self.test_status.show()
            self.footer.layout().setStretch(self._test_footer_gap_index, 0)
        self._tested_driver = config.driver
        self._test_server_version = ''
        self._testing = True
        self.content.setEnabled(False)
        self.buttons.setEnabled(False)
        self.test_button.setEnabled(False)
        self.test_status.setText("正在连接…")
        self.test_status.setToolTip("")
        self._test_error = None
        self._test_success_message = ""
        from opennavicat.database import DatabaseSession
        from opennavicat.tasks import Task
        probe = self._probe_session

        def work():
            session = DatabaseSession(config)
            try:
                return probe(session, config)
            finally:
                session.close()

        task = Task(work)
        task.signals.setParent(self)
        self._test_task = task
        task.signals.succeeded.connect(self._test_succeeded)
        task.signals.failed.connect(self._test_failed)
        task.signals.finished.connect(self._test_complete)
        task.signals.finished.connect(task.signals.deleteLater)
        self._test_pool.start(task)

    def _test_succeeded(self, message):
        if isinstance(message, _ConnectionProbeResult):
            self._test_success_message = message.message
            self._test_server_version = message.server_version
        else:
            self._test_success_message = message
            self._test_server_version = ''

    def _display_test_result(self, success, message):
        for diagram in (self.diagram, getattr(self, "sqlite_database_diagram", None)):
            if diagram is not None:
                diagram.sqlite_connection_succeeded = bool(success and self._tested_driver == 'sqlite'
                    and self.driver_combo.currentData() == 'sqlite')
                diagram.update()
        if self._tested_driver == 'sqlite' and self.driver_combo.currentData() == 'sqlite' and success:
            version = self._test_server_version[:512]
            detail = '服务器版本: ' + version
            self.sqlite_test_banner.set_result(True, '连接成功', detail_text=detail,
                copy_text='连接成功  ' + detail, tooltip=version)
            self.test_status.hide()
            self.footer.layout().setStretch(self._test_footer_gap_index, 1)
        else:
            # Failed SQLite tests and other drivers retain their prior footer
            # text/tooltip. Their reference result surfaces are not observed.
            self.sqlite_test_banner.clear_result()
            self.test_status.show()
            self.footer.layout().setStretch(self._test_footer_gap_index, 0)

    def _test_failed(self, error):
        self._test_error = error

    def _test_complete(self):
        # Host-key approval happens only after the worker's finished signal.
        # A modal prompt can run a nested GUI loop, so asking from failed would
        # allow finished to run midway through that prompt and lose a retry.
        from opennavicat.transport import SSHHostKeyError
        error = self._test_error
        self._test_error = None
        approved = isinstance(error, SSHHostKeyError) and confirm_ssh_host_key(self, error)
        if approved:
            self._ssh_host_key = error.pin
            self._ssh_pin_endpoint = self._ssh_endpoint()
            self._update_ssh_pin_label()
        self._testing = False
        self._test_task = None
        self.content.setEnabled(True)
        self.buttons.setEnabled(True)
        self.test_button.setEnabled(True)
        if approved:
            self.test_connection()
        elif error is not None:
            message = str(error)
            self.test_status.setText("连接失败")
            self.test_status.setToolTip(message)
            self.test_finished.emit(False, message)
        else:
            self.test_status.setText(self._test_success_message)
            self.test_finished.emit(True, self._test_success_message)

    def reject(self):
        if not self._testing:
            super().reject()

    def done(self, result):
        if not self._testing:
            super().done(result)

    def closeEvent(self, event):
        if self._testing:
            event.ignore()
            return
        super().closeEvent(event)


class TableDesignerDialog(QDialog):
    """Visual CREATE TABLE builder with the exact SQL visible before execution."""

    TYPES = {
        "sqlite": ["INTEGER", "TEXT", "REAL", "NUMERIC", "BLOB", "BOOLEAN", "DATETIME"],
        "postgresql": ["INTEGER", "BIGINT", "TEXT", "VARCHAR(255)", "BOOLEAN", "NUMERIC(12,2)",
                       "TIMESTAMP", "TIMESTAMPTZ", "DATE", "UUID", "JSONB", "BYTEA", "SERIAL", "BIGSERIAL"],
        "mysql": ["INT", "BIGINT", "VARCHAR(255)", "TEXT", "BOOLEAN", "DECIMAL(12,2)",
                  "DATETIME", "TIMESTAMP", "DATE", "JSON", "BLOB", "DOUBLE"],
    }

    def __init__(self, parent=None, session=None, schema="main"):
        super().__init__(parent)
        self.session = session
        self.schema = schema
        self.driver = getattr(getattr(session, "config", None), "driver", "sqlite")
        self._sql = ""
        self.setWindowTitle("Create table")
        self.resize(830, 590)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Table name")
        form.addRow(f"Table in {schema}", self.name_edit)
        layout.addLayout(form)
        self.grid = QTableWidget(0, 5)
        self.grid.setHorizontalHeaderLabels(["Column", "Type", "Primary key", "Not null", "Default SQL expression"])
        self.grid.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.grid.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.grid.setColumnWidth(2, 85)
        self.grid.setColumnWidth(3, 70)
        self.grid.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.grid, 1)
        actions = QHBoxLayout()
        add = QPushButton("+ Add column")
        remove = QPushButton("Remove column")
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        layout.addLayout(actions)
        add.clicked.connect(lambda: self.add_column())
        remove.clicked.connect(self._remove_columns)
        layout.addWidget(_hint("Defaults are SQL expressions: use 'text' for strings, or CURRENT_TIMESTAMP. Review the generated SQL before creating the table."))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(150)
        self.preview.setPlaceholderText("CREATE TABLE preview")
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create table")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name_edit.textChanged.connect(self._refresh_preview)
        self.add_column("id", "INTEGER" if self.driver in ("sqlite", "postgresql") else "INT", pk=True)
        self.add_column("", "NVARCHAR(MAX)" if self.driver == "sqlserver" else "TEXT")

    def add_column(self, name="", datatype=None, pk=False):
        row = self.grid.rowCount()
        self.grid.insertRow(row)
        name_edit = QLineEdit(name)
        type_combo = QComboBox()
        type_combo.setEditable(True)
        type_combo.addItems(["INT", "BIGINT", "SMALLINT", "TINYINT", "BIT", "DECIMAL(18,2)", "FLOAT", "NVARCHAR(255)", "NVARCHAR(MAX)", "VARCHAR(255)", "VARBINARY(MAX)", "DATE", "TIME", "DATETIME2", "DATETIMEOFFSET", "UNIQUEIDENTIFIER"] if self.driver == "sqlserver" else self.TYPES.get(self.driver, self.TYPES["mysql"]))
        if datatype:
            type_combo.setCurrentText(datatype)
        key_check, null_check = QCheckBox(), QCheckBox()
        key_check.setChecked(pk)
        null_check.setChecked(pk)
        default_edit = QLineEdit()
        default_edit.setPlaceholderText("No default")
        for column, widget in enumerate((name_edit, type_combo, key_check, null_check, default_edit)):
            self.grid.setCellWidget(row, column, widget)
        name_edit.textChanged.connect(self._refresh_preview)
        type_combo.currentTextChanged.connect(self._refresh_preview)
        key_check.toggled.connect(self._refresh_preview)
        null_check.toggled.connect(self._refresh_preview)
        default_edit.textChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _remove_columns(self):
        rows = sorted({index.row() for index in self.grid.selectedIndexes()}, reverse=True)
        if not rows and self.grid.currentRow() >= 0:
            rows = [self.grid.currentRow()]
        for row in rows:
            self.grid.removeRow(row)
        self._refresh_preview()

    def _quote(self, value):
        if self.session:
            return self.session.quote_identifier(value)
        char = "`" if self.driver in ("mysql", "mariadb") else '"'
        return char + value.replace(char, char + char) + char

    def _build_sql(self, validate=False):
        table = self.name_edit.text().strip()
        if not table:
            if validate:
                raise ValueError("Enter a table name.")
            return ""
        clauses, primary, names = [], [], set()
        for row in range(self.grid.rowCount()):
            name = self.grid.cellWidget(row, 0).text().strip()
            datatype = self.grid.cellWidget(row, 1).currentText().strip()
            pk = self.grid.cellWidget(row, 2).isChecked()
            notnull = self.grid.cellWidget(row, 3).isChecked()
            default = self.grid.cellWidget(row, 4).text().strip()
            if not name:
                continue
            if name.casefold() in names:
                raise ValueError(f"Column '{name}' is duplicated.")
            names.add(name.casefold())
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_ ]*(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?(?:\[\])?", datatype):
                raise ValueError(f"Enter a valid SQL type for '{name}'.")
            clause = f"  {self._quote(name)} {datatype}"
            if notnull or pk:
                clause += " NOT NULL"
            if default:
                clause += " DEFAULT " + default
            clauses.append(clause)
            if pk:
                primary.append(self._quote(name))
        if not clauses:
            raise ValueError("Add at least one named column.")
        if primary:
            clauses.append("  PRIMARY KEY (" + ", ".join(primary) + ")")
        qualified = self._quote(self.schema) + "." + self._quote(table) if self.schema else self._quote(table)
        return f"CREATE TABLE {qualified} (\n" + ",\n".join(clauses) + "\n);"

    def _refresh_preview(self, *_):
        if not hasattr(self, "preview"):
            return
        try:
            self.preview.setPlainText(self._build_sql())
        except ValueError as exc:
            self.preview.setPlainText(str(exc))

    def accept(self):
        try:
            self._sql = self._build_sql(True)
        except ValueError as exc:
            QMessageBox.warning(self, "Table definition", str(exc))
            return
        super().accept()

    def sql(self) -> str:
        return self._sql


class RowDialog(QDialog):
    """Record fields with explicit NULL values and type-aware conversion.

    New rows include an additional Use default checkbox. Fields left on their
    default are omitted from the insert dictionary instead of sent as NULL.
    """

    def __init__(self, columns: list[dict], values: dict | None = None, parent=None):
        super().__init__(parent)
        self._new = values is None
        self._columns = columns
        self._initial = values or {}
        self._values = {}
        self._fields = []
        self.setWindowTitle("Insert record" if self._new else "Edit record")
        self.resize(640, min(650, 180 + len(columns) * 55))
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setSpacing(10)
        for column in columns:
            name = column["name"]
            value = self._initial.get(name)
            line = QLineEdit(self._display(value))
            line.setPlaceholderText(str(column.get("type", "")))
            protected = self._protected(column)
            null = QCheckBox("NULL")
            null.setChecked(value is None and not self._new)
            default = QCheckBox("Default") if self._new else None
            if default:
                default.setChecked(True)
                default.setEnabled(not protected)
            field = QWidget()
            row = QHBoxLayout(field)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(line, 1)
            row.addWidget(null)
            if default:
                row.addWidget(default)
            label = name + ("  [PK]" if column.get("pk") else "")
            if protected:
                label += "  (generated)"
                line.setToolTip("This value is generated by the database and cannot be assigned.")
            form.addRow(label, field)

            def update_state(_=None, edit=line, null_check=null, default_check=default,
                             database_generated=protected):
                using_default = bool(default_check and default_check.isChecked())
                null_check.setEnabled(not database_generated and not using_default)
                edit.setEnabled(not database_generated and not using_default and not null_check.isChecked())

            null.toggled.connect(update_state)
            if default:
                default.toggled.connect(update_state)
            update_state()
            self._fields.append((column, line, null, default))
        scroll.setWidget(body)
        layout.addWidget(scroll)
        layout.addWidget(_hint(
            "Default omits the field so the database can generate its value. Uncheck it to enter a value; NULL stores an explicit null."
            if self._new else "NULL stores a null value. An empty text field stores an empty string. Changes are applied when you save."
        ))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _protected(column: dict) -> bool:
        return bool(column.get("generated") or column.get("identity_generation") == "ALWAYS")

    @staticmethod
    def _display(value) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    @staticmethod
    def _parse(text: str, column: dict):
        datatype = column.get("type", "").upper().split("(", 1)[0].strip()
        if datatype in ("JSON", "JSONB"):
            return json.loads(text)
        if datatype in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT", "SERIAL", "BIGSERIAL", "INT2", "INT4", "INT8"):
            return int(text)
        if datatype in ("REAL", "FLOAT", "DOUBLE", "DOUBLE PRECISION", "FLOAT4", "FLOAT8"):
            return float(text)
        if datatype in ("NUMERIC", "DECIMAL"):
            # Decimal strings preserve precision and are accepted by all drivers.
            from decimal import Decimal
            Decimal(text)
            return text
        if datatype in ("BOOLEAN", "BOOL"):
            if text.casefold() in ("true", "1", "yes"):
                return True
            if text.casefold() in ("false", "0", "no"):
                return False
            raise ValueError("Use true or false for a boolean field.")
        return text

    def accept(self):
        result = {}
        for column, line, null, default in self._fields:
            name = column["name"]
            if self._protected(column):
                continue
            if default and default.isChecked():
                continue
            if null.isChecked():
                if not column.get("nullable", True) and not column.get("pk"):
                    QMessageBox.warning(self, "Record value", f"'{name}' does not allow NULL.")
                    return
                result[name] = None
            else:
                try:
                    # Preserve untouched rich values such as binary/date objects.
                    original = self._initial.get(name)
                    if not self._new and original is not None and line.text() == self._display(original):
                        result[name] = original
                    else:
                        result[name] = self._parse(line.text(), column)
                except (ValueError, ArithmeticError) as exc:
                    QMessageBox.warning(self, "Record value", f"Invalid value for '{name}': {exc}")
                    line.setFocus()
                    return
        self._values = result
        super().accept()

    def values(self) -> dict:
        return self._values
