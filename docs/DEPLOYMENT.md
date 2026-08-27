# Deployment

Running it by hand with `python main.py` is fine for an evening. This is how to
make it survive a reboot, and how to put it somewhere the household can reach.

Every path here is a placeholder. Replace `/opt/plexget`, `plexget`,
`com.example.plexget` and `your-host` with your own.

---

## Before you start

The app needs three things wherever it ends up:

- **A readable `config.yaml`** in the project directory, owned by whoever the
  service runs as.
- **A writable project directory**, or at least a writable `data/` and a
  writable path for the database. Both are created on first run: `data/` holds
  the encryption key and the push identity, and the SQLite file holds sessions
  and the wanted list.
- **A route to Plex** on whatever `plex.url` says.

Read [SECURITY.md](../SECURITY.md) first if this is going to be reachable from
outside the house. The `X-Forwarded-For` note in particular is the one that
catches people.

---

## Linux, with systemd

Put the project somewhere system-ish and give it a user of its own, so that a
mistake in this app is not a mistake in your home directory.

```bash
sudo useradd --system --home /opt/plexget --shell /usr/sbin/nologin plexget
sudo git clone <repo> /opt/plexget
sudo chown -R plexget:plexget /opt/plexget
sudo -u plexget python3 -m venv /opt/plexget/venv
sudo -u plexget /opt/plexget/venv/bin/pip install -r /opt/plexget/requirements.txt
sudo -u plexget cp /opt/plexget/config.example.yaml /opt/plexget/config.yaml
sudo -u plexget editor /opt/plexget/config.yaml
```

### The environment file

Keep the model key out of the unit. Unit files are world-readable; this is not:

```bash
sudo install -m 600 -o root -g root /dev/null /etc/plexget.env
sudo editor /etc/plexget.env
```

```ini
ANTHROPIC_API_KEY=sk-...
```

### The unit

`/etc/systemd/system/plexget.service`:

```ini
[Unit]
Description=PlexGet
Documentation=https://github.com/your/fork
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=plexget
Group=plexget
WorkingDirectory=/opt/plexget
EnvironmentFile=-/etc/plexget.env
ExecStart=/opt/plexget/venv/bin/python /opt/plexget/main.py
Restart=on-failure
RestartSec=5s

# The app writes only inside its own directory, so say so and let systemd
# enforce it. Drop these lines if you have moved the database elsewhere and
# forgotten to add it here - a read-only filesystem shows up as SQLite
# refusing to write, which is a confusing way to learn about it.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/plexget

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory` matters: `config.py` looks for `config.yaml` beside itself,
and the default database path is relative.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now plexget
systemctl status plexget
journalctl -u plexget -f
```

There is no log file to rotate — everything goes to the journal.

To pick up code changes:

```bash
sudo -u plexget git -C /opt/plexget pull
sudo -u plexget /opt/plexget/venv/bin/pip install -r /opt/plexget/requirements.txt
sudo systemctl restart plexget
```

---

## macOS, with launchd

A **LaunchAgent** runs when you log in and stops when you log out. A
**LaunchDaemon** runs at boot without anybody logged in. An agent is usually
what you want on a machine that is also somebody's desktop; a daemon is what
you want on a Mac mini in a cupboard that reboots on its own after a power cut.

`~/Library/LaunchAgents/com.example.plexget.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.plexget</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOURNAME/Apps/plexget/venv/bin/python</string>
    <string>/Users/YOURNAME/Apps/plexget/main.py</string>
  </array>

  <key>WorkingDirectory</key>
  <string>/Users/YOURNAME/Apps/plexget</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key>
    <string>sk-...</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/Users/YOURNAME/Apps/plexget/logs/plexget.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/YOURNAME/Apps/plexget/logs/plexget-error.log</string>
</dict>
</plist>
```

Create the log directory first — launchd will not, and a plist that cannot
write its log fails in a way that tells you nothing:

```bash
mkdir -p ~/Apps/plexget/logs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.plexget.plist
launchctl print gui/$(id -u)/com.example.plexget | head -20
```

Stopping, starting and restarting after a code change:

```bash
launchctl bootout gui/$(id -u)/com.example.plexget
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.plexget.plist

# or, without unloading it
launchctl kickstart -k gui/$(id -u)/com.example.plexget
```

`load` and `unload` still work and still appear in every tutorial, but they
report success on plists that never started. `bootstrap` and `bootout` tell you
what went wrong.

### The macOS permissions trap

This one costs everybody an afternoon. A process started by launchd does not
inherit the privacy permissions your terminal has been granted. If the project,
the database, or the media it scans lives under Desktop, Documents, Downloads,
an external volume or a network mount, the agent can be running perfectly
happily and still see nothing at all — no error, just empty results and files
that "do not exist".

Two ways out. Either keep everything somewhere unprotected, such as a folder in
your home directory that is not one of the special ones, or grant **Full Disk
Access** in System Settings → Privacy & Security to the binary in
`ProgramArguments` — the Python inside the venv, not Python.app and not
Terminal. Restart the agent afterwards.

The request page itself never touches media files, so if you are not running
the maintenance tooling this only affects where you put the project.

### Sleep

A Mac that goes to sleep is a server that stops answering. If the machine is
meant to be reachable, set it never to sleep, or `caffeinate` it.

---

## Docker

No `Dockerfile` ships with the project, because the interesting part of running
it is not the container. If you would rather have one, this is enough:

```dockerfile
FROM python:3.12-slim

# ffprobe is only needed by the quality audit. Drop this line if you are
# running the request page and nothing else - it is most of the image.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 5050
CMD ["python", "main.py"]
```

```yaml
services:
  plexget:
    build: .
    restart: unless-stopped
    ports:
      - "127.0.0.1:5050:5050"
    environment:
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - plexget-data:/app/data
      - plexget-db:/app/db-store

volumes:
  plexget-data:
  plexget-db:
```

Four things to get right, none of them obvious:

**Bind to `0.0.0.0` inside the container.** `flask.host: "127.0.0.1"` in
`config.yaml` means loopback *inside* the container, and nothing outside it can
connect. Publish the port to `127.0.0.1` on the host instead, as above, so the
app is still only reachable through whatever you put in front of it.

**The owner's loopback door does not work from a container.** Requests arrive
from the Docker bridge gateway rather than `127.0.0.1`, so the console is
reachable only by signing in with the owner's Plex account. That is arguably an
improvement. If you want the loopback behaviour back, run with
`network_mode: host` and no `ports:` block.

**Reaching Plex.** On Linux with the bridge network, `localhost` in `plex.url`
is the container, not the host — use the host's LAN address, or
`network_mode: host`. On Docker Desktop, `http://host.docker.internal:32400`
works.

**Persist `data/` and the database.** `data/` holds the key that decrypts
everybody's Plex tokens; lose it and everyone signs in again. Point
`database.path` at a directory you have mounted (`db-store/plexget.db` above,
or an absolute path), or those sessions go with the container.

---

## Behind a reverse proxy

Terminate TLS at the proxy, keep the app on loopback, and pass the client
address through. **The app treats a loopback request with no `X-Forwarded-For`
as the owner**, so a proxy that does not set that header hands the owner
console to the internet. This is the single most important line in this file.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name your-host;

    ssl_certificate     /etc/letsencrypt/live/your-host/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-host/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # A mood search waits on Plex and possibly on a model. The default 60
        # seconds is enough, but only just, and a truncated response reads as
        # the app being broken rather than slow.
        proxy_read_timeout 120s;

        # Posters come through the app, so give it room.
        client_max_body_size 8m;
    }
}
```

### Caddy

```
your-host {
    reverse_proxy 127.0.0.1:5050
}
```

Caddy sets `X-Forwarded-For` itself and gets a certificate on its own, which is
why it is two lines.

### Then check it

From a device outside the house, not from the machine:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://your-host/libraries
```

`403` is correct. `200` means the header is not arriving and the owner console
is public — fix that before going any further.

---

## Tunnels

If you would rather not open a port on your router at all:

- **Tailscale Funnel** publishes one port from a machine on your tailnet to a
  public HTTPS address, with a certificate handled for you.
- **A Cloudflare Tunnel** does the same through a domain you already have
  there.

Both terminate TLS and set `X-Forwarded-For` themselves, which makes them the
least error-prone option. Both mean your household page is on the public
internet under a guessable name, so the access tiers are doing real work — the
sign-in is the only thing between a stranger and the household surface.

---

## Wiring Plex's webhook

The wanted list settles itself when Plex says it has added something. In Plex's
settings, under **Webhooks**, add:

```
http://127.0.0.1:5050/plex/webhook
```

Plex Media Server posts it from the same machine, which is why the loopback
rule lets it through without a session. Only `library.new` is acted on; the
rest is noise.

Webhooks are a Plex Pass feature. Without one, ask the app to check for itself
on a timer — say every couple of hours:

```
0 */2 * * * curl -fsS -X POST http://127.0.0.1:5050/wanted/recheck >/dev/null
```

It only reports; marking something acquired stays a deliberate step.

---

## Backups

Three things, and they are all small:

| | |
|---|---|
| `config.yaml` | Your tokens and keys |
| `data/` | The encryption key and the push identity |
| The SQLite file | Sessions, the wanted list, announcements, usage counters |

Lose `data/secret.key` and every stored Plex token becomes unreadable —
recoverable, in that everybody simply signs in again, but they will all have to
do it at once and unexpectedly. Lose the database and you lose the wanted list,
which is the only thing here that is not reconstructible from Plex.

Everything the maintenance tooling produces — the scan, the quality scores, the
gap analysis — is rebuilt by running it again, so it is not worth backing up.

---

## When it will not start

**`FileNotFoundError` on `config.yaml`** — the service is running from the
wrong directory. `WorkingDirectory` in the unit, or the plist, has to be the
project root.

**Port already in use** — something else has it. `ss -ltnp | grep 5050` on
Linux, `lsof -ti :5050` on macOS. Change `flask.port` or move the other thing.

**Plex unreachable, from a service that worked by hand** — a hostname that
resolves for you may not resolve for a system user with no environment. Try the
IP address.

**SQLite says the database is read-only** — the service user cannot write to
the project directory, or `ProtectSystem=strict` is on without a matching
`ReadWritePaths`.

**Notifications never arrive** — they need HTTPS and an installed home-screen
app, not merely an open page. Check `/push/key` returns something, and that the
person actually pressed the bell.

**The page has not changed after a deploy** — the service worker caches the
shell. A hard reload, or removing the home-screen app and adding it again, gets
a fresh copy.
