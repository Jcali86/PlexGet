# Phase 8: Install: image, compose and setup page

Machine: Linux (the remote Claude Code environment or any Linux machine with Docker Engine 24 or later and `buildx`). Repository: the PlexGet server repository (this one). Branch: `phase-8-install-image-compose-and-setup-page` from `main`. This phase may run in parallel with Phases 4 to 7; it depends on Phase 2 merged and on nothing in `<AppName>-ios`.

## Read first

Read each file in full, in this order, before any other tool call.

1. `docs/ios/00-README.md` §3, §4, §5 (the `<org>` placeholder).
2. `docs/ios/07-build-phases.md` §1, §2, §3, §4.1 and §5 "Phase 8".
3. `docs/ios/06-install-and-setup.md`, all of it; §5.4 and §5.5 are the GAP shapes this phase adds, §13 the gates.
4. `docs/ios/03-server-api.md` §3.1, §3.14, §5, §6.2, §7.
5. `docs/ios/10-decision-record.md` §2 ("Install artefacts"), §4 rows "Remote reach", "Server install", "Bind address", §5 items 7, 8, 9, 19 and 24, §6 items 2 and 3.
6. `docs/ios/09-security.md` §2.4, §7, §11 (the `tests/test_setup.py` names), §12.
7. `docs/ios/02-architecture.md` §2.9, §3.5.
8. `config.py`, `main.py`, `api/routes.py` lines 100-135 and 1230-1275, `api/plex_auth.py` lines 20-60 and 200-240, `api/secrets_store.py`, `test_connections.py`, `docs/DEPLOYMENT.md`, `docker-compose.yml`, `.env.example`, `tests/conftest.py`, `tests/test_config_env.py`, `tests/test_client_id.py`, `tests/test_webhook_key.py`, `CONTRIBUTING.md`.
9. `docs/build/deviations.md` if it exists.

## Goal

Ship the server as a published image with a first-run setup page: a `Dockerfile` on `python:3.12-slim` running as user `plexget` with the four switches baked in, `docker-compose.yml` rewritten in place with the `plexget` service and Posteria under `profiles: [posters]`, `install.sh`, `.github/workflows/image.yml` building on pull requests and publishing amd64 and arm64 to `ghcr.io/<org>/plexget` on tags `v*`, the setup blueprint `api/setup.py` with `dashboard/setup.html` (five steps, the self-check, the done panel) and the health panel from `api/checks.py` extracted from `test_connections.py`, setup-mode serving in the guard, `docs/DEPLOYMENT.md` rewritten Docker-first, and `tests/test_setup.py` proving every setup route with plex.tv, `PlexServer`, TMDb and the model stubbed. A container on an empty volume answers `/health` with `{"status": "setup"}` and the venv path is unchanged.

## Constraints

- Every server change ships with pytest tests under `tests/`; `pytest -q` and `git diff --exit-code -- tests/fixtures/api` stay clean; no `/api/v1` shape or fixture changes in this phase.
- The image contains no secret, key, token, `config.yaml` or `.env`; `.dockerignore` excludes `venv/`, `.git/`, `data/`, `config.yaml`, `.env*`, `*.db*`, `logs/`, `posteria/`, `docs/screenshots/`, `scratchpad/`.
- The venv path keeps today's defaults: `python main.py` with `config.yaml` beside `config.py` and no environment variables binds `127.0.0.1` with the loopback door on and the scheduler off; the image and the setup page are what change the defaults, and they change `flask.host: 0.0.0.0` and `app.loopback_owner: false` together.
- One process, eight threads (`waitress` from Phase 2); never a second worker, a `gunicorn` pool or `network_mode: host` as the compose default.
- The setup routes are browser routes: nothing in `api/native.py` changes beyond setup-mode serving of `GET /api/v1/server`, and the Kit decodes none of §5.4.
- Every third-party command in the owner copy (Tailscale install and `funnel --bg`, `tailscale serve`, `cloudflared`, Docker Desktop `host-gateway`) is checked against the vendor's page before the copy is final and the PR says what was checked (06 §13); a command that cannot be checked stays marked (unverified) in the copy's source comment, never presented as fact.
- No banned word (`download`, `torrent`, `usenet`, `nzb`, `indexer`, `tracker`, `sonarr`, `radarr`, `seed`, `magnet`, `release group`) in `dashboard/setup.html` or `docs/DEPLOYMENT.md`; `scripts/banned-words.sh` is the gate.
- Forbidden in this phase (07 §5 Phase 8): baking a secret, `config.yaml` or `.env` into the image; changing any `/api/v1` shape or fixture; changing the venv path's defaults (`127.0.0.1`, door on); a compose file that builds locally; `network_mode: host` as the compose default; a second worker process; `api/native.py` beyond setup-mode serving.

## Steps

1. `git checkout main && git pull && git checkout -b phase-8-install-image-compose-and-setup-page`; `venv/bin/pytest -q` must pass before any change (Phase 2 merged). Confirm `docker --version` and `docker buildx version`; if Docker is unavailable in this session, the Docker gate lines go under `## Not run here` for the owner and everything else proceeds.
2. `Dockerfile` at the repository root exactly as `06-install-and-setup.md` §2, and `.dockerignore` per §2's rule. `main.py` treats `PLEXGET_CONFIG` being set as the signal to bind `0.0.0.0` while in setup mode (06 §2), with a test in `tests/test_setup.py`.
3. `docker-compose.yml` replaced in place with the file of 06 §3 (service `plexget` on `ghcr.io/<org>/plexget:latest`, `5050:5050`, `plexget-data:/data`, `extra_hosts` `host.docker.internal:host-gateway`, Posteria under `profiles: ["posters"]`). Note: design-A §2.4 names `compose.yaml` and `compose.posteria.yml`; the ADR keeps `docker-compose.yml` and the ADR wins. `.env.example` keeps its Posteria keys; the `plexget` service reads none of them.
4. `install.sh` (POSIX `sh`) with the eight behaviours of 06 §4.4 in order: the Docker check, flags `--port`, `--tag`, `--host-network`, `--name`, the pull, the stop-and-remove of an existing container, the volume and `docker run` line of §4.3, the sixty-second `/health` poll, the printed setup or upgraded line, non-zero exit on failure. Run it under `sh -n` and `shellcheck` if present.
5. `.github/workflows/image.yml`: `docker/setup-qemu-action`, `docker/setup-buildx-action`, `docker/login-action` to GHCR with `GITHUB_TOKEN`, `docker/build-push-action` with `platforms: linux/amd64,linux/arm64`, `push: false` on pull requests, `push: true` with tags `ghcr.io/<org>/plexget:<tag>` and `:latest` on tags `v*` (06 §1; action majors are unverified, record the versions used).
6. `api/checks.py`: move the checks of `test_connections.py:48-232` into importable functions (config, plex, libraries, tmdb, assistant, ffprobe, webhook, settle, remote, push, build) returning the blocks of 06 §5.5; `test_connections.py` calls them so the CLI keeps working; `GET /api/v1/owner/status` (03 §3.14) reads `webhook`, `settle`, `push` and `build` from the same functions so the two never disagree.
7. `config.py` and `api/routes.py`: in setup mode (`config["_setup"]` from Phase 2) `create_app()` registers the setup blueprint, `/health` (`{"status": "setup"}`) and `GET /api/v1/server` (`"setup": true`, `server_name` and `plex_machine_id` `null`); the guard answers every other path `503 {"error": "this server is being set up", "setup": true}`; the scheduler thread does not start. After the first save, `/setup` and `/setup/*` require an owner session through `current_user()` (bearer or cookie).
8. `api/setup.py`: the routes and shapes of 06 §5.4 exactly (`GET /setup`, `GET /setup/draft`, `POST /setup/plex/start`, `GET /setup/plex/poll/<pin_id>?also=`, `POST /setup/plex/server`, `POST /setup/keys`, `POST /setup/remote/check`, `GET /setup/ping?nonce=`, `POST /setup/save`, `GET /setup/health`); one in-memory draft per process bound to the first Plex account, thirty minutes from its last change, `409` for a second account, never written to disk and never returned; the account token discarded at Save; step 2 storing the per-server access token from `server_access_token()` (`api/plex_auth.py:203-234`), not the account token; the self-check of 06 §6.2 (`https://` only, nonce echo, ten-second timeout, certificate verification on, the header echo stored); Save writing the keys of 06 §5.3 with `flask.host: 0.0.0.0`, `app.loopback_owner: false`, `app.settle_every_minutes: 120`, `plex.webhook_key`, `app.urls`, and `database.path: "/data/plexget.db"` only when `PLEXGET_DATA` is set; generating `data/secret.key` and `data/client_id` if absent (legacy `uuid5` kept where `data/` already holds `secret.key` or the SQLite file, 06 §11); file mode `0600`; the re-exec one second later from a timer thread.
9. `dashboard/setup.html`: one file in the style of `dashboard/request.html`, the warning sentence above step 1, five numbered steps with the copy of 06 §5.3, the bind-address sentence, the done panel with the health panel rows, the webhook URL with its key and the Plex Pass note, and the invite as a link and a QR drawn by an inline routine with no CDN script. Remote-reach copy per 06 §6.1 with §6.3's branch held until step 12 answers, and §6.4, §6.5 for the alternatives.
10. `docs/DEPLOYMENT.md` rewritten Docker-first (the §4.3 commands, NAS forms, the compose file, upgrade §9, backup §10, the venv migration §11 both routes, "what remains hard" §12) with the venv and systemd path second and the existing nginx and Caddy blocks kept verbatim; the old cron recheck line removed. `README.md` gains the one-command install, the LAN-exposure paragraph of `10-decision-record.md` §5 item 19 and the venv `config.yaml` block of 06 §11; `SECURITY.md` states the door's status per install path.
11. `tests/test_setup.py`: every route of 06 §5.4 through Flask's test client with plex.tv, `PlexServer`, TMDb and the model monkeypatched in `tests/conftest.py`; `/health` → `{"status": "setup"}` and `GET /api/v1/server` → `setup: true` on a fresh `PLEXGET_DATA`; the `503` guard answer; the health panel shape; after Save `SETUP_MODE` false and the page owner-only; the tests `09-security.md` §11 names: `test_setup_open_until_save_then_owner_only`, `test_setup_draft_bound_to_first_account`, `test_setup_never_writes_debug`, `test_secret_key_and_client_id_permissions` (gate 3 re-asserted: legacy `uuid5` kept where `data/` exists, fresh `uuid4` otherwise). The re-exec is asserted on a monkeypatched `os.execv`, never run.
12. HUMAN (`10-decision-record.md` §6 item 2): with a free Tailscale Personal account on a machine running the image, run `tailscale funnel 5050` (or `--bg`), record whether it is permitted, run the setup page's "Check", and paste the `echo` block from `POST /setup/remote/check` (which is the header capture); remove any temporary header dump before merge. Then finalise the Funnel copy: §6.1 as written if permitted, §6.3's ordering if not, and the `X-Forwarded-Proto` sentence in `docs/DEPLOYMENT.md` per the echo. Wait for this before the copy is final.
13. HUMAN: a first run on a Linux box and on Docker Desktop, from `install.sh` to a completed setup page and a household sign-in, with screenshots; note the Docker Desktop `host.docker.internal` result.
14. Run the gate; commit; open the pull request as under Deliver; stop.

## Gate

```
docker build -t plexget:phase8 .                                        # pass: exit 0
docker volume create plexget-test && docker run -d --name plexget-test -p 5050:5050 -v plexget-test:/data plexget:phase8
sleep 3 && curl -s localhost:5050/health                                # pass: {"status": "setup"}
curl -s -o /dev/null -w '%{http_code}\n' localhost:5050/libraries       # pass: 403
docker run --rm plexget:phase8 sh -c 'test ! -e /app/config.yaml && echo clean' && docker rm -f plexget-test && docker volume rm plexget-test   # pass: clean
venv/bin/pytest -q tests/test_setup.py                                  # pass: "N passed"
venv/bin/pytest -q                                                      # pass: "N passed"
scripts/banned-words.sh dashboard/setup.html docs/DEPLOYMENT.md         # pass: exit 0
```

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you cannot, write docs/build/deviations.md and stop.

## Deliver

One pull request in this repository from `phase-8-install-image-compose-and-setup-page`, titled `Phase 8: Install: image, compose and setup page`, with `image.yml` green for both architectures on the pull request. The body carries the headings of `07-build-phases.md` §1.2 in order: `## Goal` (07's Phase 8 Goal paragraph), `## Changed` (07's Phase 8 deliverables table, ticked or "not needed: <why>"), `## Gate output` (every command and its complete output, verbatim), `## Not run here` (the Docker lines if this session has no Docker), `## Human steps` (the Funnel result with the pasted `echo` block, the two recorded first runs with screenshots, each "done: <date, by whom, result>" or "pending"), `## Deviations`, `## Forbidden` (each item with "confirmed absent" and the grep: `git diff main --stat -- tests/fixtures/api api/native.py`, `grep -n "network_mode" docker-compose.yml`, `grep -n "build:" docker-compose.yml`, `grep -n "processes\|workers" main.py Dockerfile`, `docker run --rm plexget:phase8 sh -c 'ls -a /app | grep -E "config.yaml|\.env"'` printing nothing). Also list the vendor pages checked for the third-party commands (06 §13). Then stop and wait for the owner.

## Do not

- Do not copy `config.yaml`, `.env`, `data/`, a key or a token into the image, and do not add a build argument or environment default that carries one.
- Do not change `config.py` so that a `config.yaml` that exists but is invalid enters setup mode; only an absent file does, and every other failure still stops the process.
- Do not change the venv defaults: no environment variables and a local `config.yaml` must still give `127.0.0.1`, the door on and the scheduler off.
- Do not write the account token, a draft, a key or a nonce to disk or return one from `GET /setup/draft`; the draft is process memory and dies with it.
- Do not accept a plain `http://` remote address in `POST /setup/remote/check` or write one to `app.urls.remote`.
- Do not change any `/api/v1` route body, any fixture or anything in `api/native.py` beyond serving `GET /api/v1/server` in setup mode; a needed change there is a deviation note.
- Do not make `network_mode: host` the compose default, add a `build:` block to the compose file, or start a second worker process or replica anywhere.
- Do not present a Tailscale, Cloudflare or Docker Desktop command as fact until the vendor page was read in this session; leave the (unverified) mark in the source comment otherwise.
- Do not finalise the Funnel copy or the `X-Forwarded-Proto` sentence before the owner's Funnel check in step 12 is pasted into the pull request.
- Do not run `os.execv` in a test, open a real socket in a test, or write a banned word into the setup page, the README or `docs/DEPLOYMENT.md`.
