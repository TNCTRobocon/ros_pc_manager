from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config import AgentSettings

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
PACKAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
ACTIVE_STATUSES = {"STARTING", "RUNNING", "STOPPING"}
STOPPABLE_STATUSES = {"STARTING", "RUNNING"}


class LaunchManager:
    """Manages only launch processes started by this Agent.

    It deliberately does not discover arbitrary ros2 launch processes with ps/pgrep.
    The PID plus Linux process start-time value prevents accidentally controlling a
    different process after PID reuse. State is persisted so an Agent restart can
    reconnect to launches that survived the Agent service restart.
    """

    def __init__(self, settings: AgentSettings):
        self.settings = settings
        self.settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.log_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.states: dict[str, dict[str, Any]] = self._load_state()

    def _load_state(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.settings.state_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): value
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def _save_state(self) -> None:
        self.settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.states, indent=2, ensure_ascii=False))
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        temp.replace(self.settings.state_file)

    @staticmethod
    def _proc_info(pid: int) -> tuple[str | None, str | None]:
        """Return (start_ticks, process_state) from /proc/<pid>/stat."""
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
            # comm is parenthesized and may itself contain spaces or ')'.
            fields = raw[raw.rfind(")") + 2:].split()
            # The remaining list starts at field 3 (state); starttime is field 22.
            return fields[19], fields[0]
        except (OSError, IndexError, ValueError):
            return None, None

    def _alive(self, state: dict[str, Any]) -> bool:
        try:
            pid = int(state["pid"])
        except (KeyError, TypeError, ValueError):
            return False
        ticks, proc_state = self._proc_info(pid)
        expected_ticks = str(state.get("start_ticks") or "")
        return bool(ticks and expected_ticks and ticks == expected_ticks and proc_state != "Z")

    def _load_launches(self) -> list[dict[str, Any]]:
        if not self.settings.launches_file.is_file():
            return []
        raw = yaml.safe_load(self.settings.launches_file.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError("launches.yaml must contain a YAML mapping")
        entries = raw.get("launches", [])
        if not isinstance(entries, list):
            raise ValueError("launches.yaml: 'launches' must be a list")

        ids: set[str] = set()
        result: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Each launch entry must be a YAML mapping")
            launch_id = str(entry.get("id", ""))
            if not ID_PATTERN.fullmatch(launch_id):
                raise ValueError(f"Invalid launch id: {launch_id!r}")
            if launch_id in ids:
                raise ValueError(f"Duplicate launch id: {launch_id}")
            ids.add(launch_id)
            result.append(entry)
        return result

    @staticmethod
    def _workspace_path(value: str | Path) -> Path:
        workspace = Path(str(value)).expanduser()
        if not workspace.is_absolute():
            raise ValueError("Workspaceは絶対パスで指定してください")
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise ValueError(f"Workspaceが存在しません: {workspace}")
        setup = workspace / "install" / "setup.bash"
        if not setup.is_file():
            raise ValueError(f"Workspaceがビルドされていません: {setup} がありません")
        return workspace

    @staticmethod
    def _share_roots(workspace: Path) -> list[Path]:
        install = workspace / "install"
        roots: list[Path] = []
        merged = install / "share"
        if merged.is_dir():
            roots.append(merged)
        try:
            prefixes = list(install.iterdir())
        except OSError:
            prefixes = []
        for prefix in prefixes:
            share = prefix / "share"
            if prefix.is_dir() and share.is_dir() and share not in roots:
                roots.append(share)
        return roots

    def workspace_packages(self, workspace_value: str | Path) -> list[str]:
        workspace = self._workspace_path(workspace_value)
        packages: set[str] = set()
        for share_root in self._share_roots(workspace):
            try:
                package_dirs = list(share_root.iterdir())
            except OSError:
                continue
            for package_dir in package_dirs:
                package_xml = package_dir / "package.xml"
                if not package_dir.is_dir() or not package_xml.is_file():
                    continue
                try:
                    package_name = (ET.parse(package_xml).getroot().findtext("name") or "").strip()
                except (ET.ParseError, OSError):
                    continue
                if package_name and PACKAGE_PATTERN.fullmatch(package_name):
                    packages.add(package_name)
        return sorted(packages)

    def workspace_launch_files(
        self, workspace_value: str | Path, package: str
    ) -> list[str]:
        if not PACKAGE_PATTERN.fullmatch(package):
            raise ValueError(f"Invalid ROS package name: {package!r}")
        workspace = self._workspace_path(workspace_value)
        if package not in self.workspace_packages(workspace):
            raise ValueError(f"Package is not installed in this Workspace: {package}")
        files: set[str] = set()
        for share_root in self._share_roots(workspace):
            launch_dir = share_root / package / "launch"
            if not launch_dir.is_dir():
                continue
            for path in launch_dir.iterdir():
                if path.is_file() and (
                    path.name.endswith(".launch.py")
                    or path.name.endswith(".launch.xml")
                    or path.name.endswith(".launch.yaml")
                    or path.name.endswith(".launch.yml")
                ):
                    files.add(path.name)
        return sorted(files)

    def get_launch_config(self) -> dict[str, Any]:
        with self._lock:
            entries = self._load_launches()
            workspaces = sorted({
                str(Path(str(entry.get("workspace", ""))).expanduser())
                for entry in entries if entry.get("workspace")
            })
            return {"launches": entries, "workspaces": workspaces}

    def validate_launch_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(entry, dict):
            raise ValueError("launch設定はオブジェクトで指定してください")
        launch_id = str(entry.get("id", "")).strip()
        if not ID_PATTERN.fullmatch(launch_id):
            raise ValueError("launch IDが不正です")
        display_name = str(entry.get("display_name", "")).strip()
        if not display_name or len(display_name) > 120:
            raise ValueError("表示名は1〜120文字で指定してください")

        workspace = self._workspace_path(str(entry.get("workspace", "")))
        package = str(entry.get("package", "")).strip()
        if not PACKAGE_PATTERN.fullmatch(package):
            raise ValueError(f"Package名が不正です: {package!r}")
        packages = self.workspace_packages(workspace)
        if package not in packages:
            raise ValueError(f"PackageがWorkspaceにインストールされていません: {package}")

        launch_file = str(entry.get("launch_file", "")).strip()
        if (
            not launch_file
            or "/" in launch_file
            or "\\" in launch_file
            or ".." in launch_file
            or launch_file not in self.workspace_launch_files(workspace, package)
        ):
            raise ValueError(f"LaunchファイルがWorkspace内に見つかりません: {launch_file!r}")

        try:
            domain_id = int(entry.get("ros_domain_id", self.settings.default_domain_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("ROS_DOMAIN_IDは整数で指定してください") from exc
        if not 0 <= domain_id <= 232:
            raise ValueError("ROS_DOMAIN_IDは0〜232で指定してください")

        arguments = entry.get("arguments", {}) or {}
        if not isinstance(arguments, dict):
            raise ValueError("Launch引数は名前と値のマップで指定してください")
        normalized_arguments: dict[str, Any] = {}
        for key, value in arguments.items():
            key = str(key).strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                raise ValueError(f"Launch引数名が不正です: {key!r}")
            if isinstance(value, (dict, list)) or value is None:
                raise ValueError(f"Launch引数 {key} は文字列・数値・真偽値で指定してください")
            normalized_arguments[key] = value

        return {
            "id": launch_id,
            "display_name": display_name,
            "workspace": str(workspace),
            "package": package,
            "launch_file": launch_file,
            "ros_domain_id": domain_id,
            "arguments": normalized_arguments,
        }

    def replace_launch_config(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(entries, list):
            raise ValueError("launchesはリストで指定してください")
        with self._lock:
            normalized = [self.validate_launch_entry(entry) for entry in entries]
            ids = [entry["id"] for entry in normalized]
            if len(ids) != len(set(ids)):
                raise ValueError("launch IDが重複しています")

            old_entries = {entry["id"]: entry for entry in self._load_launches()}
            new_entries = {entry["id"]: entry for entry in normalized}
            for launch_id, state in self.states.items():
                if self._alive(state):
                    old_entry = old_entries.get(launch_id)
                    new_entry = new_entries.get(launch_id)
                    if old_entry != new_entry:
                        raise RuntimeError(
                            f"実行中のlaunchは編集・削除できません。先に停止してください: {launch_id}"
                        )

            self.settings.launches_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings.launches_file.with_name(
                self.settings.launches_file.name + ".tmp"
            )
            payload = yaml.safe_dump(
                {"launches": normalized},
                allow_unicode=True,
                sort_keys=False,
            )
            temp.write_text(payload, encoding="utf-8")
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            temp.replace(self.settings.launches_file)

            for launch_id in list(self.states):
                if launch_id not in new_entries and not self._alive(self.states[launch_id]):
                    self.states.pop(launch_id, None)
            self._save_state()
            return self.get_launch_config()

    def list_launches(self) -> dict[str, Any]:
        with self._lock:
            items: list[dict[str, Any]] = []
            for entry in self._load_launches():
                launch_id = str(entry["id"])
                state = self.states.get(launch_id, {})
                alive = self._alive(state)
                status = str(state.get("status", "STOPPED"))

                if status in ACTIVE_STATUSES and not alive:
                    state["status"] = "STOPPED"
                    state["finished_at"] = state.get("finished_at") or datetime.now().astimezone().isoformat()
                    state.pop("pid", None)
                    state.pop("pgid", None)
                    state.pop("start_ticks", None)
                    self._save_state()
                    status = "STOPPED"
                elif not alive and status not in {"ERROR", "STOPPED"}:
                    status = "STOPPED"

                domain_id = int(entry.get("ros_domain_id", self.settings.default_domain_id))
                items.append({
                    "id": launch_id,
                    "display_name": str(entry.get("display_name") or launch_id),
                    "domain_id": domain_id,
                    "status": status if status in {"STARTING", "RUNNING", "STOPPING", "ERROR"} else "STOPPED",
                    "managed": bool(state.get("started_at")),
                    "started_at": state.get("started_at"),
                    "finished_at": state.get("finished_at"),
                    "pid": state.get("pid") if alive else None,
                    "log_path": state.get("log_path"),
                    "error": state.get("error"),
                })

            active_count = sum(item["status"] in STOPPABLE_STATUSES for item in items)
            return {
                "launches": items,
                "running_count": active_count,
                "managed_only": True,
            }

    def _launch_config(self, launch_id: str) -> dict[str, Any]:
        for entry in self._load_launches():
            if entry["id"] == launch_id:
                return entry
        raise KeyError(f"Launch is not registered: {launch_id}")

    def start(self, launch_id: str) -> dict[str, Any]:
        if not ID_PATTERN.fullmatch(launch_id):
            raise ValueError("Invalid launch id")

        with self._lock:
            entry = self._launch_config(launch_id)
            previous = self.states.get(launch_id, {})
            if self._alive(previous):
                raise RuntimeError(f"Launch is already running: {launch_id}")

            timestamp = datetime.now().astimezone()
            display_name = str(entry.get("display_name") or launch_id)
            domain_id = int(entry.get("ros_domain_id", self.settings.default_domain_id))
            self.states[launch_id] = {
                "status": "STARTING",
                "started_at": timestamp.isoformat(),
                "display_name": display_name,
                "domain_id": domain_id,
            }
            self._save_state()

            process: subprocess.Popen | None = None
            log_path: Path | None = None
            try:
                package = str(entry.get("package", ""))
                launch_file = str(entry.get("launch_file", ""))
                workspace = Path(str(entry.get("workspace", ""))).expanduser()

                if not PACKAGE_PATTERN.fullmatch(package):
                    raise ValueError(f"Invalid ROS package name: {package!r}")
                if (
                    not launch_file
                    or "/" in launch_file
                    or "\\" in launch_file
                    or ".." in launch_file
                ):
                    raise ValueError("launch_file must be a file name, not a path")
                if not workspace.is_dir():
                    raise ValueError(f"Workspace does not exist: {workspace}")
                if not self.settings.ros_setup.is_file():
                    raise ValueError(f"ROS setup file does not exist: {self.settings.ros_setup}")
                overlay_setup = workspace / "install" / "setup.bash"
                if not overlay_setup.is_file():
                    raise ValueError(
                        f"Workspace is not built or install/setup.bash is missing: {overlay_setup}"
                    )
                if not 0 <= domain_id <= 232:
                    raise ValueError("ros_domain_id must be between 0 and 232")

                arguments = entry.get("arguments", {}) or {}
                if not isinstance(arguments, dict):
                    raise ValueError("launch arguments must be a YAML mapping")
                arg_tokens: list[str] = []
                for key, value in arguments.items():
                    key = str(key)
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                        raise ValueError(f"Invalid launch argument name: {key!r}")
                    arg_tokens.append(f"{key}:={value}")

                command_parts = [
                    f"source {shlex.quote(str(self.settings.ros_setup))}",
                    f"source {shlex.quote(str(overlay_setup))}",
                    f"export ROS_DOMAIN_ID={domain_id}",
                ]
                command = (
                    " && ".join(command_parts)
                    + " && exec ros2 launch "
                    + " ".join(
                        shlex.quote(value)
                        for value in [package, launch_file, *arg_tokens]
                    )
                )

                log_path = self.settings.log_root / (
                    f"{launch_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}.log"
                )
                with log_path.open("ab") as log_stream:
                    process = subprocess.Popen(
                        ["/bin/bash", "-c", command],
                        cwd=str(workspace),
                        stdin=subprocess.DEVNULL,
                        stdout=log_stream,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )

                time.sleep(0.2)
                if process.poll() is not None:
                    tail = log_path.read_text(errors="replace")[-2000:]
                    raise RuntimeError(f"Launch exited during startup. {tail}")

                ticks, _ = self._proc_info(process.pid)
                if not ticks:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    raise RuntimeError("Could not verify launch process identity in /proc")

                self.states[launch_id] = {
                    "status": "RUNNING",
                    "pid": process.pid,
                    "pgid": process.pid,
                    "start_ticks": ticks,
                    "started_at": timestamp.isoformat(),
                    "display_name": display_name,
                    "domain_id": domain_id,
                    "log_path": str(log_path),
                }
                self._save_state()
                return {
                    "ok": True,
                    "message": "Launch started",
                    "launch_id": launch_id,
                    "pid": process.pid,
                }
            except Exception as exc:
                # If startup failed after Popen, do not leave an untracked launch behind.
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
                self.states[launch_id] = {
                    "status": "ERROR",
                    "started_at": timestamp.isoformat(),
                    "finished_at": datetime.now().astimezone().isoformat(),
                    "display_name": display_name,
                    "domain_id": domain_id,
                    "log_path": str(log_path) if log_path else None,
                    "error": str(exc),
                }
                self._save_state()
                raise

    def stop(self, launch_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.states.get(launch_id)
            if not state:
                return {
                    "ok": True,
                    "message": "Launch was not started by this Agent",
                    "launch_id": launch_id,
                }
            if not self._alive(state):
                state["status"] = "STOPPED"
                state["finished_at"] = state.get("finished_at") or datetime.now().astimezone().isoformat()
                state.pop("pid", None)
                state.pop("pgid", None)
                state.pop("start_ticks", None)
                self._save_state()
                return {"ok": True, "message": "Launch is not running", "launch_id": launch_id}

            pid = int(state["pid"])
            pgid = int(state.get("pgid", pid))
            state["status"] = "STOPPING"
            self._save_state()

            try:
                os.killpg(pgid, signal.SIGINT)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                state["status"] = "ERROR"
                state["error"] = f"Permission denied while stopping launch: {exc}"
                self._save_state()
                raise RuntimeError(state["error"]) from exc

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and self._alive(state):
                time.sleep(0.2)

            if self._alive(state):
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline and self._alive(state):
                    time.sleep(0.2)

            if self._alive(state):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and self._alive(state):
                    time.sleep(0.1)

            if self._alive(state):
                state["status"] = "ERROR"
                state["error"] = "Launch process did not exit after SIGKILL"
                self._save_state()
                raise RuntimeError(state["error"])

            state["status"] = "STOPPED"
            state["finished_at"] = datetime.now().astimezone().isoformat()
            state.pop("pid", None)
            state.pop("pgid", None)
            state.pop("start_ticks", None)
            state.pop("error", None)
            self._save_state()
            return {"ok": True, "message": "Launch stopped", "launch_id": launch_id}

    def stop_running(self) -> dict[str, Any]:
        """Stop only currently active launches that are still registered in YAML."""
        with self._lock:
            registered_ids = {str(entry["id"]) for entry in self._load_launches()}
            targets = [
                launch_id
                for launch_id in registered_ids
                if self._alive(self.states.get(launch_id, {}))
            ]
            results: list[dict[str, Any]] = []
            errors: list[str] = []
            for launch_id in sorted(targets):
                try:
                    results.append(self.stop(launch_id))
                except Exception as exc:
                    errors.append(f"{launch_id}: {exc}")
            return {
                "ok": not errors,
                "stopped": [item["launch_id"] for item in results],
                "results": results,
                "errors": errors,
            }
