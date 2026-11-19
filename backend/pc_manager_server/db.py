from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hosts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 8765,
                    domain_id INTEGER NOT NULL DEFAULT 0,
                    token TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(address, port)
                )
            """)

    def list_hosts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,name,address,port,domain_id,created_at FROM hosts ORDER BY name"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_host(self, host_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
        return dict(row) if row else None

    def add_host(self, name: str, address: str, port: int, domain_id: int) -> tuple[dict[str, Any], str]:
        token = secrets.token_urlsafe(32)
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO hosts(name,address,port,domain_id,token) VALUES(?,?,?,?,?)",
                (name, address, port, domain_id, token),
            )
            host_id = int(cur.lastrowid)
        return self.get_host(host_id) or {}, token

    def delete_host(self, host_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM hosts WHERE id=?", (host_id,))
            return cur.rowcount > 0
