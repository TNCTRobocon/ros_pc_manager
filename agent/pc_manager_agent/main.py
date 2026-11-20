from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import platform
import secrets
import shlex
import socket
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psutil
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import AgentSettings, load_agent_settings
from .launch_manager import LaunchManager
from .discovery import AgentDiscovery
from .rosbag_manager import RosbagManager


class LaunchConfigUpdate(BaseModel):
    launches: list[dict[str, Any]]


class LaunchConfigValidation(BaseModel):
    entry: dict[str, Any]


class RosbagStart(BaseModel):
    domain_id: int
    topics: list[str]


class PairingCompletion(BaseModel):
    code: str


def create_app(settings: AgentSettings) -> FastAPI:
    discovery = AgentDiscovery(
        settings.agent_id, settings.name, settings.bind_port, settings.default_domain_id
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.discovery_enabled:
            await run_in_threadpool(discovery.start)
        try:
            yield
        finally:
            await run_in_threadpool(discovery.close)

    app = FastAPI(title="ROS PC Manager Agent", version="0.3.0", lifespan=lifespan)
    # Bearer-token authentication remains mandatory for all control APIs.
    # CORS is required because the static PWA and Agents have different origins.
    app.add_middleware(
        CORSMiddleware,
        # Static PWA origin plus HTTP pages served by LAN-local Agents.
        allow_origins=["https://tnctrobocon.github.io"],
        allow_origin_regex=(
            r"^http://(?:localhost|127[.]0[.]0[.]1|"
            r"10[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}|"
            r"192[.]168[.][0-9]{1,3}[.][0-9]{1,3}|"
            r"172[.](?:1[6-9]|2[0-9]|3[01])[.][0-9]{1,3}[.][0-9]{1,3}|"
            r"[A-Za-z0-9-]+(?:[.][A-Za-z0-9-]+)*[.]local)(?::[0-9]{1,5})?$"
        ),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    launch_manager = LaunchManager(settings)
    rosbag_manager = RosbagManager(settings)
    pairing_lock = threading.Lock()
    pending_pairing: dict[str, Any] = {"hash": None, "expires_at": 0.0}
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

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
            "boot_time": psutil.boot_time(),
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

    @app.get("/")
    async def index():
        return FileResponse(frontend_dir / "index.html")

    @app.get("/icon.svg")
    async def icon_svg():
        return FileResponse(frontend_dir / "icon.svg", media_type="image/svg+xml")

    @app.get("/service-worker.js")
    async def service_worker():
        return FileResponse(
            frontend_dir / "service-worker.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(
            frontend_dir / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "id": settings.agent_id,
            "name": settings.name,
            "port": settings.bind_port,
            "api_version": 1,
        }

    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def status():
        system = await run_in_threadpool(system_info)
        launches = await run_in_threadpool(launch_manager.list_launches)
        return {
            "agent_id": settings.agent_id,
            "name": settings.name,
            "online": True,
            "system": system,
            "launches": launches,
        }

    @app.get("/api/launch-config", dependencies=[Depends(require_token)])
    async def launch_config():
        return await run_in_threadpool(launch_manager.get_launch_config)

    @app.put("/api/launch-config", dependencies=[Depends(require_token)])
    async def update_launch_config(body: LaunchConfigUpdate):
        try:
            return await run_in_threadpool(launch_manager.replace_launch_config, body.launches)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/launch-config/validate", dependencies=[Depends(require_token)])
    async def validate_launch_config(body: LaunchConfigValidation):
        try:
            entry = await run_in_threadpool(launch_manager.validate_launch_entry, body.entry)
            return {"ok": True, "entry": entry}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/workspaces/packages", dependencies=[Depends(require_token)])
    async def workspace_packages(workspace: str):
        try:
            packages = await run_in_threadpool(launch_manager.workspace_packages, workspace)
            return {"workspace": workspace, "packages": packages}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/workspaces/launch-files", dependencies=[Depends(require_token)])
    async def workspace_launch_files(workspace: str, package: str):
        try:
            files = await run_in_threadpool(
                launch_manager.workspace_launch_files, workspace, package
            )
            return {"workspace": workspace, "package": package, "launch_files": files}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    @app.post("/api/pairing/create", dependencies=[Depends(require_token)])
    async def create_pairing_code():
        code = secrets.token_urlsafe(32)
        with pairing_lock:
            pending_pairing["hash"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
            pending_pairing["expires_at"] = time.monotonic() + 300.0
        return {
            "agent_id": settings.agent_id,
            "name": settings.name,
            "code": code,
            "expires_in": 300,
        }

    @app.post("/api/pairing/complete")
    async def complete_pairing(body: PairingCompletion):
        if not body.code or len(body.code) > 128:
            raise HTTPException(status_code=401, detail="Invalid or expired pairing code")
        supplied_hash = hashlib.sha256(body.code.encode("utf-8")).hexdigest()
        with pairing_lock:
            expected_hash = pending_pairing["hash"]
            expired = time.monotonic() >= float(pending_pairing["expires_at"])
            if expected_hash is None or expired:
                pending_pairing["hash"] = None
                pending_pairing["expires_at"] = 0.0
                raise HTTPException(status_code=401, detail="Invalid or expired pairing code")
            if not hmac.compare_digest(supplied_hash, str(expected_hash)):
                raise HTTPException(status_code=401, detail="Invalid or expired pairing code")
            # Consume the one-time code before returning the long-lived Agent token.
            pending_pairing["hash"] = None
            pending_pairing["expires_at"] = 0.0
        return {
            "agent_id": settings.agent_id,
            "name": settings.name,
            "token": settings.token,
        }

    @app.get("/api/peers", dependencies=[Depends(require_token)])
    async def peers():
        return {
            "self": {
                "id": settings.agent_id,
                "name": settings.name,
                "port": settings.bind_port,
            },
            "peers": discovery.list_peers(),
        }

    @app.get("/api/rosbag/status", dependencies=[Depends(require_token)])
    async def rosbag_status():
        return await run_in_threadpool(rosbag_manager.status)

    @app.get("/api/rosbag/topics", dependencies=[Depends(require_token)])
    async def rosbag_topics(domain_id: int):
        try:
            topics = await run_in_threadpool(rosbag_manager.list_topics, domain_id)
            return {"domain_id": domain_id, "topics": topics}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Topic discovery failed: {exc}") from exc

    @app.post("/api/rosbag/start", dependencies=[Depends(require_token)])
    async def rosbag_start(body: RosbagStart):
        try:
            return await run_in_threadpool(
                rosbag_manager.start, body.domain_id, body.topics
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not start rosbag: {exc}") from exc

    @app.post("/api/rosbag/stop", dependencies=[Depends(require_token)])
    async def rosbag_stop():
        try:
            return await run_in_threadpool(rosbag_manager.stop)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not stop rosbag: {exc}") from exc

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
