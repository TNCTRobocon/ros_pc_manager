from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from pc_manager_agent.config import AgentSettings
from pc_manager_agent.main import create_app


def test_agent_api_requires_per_pc_bearer_token(tmp_path: Path):
    launches_file = tmp_path / "launches.yaml"
    launches_file.write_text(yaml.safe_dump({
        "launches": [{
            "id": "demo",
            "display_name": "Demo",
            "workspace": str(tmp_path / "ws"),
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
