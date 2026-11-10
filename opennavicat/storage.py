"""Local profiles, OS-keychain secrets, and query history."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .database import ConnectionConfig


def data_directory() -> Path:
    override = os.environ.get("OPENNAVICAT_DATA_DIR")
    path = Path(override) if override else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "workspace.json"
        self.state = {"connections": [], "favorites": [], "drafts": [], "remembered_passwords": [], "remembered_secrets": {}}
        if self.path.exists():
            try:
                self.state.update(json.loads(self.path.read_text("utf-8")))
            except (ValueError, OSError) as exc:
                raise RuntimeError(f"Could not read workspace {self.path}: {exc}") from exc
        self.passwords: dict[str, str] = {}
        self.secrets: dict[tuple[str, str], str] = {}
        self.history_path = self.directory / "history.sqlite3"
        with sqlite3.connect(self.history_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, created TEXT, connection_id TEXT, connection_name TEXT, sql TEXT, elapsed REAL, status TEXT)")
        try:
            self.history_path.chmod(0o600)
        except OSError:
            pass

    def flush(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.path)

    def connections(self) -> list[ConnectionConfig]:
        return [ConnectionConfig(**item) for item in self.state["connections"]]

    SECRET_FIELDS = ("password", "ssh_password", "ssh_key_passphrase", "ssl_key_password")

    @staticmethod
    def _secret_key(connection_id, field):
        return connection_id if field == "password" else f"{connection_id}:{field}"

    def secret(self, config: ConnectionConfig, field: str = "password") -> str:
        if field not in self.SECRET_FIELDS:
            raise ValueError("Unknown connection secret field")
        supplied = getattr(config, field)
        if supplied:
            return supplied
        cache = self.passwords if field == "password" else self.secrets
        key = config.id if field == "password" else (config.id, field)
        if key in cache:
            return cache[key]
        remembered = (config.id in self.state["remembered_passwords"] if field == "password"
                      else field in self.state["remembered_secrets"].get(config.id, []))
        if remembered:
            try:
                import keyring
                value = keyring.get_password("OpenNavicat", self._secret_key(config.id, field)) or ""
                cache[key] = value
                return value
            except Exception:
                return ""
        return ""

    def hydrate(self, config: ConnectionConfig) -> ConnectionConfig:
        """Return a copy with supplied/process/keychain secrets, never write them."""
        return replace(config, **{field: self.secret(config, field) for field in self.SECRET_FIELDS})

    def save_connection(self, config: ConnectionConfig, remember: bool = False,
                        remember_ssh: bool = False, remember_ssl: bool = False,
                        remember_ssh_key: bool = False):
        if not config.id:
            config.id = str(uuid.uuid4())
        flags = {"password": remember, "ssh_password": remember_ssh,
                 "ssh_key_passphrase": remember_ssh_key, "ssl_key_password": remember_ssl}
        # Complete requested keychain writes before changing durable profile state.
        # Backend failures remain errors; credentials never fall back to JSON.
        written = []
        try:
            for field in self.SECRET_FIELDS:
                value = getattr(config, field)
                if flags[field] and value:
                    import keyring
                    key = self._secret_key(config.id, field)
                    try:
                        previous = keyring.get_password("OpenNavicat", key)
                    except Exception:
                        previous = None
                    keyring.set_password("OpenNavicat", key, value)
                    written.append((key, previous))
        except Exception:
            for key, previous in reversed(written):
                try:
                    if previous is None:
                        keyring.delete_password("OpenNavicat", key)
                    else:
                        keyring.set_password("OpenNavicat", key, previous)
                except Exception:
                    pass
            raise
        for field in self.SECRET_FIELDS:
            value = getattr(config, field)
            cache = self.passwords if field == "password" else self.secrets
            key = config.id if field == "password" else (config.id, field)
            cache[key] = value
            if not (flags[field] and value):
                try:
                    import keyring
                    keyring.delete_password("OpenNavicat", self._secret_key(config.id, field))
                except Exception:
                    pass
        self.state["remembered_passwords"] = [identifier for identifier in self.state["remembered_passwords"] if identifier != config.id]
        if remember and config.password:
            self.state["remembered_passwords"].append(config.id)
        remembered = [field for field in self.SECRET_FIELDS if field != "password" and flags[field] and getattr(config, field)]
        if remembered:
            self.state["remembered_secrets"][config.id] = remembered
        else:
            self.state["remembered_secrets"].pop(config.id, None)
        value = asdict(config)
        for field in self.SECRET_FIELDS:
            value[field] = ""
        self.state["connections"] = [c for c in self.state["connections"] if c["id"] != config.id] + [value]
        self.flush()

    def delete_connection(self, connection_id: str):
        for field in self.SECRET_FIELDS:
            try:
                import keyring
                keyring.delete_password("OpenNavicat", self._secret_key(connection_id, field))
            except Exception:
                pass
            self.secrets.pop((connection_id, field), None)
        self.passwords.pop(connection_id, None)
        self.state["remembered_passwords"] = [identifier for identifier in self.state["remembered_passwords"] if identifier != connection_id]
        self.state["remembered_secrets"].pop(connection_id, None)
        self.state["connections"] = [c for c in self.state["connections"] if c["id"] != connection_id]
        self.flush()

    def record(self, config, sql, elapsed, status):
        with sqlite3.connect(self.history_path) as db:
            db.execute("INSERT INTO history(created, connection_id, connection_name, sql, elapsed, status) VALUES (?, ?, ?, ?, ?, ?)",
                       (datetime.now(timezone.utc).isoformat(timespec="seconds"), config.id, config.name, sql, elapsed, status))
            db.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 2000)")

    def history(self, search=""):
        with sqlite3.connect(self.history_path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM history WHERE sql LIKE ? OR connection_name LIKE ? ORDER BY id DESC LIMIT 200", (f"%{search}%", f"%{search}%"))]

    def add_favorite(self, name, sql, connection_id):
        self.state["favorites"].append({"name": name, "sql": sql, "connection_id": connection_id})
        self.flush()


def create_sample(directory: Path) -> Path:
    path = directory / "sample-workspace.sqlite3"
    with sqlite3.connect(path) as db:
        if db.execute("SELECT name FROM sqlite_master WHERE name='customers'").fetchone():
            return path
        db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, city TEXT, joined_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT, price REAL NOT NULL CHECK(price >= 0), stock INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL DEFAULT 1, status TEXT DEFAULT 'paid', ordered_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX orders_customer_idx ON orders(customer_id);
            CREATE VIEW order_summary AS SELECT o.id, c.name AS customer, p.name AS product, o.quantity, round(p.price*o.quantity,2) AS total, o.status, o.ordered_at FROM orders o JOIN customers c ON c.id=o.customer_id JOIN products p ON p.id=o.product_id;
        """)
        names = [("Alex Morgan", "alex@example.com", "San Francisco"), ("Lin Chen", "lin@example.com", "Shanghai"), ("Sam Taylor", "sam@example.com", "London"), ("Yuki Tanaka", "yuki@example.com", "Tokyo"), ("Olivia Wilson", "olivia@example.com", "Berlin"), ("Noah Kim", "noah@example.com", "Seoul")]
        db.executemany("INSERT INTO customers(name,email,city) VALUES(?,?,?)", names)
        db.executemany("INSERT INTO products(name,category,price,stock) VALUES(?,?,?,?)", [("Mechanical keyboard", "Accessories", 129, 42), ("Studio headphones", "Audio", 249, 18), ("USB-C hub", "Accessories", 69, 85), ("Desk lamp", "Workspace", 89, 23), ("4K monitor", "Displays", 499, 12), ("Notebook", "Workspace", 19, 120)])
        db.executemany("INSERT INTO orders(customer_id,product_id,quantity,status) VALUES(?,?,?,?)", [(i % 6 + 1, (i * 7 + 2) % 6 + 1, i % 3 + 1, ["paid", "shipped", "delivered"][i % 3]) for i in range(1, 37)])
    return path
