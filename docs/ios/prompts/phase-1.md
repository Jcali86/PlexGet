# Phase 1: Server: bearer sessions and sign-in exchange

Machine: Linux. Repository: this server repository (public, MIT). Phase 0 is merged: `tests/conftest.py`,
`pytest.ini` and `.github/workflows/tests.yml` exist and `venv/bin/pytest -q` passes.

## Read first

Read each file in full, in this order, before any other tool call.

1. `docs/ios/00-README.md` §3-§4 (how a phase runs; the six-line deviation note)
2. `docs/ios/10-decision-record.md` §2 (the server paragraph), §4 rows "Sign-in exchange", "Session lifetime",
   "Exchange rate limiter", §5 items 1-5, §6 items 1, 7 and 8
3. `docs/ios/07-build-phases.md` §1, §5 "Phase 1, Server: bearer sessions and sign-in exchange"
4. `docs/ios/03-server-api.md` §1 (conventions), §3.1-§3.7, §3.15, §6 (fixtures contract, all of it), §7 (the
   strip table), §8 (routes the app never calls), §9
5. `docs/ios/02-architecture.md` §2.1-§2.2 (the exchange, step by step), §2.16 and its GAP (the `401` codes), §3.1
6. `docs/ios/09-security.md` §3 (token rules and its GAP), §5 (the pin hand-off), §6 (the limiter table), §11
   (the server tests by name)
7. `docs/ios/04-ios-app.md` §10.3 (banned words), §12 GAP 2 (three `204` fixtures)
8. `api/routes.py` lines 75-135, 940-1060, 1150-1225, 1229-1273; `api/plex_auth.py` in full (the pin, identity,
   roster and session functions); `db/database.py` lines 100-175; `tests/conftest.py`; `CONTRIBUTING.md`

## Goal

Make every existing household and owner route usable with `Authorization: Bearer <token>`, add the open `GET
/api/v1/server`, the pin hand-off exchange `POST /api/v1/auth/plex` with its device-keyed limiter, the session
endpoints, the JSON error handler, `missing_files` stripping for members, and the fixtures writer, all with tests,
so that Phase 3 can decode a recorded response for every route the app will call. The cookie flow is untouched and
its own tests prove it (07-build-phases.md §5 Phase 1).

## Constraints

- Forbidden in this phase (07-build-phases.md §5 Phase 1): changing the body of any `/request/*` or owner route
  (the strip is a response hook); changing the cookie flow or `SESSION_DAYS` for `web` rows; any new dependency;
  anything listed under Phase 2 (`events`, `native_devices`, `api/relay.py`, `tmdb_id`, `recheck()`, `waitress`,
  setup mode, `PLEXGET_DATA`); UI code; a network call in a test; editing a fixture by hand.
- Every `/api/v1` failure is a non-`2xx` status with `error` and `code` (03-server-api.md §1.5).
- Never serialise a session token, its hash, `plex_token`, `plex_token_enc`, `relay_handle` or another person's
  row; `api/native.py` builds explicit dicts (03-server-api.md §7; 09-security.md §3).
- The plex.tv account token is discarded after `server_access_token()`; no column ever holds it (09 §3).
- `tests/fixtures/api/` is written only by `pytest` through `record`; fixture data uses public-domain titles
  only (03 §6.2). No word of 04-ios-app.md §10.3 in any fixture, test or commit message.

## Steps

1. `git checkout -b phase-1-server-bearer-sessions-and-sign-in-exchange main`. Run `venv/bin/pytest -q` and
   confirm the Phase 0 smoke test passes before changing anything.
2. Add `scripts/banned-words.sh` (the iOS repository's script: case-insensitive grep for the words of
   04-ios-app.md §10.3 over the paths given as arguments; exit 1 on a hit, no output on a pass; `chmod +x`) and
   the `banned words` step to `.github/workflows/tests.yml` exactly as 07-build-phases.md §4.1 shows it.
3. `api/plex_auth.py`, sessions: in `ensure_table()` add the columns `device_id TEXT UNIQUE`, `device_name TEXT`,
   `platform TEXT`, `app_version TEXT`, `kind TEXT NOT NULL DEFAULT 'web'`, `revoked_at TIMESTAMP` with the
   `PRAGMA table_info` then `ALTER TABLE ADD COLUMN` pattern of `api/plex_auth.py:315-319` (`ALTER TABLE` cannot
   add `UNIQUE`; create a unique index on `device_id` instead). `start_session()` stores `sha256(token)` hex (64
   characters) and returns the raw token; `session_user()` looks up the hash first and the raw value second.
   Validity: `web` rows keep 90 days from `created_at` (`SESSION_DAYS`, unchanged); `ios` rows are valid while
   `last_seen` is within 180 days; a row with `revoked_at` set is invalid. Compute expiry in Python from a
   module-level `now()` that tests monkeypatch, not from SQL `datetime('now')`, so the frozen-clock tests can
   run. The purge in `start_session()` applies each kind's rule. Make `session_user()` report why a lookup
   failed (the internal signature is yours) so the guard can put `code` in the `401` body.
4. `api/routes.py`, `current_user()` (lines 943-951): read `Authorization: Bearer <token>` first, the cookie
   second. Add `/api/v1/server` and `/api/v1/auth/plex` to the open set, give the household tier the `/api/v1/`
   prefix, and let `/api/v1/owner/` fall through to the owner tier. The guard's `401` body on `/api/v1` paths and
   on any request carrying a bearer becomes `{"error", "code", "auth_required": true}` with `code` one of
   `session_revoked`, `session_expired`, `plex_token_dead` (09-security.md §3 GAP; 02 §2.16); add those three
   codes to 03-server-api.md §1.4 in this pull request. Add the JSON handler of 03 §3.15 for unhandled exceptions
   on `/api/v1` paths and bearer requests (`500 {"error": "something went wrong on the server", "code":
   "internal"}`; the exception logged, never sent). Add an `after_request` hook that removes the key
   `missing_files` at every depth from any JSON response to a session whose role is not `owner` (03 §7 row 1).
5. Create `api/native.py`: `Blueprint("native", __name__, url_prefix="/api/v1")`, registered in `create_app()`.
   Implement `GET /api/v1/server` (03 §3.1; in this phase `push.delivery` is always `"polling"`, `setup` is
   `false`, `urls` reads `app.urls` with `{"local": [], "remote": []}` as the default, `native_signin` reads
   `app.native_signin` default `true`, `min_app_version` reads `app.min_app_version` default `"1.0.0"`;
   `Cache-Control: no-store`), `GET`/`DELETE /api/v1/auth/session` (§3.3-§3.4), `GET /api/v1/auth/sessions`
   (§3.5; `username` only for the owner; revoked rows omitted), `DELETE /api/v1/auth/sessions/<int:id>` (§3.6;
   `404 not_found` for another person's row unless the caller is the owner), `DELETE /api/v1/auth/sessions`
   (§3.7). Every timestamp in these bodies is ISO 8601 with `Z` (03 §1.6).
6. `POST /api/v1/auth/plex` (03 §3.2, in that order): validate `device` (`id` required, ≤64); the limiter keyed
   on `device.id` (12 per 600 s) and overall (40 per 600 s), in-process, without `pin_allowed()`'s caller-address
   rule; with `pin`, read `GET https://plex.tv/api/v2/pins/<id>` with `X-Plex-Client-Identifier: <client_id>`
   and the server's usual headers (`api/plex_auth.py:32-39`) through one new function, named `read_pin(pin_id,
   client_id)` here so later phases can monkeypatch it by that name; refuse a pin whose `expiresIn` is not
   positive with `401 pin_invalid` and one without `authToken` with `409 pin_not_linked`. Note:
   07-build-phases.md's test list names `pin_expired` and `pin_unclaimed` and 02 §2.1's GAP says 03 defines them;
   03 §3.2 defines `pin_invalid` and `pin_not_linked` instead, and 03 is the contract, so use 03's codes and cover
   both cases under them. If the pin JSON carries a creation time (unverified; 10-decision-record.md §5 item 2),
   also refuse a pin older than `PIN_LIFE`. With `plex_token`, accept it only when `app.testing` or
   `request.is_secure` is true, else `400 pin_required`. Then `plex_identity` (`401 plex_token_invalid`), the
   per-account limiter (6 per 3600 s), `authorise` against `allowed_users()` (`403 not_shared`),
   `server_access_token` (`403 invite_pending` on `None`), then `start_session()` with the device columns and
   `kind = "ios"`, upserting on `device_id`. Answer the §3.2 body; every `429` carries `code: "rate_limited"`,
   `rate_limited: true` and `retry_after`.
7. Extend `tests/conftest.py`: monkeypatch `plex_auth.plex_identity`, `plex_auth.server_access_token`,
   `plex_auth.allowed_users`, `plex_auth.read_pin` and the Plex search functions the household routes call
   (`api.routes` import site) with fixed public-domain data; add fixtures `shared_session` and `owner_session`
   that run the exchange and return the bearer; a `frozen_clock` fixture over the `now()` of step 3.
8. Write the test files of 07-build-phases.md §5 Phase 1: `tests/test_bearer.py`, `test_server_info.py`,
   `test_auth_plex.py`, `test_sessions.py`, `test_errors.py`, `test_missing_files.py`, `test_cookie_flow.py`,
   with the cases that table lists. Put the tests 09-security.md §11 names for `test_guard.py` into
   `test_bearer.py` and `test_errors.py`, those for `test_exchange.py` into `test_auth_plex.py`, and those for
   `test_strip.py` into `test_missing_files.py`, keeping every test function name verbatim (`test_sessions.py`
   keeps its own). Defer `test_loopback_door_off_by_config`, `test_webhook_key` and `test_push_test_limiter` to
   Phase 2 and say so in the pull request.
9. Record fixtures: every file of 03-server-api.md §6.4 that needs no Phase 2 column, plus the three `204` files
   of 04-ios-app.md §12 GAP 2 (`api.v1.push.devices.delete.json` waits for Phase 2; add
   `api.v1.auth.session.delete.json` and `api.v1.auth.sessions.delete.json` now, body `null`). Leave for Phase 2:
   `api.v1.server.setup.json`, `api.v1.push.*`, `api.v1.changes.*`, `api.v1.owner.*`, `notifications.json`,
   `wanted.recheck.json`. Record `request.wanted.*`, `wanted.*`, `wanted.search.json` now; Phase 2 re-records
   them when `tmdb_id` lands. Delete `tests/fixtures/api/.gitkeep`. Run `venv/bin/pytest -q` twice and confirm
   `git diff --exit-code -- tests/fixtures/api` is clean after the second run.
10. Update 03-server-api.md in this pull request for the `401` codes (step 4) and any shape the tests proved
    differs from the document; a difference the cited code cannot settle is a deviation note.
11. HUMAN (10-decision-record.md §6 item 1): with a real Plex account and the headers of 03-server-api.md §3.2,
    mint a strong pin, link it with `PUT https://plex.tv/api/v2/pins/link`, read `GET /api/v2/pins/{id}` twice,
    and record whether a strong pin links, whether a short pin links, and whether `authToken` is present on the
    second read. Wait for the result. If only short pins link, change nothing server-side (the server reads
    whatever pin it is handed) and record that Phase 3 sets `SignInMachine.handoffPinStrength = false`. If a
    claimed pin cannot be re-read at all, write the deviation note and stop.
12. HUMAN (10-decision-record.md §6 item 7): with the owner's token, print `account.users()`
    (`api/plex_auth.py:162-200`) for a managed Plex Home user and record whether that user appears with a server
    entry. Wait for the result and paste it under `## Human steps`.

## Gate

```
venv/bin/pytest -q                                                     # pass: "N passed", exit 0
venv/bin/pytest -q && git diff --exit-code -- tests/fixtures/api      # pass: no diff
scripts/banned-words.sh tests/fixtures/api                             # pass: no output, exit 0
grep -n "plex_token" api/native.py | grep -v "testing\|https"          # pass: no line that stores it
```

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you
cannot, write docs/build/deviations.md and stop.

## Deliver

One pull request titled `Phase 1: Server: bearer sessions and sign-in exchange` from the branch above, with the
seven headings of 07-build-phases.md §1.2 in order. `## Changed` ticks every row of the Phase 1 deliverables
table; `## Gate output` carries the full `pytest -q` output and the empty diff; `## Human steps` carries both
results from steps 11 and 12 verbatim; `## Forbidden` shows `git diff --stat main -- api/routes.py` and the greps
proving no `/request/*` body changed and no dependency was added. Confirm in the body that `sessions.token` for
new rows is a 64-character hex digest, that the seventh exchange for one account within an hour is `429` with
`retry_after`, and that no response to a member session contains `missing_files`. Then stop and wait for the
owner; do not start Phase 2.

## Do not

- Do not store, log or return the plex.tv account token anywhere; `test_account_token_never_stored` must find
  it in no table, in clear or encrypted.
- Do not apply `pin_allowed()`'s caller-address or gap rule to `POST /api/v1/auth/plex`; a household behind one
  tunnel shares one address.
- Do not change `SESSION_DAYS`, the cookie name, `/auth/start`, `/auth/poll`, `/auth/me` or `/auth/logout`;
  `test_cookie_flow.py` exists to prove they still work.
- Do not strip `missing_files` inside the route functions; it is one response hook, and owners keep the key.
- Do not invent an error code, field or column; the codes are those of 03-server-api.md §3.2 and §1.4 as amended
  in step 4, and the columns are those of 02-architecture.md §3.1.
- Do not write or edit a fixture by hand, and do not let a test open a socket; plex.tv and Plex are stubs.
- Do not start the `events` table, `native_devices`, `api/relay.py` or `tmdb_id` early because a fixture would
  be more complete; those are Phase 2's rows.
