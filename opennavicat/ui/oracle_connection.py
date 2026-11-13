"""Oracle connection settings shaped by the retained OR18-01 observation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re
import threading
import uuid

from PySide6.QtCore import QRegularExpression, QSignalBlocker, QSize, Qt, QThreadPool
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QSizePolicy, QVBoxLayout, QWidget,
)

from opennavicat.database import ConnectionConfig
from .dialogs import ConnectionDialog, _ConnectionDiagram, _PortSpinBox
from .icons import tool_icon, toggle_icon


class _OraclePortEdit(QLineEdit):
    """Oracle's plain port field; keep invalid drafts visible for validation.

    The small value/setValue API retains existing form callers without adding
    spin buttons or changing a port through Up/Down. Programmatic assignment is
    deliberately neither truncated nor clamped to a different valid endpoint.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setValidator(QRegularExpressionValidator(QRegularExpression('[0-9]{1,5}'), self))

    def setValue(self, value):
        self._assigned_value = value
        self._assigned_text = '' if value is None else str(value)
        self.setText(self._assigned_text)
        self._assigned_text = self.text()

    def value(self):
        text = self.text()
        if not re.fullmatch('[0-9]{1,5}', text) or not 1 <= int(text) <= 65535:
            raise ValueError('请输入 1 到 65535 之间的端口号。')
        return int(text)

    def draftValue(self):
        """Retain hidden Basic options while editing an unsupported TNS draft."""
        text = self.text()
        if text == self._assigned_text:
            return self._assigned_value
        return int(text) if re.fullmatch('[0-9]{1,5}', text) else text

# Public Oracle character-set identifiers in the actually observed menu order.
# This module has no runtime dependency on reference files or local TNS clients.
ORACLE_CHARACTER_SETS = tuple("""AL16UTF16 AL24UTFFSS AL32UTF8 AR8ADOS710 AR8ADOS710T AR8ADOS720
AR8ADOS720T AR8APTEC715 AR8APTEC715T AR8ARABICMAC AR8ARABICMACS AR8ARABICMACT
AR8ASMO708PLUS AR8ASMO8X AR8EBCDIC420S AR8EBCDICX AR8HPARABIC8T AR8ISO8859P6
AR8MSWIN1256 AR8MUSSAD768 AR8MUSSAD768T AR8NAFITHA711 AR8NAFITHA711T AR8NAFITHA721
AR8NAFITHA721T AR8SAKHR706 AR8SAKHR707 AR8SAKHR707T AR8XBASIC AZ8ISO8859P9E
BG8MSWIN BG8PC437S BLT8CP921 BLT8EBCDIC1112 BLT8EBCDIC1112S BLT8ISO8859P13
BLT8MSWIN1257 BLT8PC775 BN8BSCII CDN8PC863 CE8BS2000 CEL8ISO8859P14
CH7DEC CL8BS2000 CL8EBCDIC1025 CL8EBCDIC1025C CL8EBCDIC1025R CL8EBCDIC1025S
CL8EBCDIC1025X CL8EBCDIC1158 CL8EBCDIC1158R CL8ISO8859P5 CL8ISOIR111 CL8KOI8R
CL8KOI8U CL8MACCYRILLIC CL8MACCYRILLICS CL8MSWIN1251 D7DEC D7SIEMENS9780X
D8BS2000 D8EBCDIC1141 D8EBCDIC273 DK7SIEMENS9780X DK8BS2000 DK8EBCDIC1142
DK8EBCDIC277 E7DEC E7SIEMENS9780X E8BS2000 EE8BS2000 EE8EBCDIC870
EE8EBCDIC870C EE8EBCDIC870S EE8ISO8859P2 EE8MACCE EE8MACCES EE8MACCROATIAN
EE8MACCROATIANS EE8MSWIN1250 EE8PC852 EEC8EUROASCI EEC8EUROPA3 EL8DEC
EL8EBCDIC423R EL8EBCDIC875 EL8EBCDIC875R EL8EBCDIC875S EL8GCOS7 EL8ISO8859P7
EL8MACGREEK EL8MACGREEKS EL8MSWIN1253 EL8PC437S EL8PC737 EL8PC851
EL8PC869 ET8MSWIN923 F7DEC F7SIEMENS9780X F8BS2000 F8EBCDIC1147
F8EBCDIC297 HU8ABMOD HU8CWI2 I7DEC I7SIEMENS9780X I8EBCDIC1144
I8EBCDIC280 IN8ISCII IS8MACICELANDIC IS8MACICELANDICS IS8PC861 IW7IS960
IW8EBCDIC1086 IW8EBCDIC424 IW8EBCDIC424S IW8ISO8859P8 IW8MACHEBREW IW8MACHEBREWS
IW8MSWIN1255 IW8PC1507 JA16DBCS JA16DBCSFIXED JA16EBCDIC930 JA16EUC
JA16EUCFIXED JA16EUCTILDE JA16EUCYEN JA16MACSJIS JA16SJIS JA16SJISFIXED
JA16SJISTILDE JA16SJISYEN JA16VMS KO16DBCS KO16DBCSFIXED KO16KSC5601
KO16KSC5601FIXED KO16KSCCS KO16MSWIN949 LA8ISO6937 LA8PASSPORT LT8MSWIN921
LT8PC772 LT8PC774 LV8PC1117 LV8PC8LR LV8RST104090 N7SIEMENS9780X
N8PC865 NDK7DEC NE8ISO8859P10 NEE8ISO8859P4 NL7DEC RU8BESTA
RU8PC855 RU8PC866 S7DEC S7SIEMENS9780X S8BS2000 S8EBCDIC1143
S8EBCDIC278 SE8ISO8859P3 SF7ASCII SF7DEC TH8MACTHAI TH8MACTHAIS
TH8TISASCII TH8TISEBCDIC TH8TISEBCDICS TR7DEC TR8DEC TR8EBCDIC1026
TR8EBCDIC1026S TR8MACTURKISH TR8MACTURKISHS TR8MSWIN1254 TR8PC857 US7ASCII
US8BS2000 US8ICL US8PC437 UTF8 UTFE VN8MSWIN1258
VN8VN3 WE8BS2000 WE8BS2000E WE8BS2000L5 WE8DEC WE8DG
WE8EBCDIC1047 WE8EBCDIC1047E WE8EBCDIC1140 WE8EBCDIC1140C WE8EBCDIC1145 WE8EBCDIC1146
WE8EBCDIC1148 WE8EBCDIC1148C WE8EBCDIC284 WE8EBCDIC285 WE8EBCDIC37 WE8EBCDIC37C
WE8EBCDIC500 WE8EBCDIC500C WE8EBCDIC871 WE8EBCDIC924 WE8GCOS7 WE8HP
WE8ICL WE8ISO8859P1 WE8ISO8859P15 WE8ISO8859P9 WE8ISOICLUK WE8MACROMAN8
WE8MACROMAN8S WE8MSWIN1252 WE8NCR4970 WE8NEXTSTEP WE8PC850 WE8PC858
WE8PC860 WE8ROMAN8 YUG7ASCII ZHS16CGB231280 ZHS16CGB231280FIXED ZHS16DBCS
ZHS16DBCSFIXED ZHS16GBK ZHS16GBKFIXED ZHS16MACCGB231280 ZHS32GB18030 ZHT16BIG5
ZHT16BIG5FIXED ZHT16CCDC ZHT16DBCS ZHT16DBCSFIXED ZHT16DBT ZHT16HKSCS
ZHT16HKSCS31 ZHT16MSWIN950 ZHT32EUC ZHT32EUCFIXED ZHT32SOPS ZHT32TRIS
ZHT32TRISFIXED""".split())


class _OracleConnectionDiagram(_ConnectionDiagram):
    """Original artwork with the actually observed successful direct line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.connection_succeeded = False

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.connection_succeeded and not self.tunnel_enabled:
            from PySide6.QtGui import QColor, QPainter, QPen
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('#4CAF50'), 1.5))
            painter.drawLine(round(self.width() * .24 + 40), 63,
                             round(self.width() * .76 - 40), 63)


class OracleConnectionDialog(ConnectionDialog):
    """Dedicated five-page form; common Test owns its worker and signals."""

    def __init__(self, parent=None, config=None, *, is_new=None):
        QDialog.__init__(self, parent)
        is_new = config is None if is_new is None else is_new
        self._original, self._config = None if is_new else config, None
        self._base = config or ConnectionConfig(uuid.uuid4().hex, '', driver='oracle',
            host='', port=1521, database='', ssl_mode='disable')
        self._testing, self._test_task = False, None
        self._test_pool = QThreadPool(self)
        self._test_pool.setMaxThreadCount(1)
        self._reference_frame_size = QSize(900, 700)
        self.back_requested = False
        self._loading = True
        self._diagrams = []
        self._draft_error = None
        self._initial_query = self._base.oracle_initial_query
        self._ssh_host_key = self._base.ssh_host_key
        self._ssh_pin_endpoint = (self._base.ssh_host.strip().casefold(), self._base.ssh_port)
        self.setObjectName('oracleConnectionDialog')
        self.setWindowTitle(('编辑连接' if self._original else '新建连接') + ' (Oracle)')
        self.resize(900, 700)
        self.setMinimumSize(700, 620)
        font = self.font(); font.setPixelSize(12); self.setFont(font)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.content = QWidget(self)
        row = QHBoxLayout(self.content); row.setContentsMargins(18, 14, 18, 0); row.setSpacing(18)
        self.profile_pane = QFrame(); self.profile_pane.setFixedWidth(200)
        self.profile_pane.setObjectName('connectionProfilePane')
        profiles = QVBoxLayout(self.profile_pane); profiles.setContentsMargins(0, 0, 0, 0)
        title = QHBoxLayout(); title.addWidget(QLabel('连接配置文件'))
        self.new_profile_button = QPushButton(); self.new_profile_button.setFixedWidth(26)
        self.new_profile_button.setIcon(tool_icon('profile_add', 16)); self.new_profile_button.setIconSize(QSize(16, 16))
        self.new_profile_button.setStyleSheet('QPushButton { padding: 0px; }')
        self.new_profile_button.setAccessibleName('新建连接配置文件')
        self.new_profile_button.setEnabled(False)
        self.new_profile_button.setToolTip('多个连接配置文件尚未实现。')
        title.addWidget(self.new_profile_button); profiles.addLayout(title)
        self.profiles = QListWidget(); self.profiles.addItem('主要配置文件（有效）'); self.profiles.setCurrentRow(0); profiles.addWidget(self.profiles)
        row.addWidget(self.profile_pane)
        self.settings_pane = QWidget(); self.settings_pane.setObjectName('connectionSettingsPane')
        right = QVBoxLayout(self.settings_pane); right.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtWidgets import QTabWidget
        self.tabs = QTabWidget(); self.tabs.setObjectName('connectionTabs')
        self.tabs.setStyleSheet('QTabWidget::pane { border: 0; } QTabWidget::tab-bar { alignment: center; }')
        right.addWidget(self.tabs); row.addWidget(self.settings_pane, 1); outer.addWidget(self.content, 1)
        self.driver_combo = QComboBox(self); self.driver_combo.addItem('Oracle', 'oracle'); self.driver_combo.hide()
        self._build_general(); self._build_advanced(); self._build_databases(); self._build_ssh(); self._build_notes()
        self.test_banner = QFrame(); self.test_banner.setObjectName('oracleConnectionTestBanner')
        banner = QHBoxLayout(self.test_banner); banner.setContentsMargins(10, 5, 10, 5)
        self.test_status = QLabel(); self.test_status.setObjectName('connectionTestStatus')
        self.test_status.setTextFormat(Qt.TextFormat.PlainText)
        banner.addWidget(self.test_status, 1)
        self.test_copy_button = QPushButton('⧉'); self.test_copy_button.setFixedWidth(26)
        self.test_copy_button.setToolTip('复制连接测试结果'); self.test_copy_button.clicked.connect(self._copy_test_result)
        self.test_dismiss_button = QPushButton('×'); self.test_dismiss_button.setFixedWidth(26)
        self.test_dismiss_button.setToolTip('关闭连接测试结果'); self.test_dismiss_button.clicked.connect(self.test_banner.hide)
        banner.addWidget(self.test_copy_button); banner.addWidget(self.test_dismiss_button)
        self.test_banner.hide(); outer.addWidget(self.test_banner)
        self.test_finished.connect(self._display_test_result)
        self.footer = QFrame(); self.footer.setFixedHeight(60); footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(18, 12, 18, 12)
        self.profile_toggle = QPushButton(); self.profile_toggle.setCheckable(True); self.profile_toggle.setChecked(True)
        self.profile_toggle.setIcon(toggle_icon('profile_pane', 16)); self.profile_toggle.setIconSize(QSize(16, 16))
        self.profile_toggle.setStyleSheet('QPushButton { padding: 0px; }')
        self.profile_toggle.setAccessibleName('隐藏或显示连接配置文件')
        self.profile_toggle.setFixedWidth(28); self.profile_toggle.setToolTip('隐藏或显示连接配置文件')
        self.profile_toggle.toggled.connect(self._toggle_profiles); footer.addWidget(self.profile_toggle)
        self.uri_button = QPushButton('URI'); self.uri_button.setEnabled(False)
        self.uri_button.setToolTip('Oracle URI 编辑尚未实现。'); footer.addWidget(self.uri_button)
        self.test_button = QPushButton('测试连接'); self.test_button.setAutoDefault(False)
        self.test_button.clicked.connect(self.test_connection); footer.addWidget(self.test_button)
        footer.addStretch(1)
        self.back_button = QPushButton('上一步'); self.back_button.setVisible(self._original is None)
        self.back_button.clicked.connect(self._go_back)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('好')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.back_button); footer.addWidget(self.buttons); outer.addWidget(self.footer)
        # Compatibility flags let the window preserve prior keyring choices for
        # a retained, unsupported TLS draft without introducing an SSL page.
        self.ssl_remember_check = QCheckBox(self); self.ssl_remember_check.hide()
        self._loading = False
        self._state_changed(); self._ssh_state_changed()

    def _page(self, title):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(16, 4, 16, 8)
        diagram = _OracleConnectionDiagram(); diagram.driver = 'Oracle'; self._diagrams.append(diagram); layout.addWidget(diagram)
        self.tabs.addTab(page, title)
        return page, layout, diagram

    @staticmethod
    def _center_form(layout, form, label_width=80):
        """Center the retained 300px field column, with labels on its left."""
        form.setContentsMargins(0, 0, 0, 0)
        # The form owns its label/spacing metrics. A nominal label-width cap
        # clips fixed-width fields as styles or visible rows change. The outer
        # stretches center this natural-size form; align its contents left so
        # QFormLayout does not add right-alignment spacing past that size.
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        widget = QWidget(); widget.setLayout(form)
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        centered = QHBoxLayout(); centered.setContentsMargins(0, 0, 0, 0)
        centered.addStretch(1); centered.addWidget(widget); centered.addSpacing(label_width); centered.addStretch(1)
        layout.addLayout(centered)

    @staticmethod
    def _separator(layout):
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine); layout.addWidget(line)

    @staticmethod
    def _combo(items, value):
        combo = QComboBox()
        for label, data in items: combo.addItem(label, data)
        index = combo.findData(value)
        if index < 0:
            text = str(value)
            combo.addItem(text[:120] + ('…' if len(text) > 120 else '') + '（不支持）', value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        return combo

    def _build_general(self):
        self.general_tab, layout, self.diagram = self._page('常规')
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignmentFlag.AlignRight); form.setVerticalSpacing(10)
        self.general_form = form
        self.name_edit = QLineEdit(self._base.name); self.name_edit.setObjectName('connectionName')
        form.addRow('连接名称:', self.name_edit)
        self.oracle_type_combo = self._combo([('基本', 'basic'), ('TNS', 'tns')], self._base.oracle_connection_type)
        form.addRow('连接类型:', self.oracle_type_combo)
        self.host_edit = QLineEdit(self._base.host); self.host_edit.setObjectName('connectionHost')
        self.port_spin = _OraclePortEdit(); self.port_spin.setValue(self._base.port)
        self.oracle_service_edit = QLineEdit(self._base.oracle_service_identifier)
        self.oracle_service_edit.setObjectName('oracleServiceIdentifier')
        self.database_edit = QLineEdit(self); self.database_edit.hide(); self.database_edit.setText('')
        self._basic_rows = []
        for title, field in [('主机:', self.host_edit), ('端口:', self.port_spin), ('服务名称:', self.oracle_service_edit)]:
            label = QLabel(title); form.addRow(label, field); self._basic_rows.append(label)
        self.oracle_service_label = self._basic_rows[-1]
        radio_row = QWidget(); radios = QHBoxLayout(radio_row); radios.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtWidgets import QButtonGroup, QRadioButton
        self.oracle_service_group = QButtonGroup(self)
        self.oracle_service_radio = QRadioButton('服务名称'); self.oracle_sid_radio = QRadioButton('SID')
        for radio in (self.oracle_service_radio, self.oracle_sid_radio): self.oracle_service_group.addButton(radio); radios.addWidget(radio)
        self.oracle_sid_radio.setChecked(self._base.oracle_service_type == 'sid')
        self.oracle_service_radio.setChecked(self._base.oracle_service_type != 'sid')
        self._unknown_service_type = self._base.oracle_service_type if self._base.oracle_service_type not in ('service', 'sid') else None
        self.oracle_service_group.buttonClicked.connect(lambda _button: setattr(self, '_unknown_service_type', None))
        form.addRow('', radio_row); self._basic_rows.append(radio_row)
        self.oracle_tns_combo = QComboBox(); self.oracle_tns_combo.setEditable(True); self.oracle_tns_combo.addItem(self._base.oracle_tns_alias)
        self.oracle_tns_label = QLabel('网络服务名称:'); form.addRow(self.oracle_tns_label, self.oracle_tns_combo)
        role_row = QWidget(); roles = QHBoxLayout(role_row); roles.setContentsMargins(0, 0, 0, 0)
        self.oracle_role_combo = self._combo([('默认', 'default'), ('SYSDBA', 'sysdba'), ('SYSOPER', 'sysoper')], self._base.oracle_role)
        self.oracle_os_auth_check = QCheckBox('操作系统验证'); self.oracle_os_auth_check.setChecked(self._base.oracle_os_auth)
        roles.addWidget(self.oracle_role_combo); roles.addWidget(self.oracle_os_auth_check); form.addRow('角色:', role_row)
        self.user_edit = QLineEdit(self._base.username); self.user_edit.setObjectName('connectionUsername')
        form.addRow('用户名:', self.user_edit)
        self.password_edit = QLineEdit(self._base.password); self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setObjectName('connectionPassword')
        password_row = QWidget(); password_layout = QVBoxLayout(password_row); password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.addWidget(self.password_edit)
        self.remember_check = QCheckBox('保存密码'); self.remember_check.setChecked(bool(self._base.password) if self._original else True)
        self.remember_check.toggled.connect(self._remember_changed); password_layout.addWidget(self.remember_check)
        form.addRow('密码:', password_row)
        # The reference centers the field column rather than stretching every
        # field across the settings pane. Labels sit to its left.
        for field in (self.name_edit, self.oracle_type_combo, self.host_edit, self.port_spin,
                      self.oracle_service_edit, radio_row, self.oracle_tns_combo, role_row,
                      self.user_edit, password_row):
            field.setFixedWidth(300)
        self.oracle_role_combo.setFixedWidth(92)
        self._center_form(layout, form)
        self.support_label = QLabel(); self.support_label.setObjectName('oracleSupportState'); self.support_label.setWordWrap(True)
        layout.addWidget(self.support_label); layout.addStretch(1)
        for widget in (self.oracle_type_combo, self.oracle_role_combo): widget.currentIndexChanged.connect(self._state_changed)
        for widget in (self.oracle_os_auth_check, self.oracle_service_radio, self.oracle_sid_radio): widget.toggled.connect(self._state_changed)
        for widget in (self.host_edit, self.user_edit, self.oracle_service_edit, self.port_spin): widget.textChanged.connect(self._state_changed)

    def _build_advanced(self):
        self.advanced_tab, layout, _diagram = self._page('高级')
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        directory, self.oracle_settings_edit, browse = self._file_field(self._base.oracle_settings_directory, '设置位置')
        directory.setFixedWidth(300)
        browse.clicked.disconnect(); browse.clicked.connect(self._choose_settings_directory)
        self.oracle_settings_edit.setPlaceholderText('OpenNavicat/Oracle/<连接名称>')
        form.addRow('设置位置:', directory)
        self._center_form(layout, form, 100)
        self._separator(layout)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.oracle_auto_connect_check = QCheckBox('自动连接'); self.oracle_auto_connect_check.setChecked(self._base.oracle_auto_connect)
        self.oracle_auto_connect_check.setFixedWidth(300)
        form.addRow('', self.oracle_auto_connect_check)
        self.oracle_session_limit_check = QCheckBox('限制连接会话:'); self.oracle_session_limit_check.setChecked(self._base.oracle_session_limit_enabled)
        self.oracle_session_limit_spin = QSpinBox(); self.oracle_session_limit_spin.setRange(0, 2147483647); self.oracle_session_limit_spin.setValue(self._base.oracle_session_limit)
        self.oracle_session_limit_spin.lineEdit().setPlaceholderText('10')
        self.oracle_session_limit_spin.setFixedWidth(80)
        limit_row = QWidget(); limit_row.setFixedWidth(300); limit_layout = QHBoxLayout(limit_row); limit_layout.setContentsMargins(0, 0, 0, 0)
        limit_layout.addWidget(self.oracle_session_limit_check); limit_layout.addWidget(self.oracle_session_limit_spin); limit_layout.addStretch(1)
        form.addRow('', limit_row)
        self._center_form(layout, form, 100)
        self._separator(layout)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.oracle_keepalive_check = QCheckBox('保持连接间隔（秒）:'); self.oracle_keepalive_check.setChecked(self._base.oracle_keepalive_enabled)
        self.oracle_keepalive_spin = QSpinBox(); self.oracle_keepalive_spin.setRange(0, 2147483647); self.oracle_keepalive_spin.setValue(self._base.oracle_keepalive_seconds)
        self.oracle_keepalive_spin.setFixedWidth(80)
        keepalive_row = QWidget(); keepalive_row.setFixedWidth(300); keepalive_layout = QHBoxLayout(keepalive_row); keepalive_layout.setContentsMargins(0, 0, 0, 0)
        keepalive_layout.addWidget(self.oracle_keepalive_check); keepalive_layout.addWidget(self.oracle_keepalive_spin); keepalive_layout.addStretch(1)
        form.addRow('', keepalive_row)
        self.oracle_charset_combo = self._combo([('自动', ''), *[(value, value) for value in ORACLE_CHARACTER_SETS]], self._base.oracle_client_charset)
        self.oracle_charset_combo.setFixedWidth(280)
        form.addRow('客户端字符集:', self.oracle_charset_combo)
        query, self.oracle_initial_query_edit, edit_button = self._file_field(self._initial_query.replace('\n', ' '), '初始查询')
        query.setFixedWidth(300)
        edit_button.clicked.disconnect(); edit_button.clicked.connect(self._edit_initial_query)
        self.oracle_initial_query_edit.textChanged.connect(self._query_changed); form.addRow('初始查询:', query)
        self._center_form(layout, form, 100); layout.addStretch(1)
        for widget in (self.oracle_auto_connect_check, self.oracle_session_limit_check, self.oracle_keepalive_check): widget.toggled.connect(self._state_changed)
        for widget in (self.oracle_settings_edit,): widget.textChanged.connect(self._state_changed)
        self.oracle_charset_combo.currentIndexChanged.connect(self._state_changed)

    def _build_databases(self):
        self.databases_tab, layout, _diagram = self._page('数据库')
        self.oracle_custom_database_check = QCheckBox('使用自定义数据库列表'); self.oracle_custom_database_check.setChecked(self._base.oracle_custom_database_list_enabled)
        layout.addWidget(self.oracle_custom_database_check)
        self.oracle_database_table = QTableWidget(0, 3); self.oracle_database_table.setHorizontalHeaderLabels(['', '名称', '自动打开'])
        self.oracle_database_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.oracle_database_table.verticalHeader().hide(); layout.addWidget(self.oracle_database_table, 1)
        self.oracle_database_table.setObjectName('oracleCustomDatabaseList')
        self._retained_databases = deepcopy(self._base.oracle_custom_database_list)
        try:
            for entry in self._retained_databases:
                if (type(entry) is not dict or set(entry) != {'active', 'name', 'auto_open'}
                        or type(entry['active']) is not bool or type(entry['name']) is not str or type(entry['auto_open']) is not bool):
                    raise ValueError()
                self._add_database_row(entry)
        except (TypeError, ValueError): self._draft_error = '自定义数据库列表包含无法表示的设置；原始草稿未改动。'
        buttons = QHBoxLayout()
        self.oracle_database_add = QPushButton('＋'); self.oracle_database_remove = QPushButton('－'); self.oracle_database_refresh = QPushButton('刷新')
        self.oracle_database_add.clicked.connect(self._add_database); self.oracle_database_remove.clicked.connect(self._remove_database)
        self.oracle_database_refresh.setEnabled(False); self.oracle_database_refresh.setToolTip('Oracle 自定义数据库列表获取尚未实现。')
        for button in (self.oracle_database_add, self.oracle_database_remove, self.oracle_database_refresh): buttons.addWidget(button)
        buttons.addStretch(1); layout.addLayout(buttons)
        self.oracle_custom_database_check.toggled.connect(self._state_changed)

    def _build_ssh(self):
        self.ssh_tab, layout, self.ssh_diagram = self._page('SSH')
        self.ssh_enabled_check = QCheckBox('使用 SSH 隧道'); self.ssh_enabled_check.setChecked(self._base.ssh_enabled); layout.addWidget(self.ssh_enabled_check)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ssh_host_edit = QLineEdit(self._base.ssh_host); self.ssh_port_spin = _PortSpinBox(); self.ssh_port_spin.setValue(self._base.ssh_port)
        self.ssh_user_edit = QLineEdit(self._base.ssh_username)
        self.ssh_auth_combo = self._combo([('密码', 'password'), ('公钥', 'key'), ('密码和公钥', 'password+key')], self._base.ssh_auth_method)
        for label, field in [('主机:', self.ssh_host_edit), ('端口:', self.ssh_port_spin), ('用户名:', self.ssh_user_edit), ('验证方法:', self.ssh_auth_combo)]: form.addRow(label, field)
        self.ssh_password_edit = QLineEdit(self._base.ssh_password); self.ssh_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_ssh_check = QCheckBox('保存密码'); self.remember_ssh_check.setChecked(bool(self._base.ssh_password) if self._original else True)
        self.ssh_remember_check = self.remember_ssh_check
        self.ssh_key_field, self.ssh_key_edit, self.ssh_key_browse = self._file_field(self._base.ssh_key_file, '私钥')
        self.ssh_key_passphrase_edit = QLineEdit(self._base.ssh_key_passphrase); self.ssh_key_passphrase_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_ssh_key_check = QCheckBox('保存通行短语'); self.remember_ssh_key_check.setChecked(bool(self._base.ssh_key_passphrase))
        self.ssh_key_remember_check = self.remember_ssh_key_check
        self._ssh_password_rows, self._ssh_key_rows = [], []
        for label, field in [('密码:', self.ssh_password_edit), ('', self.remember_ssh_check)]:
            text = QLabel(label); form.addRow(text, field); self._ssh_password_rows.append((text, field))
        for label, field in [('私钥:', self.ssh_key_field), ('通行短语:', self.ssh_key_passphrase_edit), ('', self.remember_ssh_key_check)]:
            text = QLabel(label); form.addRow(text, field); self._ssh_key_rows.append((text, field))
        self.ssh_compression_check = QCheckBox(self); self.ssh_compression_check.setChecked(self._base.ssh_compression); self.ssh_compression_check.hide()
        self.ssh_pin_label = QLabel(self); self.ssh_pin_label.hide()
        for field in (self.ssh_host_edit, self.ssh_user_edit, self.ssh_password_edit,
                      self.ssh_key_field, self.ssh_key_passphrase_edit): field.setFixedWidth(300)
        self.ssh_port_spin.setMinimumWidth(80); self.ssh_auth_combo.setFixedWidth(112)
        self._center_form(layout, form); layout.addStretch(1)
        self.ssh_enabled_check.toggled.connect(self._ssh_changed); self.ssh_auth_combo.currentIndexChanged.connect(self._ssh_changed)
        self.ssh_host_edit.textChanged.connect(self._ssh_endpoint_changed); self.ssh_port_spin.valueChanged.connect(self._ssh_endpoint_changed)

    def _build_notes(self):
        self.notes_tab, layout, _diagram = self._page('备注')
        layout.addWidget(QLabel('备注:'))
        self.oracle_notes_edit = QPlainTextEdit(self._base.oracle_notes); layout.addWidget(self.oracle_notes_edit, 1)
        layout.addWidget(QLabel('* 更改将应用于所有连接配置文件。'))

    def _remember_changed(self, checked):
        if not checked: self.password_edit.clear()
        self._state_changed()

    def _ssh_changed(self, *_):
        self._ssh_state_changed(); self._state_changed()

    def _toggle_profiles(self, visible):
        self.profile_pane.setVisible(visible)
        if self.isVisible(): self._fit_reference_frame()

    def _state_changed(self, *_):
        if self._loading: return
        basic = self.oracle_type_combo.currentData() == 'basic'
        try:
            if basic: self.port_spin.value()
            port_valid = True
        except ValueError:
            port_valid = False
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(port_valid and not self._testing)
        self.test_button.setEnabled(port_valid and not self._testing)
        for row in self._basic_rows: self.general_form.setRowVisible(row, basic)
        self.general_form.setRowVisible(self.oracle_tns_label, not basic)
        self.oracle_service_label.setText('SID:' if self.oracle_sid_radio.isChecked() else '服务名称:')
        password = not self.oracle_os_auth_check.isChecked()
        self.user_edit.setVisible(password); self.password_edit.setVisible(password and self.remember_check.isChecked())
        self.remember_check.setEnabled(password)
        self.oracle_session_limit_spin.setEnabled(self.oracle_session_limit_check.isChecked())
        self.oracle_keepalive_spin.setEnabled(self.oracle_keepalive_check.isChecked())
        custom = self.oracle_custom_database_check.isChecked()
        self.oracle_database_table.setEnabled(custom); self.oracle_database_add.setEnabled(custom); self.oracle_database_remove.setEnabled(custom)
        try:
            from opennavicat.oracle_session import validate_oracle_execution
            draft = self._draft_config()
            # Blank required fields are normal on a new form. Show support
            # limitations here; Test validates the actual captured values.
            validate_oracle_execution(replace(draft, host=draft.host or 'validation.invalid',
                username=draft.username or 'VALIDATION',
                oracle_service_identifier=draft.oracle_service_identifier or 'VALIDATION'))
            self.support_label.clear(); self.support_label.hide()
        except ImportError:
            self.support_label.setText('Oracle 专用适配器尚未就绪；设置可以保存为草稿。'); self.support_label.show()
        except ValueError as error:
            self.support_label.setText(str(error)); self.support_label.show()

    def _database_rows(self):
        if self._draft_error: raise ValueError(self._draft_error)
        return [dict(active=self.oracle_database_table.item(row, 0).checkState() == Qt.CheckState.Checked,
            name=self.oracle_database_table.item(row, 1).text(),
            auto_open=self.oracle_database_table.item(row, 2).checkState() == Qt.CheckState.Checked)
            for row in range(self.oracle_database_table.rowCount())]

    def _draft_config(self):
        return replace(self._base, name=self.name_edit.text().strip(), driver='oracle', database='',
            host=self.host_edit.text().strip(),
            port=self.port_spin.value() if self.oracle_type_combo.currentData() == 'basic' else self.port_spin.draftValue(),
            username=self.user_edit.text(),
            password=self.password_edit.text() if self.remember_password else '',
            oracle_connection_type=self.oracle_type_combo.currentData(),
            oracle_service_type=self._unknown_service_type or ('sid' if self.oracle_sid_radio.isChecked() else 'service'),
            oracle_service_identifier=self.oracle_service_edit.text().strip(), oracle_tns_alias=self.oracle_tns_combo.currentText(),
            oracle_role=self.oracle_role_combo.currentData(), oracle_os_auth=self.oracle_os_auth_check.isChecked(),
            oracle_settings_directory=self.oracle_settings_edit.text(), oracle_auto_connect=self.oracle_auto_connect_check.isChecked(),
            oracle_session_limit_enabled=self.oracle_session_limit_check.isChecked(), oracle_session_limit=self.oracle_session_limit_spin.value(),
            oracle_keepalive_enabled=self.oracle_keepalive_check.isChecked(), oracle_keepalive_seconds=self.oracle_keepalive_spin.value(),
            oracle_client_charset=self.oracle_charset_combo.currentData(), oracle_initial_query=self._initial_query,
            oracle_custom_database_list_enabled=self.oracle_custom_database_check.isChecked(), oracle_custom_database_list=self._database_rows(),
            oracle_notes=self.oracle_notes_edit.toPlainText(), ssh_enabled=self.ssh_enabled_check.isChecked(),
            ssh_host=self.ssh_host_edit.text(), ssh_port=self.ssh_port_spin.value(), ssh_username=self.ssh_user_edit.text(),
            ssh_auth_method=self.ssh_auth_combo.currentData(), ssh_password=self.ssh_password_edit.text(),
            ssh_key_file=self.ssh_key_edit.text(), ssh_key_passphrase=self.ssh_key_passphrase_edit.text(),
            ssh_compression=self.ssh_compression_check.isChecked(), ssh_host_key=self._ssh_host_key)

    def _collect_config(self, *, testing=False):
        config = self._draft_config()
        if not testing and not config.name: raise ValueError('请输入连接名称。')
        return config

    @property
    def remember_password(self):
        return self.remember_check.isChecked() and not self.oracle_os_auth_check.isChecked()

    @property
    def remember_ssl_password(self):
        return self.ssl_remember_check.isChecked()

    @staticmethod
    def _probe_session(session, config):
        return session.test_connection(cancel_event=threading.Event())

    def _start_connection_test(self, config):
        self.test_banner.hide()
        self.back_button.setEnabled(False)
        for diagram in self._diagrams:
            diagram.connection_succeeded = False; diagram.update()
        super()._start_connection_test(config)

    def _display_test_result(self, success, message):
        self.back_button.setEnabled(True)
        if success:
            self.test_status.setText('✓  连接成功  服务器版本: ' + message[:512])
            self.test_banner.setStyleSheet('#oracleConnectionTestBanner { background: rgba(76, 175, 80, 45); }')
        else:
            # A failure's reference presentation has not been established.
            self.test_status.setText('连接失败')
            self.test_banner.setStyleSheet('')
        self.test_status.setToolTip(message[:512])
        for diagram in self._diagrams:
            diagram.connection_succeeded = success; diagram.update()
        self.test_banner.show()
        self._state_changed()

    def _copy_test_result(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.test_status.text())

    def test_connection(self):
        if self._testing: return
        try:
            from opennavicat.oracle_session import validate_oracle_execution
            config = self._collect_config(testing=True)
            validate_oracle_execution(config)
        except ImportError:
            QMessageBox.warning(self, '测试连接', 'Oracle 专用适配器尚未就绪。'); return
        except ValueError as error:
            QMessageBox.warning(self, '测试连接', str(error)); return
        if not config.password:
            password, ok = QInputDialog.getText(self, '连接密码',
                '输入连接 "' + config.name + '" 的密码。', QLineEdit.EchoMode.Password)
            if not ok: return
            config = replace(config, password=password)
        self._start_connection_test(config)

    def _go_back(self):
        if self._testing: return
        self.back_requested = True; self.reject()

    def _choose_settings_directory(self):
        directory = QFileDialog.getExistingDirectory(self, '设置位置', self.oracle_settings_edit.text())
        if directory: self.oracle_settings_edit.setText(directory)

    def _query_changed(self, value):
        if not self._loading: self._initial_query = value; self._state_changed()

    def _edit_initial_query(self):
        editor = QDialog(self); editor.setWindowTitle('初始查询'); layout = QVBoxLayout(editor)
        text = QPlainTextEdit(self._initial_query); layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(editor.accept); buttons.rejected.connect(editor.reject); layout.addWidget(buttons)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._initial_query = text.toPlainText()
            with QSignalBlocker(self.oracle_initial_query_edit): self.oracle_initial_query_edit.setText(self._initial_query.replace('\n', ' '))
            self._state_changed()

    def _add_database_row(self, entry):
        row = self.oracle_database_table.rowCount(); self.oracle_database_table.insertRow(row)
        for column, key in ((0, 'active'), (2, 'auto_open')):
            item = QTableWidgetItem(); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if entry[key] else Qt.CheckState.Unchecked)
            self.oracle_database_table.setItem(row, column, item)
        self.oracle_database_table.setItem(row, 1, QTableWidgetItem(entry['name']))

    def _add_database(self):
        value, ok = QInputDialog.getText(self, '数据库', '名称:')
        if ok and value: self._add_database_row(dict(active=True, name=value, auto_open=False))

    def _remove_database(self):
        row = self.oracle_database_table.currentRow()
        if row >= 0: self.oracle_database_table.removeRow(row)
