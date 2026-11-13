"""Probe the bundled Oracle Thin client without a database or external files."""
from __future__ import annotations

import importlib
from importlib.machinery import EXTENSION_SUFFIXES
from importlib.metadata import version


ORACLE_DRIVER_VERSION = "26.1.0"
ORACLE_RUNTIME_FLAGS = (
    "oracle_adapter_constructed", "oracle_native_extensions_loaded",
    "oracle_parameters_ready", "oracle_thin_mode_ready",
)


def probe_oracle_runtime():
    import oracledb
    from .database import ConnectionConfig, DatabaseSession

    if oracledb.__version__ != ORACLE_DRIVER_VERSION or version("oracledb") != ORACLE_DRIVER_VERSION:
        raise RuntimeError("The bundled Oracle driver does not match its qualified version")
    for name in ("base_impl", "thin_impl"):
        module = importlib.import_module("oracledb." + name)
        if not any(str(getattr(module, "__file__", "")).endswith(suffix)
                   for suffix in EXTENSION_SUFFIXES):
            raise RuntimeError("The bundled Oracle native extensions did not load")

    config = ConnectionConfig("native-smoke-oracle", "Generated Oracle smoke", driver="oracle",
        host="127.0.0.1", port=1521, username="GENERATED_SMOKE", password="generated-probe-only",
        database="", oracle_service_identifier="GENERATED_SERVICE", ssl_mode="disable")
    session = DatabaseSession(config)
    if session.__class__.__module__ != "opennavicat.oracle_session":
        raise RuntimeError("The Oracle factory did not construct its dedicated adapter")
    # ConnectParams and descriptor parsing are public, local client operations.
    # No Connection, DSN alias, wallet, TNS file or network endpoint is opened.
    params = oracledb.ConnectParams(**session._connection_options())
    descriptor = params.get_connect_string()
    parsed = oracledb.ConnectParams()
    parsed.parse_connect_string(descriptor)
    if (parsed.host != config.host or parsed.port != config.port
            or parsed.service_name != config.oracle_service_identifier
            or config.password in descriptor):
        raise RuntimeError("The bundled Oracle native parameter parser did not roundtrip")
    if not oracledb.is_thin_mode():
        raise RuntimeError("The isolated Oracle import probe did not start in Thin mode")
    session.close()
    return {"oracledb": ORACLE_DRIVER_VERSION, **dict.fromkeys(ORACLE_RUNTIME_FLAGS, True)}
