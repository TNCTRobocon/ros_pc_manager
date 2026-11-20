# Architecture

## Components

1. **Mobile browser / PWA**: static responsive frontend. The browser stores
   Agent inventory and per-Agent bearer tokens in IndexedDB.
2. **Per-PC Agent**: FastAPI server, managed ROS 2 Launch lifecycle,
   ROSbag recording, shutdown, and mDNS discovery.
3. **mDNS/DNS-SD**: Agents advertise their identity and discover peer
   metadata. Peer announcements contain no secrets and grant no control
   permissions.
4. **ROS 2 DDS**: independent data plane used by each Agent on its own PC.

There is no required parent PC, central Manager process, central database, or
always-on Agent. The browser sends each operation directly to the target Agent.

## Browser connection modes

- Recommended: host the static PWA on HTTPS (for example, GitHub Pages) and
  connect to LAN Agents directly. Chromium browsers may require the user to
  grant Local Network Access permission.
- Compatibility: open `http://<agent-ip>:8765/` to use the same GUI served
  by an Agent on the LAN. This HTTP mode is not suitable for PWA installation
  or Service Worker offline caching.

A normal browser page cannot receive mDNS multicast directly. Therefore, the
first Agent is added using its IP address through a one-time QR pairing link or
manual Token entry. The QR contains a 5-minute, one-time code, not the
long-lived Agent Token. The GUI then retrieves discovery results from any
registered, reachable Agent.

## Agent identity and authentication

Each Agent has a stable UUID in `agent.yaml` and an independent random
Bearer token. The browser stores tokens in its own IndexedDB. Agents never
share tokens with peers. The health endpoint and mDNS announcements expose
only non-secret identity information; all control and status APIs require the
Agent's Bearer token.

## Launch ownership model

An entry in `launches.yaml` is a launch definition, not proof that a process
is running. A launch appears as RUNNING only when the Agent has a matching
managed state record and `/proc/<pid>/stat` still matches the process start
time recorded at launch. External terminal starts are never discovered by
name matching and never appear in the UI. The bulk-stop operation enumerates
only the Agent's persisted managed states.

Each launch runs in a new process group. Stop sends SIGINT, waits, then
escalates to SIGTERM/SIGKILL only if necessary. State is persisted to recover
tracking after an Agent restart.

## ROSbag ownership model

Each Agent owns only its local ROSbag recorder. Topic discovery and recording
run on that Agent's PC using the selected ROS_DOMAIN_ID and the Agent's
configured ROS setup and overlays. There is no central recorder.

## API boundaries

- Browser -> target Agent: JSON over HTTP on the LAN, Agent-specific
  `Authorization: Bearer` token.
- Agent <-> Agent: mDNS/DNS-SD discovery metadata only; no proxy or remote
  process control.
- DDS: separate ROS 2 transport. Web authorization does not configure DDS
  security.

## Legacy Manager

The old Manager Server package, SQLite database, and deployment files remain
in the repository temporarily for rollback. The current frontend and setup
workflow do not use them. The legacy systemd service should be disabled after
the Agent-based workflow has been verified on the relevant PCs.
