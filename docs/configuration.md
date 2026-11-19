# Configuration

## server.yaml

Copy config/server.yaml.example to config/server.yaml. Important fields:

- server.host / server.port: bind address and web port.
- security.admin_password: leave empty to generate var/admin_password.txt, or provide a fixed value.
- paths.rosbag_root: parent-PC rosbag storage root.
- ros.setup: ROS distribution setup file.
- ros.overlays: parent-PC Workspace paths whose install/setup.bash is sourced before topic discovery and recording.
- limits.minimum_free_gb: minimum free disk space required to start a new recording.

## domains.yaml

Copy config/domains.yaml.example to config/domains.yaml. Manually configured IDs are combined with each registered PC default ID and its configured launch Domain IDs.

## Agent YAML

The Agent YAML is stored at /etc/ros-pc-manager/agent.yaml in a service deployment or agent/config/agent.yaml for local development. launches_file is resolved relative to the Agent YAML directory when it is a relative path.

For every launch entry, choose a stable unique id. display_name is only a UI label. The workspace must already be built, with install/setup.bash present.
