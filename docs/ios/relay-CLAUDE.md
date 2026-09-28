# CLAUDE.md for `<AppName>-relay`

Purpose. This file is copied verbatim to the root of the push-relay repository as `CLAUDE.md` in Phase 0 and is
loaded into every Claude Code session there. It carries the standing rules for `push-relay`, the developer's only
cloud component: what it is built with, how it is deployed, what it holds and what it must never hold, how it is
tested, and what to do when Apple's documentation or the server disagrees with the plan. Each rule is one
imperative sentence followed by its reason. Names are the ADR's (10-decision-record.md §2, cloud paragraph)
verbatim; endpoint shapes, tables and quotas live in 05-push-relay.md and are referenced, not restated.

Read with: 05-push-relay.md (the complete specification: §2 stack and layout, §3 tables, §4 endpoints, §5
entitlement, §6 quotas, §7 APNs, §9 deployment, §12 tests), 09-security.md (§2.5 the compromised-relay row, §9
relay abuse, §11 the test names), 07-build-phases.md (Phase 0 skeleton, Phase 6 and Phase 7 gates),
08-app-store-and-release.md (the human steps: APNs key, server-notifications URL, Billing Grace Period),
10-decision-record.md (binding; §6 gates 4 and 5), and the current `docs/build/phase-N.md`.

## 1. Repository facts

- Keep the layout of 05-push-relay.md §2: `relay/__init__.py` (`create_app(config)`), `wsgi.py`, `config.py`,
  `db.py`, `apns.py`, `entitlements.py`, `quotas.py`, `routes.py`, `join.py`, `admin.py` (the one operator
  command); `scripts/request-test-notification.py`; `tests/` with one file per endpoint group plus
  `test_admin.py`; `Dockerfile`, `compose.yaml`, `fly.toml`, `pytest.ini`. The phase prompts name files by that map.
  `relay/wsgi.py` holds `app = create_app(Config.from_env())` and nothing else; `relay/__init__.py` exposes only
  the factory, so importing `relay` in tests validates no secrets (05 §9).
- Record every Apple fact the code depends on in `docs/verified.md` as (verified: <url>, <date>) before the code that
  uses it is written. 05 §5 and §7 are from memory until then (ADR §6 gates 4 and 5).
- Never add a route, table, column or field that 05-push-relay.md does not name. A missing one is a `GAP:` line
  there first; `GET /v1/server-keys/<id>` is the recorded example that stays unbuilt until a screen needs it (05 §4).

## 2. Toolchain and the exact commands

- Use Python 3.12, Flask, `waitress`, `httpx` with the `http2` extra, `PyJWT[crypto]`, Apple's App Store Server
  Library and the standard-library `sqlite3`, all pinned in `requirements.txt`; `pytest` 8.x in
  `requirements-dev.txt`. Nothing else (05-push-relay.md §2). Note: the server's own tests run on Python 3.11
  (07-build-phases.md §2); the relay is 3.12 per 05 §2 and the relay `ci.yml` of 07 §4; do not mix them.
- Set up with `python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-dev.txt`. CI does
  the same on `ubuntu-24.04` with `python-version: "3.12"` (07-build-phases.md §4).
- Run the gate exactly as: `venv/bin/pytest -v` (pass: "N passed", every 05 §12 case listed by name) and
  `grep -rn "logger\|logging" relay/ | grep -i "body\|title"` (pass: no output). Both are Phase 6's gate
  (07-build-phases.md §5, Phase 6).
- Never add a dependency beyond the list above. Each is supply-chain and review surface; a need for one is a
  deviation note, not a `pip install`.

## 3. Deployment: one container

- Ship one container from `python:3.12-slim`, non-root user `relay`, `VOLUME /data`, `HEALTHCHECK` on
  `GET /v1/health`, `CMD ["waitress-serve", "--listen=0.0.0.0:8080", "--threads=8", "relay.wsgi:app"]`. One process
  keeps the quota counters and the provider-token cache coherent (05-push-relay.md §9; ADR §2).
- Never run a second worker process, a `gunicorn` worker pool or a second replica against one database. The
  fixed-window counters in SQLite are correct only under one writer (05 §3.4).
- Listen on plain HTTP 8080 inside the container only; TLS is terminated in front by Fly.io or the `caddy`
  service in `compose.yaml`, and `RELAY_TRUST_PROXY=1` is set so the client IP is the last (rightmost) `X-Forwarded-For`
  entry, the one the single trusted hop appended; the first entry is client-supplied and never used, since a
  per-IP quota keyed on it is bypassed by one header (05 §4, §9; the server's `caller()` in `api/routes.py` does
  the same).
- Take every setting from the environment variables of 05-push-relay.md §9, validated in `relay/config.py` at
  start. A missing secret fails start-up loudly rather than at the first push.

## 4. Secrets

- Read the APNs key only from `APNS_KEY_P8` (PEM contents) or `APNS_KEY_PATH` (a mounted secret file), with
  `APNS_KEY_ID` and `APNS_TEAM_ID` beside it. The key authorises pushes to every install; it is the one thing on the
  relay worth stealing (constraints brief fact 2; 05 §9).
- Never commit a `.p8`, a `.env`, `RELAY_QUOTA_SALT` or any key material; keep `*.p8` and `.env` in `.gitignore` and
  check `git ls-files | grep -E '\.p8$|\.env'` is empty before every push. A leaked key means a portal revocation
  and redeploy (09-security.md §12).
- Store `device_secret` and `server_key` only as `sha256` hex; return the clear value once and never again. A
  database leak must yield nothing usable (05 §3).
- Make `handle` and `device_secret` two distinct random values. The handle is what the server stores; the secret is
  presented by the phone alone (ADR §5 items 6 and 13).

## 5. Data the relay never holds

- Never accept, store, forward or log a Plex token, a bearer session token, a PlexGet server address or URL, a
  username, an email address or a plex.tv account id as a named field in any request, table, log line or error
  message. `title` and `body` are opaque text that the relay never inspects, stores or logs; a username inside
  them (the server's `"Message from {who}"`) is the server's business (05 §1). The relay's value to an attacker
  must stay "APNs tokens under random handles" (ADR §1; 09-security.md §2.5).
- Reject any `POST /v1/push` field outside `handle`, `event`, `title`, `body`, `collapse_id`, `data.ref`, `data.srv`
  with `400 bad_request`; `srv` is the public Plex machine identifier, never an address (05 §4.5).
- Never write a notification `title` or `body` to the database, a log, a metric label or an exception message.
  They transit in the request and the APNs payload and nowhere else (ADR §2 "Bodies are never logged").
- Never store a raw IP address; quota subjects are `sha256(ip + RELAY_QUOTA_SALT)` (05 §3.4).

## 6. Logging

- Log one JSON line per request to stdout with method, path, status, `server_keys.id`, the first 6 characters of
  the handle and duration; never a body, a full handle, a device secret, a server key, a JWS or a raw IP
  (05 §4.5, §11).
- Log a verification failure with the first 32 characters of the payload only. Enough to correlate, not enough to
  replay (05 §4.6).

## 7. Quotas

- Enforce, as fixed windows in `quota_counters`: 10 `POST /v1/devices` per IP per clock hour; 60 `POST /v1/push` per
  key per clock minute; 1,000 per key per UTC day; 100 per handle per UTC day; 30 `POST`/`DELETE /v1/server-keys`
  per IP per hour under subject prefix `sk:` (ADR §2; 05 §6).
- Increment a counter only on `sent`, `unregistered` and `apns_unavailable`; never on `expired` or `bad_key`. A lapsed
  key must not be able to spend a quota (05 §4.5).
- Keep counters in SQLite, never in memory. A restart must not reset a window (05 §3.4).

## 8. Apple's App Store Server Library and APNs

- Read the App Store Server Library's README (PyPI name, module, verifier class, constructor, which root
  certificates it wants) before writing `relay/entitlements.py`, and record the result in `docs/verified.md`. The
  names in 05 §5 are from memory and the human confirms the package name (ADR §6 gate 4).
- Read Apple's "Sending notification requests to APNs" and "Establishing a token-based connection to APNs" before
  writing `relay/apns.py`; correct 05 §7 in the same pull request. Header names, the `410` and bad-token `400`
  semantics and the two hosts are from memory (ADR §6 gate 5).
- Stop with a deviation note if either page cannot be read or disagrees with 05 §5 or §7. A guess here is a silent
  loss of push for every household (07-build-phases.md §5, Phase 6, definition of done item 1).
- Never hand-roll JWS chain verification or notification decoding. `SignedDataVerifier` is the only verifier;
  the Phase 7 gate proves a forged JWS is refused through it (ADR §4 "Relay language"; 05 §12).
- Honour a key while `status = 'active'` and `now <= expires_at + 16 days`; the human sets Billing Grace Period
  to 16 days so the two agree. Never describe 16 as Apple's maximum (ADR §5 item 20).

## 9. Testing: the fake-APNs pattern

- Build `tests/conftest.py` with `create_app` on `RELAY_DB=:memory:` (one shared connection), a `FakeAPNs`
  implementing the `APNsClient` protocol (`send(environment, token, headers, payload) -> APNsResult(status, reason,
  apns_id)`) that records every call and answers from a script, a `FakeVerifier` that decodes a test JWS without
  signature checks, and a frozen clock (05 §12).
- Install a `socket.socket` monkeypatch in `conftest.py` that raises, so any test opening a real socket fails.
  No test may reach APNs, Apple or the network (05 §12; 09-security.md §11).
- Name every test exactly as 05-push-relay.md §12 and 09-security.md §11 list them; the gate lists them by name.
- Keep exactly one real-verifier test: a JWS signed with a locally generated EC key run through the real
  `SignedDataVerifier` with Apple's roots is `401 invalid_jws`. It is the Phase 7 gate (ADR §7).
- Never skip, mark `xfail`, or delete a test to pass a gate. A failing test is a deviation note.

## 10. Process

- Work one phase per session, reading `docs/build/phase-N.md` first; the relay is built in Phase 6 from the
  Phase 0 skeleton, with the forged-JWS test possibly landing in Phase 7 (07-build-phases.md §5).
- Branch `phase-N-<slug>`, title `Phase N: <name>`, and use the pull-request headings of 07-build-phases.md §1.2.
  Phase 6 has one branch in this repository and one in `<AppName>-ios`.
- Paste every gate command and its complete output verbatim; never claim a gate passed without it.
- Keep each commit about one thing with an imperative first line under 72 characters naming the phase and a body
  that says why, in plain British prose, never "we" (`CONTRIBUTING.md:64-82` in the server repository).
- Write the six-line deviation note of 00-README.md §4 in `docs/build/deviations.md` and stop when reality
  contradicts the plan; never work around it.

## 11. When the plan and the world disagree

- When Apple's documentation disagrees with 05 §5 or §7, follow Apple, correct the document in the same pull request
  with a (verified: url, date) mark, and stop if the change alters an endpoint's answer to the server or the app.
- When the server's `api/relay.py` disagrees with 05 §4.5 or §8 (a status word, a field, an outcome), fix the server
  in its own repository with a pytest test under `tests/` and update 05; never bend the relay to an undocumented
  client (ADR §6 item 8).
