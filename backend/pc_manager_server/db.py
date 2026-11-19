from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
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
                    shutdown_requested_at REAL,
                    shutdown_boot_time REAL,
                    UNIQUE(address, port)
                )
            """)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(hosts)").fetchall()
            }
            if "shutdown_requested_at" not in columns:
                conn.execute("ALTER TABLE hosts ADD COLUMN shutdown_requested_at REAL")
            if "shutdown_boot_time" not in columns:
                conn.execute("ALTER TABLE hosts ADD COLUMN shutdown_boot_time REAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS manager_sessions (
                    token_hash TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_manager_sessions_expires "
                "ON manager_sessions(expires_at)"
            )

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

    def update_host(
        self,
        host_id: int,
        name: str,
        address: str,
        port: int,
        domain_id: int,
    ) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE hosts
                SET name=?, address=?, port=?, domain_id=?,
                    shutdown_requested_at=NULL, shutdown_boot_time=NULL
                WHERE id=?
                """,
                (name, address, port, domain_id, host_id),
            )
            return cur.rowcount > 0

    def delete_host(self, host_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM hosts WHERE id=?", (host_id,))
            return cur.rowcount > 0

    @staticmethod
    def _session_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(self, token: str, expires_at: float) -> None:
        token_hash = self._session_hash(token)
        now = time.time()
        with self.connect() as conn:
            conn.execute("DELETE FROM manager_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT OR REPLACE INTO manager_sessions(token_hash, expires_at, created_at) "
                "VALUES(?,?,?)",
                (token_hash, float(expires_at), now),
            )

    def get_session_expiry(self, token: str) -> float | None:
        token_hash = self._session_hash(token)
        now = time.time()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM manager_sessions WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            expires_at = float(row["expires_at"])
            if expires_at <= now:
                conn.execute(
                    "DELETE FROM manager_sessions WHERE token_hash=?",
                    (token_hash,),
                )
                return None
            return expires_at

    def delete_session(self, token: str) -> None:
        token_hash = self._session_hash(token)
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM manager_sessions WHERE token_hash=?",
                (token_hash,),
            )

    def mark_shutdown_requested(
        self, host_id: int, requested_at: float, boot_time: float | None
    ) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE hosts SET shutdown_requested_at=?, shutdown_boot_time=? WHERE id=?",
                (float(requested_at), boot_time, host_id),
            )
            return cur.rowcount > 0

    def clear_shutdown_requested(self, host_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE hosts SET shutdown_requested_at=NULL, shutdown_boot_time=NULL WHERE id=?",
                (host_id,),
            )
