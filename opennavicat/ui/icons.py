"""Scalable, independently painted Qt icons with explicit presentation states.

Artwork coordinates are owned by the two painter modules.  This factory owns
device density, theme-aware disabled colors and QIcon mode/state semantics.
No reference application resources are read at runtime.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from math import ceil, isfinite

from PySide6.QtCore import QRectF, QSize, QThread, QTimer, Qt
from PySide6.QtGui import (
    QColor, QGuiApplication, QIcon, QIconEngine, QPainter, QPalette, QPen, QPixmap,
)

from .action_artwork import ACTION_NAMES, draw_action
from .icon_artwork import PRIMARY_NAMES, draw_primary
from shiboken6 import isValid

# Keep the original public names, including their primary/action distinctions.
NAMES = (
    "connection", "new_query", "table", "view", "function", "other", "user",
    "query", "backup", "automation", "model", "chart", "bi", "index", "trigger", "ai", "folder", "database",
    "sqlite", "postgresql", "mysql", "mariadb", "sqlserver", "redis", "oracle", "mongodb", "snowflake", "redis_data", "open", "design", "add",
    "delete", "import", "export", "refresh", "run", "stop", "save", "format",
    "filter", "list", "er", "info", "navigation", "commit", "rollback",
)
TOOL_NAMES = tuple(sorted(ACTION_NAMES))
_TOOL_ALIASES = {
    "model": "builder", "query_builder": "builder", "code": "snippets",
    "new_query": "snippets", "query": "snippets", "function": "snippets",
    "table": "columns", "view": "columns", "column": "columns",
    "new": "add", "remove": "delete", "prev": "previous",
    "begin": "transaction_begin", "transaction": "transaction_begin",
    "commit": "transaction_commit", "rollback": "transaction_rollback",
    "record_insert": "record_add", "record_remove": "record_delete",
    "record_save": "record_apply", "record_revert": "record_cancel",
    "schema": "database",
}
_DRIVERS = frozenset({"sqlite", "mysql", "postgresql", "sqlserver", "oracle",
                      "mariadb", "mongodb", "redis", "snowflake"})
CONNECTION_ICON_DRIVERS = _DRIVERS

# Display labels and stable semantic keys both resolve here.  Redis Enterprise
# has no accepted artwork yet: it is explicitly unavailable, never a cylinder.
_PROVIDERS = {
    "alibaba": "provider_alibaba", "huawei": "provider_huawei",
    "tencent": "provider_tencent", "kingbase": "provider_kingbase",
    "pingcap": "provider_pingcap", "highgo": "provider_highgo",
    "oceanbase": "provider_oceanbase", "aws": "provider_aws",
    "fujitsu": "provider_fujitsu", "google": "provider_google",
    "microsoft": "provider_microsoft", "mongodb_atlas": "provider_mongodb_atlas",
    "oracle_cloud": "provider_oracle_cloud", "redis_enterprise": None,
}
_PROVIDER_LABELS = {
    "Alibaba Cloud": "alibaba", "Huawei": "huawei", "Tencent Cloud": "tencent",
    "Kingbase": "kingbase", "PingCAP": "pingcap", "HighGo": "highgo",
    "OceanBase": "oceanbase", "AWS": "aws", "Fujitsu": "fujitsu",
    "Google Cloud": "google", "Microsoft": "microsoft", "MongoDB Atlas": "mongodb_atlas",
    "Oracle": "oracle_cloud", "Redis Enterprise Cloud": "redis_enterprise",
}
PROVIDER_NAMES = tuple(_PROVIDERS)
UNOBSERVED_PROVIDER_NAMES = frozenset({"redis_enterprise"})
_TOGGLES = frozenset({"info", "ai", "new_ai", "result_below", "result_beside", "grid", "list", "focus"})


def _gui_app() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError("Icon creation and rendering require the GUI thread")
    return app


def _size(value: int) -> None:
    if type(value) is not int or not 8 <= value <= 256:
        raise ValueError("Icon size must be an integer between 8 and 256")


def _palette_key() -> tuple[int, ...]:
    palette = _gui_app().palette()
    return tuple(palette.color(group, role).rgba()
                 for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Disabled)
                 for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                              QPalette.ColorRole.Button, QPalette.ColorRole.Text,
                              QPalette.ColorRole.ButtonText, QPalette.ColorRole.Highlight))


@dataclass(frozen=True)
class _Spec:
    family: str
    name: str
    connection_state: str | bool | None = None
    phase: int = 0  # sixteenths of a degree
    provider_driver: str | None = None


class _RasterCache:
    """Bound both entry count and estimated RGBA bytes, including animated icons."""
    max_entries = 256
    max_bytes = 32 * 1024 * 1024

    def __init__(self):
        self.items: OrderedDict[tuple, QPixmap] = OrderedDict()
        self.bytes = 0

    def get(self, key):
        pixmap = self.items.get(key)
        if pixmap is not None:
            self.items.move_to_end(key)
            return QPixmap(pixmap)
        return None

    def put(self, key, pixmap):
        cost = pixmap.width() * pixmap.height() * 4
        if cost > self.max_bytes:
            return
        while self.items and (len(self.items) >= self.max_entries or self.bytes + cost > self.max_bytes):
            _, old = self.items.popitem(last=False)
            self.bytes -= old.width() * old.height() * 4
        self.items[key] = QPixmap(pixmap)
        self.bytes += cost

    def clear(self):
        self.items.clear()
        self.bytes = 0


_RASTERS = _RasterCache()

# PySide 6.12 retains constructor engines but does not transfer the Python
# ownership of a virtual clone() return.  Without a strong wrapper reference,
# QIcon.detach() immediately leaves a dangling native engine.  These are live
# owners, not a cache: never evict an engine while a native QIcon still uses it.
# Qt invalidates its wrapper on native deletion; the GUI timer then releases the
# Python wrapper.  No live QIcons are retained by this registry.
_LIVE_CLONES: dict[int, QIconEngine] = {}
_CLONE_SWEEPER: QTimer | None = None


def _sweep_clones():
    _gui_app()
    for ident, engine in tuple(_LIVE_CLONES.items()):
        if not isValid(engine):
            del _LIVE_CLONES[ident]
    if not _LIVE_CLONES and _CLONE_SWEEPER is not None:
        _CLONE_SWEEPER.stop()


def _retain_clone(engine):
    global _CLONE_SWEEPER
    app = _gui_app()
    _sweep_clones()
    _LIVE_CLONES[id(engine)] = engine
    if _CLONE_SWEEPER is None or not isValid(_CLONE_SWEEPER):
        _CLONE_SWEEPER = QTimer(app)
        _CLONE_SWEEPER.setInterval(50)
        _CLONE_SWEEPER.timeout.connect(_sweep_clones)
    _CLONE_SWEEPER.start()


def _render(spec: _Spec, size: QSize, dpr: float, mode, state) -> QPixmap:
    palette = _palette_key()  # Check thread and theme even on cache hits.
    if size.isEmpty() or not isfinite(dpr) or dpr <= 0:
        return QPixmap()
    width, height = ceil(size.width() * dpr), ceil(size.height() * dpr)
    if max(width, height) > 4096:
        raise ValueError("Icon raster exceeds the supported device size")
    disabled = mode == QIcon.Mode.Disabled
    checked = spec.family == "toggle" and state == QIcon.State.On
    key = (spec, width, height, dpr, disabled, checked, palette)
    cached = _RASTERS.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(width, height)
        painter.translate((width - side) / 2, (height - side) / 2)
        units = 32 if spec.family == "primary" else 18
        painter.scale(side / units, side / units)
        if spec.family == "primary":
            kwargs = {"connection_state": spec.connection_state}
            if spec.provider_driver is not None:
                kwargs["provider_driver"] = spec.provider_driver
            draw_primary(painter, spec.name, **kwargs)
        elif spec.family in {"action", "toggle"}:
            colors = {"blue": "#96999d"} if spec.family == "toggle" and not checked else None
            draw_action(painter, spec.name, palette=colors)
        else:
            painter.scale(18 / 16, 18 / 16)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#b4b7bc"), 1.7))
            painter.drawEllipse(QRectF(2, 2, 12, 12))
            pen = QPen(QColor("#565b62"), 1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(QRectF(2, 2, 12, 12), spec.phase, 100 * 16)
        if disabled:
            # SourceAtop blends toward the current background while preserving
            # every painted alpha and hue; Qt never generates a grayscale mode.
            painter.resetTransform()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            background = QColor.fromRgba(palette[0])
            background.setAlphaF(.40)
            painter.fillRect(pixmap.rect(), background)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(dpr)
    _RASTERS.put(key, pixmap)
    return pixmap


class _PaintedIconEngine(QIconEngine):
    def __init__(self, spec: _Spec):
        super().__init__()
        self.spec = spec

    def clone(self):
        result = _PaintedIconEngine(self.spec)
        _retain_clone(result)
        return result

    def key(self):
        return "OpenNavicatPainted18"

    def isNull(self):
        return False

    def actualSize(self, size, mode, state):
        return size

    def pixmap(self, size, mode, state):
        return _render(self.spec, size, 1.0, mode, state)

    def scaledPixmap(self, size, mode, state, scale):
        # Qt 6.8+ passes logical size separately from devicePixelRatio.
        return _render(self.spec, size, scale, mode, state)

    def paint(self, painter, rect, mode, state):
        pixmap = _render(self.spec, rect.size(), painter.device().devicePixelRatioF(), mode, state)
        painter.drawPixmap(rect, pixmap)


def _make(spec: _Spec, size: int) -> QIcon:
    _size(size)
    _gui_app()
    # Scalable engine: nominal size remains a caller hint, not a raster ceiling.
    # QIcon owns the engine; Qt's implicit copies share it until detach/clone.
    return QIcon(_PaintedIconEngine(spec))


def icon(name: str, size: int = 32) -> QIcon:
    """Primary artwork, or an explicit known action; unknown names are errors."""
    if not isinstance(name, str):
        raise ValueError("Unknown workbench icon")
    if name in PRIMARY_NAMES:
        return _make(_Spec("primary", name), size)
    canonical = _TOOL_ALIASES.get(name, name)
    if canonical in ACTION_NAMES:
        return _make(_Spec("action", canonical), size)
    raise ValueError(f"Unknown workbench icon: {name}")


def tool_icon(name: str, size: int = 18) -> QIcon:
    """Thin action artwork; known primary-only names retain their own artwork."""
    if not isinstance(name, str):
        raise ValueError("Unknown toolbar icon")
    canonical = _TOOL_ALIASES.get(name, name)
    if canonical in ACTION_NAMES:
        return _make(_Spec("action", canonical), size)
    if name in PRIMARY_NAMES:
        return _make(_Spec("primary", name), size)
    raise ValueError(f"Unknown toolbar icon: {name}")


def connection_icon(driver: str, size: int = 16, connected: bool = False) -> QIcon:
    """Connection status badge, independent of action enabled/disabled state."""
    if not isinstance(driver, str) or driver not in _DRIVERS or type(connected) is not bool:
        raise ValueError("Unknown driver or invalid connection state")
    state = "connected" if connected else "disconnected"
    return _make(_Spec("primary", driver, state), size)


def database_icon(size: int = 16, *, opened: bool = True) -> QIcon:
    """Opened green / closed gray schema, independent of action availability."""
    if type(opened) is not bool:
        raise ValueError("Invalid database icon state")
    return _make(_Spec("primary", "database", opened), size)


def provider_icon(provider: str, size: int = 16, *, driver: str | None = None) -> QIcon:
    """Resolve one of fourteen declared vendors; unobserved artwork is explicit."""
    if not isinstance(provider, str):
        raise ValueError("Unknown provider icon")
    _size(size)
    _gui_app()
    if driver is not None and (not isinstance(driver, str) or driver not in _DRIVERS):
        raise ValueError("Unknown provider driver")
    key = _PROVIDER_LABELS.get(provider, provider)
    if key.startswith("provider_"):
        key = next((key_ for key_, value in _PROVIDERS.items() if value == key), key)
    if key not in _PROVIDERS:
        raise ValueError(f"Unknown provider icon: {provider}")
    name = _PROVIDERS[key]
    if name is None:
        return QIcon()
    return _make(_Spec("primary", name, provider_driver=driver), size)


def toggle_icon(name: str, size: int = 18) -> QIcon:
    """Checked blue / unchecked gray outlines, separate from disabled mode."""
    if not isinstance(name, str) or name not in _TOGGLES:
        raise ValueError(f"Unknown toggle icon: {name}")
    return _make(_Spec("toggle", name), size)


def busy_icon(phase: float, size: int = 16) -> QIcon:
    """One bounded-density busy-ring frame; phase is in Qt arc degrees."""
    if isinstance(phase, bool) or not isinstance(phase, (int, float)) or not isfinite(phase):
        raise ValueError("Busy icon phase must be finite degrees")
    return _make(_Spec("busy", "busy", phase=round((phase % 360) * 16) % 5760), size)
