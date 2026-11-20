# API overview

The browser calls each registered Agent directly. There is no Manager login,
central host registry, session token, or API proxy in the current frontend.

All Agent control APIs require:

```http
Authorization: Bearer <agent-token>
```

Only `GET /health` and the static web files are public. Discovery metadata
never contains the Agent token.

## Agent public endpoints

- `GET /`: responsive web GUI served by the Agent.
- `GET /health`: liveness and non-secret Agent identity.
- `GET /static/{path}`: frontend assets.
- `GET /manifest.webmanifest`
- `GET /service-worker.js`
- `GET /icon.svg`

## Agent authenticated endpoints

### Status and peer discovery

- `GET /api/status`: PC and system information plus managed Launch states.
- `GET /api/peers`: this Agent identity and mDNS-discovered peers (ID, name,
  private IPv4 addresses, port, protocol). No credentials are shared.

### Launch management

- `GET /api/launches`
- `GET /api/launch-config`
- `PUT /api/launch-config`
- `POST /api/launch-config/validate`
- `GET /api/workspaces/packages?workspace=...`
- `GET /api/workspaces/launch-files?workspace=...&package=...`
- `POST /api/launches/{launch_id}/start`
- `POST /api/launches/{launch_id}/stop`
- `POST /api/launches/stop-running`

Only Launches registered in this Agent's `launches.yaml` are controlled.

### ROSbag

- `GET /api/rosbag/status`
- `GET /api/rosbag/topics?domain_id=N`
- `POST /api/rosbag/start`: JSON body with `domain_id` and `topics`.
- `POST /api/rosbag/stop`

Discovery and recording are performed on the Agent's own PC.

### System control

- `POST /api/shutdown`: request shutdown of this Agent's PC.

## QR pairing

- `POST /api/pairing/create`: authenticated; creates a cryptographically
  random one-time pairing code with a 300-second expiry. Only its SHA-256 hash
  is kept in Agent memory.
- `POST /api/pairing/complete`: public, one-time completion endpoint. A
  matching unexpired code is consumed before the Agent Token is returned.
  Reuse and expired codes are rejected with HTTP 401.

The QR contains the PWA URL, Agent address/port/identity/default Domain ID,
and the temporary one-time code. It never contains the long-lived Agent Token.
The CLI obtains a temporary code through the authenticated create endpoint.

## Discovery

Agents advertise `_ros-pc-manager._tcp.local.` over mDNS/DNS-SD.
A normal browser page cannot subscribe directly to mDNS multicast. Therefore,
the first Agent is registered manually or by QR code. Once one or more Agents
are registered, the GUI queries their authenticated `/api/peers` endpoint
and offers discovered Agents for QR or manual Token pairing.
