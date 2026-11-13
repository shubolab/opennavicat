"""Exercise shipped Oracle extension imports without opening a database."""
import importlib
from pathlib import Path

import pytest


def test_actual_native_oracle_probe_roundtrips_parameters_without_connecting(monkeypatch):
    import oracledb
    from opennavicat.oracle_probe import probe_oracle_runtime

    def forbidden(*_args, **_kwargs):
        pytest.fail("A packaged Oracle import probe must not open a database")

    monkeypatch.setattr(oracledb, "connect", forbidden)
    result = probe_oracle_runtime()
    assert result["oracledb"] == "26.1.0"
    for key in ("oracle_adapter_constructed", "oracle_native_extensions_loaded",
                "oracle_parameters_ready", "oracle_thin_mode_ready"):
        assert result[key] is True
    assert all(Path(importlib.import_module("oracledb." + name).__file__).is_file()
               for name in ("base_impl", "thin_impl"))
