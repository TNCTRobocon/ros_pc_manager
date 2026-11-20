from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from pc_manager_agent.config import AgentSettings
from pc_manager_agent.main import create_app


def test_agent_api_requires_per_pc_bearer_token(tmp_path: Path):
    workspace = tmp_path / "ws"
    package_share = workspace / "install" / "share" / "demo_pkg"
    launch_dir = package_share / "launch"
    launch_dir.mkdir(parents=True)
    (workspace / "install" / "setup.bash").write_text("# test setup\n")
    (package_share / "package.xml").write_text(
        "<package format='3'><name>demo_pkg</name><version>0.1.0</version>"
        "<description>test</description><maintainer email='a@b'>test</maintainer>"
        "<license>MIT</license></package>"
    )
    (launch_dir / "demo.launch.py").write_text("# test launch\n")
    launches_file = tmp_path / "launches.yaml"
    launches_file.write_text(yaml.safe_dump({
        "launches": [{
            "id": "demo",
            "display_name": "Demo",
            "workspace": str(workspace),
            "package": "demo_pkg",
            "launch_file": "demo.launch.py",
            "ros_domain_id": 32,
        }]
    }))
    settings = AgentSettings(
        name="robot-test",
        bind_host="127.0.0.1",
        bind_port=8765,
        token="agent-secret-token-with-at-least-24",
        ros_setup=tmp_path / "ros_setup.bash",
        default_domain_id=32,
        launches_file=launches_file,
        state_file=tmp_path / "state" / "launches.json",
        log_root=tmp_path / "logs",
    )
    client = TestClient(create_app(settings))

    assert client.get("/health").status_code == 200
    assert client.get("/api/launches").status_code == 401
    status = client.get(
        "/api/status",
        headers={"Authorization": "Bearer " + settings.token},
    )
    assert status.status_code == 200
    assert "ros_nodes" not in status.json()["system"]
    response = client.get(
        "/api/launches",
        headers={"Authorization": "Bearer " + settings.token},
    )
    assert response.status_code == 200
    assert response.json()["launches"][0]["id"] == "demo"
    assert response.json()["launches"][0]["status"] == "STOPPED"

    config = client.get("/api/launch-config", headers={"Authorization": "Bearer " + settings.token})
    assert config.status_code == 200
    assert config.json()["workspaces"] == [str(workspace)]

    packages = client.get(
        "/api/workspaces/packages",
        params={"workspace": str(workspace)},
        headers={"Authorization": "Bearer " + settings.token},
    )
    assert packages.json()["packages"] == ["demo_pkg"]

    files = client.get(
        "/api/workspaces/launch-files",
        params={"workspace": str(workspace), "package": "demo_pkg"},
        headers={"Authorization": "Bearer " + settings.token},
    )
    assert files.json()["launch_files"] == ["demo.launch.py"]

    changed = dict(config.json()["launches"][0])
    changed["display_name"] = "Updated from GUI"
    validated = client.post(
        "/api/launch-config/validate",
        headers={"Authorization": "Bearer " + settings.token},
        json={"entry": changed},
    )
    assert validated.status_code == 200
    updated = client.put(
        "/api/launch-config",
        headers={"Authorization": "Bearer " + settings.token},
        json={"launches": [changed]},
    )
    assert updated.status_code == 200
    assert updated.json()["launches"][0]["display_name"] == "Updated from GUI"



def test_agent_exposes_peer_discovery_and_rosbag_api_with_cors(tmp_path: Path):
    settings = AgentSettings(
        name="robot-test",
        bind_host="127.0.0.1",
        bind_port=8765,
        token="agent-secret-token-with-at-least-24",
        ros_setup=tmp_path / "ros_setup.bash",
        default_domain_id=32,
        launches_file=tmp_path / "launches.yaml",
        state_file=tmp_path / "state" / "launches.json",
        log_root=tmp_path / "logs",
        agent_id="a8f3d90b-5ee8-4f0d-9654-5a1c2d5d37aa",
    )
    client = TestClient(create_app(settings))
    assert client.get("/health").json()["id"] == settings.agent_id

    unauthorized = client.get("/api/peers")
    assert unauthorized.status_code == 401

    headers = {"Authorization": "Bearer " + settings.token}
    peers = client.get("/api/peers", headers=headers)
    assert peers.status_code == 200
    assert peers.json()["self"]["id"] == settings.agent_id
    assert peers.json()["peers"] == []

    preflight = client.options(
        "/api/peers",
        headers={
            "Origin": "https://tnctrobocon.github.io",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://tnctrobocon.github.io"
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()

    pairing_preflight = client.options(
        "/api/pairing/complete",
        headers={
            "Origin": "https://tnctrobocon.github.io",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert pairing_preflight.status_code == 200
    assert pairing_preflight.headers["access-control-allow-origin"] == "https://tnctrobocon.github.io"
    assert "POST" in pairing_preflight.headers["access-control-allow-methods"]

    local_preflight = client.options(
        "/api/peers",
        headers={
            "Origin": "http://192.168.1.45:8765",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert local_preflight.status_code == 200
    assert local_preflight.headers["access-control-allow-origin"] == "http://192.168.1.45:8765"

    denied_preflight = client.options(
        "/api/peers",
        headers={
            "Origin": "https://malicious.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert denied_preflight.status_code == 400

    bag_status = client.get("/api/rosbag/status", headers=headers)
    assert bag_status.status_code == 200
    assert bag_status.json()["recording"] is False


def test_agent_qr_pairing_is_authenticated_and_one_time(tmp_path: Path):
    settings = AgentSettings(
        name="robot-pairing",
        bind_host="127.0.0.1",
        bind_port=8765,
        token="agent-secret-token-with-at-least-24",
        ros_setup=tmp_path / "ros_setup.bash",
        default_domain_id=32,
        launches_file=tmp_path / "launches.yaml",
        state_file=tmp_path / "state" / "launches.json",
        log_root=tmp_path / "logs",
        agent_id="5f3fbc30-8ed6-4d5f-ae2e-2a9a7f9dfe42",
    )
    client = TestClient(create_app(settings))

    assert client.post("/api/pairing/create").status_code == 401
    headers = {"Authorization": "Bearer " + settings.token}
    created = client.post("/api/pairing/create", headers=headers)
    assert created.status_code == 200
    pairing = created.json()
    assert pairing["agent_id"] == settings.agent_id
    assert pairing["expires_in"] == 300
    assert len(pairing["code"]) >= 40

    invalid = client.post("/api/pairing/complete", json={"code": "invalid-code"})
    assert invalid.status_code == 401

    completed = client.post(
        "/api/pairing/complete",
        json={"code": pairing["code"]},
    )
    assert completed.status_code == 200
    assert completed.json() == {
        "agent_id": settings.agent_id,
        "name": settings.name,
        "token": settings.token,
    }

    replay = client.post(
        "/api/pairing/complete",
        json={"code": pairing["code"]},
    )
    assert replay.status_code == 401
