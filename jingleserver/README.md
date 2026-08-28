# jingleserver

Middleman relay + webapp host for JingleAllTheDay remote control. Runs on the
Ubuntu box behind Caddy (TLS) at `jingles.brianpclark.com`; the desktop app
dials **out** to this service, and browsers talk to this service too — the
desktop app is never contacted directly.

## Ubuntu 24.04.4 dependency checklist

- `python3` (3.12, ships with 24.04) and `python3-venv`, `python3-pip`
- Caddy (already installed/managed by you) for TLS + reverse proxy
- systemd (built in) for the `jingleserver.service` unit
- Python packages from `requirements.txt` (installed into a venv, not system-wide):
  `fastapi`, `uvicorn[standard]`, `websockets`, `python-multipart`

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
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

## Ongoing deploys (after webclient/jingleserver code changes)

Copy the updated `jingleserver/` and `webclient/` folders to
`/opt/jingleserver` on the server (preserving ownership), then:

```bash
# If copying from a Windows machine (e.g. via a Samba share), the `jingleserver`
# CLI script picks up CRLF line endings, which breaks its shebang line
# (`/usr/bin/env: 'python3\r': No such file or directory`). Strip them:
sudo sed -i 's/\r$//' /opt/jingleserver/jingleserver

sudo systemctl restart jingleserver   # or: ./jingleserver restart
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
