# JingleAllTheDay Remote Control API

This document describes the remote-control architecture as of the
jingleserver relay redesign: the desktop app no longer hosts an inbound
server. Instead it dials **out** to a middleman server called
**jingleserver** (see [jingleserver/](jingleserver/)), and browsers talk to
jingleserver too. jingleserver relays commands between the two.

```
Browser  <--HTTP/WS, session cookie-->  jingleserver  <--WebSocket, device token-->  Desktop app
(webclient/)                            (jingleserver/)                              (remote_server.py)
```

- The desktop app is always the one initiating the connection (outbound),
  so no port-forwarding is needed on the jingle machine's network.
- jingleserver is a standalone Python/FastAPI service you run on a server
  you control (see [jingleserver/README.md](jingleserver/README.md) for
  setup, the CLI, systemd unit, and Caddy reverse-proxy config).
- Real user accounts (username/password, role `admin` or `user`) replace
  the old shared PINs for the web app. A separate per-machine **device
  token** (issued via `jingleserver adddevice`) authenticates the desktop
  app's connection to jingleserver.

## Desktop app configuration

Tools > Options > Remote Server:

- **Auto-connect on launch** — whether the app dials jingleserver
  automatically on startup (Server menu always has manual Connect/
  Disconnect/Reconnect regardless of this setting).
- **Server Address** — jingleserver's host, e.g. `jingles.brianpclark.com`
  or `192.168.1.50:47030`. Bare hosts default to `wss://`; you can specify
  `ws://host:port` explicitly for a plain (non-TLS) LAN jingleserver.
- **Device Token** — from `jingleserver adddevice <label>` on the server.
- **Test Connection** — attempts a one-shot handshake against the
  configured address/token and reports success/failure inline.
- **Cache Backup** reminder interval — see [Offline cache](#offline-cache).

## Desktop ↔ jingleserver: agent protocol

WebSocket endpoint: `wss://<jingleserver>/agent/connect`.

1. Desktop connects and sends a JSON hello frame:
   ```json
   { "token": "<device token>", "label": "my-hostname" }
   ```
2. jingleserver validates the token against its `devices` table. On success
   it replies `{ "type": "ack", "ok": true }`; on failure it closes the
   socket (code `4401`). A new agent connection always replaces any
   previously connected one (only one jingle machine drives the webapp at a
   time in this version).
3. After the ack, both JSON text frames and binary frames flow over the
   same connection:

   **jingleserver → desktop** (JSON text): a command envelope
   ```json
   { "type": "command", "id": 42, "action": "play", "kwargs": { "path": "...", "loop_mode": "off", "live": true } }
   ```
   Actions: `play`, `toggle_pause`, `stop`, `set_loop_mode`, `set_live_mode`,
   `get_library`, `get_audio` (see [Audio streaming](#audio-streaming-guest-local-preview-works-offline-too)
   for why `get_audio` doesn't use the normal response shape).

   **desktop → jingleserver** (JSON text): a matching reply
   ```json
   { "type": "response", "id": 42, "data": { "ok": true, "state": "playing", "...": "..." } }
   ```

   **desktop → jingleserver** (JSON text, unsolicited, ~4×/sec): a status push
   ```json
   { "type": "status", "data": { "state": "playing", "is_live_mode": true, "current_name": "...", "current_path": "...", "position_seconds": 3.4, "duration_seconds": 9.2, "loop_mode": "off" } }
   ```

Desktop-side implementation: `remote_server.py`'s `RemoteRelayClient` (background
asyncio thread, reconnects with backoff 1s→30s on any failure). Incoming
commands are marshaled onto the Qt main thread via `RemoteServerBridge`
using `Qt.ConnectionType.BlockingQueuedConnection`, calling the same
`MainWindow.remote_*` methods the old inbound server used
(`remote_play`, `remote_get_status`, `remote_get_library`, etc.) — only the
transport changed, not the desktop-side playback integration surface.

## Browser ↔ jingleserver: web API

All endpoints are served by jingleserver (`jingleserver/jsrv/web.py`), which
also hosts the static webclient (`webclient/`) at `/`.

### Authentication

Session-cookie based (`jsid`, httponly + secure + `SameSite=Lax`), backed by
a server-side `sessions` table (so `jingleserver deluser`/`setrole` can
revoke access immediately — no stateless tokens to wait out).

- `POST /api/login` — body `{ "username": "...", "password": "..." }`.
  Sets the session cookie. Response: `{ "ok": true, "username", "role" }`
  (role is `admin` or `user`) or `401`.
- `POST /api/logout` — clears the session.
- `GET /api/session` — `{ "authenticated": true, "username", "role" }` or
  `{ "authenticated": false }`. Used by the webclient on load to decide
  whether to show the login screen or the app.

All other `/api/*` endpoints below require a valid session (`401` if not
authenticated).

### `GET /api/status`

If no desktop agent is connected: `{ "agent_connected": false }`.
Otherwise: `{ "agent_connected": true, ...status }` (see
[Status object](#status-object)) — served from jingleserver's cached copy of
the agent's last status push, so this never blocks on the agent.

### `WS /ws/status`

Same payload shape as `GET /api/status`, pushed every ~500ms. Requires the
session cookie to be present at handshake time (closes with code `4401`
otherwise).

### `GET /api/library`

Query params: `search`, `scope` (`all`/`name`/`tag`/`path`), `category`,
`category_mode` (`any`/`all`), `limit` (default 200, `0` = all), `offset`.
These are relayed verbatim to the desktop's `remote_get_library()`, so
search semantics match the desktop app's own library table exactly.

- **Agent connected**: `{ "agent_connected": true, "total": N, "items": [...] }`,
  each item `{ cache_id, name, categories, duration_seconds, size_bytes }`.
- **Agent not connected**: falls back to jingleserver's offline cache —
  `{ "agent_connected": false, "offline": true, "generated_at": <epoch>, "items": [...] }`
  (items here have `{ name, path, categories, duration_seconds }` — the same
  `path` field as the online shape, so the webclient can request
  `GET /api/audio?path=...` identically whether an agent is connected or
  not). If nothing has ever been cached: `{ "agent_connected": false, "offline": true, "items": [], "message": "no jingle machine currently connected" }`.

### `POST /api/playback/play` / `pause` / `stop` / `mode` / `output`

Same request/response contract as the pre-relay API (bodies below), relayed
to the connected agent. `409 { "ok": false, "error": "agent_not_connected" }`
if no desktop app is connected; `504 { "ok": false, "error": "agent_timeout" }`
if the agent doesn't answer in time.

- `POST /api/playback/play` — `{ "cache_id": "...", "loop_mode": "off", "live": true }`.
  For non-admin users, `live` is always overridden server-side to the host's
  actual current `is_live_mode` (guests can never force real Live/Preview
  routing, even via a crafted request).
- `POST /api/playback/pause` / `POST /api/playback/stop` — no body.
- `POST /api/playback/mode` — `{ "loop_mode": "off" | "loop" | "continuous" }`.
  Rejected (`403`) for non-admins while the host is in Preview (guests only
  mirror the host's loop mode while actually riding the host's Live output).
- `POST /api/playback/output` — `{ "live": true|false }`. Admins actually
  change the host's device routing. Non-admins never touch the real device:
  requesting `live: true` while the host isn't already Live is rejected
  (`403`); anything else is acknowledged as a no-op affecting only the
  caller's own local UI state.

### Audio streaming (guest local preview, works offline too)

`GET /api/audio/<cache_id>` — the opaque ID returned by `/api/library`.
The server resolves the ID to its private library record, so browsers cannot
request arbitrary desktop paths. This intentionally streams progressively instead of buffering the
whole file, so large jingles (100MB+) start playing immediately instead of
after a full download, and works even with **no agent connected** as long
as the file has been cached at least once:

1. jingleserver first checks for a cached copy at
   `cache/audio/live_<sha256(path)>.<ext>` — a deterministic name computed
   from `path` alone, used both by the write-through live-preview cache
   *and* by the desktop app's manual Offline Cache Backup (see
   [Offline cache](#offline-cache)), so either path populates the same
   cache slot. If found, it's served directly via `FileResponse`, which
   supports HTTP Range requests natively (instant seek, minimal latency,
   and works with no agent connected at all).
2. Otherwise, if an agent is connected, jingleserver asks it to stream the
   file: the desktop app sends the bytes over the *same* `/agent/connect`
   WebSocket in 64KB **binary** frames (each prefixed with a 4-byte
   big-endian request id, so multiple concurrent previews never get their
   chunks crossed), bracketed by JSON control frames
   `{ "type": "audio_start", "id", "size", "content_type" }` and
   `{ "type": "audio_end", "id" }` (or `{ "type": "audio_error", "id", "error" }`).
   jingleserver forwards each chunk to the browser as it arrives
   (`StreamingResponse`, with an explicit `Content-Length` header taken from
   `audio_start`'s `size` — this, rather than chunked transfer-encoding, is
   what lets browsers such as Safari/iOS treat it as a normal progressive
   download instead of buffering the whole response before starting
   playback) while simultaneously writing it to the same `live_<sha256(path)>`
   cache path above — so only the *first* preview of a given file ever pays
   the round-trip through the agent.
3. `404 jingle_not_found` for an unknown ID, `409 agent_not_connected` if a
  known jingle is neither cached nor connected, and `504 agent_audio_timeout`
  if the relay cannot begin a transfer in time. Cached responses include
  `X-Jingle-Cache: hit` and `X-Jingle-Cache-Bytes` diagnostic headers.

The webclient plays this by pointing `<audio src>` straight at the endpoint
(no `fetch`/`blob()` — letting the browser's own HTTP client handle
progressive buffering and Range requests is what makes large-file preview
start quickly), and always attempts local preview for Play when not
actively mirroring a connected host's Live output — including with no
agent connected at all, so cached jingles remain previewable while the
jingle machine is offline.

For `user` accounts only, cached lossless sources (WAV, FLAC, AIFF, ALAC, or
PCM) are converted once on jingleserver to a 96 kbps AAC/M4A preview and the
converted copy is reused for later browser-local previews. This reduces LTE
startup time and transfer size. `admin` accounts always receive the original
cached file. The Ubuntu host requires `ffmpeg`; if it is unavailable or a
conversion fails, the server logs a warning and safely serves the original.

## Offline mode (no agent connected)

When a user is logged in but no jingle machine is connected:

- The webclient shows an offline banner with a **Refresh** button.
- Playback control of the real jingle machine (pause/stop/loop/Live-Preview
  mode) is disabled, since there's no host to control — but **Play still
  works** for any jingle that's been cached (see [Offline cache](#offline-cache)
  below), via the same browser-local preview path guests normally use.
- The library still displays from jingleserver's cache (see
  `GET /api/library` above) if one exists; otherwise a "no jingle machine
  currently connected" message is shown.
- As soon as a desktop app connects, `agent_connected` flips to `true` on
  the next status push/library fetch and the webapp returns to full live
  control automatically.

## Offline cache

jingleserver persists a jingle library snapshot + the audio files
themselves under `JINGLESERVER_CACHE_DIR` (`cache/library_cache.json` +
`cache/audio/`), so guests can still browse **and preview** the library
while no jingle machine is connected.

- **Desktop app**: File > "Offline Cache Backup..." (enabled only while
  connected) uploads jingle files plus a manifest to jingleserver. It's
  incremental: `GET /agent/cache/status` first returns `{ "files": { "<relpath>": <size_bytes>, ... } }`
  for everything already cached, and the desktop app skips re-uploading any
  file whose deterministic cache relpath (`live_<sha256(path)>.<ext>`,
  matching [Audio streaming](#audio-streaming-guest-local-preview-works-offline-too)'s
  scheme) already exists there with a matching size — only new or
  changed files are actually uploaded via `POST /agent/cache/file?relpath=...`.
  A final `POST /agent/cache/manifest` always re-submits the full current
  item list (including unchanged/skipped ones) so the offline library
  listing stays accurate. All three endpoints are authenticated with the
  `X-Device-Token` header, not the session cookie — these are
  agent-to-server calls, not browser calls. A popup on the desktop app
  reports how many files were uploaded vs. already up to date.
- A reminder dialog (Options > Cache Backup interval, default 7 days) offers
  **Backup Now** / **Remind me in 24 hours** / **Ignore for N days**.
- The write-through cache described in
  [Audio streaming](#audio-streaming-guest-local-preview-works-offline-too)
  also populates this same cache incrementally as guests preview jingles
  live, independent of the manual backup action — both paths use the same
  deterministic `live_<sha256(path)>` naming, so they converge on one
  cache entry per jingle regardless of how it got there.

## Status object

```json
{
  "state": "playing",
  "is_live_mode": true,
  "current_name": "Applause",
  "current_path": "C:\\Users\\me\\Samples\\Applause.mp3",
  "position_seconds": 3.42,
  "duration_seconds": 9.24,
  "loop_mode": "off"
}
```

| Field              | Type    | Values                                    |
|---------------------|---------|--------------------------------------------|
| `state`             | string  | `stopped`, `playing`, `paused`             |
| `is_live_mode`      | bool    | `true` = Live output, `false` = Preview    |
| `current_name`      | string  | Jingle name, `""` when nothing is loaded   |
| `current_cache_id`  | string  | Opaque ID, omitted when nothing is loaded   |
| `position_seconds`  | number  | Current playback position                 |
| `duration_seconds`  | number  | Clip/track duration                        |
| `loop_mode`         | string  | `off`, `loop`, `continuous`                |

## Scope / limitations

- Mirrors **main window** playback only: library search/browse, play/pause/
  stop, loop/continuous, Live/Preview, and progress. Sample Pads and
  Playlists are not exposed through this API.
- Only one desktop agent can drive the webapp at a time (v1 scope); the
  `devices` table supports issuing multiple device tokens for future
  multi-machine support, but jingleserver only tracks one "current" agent
  connection.
- jingleserver should bind to `127.0.0.1` only, with Caddy (or another
  reverse proxy) terminating TLS and forwarding the public domain to it —
  see [jingleserver/README.md](jingleserver/README.md).

## Example (curl)

```bash
# Log in (stores the session cookie in cookies.txt)
curl -c cookies.txt -X POST "https://jingles.brianpclark.com/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "brian", "password": "..."}'

# Browse/search
curl -b cookies.txt "https://jingles.brianpclark.com/api/library?search=applause&limit=10"

# Play a jingle
curl -b cookies.txt -X POST "https://jingles.brianpclark.com/api/playback/play" \
  -H "Content-Type: application/json" \
  -d '{"path": "C:\\Users\\me\\Samples\\Applause.mp3", "loop_mode": "off", "live": true}'

# Stop
curl -b cookies.txt -X POST "https://jingles.brianpclark.com/api/playback/stop"
```
