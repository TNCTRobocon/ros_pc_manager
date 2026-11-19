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
