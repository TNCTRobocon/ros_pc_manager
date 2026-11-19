# Architecture

## Components

1. Mobile browser: serves the responsive frontend from the manager server.
2. Manager server: authenticates browser sessions, stores manually registered PCs in SQLite, proxies allowlisted control actions to Agents, and owns the only rosbag2 recorder.
3. Agent: a per-PC FastAPI process protected by a unique bearer token. It owns only launches it starts from launches.yaml.
4. ROS 2 DDS: independent data plane. The manager server joins one selected ROS_DOMAIN_ID to discover and record topics.

## Launch ownership model

An entry in launches.yaml is a launch definition, not proof that a process is running. A launch appears as RUNNING only when the Agent has a matching managed state record and /proc/<pid>/stat still matches the process start time recorded at launch. External terminal starts are never discovered by name matching and never appear in the UI. The bulk-stop operation enumerates only the Agent's persisted managed states.

Each launch runs in a new process group. Stop sends SIGINT, waits, then escalates to SIGTERM/SIGKILL only if necessary. State is persisted to recover tracking after an Agent restart.

## ROSbag ownership model

Only one rosbag process may be active on the manager PC. The selected Domain ID is set in the recording process environment. Topic discovery is performed using ros2 topic list --no-daemon with the same Domain ID. The recording process uses an argument array (not a shell command string).

## API boundaries

- Browser -> Manager: JSON over HTTP, manager session token in X-Manager-Session.
- Manager -> Agent: JSON over HTTP, per-PC token in Authorization: Bearer.
- DDS: separate ROS 2 transport. Web authorization does not configure DDS security.
