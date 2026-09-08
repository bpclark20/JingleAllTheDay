# Plan: Discord Bot as a Second Remote-Control Target
(Saved from planning session on 2026-09-07 — pick this up later by opening this file.)

## Decisions (confirmed with user)
- Single-tenant: one bot token, one Discord guild (the personal server). No OAuth/multi-guild linking.
- Voice channel picker: bot enumerates the guild's voice channels live via discord.py; webapp shows a dropdown (admin-only to connect/disconnect).
- Reuse existing AgentManager command/status conventions, generalized with a "control target" concept rather than making the bot a WebSocket "agent" (it runs in-process, no relay handshake needed).
- Discord playback source is cache-only: bot always plays from `jingleserver/cache/audio/...` files. When desktop is online and file isn't cached yet, jingleserver first fully downloads it from the desktop agent (reusing existing tee-to-cache logic) before starting Discord playback. When desktop is offline, only already-cached jingles are playable (same limit as today's browser preview).
- Bot process model: runs in-process inside the same asyncio event loop as the FastAPI/uvicorn app (single systemd unit `jingleserver.service`, no new service file).
- Independence model (scenario 1): only one "control target" (`desktop` or `discord`) receives webapp playback commands at a time; switching target does NOT stop whatever the other target is already doing on its own (desktop keeps playing locally regardless of remote command routing).
- Scenario 2 auto-prompt: implemented client-side — webapp already polls `agent_connected` in status; add a `control_target` field to the same payload so the browser can detect "desktop just came back online while target=discord" and show a switch-back prompt. No new server-side notification channel needed.
- MVP scope: support `off` and `loop` loop-modes for Discord playback (continuous/next-in-library is a stretch goal, not required for v1).

## Steps

### Phase 1 — Backend: Discord bot manager & config (no dependency on other phases)
1. Add `discord.py` (or `py-cord`) and `PyNaCl` to `jingleserver/requirements.txt`. Note new Ubuntu apt dependency `libopus0` (discord.py voice needs libopus) in `jingleserver/README.md` dependency checklist.
2. Add config to `jingleserver/jsrv/config.py` (pattern matches existing env-var style, e.g. `AGENT_COMMAND_TIMEOUT_SECONDS`):
   - `DISCORD_BOT_TOKEN` (from `JINGLESERVER_DISCORD_BOT_TOKEN`, optional — feature disabled if unset)
   - `DISCORD_GUILD_ID` (optional int filter; if unset, bot uses the single guild it's a member of)
3. Create `jingleserver/jsrv/discord_bot.py` (new file) with a `DiscordBotManager` class, modeled after `jsrv/agent_manager.py`'s responsibilities but adapted for local (non-relay) control:
   - Wraps a `discord.Client` (or `discord.ext.commands.Bot` with no prefix commands needed) started via `asyncio.create_task(client.start(token))` during FastAPI startup (only if `DISCORD_BOT_TOKEN` configured).
   - `list_voice_channels() -> list[{id, name}]` — reads `guild.voice_channels` for the configured/only guild.
   - `async connect(channel_id) -> None` — `channel.connect()`, raises a manager-specific error (mirror `AgentNotConnected`/`AgentCommandTimeout` naming, e.g. `DiscordNotConnected`) on failure.
   - `async disconnect() -> None` — `voice_client.disconnect()`.
   - `async play(cache_path, loop_mode) -> status` — build `discord.FFmpegPCMAudio(str(cache_path))`, call `voice_client.play(source, after=...)`; track `state`, `current_name`, `current_path`, `loop_mode`, `start_ts` (via `time.monotonic()`) and pre-known `duration_seconds` (passed in from caller, sourced from the library item, not recomputed).
   - `pause()/resume()/stop()/set_loop_mode()` — mirror discord `VoiceClient.pause()/resume()/stop()`.
   - `status() -> dict` — same shape as `agent_manager`'s cached status dict (`state`, `current_name`, `current_path`, `position_seconds` computed from monotonic clock minus paused time, `duration_seconds`, `loop_mode`), plus `connected: bool`, `channel_name`.
   - On track-end callback (`after=`), if `loop_mode == "loop"` replay same file; otherwise mark `state="stopped"`.

### Phase 2 — Backend: control routing & API endpoints (*depends on Phase 1*)
4. In `jingleserver/jsrv/web.py`, instantiate `discord_bot_manager = DiscordBotManager(config.DISCORD_BOT_TOKEN, config.DISCORD_GUILD_ID)` next to the existing `agent_manager = AgentManager()` (~line 24), start it in the FastAPI startup event only if a token is configured.
5. Add a small global control-target state (in-memory, e.g. `_control_target: str = "desktop"` module var in web.py, values `"desktop"` | `"discord"`) — no DB persistence needed (resets to desktop on server restart, which is the safe default).
6. Extract the existing cache-ensure logic currently inline in the `/api/audio/{cache_id}` handler (~jsrv/web.py#L374-405) into a reusable helper, e.g. `async def _ensure_cached(cache_id_or_path) -> Path`, usable both by that endpoint and by the new Discord play path.
7. Add new endpoints (admin-only, following the existing 403 guest-restriction pattern used for `/api/playback/mode` and `/api/playback/output`):
   - `GET /api/discord/status` → `{available: bool, connected: bool, channel_name, channels: [...]}` (available = bot logged in to gateway; channels populated only when not connected).
   - `POST /api/discord/connect` `{channel_id}` → calls `discord_bot_manager.connect()`, sets `_control_target = "discord"`.
   - `POST /api/discord/disconnect` → calls `discord_bot_manager.disconnect()`, sets `_control_target = "desktop"`.
8. Modify the `/api/playback/{play,pause,stop,mode}` handlers (~jsrv/web.py#L489-L590) to branch on `_control_target`: route to `discord_bot_manager` (using `_ensure_cached()` for the file path) when target is `"discord"`, else keep existing `agent_manager` routing. Keep existing role-based restrictions (guest loop-mode/live-mode limits) applying uniformly to whichever target is active.
9. Extend `_public_status()` (~jsrv/web.py#L149-156) and the `/ws/status` push payload to include `control_target` alongside the existing `agent_connected` boolean, so the browser can independently track "is desktop physically online" vs "who is currently being controlled."

### Phase 3 — Frontend: webapp UI (*depends on Phase 2 API shape, can build UI skeleton in parallel*)
10. In `webclient/index.html` / `webclient/app.js`, add a "Discord Bot" control panel (admin-only, hidden for guest role like other host-only controls):
    - When disconnected: "Connect Discord Bot" button + channel `<select>` populated from `GET /api/discord/status`.
    - When connected: shows current channel name + "Disconnect" button.
11. Update `applyStatus()` (~webclient/app.js#L220-250) to read the new `control_target` field:
    - Now Playing bar reflects whichever target is active (server already routes commands to the right backend, so status polling naturally reflects it once agent_manager/discord_bot_manager both feed into the same public status shape — confirm `_public_status()` merges from the currently active target, not always desktop).
    - Track local previous `agent_connected` value; on transition `false → true` while `control_target === "discord"`, show a dismissible banner/modal: "Desktop jingle machine is back online — switch control back?" with a button that calls `POST /api/discord/disconnect`.
12. Minor styles in `webclient/styles.css` for the new panel/banner.

### Phase 4 — Docs & deployment
13. Update `jingleserver/README.md`: add `libopus0` to the apt checklist, document `JINGLESERVER_DISCORD_BOT_TOKEN` / `JINGLESERVER_DISCORD_GUILD_ID` under "Data locations" env-var section, and add a short "Discord bot setup" section (create bot in Discord Developer Portal, invite with `Connect`+`Speak` voice permissions, paste token into systemd unit's environment).
14. Update `jingleserver/deploy/jingleserver.service` to pass through the two new env vars (`Environment=` lines or an `EnvironmentFile=`).

## Relevant files
- `jingleserver/jsrv/discord_bot.py` — new `DiscordBotManager` (Phase 1)
- `jingleserver/jsrv/config.py` — new env vars (Phase 1)
- `jingleserver/requirements.txt` — add `discord.py`, `PyNaCl` (Phase 1)
- `jingleserver/jsrv/web.py` — new endpoints, control-target routing, extracted `_ensure_cached()`, extended status payload (Phase 2)
- `webclient/app.js`, `webclient/index.html`, `webclient/styles.css` — Discord panel UI, status handling, switch-back prompt (Phase 3)
- `jingleserver/README.md`, `jingleserver/deploy/jingleserver.service` — docs/deploy (Phase 4)

## Verification
1. Manual: start jingleserver with `JINGLESERVER_DISCORD_BOT_TOKEN` unset → confirm no behavior change (feature fully optional/no-op), existing desktop/browser flows untouched.
2. Manual: set token + invite bot to a test guild → `GET /api/discord/status` returns `available: true` and a channel list.
3. Manual scenario 1: desktop app connected and playing; admin connects bot to a voice channel via webapp; confirm desktop keeps playing unaffected; confirm playback commands from webapp now go to bot (verify in Discord voice channel); disconnect bot, confirm commands route back to desktop.
4. Manual scenario 2: desktop offline; connect bot, play a cached jingle in Discord; bring desktop app online; confirm banner appears in webapp prompting switch-back; click it and confirm bot disconnects and desktop regains control.
5. Confirm guest (non-admin) sessions cannot see/use connect/disconnect controls and get 403 if calling the API directly.
6. `python -m py_compile` / existing test run for `jingleserver/test_cache.py` after touching `jsrv/cache.py`-adjacent extraction, to ensure no regressions.

## Further Considerations
1. Loop mode `continuous` for Discord (auto-advance to next library item) is deferred — Phase 1 only implements `off`/`loop`. Recommend adding later by reusing the same "next item in current query" logic already in `webclient/app.js` (~L430) but server-side, once basic bot control is validated.
2. No DB persistence of "last used channel" or control-target across server restarts — always resets to `desktop` on restart, which is the safer default (avoids a stale bot silently controlling nothing after a crash). Confirm this default is acceptable.
