# Phase 2: Server: events, devices, arrivals and install switches

Machine: Linux. Repository: this server repository (public, MIT). Phase 1 is merged and `venv/bin/pytest -q`
passes. The relay does not exist yet; `api/relay.py` is written against 05-push-relay.md and stubbed in tests.

## Read first

Read each file in full, in this order, before any other tool call.

1. `docs/ios/00-README.md` §3-§4
2. `docs/ios/10-decision-record.md` §2 (server and install paragraphs), §4 rows "Push", "Server install", "Arrival
   matching", "Bind address", "Exchange rate limiter", §5 items 7, 19 and 24, §6 items 3 and 8
3. `docs/ios/07-build-phases.md` §5 "Phase 2, Server: events, devices, arrivals and install switches"
4. `docs/ios/03-server-api.md` §1.6-§1.7, §3.8-§3.14, §4.3 (the `tmdb_id` GAP), §4.17 (the `tmdb_id` GAP),
   §4.19, §6.4, §7, §9
5. `docs/ios/02-architecture.md` §2.8-§2.13 (arrival, message, announcement, relay push, polling), §3.2-§3.5
6. `docs/ios/05-push-relay.md` §4.5 (the push request and its answers), §8 (`api/relay.py`, the whole section)
7. `docs/ios/06-install-and-setup.md` §5.1 (setup mode in `config.py`), §7 (the door), §8 (webhook key and
   scheduler), §11 (client identifier rule), §13 (the webhook GAP and gate 3)
8. `docs/ios/09-security.md` §2.2 (the `push/test` limiter and the per-actor relay budget), §6 (the limiter table),
   §7, §11 (server tests by name), §13
9. `api/push.py` in full; `api/routes.py` lines 560-710, 960-1060, 1229-1273, 1975-2050; `scanner/wanted_search.py`
   lines 300-460; `api/plex_auth.py` lines 20-40; `api/secrets_store.py`; `config.py` in full; `main.py`;
   `config.example.yaml`; `tests/conftest.py`

## Goal

Give the server what push and polling need (`events`, `GET /api/v1/changes`, `native_devices`, the push endpoints,
`api/relay.py` and the fan-out, the configurable announcement), make arrivals correct (`wanted.tmdb_id`,
`Guid`/`kind`/`season` in `recheck()`, the `settle_wanted()` loop fix, the settle scheduler), and add the install
switches (`plex.webhook_key`, `app.loopback_owner`, `app.urls`, `data/client_id`, `waitress`, `PLEXGET_DATA`,
setup mode), all tested with the relay and web push stubbed (07-build-phases.md §5 Phase 2).

## Constraints

- Forbidden in this phase (07-build-phases.md §5 Phase 2): any change to notification wording or catalogue keys
  (`config.example.yaml` gains only `notifications.announcement`, the `app.*` keys and `plex.webhook_key`); a
  network call in a test; anything listed under Phase 8 (`Dockerfile`, compose, `install.sh`, `api/setup.py`,
  `dashboard/setup.html`, the setup-mode guard); UI code; a dependency other than `waitress`; a second worker
  process anywhere (the in-memory limiters at `api/routes.py:969, 1013, 1029` need one process).
- `api/relay.py` sends exactly `handle, event, title, body, collapse_id, data.ref, data.srv` with `Authorization:
  Bearer <server_key>`, a 5 s timeout, and never a token, username or URL (05 §8; 09 §11).
- `data/relay.json` is written with mode `0600` like `data/vapid.json` (`api/push.py:26-48`); the server key is
  never in any response (03 §7).
- A member's `GET /api/v1/changes` never carries another person's event or `unread_messages` (03 §3.11).
- Every server change ships with tests and re-records `tests/fixtures/api/`; the Phase 1 tests keep passing.

## Steps

1. `git checkout -b phase-2-server-events-devices-arrivals-and-install-switches main`; run `venv/bin/pytest -q`.
2. `config.py`: add `DATA_DIR = Path(os.environ.get("PLEXGET_DATA") or ROOT / "data")` (this prompt's name for the
   one place the data directory is resolved; `api/secrets_store.py`, `api/push.py` and `api/themes.py`
   (`data/themes.json`, `data/themes/`, `api/themes.py:19-21`; 06 §4.1) read it from here), the
   environment overrides `PLEXGET_LOOPBACK_OWNER` and `PLEXGET_SETTLE_MINUTES`, and the `DEFAULTS` keys `app.urls:
   {local: [], remote: []}`, `app.loopback_owner: true`, `app.settle_every_minutes: 0`, `plex.webhook_key: ""`,
   `app.native_signin: true`, `app.min_app_version: "1.0.0"` (02 §3.5; Phase 1 read them with a default),
   `notifications.announcement: {enabled: true, title, body}` with defaults `title: "{app_name}"` and `body:
   "{text}"`, which reproduce today's `POST /announce` call at `api/routes.py:1855-1858` (`web_push.broadcast(db,
   user_library.app_name(), body, …)`; `api/push.py` holds no announcement wording); the route formats them with
   `app_name` and `text`. When the file at `CONFIG_PATH` does not exist, `load_config()` returns the merged
   `DEFAULTS` with `config["_setup"] = True` and skips `_check_required()` (06 §5.1); expose it as
   `SETUP_MODE`. Every other failure still calls `_stop()`. Add the same keys, commented, to
   `config.example.yaml`.
3. `db/database.py`: create `events` (02 §3.3) and `native_devices` (02 §3.2) in the schema script; add
   `wanted.tmdb_id INTEGER` with the pattern at lines 152-157; reuse the existing `count_wanted()`
   (`db/database.py:404-409`) for `wanted_counts`; the 30-day `events` purge on insert.
4. `api/push.py`: inside `send_event()` (line 188) and `broadcast()` (line 225), write the `events` row with the
   rendered title and body before any delivery (`recipient_id` the user for `send_event`, `NULL` with `actor_id =
   exclude_user` for `broadcast`; `ref` from the tag as 03 §3.11 lists: `wanted:<id>`, `message:<id>`,
   `note:<id>`, `NULL` for `test` and `file_missing`). After web push, for each `native_devices` row of the
   recipient call `relay.send(...)`, passing as `actor` the `plex_user_id` that caused the event (the requester
   for `requested`, the poster for `message` and `announcement`, `None` for `arrived`, `file_missing` and
   `test`). Read the announcement text from `notifications.announcement` and honour
   `enabled`. Keep `OWNER_ONLY` and every other key unchanged.
5. Create `api/relay.py` with `active()`, `send(db, row, event, title, body, collapse_id, ref, srv, actor=None)`,
   `post(url, key, payload)` (the single `requests.post`) and `mark_inactive(reason)` exactly as 05-push-relay.md
   §8 specifies, including the outcome table (`sent`, `unregistered` deletes the row, `expired`/`401`/`403`
   marks inactive, `429` logs and increments `failures`, `502`/timeout retries once after 2 s), the five-minute
   skip after three consecutive connection failures, and the per-actor budget (09 §2.2, §6): relay sends caused
   by one `actor` are capped at 60 per 3600 s in an in-process dict; above it the `events` row is still written
   (step 4 wrote it first), the relay call is skipped and one INFO line is logged; `actor` `None` is not budgeted.
   `srv` is the Plex machine identifier, never an address.
6. `api/native.py`: add `POST /api/v1/push/devices` (03 §3.8; upsert on `device_id`; bound to the session's user;
   sixth row `409 device_limit` with `limit: 5`), `DELETE /api/v1/push/devices/<device_id>` (§3.9), `POST
   /api/v1/push/test` (§3.10, through `send_event(db, "test", ...)`; add the 5-per-user-per-hour limiter of
   09-security.md §13 GAP 1 here and on `POST /push/test`, answering `429` as 03 §1.7), `GET /api/v1/changes`
   (§3.11: `since`, `limit` clamped to 100, visibility rule, `cursor` = highest `events.id` in the table,
   `unread_messages` for the owner only, `wanted_counts`), `POST`/`DELETE /api/v1/owner/relay` (§3.12-§3.13),
   `GET /api/v1/owner/status` (§3.14, counts only). Make `GET /api/v1/server` report `push.delivery: "relay"` when
   `relay.active()` and `setup: true` with `server_name` and `plex_machine_id` `null` in setup mode; it reads
   `config.SETUP_MODE` at request time, never at import, so a test can switch it.
7. `api/routes.py`: `POST /request/wanted` accepts `tmdb_id` (03 §4.3 GAP) and stores it on the row; `GET
   /wanted/search` accepts `&tmdb_id=` (03 §4.17 GAP) likewise; `WantedRow` dicts include `tmdb_id`.
   `settle_wanted()` (line 625) sends `arrived` to `requested_by_id` through every device kind (web rows and
   `native_devices` rows) and falls back to the owner only when the requester has none; it records
   `last_run_at` and takes one lock shared with the scheduler. The webhook key check lives in `guard()` (lines
   1235-1273), not in `plex_webhook()`, because the guard runs before the route and would answer `403 that part
   is the owner's` to a keyed, sessionless request once the door is off: before the owner-session test, admit
   `POST /plex/webhook` when the configured `plex.webhook_key` is non-empty and
   `hmac.compare_digest(request.args.get("key", ""), config["plex"]["webhook_key"])` is true; otherwise, unless
   the loopback door admits it, the guard answers `403 {"error": "webhook key missing or wrong"}`. An empty
   configured key disables the key path (never compare against `""`); the keyless form is admitted only when the
   door is open and the peer is loopback (06 §8, §13 GAP). Do not put the check in `plex_webhook()`.
   `tests/test_webhook_key.py` covers: right key with the door off `200`; wrong, missing and empty key `403`;
   empty configured key with `?key=` `403`; keyless with the door open from loopback `200`. The loopback rule at
   lines 1263-1265 applies only when the effective `app.loopback_owner` is true (`PLEXGET_LOOPBACK_OWNER=0`
   overrides `true`) and the effective `flask.host` is `127.0.0.1`, `localhost` or `::1` (06 §7; 09 §7): with
   `flask.host: 0.0.0.0` and `loopback_owner: true` the door is off and `main.py` logs one warning at start,
   "loopback owner door disabled because flask.host is not loopback"; test
   `test_loopback_door_closed_when_bound_beyond_loopback` in `tests/test_webhook_key.py`.
8. `scanner/wanted_search.py`, `recheck()` (line 405): for each row, when `tmdb_id` is set match a Plex item whose
   `guids` contain `tmdb://<id>` (then `imdb://`, `tvdb://` when the row carries one) before the folded title and
   year; for `kind == "show"` rows search show sections and mark acquired only when the requested `season` is
   present (all seasons when `season` is `NULL`); a show hit's `matches` entry is shape 2c. `guids` is read as
   `plexapi/video.py:437` and `plexapi/media.py:937` expose it (verified by the ADR).
9. `api/plex_auth.py`: `X-Plex-Client-Identifier` comes from `DATA_DIR / "client_id"`; when the file is absent and
   the directory already holds `secret.key` or the SQLite file, write the legacy `uuid5(NAMESPACE_DNS,
   "plex-ops.local")` (`api/plex_auth.py:27`) into it; only a directory with neither gets a new `uuid4` (06 §11).
10. `main.py`: when `flask.debug` is false serve with `waitress.serve(app, host=..., port=..., threads=8)` (one
    process); keep `app.run()` for debug. Start one daemon thread when the effective `app.settle_every_minutes`
    is above zero that sleeps the interval then calls `settle_wanted()` under the lock, logging a
    `PlexUnavailable` and continuing; it does not start in setup mode (06 §8). `settle_wanted` is a closure
    inside `create_app()` (`api/routes.py:110`, 625) and cannot be imported by `main.py`: `create_app()` exposes
    the closure and its lock as `app.settle_wanted = settle_wanted` and `app.settle_lock = threading.Lock()`;
    the scheduler thread in `main.py` calls `app.settle_wanted(db)` under `app.settle_lock`, and the
    `/wanted/settle` and `/plex/webhook` routes take the same lock. Pin `waitress` in
    `requirements.txt`. The scheduler's `sleep` and clock are injectable for the test.
11. `tests/conftest.py`: monkeypatch `relay.post` (recording every call and answering from a script) and
    `web_push.send_to_user`; add a fixture that points `PLEXGET_DATA` at a fresh temporary directory per test.
    Write `tests/test_changes.py`, `test_push_devices.py`, `test_relay_client.py`, `test_recheck.py`,
    `test_settle.py`, `test_webhook_key.py`, `test_config_env.py`, `test_client_id.py` with the cases of
    07-build-phases.md §5 Phase 2 and the 09-security.md §11 names (`test_member_never_sees_owner_only_events`,
    `test_broadcast_excludes_poster`, `test_device_bound_to_session_user`, `test_sixth_device_is_409`,
    `test_handle_never_listed`, `test_push_test_limiter`, `test_relay_call_carries_no_plex_data`,
    `test_relay_actor_budget` (the 61st send caused by one actor in an hour is skipped, its `events` row is still
    written, another actor's send goes out), `test_relay_json_is_0600`, and from the deferred Phase 1 set
    `test_loopback_door_off_by_config`, `test_webhook_key` and
    `test_loopback_door_closed_when_bound_beyond_loopback` in `test_webhook_key.py`). `test_config_env.py` imports `config` in a subprocess with no
    `config.yaml` and no `PLEXGET_CONFIG` file and asserts `SETUP_MODE` is set and the exit code is zero;
    `test_settle.py` drives the scheduler on a fake clock with `settle_wanted` stubbed; the `waitress` branch is
    asserted on the branch taken, not on a socket.
12. Record the remaining fixtures of 03 §6.4 (`api.v1.server.setup.json`, `api.v1.push.*`, `api.v1.changes.*`,
    `api.v1.owner.*`, `notifications.json`, `wanted.recheck.json` with one film and one show hit) and
    `api.v1.push.devices.delete.json` (04 §12 GAP 2); re-record every Phase 1 file that gained `tmdb_id`.
    `config` is read once at import (`config.py:160`) and `tests/conftest.py` imports it with a valid file, so
    the pytest process is never in setup mode by itself: record `api.v1.server.setup.json` in a test that
    `monkeypatch.setattr(config, "SETUP_MODE", True)` (and `config.config["_setup"] = True`) for its duration;
    the route answers `setup: true` with `server_name` and `plex_machine_id` `null` without any Plex call. Run
    `pytest -q` twice; the diff must be clean after the second run. The fixture count is the 77 rows of 03 §6.4
    (its list already includes the three `204` files of 04 §12 GAP 2 and `api.v1.push.test.rate_limited.json`);
    state that count in the pull request.
13. Update 03-server-api.md (`tmdb_id` on §4.3 and §4.17 marked done; the `push/test` limiter in §1.7),
    06-install-and-setup.md §13 (webhook GAP closed) and `config.example.yaml` in the same pull request.
14. HUMAN (10-decision-record.md §6 item 3): restart an existing venv install on this branch, confirm a member's
    web sign-in still works and that `data/client_id` holds the legacy `uuid5` value, and record it. Wait.

## Gate

```
HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 venv/bin/pytest -q   # pass: "N passed"
venv/bin/pytest -q && git diff --exit-code -- tests/fixtures/api                  # pass: no diff
scripts/banned-words.sh tests/fixtures/api                                        # pass: exit 0
ls tests/fixtures/api | wc -l                          # pass: 77, the row count of 03-server-api.md §6.4
```

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you
cannot, write docs/build/deviations.md and stop.

## Deliver

One pull request titled `Phase 2: Server: events, devices, arrivals and install switches` with the seven headings
of 07-build-phases.md §1.2 in order. `## Gate output` carries the proxy-blocked `pytest -q` run, the empty
fixture diff, the fixture count with its arithmetic, and `grep -n "processes\|workers" main.py` (empty).
`## Changed` ticks every row of the Phase 2 deliverables table. Confirm the four definition-of-done facts: a
member's `GET /api/v1/changes` never returns another member's `message` event; `recheck()` marks a show row
acquired only when the season is present; `settle_wanted()` falls back to the owner only when the requester has
no device of any kind; a pre-existing `data/` keeps the `uuid5` identifier. `## Human steps` carries the step 14
result. Then stop and wait for the owner; do not start Phase 3 or Phase 8.

## Do not

- Do not change any notification title, body, catalogue key or `OWNER_ONLY`; only `announcement` becomes
  configurable, with today's words as its default.
- Do not deliver web push or relay push before the `events` row is written; the row is what makes polling and
  de-duplication possible.
- Do not put a Plex token, a username, a session token or a server URL in the relay payload, and do not return
  a relay handle in any response; `GET /api/v1/owner/status` returns counts.
- Do not let `POST /api/v1/push/devices` trust a `plex_user_id` in the body; the row belongs to the bearer.
- Do not open the loopback door on a server bound to `0.0.0.0`; the default stays `true` only so venv installs
  behave as today; the image and setup page (Phase 8) turn it off.
- Do not add the setup-mode guard, `api/setup.py`, the Dockerfile or the compose file; Phase 8 owns them.
- Do not let any test sleep for real or reach plex.tv, Plex, TMDb or the relay; the gate's proxy variables catch it.
- Do not overwrite `data/client_id` or mint a new identifier for a directory holding `secret.key` or the database.
