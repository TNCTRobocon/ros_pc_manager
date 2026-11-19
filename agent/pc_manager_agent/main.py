from __future__ import annotations

import argparse
import hmac
import os
import platform
import shlex
import socket
import subprocess
from typing import Any

import psutil
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .config import AgentSettings, load_agent_settings
from .launch_manager import LaunchManager


def create_app(settings: AgentSettings) -> FastAPI:
    app = FastAPI(title="ROS PC Manager Agent", version="0.2.0")
    launch_manager = LaunchManager(settings)

    async def require_token(authorization: str | None = Header(default=None)):
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if not hmac.compare_digest(supplied, settings.token):
            raise HTTPException(status_code=401, detail="Invalid agent token")

    def ros_nodes() -> list[str]:
        if not settings.ros_setup.is_file():
            return []
        command = (
            f"source {shlex.quote(str(settings.ros_setup))} && "
            f"export ROS_DOMAIN_ID={settings.default_domain_id} && "
            "ros2 node list --no-daemon"
        )
        try:
            result = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=5,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        return sorted({
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        })

    def system_info() -> dict[str, Any]:
        memory = psutil.virtual_memory()
        root_disk = psutil.disk_usage("/")
        ipv4 = sorted({
            address.address
            for interface in psutil.net_if_addrs().values()
            for address in interface
            if address.family == socket.AF_INET
            and not address.address.startswith("127.")
        })
        temperatures = None
        try:
            sensor_groups = psutil.sensors_temperatures()
            readings = [
                item.current
                for group in sensor_groups.values()
                for item in group
                if item.current is not None
            ]
            temperatures = max(readings) if readings else None
        except (AttributeError, OSError):
            pass

        distro = settings.ros_setup.parent.name
        return {
            "hostname": socket.gethostname(),
            "ip_addresses": ipv4,
            "os": f"{platform.system()} {platform.release()}",
            "ros_distro": distro,
            "default_domain_id": settings.default_domain_id,
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": memory.percent,
            "memory_total_bytes": memory.total,
            "disk_percent": root_disk.percent,
            "disk_free_bytes": root_disk.free,
            "cpu_temperature_c": temperatures,
            "ros_nodes": ros_nodes(),
        }

    @app.get("/health")
    async def health():
        return {"ok": True, "name": settings.name}

    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def status():
        system = await run_in_threadpool(system_info)
        launches = await run_in_threadpool(launch_manager.list_launches)
        return {
            "name": settings.name,
            "online": True,
            "system": system,
            "launches": launches,
        }

    @app.get("/api/launches", dependencies=[Depends(require_token)])
    async def launches():
        return await run_in_threadpool(launch_manager.list_launches)

    @app.post("/api/launches/{launch_id}/start", dependencies=[Depends(require_token)])
    async def start_launch(launch_id: str):
        try:
            return await run_in_threadpool(launch_manager.start, launch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/launches/{launch_id}/stop", dependencies=[Depends(require_token)])
    async def stop_launch(launch_id: str):
        try:
            return await run_in_threadpool(launch_manager.stop, launch_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/launches/stop-running", dependencies=[Depends(require_token)])
    async def stop_running():
        return await run_in_threadpool(launch_manager.stop_running)

    @app.post("/api/shutdown", dependencies=[Depends(require_token)])
    async def shutdown():
        command = (
            ["/usr/bin/systemctl", "poweroff"]
            if os.geteuid() == 0
            else ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "poweroff"]
        )
        try:
            result = await run_in_threadpool(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=503, detail=f"Could not request shutdown: {exc}")
        if result.returncode != 0:
            detail = result.stderr.strip() or (
                "Shutdown permission is missing. Install the restricted sudoers rule "
                "from deploy/sudoers/ros-pc-manager.example."
            )
            raise HTTPException(status_code=503, detail=detail)
        return JSONResponse(
            status_code=202,
            content={"ok": True, "message": "Shutdown requested"},
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="ROS PC Manager Agent")
    parser.add_argument("--config", default=None, help="Path to agent.yaml")
    args = parser.parse_args()
    settings = load_agent_settings(args.config)
    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
