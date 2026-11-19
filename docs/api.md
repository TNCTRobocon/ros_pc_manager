# API overview

All /api/* endpoints except /api/login and /api/health require X-Manager-Session.

## Manager

- POST /api/login: password -> session token.
- GET /api/session: validate the current session.
- GET /api/hosts: registered PCs and online/offline probe results.
- POST /api/hosts: manual PC registration; returns the generated Agent token once.
- PUT /api/hosts/{id}: update display name, IP address, Agent port, and default Domain ID without rotating the Agent token.
- DELETE /api/hosts/{id}: remove a registration.
- PC create/update/delete operations regenerate config/hosts.yaml atomically from SQLite. The YAML contains public inventory fields only and never contains Agent tokens; SQLite remains authoritative.
- GET /api/hosts/{id}/status: system status and node names from Agent.
- GET /api/hosts/{id}/launches: registered launch state.
- POST /api/hosts/{id}/launches/start: start an allowlisted launch ID.
- POST /api/hosts/{id}/launches/stop: stop selected launch IDs or __running__.
- POST /api/hosts/{id}/shutdown: request OS shutdown.
- GET /api/domains: merged Domain ID choices.
- GET /api/rosbag/topics?domain_id=N: topic discovery on manager.
- GET /api/rosbag/status: recording state.
- POST /api/rosbag/start: start selected topics on one Domain ID.
- POST /api/rosbag/stop: stop and finalize the recording.

## Agent

Agent endpoints require Authorization: Bearer <token>.

- GET /health
- GET /api/status
- GET /api/launches
- POST /api/launches/{launch_id}/start
- POST /api/launches/{launch_id}/stop
- POST /api/launches/stop-running
- POST /api/shutdown
