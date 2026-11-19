from __future__ import annotations

import shlex
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def _load_environment(setup: str, overlays: tuple[str, ...]) -> dict[str, str]:
    setup_path = Path(setup)
    if not setup_path.is_file():
        raise RuntimeError(f"ROS setup file does not exist: {setup_path}")
    source_lines = [f"source {shlex.quote(str(setup_path))}"]
    for overlay in overlays:
        candidate = Path(overlay) / "install" / "setup.bash"
        if not candidate.is_file():
            raise RuntimeError(f"ROS overlay setup file does not exist: {candidate}")
        source_lines.append(f"source {shlex.quote(str(candidate))}")
    command = " && ".join(source_lines + ["env -0"])
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    env: dict[str, str] = {}
    for item in result.stdout.split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            env[key.decode(errors="replace")] = value.decode(errors="replace")
    return env


def ros_environment(setup: Path, overlays: tuple[Path, ...], domain_id: int) -> dict[str, str]:
    env = _load_environment(str(setup), tuple(str(p) for p in overlays)).copy()
    env["ROS_DOMAIN_ID"] = str(domain_id)
    return env
