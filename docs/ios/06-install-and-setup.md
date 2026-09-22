# 06. Install and setup

Purpose. This document is the owner's installation of the PlexGet server as it will be after Phase 8, written for two readers at once: the builder, who writes the `Dockerfile`, `docker-compose.yml`, `install.sh`, the GHCR workflow, the setup blueprint `api/setup.py` with `dashboard/setup.html`, and the rewritten `docs/DEPLOYMENT.md`; and the owner, who runs one command, opens one page and follows it. It fixes the image name, what is inside it, the file contents, the volume layout, the exact commands per host type, what setup mode is and what each of the setup page's five steps writes to `config.yaml`, how the server is reached from outside with Tailscale Funnel first, why the loopback door ships off, how the webhook key and the settle scheduler make "it arrived" true in Docker, how upgrades, backups and the venv migration work, and what stays hard. Endpoint, table, config-key and phase names are the ADR's verbatim (`10-decision-record.md`). Shapes the ADR does not define are marked `GAP:` and are the builder's to add in Phase 8 with `tests/test_setup.py`.

Read with: `02-architecture.md` (the setup blueprint's place among the components, §2.9 for the scheduler), `03-server-api.md` (§3.1 `GET /api/v1/server` and its `setup` flag; §5 hands the setup routes' shapes to this file), `05-push-relay.md` (`GET /join`, the invite the setup page shows as a QR), `07-build-phases.md` (Phase 2 builds the switches, Phase 8 builds everything here), `09-security.md` (the loopback door and the exposed-port threat), `10-decision-record.md` (§2 "Install artefacts", §4 "Server install", "Remote reach", "Bind address", §5 items 7-9, 19, 24, §6 gates 2 and 3).

Note: design-A §2.4 and §7 name the compose file `compose.yaml`, rename the Posteria file to `compose.posteria.yml` and run the Linux container with `--network host`. The ADR (§2 "Install artefacts") keeps the name `docker-compose.yml`, keeps Posteria in it under `profiles: [posters]`, publishes `5050:5050` and adds `extra_hosts: host.docker.internal:host-gateway`. The ADR is followed; host networking is an option in §4.3, not the default.

## 1. The published image

| Item | Value |
|---|---|
| Name | `ghcr.io/<org>/plexget` (`<org>` is the owner's GitHub account or organisation, unresolved until the owner confirms the repository row in ADR §4; `00-README.md` carries it as an open placeholder) |
| Tags | `:<git tag>` (e.g. `:v1.0.0`) and `:latest`, pushed by `.github/workflows/image.yml` on every tag matching `v*`; pull requests build without pushing, which is the `docker build` gate of ADR §7 Phase 8. The workflow declares `permissions: { contents: read, packages: write }` at job level (without `packages: write` the default `GITHUB_TOKEN` cannot push to GHCR and the tag push fails with `403`; unverified from memory of the default token permissions, as prompts/phase-8.md step 5 says) and logs in with `docker/login-action` using `${{ github.actor }}` and `${{ secrets.GITHUB_TOKEN }}`; the image name is lower-cased (`ghcr.io/${{ github.repository_owner }}/plexget` through `tr A-Z a-z`), because GHCR refuses upper-case names |
| Architectures | `linux/amd64` and `linux/arm64` in one manifest list (Intel and AMD PCs, Apple-silicon Docker Desktop, Raspberry Pi 4 and 5, arm64 NAS units) |
| Base | `python:3.12-slim` |
| Process | `python main.py`, which serves through `waitress` (one process, eight threads) whenever `flask.debug` is false (ADR §2). Werkzeug's `app.run()` at `main.py:10-14` is only used with `flask.debug: true`, which the image never sets |
| User | `plexget`, uid 1000, gid 1000; nothing runs as root after the build stage |
| Volume | `/data`, holding `config.yaml`, the SQLite file and everything the code writes under `data/` today |
| Switches baked in | `PLEXGET_CONFIG=/data/config.yaml`, `PLEXGET_DATA=/data`, `PLEXGET_LOOPBACK_OWNER=0` (the door off, §7), `PLEXGET_SETTLE_MINUTES=120` (the scheduler on, §8) |
| Not inside | `ffmpeg`/`ffprobe`. The quality audit that needs it (`test_connections.py:213`) is a venv feature the app never calls; leaving it out keeps the image small. The health panel reports it as "not installed" rather than failing |
| Health | `HEALTHCHECK` fetching `http://127.0.0.1:5050/health`; any `200`, including `{"status": "setup"}`, is healthy |

The image contains no secret and no `config.yaml`. A container started from it with an empty volume is in setup mode (§5) until the owner completes the setup page.

## 2. `Dockerfile`

To be written at the repository root exactly as follows. `waitress` is added to `requirements.txt` in Phase 2 (ADR §2), pinned like every other line there.

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLEXGET_CONFIG=/data/config.yaml \
    PLEXGET_DATA=/data \
    PLEXGET_LOOPBACK_OWNER=0 \
    PLEXGET_SETTLE_MINUTES=120

RUN groupadd --gid 1000 plexget \
    && useradd --uid 1000 --gid 1000 --home /app --shell /usr/sbin/nologin plexget \
    && mkdir -p /data && chown plexget:plexget /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=plexget:plexget . .

USER plexget
VOLUME /data
EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5050/health', timeout=4).status == 200 else 1)"

CMD ["python", "main.py"]
```

Rules for the builder: `.dockerignore` excludes `venv/`, `.git/`, `data/`, `config.yaml`, `.env*`, `*.db*`, `logs/`, `posteria/`, `docs/screenshots/`, `scratchpad/`; the `flask.host` inside the image is decided by the setup page (§5.3), which writes `0.0.0.0`, and `main.py` additionally treats `PLEXGET_CONFIG` being set as a signal to bind `0.0.0.0` while in setup mode, because a container bound to loopback cannot show its own setup page (docs/DEPLOYMENT.md:251-254). No secret, key or token is ever baked in (design-A §10 Phase 8 "Forbidden").

## 3. `docker-compose.yml`

Replaces the current Posteria-only file (`docker-compose.yml:1-85`; deployment-and-security.md §2.4) in place. The `plexget` service is the app; Posteria stays, unchanged in substance, behind a profile so `docker compose up -d` never starts it unless asked.

```yaml
# The app, and the optional Posteria poster manager behind a profile.
#   docker compose up -d                      # the app only
#   docker compose --profile posters up -d    # the app and Posteria
# .env is read by compose only; the app reads nothing from it.

services:
  plexget:
    image: ghcr.io/<org>/plexget:latest
    container_name: plexget
    restart: unless-stopped
    ports:
      - "5050:5050"
    volumes:
      - plexget-data:/data
    extra_hosts:
      # Lets the container reach a Plex server on the same machine as
      # http://host.docker.internal:32400. Docker Desktop provides the name
      # already; on Linux this line is what creates it.
      - "host.docker.internal:host-gateway"
    # To run on the host's network instead (Linux only), delete the ports:
    # block and add:  network_mode: host

  posteria:
    profiles: ["posters"]
    image: bozodev/posteria:latest
    container_name: posteria
    restart: unless-stopped
    environment:
      SITE_TITLE: "Posteria"
      AUTH_USERNAME: "${POSTERIA_USER:-admin}"
      AUTH_PASSWORD: "${POSTERIA_PASSWORD:?set POSTERIA_PASSWORD in .env}"
      AUTH_BYPASS: "false"
      SESSION_DURATION: "3600"
      PLEX_SERVER_URL: "${PLEX_SERVER_URL:-http://host.docker.internal:32400}"
      PLEX_TOKEN: "${PLEX_TOKEN:?set PLEX_TOKEN in .env}"
      PLEX_REMOVE_OVERLAY_LABEL: "false"
      PUID: "${PUID:-1000}"
      PGID: "${PGID:-1000}"
      TZ: "${TZ:-Etc/UTC}"
      IMAGES_PER_PAGE: "24"
      MAX_FILE_SIZE: "5242880"
      IGNORE_ARTICLES_IN_SORT: "true"
      SORT_BY_DATE_ADDED: "false"
      AUTO_IMPORT_ENABLED: "false"
      AUTO_IMPORT_SCHEDULE: "1h"
      AUTO_IMPORT_MOVIES: "false"
      AUTO_IMPORT_SHOWS: "false"
      AUTO_IMPORT_COLLECTIONS: "false"
    ports:
      - "${POSTERIA_PORT:-1818}:80"
    volumes:
      - ./posteria/posters:/app/posters
      - ./posteria/config:/app/config
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:80"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
    extra_hosts:
      - "host.docker.internal:host-gateway"

volumes:
  plexget-data:
```

The `plexget` service reads no `.env` value: the model key and every other secret go through the setup page into `config.yaml` (§5), so the `.env.example` note that "the app does not read this file" (`.env.example:3-8`) stays true. `.env.example` keeps its Posteria keys and drops nothing.

## 4. Volumes, ports, environment and the one-line commands

### 4.1 What lives in `/data`

| Path | Written by | Loss means |
|---|---|---|
| `/data/config.yaml` | the setup page (§5); editable by hand afterwards | setup mode again on next start; nothing else lost |
| `/data/plexget.db` (+ `-wal`, `-shm`) | `db/database.py`; the setup page writes `database.path: "/data/plexget.db"` | the wanted list, messages, sessions, `events`, `native_devices`, announcements, usage counters (docs/DEPLOYMENT.md:373-389) |
| `/data/secret.key` | `api/secrets_store.py:18-32`, 0600 | every stored Plex token unreadable; everybody signs in again (SECURITY.md:106-115) |
| `/data/vapid.json` | `api/push.py:26-48`, 0600 | every web-push (browser) subscription orphaned; the app's relay push is unaffected |
| `/data/client_id` | Phase 2 migration (ADR §2; §11 here) | the server appears to plex.tv as a new device; gate 3 |
| `/data/relay.json` | `POST /api/v1/owner/relay`, 0600 | push drops to `polling` until the owner's app re-posts its server key on next launch |
| `/data/themes.json`, `/data/themes/*.png` | `api/themes.py:19-21` | owner-made looks |

Phase 2 makes every `Path(__file__).resolve().parent.parent / "data"` in `api/secrets_store.py:15`, `api/push.py:21` and `api/themes.py:19` resolve through `PLEXGET_DATA` when it is set, and `config.py` reads `PLEXGET_CONFIG` before its `ROOT / "config.yaml"` default (`config.py:20-22`). Unset, both keep today's paths, so venv installs notice nothing.

### 4.2 Ports and environment

One port, `5050/tcp`, published on every interface of the host. Nothing else listens. The environment variables the image honours:

| Variable | Image value | Meaning |
|---|---|---|
| `PLEXGET_CONFIG` | `/data/config.yaml` | where `config.py` looks for the file; absent file means setup mode (§5) |
| `PLEXGET_DATA` | `/data` | the `data/` directory |
| `PLEXGET_LOOPBACK_OWNER` | `0` | overrides `app.loopback_owner` when set; `0`, `false`, `no` mean off |
| `PLEXGET_SETTLE_MINUTES` | `120` | overrides `app.settle_every_minutes` when set; `0` means off |
| `ANTHROPIC_API_KEY` | unset | still honoured (`api/ai/__init__.py:101-112`) for owners who prefer `-e` over the setup page; the environment wins over `ai.api_key` |

### 4.3 The commands

Linux (Docker Engine; install Docker first with `curl -fsSL https://get.docker.com | sh`):

```bash
docker run -d --name plexget --restart unless-stopped -p 5050:5050 --add-host host.docker.internal:host-gateway -v plexget-data:/data ghcr.io/<org>/plexget:latest
```

macOS with Docker Desktop (also Windows with Docker Desktop, untested as README.md:134-135 says of Windows generally):

```bash
docker run -d --name plexget --restart unless-stopped -p 5050:5050 -v plexget-data:/data ghcr.io/<org>/plexget:latest
```

Docker Desktop resolves `host.docker.internal` by itself (docs/DEPLOYMENT.md:262-264), so `--add-host` is not needed; passing it does no harm, and `install.sh` passes it everywhere for one code path.

Synology Container Manager, Unraid Community Applications, Portainer and similar forms: image `ghcr.io/<org>/plexget:latest`; port `5050` (host) to `5050` (container), TCP; a volume mapping a host folder such as `/volume1/docker/plexget` (Synology) or `/mnt/user/appdata/plexget` (Unraid) to `/data`; restart policy "always" or "unless stopped"; no environment variables required. Because the container runs as uid 1000, a bind-mounted folder must be writable by that uid: on Synology run `sudo chown -R 1000:1000 /volume1/docker/plexget` once over SSH, or pick a folder the "docker" user owns; on Unraid the default appdata ownership (`nobody:users`, 99:100) is not writable by uid 1000, so `chown -R 1000:1000` the folder or add `--user 99:100` to the extra parameters. Named volumes need nothing. On a NAS Plex usually runs on the same host, so `plex.url` becomes the NAS's LAN address, which the setup page offers (§5.3).

With the compose file: `docker compose up -d` in the folder holding it, after replacing `<org>`.

Linux host networking (optional, `--network host` in place of `-p … --add-host …`): makes `http://localhost:32400` valid for Plex and removes one bridge hop. It also makes traffic from a Funnel or proxy on the same machine arrive from `127.0.0.1` with no `X-Forwarded-For`, which is exactly why the door is off (§7). Not offered on Docker Desktop, where host networking does not reach the Mac's interfaces.

### 4.4 `install.sh`

A POSIX `sh` script at the repository root, fetched with `curl -fsSL https://raw.githubusercontent.com/<org>/PlexGet/main/install.sh | sh` and also runnable from a clone. Behaviour, in order:

1. Refuses to run as a non-root user without Docker access (tests `docker info`), and says to install Docker (`https://get.docker.com` on Linux, Docker Desktop elsewhere) when the `docker` command is missing. It never installs Docker itself.
2. Reads flags: `--port <n>` (default `5050`, changes only the host side of the mapping and prints a reminder that the app's LAN probe expects 5050), `--tag <tag>` (default `latest`), `--host-network` (Linux only), `--name <container>` (default `plexget`).
3. `docker pull ghcr.io/<org>/plexget:<tag>`.
4. If a container named `plexget` exists, stops and removes it (the volume is untouched); this is how an upgrade is one re-run (§9).
5. `docker volume create plexget-data` (idempotent) and the `docker run` line from §4.3 with the flags applied.
6. Polls `http://127.0.0.1:<port>/health` every two seconds for up to sixty seconds until it returns `200`.
7. Prints the setup URL using the first non-loopback IPv4 address (`hostname -I | awk '{print $1}'` on Linux, `ipconfig getifaddr en0` on macOS, falling back to `localhost`): `Open http://<address>:5050/setup?code=<code> on any computer or phone in the house to finish.`, where `<code>` is read from the container's stdout (`docker logs plexget`, the `Setup code: <code>` line of §5.2). When `/health` returned `{"status": "ok"}` instead (an upgrade of a configured install) it prints `Upgraded; http://<address>:5050 is back.`
8. Exits non-zero with the last `docker` error on any failure. It writes no file outside Docker, and the README shows the plain `docker run` line beside it for owners who will not pipe a script into a shell.

## 5. Setup mode and the setup page

### 5.1 What `config.py` does when `config.yaml` is absent

Today a missing file is `_stop()` and `SystemExit(1)` (`config.py:132-133`; deployment-and-security.md §1; `_check_required()` at `:116-126` still stops on a file that exists but is wrong). After Phase 2, when the file at `PLEXGET_CONFIG` (or `ROOT / "config.yaml"` when unset) does not exist, `load_config()` returns the merged `DEFAULTS` (`config.py:42-71`) with `plex.url` and `plex.token` empty and a marker `config["_setup"] = True`; `_check_required()` is skipped. Every other failure (invalid YAML, empty file, placeholders, missing `plex.url`) still stops the process as today, because a file that exists but is wrong is the owner's to fix, and silently entering setup over it would let the setup page overwrite their edits.

In setup mode `create_app()` registers only the setup blueprint plus `/health` and `GET /api/v1/server`; the guard (`api/routes.py:1235-1273`) answers every other path with `503 {"error": "this server is being set up", "setup": true}`. `GET /health` returns `{"status": "setup"}` (ADR §7 Phase 8 gate); `GET /api/v1/server` returns `"setup": true` with `server_name` and `plex_machine_id` `null` (03-server-api.md §3.1), which is what makes the app say "this server has not been set up yet; open http://<host>:5050/setup on a computer" and stop (02-architecture.md §6.3). The Phase 2 scheduler thread does not start in setup mode.

### 5.2 Who may use the page

In setup mode the process generates `data/setup_code` (`secrets.token_urlsafe(12)`) on first start and prints `Setup code: <code>` to stdout (`docker logs plexget`; the NAS log viewer); `install.sh` prints the setup URL as `http://<address>:5050/setup?code=<code>` (§4.4 step 7). Every `POST /setup/*` route in setup mode requires `code` (query or JSON field) equal to it under `hmac.compare_digest`, else `403 {"error": "setup code missing or wrong"}`; `GET /setup` without a valid `code` shows only a field to type it. The file is deleted at Save. (design-A §8 left the page open to the LAN until Save; the code is the tightening, because a first-come rule is not a control: whoever completed step 1 first, from the LAN or from the internet on a forwarded port, would own the install and write their own keys.) The page still says, above step 1, "Finish this now: anyone on your network with the code can open this page." A draft (§5.4) is bound to the first Plex account that completes step 1 and lives thirty minutes from its last change; a second browser starting step 1 while a draft exists receives `409 {"error": "setup is already in progress on another device"}`. After the save, `/setup` and every `/setup/*` route except `GET /setup/ping` (which answers only to a fresh nonce, §5.4, §6.2) require an owner session (`current_user()` with `role == "owner"`, via bearer or cookie) and the page opens on the health panel (§5.5) with the five steps below it for re-running one, such as pasting a new remote address. The setup routes are never called by the app (03-server-api.md §5).

### 5.3 The five steps, in order, and what each writes

One HTML file, `dashboard/setup.html`, in the style of `request.html`, five numbered steps, one visible at a time, each with a "Continue" that posts to the route named. Nothing is written to disk until step 5's Save.

| Step | What the owner does | Route(s) | Written to `config.yaml` at Save |
|---|---|---|---|
| 1 Sign in with Plex | Taps "Sign in with Plex"; the page runs the existing server-side pin flow (`create_pin`/`check_pin`, `api/plex_auth.py:42-145`) with `origin` set so `forwardUrl` returns to `/setup`, then polls | `POST /setup/plex/start`, `GET /setup/plex/poll/<pin_id>?also=<link_id>` | nothing yet; the account token is held in the server's memory draft only and is discarded at Save. The page refuses to continue with an account that owns no Plex server ("This Plex account owns no server; sign in as the account that runs Plex") |
| 2 Choose the server | Sees the owned servers from `GET https://plex.tv/api/v2/resources?includeHttps=1&includeRelay=1` (the same headers as `api/plex_auth.py:203-234`; that call sends `includeHttps=1` only, and `includeRelay=1` is what makes relay connections appear, as plexapi's `MyPlexResource.key` does) with each server's `connections[]`; picks one. The server process, not the browser, tests each candidate address in order (local `connections` first, then `http://host.docker.internal:32400`, then relay addresses) with `PlexServer(url, token)` and shows the first that answers, with a field to type another | `POST /setup/plex/server` | `plex.url` (the working address), `plex.token` (the per-server access token from `server_access_token()`, `api/plex_auth.py:203-234`, not the account token; before Save the page confirms `plex_identity(<token to store>)` (`api/plex_auth.py:148-159`) returns the signed-in account's `id`, and if it does not, the account token is written instead, because `allowed_users()` (`:174`) builds `MyPlexAccount(token=config["plex"]["token"])` and needs a token plex.tv's `/api/v2/user` accepts), `app.name` (defaults to the Plex server's friendly name, editable on this step) |
| 3 TMDb key | Pastes a TMDb v3 API key, or skips. The server tests it with one `GET /configuration` call as `test_connections.py:164-186` does. Copy: "Without it there are no posters and no 'not in Plex' answers" | `POST /setup/keys` | `tmdb.api_key` |
| 4 Model key | Pastes an Anthropic API key, or skips. The server tests it with one small call through the existing provider (`api/ai/__init__.py`). Copy: "Without it, requests are matched by keyword only; the app says so" (ADR §4 "Hosted smart search": owners paste their own key) | `POST /setup/keys` | `ai.api_key` (left empty when skipped; `ai.api_key_env` keeps its default so `ANTHROPIC_API_KEY` in the environment still wins) |
| 5 Reach it from outside | Picks Funnel, proxy or tunnel (§6), follows the shown commands, pastes the resulting `https://` address, presses "Check", then "Save" | `POST /setup/remote/check`, `POST /setup/save` | `app.urls.remote: [<pasted address>]` (may be empty if the owner chose "home only for now"), `app.urls.local: ["http://<host LAN IPv4>:5050"]` (taken from the `Host` header of the owner's own request to the setup page, i.e. the address the owner typed to reach `/setup`, normalised to `http://<host>:<port>`; inside a bridge-networked container the server's own interface is the container's `172.17.x.x` address, unreachable from phones and from Plex, and is never used; the page lets the owner edit the value before Save), `flask.host: "0.0.0.0"`, `flask.port: 5050`, `app.loopback_owner: false`, `app.settle_every_minutes: 120`, `plex.webhook_key: <secrets.token_urlsafe(24)>`, `database.path: "/data/plexget.db"` (only when `PLEXGET_DATA` is set) |

The written `config.yaml` is the `DEFAULTS` blocks plus the keys above, with a header comment naming the date and "written by the setup page; edit by hand if you like, and see config.example.yaml for every optional block". `persona` is not written; the persona module's behaviour for an absent key is unchanged. The page explains the two bind-related keys with one sentence: "The server now listens on your whole home network, so the phone app can find it; because of that, the browser-on-this-machine shortcut to the owner console is turned off and the console needs your Plex sign-in" (ADR §4 "Bind address"; §5 item 19's list of what LAN publication exposes is repeated in the README, not on the page).

Save also generates `data/secret.key` (`api/secrets_store.py:18-32`) and `data/client_id` (§11) if absent, writes the file with mode 0600, responds, and one second later re-executes the process (`os.execv(sys.executable, [sys.executable, "main.py"])` from a timer thread), because `config` is read at import (`config.py:160`) and a re-exec is the only way out of setup mode that works both under Docker's restart policy and in a venv. The page polls `/health` until `{"status": "ok"}` and then shows the done panel: the health panel (§5.5), the webhook URL with its key (§8), and the invite as a link and a QR (`https://push.<domain>/join#v=1&m=<machine_id>&l=<urlencoded local>&r=<urlencoded remote>&n=<name>`, 02-architecture.md §2.4, rendered client-side with an inline QR routine, no CDN script).

### 5.4 Setup route shapes

GAP: none of these shapes is in the ADR; Phase 8 adds them exactly as below, with `tests/test_setup.py` driving each through Flask's test client with plex.tv, `PlexServer`, TMDb and the model stubbed (ADR §7 Phase 8; constraints fact 17). The draft is one in-memory dict per process holding the account token, the chosen server, the two keys and the remote address; it is never written to disk and never returned.

| Route | Request | Response |
|---|---|---|
| `GET /setup` | none | `dashboard/setup.html` |
| `GET /setup/draft` | none | `200 {"signed_in": bool, "username": str\|null, "server": {"machine_id", "name", "url"}\|null, "tmdb_set": bool, "ai_set": bool, "remote": {"url": str\|null, "checked": bool}, "local_url": str, "expires_in": int}`; no token or key ever |
| `POST /setup/plex/start` | none | the `POST /auth/start` shape (`api/routes.py:1045-1077`; auth-and-tiers.md §1.4) with `forwardUrl` pointing at `/setup`; `409` when a draft belongs to another account; `429`/`502` as `/auth/start` |
| `GET /setup/plex/poll/<pin_id>?also=<link_id>` | none | `{"status": "waiting"}` \| `{"status": "unknown", "error"}` \| `{"status": "ok", "account": {"username", "email", "thumb"}, "servers": [{"machine_id", "name", "owned": true, "connections": [{"uri", "local": bool}]}]}`; `403 {"status": "denied", "error": "that account owns no Plex server"}` when the resources list holds no `owned` server |
| `POST /setup/plex/server` | `{"machine_id": str, "url": str\|null}` (`null` means "test the candidates for me") | `200 {"url", "name", "version", "tried": [{"url", "ok": bool, "error": str\|null}]}`; `400 {"error": "none of these addresses answered", "tried": [...]}` |
| `POST /setup/keys` | `{"tmdb_api_key": str\|"", "ai_api_key": str\|""}` | `200 {"tmdb": {"set": bool, "ok": bool\|null, "error": str\|null}, "ai": {"set": bool, "ok": bool\|null, "error": str\|null}}` (`ok` `null` when skipped) |
| `POST /setup/remote/check` | `{"url": str}` | §6.2 |
| `GET /setup/ping?nonce=<str>` | none | `200 {"nonce", "remote_addr", "host", "x_forwarded_for": str\|null, "x_forwarded_proto": str\|null, "x_forwarded_host": str\|null, "is_secure": bool}`; open at all times, but answers only when `nonce` equals a nonce minted by `POST /setup/remote/check` in the last 60 seconds (one use); otherwise `404 {"error": "unknown nonce"}` with no echo |
| `POST /setup/save` | `{"app_name": str, "remote_url": str\|""}` | `200 {"saved": true, "restarting": true}`; `400 {"error"}` naming the incomplete step; `409` from the wrong account |
| `GET /setup/health` | none | §5.5 |

Every setup `POST` reads `request.get_json(silent=True)` (never `force=True`) and answers `400 {"error": "json body required"}` when the content type is not `application/json`; no `/setup/*` response carries a CORS header; the page sends `X-Requested-With: setup` and the routes require it, so a cross-site form post (`enctype=text/plain`) from a page the owner visits after Save cannot drive `/setup/keys`, `/setup/save` or `/setup/plex/server` with the owner's cookie.

### 5.5 The health panel

Built from the checks in `test_connections.py:48-232`, which Phase 8 moves into importable functions (`api/checks.py`, with `test_connections.py` calling them so the CLI keeps working) and extends. `GET /setup/health` returns:

```json
{"config": {"ok": true, "path": "/data/config.yaml"},
 "plex": {"ok": true, "name": "Living room", "version": "1.41.0", "url": "http://host.docker.internal:32400", "error": null},
 "libraries": {"ok": true, "count": 4, "names": ["Films", "TV", "Anime", "Concerts"]},
 "tmdb": {"ok": true, "error": null}, "assistant": {"ok": false, "error": "no key"},
 "ffprobe": {"ok": false, "error": "not installed in the image"},
 "webhook": {"configured": true, "url": "http://192.168.1.10:5050/plex/webhook?key=…", "last_hit_at": null},
 "settle": {"every_minutes": 120, "last_run_at": "2026-09-22T08:00:00Z"},
 "remote": {"url": "https://nas.tail1234.ts.net", "last_check": {"ok": true, "at": "2026-09-22T08:05:00Z", "x_forwarded_proto": "https"}},
 "push": {"delivery": "polling", "native_devices": 0, "web_devices": 1, "relay_expires_at": null},
 "build": "missing-search-4"}
```

Each block renders as one row: a tick or cross, the name, the detail, and for a cross the sentence `test_connections.py` prints today. The `webhook`, `settle`, `push` and `build` values are the same ones `GET /api/v1/owner/status` reports to the Console (03-server-api.md §3.14), read from the same functions so the two never disagree. Timestamps are ISO 8601 `Z` (03-server-api.md §1.6) because the same functions feed `GET /api/v1/owner/status`. `webhook.url` is built from the same `Host`-derived address as `app.urls.local` (§5.3 step 5), never from the container's own interface.

## 6. Remote reach

Everything the app does at home works with nothing in this section done; the app's Plex-address probe finds `http://<lan address>:5050` by itself (02-architecture.md §2.3). Remote reach is for the train, and for household members who are never on the home network. It must be `https://` (constraints fact 10), and the server has no TLS code of its own (deployment-and-security.md §3), so something in front of it terminates TLS.

### 6.1 Tailscale Funnel, step by step

The primary path (ADR §4 "Remote reach"). Copy for the setup page and the README:

1. Make a Tailscale account at tailscale.com and choose the Personal plan (verified by design-A §4.2 that the free plan admits six users, tailscale.com/pricing; whether Funnel itself is included is gate 2, §6.3).
2. Install Tailscale on the machine running Docker: Linux `curl -fsSL https://tailscale.com/install.sh | sh`; macOS from the App Store or `brew install tailscale`; Synology from the Package Center (unverified: install methods from Tailscale's download page as remembered; the builder checks tailscale.com/download before writing the copy).
3. `sudo tailscale up` and sign in in the browser it opens.
4. In the Tailscale admin console, under DNS, enable HTTPS certificates (unverified: required for Serve and Funnel certificates according to the Funnel KB as remembered).
5. `sudo tailscale funnel --bg 5050`. The first run prints a link to the tailnet policy page to add the `funnel` node attribute; click it, accept, and run the command again. `tailscale funnel 5050` without `--bg` is the ADR's wording and runs in the foreground until stopped; `--bg` makes it persist across reboots (unverified: `--bg` is from the Funnel CLI reference as remembered; the builder confirms with `tailscale funnel --help` and uses whichever flag makes the mapping persistent).
6. `tailscale funnel status` prints the public address, `https://<machine>.<tailnet>.ts.net`. Funnel serves only ports 443, 8443 and 10000 publicly; 5050 is the local target only, so the address has no port (design-A §4.2, verified there against tailscale.com/kb/1223/funnel).
7. Paste that address into step 5 of the setup page and press "Check".

Nothing is installed on any phone; visitors need no Tailscale account (design-A §4.2). The other Tailscale mode, every member joining the tailnet, is not recommended because it puts an install on every member (ADR §4), but it is the fallback in §6.3.

### 6.2 The self-check

`POST /setup/remote/check {"url"}` does, from the server process: reject anything not `https://` with `400 {"ok": false, "error": "the address must start with https://"}`; generate a nonce (`secrets.token_urlsafe(16)`, kept in memory with its minting time, and consumed by the first matching ping; §5.4 is why the route can stay open after Save without echoing headers to anyone); `GET <url>/setup/ping?nonce=<nonce>` with a 10-second timeout and certificate verification on; compare the returned nonce. Response:

```json
{"ok": true, "url": "https://nas.tail1234.ts.net", "status": 200,
 "echo": {"remote_addr": "127.0.0.1", "host": "nas.tail1234.ts.net", "x_forwarded_for": null, "x_forwarded_proto": null, "x_forwarded_host": null, "is_secure": false},
 "warnings": ["No X-Forwarded-Proto arrived, so the web page's sign-in cookie will not be marked Secure behind this address. The phone app is unaffected."],
 "error": null}
```

`ok` is false with `error` set on DNS failure, connection refusal, a certificate error, a non-200 status, a nonce mismatch (the address reaches some other server) or a timeout, each with a one-sentence remedy ("Funnel is not running: run `tailscale funnel --bg 5050` and check `tailscale funnel status`"). The echo is stored in the draft and, after Save, in the health panel's `remote.last_check`. The echo is also the header capture ADR §6 gate 2 asks for: the builder records one real check through Funnel in the Phase 8 PR, which settles whether `X-Forwarded-For` and `X-Forwarded-Proto` arrive (ADR §5 item 8: the two judges disagreed and neither verified it).

### 6.3 When Funnel is not available on the plan (gate 2)

The Funnel knowledge-base page says all plans, the pricing chart lists Funnel under paid tiers (ADR §5 item 9; design-A §4.2, both pages fetched 2026-09-21). Before Phase 8's copy is written, the builder or owner tests `tailscale funnel` on a free Personal account and records the result in the PR. Then:

- Permitted: §6.1 stands as written.
- Not permitted: §6.1 leads with Cloudflare Tunnel (§6.5) for owners with a domain and the reverse proxy (§6.4) for those with one already, and offers the tailnet fallback: every household member installs Tailscale and is invited to the tailnet (six users on the Personal plan), the owner runs `sudo tailscale serve --bg 5050` instead of `funnel`, and the address `https://<machine>.<tailnet>.ts.net` works only on the tailnet (unverified: `tailscale serve` semantics as remembered from tailscale.com/kb/1312/serve). It is still `https://`, so the app's rule against plain HTTP to a non-private address (100.64.0.0/10 is not RFC 1918; 03-server-api.md §1) is not violated. The self-check still passes because the server is itself on the tailnet.

The decision does not depend on the header answer: the door is off in the image regardless (§7).

### 6.4 A reverse proxy

For owners who already run nginx, Caddy or Traefik with a domain and certificate. The existing configurations are kept verbatim in the rewritten `docs/DEPLOYMENT.md`: nginx with `proxy_pass http://127.0.0.1:5050`, `X-Real-IP`, `X-Forwarded-For $proxy_add_x_forwarded_for`, `X-Forwarded-Proto $scheme`, `proxy_read_timeout 120s`, `client_max_body_size 8m` (docs/DEPLOYMENT.md:283-306); Caddy `your-host { reverse_proxy 127.0.0.1:5050 }`, which sets the headers itself (docs/DEPLOYMENT.md:311-315). With the Docker image the app port is published on `0.0.0.0:5050`; an owner who wants only the proxy to reach it changes the mapping to `-p 127.0.0.1:5050:5050`, at the cost of the LAN probe, and the README says so.

Requirements: the proxy must set `X-Forwarded-For` and `X-Forwarded-Proto: https`. `ProxyFix(x_proto=1, x_host=1)` with werkzeug's default `x_for=1` trusts exactly one hop (`api/routes.py:112-119`; ADR §5 item 24), so `request.is_secure`, the web cookie's `Secure` flag (`api/routes.py:1152`) and `request.url_root` for the web sign-in's `forwardUrl` are right behind one proxy and wrong behind two unless the outer one's headers are forwarded by the inner one. The verification from outside the house is unchanged: `curl -s -o /dev/null -w '%{http_code}\n' https://your-host/libraries` must print `403` (docs/DEPLOYMENT.md:324-329; SECURITY.md:39-47), and the setup page's self-check adds the header echo.

### 6.5 Cloudflare Tunnel

For owners with a domain on Cloudflare. Install `cloudflared`, `cloudflared tunnel login`, `cloudflared tunnel create plexget`, `cloudflared tunnel route dns plexget plexget.example.com`, a config with `ingress: [{hostname: plexget.example.com, service: http://localhost:5050}, {service: http_status:404}]`, then `cloudflared tunnel run plexget` as a service (unverified: command names from Cloudflare's tunnel guide as remembered; the builder checks developers.cloudflare.com before writing the copy). `cloudflared` sets `X-Forwarded-For`, `X-Forwarded-Proto` and `CF-Connecting-IP` (unverified). The tunnel is one hop, so `ProxyFix` sees the right client. Cloudflare's free plan proxies the traffic through its edge, which the README states plainly: a third party then sits on every request, which is the trade Funnel does not make.

## 7. The loopback door, and why the image ships it off

The guard treats a request with no `X-Forwarded-For` header from `127.0.0.1` or `::1` as the owner (`api/routes.py:1261-1265`; deployment-and-security.md §4.1, §4.3). It exists so a browser on the machine itself and Plex's webhook from the same machine need no sign-in (`api/routes.py:673-677`). Its danger is documented: any proxy on the same host that omits the header makes every visitor the owner (SECURITY.md:28-47), and any local process can open `127.0.0.1:5050` and be the owner (deployment-and-security.md §4.3).

Phase 2 adds `app.loopback_owner` (default `true`, so venv installs keep today's behaviour) and `PLEXGET_LOOPBACK_OWNER`, which overrides it. The image sets `PLEXGET_LOOPBACK_OWNER=0` and the setup page writes `app.loopback_owner: false` (ADR §4 "Bind address"). The reasons, for the README: in bridge mode the door is closed anyway because the peer is the bridge gateway (docs/DEPLOYMENT.md:256-260); with host networking or a Funnel or proxy on the same machine, traffic may arrive from `127.0.0.1` with no forwarding header, which the Serve/Funnel documentation does not promise to add (ADR §5 item 8), and the door would then be open to the internet; and a server bound to `0.0.0.0` on the LAN must never have it open at all. With the door off the owner console at `/dashboard` needs the owner's Plex sign-in, which is what the app does anyway, and the webhook needs the key (§8). The invariant is enforced in code, not by the owner (09-security.md §7): the door applies only when the effective `app.loopback_owner` is true and the effective `flask.host` is `127.0.0.1`, `localhost` or `::1`. When `flask.host` is anything else and `loopback_owner` is true, `main.py` logs one warning at start ("loopback owner door disabled because flask.host is not loopback") and the guard treats the door as off; `tests/test_webhook_key.py` has `test_loopback_door_closed_when_bound_beyond_loopback`. The cron line in the old `docs/DEPLOYMENT.md:366` (`curl -X POST http://127.0.0.1:5050/wanted/recheck`) stops working in Docker and is removed from the rewritten file; the scheduler replaces it.

## 8. The webhook key and the settle scheduler

Today `arrived` is sent only by `settle_wanted()` (`api/routes.py:625-656`), reached from `POST /plex/webhook` (Plex Pass, loopback only) or `POST /wanted/settle`, which nothing shipped calls (push-and-notifications.md §3.3). In Docker with the door off, neither path exists, so without the two changes below the app's headline promise would be false.

`plex.webhook_key`: generated at Save (`secrets.token_urlsafe(24)`), written to `config.yaml`. `POST /plex/webhook?key=<key>` is accepted when the query key equals the configured one (constant-time compare). The check lives in `guard()` (`api/routes.py:1235-1273`), before the owner-session test, because `/plex/webhook` is in neither `OPEN_PATHS` nor a household path (`:1229-1233`) and the guard would otherwise answer `403 {"error": "that part is the owner's"}` to every keyed, sessionless request with the door off; a check inside the handler is unreachable in the image. Rule: when `request.path == "/plex/webhook"`, `request.method == "POST"`, `config["plex"]["webhook_key"]` is non-empty and `hmac.compare_digest(request.args.get("key", ""), config["plex"]["webhook_key"])` is true, `guard()` returns `None`; when the path is `/plex/webhook` and the key is missing or wrong and the loopback door does not admit the request, `guard()` returns `403 {"error": "webhook key missing or wrong"}`. An empty configured key (the venv default `""`) disables the key path entirely: nothing is ever compared against `""`, so `?key=` from anyone on a `0.0.0.0` server is `403`. The handler `plex_webhook()` (`:669-695`) is unchanged. When the door is open (venv installs, unchanged) the loopback rule still admits the keyless form. The done panel and the health panel show the full URL to paste into Plex's Settings, Webhooks, built from the same `Host`-derived address as `app.urls.local` (§5.3 step 5): `http://<host as the owner reached it>:5050/plex/webhook?key=<key>`, plain HTTP because Plex posts it on the LAN. Webhooks are a Plex Pass feature (docs/DEPLOYMENT.md:362); the panel says so and says what happens without one.

The scheduler: `main.py` starts one daemon thread when the effective `app.settle_every_minutes` is above zero (`PLEXGET_SETTLE_MINUTES` overrides; image `120`, venv default `0`). It sleeps the interval first, then calls `settle_wanted()` (with the Phase 2 loop fix that delivers to every device kind before falling back to the owner, ADR §2) and records `last_run_at`; a `PlexUnavailable` is logged and the next tick tries again; it never runs while a request-triggered settle is in progress (one lock). "The server checks every two hours" is the copy on the panel and in the app (02-architecture.md §2.9). It does not start in setup mode.

## 9. Upgrading

Only the latest tag is supported (SECURITY.md:16-18). The upgrade is a new image and a recreated container; the volume carries everything.

```bash
docker pull ghcr.io/<org>/plexget:latest && docker rm -f plexget && docker run -d --name plexget --restart unless-stopped -p 5050:5050 --add-host host.docker.internal:host-gateway -v plexget-data:/data ghcr.io/<org>/plexget:latest
```

or `docker compose pull && docker compose up -d`, or re-run `install.sh`, which does the same. Watchtower or a NAS's own update button also works; the plan does not ship an in-app updater (design-A §7 "What remains hard").

The migration promise, which the builder keeps in every phase: every schema change is an additive `CREATE TABLE IF NOT EXISTS` or a guarded `ALTER TABLE … ADD COLUMN` in `db/database.py`, applied at start, in the pattern the `wanted` table already uses (design-A §2.2 cites `db/database.py:152-157`); every new config key has a default in `config.py` `DEFAULTS` so an older `config.yaml` keeps working untouched; a newer image never asks the owner to edit `config.yaml` or run a script; nothing in an upgrade deletes a row, a file or a session. Downgrading is not supported: extra columns are harmless to older code, but an older image may not know a newer `config.yaml` key, so the README says "pull forward, not back". `GET /health` reports `build` after an upgrade, which is how the owner or the app confirms one landed (`api/routes.py:127-132`).

## 10. Backup and restore

Back up the volume, stopped, so the SQLite file is consistent:

```bash
docker stop plexget
docker run --rm -v plexget-data:/data -v "$PWD":/backup alpine tar czf /backup/plexget-$(date +%F).tgz -C /data .
docker start plexget
```

Restore onto a fresh volume, then start the container:

```bash
docker volume create plexget-data
docker run --rm -v plexget-data:/data -v "$PWD":/backup alpine sh -c "cd /data && tar xzf /backup/plexget-YYYY-MM-DD.tgz && chown -R 1000:1000 /data"
```

With a bind mount, copy the folder. The archive holds `config.yaml` (the Plex admin token and every key, SECURITY.md:72-74), `secret.key`, `vapid.json`, `client_id`, `relay.json`, the database and the looks; keep it where a household member cannot read it. What losing each file means is the table in §4.1. Deleting `secret.key` on purpose is the "sign everyone out at once" switch (SECURITY.md:106-119).

## 11. Migrating an existing venv install

Two routes. Either keeps the install's identity at plex.tv, which is ADR §6 gate 3: whether per-server tokens minted under the shared `uuid5(NAMESPACE_DNS, "plex-ops.local")` identifier (`api/plex_auth.py:27`; deployment-and-security.md §4.4) keep working once the server presents a different `X-Plex-Client-Identifier` is unverified, so the Phase 2 rule is: when the data directory already holds `secret.key` or the SQLite file and no `client_id` file, write the legacy `uuid5` value into `client_id` and keep using it; only a data directory with neither gets a fresh `uuid4`. `tests/test_setup.py` asserts both branches. The owner never sees the identifier.

Route A, stay in the venv: `git pull`, `pip install -r requirements.txt` (for `waitress`), restart the service (docs/DEPLOYMENT.md:106-110). Nothing else changes: `config.yaml` exists so there is no setup mode, the door stays open, `flask.host` stays `127.0.0.1`, the scheduler stays off. To use the phone app from the LAN and away, add to `config.yaml` by hand and restart:

```yaml
flask:
  host: "0.0.0.0"          # the app's probe needs the LAN; the door below must then be off
app:
  loopback_owner: false    # already off automatically once host is not loopback (§7); written for clarity
  settle_every_minutes: 120
  urls:
    local: ["http://192.168.1.10:5050"]
    remote: ["https://nas.tail1234.ts.net"]
plex:
  webhook_key: "<any long random string>"
```

then repoint Plex's webhook at `http://192.168.1.10:5050/plex/webhook?key=…` and delete the cron recheck line. The README says exactly this (ADR §5 item 7).

Route B, move to Docker: stop the service; create the volume; copy `config.yaml`, everything in `data/` and the SQLite file (`database.path`, default `plex_ops.db` beside `config.py`) into it; edit the copied `config.yaml` so `database.path` is `/data/plexget.db` (rename the file to match), `flask.host` is `0.0.0.0`, `app.loopback_owner` is `false`, and `plex.url` no longer says `localhost` (use `http://host.docker.internal:32400` or the LAN address, docs/DEPLOYMENT.md:262-266); `chown -R 1000:1000`; start with the §4.3 command. Because `secret.key` came across, nobody signs in again; because `client_id` was written from the legacy value on first start, plex.tv sees the same device. Sessions in the copied database keep working: raw-token rows are still honoured by the Phase 1 fallback (ADR §2).

```bash
docker volume create plexget-data
docker run --rm -v plexget-data:/data -v /opt/plexget:/src alpine sh -c "cp /src/config.yaml /data/ && cp -r /src/data/. /data/ && cp /src/plex_ops.db /data/plexget.db && chown -R 1000:1000 /data"
```

## 12. What remains hard, said honestly

The README's closing section, kept short and true (design-A §7):

- Installing Docker. The app cannot remove it; Docker Desktop on a Mac is a download and a licence prompt, Linux is one script, a NAS is a package.
- Keeping a machine on. A sleeping Mac stops answering (docs/DEPLOYMENT.md:201-204); a laptop lid ends the household's evening.
- The Tailscale account, the one-time policy click, and the open question of whether Funnel is on the free plan (§6.3). Without any of §6, the app works only at home, and the setup page lets the owner choose that.
- Plex Pass for the instant webhook; without it, arrivals are noticed within two hours, not two seconds, and the copy says "checks every two hours" rather than "instantly".
- Updates are a command or a NAS button, not automatic. Backups are a command, not a button.
- The setup page needs the printed setup code until saved, and the copy says to finish it straight away; the code is on stdout, which is one more place a NAS owner has to look.
- Publishing 5050 on the LAN also exposes `/`, `/request`, `/auth/*`, `/manifest.webmanifest`, `/sw.js` and `/themes/art/*` to everyone on the network, and `/auth/start` mints plex.tv pins under the server's identifier for anyone on the LAN (ADR §5 item 19). The household is people already trusted with the library (SECURITY.md:151-169); a guest network is the owner's answer if that is not so.
- Windows is untested (README.md:134-135); a bind mount on a NAS needs a `chown`; `<org>` in every command is a placeholder until the owner confirms the repository.
- The web page keeps working throughout, and its owner console now needs the Plex owner sign-in rather than a local browser.

## 13. Gates and gaps carried by this document

| Gate or gap | Where | Who closes it |
|---|---|---|
| ADR §6 gate 2, Funnel on the Personal plan and forwarded headers | §6.2 echo, §6.3 branch | builder or owner before Phase 8's copy; result recorded in the PR |
| ADR §6 gate 3, client identifier migration | §11 rule; `tests/test_setup.py` asserts legacy `uuid5` kept where `data/` exists and fresh `uuid4` otherwise | Phase 2 test, re-run in Phase 8 |
| ADR §7 Phase 8 gate | `docker build`; `/health` returning `{"status": "setup"}` on a fresh volume; `pytest tests/test_setup.py`; a first run recorded on Linux and on Docker Desktop | builder, with the human's two recorded first runs |
| GAP: setup route shapes | §5.4 | Phase 8, with tests |
| GAP: `GET /setup/health` shape and `api/checks.py` | §5.5 | Phase 8, sharing functions with `GET /api/v1/owner/status` |
| GAP: `POST /plex/webhook?key=` acceptance rule, in `guard()` not the handler, and `403` body | §8 | Phase 2, `tests/test_webhook_key.py`: right key with the door off `200`; wrong, missing and empty key `403`; empty configured key with `?key=` `403`; keyless with the door open from loopback `200`; `test_loopback_door_closed_when_bound_beyond_loopback` (§7) |
| GAP: setup code until Save; `GET /setup/ping` nonce; JSON-only setup `POST`s | §5.2, §5.4, §6.2 | Phase 8, `tests/test_setup.py`: `test_setup_requires_code_until_save`, a stale or reused nonce is `404`, a non-JSON `POST` is `400` |
| Unverified third-party commands | Tailscale install and `--bg` (§6.1), `tailscale serve` (§6.3), `cloudflared` (§6.5), Docker Desktop `host-gateway` (§4.3) | builder checks each vendor page before the copy is final and marks the PR with what was checked |
