from __future__ import annotations

import httpx


class AgentClient:
    def __init__(self, address: str, port: int, token: str, timeout: float = 3.0):
        formatted_address = f"[{address}]" if ":" in address and not address.startswith("[") else address
        self.base_url = f"http://{formatted_address}:{port}"
        self.headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    async def request(self, method: str, path: str, payload: dict | None = None):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                self.base_url + path,
                headers=self.headers,
                json=payload,
            )
        response.raise_for_status()
        return response.json()

    async def status(self):
        return await self.request("GET", "/api/status")

    async def launches(self):
        return await self.request("GET", "/api/launches")

    async def start_launch(self, launch_id: str):
        return await self.request("POST", f"/api/launches/{launch_id}/start", {})

    async def stop_launch(self, launch_id: str):
        return await self.request("POST", f"/api/launches/{launch_id}/stop", {})

    async def stop_running(self):
        return await self.request("POST", "/api/launches/stop-running", {})

    async def shutdown(self):
        return await self.request("POST", "/api/shutdown", {})
