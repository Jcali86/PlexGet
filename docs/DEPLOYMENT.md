# Deployment

Running it by hand with `python main.py` is fine for an evening. This is how to
make it survive a reboot, and how to put it somewhere the household can reach.

Every path here is a placeholder. Replace `/opt/plexget`, `plexget`,
`com.example.plexget` and `your-host` with your own.

These examples operate the current single-process Flask development server.
A service manager, container or HTTPS proxy does not make it a hardened public
service. Keep this release on a private network for trusted household members;
read [Security](../SECURITY.md) before sharing any access. Publishing the source
repository does not publish or secure an installation.

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

Configuration is read beside `config.py` by default. To use another file,
set `PLEXGET_CONFIG` to its absolute path in the service environment. Keep the
working directory at the project root so relative database and index paths
remain predictable. Configuration and environment changes require a restart.

Read [SECURITY.md](../SECURITY.md) first. The `X-Forwarded-For` check below is
necessary for a proxy deployment, but passing it does not resolve all current
security limitations.

---

## Updating

Use the latest `main`; there are no maintained release branches. Before an
update, stop the app using its service manager (or stop the foreground process)
and make a private backup of `config.yaml`, `data/`, and the database named by
`database.path`. Keep the database's `-wal` and `-shm` files with it if present.
Store environment keys securely too. A media rescan cannot restore sessions or
the wanted list.

In your existing `main` checkout:

```bash
git status --short
# If source changes are listed, preserve and resolve them before continuing.
git rev-parse HEAD   # record this commit with your backup
git pull --ff-only origin main
venv/bin/python -m pip install -r requirements.txt
```

Do not overwrite an existing `config.yaml` with the example. Compare new example
options with your private configuration, then restart using the service manager
below, or run `venv/bin/python main.py` for a foreground installation. Check
`/health` for the running build and try a title search. `/health` alone does not
prove Plex, the model or push delivery works.

To roll back code, stop the app, check out your recorded previous commit and
install that commit's requirements before restarting. Keep the backup: an older
version may not understand newer stored state. Avoid running two copies against
the same database while upgrading or restoring.

---

## Linux, with systemd

Put the project somewhere system-ish and give it a user of its own, so that a
mistake in this app is not a mistake in your home directory.

```bash
sudo useradd --system --home /opt/plexget --shell /usr/sbin/nologin plexget
sudo git clone https://github.com/Jcali86/PlexGet.git /opt/plexget
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
Documentation=https://github.com/Jcali86/PlexGet
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

For updates, follow [Updating](#updating), running Git and pip as the `plexget`
service user. Use `sudo systemctl stop plexget` before the backup and
`sudo systemctl start plexget` afterwards.

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

The example plist contains a model key. Restrict the saved file to your account
with `chmod 600 ~/Library/LaunchAgents/com.example.plexget.plist`, and never
commit or share it. Omit the environment block if you are not enabling AI.

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

First create a `.dockerignore` beside it to keep the standard local credentials
and state out of `COPY . .`. Add any other private paths you use before building:

```text
.git
config.yaml
.env
.env.*
data
*.db
*.db-wal
*.db-shm
venv
.venv
logs
posteria
__pycache__
```

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

**Owner access differs in a container.** Requests commonly arrive
from the Docker bridge gateway rather than `127.0.0.1`, so the console is
reached by signing in with the owner's Plex account. Check the actual network
path; a local proxy in the container changes it again. Do not enable host
networking just to regain unauthenticated local owner access.

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

Tunnels are transport choices, not additional application hardening:

- **Tailscale Funnel** publishes one port from a machine on your tailnet to a
  public HTTPS address, with a certificate handled for you.
- **A Cloudflare Tunnel** does the same through a domain you already have
  there.

Both can place the app on the public internet. They are not recommended as a
way to publish this release without further hardening and deployment review.
Prefer private network reachability. If evaluating a proxy or tunnel, verify
the actual forwarding headers and access boundary; do not assume its defaults
make the application safe.

---

## Optional Posteria companion

The repository's `docker-compose.yml` starts **Posteria only**, not PlexGet.
Skip it entirely for the household request page. Poster operations change Plex
artwork and should be reviewed by the owner.

```bash
cp .env.example .env
chmod 600 .env
```

Set your own `PLEX_TOKEN`, `PLEX_SERVER_URL`, `POSTERIA_USER` and a strong
`POSTERIA_PASSWORD`; set `PUID` and `PGID` for the account that should own the
poster files. Before starting it, restrict the Compose port mapping to
`"127.0.0.1:${POSTERIA_PORT:-1818}:80"` if only this host needs access. The
shipped mapping publishes on all host interfaces; Posteria needs its own access
review, separate from PlexGet's proxy.

Then run `docker compose up -d posteria` and check
`docker compose logs --tail=50 posteria` locally. Set the matching URL, username
and password in your private `config.yaml` under `posteria`, because PlexGet
does not read Posteria's `.env`. Keep the local `posteria/` storage private.
This Compose file does not pass `ANTHROPIC_API_KEY` to PlexGet; export that key
in the app's own process environment if using AI.

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

Webhooks are a Plex Pass feature. Without one, nothing extra is needed: the
library index (next section) looks at what each library added every few
minutes, and when it finds something new it checks the wanted list and tells
whoever asked. That covers a new film, a new season of a series already here,
a file put back, and a Fix Match that turns an item into the film somebody
asked for. The webhook only makes it immediate.

What arrived is noted in the index file until the wanted list has been checked
against it all the way through. If Plex drops out part way, nothing is lost:
the check is made again a minute later, then at longer gaps up to half an hour,
and the console's index panel says so in the meantime. Each entry is marked
arrived before anybody is told, so the webhook and the index noticing the same
film at once still send one "it's here". A film on a drive that has dropped
out is not "here" until the drive is back; if this machine has never been able
to read a drive Plex uses, it takes Plex's word for what is on it.

If the index is switched off, settle the list on a timer instead - say every
couple of hours:

```
0 */2 * * * curl -fsS -X POST http://127.0.0.1:5050/wanted/settle >/dev/null
```

`/wanted/settle` marks what has arrived and sends the notifications.
`/wanted/recheck` is only the report behind it: it lists what looks arrived
and changes nothing.

---

## The library index

A catalogue of every film and series on the server, kept in a SQLite file of
its own and refreshed by the app in the background. Searches ask it first: a
title it does not hold is answered without a single request to Plex, and one it
does hold is looked up only in the library that holds it. The files behind each
film are checked in the background too, so no search ever waits on a disk.

Nothing needs setting up; it builds itself the first time the app starts, and
the console's overview shows what it holds. To watch the first build, or to
check on it from a terminal:

```bash
venv/bin/python scripts/sync_library.py            # read every library now
venv/bin/python scripts/sync_library.py --status   # what it holds, and where
```

**Putting it on another drive.** The file is small - a few megabytes for a
large library - and any always-connected local drive is a good home for it. On
a Mac, find the name macOS gave the drive, make a folder on it, and point
`index.path` at a file in that folder:

```bash
ls /Volumes
mkdir "/Volumes/<drive name>/PlexGet"
```

```yaml
index:
  path: "/Volumes/<drive name>/PlexGet/library_index.db"
```

Restart the app. The folder has to exist already: the app never creates it,
because a drive that is not mounted leaves nothing at `/Volumes/<drive name>`,
and creating the folder there would quietly put the index on the internal disk
under the drive's name. If the drive is missing, searches carry on asking Plex
directly, the console says why, and the index comes back when the drive does.

The drive's speed matters much less than it sounds. The gain comes from not
asking Plex, library by library, whether it has something; a lookup in the
index takes a millisecond or two on any SSD, internal or external.

**If the app's own worker is switched off** (`index.background: false`), run
the script on a schedule instead. Each run also checks the wanted list against
anything that has arrived and sends the notifications, which is the worker's
other job:

```
*/5 * * * * cd /path/to/plexget && venv/bin/python scripts/sync_library.py --recent >/dev/null
0 */6 * * * cd /path/to/plexget && venv/bin/python scripts/sync_library.py >/dev/null
```

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
The same goes for the library index: delete it and it is read again from Plex.

---

## When it will not start

**Missing `config.yaml`** — copy the example beside `config.py` and fill in
`plex.url` and `plex.token`, or check the service's `PLEXGET_CONFIG` path. Keep
`WorkingDirectory` at the project root for relative state paths.

**Port already in use** — something else has it. `ss -ltnp | grep 5050` on
Linux, `lsof -ti :5050` on macOS. Change `flask.port` or move the other thing.

**Plex unreachable, from a service that worked by hand** — a hostname that
resolves for you may not resolve for a system user with no environment. Try the
IP address.

**SQLite says the database is read-only** — the service user cannot write to
the project directory, or `ProtectSystem=strict` is on without a matching
`ReadWritePaths`.

**Notifications never arrive** — use HTTPS, open the installed Home Screen app
on iOS/iPadOS, and subscribe with the bell on each device. Other platforms need
a supported browser and permission. While signed in, use **Send a test**.
`/push/key` returning a key checks only local key generation, not delivery.

**A shared account cannot sign in** — confirm Plex shares this specific server
with that account and that Plex is reachable from the app. Test outside the
owner's local browser, where local owner access can mask the household flow.

**Moods use keywords instead of AI** — check the provider key is exported in
the service's environment, the configured model is available to that account,
and the app was restarted. `.env` is not loaded by the Python app. A title
search working does not establish provider access.

**The page has not changed after a deploy** — the service worker caches the
shell. A hard reload, or removing the home-screen app and adding it again, gets
a fresh copy.
