# jingleserver

Middleman relay + webapp host for JingleAllTheDay remote control. Runs on the
Ubuntu box behind Caddy (TLS) at `jingles.brianpclark.com`; the desktop app
dials **out** to this service, and browsers talk to this service too — the
desktop app is never contacted directly.

## Ubuntu 24.04.4 dependency checklist

- `python3` (3.12, ships with 24.04) and `python3-venv`, `python3-pip`
- `ffmpeg` for user-account lossless-preview conversion (WAV/FLAC/AIFF to AAC/M4A)
- Caddy (already installed/managed by you) for TLS + reverse proxy
- systemd (built in) for the `jingleserver.service` unit
- Python packages from `requirements.txt` (installed into a venv, not system-wide):
  `fastapi`, `uvicorn[standard]`, `websockets`, `python-multipart`

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg
```

## First-time server setup

```bash
sudo useradd --system --home /opt/jingleserver --shell /usr/sbin/nologin jingleserver
sudo mkdir -p /opt/jingleserver /var/lib/jingleserver
# copy this jingleserver/ folder (and webclient/) to /opt/jingleserver, e.g.:
#   scp -r jingleserver webclient youruser@host:/tmp/ && sudo mv /tmp/jingleserver/* /tmp/webclient /opt/jingleserver/
sudo chown -R jingleserver:jingleserver /opt/jingleserver /var/lib/jingleserver

# If copied from Windows (e.g. Samba), the `jingleserver` CLI script picks up
# CRLF line endings, which breaks its shebang line. Strip them:
sudo sed -i 's/\r$//' /opt/jingleserver/jingleserver
sudo chmod +x /opt/jingleserver/jingleserver

cd /opt/jingleserver
sudo -u jingleserver python3 -m venv .venv
sudo -u jingleserver .venv/bin/pip install -r requirements.txt

sudo cp deploy/jingleserver.service /etc/systemd/system/jingleserver.service
sudo systemctl daemon-reload
sudo systemctl enable --now jingleserver

# Add deploy/Caddyfile.snippet's block to your Caddyfile, then:
sudo systemctl reload caddy
```

## Ongoing Deploys From Windows via Samba

Copy the updated repository `jingleserver/` and `webclient/` folders from
Windows to a Samba share mounted on Ubuntu. Use a staging folder that is not
`/opt/jingleserver`; for example, `/srv/samba/jatd-stage/` containing:

```text
/srv/samba/jatd-stage/jingleserver/
/srv/samba/jatd-stage/webclient/
```

After the copy finishes, SSH to Ubuntu and set `STAGE` to that folder. These
commands preserve `/var/lib/jingleserver`, which holds the SQLite database and
offline audio cache.

```bash
STAGE=/srv/samba/jatd-stage

# Confirm the expected staged files are present before changing the live service.
find "$STAGE/jingleserver" -maxdepth 2 -type f | sort | head -50
find "$STAGE/webclient" -maxdepth 1 -type f | sort

sudo systemctl stop jingleserver

# Keep the previous code as a rollback copy. The virtual environment stays in
# the live directory and is deliberately excluded from source deployment.
sudo rm -rf /opt/jingleserver.previous
sudo mkdir -p /opt/jingleserver.previous
sudo rsync -a --exclude='.venv/' /opt/jingleserver/ /opt/jingleserver.previous/
sudo rsync -a --delete --exclude='.venv/' "$STAGE/jingleserver/" /opt/jingleserver/
sudo rsync -a --delete "$STAGE/webclient/" /opt/jingleserver/webclient/

# Windows line endings are harmless in Python source, but break executable
# shebang scripts on Ubuntu. Normalize the server CLI and retain its execute bit.
sudo sed -i 's/\r$//' /opt/jingleserver/jingleserver
sudo chmod 755 /opt/jingleserver/jingleserver
sudo chown -R jingleserver:jingleserver /opt/jingleserver

# Run this only when requirements.txt changed, or when deploying for the first time.
sudo -u jingleserver /opt/jingleserver/.venv/bin/pip install -r /opt/jingleserver/requirements.txt

sudo systemctl start jingleserver
sudo systemctl status jingleserver --no-pager
sudo journalctl -u jingleserver -n 100 --no-pager
curl --fail --silent --show-error --output /dev/null https://jingles.brianpclark.com/api/session
```

User accounts receive a cached 96 kbps AAC/M4A version of lossless cached
jingles for faster mobile preview. Admin accounts always receive the original
file. Confirm the server can create those previews after deployment:

```bash
ffmpeg -version
sudo journalctl -u jingleserver -n 100 --no-pager | grep -i 'compressed preview'
```

The final `curl` normally returns HTTP 200 with an unauthenticated JSON response;
it verifies the public TLS/proxy path without printing session data. `Caddy` does
not need a reload for application or webclient changes. Reload it only after
changing its site block, then validate before applying it:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

If the service fails after deployment, restore the previous code while keeping
the data directory and virtual environment intact:

```bash
sudo systemctl stop jingleserver
sudo rsync -a --delete --exclude='.venv/' /opt/jingleserver.previous/ /opt/jingleserver/
sudo chown -R jingleserver:jingleserver /opt/jingleserver
sudo systemctl start jingleserver
sudo journalctl -u jingleserver -n 100 --no-pager
```

## CLI reference

Run from `/opt/jingleserver`, prefixed with `sudo` (simplest option if your
account already has sudo — no polkit/sudoers rule needed):

```bash
sudo ./jingleserver start|stop|restart|status     # wraps systemctl
sudo ./jingleserver adduser <name> [--role admin|user]
sudo ./jingleserver setrole <name> <admin|user>
sudo ./jingleserver passwd <name>
sudo ./jingleserver deluser <name>
sudo ./jingleserver listusers
sudo ./jingleserver adddevice "<label>"           # prints a device token once — paste into
                                                   # the desktop app's Options > Device Token field
sudo ./jingleserver listdevices
sudo ./jingleserver deldevice "<label>"
```

`start`/`stop`/`restart`/`status` control a system-level systemd unit, which
always requires root — `sudo` satisfies that with no extra setup. The
account-management subcommands (`adduser`/`deluser`/`setrole`/`passwd`/
`adddevice`/`deldevice`) write directly to the SQLite DB; when run as root
via `sudo`, the CLI automatically hands ownership of the data directory back
to the unprivileged `jingleserver` service account afterward, so the running
systemd unit (which runs as that account, not root) never loses access to
its own database. `listusers`/`listdevices` are read-only and work as any
user with read access to the DB file.

## Data locations (override via environment variables, see `jsrv/config.py`)

- `JINGLESERVER_DATA_DIR` (default `/var/lib/jingleserver`) — SQLite DB lives here
- `JINGLESERVER_CACHE_DIR` (default `<data dir>/cache`) — offline jingle cache (audio files + `library_cache.json`)
- `JINGLESERVER_WEBCLIENT_DIR` (default `../webclient` next to this folder) — static webapp files
- `JINGLESERVER_PORT` (default `47030`) — bind port for Caddy to reverse-proxy to
- `JINGLESERVER_AGENT_AUDIO_TIMEOUT` (default `20.0`) — maximum seconds to wait for the desktop agent to begin or continue a live audio transfer
