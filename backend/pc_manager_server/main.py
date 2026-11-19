from __future__ import annotations

import asyncio
import hmac
import ipaddress
import secrets
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .agent_client import AgentClient
from .config import Settings, load_settings
from .db import Database
from .hosts_yaml import HostsYamlExporter
from .rosbag_manager import RosbagManager

SESSION_TTL_SECONDS = 12 * 60 * 60


class LoginRequest(BaseModel):
    password: str


class HostCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    address: str
    port: int = Field(default=8765, ge=1, le=65535)
    domain_id: int = Field(default=0, ge=0, le=232)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("PC name cannot be empty")
        return value

    @field_validator("address")
    @classmethod
    def valid_ip(cls, value: str) -> str:
        value = value.strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError as exc:
            raise ValueError("Enter an IPv4 or IPv6 address") from exc


class HostUpdate(HostCreate):
    """Editable public host settings; the Agent token is intentionally unchanged."""


class LaunchStart(BaseModel):
    launch_id: str = Field(min_length=1, max_length=80)


class LaunchStop(BaseModel):
    launch_ids: list[str] = Field(min_length=1, max_length=100)


class RosbagStart(BaseModel):
    domain_id: int = Field(ge=0, le=232)
    topics: list[str] = Field(min_length=1, max_length=500)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    database = Database(settings.database_path)
    hosts_yaml_path = settings.hosts_yaml_path or (settings.database_path.parent / "hosts.yaml")
    hosts_yaml_exporter = HostsYamlExporter(hosts_yaml_path)
    hosts_sync_lock = threading.RLock()
    # SQLite is authoritative. Rebuild the YAML view on every server start so
    # a previous export failure cannot leave stale registration data.
    hosts_yaml_exporter.sync(database.list_hosts())
    rosbag_manager = RosbagManager(settings)
    sessions: dict[str, float] = {}
    app = FastAPI(title="ROS PC Manager", version="0.2.0")
    frontend_dir = settings.project_root / "frontend"
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.middleware("http")
    async def manager_auth(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in {"/api/login", "/api/health"}:
            token = request.headers.get("X-Manager-Session", "")
            expires_at = sessions.get(token, 0.0)
            if not token or expires_at <= time.time():
                sessions.pop(token, None)
                return JSONResponse(status_code=401, content={"detail": "Login required"})
        return await call_next(request)

    def sync_hosts_yaml() -> None:
        try:
            # Keep the DB read and file replace in one critical section so
            # concurrent registrations cannot leave an older YAML snapshot last.
            with hosts_sync_lock:
                hosts_yaml_exporter.sync(database.list_hosts())
        except OSError as exc:
            # The SQLite transaction has already committed. A server restart
            # retries the export from the authoritative database.
            raise HTTPException(
                status_code=500,
                detail=f"PC data was saved to SQLite, but hosts.yaml export failed: {exc}",
            ) from exc

    def client_for(host_id: int) -> AgentClient:
        host = database.get_host(host_id)
        if not host:
            raise HTTPException(status_code=404, detail="PC is not registered")
        return AgentClient(
            host["address"],
            int(host["port"]),
            host["token"],
            settings.agent_timeout_seconds,
        )

    def agent_error(exc: Exception, context: str) -> HTTPException:
        status_code = 502
        detail = f"{context}: {exc}"
        if isinstance(exc, httpx.HTTPStatusError):
            response_code = exc.response.status_code
            try:
                body = exc.response.json()
                detail = str(body.get("detail", detail)) if isinstance(body, dict) else detail
            except ValueError:
                pass
            if response_code in {400, 404, 409, 422}:
                status_code = response_code
        return HTTPException(status_code=status_code, detail=detail)

    @app.get("/")
    async def index():
        return FileResponse(frontend_dir / "index.html")

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "ros-pc-manager"}

    @app.post("/api/login")
    async def login(body: LoginRequest):
        if not hmac.compare_digest(body.password, settings.admin_password):
            raise HTTPException(status_code=401, detail="パスワードが正しくありません")
        token = secrets.token_urlsafe(32)
        sessions[token] = time.time() + SESSION_TTL_SECONDS
        return {"ok": True, "session_token": token, "expires_in": SESSION_TTL_SECONDS}

    @app.get("/api/session")
    async def session():
        return {"authenticated": True, "expires_in": SESSION_TTL_SECONDS}

    @app.post("/api/logout")
    async def logout(request: Request):
        token = request.headers.get("X-Manager-Session", "")
        sessions.pop(token, None)
        return {"ok": True}

    @app.get("/api/hosts")
    async def list_hosts():
        hosts = database.list_hosts()

        async def inspect(host: dict[str, Any]):
            stored = database.get_host(host["id"])
            client = AgentClient(
                host["address"],
                int(host["port"]),
                stored["token"],
                settings.agent_timeout_seconds,
            )
            try:
                status = await client.status()
                return {
                    **host,
                    "status": "ONLINE",
                    "system": status.get("system", {}),
                    "launches": status.get("launches", {}),
                }
            except Exception as exc:
                return {**host, "status": "OFFLINE", "error": str(exc)}

        return await asyncio.gather(*(inspect(host) for host in hosts))

    @app.post("/api/hosts", status_code=201)
    async def add_host(body: HostCreate):
        try:
            host, token = database.add_host(
                body.name,
                body.address,
                body.port,
                body.domain_id,
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail="This IP address and port are already registered",
                )
            raise
        sync_hosts_yaml()
        agent_config = (
            "agent:\n"
            f"  name: {body.name!r}\n"
            "  host: 0.0.0.0\n"
            f"  port: {body.port}\n"
            f"  token: {token}\n"
            "  ros_setup: /opt/ros/jazzy/setup.bash\n"
            f"  default_domain_id: {body.domain_id}\n"
            "\n"
            "paths:\n"
            "  launches_file: /etc/ros-pc-manager/launches.yaml\n"
            "  state_file: ~/.local/state/ros-pc-manager/launches.json\n"
            "  log_root: ~/.local/state/ros-pc-manager/logs\n"
        )
        return {
            **{key: value for key, value in host.items() if key != "token"},
            "agent_token": token,
            "agent_config": agent_config,
        }

    @app.put("/api/hosts/{host_id}")
    async def update_host(host_id: int, body: HostUpdate):
        if not database.get_host(host_id):
            raise HTTPException(status_code=404, detail="PC is not registered")
        try:
            updated = database.update_host(
                host_id,
                body.name,
                body.address,
                body.port,
                body.domain_id,
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail="This IP address and port are already registered",
                ) from exc
            raise
        if not updated:
            raise HTTPException(status_code=404, detail="PC is not registered")
        sync_hosts_yaml()
        host = database.get_host(host_id) or {}
        return {key: value for key, value in host.items() if key != "token"}

    @app.delete("/api/hosts/{host_id}")
    async def remove_host(host_id: int):
        if not database.delete_host(host_id):
            raise HTTPException(status_code=404, detail="PC is not registered")
        sync_hosts_yaml()
        return {"ok": True}

    @app.get("/api/hosts/{host_id}/status")
    async def host_status(host_id: int):
        try:
            return await client_for(host_id).status()
        except Exception as exc:
            raise agent_error(exc, "PC agent unavailable")

    @app.get("/api/hosts/{host_id}/launches")
    async def host_launches(host_id: int):
        try:
            return await client_for(host_id).launches()
        except Exception as exc:
            raise agent_error(exc, "PC agent unavailable")

    @app.post("/api/hosts/{host_id}/launches/start")
    async def start_launch(host_id: int, body: LaunchStart):
        try:
            return await client_for(host_id).start_launch(body.launch_id)
        except Exception as exc:
            raise agent_error(exc, "Launch start failed")

    @app.post("/api/hosts/{host_id}/launches/stop")
    async def stop_launches(host_id: int, body: LaunchStop):
        try:
            client = client_for(host_id)
            if "__running__" in body.launch_ids:
                return await client.stop_running()
            unique_ids = list(dict.fromkeys(body.launch_ids))
            results = await asyncio.gather(
                *(client.stop_launch(launch_id) for launch_id in unique_ids),
                return_exceptions=True,
            )
            return {
                "ok": all(not isinstance(item, Exception) for item in results),
                "results": [
                    {"error": str(item)} if isinstance(item, Exception) else item
                    for item in results
                ],
            }
        except Exception as exc:
            raise agent_error(exc, "Launch stop failed")

    @app.post("/api/hosts/{host_id}/shutdown", status_code=202)
    async def shutdown_host(host_id: int):
        try:
            return await client_for(host_id).shutdown()
        except Exception as exc:
            raise agent_error(exc, "Shutdown request failed")

    @app.get("/api/domains")
    async def list_domains():
        # Domain IDs are discovered from the registered PCs and their launch YAML
        # files, then merged with manually configured IDs in config/domains.yaml.
        ids = set(settings.configured_domains)
        for host in database.list_hosts():
            ids.add(int(host["domain_id"]))
            try:
                stored = database.get_host(host["id"])
                launches = await AgentClient(
                    host["address"],
                    int(host["port"]),
                    stored["token"],
                    settings.agent_timeout_seconds,
                ).launches()
                for launch in launches.get("launches", []):
                    value = int(launch.get("domain_id", 0))
                    if 0 <= value <= 232:
                        ids.add(value)
            except Exception:
                continue
        return {"domains": sorted(domain for domain in ids if 0 <= domain <= 232)}

    @app.get("/api/rosbag/topics")
    async def rosbag_topics(domain_id: int):
        try:
            topics = await run_in_threadpool(rosbag_manager.list_topics, domain_id)
            return {"domain_id": domain_id, "topics": topics}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Topic discovery failed: {exc}")

    @app.get("/api/rosbag/status")
    async def rosbag_status():
        return await run_in_threadpool(rosbag_manager.status)

    @app.post("/api/rosbag/start")
    async def start_rosbag(body: RosbagStart):
        try:
            return await run_in_threadpool(
                rosbag_manager.start,
                body.domain_id,
                body.topics,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not start rosbag: {exc}")

    @app.post("/api/rosbag/stop")
    async def stop_rosbag():
        try:
            return await run_in_threadpool(rosbag_manager.stop)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not stop rosbag: {exc}")

    return app


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
