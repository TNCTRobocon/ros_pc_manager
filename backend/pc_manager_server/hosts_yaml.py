from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml


class HostsYamlExporter:
    """Atomically exports non-secret host inventory from SQLite to YAML."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def sync(self, hosts: list[dict[str, Any]]) -> None:
        # Whitelist fields deliberately: the SQLite token must never leave the DB.
        public_hosts = [
            {
                "id": int(host["id"]),
                "name": str(host["name"]),
                "address": str(host["address"]),
                "port": int(host["port"]),
                "domain_id": int(host["domain_id"]),
            }
            for host in hosts
        ]
        document = {
            "schema_version": 1,
            "source": "SQLite",
            "hosts": public_hosts,
        }
        content = (
            "# Auto-generated from SQLite. Do not edit manually.\n"
            "# Agent authentication tokens are intentionally omitted.\n"
            + yaml.safe_dump(
                document,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        )

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                text=True,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary_path.chmod(0o644)
                os.replace(temporary_path, self.path)
                try:
                    directory_fd = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    # Atomic replace has already succeeded; directory fsync is best effort.
                    pass
            finally:
                temporary_path.unlink(missing_ok=True)
