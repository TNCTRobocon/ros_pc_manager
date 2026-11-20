from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from typing import Any

import psutil

SERVICE_TYPE = "_ros-pc-manager._tcp.local."
logger = logging.getLogger(__name__)


class AgentDiscovery:
    """Advertise this Agent and collect peer metadata through mDNS/DNS-SD.

    Discovery data is descriptive only. It never includes authentication tokens,
    and it does not grant one Agent authority to control another.
    """

    def __init__(self, agent_id: str, name: str, port: int, domain_id: int = 0):
        self.agent_id = agent_id
        self.name = name
        self.port = port
        self.domain_id = domain_id
        self._lock = threading.RLock()
        self._peers: dict[str, dict[str, Any]] = {}
        self._service_ids: dict[str, str] = {}
        self._zeroconf = None
        self._browser = None
        self._service_info = None
        self._started = False

    @staticmethod
    def _local_addresses() -> list[bytes]:
        found: set[bytes] = set()
        for entries in psutil.net_if_addrs().values():
            for entry in entries:
                if entry.family not in (socket.AF_INET, socket.AF_INET6):
                    continue
                value = entry.address.split("%", 1)[0]
                try:
                    address = ipaddress.ip_address(value)
                except ValueError:
                    continue
                if address.is_loopback or address.is_link_local or address.is_unspecified:
                    continue
                if address.version == 4 and address.is_private:
                    found.add(address.packed)
        return sorted(found)

    def start(self) -> None:
        if self._started:
            return
        try:
            from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
            addresses = self._local_addresses()
            if not addresses:
                logger.warning("mDNS discovery: no eligible LAN address found")
                return
            self._zeroconf = Zeroconf()
            instance = f"{self.agent_id}.{SERVICE_TYPE}"
            self._service_info = ServiceInfo(
                SERVICE_TYPE,
                instance,
                addresses=addresses,
                port=self.port,
                properties={
                    b"id": self.agent_id.encode("ascii"),
                    b"name": self.name.encode("utf-8"),
                    b"domain_id": str(self.domain_id).encode("ascii"),
                    b"api": b"1",
                },
                server=f"{self.agent_id}.local.",
            )
            self._zeroconf.register_service(self._service_info, allow_name_change=False)
            self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, listener=self)
            self._started = True
            logger.info("mDNS discovery started for %s", self.name)
        except Exception:
            logger.exception("mDNS discovery could not start")
            self.close()

    def _read_peer(self, zeroconf, service_type: str, name: str) -> None:
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=1200)
            if info is None:
                return
            properties = {
                key.decode("utf-8", errors="replace"): value.decode("utf-8", errors="replace")
                for key, value in info.properties.items()
            }
            peer_id = properties.get("id", "")
            if not peer_id or peer_id == self.agent_id:
                return
            addresses: list[str] = []
            for value in info.parsed_addresses():
                try:
                    address = ipaddress.ip_address(value)
                except ValueError:
                    continue
                if address.version == 4 and address.is_private and not address.is_loopback:
                    addresses.append(str(address))
            if not addresses:
                return
            peer = {
                "id": peer_id,
                "name": properties.get("name") or name.split(".", 1)[0],
                "addresses": sorted(set(addresses)),
                "port": int(info.port),
                "domain_id": int(properties.get("domain_id", "0")),
                "protocol": "http",
                "last_seen": time.time(),
            }
            with self._lock:
                self._peers[peer_id] = peer
                self._service_ids[name] = peer_id
        except Exception:
            logger.debug("Could not read mDNS peer %s", name, exc_info=True)

    def add_service(self, zeroconf, service_type: str, name: str) -> None:
        self._read_peer(zeroconf, service_type, name)

    def update_service(self, zeroconf, service_type: str, name: str) -> None:
        self._read_peer(zeroconf, service_type, name)

    def remove_service(self, zeroconf, service_type: str, name: str) -> None:
        with self._lock:
            peer_id = self._service_ids.pop(name, None)
            if peer_id:
                self._peers.pop(peer_id, None)

    def list_peers(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            expired = [
                peer_id for peer_id, peer in self._peers.items()
                if now - float(peer.get("last_seen", 0)) > 600.0
            ]
            for peer_id in expired:
                self._peers.pop(peer_id, None)
            return [
                {key: value for key, value in peer.items() if key != "last_seen"}
                | {"last_seen_seconds": max(0, int(now - float(peer["last_seen"])))}
                for peer in sorted(self._peers.values(), key=lambda item: item["name"].lower())
            ]

    def close(self) -> None:
        browser, zeroconf, service_info = self._browser, self._zeroconf, self._service_info
        self._browser = None
        self._zeroconf = None
        self._service_info = None
        self._started = False
        with self._lock:
            self._peers.clear()
            self._service_ids.clear()
        try:
            if browser is not None:
                try:
                    browser.cancel()
                except Exception:
                    logger.debug("Could not cancel mDNS browser", exc_info=True)
            if zeroconf is not None:
                if service_info is not None:
                    try:
                        zeroconf.unregister_service(service_info)
                    except Exception:
                        logger.debug("Could not unregister mDNS service", exc_info=True)
                zeroconf.close()
        except Exception:
            logger.debug("Could not close zeroconf", exc_info=True)
