# JingleAllTheDay Remote Control API

This document describes the LAN remote-control HTTP/WebSocket API exposed by the
desktop app (`remote_server.py`), used by the bundled web client under
[webclient/](webclient/) and available to any other client on the same network.

## Overview

- **Transport**: plain HTTP (no TLS) on a configurable, non-standard TCP port.
  Not intended for exposure beyond a trusted LAN.
- **Default port**: `8765` (Options > Server Port). Changeable at runtime; a
  restart of the server (Server menu, or automatically on port change) is
  required to rebind.
- **Enable/disable**: Options > "Auto-start on launch" controls whether the
  server starts automatically when the app opens. It can always be
  started/stopped/restarted manually from the **Server** menu regardless of
  that setting.
- **Auth model**: two shared PINs, set in Options — **Guest PIN** and **Admin
  PIN**. Status and library browsing are public/read-only (no PIN required).
  Anything that changes playback requires a PIN; which PIN was sent decides
  the caller's **role** (`admin` or `user`/guest, see [Roles](#roles) below).
  If neither PIN is configured, all control endpoints return `403`.
- **Content type**: all request/response bodies are JSON.

## Roles

Every PIN-protected request is evaluated server-side against both configured
PINs to resolve a role — the client never gets to declare its own role:

- **`admin`** — PIN matches the Admin PIN. Full legacy behavior: `POST
  /api/playback/output` and the `live` flag on `POST /api/playback/play`
  actually change the desktop app's real output device, exactly as before
  this feature existed.
- **`user`** (guest) — PIN matches the Guest PIN. Guests can never change the
  host's real Live/Preview device routing, even by calling the API directly:
  - `POST /api/playback/output` never touches the host; it only reports
    whether the requested mode is currently allowed (see below).
  - `POST /api/playback/play` ignores the request's `live` flag and always
    plays through the host at the host's *actual current* Live/Preview state.
  - Guests are expected to preview jingles locally in their own browser via
    `GET /api/audio` instead (see [Local preview](#local-preview-guest-only)).

**Admin PIN has no fallback.** If the Admin PIN is left blank, nobody gets
the `admin` role — not even Guest PIN holders. Leave it blank to keep every
remote user in guest/preview-only mode.

## Authentication

Control endpoints (everything under `/api/playback/*`) require the header:

```
X-Remote-Pin: <pin>
```

Behavior:
- No PIN configured in Options → `403 { "ok": false, "error": "Set a PIN in Options to enable remote control." }`
- Wrong/missing header value (matches neither PIN) → `403 { "ok": false, "error": "Invalid or missing PIN." }`
- `GET /api/status` and `GET /api/library` never require the PIN.

Use `POST /api/login` (see below) to resolve which role a PIN has *before*
sending it on every subsequent request — useful for a client to decide how
to present the Live/Preview toggle and Play button.

Every control request (successful or rejected) is recorded in the in-app
Server > Diagnostics log with the caller's IP, action, target, and result.

## Endpoints

### `GET /api/status`

Public. Returns the current now-playing snapshot (same shape pushed over the
WebSocket — see [Status object](#status-object)).

### `GET /api/library`

Public. Query parameters (all optional):

| Param           | Type   | Default | Meaning                                                    |
|-----------------|--------|---------|--------------------------------------------------------------|
| `search`        | string | `""`    | Free-text query.                                            |
| `scope`         | string | `all`   | Where to match: `all`, `name`, `tag`, or `path`.             |
| `category`      | string | `""`    | Comma/semicolon-separated category filter.                  |
| `category_mode` | string | `any`   | `any` (OR) or `all` (AND) match against `category`.          |
| `limit`         | int    | `200`   | Max items to return. `0` means "all matching items".        |
| `offset`        | int    | `0`     | Pagination offset into the filtered result set.              |

This mirrors the exact search/filter semantics used by the desktop library
table (same `filter_jingle_records()` helper), so a search here matches what
you'd see typing the same query into the desktop app.

Response:

```json
{
  "total": 2727,
  "items": [
    {
      "path": "C:\\Users\\me\\Samples\\Applause.mp3",
      "name": "Applause",
      "folder": "Samples",
      "categories": ["sound effect"],
      "duration_seconds": 9.24,
      "size_bytes": 152034
    }
  ]
}
```

`total` is the count of items matching the filter (before `limit`/`offset`
are applied), so clients can page with `offset += limit` until
`offset >= total`.

### `POST /api/login`

Public (body-based, not header-based — used to *discover* a role before
sending a PIN as a header elsewhere). Request body:

```json
{ "pin": "1234" }
```

Response: `{ "ok": true, "role": "admin" | "user" }` or
`{ "ok": false, "error": "Invalid PIN." }`.

### `GET /api/audio`

**PIN required** (either Guest or Admin PIN). Streams the raw audio bytes for
a jingle so a client can play it locally (e.g. in an HTML `<audio>` element)
without affecting the desktop app at all.

```
GET /api/audio?path=C%3A%5CUsers%5Cme%5CSamples%5CApplause.mp3
X-Remote-Pin: 1234
```

`path` must exactly match a `path` from `/api/library` — the server
validates it against the scanned library index (not raw filesystem access),
so arbitrary files outside the library can't be read this way. Returns the
file bytes with a guessed `Content-Type`, or `404` if the path doesn't match
a known jingle.

### `POST /api/playback/play`

**PIN required.** Selects the jingle by path, applies loop mode and Live/
Preview mode, then starts playback (equivalent to selecting the row and
pressing Play in the desktop app).

Request body:

```json
{
  "path": "C:\\Users\\me\\Samples\\Applause.mp3",
  "loop_mode": "off",
  "live": true
}
```

| Field       | Type   | Default | Notes                                             |
|-------------|--------|---------|----------------------------------------------------|
| `path`      | string | —       | Must exactly match a `path` from `/api/library`.   |
| `loop_mode` | string | `"off"` | `off`, `loop`, or `continuous`.                    |
| `live`      | bool   | `true`  | `true` = Live output, `false` = Preview output. **Admin PIN only** — guest requests always play at the host's actual current Live/Preview state regardless of this field. |

Response: `{ "ok": true, ...status }` on success, or
`{ "ok": false, "error": "not_found" | "missing_file" | "playback_failed" }`.

### `POST /api/playback/pause`

**PIN required.** No body. Toggles pause/resume (same as pressing the
Play/Pause button while something is playing or paused). Response:
`{ "ok": true, ...status }`.

### `POST /api/playback/stop`

**PIN required.** No body. Stops playback entirely. Response:
`{ "ok": true, ...status }`.

### `POST /api/playback/mode`

**PIN required.** Sets the loop mode explicitly (not a cycle/toggle).

```json
{ "loop_mode": "loop" }
```

`loop_mode` must be `off`, `loop`, or `continuous`. Response:
`{ "ok": true, ...status }`.

### `POST /api/playback/output`

**PIN required.** Behavior depends on role:

- **Admin**: sets Live/Preview mode explicitly, changing the real device —
  identical to the legacy behavior.
- **Guest**: never changes the real device. Requesting `live: true` while the
  host is currently in Preview is rejected with an error the client should
  surface as an alert; requesting `live: false` (or `live: true` while the
  host is already Live) is acknowledged as `ok` and only affects the guest's
  own local toggle/UI state.

```json
{ "live": false }
```

Response (admin): `{ "ok": <applied>, ...status }`. `ok` is `false` if
Preview mode was requested but is currently disabled (Live and Preview
devices are the same in Options).

Response (guest, denied): `{ "ok": false, "error": "Cannot switch to Live - the Host hasn't engaged Live mode yet.", ...status }`.

Response (guest, allowed): `{ "ok": true, ...status }`.

### Local preview (guest only)

When a guest's local toggle is set to Preview, the bundled web client never
calls `/api/playback/play` — it instead fetches `GET /api/audio` for the
selected jingle and plays it in the browser via a hidden `<audio>` element,
so the desktop app and its real output are completely unaffected. Play/
Pause/Stop in that state control the local `<audio>` element rather than the
remote endpoints. If the guest flips their toggle to Live (only meaningful
once the host is actually Live), Play goes back to calling
`/api/playback/play` normally.

### `WS /ws/status`

Public, no PIN. On connect, the server registers the client (IP + connect
time) in Server > Diagnostics, then pushes a [status object](#status-object)
as a JSON text frame roughly every 250 ms until the connection closes.
Clients should reconnect (with backoff) on close/error — the bundled web
client retries every 2 seconds.

## Status object

Returned by `GET /api/status`, embedded in every `/api/playback/*` response,
and streamed over `/ws/status`:

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
| `current_path`      | string  | Full path, `""` when nothing is loaded     |
| `position_seconds`  | number  | Current playback position                 |
| `duration_seconds`  | number  | Clip/track duration                        |
| `loop_mode`         | string  | `off`, `loop`, `continuous`                |

## Scope / limitations

- Mirrors **main window** playback only: library search/browse, play/pause/
  stop, loop/continuous, Live/Preview, and progress. Sample Pads and
  Playlists are not exposed through this API.
- No TLS, no per-client accounts — two shared PINs (Guest/Admin) gate control
  actions and resolve a role server-side. Treat this as a trusted-LAN
  convenience feature, not a hardened public API.

## Example (curl)

```bash
# Browse/search (no PIN needed)
curl "http://192.168.1.50:8765/api/library?search=applause&limit=10"

# Play a jingle (PIN required)
curl -X POST "http://192.168.1.50:8765/api/playback/play" \
  -H "Content-Type: application/json" \
  -H "X-Remote-Pin: 1234" \
  -d '{"path": "C:\\Users\\me\\Samples\\Applause.mp3", "loop_mode": "off", "live": true}'

# Stop
curl -X POST "http://192.168.1.50:8765/api/playback/stop" -H "X-Remote-Pin: 1234"
```
