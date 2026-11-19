from pathlib import Path

import pytest

from pc_manager_server.db import Database


def test_register_and_delete_host(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    host, token = db.add_host("robot-a", "192.168.1.20", 8765, 32)
    assert host["name"] == "robot-a"
    assert len(token) >= 32
    assert db.list_hosts()[0]["address"] == "192.168.1.20"
    assert db.delete_host(host["id"]) is True
    assert db.list_hosts() == []


def test_duplicate_address_port_is_rejected(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    db.add_host("robot-a", "192.168.1.20", 8765, 32)
    with pytest.raises(Exception):
        db.add_host("robot-b", "192.168.1.20", 8765, 33)


def test_update_host_keeps_agent_token(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    host, token = db.add_host("robot-a", "192.168.1.20", 8765, 32)

    assert db.update_host(host["id"], "robot-b", "192.168.1.21", 8766, 33)
    updated = db.get_host(host["id"])
    assert updated["name"] == "robot-b"
    assert updated["address"] == "192.168.1.21"
    assert updated["port"] == 8766
    assert updated["domain_id"] == 33
    assert updated["token"] == token



def test_manager_session_survives_database_recreation_and_can_be_revoked(tmp_path: Path):
    import time

    database_path = tmp_path / "manager.sqlite3"
    db = Database(database_path)
    token = "session-token-that-must-not-be-stored-raw"
    expires_at = time.time() + 60

    db.create_session(token, expires_at)
    with db.connect() as conn:
        stored_hashes = [
            row["token_hash"]
            for row in conn.execute("SELECT token_hash FROM manager_sessions").fetchall()
        ]
    assert token not in stored_hashes

    restarted_db = Database(database_path)
    assert restarted_db.get_session_expiry(token) == expires_at
    restarted_db.delete_session(token)
    assert restarted_db.get_session_expiry(token) is None


def test_expired_manager_session_is_rejected_and_removed(tmp_path: Path):
    import time

    db = Database(tmp_path / "manager.sqlite3")
    token = "expired-session-token"
    db.create_session(token, time.time() - 1)

    assert db.get_session_expiry(token) is None
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM manager_sessions").fetchone()[0]
    assert count == 0


def test_shutdown_request_metadata_survives_database_recreation(tmp_path: Path):
    import time

    database_path = tmp_path / "manager.sqlite3"
    db = Database(database_path)
    host, _ = db.add_host("robot-a", "192.0.2.10", 8765, 32)
    requested_at = time.time()
    assert db.mark_shutdown_requested(host["id"], requested_at, 1234.5)

    restarted_db = Database(database_path)
    restored = restarted_db.get_host(host["id"])
    assert restored["shutdown_requested_at"] == requested_at
    assert restored["shutdown_boot_time"] == 1234.5

    restarted_db.clear_shutdown_requested(host["id"])
    restored = restarted_db.get_host(host["id"])
    assert restored["shutdown_requested_at"] is None
    assert restored["shutdown_boot_time"] is None


def test_database_migrates_existing_hosts_table_without_losing_hosts(tmp_path: Path):
    import sqlite3

    database_path = tmp_path / "legacy-manager.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.execute("""
            CREATE TABLE hosts (
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
        conn.execute(
            "INSERT INTO hosts(name,address,port,domain_id,token) VALUES(?,?,?,?,?)",
            ("legacy-robot", "192.0.2.99", 8765, 32, "legacy-agent-token"),
        )

    migrated = Database(database_path)
    host = migrated.get_host(1)
    assert host is not None
    assert host["name"] == "legacy-robot"
    assert host["token"] == "legacy-agent-token"
    assert host["shutdown_requested_at"] is None
    assert host["shutdown_boot_time"] is None

    with migrated.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "manager_sessions" in tables
