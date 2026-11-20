# Configuration

## Agent YAML

The Agent configuration is stored at `agent/config/agent.yaml` in a
development checkout. The service setup script creates it from
`agent/config/agent.yaml.example` if it does not exist.

Important fields:

- `agent.id`: stable UUID for this Agent. Generated once during setup.
- `agent.name`: display name shown in the smartphone GUI.
- `agent.host` / `agent.port`: API bind address and port (default 8765).
- `agent.token`: per-Agent random Bearer token. Generated if blank or left
  at the example placeholder; valid existing tokens are preserved.
- `agent.ros_setup`: ROS distribution setup file.
- `agent.default_domain_id`: default ROS_DOMAIN_ID.
- `ros.overlays`: additional workspace paths; each workspace's
  `install/setup.bash` is sourced before topic discovery or recording.
- `discovery.enabled`: enable mDNS advertisement and peer discovery.
- `paths.launches_file`: Launch definition YAML.
- `paths.state_file`: persisted managed Launch state.
- `paths.log_root`: Agent process log root.
- `paths.rosbag_root`: local ROSbag output root.
- `paths.rosbag_state`: persisted ROSbag state.
- `limits.minimum_free_gb`: minimum disk free space required to start a new
  recording.
- `limits.warning_free_gb`: low-storage warning threshold.

Relative paths are resolved relative to the Agent YAML directory. User home
shortcuts such as `~/.local/state/...` are expanded.

## Launches YAML

The Launch configuration is stored at the path referenced by
`paths.launches_file` (normally `agent/config/launches.yaml`).

For every launch entry, choose a stable unique ID. `display_name` is only a
UI label. The workspace must already be built, with
`install/setup.bash` present. Only registered Launch definitions can be
started through the Agent API.

## Smartphone storage

The browser stores the registered Agent list, connection address, per-Agent
Token, and display preferences in IndexedDB. Clearing the browser's site data
removes these credentials, so the Agents must be paired again using their
existing Tokens. The Agent-side Token is not rotated by clearing browser data.

## Discovery and network

mDNS/DNS-SD uses UDP port 5353 and service type
`_ros-pc-manager._tcp.local.`. Peer discovery requires multicast to work
between the PCs on the same LAN. IP address registration remains available
when mDNS is blocked by Wi-Fi client isolation, VLANs, or firewall rules.

## Legacy Manager Server files

`config/server.yaml`, `config/domains.yaml`, and
`backend/pc_manager_server` are retained only for rollback during migration.
They are not used by the current browser GUI or Agent-only setup workflow.
