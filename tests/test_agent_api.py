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
