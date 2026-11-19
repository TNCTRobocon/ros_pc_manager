from pathlib import Path

import yaml

from pc_manager_server.hosts_yaml import HostsYamlExporter


def test_export_contains_public_fields_but_never_agent_token(tmp_path: Path):
    target = tmp_path / "config" / "hosts.yaml"
    exporter = HostsYamlExporter(target)

    exporter.sync([{
        "id": 7,
        "name": "ロボットA",
        "address": "192.168.1.20",
        "port": 8765,
        "domain_id": 32,
        "token": "TEST_ONLY_SECRET_MUST_NOT_EXPORT",
    }])

    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["source"] == "SQLite"
    assert document["hosts"] == [{
        "id": 7,
        "name": "ロボットA",
        "address": "192.168.1.20",
        "port": 8765,
        "domain_id": 32,
    }]
    assert "TEST_ONLY_SECRET_MUST_NOT_EXPORT" not in target.read_text(encoding="utf-8")
    assert list(target.parent.glob(".hosts.yaml.*.tmp")) == []


def test_export_empty_hosts_after_delete(tmp_path: Path):
    target = tmp_path / "hosts.yaml"
    exporter = HostsYamlExporter(target)
    exporter.sync([{
        "id": 1, "name": "robot-a", "address": "192.168.1.10",
        "port": 8765, "domain_id": 32, "token": "secret",
    }])
    exporter.sync([])

    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert document["hosts"] == []
