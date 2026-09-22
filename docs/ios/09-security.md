# 09 Security: the threat model and the rules that follow from it

Purpose. This document names what is worth protecting, who might attack it, what the decided design already does about each threat, and what the builder must therefore implement, test and never do. It is the one place the token rules, the transport rules, the bounds of the pin hand-off, every rate limiter in the server, the relay and the app, the loopback door, the member strip list, the relay abuse controls, the privacy table, the security tests and the incident playbook are stated together. Names are the ADR's verbatim (`10-decision-record.md`); shapes come from `03-server-api.md` and `05-push-relay.md` and are not restated. A control that the ADR does not define is marked `GAP:` for the builder to add with tests. Apple, Plex and Tailscale facts not in the ADR or the constraints brief are marked (verified: how) or (unverified).

Read with: `02-architecture.md` (§2.1 sign-in, §2.3 probe and confirmation, §2.16 sign-out and revocation, §4.1 the Keychain table, §6 the error taxonomy), `03-server-api.md` (§1.1 base URL set, §1.2 headers, §1.7 limiters, §3.2 the exchange, §7 the strip table, §8 routes never called), `05-push-relay.md` (§3 tables, §6 quotas, §9 deployment, §11 monitoring, §12 tests), `06-install-and-setup.md` (§5.3 setup-page binding, §7 the door, §8 the webhook key), `07-build-phases.md` (which phase carries each test), `08-app-store-and-release.md` (§4 the privacy label), `10-decision-record.md` (§4 "Sign-in exchange", "Session lifetime", "Exchange rate limiter", "LAN probe and trust", "Bind address"; §5 items 1-8, 19, 24; §6 gates 1, 2, 6, 10).

## 1. Assets

| Asset | Where it lives | Who may hold it | What its loss means |
|---|---|---|---|
| plex.tv account token (the app's) | Keychain `plex.account-token` (02 §4.1); in transit to plex.tv only; read once by the server during the exchange and discarded (ADR §4 "Sign-in exchange") | the phone | full control of that Plex account until the device is signed out at plex.tv |
| per-server Plex token (the person's) | `sessions.plex_token_enc`, Fernet under `data/secret.key` (`api/plex_auth.py:323-340`, `api/secrets_store.py:18-47`) | the server | that person's playlists and library reads on this one Plex server |
| owner's Plex admin token `plex.token` | `config.yaml` (deployment-and-security.md §6) | the server | admin of the Plex server (SECURITY.md:88-92) |
| bearer session token | Keychain `session.<machine_id>`; `sessions.token` as `sha256(token)` for new rows (ADR §2) | the phone; the server holds the hash | acting as that person on that server until revoked or 180 days idle |
| server address set (`urls.local`, `urls.remote`) | `UserDefaults` (02 §4.2); `config.yaml` `app.urls`; `GET /api/v1/server` (open) | not secret: the open route serves it to anyone who can reach the port | none beyond what reaching the port already reveals |
| household identity (roster, usernames, plex.tv ids, `email`, `thumb`) | plex.tv; `sessions`, `wanted.requested_by`, `messages.posted_by` | the server; a member sees usernames only (§8) | privacy of the household |
| APNs device token and relay handle, device secret | relay `devices` (05 §3.1); Keychain `relay.handle`, `relay.device-secret`; `native_devices.relay_handle` on the server | the phone, the relay; the server holds the handle only | unwanted pushes to one phone up to the handle quota |
| server key | relay `server_keys.key_hash`; `data/relay.json` 0600 on the server | the owner's server; the relay holds the hash | pushes to that household's phones up to the key quota |
| the relay's APNs `.p8` key, key id, team id | the relay's secret store (05 §9); never in a repository or image | the developer | pushes to every install of the app (brief fact 2) |
| `data/secret.key`, `data/vapid.json`, `plex.webhook_key`, `tmdb.api_key`, `ai.api_key` | `data/` and `config.yaml` on the server | the server | as SECURITY.md:70-119 |
| the pin pair `{pin id, client_id}` during the exchange | in transit from phone to server; `auth_pins` is not used for it | transient | redeemable at plex.tv for the pin's life (§5) |

## 2. Actors and threats

Each row states what the design does, with its source, and what the builder implements. "Design" means the ADR and the sibling documents; nothing here adds a mechanism the ADR rejected.

### 2.1 Stolen unlocked phone

Design: every secret is in the Keychain under `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` with no iCloud sync and no access group (brief fact 15; 02 §4.1), so a device backup restored elsewhere carries none of it (unverified: Apple's documented meaning of the `ThisDeviceOnly` classes). The person revokes the device from any other signed-in device through `GET /api/v1/auth/sessions` and `DELETE /api/v1/auth/sessions/<id>`, or everything at once with `DELETE /api/v1/auth/sessions`; the owner may revoke any row (ADR §2; 02 §2.16). Signing the device out at plex.tv kills the account token and, through the roster and `expired_session()` path, the per-server token (`api/routes.py:1168-1184`; auth-and-tiers.md §2.4). A revoked session answers `401` with `code: "session_revoked"` and the app shows Welcome instead of re-exchanging (02 §2.16; §3 below). Not mitigated in v1: an app-level lock (Face ID before opening) is not in the ADR and is not built; the phone's own passcode is the boundary.

Builder: the `Keychain` wrapper sets the accessibility attribute on every write and never sets `kSecAttrSynchronizable`; `Sessions` in Settings and the Console both show `device_name`, `platform`, `last_seen` so a person can recognise a device they no longer hold; the revoked device's next request clears its Keychain session item (02 §6.2).

### 2.2 Malicious household member

Design: a member holds a `shared` session and can do only what the web page allows: 40 wanted adds and 10 messages an hour (§6), read the household wanted list, fetch artwork through the owner-token proxy bounded to `^/library/metadata/\d+/(thumb|art|poster|composite)/` (`api/routes.py:2081-2086`). Owner routes answer `403` (`api/routes.py:1261-1273`); the `for_user` branch of `POST /request/playlist` refuses anyone but the owner (`api/routes.py:1594-1598`). New with the app: `missing_files` and every other row of the strip table (03 §7) never reach a member; a `native_devices` row is bound to the session's user id, never to a body field (03 §3.8), so a member cannot register a device for anyone else; owner-only event kinds cannot reach a member through `GET /api/v1/changes` (`OWNER_ONLY`, `api/push.py:180`; 03 §3.11); a member cannot push to anyone, because only the server holds the server key. Removal is the owner withdrawing the Plex share, effective within 600 s (`api/plex_auth.py:355-369`; auth-and-tiers.md §2.3).

Builder: the strip table tests of §11; the device-binding test; the changes-visibility test. GAP: `POST /api/v1/push/test` (03 §3.10) and the web `POST /push/test` (`api/routes.py:919-930`) have no limiter, and each test push spends the owner's relay quota (`key_day` 1,000; `handle_day` 100), so a member could exhaust the household's daily push budget; the builder adds a limiter of 5 per user per hour on both routes, keyed on `plex_user_id`, in-process like `message_allowed()` (`api/routes.py:1029-1040`), answering `429 {"error", "code": "rate_limited", "rate_limited": true}`, with tests.

### 2.3 Network observer on the LAN

Design: at home the app may talk plain HTTP to a private address (brief fact 10; 03 §1.1), which is what the web page does today. What such an observer can capture: the bearer session token on every request, and during the exchange the pin pair, which is redeemable at plex.tv for the pin's life (ADR §5 item 1). The hand-off replaces the account-token body precisely so that the captured thing expires with the pin instead of living until the device is removed at plex.tv (ADR §5 items 1 and 3). The app prefers a server's HTTPS URL even at home when one answers (03 §1.1), never sends any token over plain HTTP to a non-private address, and asks for confirmation before the first exchange with a server it did not reach through an invite (ADR §4 "LAN probe and trust"; 02 §2.3 step 5). Request signing is not attempted: it cannot help against a party who reads the token (design-A §8).

Builder: `isPrivate(host:)` is the single decision point (04 §2), the preference for `https://` is in the `ServerClient` ranking, and the privacy copy on the Welcome and confirmation screens says that a home-network sign-in is visible to the home network for the life of the code (ADR §5 item 1 "the privacy copy says so").

### 2.4 Internet scanner finding the exposed port

Design: Funnel or a proxy makes the server public. What is reachable without a session is the open tier (ADR §5 item 19): `/`, `/request`, `/health`, `/manifest.webmanifest`, `/persona`, `/themes`, `/sw.js`, `/auth/*`, `/icons/*`, `/themes/art/*` (`api/routes.py:1229-1231`) plus `GET /api/v1/server` and `POST /api/v1/auth/plex`. `GET /api/v1/server` discloses `server_name`, `plex_machine_id` (public, auth-and-tiers.md §1.5), `app_name`, `urls` (including the LAN address), `features` and flags; nothing in it is a credential. Everything else answers `401` or `403` (`api/routes.py:1235-1273`). Session tokens carry 256 bits of entropy (`secrets.token_urlsafe(32)`, `api/plex_auth.py:334`) and are stored hashed, so a copied database yields no live bearer for new rows (ADR §2). The exchange has its own limiter (§6) and the loopback door is off in the image (§7). `flask.debug` is never true on a published server: it is the Werkzeug debugger, a remote shell (SECURITY.md:56-60), and `main.py` serves through `waitress` only when it is false (ADR §2). `/auth/start` still mints plex.tv pins for anyone on the internet under the caller-address limiter (`api/routes.py:965-1002`); a scanner can spend the 40-per-600 s overall budget and delay web sign-ins, which is today's behaviour and is left as is.

Builder: the guard tests in §11 (anonymous requests to one household route, one owner route and each `/api/v1` route); the JSON error handler so a crash on `/api/v1` never returns Flask's HTML trace (03 §3.15); the setup page never writes `flask.debug`; the `Dockerfile` never sets it.

### 2.5 Compromised relay

Design: the relay holds the `.p8` key, APNs tokens under random handles, secret hashes, key hashes, transaction ids and quota counters, and sees notification titles and bodies in transit (05 §1, §3). It never sees a Plex token, a session token or a server address; `srv` is the public machine identifier (ADR §1; 05 §1). So a full compromise lets the attacker push arbitrary text to every registered install, which is the inherent blast radius of the APNs key (brief fact 2), and learn which transaction ids exist; it reaches no server, no Plex account and no household data. Bodies are never stored or logged (ADR §2). The Notification Service Extension that would keep even the text off the relay is v2 (ADR §4).

Builder: no new field is ever added to `POST /v1/push` or the APNs payload without a change to 05 and this document; the relay's log line never contains a body (05 §4.5; test `test_push.py` "log capture contains no title or body text"); the `.p8` is read from `APNS_KEY_P8` or `APNS_KEY_PATH` and never from the repository (05 §9). The incident step is §12.

### 2.6 Malicious server operator against household phones

Design: a household member trusts the owner's server with exactly what the web page trusts it with: the account token during the exchange (the server reads the pin, then discards the token, but a hostile server could keep it) and the per-server token afterwards (ADR §5 item 3; auth-and-tiers.md §1.5). The app narrows the window in three ways: it sends the pin only to a URL that answered `GET /api/v1/server` with the expected `plex_machine_id` (02 §2.3 step 4), it asks for confirmation naming `server_name`, `app_name` and the address before the first exchange with a server not reached through an invite (02 §2.3 step 5), and the person can end the server's hold on the account by signing the app's device out at plex.tv. A server can push any title and body to devices registered with it, up to the quotas; the app renders push text as text, routes only by `ref` (`wanted:<id>`, `message:<id>`, `note:<id>`; design-A §5.2), and never opens a URL from a push or an event. The server cannot reach devices registered with another server, because it holds only the handles its own members posted.

Builder: no `URL(string:)` from a push payload, an event or a server response is ever opened (CI grep for `openURL` outside `Report` and `Paywall`); `ref` is parsed with the regular expression in 05 §4.5 and anything else is ignored; the confirmation screen is not skippable for an un-invited server.

### 2.7 App Review

Design: the reviewer uses demo mode, which makes no network call and no Keychain write (design-A §3.5; 02 §1 `Demo`), and the demo server with a reviewer Plex account whose credentials are in the review notes (ADR §2 "Demo environment"). Guidelines 5.2.3 and 5.2.1 are gate 10 (ADR §6): the banned-word grep, no Plex mark in name or icon, and the routes in 03 §8 never called.

Builder: the review notes carry the reviewer account and the demo server's address and nothing else; no production token, key or owner address appears in notes, screenshots or fixtures; `scripts/banned-words.sh` runs in both CI workflows (04 §10.3).

## 3. Token rules

| Rule | Where enforced | Source |
|---|---|---|
| Every secret in §1 that the phone holds lives in the Keychain under `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, service `AppIdentity.keychainService`, no `kSecAttrSynchronizable`, no access group | `App/Keychain` (`SecretStore` implementation); `KeychainTests` | brief fact 15; 02 §4.1 |
| Nothing secret in `UserDefaults`, files, caches, `NSUserActivity`, pasteboard or logs; the `/api/v1/changes` cursor and URL sets are the only per-server state outside the Keychain | review; 02 §4.2 lists every `UserDefaults` key | 02 §4.2 |
| `os.Logger` only, never `print`; every interpolation that is not a route name is `privacy: .private`; tokens, pins, handles, secrets and bodies are never interpolated at all, even privately | CI grep for `print(`; review | 04 §10.2; 02 §6.2 |
| The Kit never logs; it has no `os` import (it builds on Linux) | `Package.swift`; CI | ADR §2 |
| Server side, new session rows store `sha256(token)`; lookup hashes the presented bearer and falls back to the raw value only for rows made before Phase 1; those rows expire on their own 90-day clock | `api/plex_auth.py` `start_session()`/`session_user()` edits | ADR §2 |
| `ios` rows are valid while `last_seen` is within 180 days (sliding); `web` rows keep 90 days from `created_at` | `session_user()` | ADR §4 "Session lifetime" |
| One row per `device.id` (upsert), so re-exchange never accumulates rows | `POST /api/v1/auth/plex` | 03 §3.2 |
| Revocation is per device (`revoked_at`), own rows for anyone, any row for the owner; `DELETE /api/v1/auth/sessions` is sign-out everywhere for the caller | 03 §3.6, §3.7 | ADR §2 |
| A `401` carries `code`: `session_revoked` (no re-exchange, show Welcome), `session_expired` or `plex_token_dead` (silent re-exchange at most once per five minutes per device) | error mapper; `signIn.lastSilentExchangeAt` | ADR §5 item 5; 02 §2.16, §6.2 |
| The plex.tv account token is never stored on the server and never sent to the relay; the per-server token is encrypted at rest as today | `POST /api/v1/auth/plex` discards it after `server_access_token()` | ADR §4 "Sign-in exchange"; auth-and-tiers.md §3.4 |
| Session tokens, hashes, `plex_token`, `plex_token_enc`, relay handles and server keys are never serialised into any response | explicit dicts in `api/native.py` | 03 §7 |
| The web cookie flow is untouched: the app never sends `Cookie` and never calls `/auth/*` | `Endpoints.swift` | 03 §1.2, §8 |

GAP: 02 §2.16 says 03 defines the `401` codes `session_revoked`, `session_expired` and `plex_token_dead`; 03 §1.4 and §3.15 do not list them. Phase 1 adds them to the guard's `401` body on `/api/v1` and bearer requests (`{"error", "code", "auth_required": true}`) and to 03, with a test per code; the app's rule above depends on them.

## 4. Transport rules

| Rule | Value | Source |
|---|---|---|
| Remote traffic | `https://` only; the app never sends a request to a non-private host over `http://` | brief fact 10; ADR §4 "LAN probe and trust" |
| Plain `http://` permitted to | literal addresses in `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `127/8`, `::1`, `fc00::/7`, `fe80::/10` (04 §2 `isPrivate(host:)`) and, per 03 §1.1, `.local` hostnames; the builder adds the `.local` suffix to `isPrivate(host:)` with a test so the two documents agree, because mDNS names resolve only on the link (unverified: Apple's ATS treats `.local` names as local networking under `NSAllowsLocalNetworking`) | 03 §1.1; 04 §2 |
| Preference | `https://` over `http://` whenever both answer, at home too | 03 §1.1 |
| ATS keys, exactly | `NSAppTransportSecurity` = `{ NSAllowsLocalNetworking: true }` and nothing else: no `NSAllowsArbitraryLoads`, no `NSExceptionDomains`, no `NSAllowsArbitraryLoadsInWebContent` (unverified: key names other than `NSAllowsLocalNetworking` are from memory of Apple's ATS reference; the brief names only that one) | brief fact 10; 04 §1 |
| Certificate validation | the system default; no `URLSessionDelegate` that answers a server-trust challenge; CI greps `App/` and `PlexGetKit/` for `serverTrust` and `URLCredential(trust` and fails on any hit | this document |
| Certificate pinning | none in v1. The server's certificate is chosen and rotated by the owner's path (Funnel, Caddy, nginx, Cloudflare; 06 §6), the app has no channel to learn a new pin, and a pin that breaks on rotation locks a household out with no remedy but an app update. The threat pinning addresses, a mis-issued CA certificate, is out of scope for a household tool. The relay's certificate is not pinned for the same reason | this document |
| `plex_token` in the exchange body | accepted only when `app.testing` is true or `request.is_secure` is true, and sent by the app only in the gate-1 fallback to a typed `https://` address. Behind a tunnel that does not forward `X-Forwarded-Proto`, `is_secure` is false and the body is refused even though the outer hop was TLS: that fails closed and is accepted; gate 2 records the header result | 03 §3.2; ADR §6 items 1 and 2 |
| Web cookie `Secure` flag behind Funnel | depends on `X-Forwarded-Proto` through `ProxyFix(x_proto=1)` (`api/routes.py:119, 1152`); gate 2 decides it; the app is unaffected because it never uses the cookie | ADR §5 item 8, §6 item 2 |
| Requests to plex.tv | `https://plex.tv/api/v2` only, with `Accept: application/json` and the `X-Plex-*` set; never to a PlexGet server | 03 §1.2; ADR §5 item 22 |
| The relay | `https://push.<developer-domain>` only; the app posts the APNs token and receives `{handle, device_secret}`; the server posts pushes with `Authorization: Bearer <server_key>` over TLS with a 5 s timeout | 05 §4; 02 §1 |

Note: `ProxyFix` defaults `x_for=1` (ADR §5 item 24, verified there against `werkzeug/middleware/proxy_fix.py:92`), so auth-and-tiers.md §0's statement that `remote_addr` is always the TCP peer is wrong behind one proxy; the door reads the raw header and is unaffected. Follow the ADR.

## 5. The pin hand-off and its bounds

The exchange is ADR §4 "Sign-in exchange": the app mints a pin under its own `X-Plex-Client-Identifier`, links it to the signed-in account with `PUT https://plex.tv/api/v2/pins/link` (`code`, `X-Plex-Token`; verified by the ADR against `plexapi/myplex.py:122, 1144-1155`), and sends `{"pin": {"id", "client_id"}, "device": {...}}` to `POST /api/v1/auth/plex`. The server reads `GET https://plex.tv/api/v2/pins/<id>` with that client identifier as `check_pin()` does today (`api/plex_auth.py:140-145`), runs `plex_identity`, `authorise`, `server_access_token` unchanged (`api/plex_auth.py:148-245`) and discards the account token.

What it bounds, and what it does not (ADR §5 items 1 and 2):

| Claim | True or false | Why |
|---|---|---|
| A LAN observer of the exchange learns the account token | true, for the pin's life | `{pin id, client_id}` is redeemable at plex.tv until the pin expires; plex.tv reports `expiresIn`, default 900 s in today's code (`api/plex_auth.py:77`; unverified for v2 pins beyond that) |
| The exposure ends when the pin expires | true | after expiry the pair is worthless; the web flow's alternative, an account token in the body, would live until the device is removed at plex.tv (ADR §5 item 3) |
| The server bounds on the pin's claim time | false | it bounds on creation age only, refusing a pin whose `expiresIn` is not positive or which has no `authToken` (03 §3.2); a claimed pin cannot be aged by the server (ADR §5 item 2) |
| The relay is kept out of the token path | not applicable | the relay carries no API traffic (ADR §5 item 1) |
| The pair can be replayed against the server | for the pin's life, yes, producing a second session for the same account and device id, which the upsert on `device.id` replaces | the limiter (§6) caps replays at 12 per `device.id` per 600 s and 6 per account per hour |

Gate 1 (ADR §6 item 1) decides two unknowns before Phase 1 is done: whether `pins/link` accepts a strong pin's code (if not, a short pin is linked within milliseconds of minting and the four-character code is not a practical target), and whether a linked pin's `authToken` can be read twice. If it can be read only once, the observer who wins the race gets the token and the server gets nothing, which the person sees as a failed sign-in; if it can be read more than once, both do. Neither outcome changes the rules above. If a claimed pin cannot be re-read at all, the fallback is the `plex_token` body under §4, and the builder stops with a deviation note rather than improvising.

## 6. Rate limits

Every limiter in the three components. "In-process" means a dict that resets on restart and is coherent only because `waitress` runs one process (ADR §2).

| Component | What | Key | Numbers | Store | Answer | Source |
|---|---|---|---|---|---|---|
| server | `POST /api/v1/auth/plex` | `device.id` | 12 per 600 s | in-process | `429 {"error", "code": "rate_limited", "rate_limited": true, "retry_after"}` | ADR §4 "Exchange rate limiter"; 03 §1.7 |
| server | `POST /api/v1/auth/plex` | overall | 40 per 600 s | in-process | same | same |
| server | `POST /api/v1/auth/plex` | Plex account id, once identity is known | 6 per 3600 s | in-process | same | same |
| server | `POST /api/v1/auth/plex` | caller address | not applied: the `PIN_PER_CALLER` and `PIN_GAP` rules of `pin_allowed()` are skipped for this route | | | ADR §5 item 5 |
| server | `POST /auth/start` (web, unchanged) | rightmost `X-Forwarded-For` or `remote_addr` | 12 per 600 s, 2.0 s gap, 40 overall per 600 s | in-process `_pin_attempts` | `429 {"error", "retry_after"}` | `api/routes.py:965-1002` |
| server | `GET /auth/poll/<id>` (web, unchanged) | pin id | 900 polls per pin, `PIN_LIFE` 1800 s | `auth_pins` | `{"status": "waiting"}` | `api/routes.py:1043`; `api/plex_auth.py:89-137` |
| server | `POST /request/wanted` | `plex_user_id` | `app.limits.wanted_per_hour`, default 40 | in-process `_wanted_adds` | `429 {"error", "rate_limited": true}` | `api/routes.py:1013-1024`; `api/limits.py:50` |
| server | `POST /request/message` | `plex_user_id` | `MESSAGES_PER_HOUR` 10, not configurable; body ≤ 500, context ≤ 200 | in-process `_messages_sent` | `429 {"error", "rate_limited": true}` | `api/routes.py:1029-1040, 1876-1882` |
| server | model calls (`/request/search` mood, `/request/playlist/auto`, `/request/nudges`) | `plex_user_id` and server | `per_hour` 30, `per_day` 100, `server_per_day` 400, `max_query_chars` 200 | `ai_usage` table | `429 {"error", "rate_limited": true, "hint"}`; nudges `200` with `rate_limited` | `api/limits.py:31-86`; 03 §1.7 |
| server | `POST /api/v1/push/devices` | `plex_user_id` | 5 rows per user | `native_devices` | `409 {"code": "device_limit", "limit": 5}` | ADR §2; 03 §3.8 |
| server | `POST /api/v1/push/test`, `POST /push/test` | `plex_user_id` | GAP: 5 per 3600 s (§2.2) | in-process | `429` as above | this document |
| server | `GET /api/v1/changes` | `limit` | ≤ 100 events per call; no rate limit | | `400 bad_request` above 100 | 03 §3.11 |
| server | `POST /plex/webhook?key=` | none | constant-time compare of `plex.webhook_key`; `403 {"error": "webhook key missing or wrong"}` | `config.yaml` | | 06 §8 |
| server | setup draft | process | one draft, bound to the first account, 30 minutes from last change; a second browser gets `409` | in-process | | 06 §5.3 |
| relay | `POST /v1/devices` | salted IP hash (`RELAY_QUOTA_SALT`) | 10 per clock hour (`ip_register_hour`) | `quota_counters` | `429 {"error": "rate_limited", "retry_after"}` | ADR §2; 05 §6 |
| relay | `POST`/`DELETE /v1/server-keys` | salted IP hash, prefix `sk:` | 30 per clock hour | `quota_counters` | `429` | 05 §6 |
| relay | `POST /v1/push` | `server_keys.id` | 60 per clock minute (`key_minute`); 1,000 per UTC day (`key_day`) | `quota_counters` | `429 {"status": "rate_limited", "scope", "retry_after"}` | ADR §2; 05 §6 |
| relay | `POST /v1/push` | handle | 100 per UTC day (`handle_day`) | `quota_counters` | same, `scope: "handle_day"` | same |
| relay | any body | | 8 KB; title ≤ 100, body ≤ 500, `collapse_id` ≤ 64 bytes, `ref` ≤ 64 matching `^(wanted\|message\|note):[0-9]+$` | | `413 too_large`; `400 bad_request` | 05 §4 |
| relay | counters | | incremented only on `sent`, `unregistered`, `apns_unavailable`; never on `expired` or `bad_key` | | | 05 §4.5 |
| app | silent re-exchange on `401` | device | once per 300 s (`signIn.lastSilentExchangeAt`) | `UserDefaults` | Welcome instead | ADR §5 item 5; 02 §4.2 |
| app | LAN probe | server | only the plex.tv `connections[].local` addresses for that `clientIdentifier`, 1.5 s each, port 5050 or the owner's setting; never a range or broadcast | | | ADR §6 item 6; 02 §2.3 |
| app | `GET /request/nudges` | device | once per calendar day | `UserDefaults` | | 03 §4.12 |
| app | relay registration | device | on permission grant and on APNs token change only; the 10-per-IP relay quota is shared by a household behind NAT, so a re-register loop would lock the house out for an hour | | | 05 §6; this document |

## 7. The loopback door and the Docker image

The door: a request with no `X-Forwarded-For` header from `127.0.0.1` or `::1` is the owner without a session (`api/routes.py:1261-1265`). Any process on the host that can open the port is the owner (deployment-and-security.md §4.3); a proxy on the same host that omits the header makes everyone the owner (SECURITY.md:28-47); the Serve/Funnel documentation does not promise the header (ADR §5 item 8). Rules:

| Rule | Source |
|---|---|
| Phase 2 adds `app.loopback_owner` (default `true`, venv installs unchanged) and `PLEXGET_LOOPBACK_OWNER`, which overrides it; `0`, `false`, `no` mean off | ADR §2; 06 §7 |
| The image sets `PLEXGET_LOOPBACK_OWNER=0`; the setup page writes `flask.host: 0.0.0.0` and `app.loopback_owner: false` together and says why; a server bound beyond loopback never has the door open | ADR §4 "Bind address"; 06 §5.3 |
| The door's two users are replaced: the webhook by `POST /plex/webhook?key=<plex.webhook_key>` (generated at Save, `secrets.token_urlsafe(24)`, constant-time compare), the cron recheck by the settle scheduler (`app.settle_every_minutes`, `PLEXGET_SETTLE_MINUTES=120`) | ADR §2; 06 §8 |
| The image: `python:3.12-slim`, non-root user `plexget`, `VOLUME /data`, `HEALTHCHECK` on `/health`, `waitress` with eight threads, `PLEXGET_SETTLE_MINUTES=120`; `.dockerignore` excludes `venv/`, `.git/`, `data/`, `config.yaml`, `.env*`, `*.db*`, `logs/`; no secret, key or token is baked in | ADR §2 "Install artefacts"; 06 §2 |
| The port is published on `0.0.0.0:5050` so the LAN probe works; an owner who wants only a proxy to reach it uses `-p 127.0.0.1:5050:5050` at the cost of the probe, and the README says so | 06 §6 |
| `flask.debug` is never written by the setup page and never set in the image; `main.py` uses `waitress` whenever it is false | ADR §2; SECURITY.md:56-60 |
| Until the first Save, `/setup` is open to the LAN; the page says so above step 1; the draft binds to the first account that completes step 1; after Save every `/setup/*` route needs an owner session | design-A §8; 06 §5.3 |
| Publishing 5050 on the LAN also exposes the open tier of §2.4, and `/auth/start` mints pins under the server's identifier for anyone on the LAN; the setup page and README say so | ADR §5 item 19 |

## 8. What members must never receive

The authoritative list is the strip table in 03 §7; the rows that are threats rather than tidiness are restated here with the rule the tests assert.

| Data | Rule |
|---|---|
| `missing_files` (absolute server paths) | removed from every Film dict in every response to a `shared` session, including nested `close`, `matches`, `unavailable`, `now_in_plex`; `file_missing` stays (household-api.md §6.10; ADR §2) |
| other members' identities | a member sees the usernames the web page already shows (`requested_by` on `GET /request/wanted/list`, `posted_by` on notes) and nothing more: no plex.tv id, `email`, `thumb`, `last_seen`, session row, device row or `native_devices` handle of anyone else. The gift picker is owner-only (`GET /members`, owner tier, `api/routes.py:1798-1837`) and receives `username` and `last_seen` only |
| `search_query`, `notes`, `requested_by_id` on wanted rows | owner routes only; `GET /request/wanted/list` keeps its short dict (03 §4.4) |
| `persona.voice`, `persona.examples` | never on the wire to anyone (ADR §4 "Persona"; persona-themes-limits.md §1.3) |
| events addressed to others; `unread_messages`; `messages` rows | `GET /api/v1/changes` filters by `recipient_id`; owner-only kinds never reach a member; `unread_messages` is omitted for members (03 §3.11) |
| every secret in §1, `app.urls` is the only config value served | never in any response, including `GET /api/v1/server` and `GET /api/v1/owner/status` (03 §7) |
| raw file names, release tags, `path` values, Sonarr/Radarr listings | routes never called (03 §8; brief fact 8) |

## 9. Relay abuse

| Control | Rule | Source |
|---|---|---|
| Quotas | §6: 10 registrations per IP per hour, 60 per key per minute, 1,000 per key per day, 100 per handle per day, 30 server-key calls per IP per hour; counters in SQLite so a restart does not reset them | ADR §2; 05 §6 |
| Key issuance | only against a JWS verified with Apple's App Store Server Library against Apple's roots, the bundle id and the product ids; a forged JWS is `401 invalid_jws` (the Phase 7 gate) | ADR §7 Phase 7; 05 §5.1 |
| Key at rest | `server_keys.key_hash` and `previous_key_hash` are hashes; the clear key is shown once; a re-post rotates it and both work for 24 hours | 05 §3.2, §4.3 |
| Key revocation | `REFUND` and `REVOKE` notifications set `status = 'revoked'` with no grace; a lapsed key answers `expired` after `expires_at` plus 16 days and spends no quota; the owner's "Delete my relay data" sets `status = 'deleted'` and clears the hashes | ADR §2, §4 "Relay data deletion"; 05 §5.2 |
| Device secret at rest | `secret_hash` only; the secret is presented by the phone alone; handle and secret are distinct values | ADR §5 items 6 and 13; 05 §3.1 |
| Logging | one JSON line per request with method, path, status, key id, handle prefix (6 characters) and duration; never a body, a full handle, a device secret, a server key or a raw IP; verification failures log the first 32 characters of the payload only; retention 30 days | ADR §2; 05 §4.5, §4.6, §11 |
| Bodies | never written to the database; the `apple_notifications.decoded_json` is nulled on deletion | 05 §3.3, §4.4 |
| Replay | `notificationUUID` stored once, applied once, answered `200` both times; an older `signedDate` never moves `expires_at` backwards | 05 §12 |
| Reach | no CORS headers, nothing callable from a browser page; the household cannot reach `POST /v1/push` because only the server holds the key | 05 §6 |
| Monitoring | `401 invalid_jws`/`invalid_signature` above 20 per hour alerts; `403 InvalidProviderToken` alerts on first occurrence | 05 §11 |
| Deferred | App Attest on registration and the "another device registered under your server key" alert are v1.1 | ADR §4 |

## 10. Privacy: what leaves the home, and to whom

| Data | From | To | When | Stored there |
|---|---|---|---|---|
| pin creation and link, `X-Plex-*` headers (product, client identifier, version, platform, device, device name), the account token | app | plex.tv | sign-in; `GET /api/v2/user` and `/resources` afterwards | by Plex, under Plex's policy |
| pin id and client identifier; device record `{id, name, model, os, app}`; every search, playlist, wanted and message action; APNs handle, environment, app version | app | the household's PlexGet server (the owner's machine) | sign-in and use | `sessions`, `native_devices`, `wanted`, `messages`, `events` |
| `GET /api/v1/server` probe | app | LAN addresses plex.tv listed for the Plex server | when no URL is known or the Home-network toggle is on | not stored |
| APNs device token, environment, app version, client IP (hashed with a salt for quotas) | app | the relay (`push.<developer-domain>`) | after notification permission | `devices`, `quota_counters` |
| the subscription's `jwsRepresentation`, therefore `originalTransactionId`, product, expiry | owner's app | the relay | purchase, renewal, "Restore purchases" | `server_keys`, `apple_notifications` |
| App Store Server Notifications V2 for the subscription | Apple | the relay | renewal, expiry, refund, grace | `apple_notifications` |
| handle, event kind, rendered title and body, `collapse_id`, `ref`, `srv` (machine identifier), server key | the owner's server | the relay | each notification | not stored; counters only |
| title, body, `ev`, `ref`, `srv` | the relay | APNs (Apple) | each notification | by Apple, under Apple's policy |
| search text and filters; the owner's model and TMDb keys | the server | the model provider and TMDb, server side | searches | never from the phone; the app has no key |
| app version, iOS version, server `build` | the Report screen's "Report to the developer" | `AppIdentity.supportEmail`, in a Mail sheet the person sends | on request | with the developer, as email |
| nothing | app | analytics, crash reporting, advertising, any third-party SDK | never | brief facts 12 and 14 |

The privacy label and `PrivacyInfo.xcprivacy` in 08 §4 declare the relay rows of this table (Device ID, Purchase History, Other User Content in transit; all App Functionality, not linked, not tracking) and nothing else, because the developer operates only the relay. The privacy policy page says the same in plain words (08 §4.1).

## 11. Security tests the builder must write

Listed by test name. `07-build-phases.md` assigns each to its phase; the file is the one named for that phase there, defaulting to the file given here. Every server test uses Flask's test client with plex.tv, `PlexServer` and the relay stubbed (brief fact 17).

Server (`pytest -q` in this repository):

| File | Test | Asserts |
|---|---|---|
| `tests/test_guard.py` | `test_anonymous_household_route_is_401_json` | `GET /request/wanted/list` with no credential is `401` with `auth_required` |
| | `test_anonymous_owner_route_is_403_json` | `GET /libraries` with `Accept: application/json` is `403 {"error": "that part is the owner's"}` |
| | `test_member_bearer_cannot_reach_owner_routes` | a `shared` bearer on `/wanted`, `/messages`, `/api/v1/owner/status`, `/api/v1/owner/relay` is `403` |
| | `test_bearer_then_cookie_order` | a valid bearer wins over an invalid cookie and an invalid bearer is `401` even with a valid cookie |
| | `test_html_accept_never_redirects_bearer` | a bearer request with `Accept: text/html` to an owner route as a member is `403`, not `302` |
| | `test_unhandled_exception_on_api_v1_is_json` | a route that raises answers `500 {"error", "code"}` with no HTML |
| | `test_loopback_door_off_by_config` | with `app.loopback_owner: false`, `127.0.0.1` without `X-Forwarded-For` is `403` on `/libraries`; with the default it is `200`; `PLEXGET_LOOPBACK_OWNER=0` overrides `true` |
| | `test_webhook_key` | right key `200`; wrong or missing key `403 {"error": "webhook key missing or wrong"}`; keyless is accepted only when the door is open and the peer is loopback |
| `tests/test_sessions.py` | `test_new_rows_store_sha256` | after the exchange, no `sessions.token` equals the returned token and one equals its `sha256` |
| | `test_legacy_raw_row_still_valid` | a pre-existing raw-token row authenticates until its 90-day clock |
| | `test_ios_row_slides_web_row_does_not` | an `ios` row with `created_at` 200 days ago and `last_seen` 10 days ago is valid; a `web` row with `created_at` 91 days ago is not |
| | `test_ios_row_idle_181_days_is_expired` | `401 session_expired` |
| | `test_revoked_row_is_401_session_revoked` | after `DELETE /api/v1/auth/sessions/<id>`, the bearer is `401` with `code: "session_revoked"` |
| | `test_member_cannot_revoke_others` | `404` for another person's row; the owner may revoke any row |
| | `test_sign_out_everywhere` | `DELETE /api/v1/auth/sessions` revokes every row of the caller and none of anyone else |
| | `test_withdrawn_share_is_401_within_roster_refresh` | removing the account from the stubbed roster makes the next request `401` |
| | `test_no_secret_in_session_responses` | `GET /api/v1/auth/session` and `/sessions` bodies contain no `token`, `plex_token`, `plex_token_enc`, `relay_handle` |
| `tests/test_exchange.py` | `test_plex_token_body_refused_over_plain_http` | `400 pin_required` when `app.testing` is false and the request is not secure |
| | `test_plex_token_body_accepted_over_https` | with `X-Forwarded-Proto: https` through `ProxyFix` |
| | `test_account_token_never_stored` | after the exchange, no table column contains the stubbed account token in clear or encrypted form |
| | `test_exchange_limiter_device_overall_account` | the 13th call for one `device.id` in 600 s, the 41st overall, and the 7th for one account in an hour are `429 rate_limited` with `retry_after`; the caller-address rule is not applied (two devices behind one address both succeed) |
| | `test_expired_or_unlinked_pin` | `401 pin_invalid` and `409 pin_not_linked` |
| `tests/test_strip.py` | `test_missing_files_stripped_for_member` | every route in 03 §7 row 1, member session: no `missing_files` key at any depth; owner session: present |
| | `test_member_wanted_list_has_no_ids_or_notes` | `GET /request/wanted/list` carries no `requested_by_id`, `search_query`, `notes` |
| | `test_persona_has_no_voice_or_examples` | `GET /persona` |
| | `test_server_info_has_no_secrets` | `GET /api/v1/server` and `/api/v1/owner/status` contain none of `plex.token`, `webhook_key`, `server_key`, `tmdb.api_key`, `ai.api_key` |
| `tests/test_push_devices.py` | `test_device_bound_to_session_user` | a body naming another `plex_user_id` is ignored; the row belongs to the bearer |
| | `test_sixth_device_is_409` | `device_limit` |
| | `test_handle_never_listed` | `GET /api/v1/owner/status` returns counts only |
| | `test_push_test_limiter` | the GAP limiter of §2.2 |
| `tests/test_changes.py` | `test_member_never_sees_owner_only_events` | `requested`, `file_missing`, `message` rows are absent for a member; `unread_messages` absent |
| | `test_broadcast_excludes_poster` | |
| `tests/test_relay_client.py` | `test_relay_call_carries_no_plex_data` | the stubbed `relay.post` body has exactly `handle, event, title, body, collapse_id, data.ref, data.srv` and the header `Authorization: Bearer <server_key>`; no token, username or URL |
| | `test_relay_json_is_0600` | `POST /api/v1/owner/relay` writes `data/relay.json` with mode `0600` |
| `tests/test_setup.py` | `test_setup_open_until_save_then_owner_only` | before Save `/setup` is `200` anonymously; after Save it is `401`/`403` without an owner session |
| | `test_setup_draft_bound_to_first_account` | a second account's `POST /setup/plex/start` is `409` |
| | `test_setup_never_writes_debug` | the written `config.yaml` has `flask.debug` absent or false and `app.loopback_owner: false` |
| | `test_secret_key_and_client_id_permissions` | `data/secret.key` is `0600` in a `0700` directory; the legacy `uuid5` is kept where `data/` exists (gate 3) |

Relay (`pytest -q` in `<AppName>-relay`), the 05 §12 suite; the security assertions by name: in `test_devices.py` `test_two_distinct_values`, `test_secret_not_stored_in_clear`, `test_wrong_secret_is_401`, `test_eleventh_registration_is_429_and_survives_restart`; in `test_server_keys.py` `test_forged_jws_fake_verifier`, `test_forged_jws_real_verifier_local_key` (the Phase 7 gate), `test_wrong_bundle_or_product_is_403`, `test_revoked_row_is_403`, `test_delete_with_other_transaction_is_403_not_yours`, `test_key_stored_hashed`; in `test_push.py` `test_unknown_key_is_401`, `test_expired_key_spends_no_quota`, `test_oversize_body_is_413`, `test_title_101_is_400`, `test_bad_ref_is_400`, `test_log_has_no_body`; in `test_quotas.py` `test_key_minute`, `test_key_day`, `test_handle_day`, `test_windows_roll`; in `test_apple_notifications.py` `test_replay_stored_once`, `test_refund_revokes_without_grace`, `test_older_signed_date_never_lowers_expiry`, `test_unverifiable_is_401_stores_nothing`; in `test_join.py` `test_join_has_no_fragment`; and a socket guard in `conftest.py` that fails any test opening a real socket.

Kit (`swift test`, Linux):

| Suite | Test | Asserts |
|---|---|---|
| `TransportPolicyTests` | `testPrivateHostsAllowPlainHTTP` | every class in §4 row 2 and `.local` names |
| | `testPublicHostRefusesPlainHTTP` | `http://203.0.113.5:5050` and `http://example.com` are refused before any request is built |
| | `testHTTPSPreferredWhenBothAnswer` | ranking |
| `EndpointsTests` | `testNoCookieNoPlexHeadersToServer` | no `Cookie` or `X-Plex-*` header on any PlexGet request; `Accept: application/json` exactly |
| | `testBearerOnlyOnSignedRoutes` | absent on `GET /api/v1/server` and `POST /api/v1/auth/plex`, present elsewhere |
| | `testNeverCallsForbiddenRoutes` | no builder exists for any path in 03 §8 |
| `ErrorMapperTests` | `testRevokedNeverReExchanges` | `401 session_revoked` yields `.signInRequired(reason: .revoked)`; `session_expired` and `plex_token_dead` yield the re-exchange reason |
| `SignInTests` | `testSilentReExchangeOncePerFiveMinutes` | with an injected clock, a second `401` within 300 s presents Welcome |
| | `testAccountTokenNeverInExchangeBody` | the hand-off body carries `pin` and `device` only unless the gate-1 fallback is explicitly selected with an `https://` typed address |
| `PairingTests` | `testProbeTargetsOnlyListedLocalAddresses` | fake `resources` with two `local == true` and one `local == false` entry: exactly two probes |
| | `testWrongMachineIdDropsURL` | a candidate answering another `plex_machine_id` is removed, never stored |
| | `testUninvitedServerRequiresConfirmation` | the state machine does not reach `exchanging` without `confirmed` for an un-invited server |
| `ChangesTests` | `testRefParsingRejectsAnythingElse` | `wanted:12`, `message:7`, `note:3` route; `https://…`, `wanted:x`, empty are ignored |
| `RedactionTests` | `testDescriptionsHideSecrets` | `Session`, `Invite`, `RelayRegistration` `description`/`debugDescription` never contain the token, handle or secret |

App (`xcodebuild test`, simulator): `KeychainTests.testAccessibilityIsAfterFirstUnlockThisDeviceOnly`, `KeychainTests.testNotSynchronizable`, `ATSTests.testOnlyLocalNetworkingKey` (`Info.plist` `NSAppTransportSecurity` has exactly one key), `testLocalNetworkUsageDescriptionPresent` (04 §11), `PushBridgeTests.testPayloadURLNeverOpened`. CI greps in the iOS repository: `print(`, `URLRequest(` outside `Endpoints.swift`, `URLSession` outside `URLSessionTransport`, `serverTrust`, `URLCredential(trust`, `NSAllowsArbitraryLoads`, `openURL` outside `Report` and `Paywall`, the banned words of 04 §10.3, and every path in 03 §8.

## 12. Incident playbook

| Incident | Steps | Effect on people |
|---|---|---|
| APNs `.p8` key leaked or lost | revoke the key in the Apple Developer portal; create a new key; set `APNS_KEY_P8` (or the mounted file) and `APNS_KEY_ID` on the relay; redeploy; watch `GET /v1/health` and the first real send per environment (05 §9, §11) | none customer-side; no handle, key or server changes (design-A §8) |
| Relay database leaked | rotate `RELAY_QUOTA_SALT`; run the key rotation below; nothing else in it is usable without the `.p8` (APNs tokens, hashes, transaction ids, counters) | owners' apps re-post their JWS on next launch |
| A server key leaked (an owner's `data/relay.json` or a log) | the owner opens the Console's Server screen; "Delete my relay data" then subscribe-restore re-issues, or the app's next launch re-posts the JWS, which rotates the key and retires the old one after 24 hours (05 §4.3); the leaked key is dead within a day | push continues |
| Revoke every server key at once | GAP: an admin command `relay/admin.py rotate-all-keys` that clears `key_hash` and `previous_key_hash` on every `active` row, so every `POST /v1/push` answers `401` and each server marks `data/relay.json` inactive (05 §8); owners' apps re-post their JWS on next launch and push returns. Setting `status = 'revoked'` is wrong for this, because a revoked row refuses re-issue (`403 revoked`, 05 §4.3). The builder adds the command with a test in Phase 6 | push drops to polling until each owner opens the app |
| A server's `data/secret.key` rotated or deleted | delete the file; the next start makes a new one (`api/secrets_store.py:18-32`); every stored Plex token decrypts to `""`; web users are signed out by `/auth/me` (SECURITY.md:106-112); phones receive `401 plex_token_dead` on their first Plex-backed call and silently re-exchange with their plex.tv token, so they stay signed in | phones barely notice; this is not the "sign everyone out" switch for the app |
| Sign every phone out of a server | the owner revokes rows from the Console's Sessions screen (`DELETE /api/v1/auth/sessions/<id>` per row), or on the host `UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP` (every row then answers `session_revoked`, which never re-exchanges). GAP: an owner-wide `DELETE /api/v1/auth/sessions?all=1` is not in the ADR; the builder adds it, owner tier, with a test, if the Console's Sessions screen needs one action rather than one per row | everyone signs in again |
| Owner's `plex.token` leaked | rotate at plex.tv (SECURITY.md:94-104); re-run setup step 1 (06 §5.3) to write the new token; the roster, sessions and the machine identifier are unchanged | none |
| `config.yaml` or the database leaked | as SECURITY.md:70-86 and 106-119, plus: rotate `plex.webhook_key` by re-running Save; rotate the relay key as above; new session rows are hashed, but legacy raw rows are live until their 90-day clock, so revoke them | as above |
| A phone lost | the person: Settings → Sessions from another device, or "sign out everywhere"; sign the device out at plex.tv; the owner can revoke the row; the relay handle stays until the app re-registers or the device is deleted, bounded by `handle_day` 100 | that device is out |
| A member turns hostile | the owner withdraws the Plex share; every session of that account fails within 600 s; the owner deletes the member's `native_devices` rows through revocation of their sessions and, if needed, `DELETE /api/v1/push/devices/<device_id>` is the member's own; GAP: the owner has no route to delete another member's device row; the row dies on the relay's `unregistered` answer or on the 400-day purge, and until then that phone can still receive pushes addressed to that account, which the roster check prevents by never addressing any | the member is out |

## 13. Gates and GAP index

ADR §6 gates carried by this document: gate 1 (pin linking behaviour, §5), gate 2 (Funnel headers and `is_secure`, §4), gate 3 (client identifier migration, `tests/test_setup.py` in §11), gate 6 (`NSLocalNetworkUsageDescription` and the probe's targets, §6 and §11), gate 10 (5.2.3 and 5.2.1, §2.7).

`GAP:` lines in this document, for the builder to add with tests before use:

1. §2.2, §6, §11: a 5-per-user-per-hour limiter on `POST /api/v1/push/test` and `POST /push/test`.
2. §3: the `401` codes `session_revoked`, `session_expired`, `plex_token_dead` in the guard's body and in 03.
3. §12: `relay/admin.py rotate-all-keys`.
4. §12: an owner-wide session revocation, only if the Console's Sessions screen needs it.
5. §12: the owner cannot delete another member's `native_devices` row; recorded, not built, in v1.
