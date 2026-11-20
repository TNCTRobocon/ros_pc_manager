from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AgentSettings
from .ros_environment import ros_environment

TOPIC_PATTERN = re.compile(r"^/[A-Za-z0-9_~/.-]+$")
ACTIVE_STATUSES = {"STARTING", "RECORDING", "STOPPING"}


class RosbagManager:
    """Runs one rosbag2 recorder on this Agent PC for one DDS Domain ID."""

    def __init__(self, settings: AgentSettings):
        self.settings = settings
        self.state_file = settings.rosbag_state_path or (settings.state_file.parent / "rosbag_state.json")
        self.rosbag_root = settings.rosbag_root or (settings.log_root / "rosbags")
        self.log_file = settings.process_log_root / "rosbag" / "current.log"
        self._lock = threading.RLock()
        self.state: dict[str, Any] | None = self._load_state()
        # This handle is unavailable after an Agent restart. PID + start-time
        # validation allows status/stop to reconnect to a surviving recorder.
        self.process: subprocess.Popen | None = None
        self.rosbag_root.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.state_file.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _save_state(self) -> None:
        if self.state is None:
            self.state_file.unlink(missing_ok=True)
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False))
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        temp.replace(self.state_file)

    @staticmethod
    def _process_info(pid: int) -> tuple[str | None, str | None]:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
            fields = raw[raw.rfind(")") + 2:].split()
            return fields[19], fields[0]
        except (OSError, IndexError, ValueError):
            return None, None

    def _is_recording(self) -> bool:
        if not self.state:
            return False
        try:
            pid = int(self.state.get("pid", 0))
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if self.process is not None and self.process.pid == pid:
            if self.process.poll() is not None:
                return False
        current_ticks, process_state = self._process_info(pid)
        expected_ticks = str(self.state.get("start_ticks") or "")
        return bool(
            current_ticks
            and expected_ticks
            and current_ticks == expected_ticks
            and process_state != "Z"
        )

    @staticmethod
    def _validate_domain(domain_id: int) -> None:
        if not isinstance(domain_id, int) or not 0 <= domain_id <= 232:
            raise ValueError("ROS_DOMAIN_ID must be an integer from 0 to 232")

    def _existing_disk_path(self) -> Path:
        candidate = self.rosbag_root
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate

    def _available_disk_bytes(self) -> int:
        return int(shutil.disk_usage(self._existing_disk_path()).free)

    @staticmethod
    def _directory_size(path: Path | None) -> int:
        if path is None or not path.exists():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        for root, _, files in os.walk(path):
            for filename in files:
                try:
                    total += (Path(root) / filename).stat().st_size
                except OSError:
                    continue
        return total

    def _storage_info(self) -> dict[str, Any]:
        free_bytes = self._available_disk_bytes()
        free_gb = free_bytes / (1024 ** 3)
        output_path = Path(self.state["output_path"]) if self.state and self.state.get("output_path") else None
        minimum = self.settings.min_free_gb
        warning = self.settings.warning_free_gb
        return {
            "root": str(self.rosbag_root),
            "free_bytes": free_bytes,
            "free_gb": round(free_gb, 2),
            "warning_free_gb": warning,
            "minimum_free_gb": minimum,
            "low_space_warning": free_gb < warning,
            "cannot_start": free_gb < minimum,
            "current_recording_size_bytes": self._directory_size(output_path),
        }

    def _reconcile_state(self) -> bool:
        recording = self._is_recording()
        if self.state and self.state.get("status") in ACTIVE_STATUSES and not recording:
            self.state["status"] = "STOPPED"
            self.state["finished_at"] = self.state.get("finished_at") or datetime.now().astimezone().isoformat()
            self.state.pop("pid", None)
            self.state.pop("pgid", None)
            self.state.pop("start_ticks", None)
            self._save_state()
        return recording

    def status(self) -> dict[str, Any]:
        with self._lock:
            recording = self._reconcile_state()
            info = dict(self.state) if self.state else None
            if info is not None:
                for key in ("pid", "pgid", "start_ticks"):
                    info.pop(key, None)
            state_status = (
                self.state.get("status", "STOPPED")
                if self.state
                else "STOPPED"
            )
            if recording and state_status not in {"STARTING", "STOPPING"}:
                state_status = "RECORDING"
            return {
                "status": state_status,
                "recording": recording,
                "recording_info": info,
                "storage": self._storage_info(),
            }

    def list_topics(self, domain_id: int) -> list[str]:
        self._validate_domain(domain_id)
        env = ros_environment(self.settings.ros_setup, self.settings.ros_overlays, domain_id)
        result = subprocess.run(
            ["ros2", "topic", "list", "--no-daemon", "--spin-time", "1.0"],
            env=env,
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ros2 topic list failed")
        return sorted({
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().startswith("/") and TOPIC_PATTERN.fullmatch(line.strip())
        })

    def start(self, domain_id: int, topics: list[str]) -> dict[str, Any]:
        self._validate_domain(domain_id)
        if not topics:
            raise ValueError("Select at least one topic")
        unique_topics = list(dict.fromkeys(topics))
        invalid = [topic for topic in unique_topics if not isinstance(topic, str) or not TOPIC_PATTERN.fullmatch(topic)]
        if invalid:
            raise ValueError(f"Invalid topic name: {invalid[0]}")

        with self._lock:
            if self._reconcile_state():
                raise RuntimeError("A rosbag recording is already active")

            storage = self._storage_info()
            if storage["cannot_start"]:
                raise RuntimeError(
                    f"Insufficient free disk space: {storage['free_gb']:.2f} GB "
                    f"(minimum {self.settings.min_free_gb:.2f} GB)"
                )

            env = ros_environment(self.settings.ros_setup, self.settings.ros_overlays, domain_id)
            now = datetime.now().astimezone()
            date_directory = self.rosbag_root / f"domain_{domain_id}" / now.strftime("%Y-%m-%d")
            date_directory.mkdir(parents=True, exist_ok=True)
            bag_name = f"domain_{domain_id}_{now.strftime('%Y%m%d_%H%M%S_%f')}"
            output_path = date_directory / bag_name
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

            self.state = {
                "status": "STARTING",
                "domain_id": domain_id,
                "topics": unique_topics,
                "output_path": str(output_path),
                "started_at": now.isoformat(),
            }
            self._save_state()

            command = [
                "ros2", "bag", "record",
                "--topics", *unique_topics,
                "--output", str(output_path),
            ]
            process: subprocess.Popen | None = None
            try:
                with self.log_file.open("ab") as log_stream:
                    process = subprocess.Popen(
                        command,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log_stream,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
                time.sleep(0.25)
                if process.poll() is not None:
                    tail = self.log_file.read_text(errors="replace")[-2000:] if self.log_file.exists() else ""
                    raise RuntimeError(f"rosbag2 exited during startup. {tail}")

                ticks, _ = self._process_info(process.pid)
                if not ticks:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    raise RuntimeError("Could not verify rosbag2 process identity in /proc")

                self.process = process
                self.state.update({
                    "status": "RECORDING",
                    "pid": process.pid,
                    "pgid": process.pid,
                    "start_ticks": ticks,
                })
                self._save_state()
                return self.status()
            except Exception as exc:
                if process is not None and process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                self.state["status"] = "ERROR"
                self.state["error"] = str(exc)
                self.state["finished_at"] = datetime.now().astimezone().isoformat()
                self._save_state()
                raise

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self.state or not self._is_recording():
                return self.status()

            pid = int(self.state["pid"])
            pgid = int(self.state.get("pgid", pid))
            self.state["status"] = "STOPPING"
            self._save_state()

            try:
                os.killpg(pgid, signal.SIGINT)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                self.state["status"] = "ERROR"
                self.state["error"] = f"Permission denied while stopping rosbag2: {exc}"
                self._save_state()
                raise RuntimeError(self.state["error"]) from exc

            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline and self._is_recording():
                time.sleep(0.2)

            if self._is_recording():
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline and self._is_recording():
                    time.sleep(0.2)

            if self._is_recording():
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and self._is_recording():
                    time.sleep(0.1)

            if self._is_recording():
                self.state["status"] = "ERROR"
                self.state["error"] = "rosbag2 process did not exit after SIGKILL"
                self._save_state()
                raise RuntimeError(self.state["error"])

            if self.process is not None:
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass

            self.state["status"] = "STOPPED"
            self.state["finished_at"] = datetime.now().astimezone().isoformat()
            self.state.pop("pid", None)
            self.state.pop("pgid", None)
            self.state.pop("start_ticks", None)
            self.state.pop("error", None)
            self._save_state()
            return self.status()
