"""Native connection-type selection and memory-only URI handoff.

The vendor catalog records the entries observed in reference 18.0.5 (N18-05).
An alias offers ordinary driver settings, not unimplemented vendor authentication.
This dialog never connects, opens a database, or writes a workspace/credential.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QPushButton,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import database
from .icons import icon, provider_icon, toggle_icon


DRIVER_LABELS = {
    "mysql": "MySQL", "postgresql": "PostgreSQL", "sqlserver": "SQL Server",
    "oracle": "Oracle", "sqlite": "SQLite", "mariadb": "MariaDB",
    "mongodb": "MongoDB", "redis": "Redis", "snowflake": "Snowflake",
}

# Compatibility aliases are explicitly separated from direct driver entries.
# Reference inspection enumerated these names, without authenticating to services.
VENDOR_ALIASES = {
    "Alibaba Cloud": (
        ("ApsaraDB RDS for MySQL", "mysql"),
        ("ApsaraDB RDS for PostgreSQL", "postgresql"),
        ("ApsaraDB RDS for SQL Server", "sqlserver"),
        ("ApsaraDB for MongoDB", "mongodb"), ("ApsaraDB for Redis", "redis"),
        ("ApsaraDB for OceanBase (MySQL Mode)", "mysql"),
        ("PolarDB for MySQL", "mysql"), ("PolarDB for PostgreSQL", "postgresql"),
        ("PolarDB for Xscale", None),
    ),
    "Huawei": (
        ("Cloud RDS for MySQL", "mysql"), ("Cloud RDS for PostgreSQL", "postgresql"),
        ("Cloud RDS for SQL Server", "sqlserver"),
        ("Cloud Document Database Service", "mongodb"),
        ("Cloud Distributed Cache Service for Redis", "redis"),
    ),
    "Tencent Cloud": tuple((f"TencentDB for {name}", kind) for name, kind in (
        ("MySQL", "mysql"), ("PostgreSQL", "postgresql"), ("SQL Server", "sqlserver"),
        ("MariaDB", "mariadb"), ("MongoDB", "mongodb"), ("Redis", "redis"),
    )),
    "Kingbase": (("KingbaseES", "postgresql"),),
    "PingCAP": (("TiDB", "mysql"),),
    "HighGo": (("IvorySQL", "postgresql"),),
    "OceanBase": (("Community Edition", "mysql"), ("Enterprise Edition (MySQL Mode)", "mysql")),
    "AWS": (
        ("Amazon RDS for MySQL", "mysql"), ("Amazon RDS for PostgreSQL", "postgresql"),
        ("Amazon RDS for SQL Server", "sqlserver"), ("Amazon RDS for Oracle", "oracle"),
        ("Amazon RDS for MariaDB", "mariadb"), ("Amazon Aurora for MySQL", "mysql"),
        ("Amazon Aurora for PostgreSQL", "postgresql"), ("Amazon Redshift", "postgresql"),
        ("Amazon DocumentDB", "mongodb"), ("Amazon ElastiCache for Redis", "redis"),
    ),
    "Fujitsu": (("Enterprise Postgres", "postgresql"),),
    "Google Cloud": (
        ("Cloud SQL for MySQL", "mysql"), ("Cloud SQL for PostgreSQL", "postgresql"),
        ("Cloud SQL for SQL Server", "sqlserver"), ("Google Memorystore (Redis)", "redis"),
    ),
    "Microsoft": (
        ("SQL Server", "sqlserver"), ("Azure SQL Database", "sqlserver"),
        ("Azure Database for MySQL", "mysql"), ("Azure Database for PostgreSQL", "postgresql"),
        ("Azure Database for MariaDB", "mariadb"), ("Azure Cosmos DB for MongoDB", "mongodb"),
        ("Azure Cache for Redis", "redis"), ("Microsoft Garnet", "redis"),
    ),
    "MongoDB Atlas": (("MongoDB Atlas", "mongodb"),),
    "Oracle": (
        ("MySQL", "mysql"), ("Oracle", "oracle"),
        ("Oracle Database Cloud Services", "oracle"), ("Oracle MySQL Cloud Service", "mysql"),
    ),
    "Redis Enterprise Cloud": (("Redis Enterprise Cloud", "redis"),),
}


# These are independently drawn provider identities, not backend compatibility.
# No observed standalone Redis Enterprise provider glyph is available yet.
_PROVIDER_GLYPHS = {
    "Alibaba Cloud": "provider_alibaba", "Huawei": "provider_huawei",
    "Tencent Cloud": "provider_tencent", "Kingbase": "provider_kingbase",
    "PingCAP": "provider_pingcap", "HighGo": "provider_highgo",
    "OceanBase": "provider_oceanbase", "AWS": "provider_aws",
    "Fujitsu": "provider_fujitsu", "Google Cloud": "provider_google",
    "Microsoft": "provider_microsoft", "MongoDB Atlas": "provider_mongodb_atlas",
    "Oracle": "provider_oracle_cloud",
}


@dataclass(frozen=True)
class ConnectionPreset:
    label: str
    kind: str | None
    vendor: str = ""


def supported_driver_kinds() -> frozenset[str]:
    """Read the backend capability registry on demand, including future adapters.

    The fallback only bridges older database modules lacking the registry; it
    does not claim that selecting a catalog card implements a database adapter.
    """
    kinds = getattr(database, "SUPPORTED_DRIVERS", ("sqlite", "postgresql", "mysql", "mariadb"))
    kinds = set(kinds)
    if "oracle" in kinds:
        from importlib.util import find_spec
        if find_spec("oracledb") is None or find_spec("opennavicat.oracle_session") is None:
            kinds.discard("oracle")
    return frozenset(kinds)


def _driver_preset(kind: str, label: str) -> dict:
    result = {"driver": kind, "name": label}
    if kind != "sqlite":
        result.update(host="localhost", port={"postgresql": 5432, "sqlserver": 1433, "redis": 6379,
                                             "oracle": 1521, "mongodb": 27017, "snowflake": 443}.get(kind, 3306))
    if kind == "redis":
        result.update(database="0", ssl_mode="disable")
    elif kind == "sqlserver":
        result["ssl_mode"] = "verify-full"
    elif kind == "mongodb":
        result.update(database="", ssl_mode="disable", mongo_connection_type="standalone",
                      mongo_auth_source="admin", mongo_auth_mechanism="NONE")
    elif kind == "oracle":
        result.update(name="" if label == "Oracle" else label, host="", database="",
                      ssl_mode="disable", oracle_service_identifier="ORCL")
    return result


class ConnectionURIError(ValueError):
    """A safe, user-visible message. Never include the URI or its credentials."""


def _decoded(value: str) -> str:
    try:
        result = unquote(value, encoding="utf-8", errors="strict")
    except UnicodeError:
        raise ConnectionURIError("URI 的百分号编码必须是有效的 UTF-8。") from None
    if any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ConnectionURIError("URI 字段不能包含控制字符。")
    return result


def _mongodb_uri_options(parts, options: dict, result: dict) -> dict:
    """Represent a single-host Mongo URI exactly, or reject it before any I/O.

    This is a deliberately bounded native URI subset. It never resolves SRV,
    reads a PEM, or asks PyMongo to construct a client. Unsupported security or
    topology semantics must be reviewed by the user instead of being dropped.
    """
    supported = {
        "authsource", "authmechanism", "directconnection", "readpreference",
        "retryreads", "retrywrites", "connecttimeoutms", "serverselectiontimeoutms",
        "tls", "ssl", "tlscafile", "tlscrlfile", "tlsinsecure",
        "tlsallowinvalidcertificates", "tlsallowinvalidhostnames",
    }
    if set(options) - supported:
        raise ConnectionURIError("MongoDB URI 包含尚未支持的参数；客户端组合 PEM、SRV、副本集和外部验证尚未完成接入。")

    def boolean(name, default=False):
        value = options.get(name)
        if value is None:
            return default
        if value not in {"true", "false"}:
            raise ConnectionURIError("MongoDB URI 的布尔参数必须是 true 或 false。")
        return value == "true"

    if "directconnection" in options and not boolean("directconnection"):
        raise ConnectionURIError("MongoDB 当前仅支持 directConnection=true 的单机连接。")
    if options.get("readpreference", "primary") != "primary":
        raise ConnectionURIError("MongoDB 当前仅支持 primary 读取偏好。")
    result.update(mongo_read_preference="primary", mongo_retry_reads=boolean("retryreads", True),
                  mongo_retry_writes=boolean("retrywrites", True))

    if "@" in parts.netloc:
        # MongoDB requires reserved userinfo characters to be percent encoded.
        # A single raw ':' separates username and password; additional ones
        # cannot be silently treated as literal password text.
        userinfo = parts.netloc.rsplit("@", 1)[0]
        if any(char in userinfo for char in "$/?#[]@") or userinfo.count(":") > 1:
            raise ConnectionURIError("MongoDB 用户名和密码中的保留字符必须使用百分号编码。")
        if not result["username"]:
            raise ConnectionURIError("MongoDB 密码验证需要非空用户名。")
    elif "authsource" in options or "authmechanism" in options:
        raise ConnectionURIError("MongoDB 验证参数需要同时指定用户名。")
    database = result["database"]
    if any(char in database for char in ' /\\.\"$'):
        raise ConnectionURIError("MongoDB 数据库名称包含不支持的字符。")
    source = options.get("authsource", database or "admin")
    if not source or any(char in source for char in ' /\\.\"') or ("$" in source and source != "$external"):
        raise ConnectionURIError("MongoDB 验证数据库名称无效。")
    mechanism = options.get("authmechanism", "DEFAULT" if result["username"] else "NONE")
    if "authmechanism" in options and mechanism not in {"SCRAM-SHA-1", "SCRAM-SHA-256"}:
        raise ConnectionURIError("MongoDB URI 的显式验证机制当前仅支持 SCRAM-SHA-1 或 SCRAM-SHA-256。")
    if mechanism not in {"NONE", "DEFAULT", "SCRAM-SHA-1", "SCRAM-SHA-256"}:
        raise ConnectionURIError("此 MongoDB 验证机制尚未完成支持；当前仅支持无验证或 SCRAM 密码验证。")
    if mechanism == "NONE" and result["username"]:
        raise ConnectionURIError("MongoDB URI 不能同时指定凭据与无验证模式。")
    result.update(mongo_auth_source=source, mongo_auth_mechanism=mechanism)

    timeout_values = []
    for name in ("connecttimeoutms", "serverselectiontimeoutms"):
        if name in options:
            value = options[name]
            if not re.fullmatch(r"[0-9]+", value) or len(value) > 11 or not 1000 <= int(value) <= 31536000000 or int(value) % 1000:
                raise ConnectionURIError("MongoDB 超时必须是可精确表示为整秒的正整数毫秒值。")
            timeout_values.append(int(value) // 1000)
    if bool("serverselectiontimeoutms" in options) != bool("connecttimeoutms" in options):
        raise ConnectionURIError("当前设置使用同一个连接和服务器选择超时；请同时指定相等的两个超时。")
    if len(set(timeout_values)) > 1:
        raise ConnectionURIError("当前设置要求 connectTimeoutMS 与 serverSelectionTimeoutMS 相等。")
    if timeout_values:
        result["connect_timeout"] = timeout_values[0]

    if "tls" in options and "ssl" in options:
        raise ConnectionURIError("MongoDB URI 不能同时指定 tls 与其别名 ssl。")
    security_flags = {"tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames"}
    if "tlsinsecure" in options and security_flags.intersection(options) - {"tlsinsecure"}:
        raise ConnectionURIError("tlsInsecure 不能与单独的证书或主机名验证选项同时指定。")
    tls_explicit = "tls" in options or "ssl" in options
    files = {name: options[name] for name in ("tlscafile", "tlscrlfile") if name in options}
    if any(not value for value in files.values()):
        raise ConnectionURIError("MongoDB TLS 文件路径不能为空。")
    if security_flags.intersection(options) and not tls_explicit:
        raise ConnectionURIError("使用 MongoDB TLS 验证策略参数时，请明确设置 tls=true。")
    enabled = boolean("tls", boolean("ssl", bool(files)))
    if not enabled and (files or security_flags.intersection(options)):
        raise ConnectionURIError("tls=false 不能同时指定 TLS 文件或验证策略。")
    insecure = boolean("tlsinsecure")
    invalid_certs = boolean("tlsallowinvalidcertificates", insecure)
    invalid_host = boolean("tlsallowinvalidhostnames", insecure)
    mode = "disable" if not enabled else "require" if invalid_certs else "verify-ca" if invalid_host else "verify-full"
    if files and mode == "require":
        raise ConnectionURIError("CA 与证书撤消列表需要证书链验证，不能与跳过证书验证同时使用。")
    result["ssl_mode"] = mode
    if "tlscafile" in files:
        result["ssl_ca"] = files["tlscafile"]
    if "tlscrlfile" in files:
        result["ssl_crl"] = files["tlscrlfile"]
    return result


def parse_connection_uri(uri: str) -> dict:
    """Parse a supported URI into ConnectionConfig fields, without any I/O.

    Fail closed for unknown, repeated or unrepresentable options. A returned
    password exists only in the caller's memory, never in exception messages.
    SQLite follows SQLAlchemy's three-slash relative/four-slash absolute form.
    Redis indices are decimal strings; rediss defaults to verified TLS.
    """
    if not isinstance(uri, str) or not uri or len(uri) > 32768:
        raise ConnectionURIError("请输入有效的连接 URI（最多 32768 字符）。")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in uri):
        raise ConnectionURIError("URI 中的空格和控制字符必须使用百分号编码。")
    if re.search(r"%(?![0-9A-Fa-f]{2})", uri):
        raise ConnectionURIError("URI 包含无效的百分号编码。")
    if "#" in uri:
        raise ConnectionURIError("连接 URI 不支持片段；字段中的 # 请编码为 %23。")
    try:
        parts = urlsplit(uri)
    except ValueError:
        raise ConnectionURIError("URI 的主机或 IPv6 地址格式无效。") from None
    schemes = {"sqlite": "sqlite", "postgres": "postgresql", "postgresql": "postgresql",
               "mysql": "mysql", "mariadb": "mariadb", "sqlserver": "sqlserver",
               "redis": "redis", "rediss": "redis", "mongodb": "mongodb"}
    if parts.scheme.lower() == "mongodb+srv":
        raise ConnectionURIError("MongoDB SRV 发现尚未实现；请使用 mongodb:// 的单一主机与端口。")
    kind = schemes.get(parts.scheme.lower())
    if not kind:
        raise ConnectionURIError("暂不支持此 URI 协议。可用：sqlite、postgresql、mysql、mariadb、sqlserver、redis、rediss、mongodb。")
    if not uri.lower().startswith(parts.scheme.lower() + "://"):
        raise ConnectionURIError("连接 URI 必须使用 协议:// 格式。")
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        raise ConnectionURIError("URI 查询参数必须是有效的 名称=值。") from None
    options = {}
    for key, value in pairs:
        key = key.lower()
        if key in options:
            raise ConnectionURIError("URI 不能重复指定同一个连接参数。")
        if any(ord(char) < 32 or ord(char) == 127 for char in key + value):
            raise ConnectionURIError("URI 参数不能包含控制字符。")
        options[key] = value
    result = _driver_preset(kind, DRIVER_LABELS[kind])
    if kind == "sqlite":
        if parts.netloc or not parts.path.startswith("/"):
            raise ConnectionURIError("SQLite URI 仅支持本地路径：sqlite:///相对路径 或 sqlite:////绝对路径。")
        path = _decoded(parts.path[1:])
        if not path or path.startswith("file:"):
            raise ConnectionURIError("请指定 SQLite 文件路径或 :memory:；嵌套 file: URI 尚未支持。")
        if set(options) - {"mode", "connect_timeout"}:
            raise ConnectionURIError("SQLite URI 包含尚未支持的参数；仅支持 mode 和 connect_timeout。")
        if "mode" in options and options["mode"] not in ("ro", "rwc"):
            raise ConnectionURIError("SQLite URI 的 mode 仅支持 ro（只读）或 rwc（允许创建）。")
        result.update(database=path, read_only=options.get("mode") == "ro")
    else:
        if parts.netloc.count("@") > 1 or not parts.hostname:
            raise ConnectionURIError("请指定单一主机；用户名和密码中的 @ 必须编码为 %40。")
        authority = parts.netloc.rsplit("@", 1)[-1]
        if authority.startswith("[") and not re.fullmatch(r"\[[^\[\]]+\](?::[0-9]+)?", authority):
            raise ConnectionURIError("IPv6 主机必须完整包含在方括号内，后面只能填写端口。")
        if not authority.startswith("[") and ("[" in authority or "]" in authority):
            raise ConnectionURIError("URI 的主机或 IPv6 地址格式无效。")
        host = _decoded(parts.hostname)
        if any(char in host for char in "/\\,@ ") or not host:
            raise ConnectionURIError("URI 仅支持单一 TCP 主机，暂不支持套接字或多主机列表。")
        try:
            port = parts.port
        except ValueError:
            raise ConnectionURIError("端口必须是 1 到 65535 之间的整数。") from None
        if port is not None and not 1 <= port <= 65535:
            raise ConnectionURIError("端口必须是 1 到 65535 之间的整数。")
        # An explicitly empty port is usually a paste error, not a request for a default.
        if parts.netloc.rsplit("@", 1)[-1].endswith(":"):
            raise ConnectionURIError("冒号后必须填写端口。")
        path = parts.path[1:] if parts.path.startswith("/") else parts.path
        if "/" in path:
            raise ConnectionURIError("数据库名称中的 / 必须编码为 %2F。")
        db_name = _decoded(path)
        if kind == "redis":
            if db_name and (not re.fullmatch(r"[0-9]+", db_name) or len(db_name) > 10 or int(db_name) > 2147483647):
                raise ConnectionURIError("Redis 数据库必须是 0 到 2147483647 之间的整数索引。")
            db_name = str(int(db_name or "0"))
        result.update(host=host, port=port or result["port"], database=db_name,
                      username=_decoded(parts.username or ""), password=_decoded(parts.password or ""))
        if kind == "mongodb":
            return _mongodb_uri_options(parts, options, result)
        names = {"sslmode": "ssl_mode", "sslrootcert": "ssl_ca", "sslcert": "ssl_cert",
                 "sslkey": "ssl_key", "sslcrl": "ssl_crl", "sslpassword": "ssl_key_password"}
        if set(options) - (set(names) | {"connect_timeout"}):
            raise ConnectionURIError("URI 包含尚未支持的连接参数；请在设置页中配置或移除该参数。")
        if kind == "sqlserver" and set(options) & {"sslcert", "sslkey", "sslcrl", "sslpassword"}:
            raise ConnectionURIError("SQL Server 尚不支持客户端证书、证书撤消列表或密钥通行短语；URI 只支持 sslrootcert 根证书。")
        modes = {"disable", "require", "verify-ca", "verify-full"}
        if kind not in ("sqlserver", "redis"):
            modes.add("prefer")
        if kind == "postgresql":
            modes.add("allow")
        default_mode = "verify-full" if parts.scheme.lower() == "rediss" else result.get("ssl_mode", "prefer")
        mode = options.get("sslmode", default_mode)
        if mode not in modes:
            raise ConnectionURIError("该数据库不支持此 SSL 模式。")
        if parts.scheme.lower() == "rediss" and mode == "disable":
            raise ConnectionURIError("rediss URI 必须启用 TLS；明文连接请使用 redis 协议。")
        certificate_modes = {"verify-ca", "verify-full"} | ({"require"} if kind == "sqlserver" else set())
        if any(key in options for key in names if key != "sslmode") and mode not in certificate_modes:
            raise ConnectionURIError("证书参数需要 verify-ca 或 verify-full，不能被静默忽略。")
        result["ssl_mode"] = mode
        for name, field in names.items():
            if name != "sslmode" and name in options:
                if not options[name]:
                    raise ConnectionURIError("证书或密钥参数不能为空。")
                result[field] = options[name]
        if bool(result.get("ssl_cert")) != bool(result.get("ssl_key")):
            raise ConnectionURIError("客户端证书与客户端密钥必须同时设置。")
        if result.get("ssl_key_password") and not result.get("ssl_key"):
            raise ConnectionURIError("密钥通行短语需要同时指定客户端密钥。")
    if "connect_timeout" in options:
        value = options["connect_timeout"]
        if not re.fullmatch(r"[0-9]+", value) or len(value) > 8 or not 1 <= int(value) <= 31536000:
            raise ConnectionURIError("连接超时必须为 1 到 31536000 秒的整数。")
        result["connect_timeout"] = int(value)
    return result


class _ConnectionDelegate(QStyledItemDelegate):
    """Compact connection rows and separately sized driver tiles."""

    GRID_WIDTH = 126
    GRID_MIN_HEIGHT = 96
    GRID_ICON_SIZE = 50
    LIST_ROW_HEIGHT = 26
    LIST_ICON_SIZE = 16
    LABEL_PIXEL_SIZE = 14

    @classmethod
    def label_font(cls, font):
        result = QFont(font)
        result.setPixelSize(cls.LABEL_PIXEL_SIZE)
        return result

    @classmethod
    def grid_height(cls, font, labels):
        metrics = QFontMetrics(cls.label_font(font))
        width = cls.GRID_WIDTH - 12
        text_height = max((metrics.boundingRect(
            QRect(0, 0, width, 4096), int(Qt.TextFlag.TextWordWrap), label).height()
            for label in labels), default=0)
        # Keep two label lines available. Longer cloud names grow the row
        # rather than clipping their identity or shrinking the observed font.
        return max(cls.GRID_MIN_HEIGHT, 4 + cls.GRID_ICON_SIZE + 5
                   + max(metrics.height()*2, text_height) + 3)

    @staticmethod
    def _paint_icon(painter, artwork, rect, mode):
        artwork.paint(painter, rect, Qt.AlignmentFlag.AlignCenter, mode, QIcon.State.Off)

    def paint(self, painter, option, index):
        view = option.widget
        grid = isinstance(view, QListView) and view.viewMode() == QListView.ViewMode.IconMode
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text = ""
        styled.icon = QIcon()
        painter.save()
        try:
            style = view.style() if view else QStyle()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, view)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            fg = option.palette.color(option.palette.currentColorGroup() if enabled else QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
            item_icon = index.data(Qt.ItemDataRole.DecorationRole)
            bounds = option.rect
            if grid:
                icon_rect = QRect(bounds.x() + (bounds.width() - self.GRID_ICON_SIZE)//2,
                                  bounds.y() + 4, self.GRID_ICON_SIZE, self.GRID_ICON_SIZE)
                title_rect = bounds.adjusted(6, 4 + self.GRID_ICON_SIZE + 5, -6, -3)
                alignment = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            else:
                icon_rect = QRect(bounds.x() + 6, bounds.y() + (bounds.height() - self.LIST_ICON_SIZE)//2,
                                  self.LIST_ICON_SIZE, self.LIST_ICON_SIZE)
                title_rect = QRect(icon_rect.right() + 8, bounds.y(),
                                   max(0, bounds.right() - icon_rect.right() - 12), bounds.height())
                alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if item_icon and not item_icon.isNull():
                mode = (QIcon.Mode.Disabled if not enabled else
                        QIcon.Mode.Selected if selected else QIcon.Mode.Normal)
                self._paint_icon(painter, item_icon, icon_rect, mode)
            font = self.label_font(option.font)
            painter.setPen(fg)
            painter.setFont(font)
            title = index.data(Qt.ItemDataRole.DisplayRole)
            if grid:
                painter.drawText(title_rect, int(alignment | Qt.TextFlag.TextWordWrap), title)
            else:
                painter.drawText(title_rect, int(alignment),
                    QFontMetrics(font).elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()))
        finally:
            painter.restore()


class ConnectionUriDialog(QDialog):
    """Masked URI input with safe errors and no persistent fields."""

    selectionRequested = Signal(str, dict)

    def __init__(self, parent=None, *, supported_kinds=None):
        super().__init__(parent)
        self._supported = frozenset(supported_kinds) if supported_kinds is not None else supported_driver_kinds()
        self.setWindowTitle("通过 URI 新建连接")
        self.resize(600, 250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        label = QLabel("连接 URI")
        layout.addWidget(label)
        self.uri_edit = QLineEdit()
        self.uri_edit.setObjectName("connectionUri")
        self.uri_edit.setEchoMode(QLineEdit.EchoMode.Password)
        # QLineEdit counts UTF-16 code units and silently truncates on paste.
        # Keep its cap above twice the parser's limit so truncation can never
        # turn an overlong URI into an accepted URI with its TLS suffix lost.
        self.uri_edit.setMaxLength(65538)
        self.uri_edit.setPlaceholderText("粘贴连接 URI（输入内容已遮蔽）")
        self.uri_edit.setAccessibleName("连接 URI，输入已遮蔽")
        label.setBuddy(self.uri_edit)
        layout.addWidget(self.uri_edit)
        note = QLabel("支持 SQLite、PostgreSQL、MySQL、MariaDB、SQL Server、Redis 和 MongoDB URI。\nURI 仅在内存中解析；进入设置页后再确认连接。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error_label = QLabel()
        self.error_label.setObjectName("connectionUriError")
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("URI 错误")
        layout.addWidget(self.error_label)
        layout.addStretch()
        self.buttons = QDialogButtonBox()
        self.cancel_button = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.next_button = self.buttons.addButton("下一步", QDialogButtonBox.ButtonRole.AcceptRole)
        self.next_button.setDefault(True)
        layout.addWidget(self.buttons)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.submit)
        self.uri_edit.textChanged.connect(lambda: self.error_label.clear())

    def submit(self):
        try:
            preset = parse_connection_uri(self.uri_edit.text())
            kind = preset["driver"]
            if kind not in self._supported:
                raise ConnectionURIError(f"{DRIVER_LABELS[kind]} 连接尚未实现，当前无法进入设置。")
        except ConnectionURIError as error:
            self.error_label.setText(str(error))
            return
        self.uri_edit.clear()
        super().accept()
        self.selectionRequested.emit(kind, preset)

    def reject(self):
        self.uri_edit.clear()
        self.error_label.clear()
        super().reject()

    def closeEvent(self, event):
        self.uri_edit.clear()
        super().closeEvent(event)


class ConnectionManagerDialog(QDialog):
    """Choose a driver/observed vendor alias, then hand off to real settings.

    selectionRequested(kind, preset) contains only ConnectionConfig fields and
    is emitted after this dialog accepts. There is deliberately no Store or
    database session here. Consumers retain the existing settings/test lifecycle.
    """

    selectionRequested = Signal(str, dict)

    def __init__(self, parent=None, *, supported_kinds: Iterable[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建连接")
        self.setObjectName("connectionManager")
        self.resize(900, 700)
        self.setMinimumSize(760, 520)
        self._supported = frozenset(supported_kinds) if supported_kinds is not None else supported_driver_kinds()
        self._catalog = tuple(ConnectionPreset(name, kind) for kind, name in DRIVER_LABELS.items()) + tuple(
            ConnectionPreset(label, kind, vendor)
            for vendor, aliases in VENDOR_ALIASES.items() for label, kind in aliases
        )
        self.uri_dialog = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        header = QHBoxLayout()
        new_button = QPushButton("新建连接")
        new_button.setCheckable(True)
        new_button.setChecked(True)
        new_button.setEnabled(False)
        header.addWidget(new_button)
        self.edit_button = QPushButton("编辑连接")
        self.edit_button.setEnabled(False)
        self.edit_button.setToolTip("现有连接请在主窗口连接菜单中编辑；管理器内编辑流程尚未实现。")
        header.addWidget(self.edit_button)
        header.addStretch()
        layout.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.filter_tree = QTreeWidget()
        self.filter_tree.setObjectName("connectionTypeFilters")
        self.filter_tree.setHeaderHidden(True)
        self.filter_tree.setFixedWidth(205)
        self.filter_tree.setIndentation(15)
        self.filter_tree.setIconSize(QSize(16, 16))
        self.filter_tree.setAccessibleName("连接类型及供应商筛选")
        self.all_item = QTreeWidgetItem(self.filter_tree, ["全部连接类型"])
        self.all_item.setData(0, Qt.ItemDataRole.UserRole, ("all", ""))
        types = QTreeWidgetItem(self.filter_tree, ["数据库类型"])
        types.setFlags(types.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.driver_items = {}
        for kind, name in DRIVER_LABELS.items():
            item = QTreeWidgetItem(types, [name])
            item.setData(0, Qt.ItemDataRole.UserRole, ("driver", kind))
            item.setIcon(0, icon(kind, 16))
            self.driver_items[kind] = item
        vendors = QTreeWidgetItem(self.filter_tree, ["供应商"])
        vendors.setFlags(vendors.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.vendor_items = {}
        for vendor in VENDOR_ALIASES:
            item = QTreeWidgetItem(vendors, [vendor])
            item.setData(0, Qt.ItemDataRole.UserRole, ("vendor", vendor))
            provider_name = _PROVIDER_GLYPHS.get(vendor)
            artwork = provider_icon(provider_name, 16) if provider_name else QIcon()
            item.setIcon(0, artwork)
            if artwork.isNull():
                item.setToolTip(0, "供应商图标待对齐。")
            self.vendor_items[vendor] = item
        types.setExpanded(True)
        vendors.setExpanded(True)
        body.addWidget(self.filter_tree)
        content = QVBoxLayout()
        search_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("connectionTypeSearch")
        self.search_edit.setPlaceholderText("搜索连接类型或供应商")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索连接类型或供应商")
        search_bar.addWidget(self.search_edit, 1)
        self.grid_button = QToolButton()
        self.grid_button.setIcon(toggle_icon("grid", 18))
        self.grid_button.setIconSize(QSize(18, 18))
        self.grid_button.setFixedSize(26, 26)
        self.grid_button.setAccessibleName("图标视图")
        self.grid_button.setToolTip("图标视图")
        self.grid_button.setCheckable(True)
        self.list_button = QToolButton()
        self.list_button.setIcon(toggle_icon("list", 18))
        self.list_button.setIconSize(QSize(18, 18))
        self.list_button.setFixedSize(26, 26)
        self.list_button.setAccessibleName("列表视图")
        self.list_button.setToolTip("列表视图")
        self.list_button.setCheckable(True)
        self.view_buttons = QButtonGroup(self)
        self.view_buttons.addButton(self.grid_button)
        self.view_buttons.addButton(self.list_button)
        self.grid_button.setChecked(True)
        search_bar.addWidget(self.grid_button)
        search_bar.addWidget(self.list_button)
        content.addLayout(search_bar)
        self.connection_list = QListWidget()
        self.connection_list.setObjectName("connectionTypeList")
        self.connection_list.setAccessibleName("连接类型")
        self.connection_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.connection_list.setItemDelegate(_ConnectionDelegate(self.connection_list))
        self.connection_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.connection_list.setMovement(QListView.Movement.Static)
        self.connection_list.setSpacing(0)
        self.connection_list.setStyleSheet("QListWidget#connectionTypeList::item { padding: 0; }")
        self.connection_list.setWordWrap(True)
        content.addWidget(self.connection_list, 1)
        self.empty_label = QLabel("未找到匹配的连接类型。")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        content.addWidget(self.empty_label)
        self.count_label = QLabel()
        content.addWidget(self.count_label)
        self.detail_label = QLabel("请选择数据库类型。")
        self.detail_label.setObjectName("connectionTypeDetails")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(44)
        content.addWidget(self.detail_label)
        body.addLayout(content, 1)
        layout.addLayout(body, 1)
        footer = QHBoxLayout()
        self.uri_button = QPushButton("通过 URI 新建连接…")
        footer.addWidget(self.uri_button)
        footer.addStretch()
        self.buttons = QDialogButtonBox()
        self.cancel_button = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.next_button = self.buttons.addButton("下一步", QDialogButtonBox.ButtonRole.AcceptRole)
        self.next_button.setDefault(True)
        self.next_button.setEnabled(False)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        self.search_edit.textChanged.connect(self._refresh)
        self.filter_tree.currentItemChanged.connect(self._refresh)
        self.grid_button.clicked.connect(lambda: self._set_view(True))
        self.list_button.clicked.connect(lambda: self._set_view(False))
        self.connection_list.currentItemChanged.connect(self._selection_changed)
        self.connection_list.itemDoubleClicked.connect(lambda *_: self.choose_selected())
        self.buttons.accepted.connect(self.choose_selected)
        self.buttons.rejected.connect(self.reject)
        self.uri_button.clicked.connect(self.open_uri)
        self._set_view(True)
        self.filter_tree.setCurrentItem(self.all_item)

    def set_supported_kinds(self, kinds: Iterable[str]):
        """Refresh statuses when the host registers another real adapter."""
        self._supported = frozenset(kinds)
        if self.uri_dialog is not None:
            self.uri_dialog._supported = self._supported
        self._refresh()

    def _apply_item_geometry(self):
        grid = self.grid_button.isChecked()
        labels = [self.connection_list.item(row).text() for row in range(self.connection_list.count())]
        height = _ConnectionDelegate.grid_height(self.connection_list.font(), labels)
        size = QSize(_ConnectionDelegate.GRID_WIDTH, height) if grid else QSize(0, _ConnectionDelegate.LIST_ROW_HEIGHT)
        self.connection_list.setGridSize(size if grid else QSize())
        self.connection_list.setIconSize(QSize(50, 50) if grid else QSize(16, 16))
        for row in range(self.connection_list.count()):
            item = self.connection_list.item(row)
            item.setSizeHint(size)
            preset = item.data(Qt.ItemDataRole.UserRole)
            artwork = self._preset_artwork(preset, 50 if grid else 16)
            item.setIcon(artwork)
            missing = bool(preset.vendor and artwork.isNull())
            item.setData(Qt.ItemDataRole.UserRole + 2, missing)
            status = item.data(Qt.ItemDataRole.UserRole + 1)
            note = "\n供应商图标待对齐。" if missing else ""
            item.setToolTip(f"{preset.label}\n{preset.vendor + ' · ' if preset.vendor else ''}{status}{note}")
            item.setData(Qt.ItemDataRole.AccessibleTextRole,
                f"{preset.label}，{preset.vendor}，{status}" + ("，供应商图标待对齐" if missing else ""))

    @staticmethod
    def _preset_artwork(preset, size):
        if not preset.vendor:
            return icon(preset.kind, size)
        provider_name = _PROVIDER_GLYPHS.get(preset.vendor)
        if provider_name is None:
            return QIcon()
        return provider_icon(provider_name, size, driver=preset.kind)

    def _set_view(self, grid: bool):
        self.connection_list.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.connection_list.setWrapping(grid)
        self.grid_button.setChecked(grid)
        self.list_button.setChecked(not grid)
        self._apply_item_geometry()
        self.connection_list.viewport().update()

    def _refresh(self, *_):
        previous = self.connection_list.currentItem()
        selected = previous.data(Qt.ItemDataRole.UserRole) if previous else None
        item = self.filter_tree.currentItem()
        filter_type, filter_value = item.data(0, Qt.ItemDataRole.UserRole) if item and item.data(0, Qt.ItemDataRole.UserRole) else ("all", "")
        terms = self.search_edit.text().casefold().split()
        self.connection_list.clear()
        for preset in self._catalog:
            if filter_type == "all" and preset.vendor:
                # Searching all also discovers vendor aliases; the initial view
                # remains the nine directly observed database types.
                if not terms:
                    continue
            if filter_type == "driver" and (preset.kind != filter_value or preset.vendor):
                continue
            if filter_type == "vendor" and preset.vendor != filter_value:
                continue
            haystack = " ".join((preset.label, preset.vendor, DRIVER_LABELS.get(preset.kind, ""), preset.kind or "")).casefold()
            if any(term not in haystack for term in terms):
                continue
            available = preset.kind in self._supported
            status = ("兼容驱动设置 · 专属功能未验证" if preset.vendor else "可配置连接") if available else "尚未实现"
            if preset.kind is None:
                status = "驱动兼容性待确认 · 尚未实现"
            row = QListWidgetItem(preset.label)
            row.setData(Qt.ItemDataRole.UserRole, preset)
            row.setData(Qt.ItemDataRole.UserRole + 1, status)
            self.connection_list.addItem(row)
            if preset == selected:
                self.connection_list.setCurrentItem(row)
        self._apply_item_geometry()
        count = self.connection_list.count()
        self.count_label.setText(f"{count} 个连接类型")
        self.empty_label.setVisible(not count)
        self._selection_changed()

    def _selection_changed(self, *_):
        item = self.connection_list.currentItem()
        preset = item.data(Qt.ItemDataRole.UserRole) if item else None
        available = preset is not None and preset.kind in self._supported
        self.next_button.setEnabled(available)
        if not preset:
            self.detail_label.setText("请选择数据库类型。")
        elif preset.kind is None:
            self.detail_label.setText(f"{preset.label}：驱动兼容性尚未确认，当前无法创建连接。")
        elif not available:
            self.detail_label.setText(f"{preset.label}：{DRIVER_LABELS[preset.kind]} 驱动尚未实现，当前无法创建连接。")
        elif preset.vendor:
            self.detail_label.setText(f"{preset.vendor} · {preset.label}\n下一步使用 {DRIVER_LABELS[preset.kind]} 常规设置。供应商专属认证和兼容性尚未验证。")
        else:
            self.detail_label.setText(f"{preset.label}\n下一步填写连接设置；此窗口不会连接数据库。")

    def choose_selected(self):
        item = self.connection_list.currentItem()
        preset = item.data(Qt.ItemDataRole.UserRole) if item else None
        if preset and preset.kind in self._supported:
            self._handoff(preset.kind, _driver_preset(preset.kind, preset.label))

    def _handoff(self, kind: str, preset: dict):
        super().accept()
        self.selectionRequested.emit(kind, preset)

    def open_uri(self):
        if self.uri_dialog is not None and self.uri_dialog.isVisible():
            self.uri_dialog.raise_()
            self.uri_dialog.activateWindow()
            return
        self.uri_dialog = ConnectionUriDialog(self, supported_kinds=self._supported)
        self.uri_dialog.selectionRequested.connect(self._handoff)
        self.uri_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.uri_dialog.show()
        self.uri_dialog.uri_edit.setFocus()

    def reject(self):
        if self.uri_dialog is not None:
            self.uri_dialog.reject()
        super().reject()
