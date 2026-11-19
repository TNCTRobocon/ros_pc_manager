from pathlib import Path

from fastapi.testclient import TestClient

from pc_manager_server.config import Settings
from pc_manager_server.db import Database
from pc_manager_server.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1]
    return Settings(
        project_root=root,
        bind_host="127.0.0.1",
        bind_port=8080,
        admin_password="correct-horse-battery",
        database_path=tmp_path / "manager.sqlite3",
        ros_setup=Path("/opt/ros/jazzy/setup.bash"),
        ros_overlays=(),
        rosbag_root=tmp_path / "rosbags",
        rosbag_state_path=tmp_path / "state" / "rosbag.json",
        process_log_root=tmp_path / "logs",
        min_free_gb=0,
        warning_free_gb=0,
        agent_timeout_seconds=0.05,
        configured_domains=(32,),
    )


def test_api_requires_login_and_registers_pc_once(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    assert client.get("/api/hosts").status_code == 401
    login = client.post("/api/login", json={"password": "wrong"})
    assert login.status_code == 401

    login = client.post("/api/login", json={"password": "correct-horse-battery"})
    assert login.status_code == 200
    token = login.json()["session_token"]
    headers = {"X-Manager-Session": token}

    registered = client.post("/api/hosts", headers=headers, json={
        "name": "robot-test",
        "address": "192.0.2.10",
        "port": 8765,
        "domain_id": 32,
    })
    assert registered.status_code == 201
    body = registered.json()
    assert len(body["agent_token"]) >= 32
    assert "token:" in body["agent_config"]
    assert "\n" in body["agent_config"]
    assert "agent_token" not in client.get("/api/hosts", headers=headers).json()[0]

    duplicate = client.post("/api/hosts", headers=headers, json={
        "name": "robot-test-duplicate",
        "address": "192.0.2.10",
        "port": 8765,
        "domain_id": 32,
    })
    assert duplicate.status_code == 409

    assert client.get("/api/session", headers=headers).status_code == 200

    # hosts.yaml is regenerated from SQLite, and never receives the Agent token.
    import yaml
    hosts_yaml = tmp_path / "hosts.yaml"
    exported = yaml.safe_load(hosts_yaml.read_text(encoding="utf-8"))
    assert exported["hosts"][0] == {
        "id": body["id"],
        "name": "robot-test",
        "address": "192.0.2.10",
        "port": 8765,
        "domain_id": 32,
    }
    assert body["agent_token"] not in hosts_yaml.read_text(encoding="utf-8")

    updated = client.put(
        f"/api/hosts/{body['id']}",
        headers=headers,
        json={
            "name": "robot-test-renamed",
            "address": "192.0.2.11",
            "port": 8766,
            "domain_id": 33,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "robot-test-renamed"
    assert "token" not in updated.json()
    exported = yaml.safe_load(hosts_yaml.read_text(encoding="utf-8"))
    assert exported["hosts"][0] == {
        "id": body["id"],
        "name": "robot-test-renamed",
        "address": "192.0.2.11",
        "port": 8766,
        "domain_id": 33,
    }
    assert body["agent_token"] not in hosts_yaml.read_text(encoding="utf-8")

    deleted = client.delete(f"/api/hosts/{body['id']}", headers=headers)
    assert deleted.status_code == 200
    assert yaml.safe_load(hosts_yaml.read_text(encoding="utf-8"))["hosts"] == []


def test_server_startup_rebuilds_hosts_yaml_from_sqlite(tmp_path):
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    host, token = database.add_host("robot-existing", "192.0.2.50", 8765, 32)
    hosts_yaml = tmp_path / "hosts.yaml"
    hosts_yaml.write_text("stale: true\\n", encoding="utf-8")

    create_app(settings)

    import yaml
    exported = yaml.safe_load(hosts_yaml.read_text(encoding="utf-8"))
    assert exported["hosts"] == [{
        "id": host["id"],
        "name": "robot-existing",
        "address": "192.0.2.50",
        "port": 8765,
        "domain_id": 32,
    }]
    assert token not in hosts_yaml.read_text(encoding="utf-8")


def test_launch_configuration_routes_proxy_to_registered_agent(tmp_path, monkeypatch):
    import pc_manager_server.main as server_module

    class FakeAgentClient:
        def __init__(self, address, port, token, timeout):
            self.address = address

        async def launch_config(self):
            return {
                "launches": [{
                    "id": "demo",
                    "display_name": "Demo",
                    "workspace": "/opt/ws",
                    "package": "demo_pkg",
                    "launch_file": "demo.launch.py",
                    "ros_domain_id": 32,
                    "arguments": {},
                }],
                "workspaces": ["/opt/ws"],
            }

        async def update_launch_config(self, launches):
            return {"launches": launches, "workspaces": sorted({
                item["workspace"] for item in launches
            })}

        async def validate_launch_config(self, entry):
            return {"ok": True, "entry": entry}

        async def workspace_packages(self, workspace):
            return {"workspace": workspace, "packages": ["demo_pkg"]}

        async def workspace_launch_files(self, workspace, package):
            return {
                "workspace": workspace,
                "package": package,
                "launch_files": ["demo.launch.py"],
            }

    monkeypatch.setattr(server_module, "AgentClient", FakeAgentClient)
    client = TestClient(create_app(make_settings(tmp_path)))
    login = client.post("/api/login", json={"password": "correct-horse-battery"})
    headers = {"X-Manager-Session": login.json()["session_token"]}
    registered = client.post("/api/hosts", headers=headers, json={
        "name": "robot-test",
        "address": "192.0.2.81",
        "port": 8765,
        "domain_id": 32,
    })
    host_id = registered.json()["id"]

    config = client.get(f"/api/hosts/{host_id}/launch-config", headers=headers)
    assert config.status_code == 200
    assert config.json()["workspaces"] == ["/opt/ws"]

    entry = config.json()["launches"][0]
    entry["display_name"] = "Edited"
    updated = client.put(
        f"/api/hosts/{host_id}/launch-config",
        headers=headers,
        json={"launches": [entry]},
    )
    assert updated.status_code == 200
    assert updated.json()["launches"][0]["display_name"] == "Edited"

    validated = client.post(
        f"/api/hosts/{host_id}/launch-config/validate",
        headers=headers,
        json={"entry": entry},
    )
    assert validated.status_code == 200
    assert validated.json()["ok"] is True

    packages = client.get(
        f"/api/hosts/{host_id}/workspaces/packages",
        headers=headers,
        params={"workspace": "/opt/ws"},
    )
    assert packages.json()["packages"] == ["demo_pkg"]

    files = client.get(
        f"/api/hosts/{host_id}/workspaces/launch-files",
        headers=headers,
        params={"workspace": "/opt/ws", "package": "demo_pkg"},
    )
    assert files.json()["launch_files"] == ["demo.launch.py"]



def test_login_session_survives_manager_server_recreation(tmp_path):
    settings = make_settings(tmp_path)
    first_client = TestClient(create_app(settings))
    login = first_client.post("/api/login", json={"password": "correct-horse-battery"})
    assert login.status_code == 200
    token = login.json()["session_token"]
    headers = {"X-Manager-Session": token}

    # A fresh FastAPI app instance represents a Manager Server restart.
    restarted_client = TestClient(create_app(settings))
    session = restarted_client.get("/api/session", headers=headers)
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    hosts_response = restarted_client.get("/api/hosts", headers=headers)
    assert hosts_response.status_code == 200
    assert hosts_response.headers["cache-control"].startswith("no-store")

    logout = restarted_client.post("/api/logout", headers=headers)
    assert logout.status_code == 200
    assert restarted_client.get("/api/hosts", headers=headers).status_code == 401


def test_host_status_changes_shutdown_offline_and_back_online(tmp_path, monkeypatch):
    import pc_manager_server.main as server_module

    class FakeAgentClient:
        boot_time = 1000.0
        available = True

        def __init__(self, address, port, token, timeout):
            self.address = address

        async def status(self):
            if not self.available:
                raise ConnectionError("Agent is offline")
            return {
                "system": {"boot_time": self.boot_time, "default_domain_id": 32},
                "launches": {"launches": [], "running_count": 0},
            }

        async def shutdown(self):
            return {"ok": True, "message": "Shutdown requested"}

    monkeypatch.setattr(server_module, "AgentClient", FakeAgentClient)
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings))
    login = client.post("/api/login", json={"password": "correct-horse-battery"})
    headers = {"X-Manager-Session": login.json()["session_token"]}
    registered = client.post("/api/hosts", headers=headers, json={
        "name": "robot-shutdown-test",
        "address": "192.0.2.82",
        "port": 8765,
        "domain_id": 32,
    })
    host_id = registered.json()["id"]

    assert client.get("/api/hosts", headers=headers).json()[0]["status"] == "ONLINE"

    shutdown = client.post(f"/api/hosts/{host_id}/shutdown", headers=headers, json={})
    assert shutdown.status_code == 202
    assert shutdown.json()["host_status"] == "SHUTTING_DOWN"

    # It remains reachable briefly, but must not be reported ONLINE.
    assert client.get("/api/hosts", headers=headers).json()[0]["status"] == "SHUTTING_DOWN"
    assert client.get(
        f"/api/hosts/{host_id}/status", headers=headers
    ).json()["host_status"] == "SHUTTING_DOWN"

    # Once the Agent stops responding, the host is OFFLINE.
    FakeAgentClient.available = False
    assert client.get("/api/hosts", headers=headers).json()[0]["status"] == "OFFLINE"

    # A new OS boot time means the PC has restarted; its Agent is ONLINE again.
    FakeAgentClient.available = True
    FakeAgentClient.boot_time = 2000.0
    assert client.get("/api/hosts", headers=headers).json()[0]["status"] == "ONLINE"
