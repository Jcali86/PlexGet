# 05. The push relay (`push-relay`)

Purpose: this document is the complete specification of the one cloud component, the push relay, for the builder of Phase 6 (relay and push) and Phase 7 (server-key issuance). It states what the relay is and is not, its SQLite schema, every endpoint with request and response JSON and errors, the APNs sending path, the entitlement rules, the quotas, how the self-hosted server calls it through `api/relay.py`, deployment, cost, monitoring and the pytest suite. Every Apple fact not in the ADR or the constraints brief is marked (verified: how) or (unverified) and the two ADR gates that cover them (ADR §6 items 4 and 5) are carried here as stop conditions. The relay's names come from the ADR §2 verbatim: repository `<AppName>-relay` (private), service name `push-relay`, host `push.<developer-domain>`.

Read with: 02-architecture.md (§2.12 relay push end to end, §2.14 purchase and issuance, §2.15 lapse, §3.6 the minimum table content), 03-server-api.md (§3.12 to §3.14, the server endpoints that store and report the key), 04-ios-app.md (`PushBridge`, `Store`, the Keychain rows `relay.handle`, `relay.device-secret`, `relay.server-key-id.<machine_id>`), 07-build-phases.md (Phase 6 and Phase 7 gates), 08-app-store-and-release.md (the human steps: APNs key, server-notifications URL, Billing Grace Period), 09-security.md (the relay's threat rows), 10-decision-record.md (the ADR).

## 1. What the relay is and is not

The relay exists because a push to an iOS app must be signed with the developer's APNs auth key, and that key cannot be given to customers' self-hosted servers (constraints brief fact 2; push-and-notifications.md §6.2). It is the developer's only cloud component and what the Owner subscription pays for (ADR §4 "What is paid").

| The relay does | The relay never does |
|---|---|
| Holds the APNs `.p8` key, its key id and team id, as secrets outside the repository | Sees a Plex token, a session token or a server address (ADR §1). The `srv` value it forwards is the Plex `machineIdentifier`, which is public (ADR §5 item 4), not an address |
| Maps an opaque `handle` to an APNs device token and environment | Stores a notification title or body, or logs one (ADR §2 "Bodies are never logged") |
| Issues a server key to an owner's app against a verified StoreKit 2 transaction JWS | Talks to any PlexGet server; the server calls it, never the reverse |
| Receives App Store Server Notifications V2 and keeps `expires_at` current | Carries API traffic of any kind (design C's tunnel is rejected for v1, ADR §3) |
| Sends one APNs request per `POST /v1/push` and reports the outcome | Queue, retry beyond one attempt, or store a push for later; the server's `events` table is the durable record (03-server-api.md §3.11) |
| Serves `GET /join` and `/.well-known/apple-app-site-association` for the invite universal link | Read the invite fragment; `#…` never reaches a web server |

Note: the ADR §4 row "Notification Service Extension (push carries only `{ev, ref}`)" is v2. In v1 the rendered title and body transit the relay over TLS inside the request and the APNs payload, and are not stored; the custom keys outside `aps` are only `ev`, `ref` and `srv` (design-A §2.3, §5.2). The privacy policy says so (08-app-store-and-release.md).

Note: design-A §2.3 specified a Go binary and a `device_secret` equal to the handle; the ADR overrides both (Python; two distinct random values, ADR §5 items 6 and 13). This document follows the ADR.

## 2. Stack and layout

| Item | Choice | Reason |
|---|---|---|
| Language and framework | Python 3.12, Flask, served by `waitress` (one process, eight threads) | Same pattern as the server (ADR §2), so the builder's tests and habits carry over; one process keeps the quota counters coherent |
| APNs transport | `httpx` with the `http2` extra (`h2`), one `httpx.Client(http2=True)` per APNs environment, shared across threads (unverified: `httpx.Client` thread safety is stated in the httpx documentation from memory; the builder confirms it in the library's docs before Phase 6 or serialises sends behind one lock) | APNs requires HTTP/2 (unverified, gate 5) |
| Provider token | `PyJWT[crypto]` signing ES256 with the `.p8` | Token-based APNs auth (unverified, gate 5) |
| JWS and notification verification | Apple's App Store Server Library for Python, PyPI name `app-store-server-library`, module `appstoreserverlibrary`, class `SignedDataVerifier` (unverified, gate 4: the human confirms the package name; the builder reads its README before writing `entitlements.py` and stops if verification cannot be done with it) | ADR §2, §4 "Relay language": never hand-rolled chain checks |
| Storage | `sqlite3` from the standard library, WAL mode, one file on a volume | ADR §2 |
| Tests | `pytest`, Flask test client, a fake APNs transport, a fake verifier plus one real-verifier refusal test | ADR §7 Phase 6, Phase 7 |

Repository layout, fixed so the prompts can name files:

```
<AppName>-relay/
  relay/__init__.py        create_app(config) -> Flask
  relay/wsgi.py            app = create_app(Config.from_env()), the target of waitress-serve (§9)
  relay/config.py          environment variables (§9), validated at start
  relay/db.py              schema (§3), migrations, purge()
  relay/apns.py            APNsClient protocol, HTTPX implementation, provider token cache
  relay/entitlements.py    JWS and notification verification, expires_at rules (§5)
  relay/quotas.py          fixed-window counters (§6)
  relay/routes.py          every endpoint (§4)
  relay/join.py            GET /join and the AASA file
  relay/admin.py           python -m relay.admin rotate-all-keys (09-security.md §12), the only operator command
  scripts/request-test-notification.py   asks Apple for an App Store Server Notifications TEST through the
                           library's request_test_notification() / get_test_notification_status()
                           (08-app-store-and-release.md §1 row 10); credentials from environment variables
  tests/                   conftest.py, test_devices.py, test_server_keys.py, test_push.py,
                           test_apple_notifications.py, test_quotas.py, test_join.py, test_health.py,
                           test_admin.py
  Dockerfile, compose.yaml, fly.toml, CLAUDE.md (relay-CLAUDE.md in this plan), pytest.ini
```

## 3. Data model

All timestamps are ISO 8601 UTC text with `Z`. Secrets are stored as `sha256` hex of the value; the value itself is returned once and never again. No column ever holds a title, a body, a Plex token, a session token, a server address or a raw IP address (02-architecture.md §3.6).

### 3.1 `devices`

| Column | Type | Meaning |
|---|---|---|
| `handle` | `TEXT PRIMARY KEY` | 32 random bytes, base64url without padding, prefixed `h_`; what the server stores in `native_devices.relay_handle`; never returned in any server response (03-server-api.md §3.14, §7) |
| `secret_hash` | `TEXT NOT NULL` | `sha256(device_secret)`; the secret is a second, distinct 32-byte value prefixed `ds_` |
| `apns_token` | `TEXT NOT NULL UNIQUE` | Lower-case hex as the app received it; validated `^[0-9a-f]{32,200}$` (unverified: current tokens are 64 hex characters; the range is deliberately loose) |
| `environment` | `TEXT NOT NULL CHECK IN ('production','sandbox')` | Chooses the APNs host (§7.3) |
| `app_version` | `TEXT` | `"1.0 (42)"`, for support only |
| `created_at` | `TEXT NOT NULL` | |
| `last_push_at` | `TEXT` | Last successful send |
| `last_status` | `TEXT` | Last APNs outcome (`200`, `410`, `400:BadDeviceToken`, …), for support only |
| `failures` | `INTEGER NOT NULL DEFAULT 0` | Consecutive non-2xx outcomes that did not delete the row |

Rules: a `POST /v1/devices` carrying an `apns_token` already present replaces that row (new handle, new secret); the old handle then answers `unregistered`, and the app re-posts the new handle to every paired server (02-architecture.md §2.12 step 4). Rows are deleted on APNs `410` (`Unregistered`, `ExpiredToken`), on `400` with a bad-token reason (§7.5), on `DELETE /v1/devices/<handle>`, and by the nightly purge when `created_at` and `last_push_at` are both older than 400 days.

### 3.2 `server_keys`

| Column | Type | Meaning |
|---|---|---|
| `id` | `TEXT PRIMARY KEY` | 16 random bytes, base64url, prefixed `k_`; public identifier returned to the app and used in `DELETE /v1/server-keys/<id>` |
| `key_hash` | `TEXT UNIQUE` | `sha256(server_key)`; `NULL` when the row was created by a notification before any issue (§5.3) |
| `previous_key_hash` | `TEXT` | The hash replaced by the latest issue, honoured until `previous_valid_until` |
| `previous_valid_until` | `TEXT` | 24 hours after the rotation |
| `original_transaction_id` | `TEXT NOT NULL UNIQUE` | Apple's `originalTransactionId`; the identity of the subscription |
| `product_id` | `TEXT NOT NULL` | `owner.yearly` or `owner.monthly` (01-product-and-decisions.md §2) |
| `bundle_id` | `TEXT NOT NULL` | Must equal `APPLE_BUNDLE_ID` |
| `environment` | `TEXT NOT NULL CHECK IN ('production','sandbox')` | From the JWS or notification, lower-cased |
| `expires_at` | `TEXT NOT NULL` | Apple's `expiresDate`, latest known |
| `status` | `TEXT NOT NULL CHECK IN ('active','revoked','deleted')` | `revoked` on refund or revocation; `deleted` on the owner's request (row kept as a tombstone for 30 days, then purged) |
| `revoked_reason` | `TEXT` | `REFUND`, `REVOKE`, `owner_request` |
| `created_at`, `updated_at`, `last_used_at` | `TEXT` | `last_used_at` is the last `POST /v1/push` |

### 3.3 `apple_notifications`

| Column | Type | Meaning |
|---|---|---|
| `notification_uuid` | `TEXT PRIMARY KEY` | Apple's `notificationUUID`; the replay key |
| `notification_type` | `TEXT NOT NULL` | e.g. `DID_RENEW` |
| `subtype` | `TEXT` | e.g. `GRACE_PERIOD` |
| `original_transaction_id` | `TEXT` | |
| `environment` | `TEXT` | |
| `signed_date` | `TEXT` | Apple's `signedDate` |
| `applied` | `INTEGER NOT NULL DEFAULT 0` | 1 when it changed a `server_keys` row |
| `received_at` | `TEXT NOT NULL` | |
| `decoded_json` | `TEXT` | The decoded payload with `signedTransactionInfo` and `signedRenewalInfo` replaced by their decoded objects; no signed strings kept |

Retention: 400 days, purged nightly. This table is the audit trail for support questions ("why did push stop?").

### 3.4 `quota_counters`

| Column | Type | Meaning |
|---|---|---|
| `scope` | `TEXT NOT NULL` | `ip_register_hour`, `key_day`, `key_minute`, `handle_day` |
| `subject` | `TEXT NOT NULL` | `sha256` of the client IP (with a per-deployment salt `RELAY_QUOTA_SALT`), or the `server_keys.id`, or the handle |
| `window_start` | `TEXT NOT NULL` | The hour, minute or UTC day the window began |
| `count` | `INTEGER NOT NULL` | |

Primary key `(scope, subject, window_start)`. Rows older than two days are purged nightly. Counters are in SQLite, not memory, so a restart does not reset a quota.

## 4. Endpoints

All bodies are JSON; every response is JSON with `Content-Type: application/json`. Errors are `{"error": "<code>", "message": "<sentence>"}`; the codes are fixed here so `api/relay.py` and the app can switch on them. Bodies are capped before parsing: 64 KB on `POST /v1/apple/notifications` and `POST`/`DELETE /v1/server-keys` (Apple's JWS carry `x5c` certificate chains in every header, so a V2 notification with its two inner JWS runs to 12 to 20 KB; unverified sizes), 8 KB everywhere else; over the cap is `413 {"error": "too_large"}`. Unknown fields are ignored on every endpoint except `POST /v1/push`, where any key outside `handle`, `event`, `title`, `body`, `collapse_id`, `data.ref`, `data.srv` is `400 bad_request` and nothing is sent (relay-CLAUDE §5); missing required fields are `400 {"error": "bad_request", "message": "<field> required"}`. The client IP is the last (rightmost) entry of `X-Forwarded-For` when `RELAY_TRUST_PROXY=1` (exactly one trusted hop: Fly.io's edge or the `caddy` service), else the socket address; the first entry is client-supplied and is never used (the server's own `caller()` in `api/routes.py` takes the rightmost entry for the same reason).

| Method and path | Caller | Auth | Purpose |
|---|---|---|---|
| `POST /v1/devices` | app | none; IP quota | Register an APNs token, receive `{handle, device_secret}` |
| `DELETE /v1/devices/<handle>` | app | `Authorization: Bearer <device_secret>` | Member's "delete my relay data" |
| `POST /v1/server-keys` | owner's app | a verified JWS in the body | Issue or rotate a server key |
| `DELETE /v1/server-keys/<id>` | owner's app | a verified JWS in the body for the same `originalTransactionId` | Owner's "delete my relay data" |
| `POST /v1/push` | PlexGet server | `Authorization: Bearer <server_key>` | Send one push |
| `POST /v1/apple/notifications` | Apple | the notification's own signature | App Store Server Notifications V2 |
| `GET /v1/health` | monitor | none | Liveness and dependency state |
| `GET /join` | phone browser | none | Universal-link landing |
| `GET /.well-known/apple-app-site-association` | iOS | none | AASA file |

GAP: `GET /v1/server-keys/<id>` (status). The ADR §2 lists issue and delete only. No v1 screen needs it: the Console's Server screen reads `push.relay_expires_at` from `GET /api/v1/owner/status` (03-server-api.md §3.14). The builder adds it, with tests, only if 04-ios-app.md names a use; until then it does not exist.

### 4.1 `POST /v1/devices`

Request `{"apns_token": "<hex>", "environment": "production" | "sandbox", "app": "1.0 (42)"}`.
Response `201 {"handle": "h_…", "device_secret": "ds_…"}`. The secret is shown exactly once.
Errors: `400 bad_request` (token not hex, environment not one of the two); `429 {"error": "rate_limited", "retry_after": <seconds>}` after 10 registrations from one IP in an hour (ADR §2). Side effects: §3.1 replacement rule. The app stores both values in the Keychain (02-architecture.md §4.1) and posts only the handle to the server (03-server-api.md §3.8).

### 4.2 `DELETE /v1/devices/<handle>`

Header `Authorization: Bearer <device_secret>`. Response `204`, also `204` for an unknown handle. Errors: `401 {"error": "bad_secret"}` when the handle exists and the hash does not match; `429 rate_limited` above 30 deletes from one IP in an hour (§6). Side effects: the row is deleted; the next `POST /v1/push` for that handle answers `unregistered`, which makes the server delete its `native_devices` row (§8).

### 4.3 `POST /v1/server-keys`

Request `{"transaction_jws": "<StoreKit 2 Transaction.jwsRepresentation>"}`.
Verification (§5.1) yields `originalTransactionId`, `productId`, `bundleId`, `expiresDate`, `environment`, `revocationDate` (unverified field names, gate 4).
Response `201 {"id": "k_…", "server_key": "sk_…", "expires_at": "2027-09-21T00:00:00Z"}` where `server_key` is 32 random bytes base64url. On a repeat post for an `originalTransactionId` already holding a key: when the body carries `"rotate": true` the response is `200` with a new `server_key` and the same `id`, the old key staying valid for 24 hours (§3.2) so the app's forward to `POST /api/v1/owner/relay` (03-server-api.md §3.12) cannot race a rotation; otherwise the response is `200 {"id", "expires_at"}` with no `server_key` and `expires_at` updated. The app sends `rotate: true` only when the server reports `push.delivery: "polling"` after a `bad_key`/`401`, when it holds no `relay.server-key-id.<machine_id>`, or after "Delete my relay data"; it keeps an un-forwarded key in the Keychain as `relay.server-key.pending.<machine_id>` and retries `POST /api/v1/owner/relay` on every launch and foreground until `200`, so a rotation whose forward failed cannot leave the server holding a key that dies 24 hours later. `expires_at` is the later of the stored value and the JWS's `expiresDate`, never earlier, unless the JWS carries a `revocationDate`.
Errors: `401 {"error": "invalid_jws"}` (signature, chain or decode failure); `403 {"error": "wrong_app"}` (bundle id or product id not in `RELAY_PRODUCT_IDS`); `403 {"error": "revoked"}` when the row is `revoked`; `403 {"error": "expired", "expires_at": "…"}` when `expiresDate` plus 16 days is in the past; `400 bad_request`. The app maps `403 {"error": "expired"}` to `RelayError.expired` and every other `401`/`403` to `RelayError.rejected` (02-architecture.md §6.1).

### 4.4 `DELETE /v1/server-keys/<id>`

Request `{"transaction_jws": "…"}`; the JWS must verify and its `originalTransactionId` must equal the row's. Response `204`, also `204` when `<id>` is unknown or already `deleted`. Errors: `401 invalid_jws`; `403 {"error": "not_yours"}` on a mismatch. Side effects: `status = 'deleted'`, `key_hash`, `previous_key_hash` cleared, `decoded_json` of that transaction's `apple_notifications` rows set to `NULL`; a later `POST /v1/server-keys` with a valid JWS for the same transaction re-creates the entitlement (nothing at Apple changed). This is the owner half of ADR §4 "Relay data deletion"; the app also calls `DELETE /api/v1/owner/relay` and `DELETE /v1/devices/<handle>` (02-architecture.md §2.16 item 6).

### 4.5 `POST /v1/push`

Header `Authorization: Bearer <server_key>`.
Request, the ADR §2 shape exactly:

```json
{"handle": "h_…", "event": "arrived", "title": "It's here",
 "body": "Dune (2021) is now on Plex - the film you asked for.",
 "collapse_id": "arrived-42", "data": {"ref": "wanted:42", "srv": "<plex machineIdentifier>"}}
```

Validation: `event` in `arrived | requested | file_missing | message | announcement | test` (push-and-notifications.md §2.3 plus the announcement broadcast); `title` ≤ 100 characters, `body` ≤ 500, `collapse_id` ≤ 64 bytes (unverified: APNs limits `apns-collapse-id` to 64 bytes, gate 5), `ref` ≤ 64 matching `^(wanted|message|note):[0-9]+$` or `null`, `srv` ≤ 64. Anything else is `400 bad_request` and nothing is sent.
Response, always the ADR's `status` word:

| HTTP | Body | Meaning | Server action (§8) |
|---|---|---|---|
| `200` | `{"status": "sent", "apns_id": "<uuid>"}` | APNs accepted it | none |
| `200` | `{"status": "unregistered"}` | Handle unknown, or APNs said the token is dead (§7.5) | delete the `native_devices` row |
| `200` | `{"status": "expired", "expires_at": "…"}` | Key known but past `expires_at` plus 16 days, or `revoked`/`deleted` | mark `data/relay.json` inactive |
| `429` | `{"status": "rate_limited", "scope": "key_minute" \| "key_day" \| "handle_day", "retry_after": n}` | A §6 quota is spent | log, continue |
| `401` | `{"error": "bad_key"}` | No matching `key_hash` or `previous_key_hash` | mark `data/relay.json` inactive |
| `502` | `{"error": "apns_unavailable"}` | APNs unreachable or 5xx after one retry | count a failure, continue |

Quota counters are incremented only on `sent`, `unregistered` and `apns_unavailable`, not on `expired` or `bad_key`, so a lapsed key cannot spend a quota. Bodies are never written to the log or the database; the log line is `push key=<id> handle=<handle[:6]>… event=<event> status=<status> apns=<code>`.

### 4.6 `POST /v1/apple/notifications`

Request: Apple's V2 body `{"signedPayload": "<JWS>"}` (unverified, gate 4). The relay verifies with `SignedDataVerifier.verify_and_decode_notification` (unverified name, gate 4), reads `notificationUUID`, `notificationType`, `subtype`, `signedDate`, `data.environment`, `data.bundleId`, `data.signedTransactionInfo` and `data.signedRenewalInfo` (unverified names, gate 4), decodes the two inner JWS with the same verifier, and applies §5.2.
Response: `200 {}` once stored, including for a replayed `notificationUUID` (stored once, applied once, answered `200` again so Apple stops retrying); `200 {}` for types the relay ignores. Errors: `401 {"error": "invalid_signature"}` when verification fails, which is the only non-200 answer; Apple retries a V2 notification that did not get a 2xx five times, at 1, 12, 24, 48 and 72 hours, in production only; in the sandbox each notification is sent exactly once (verified: Apple "Responding to App Store Server Notifications", 2026-09-22). The re-posted JWS path (§5.3) is therefore the only recovery for a sandbox notification the relay missed. Verification failures are logged with the first 32 characters of the payload only.
The human enters `https://push.<developer-domain>/v1/apple/notifications` as the production and sandbox server URL in App Store Connect (ADR §7 Phase 9).

### 4.7 `GET /v1/health`

Response `200 {"status": "ok", "db": "ok", "apns": {"production": "ok" | "unchecked" | "error", "sandbox": …}, "provider_token_age_s": n, "version": "<git sha>"}`; `503` with `"status": "degraded"` when the database cannot be written or the `.p8` cannot sign. `apns` is the outcome of the most recent real send per environment, not a probe; APNs has no free health check the relay should call on a timer (unverified).

### 4.8 `GET /join` and the AASA file

`GET /join` returns a static HTML page that says the app is needed and links to the App Store listing (`RELAY_APP_STORE_URL`); it never reads the fragment (02-architecture.md §2.4). `/.well-known/apple-app-site-association` returns, with `Content-Type: application/json` and no redirect, `{"applinks": {"apps": [], "details": [{"appID": "<TEAM_ID>.<bundle id>", "paths": ["/join"]}]}}` (unverified: the AASA format is from memory; the builder checks Apple's "Supporting associated domains" before Phase 6 and 04-ios-app.md carries the matching Associated Domains entitlement).

## 5. Entitlement logic

### 5.1 Verifying a JWS

`SignedDataVerifier(root_certificates, enable_online_checks, environment, bundle_id, app_apple_id)` (unverified constructor, gate 4) is built once per environment from `APPLE_ROOT_CERTS_DIR` (the Apple Root CA G3 and the other roots Apple's README names; unverified), `APPLE_BUNDLE_ID` and `APPLE_APP_APPLE_ID`. A JWS is tried against the production verifier first and the sandbox verifier second; the one that succeeds fixes `environment`. Constructor facts checked against `app-store-server-library` 3.1.2 (verified: `appstoreserverlibrary/signed_data_verifier.py`, 2026-09-22): the signature is `(root_certificates, enable_online_checks, environment, bundle_id, app_apple_id=None)`; a production verifier raises at construction without an integer `app_apple_id`; with `enable_online_checks=True` chain checks perform OCSP over the network. A key issued from a sandbox JWS has `server_keys.environment = 'sandbox'`; such an issue is logged at WARNING and counted in the daily digest (§11) so the developer sees any growth (a sandbox transaction is free, so this is the paid tier for anyone on a TestFlight build), and `RELAY_ALLOW_SANDBOX_KEYS=0` (§9, default `1`) refuses it with `403 wrong_app` once the TestFlight period is over. Checks after decoding: `bundleId == APPLE_BUNDLE_ID`, `productId in RELAY_PRODUCT_IDS`, `type == "Auto-Renewable Subscription"` (unverified value), `revocationDate` absent. A `signedDate` older than 30 days is still accepted, because the app may re-post an old transaction after a reinstall; entitlement comes from `expiresDate`, not from freshness.

### 5.2 Notification types and their effect

Type and subtype names are Apple's from memory (unverified, gate 4); the builder replaces this table's names with the library's enum members and keeps the effects.

| `notificationType` (`subtype`) | Effect on the `server_keys` row for `originalTransactionId` |
|---|---|
| `SUBSCRIBED` (`INITIAL_BUY`, `RESUBSCRIBE`) | Create the row if absent with `key_hash NULL` (§5.3); set `expires_at`, `product_id`, `status = 'active'` |
| `DID_RENEW` (any) | `expires_at = expiresDate`; `status = 'active'` |
| `DID_CHANGE_RENEWAL_PREF`, `DID_CHANGE_RENEWAL_STATUS`, `OFFER_REDEEMED`, `PRICE_INCREASE`, `RENEWAL_EXTENDED` | `expires_at = expiresDate` from the transaction info; no status change |
| `DID_FAIL_TO_RENEW` (`GRACE_PERIOD`) | No change; the 16-day window after `expires_at` is already the grace |
| `DID_FAIL_TO_RENEW` (no subtype) | No change; billing retry without grace still ends at `expires_at` plus 16 days at the relay |
| `GRACE_PERIOD_EXPIRED`, `EXPIRED` (any) | `expires_at = expiresDate` (already past); the key answers `expired` once `expires_at` plus 16 days passes |
| `REFUND`, `REVOKE` | `status = 'revoked'`, `revoked_reason = type`, `expires_at = signedDate`; no grace |
| `REFUND_REVERSED` | `status = 'active'`, `expires_at = expiresDate` |
| `TEST` | Stored, `applied = 0` |
| anything else | Stored, `applied = 0`, logged at INFO |

A notification whose `signedDate` is older than the row's `updated_at` source date is stored but not applied, so out-of-order delivery cannot move `expires_at` backwards; refunds and revocations apply regardless of order.

### 5.3 The rule

A server key is honoured by `POST /v1/push` when the row is `active` and `now <= expires_at + 16 days` (ADR §2; the human sets Billing Grace Period to 16 days in App Store Connect so the two agree, ADR §5 item 20). `expires_at` is updated from notifications first and from any JWS the owner's app re-posts second (ADR §4 "Relay entitlement source"); the two paths are idempotent and neither can shorten `expires_at` except through revocation. Key material is only ever issued to an app that presents a verified JWS (§4.3); a notification can create or extend an entitlement row but cannot issue a key, because Apple has no channel to the app. A row created by a notification before any issue has `key_hash NULL` and answers nothing until the app posts.

## 6. Quotas and abuse controls

Fixed windows in `quota_counters` (§3.4), the ADR §2 numbers:

| Scope | Limit | Window | Subject | Answer when spent |
|---|---|---|---|---|
| `ip_register_hour` | 10 `POST /v1/devices` | clock hour | salted IP hash | `429 rate_limited` |
| `key_minute` | 60 `POST /v1/push` | clock minute | `server_keys.id` | `429 {"status": "rate_limited", "scope": "key_minute"}` |
| `key_day` | 1,000 `POST /v1/push` | UTC day | `server_keys.id` | same, `scope: "key_day"` |
| `handle_day` | 100 `POST /v1/push` | UTC day | handle | same, `scope: "handle_day"` |

Other controls: `POST /v1/server-keys` and `DELETE /v1/server-keys/<id>` share a 30-per-IP-per-hour window (`ip_register_hour` with subject prefix `sk:`) because verification costs a signature check; `DELETE /v1/devices/<handle>` shares a 30-per-IP-per-hour window (`ip_register_hour`, subject prefix `dd:`), above it `429 rate_limited`, so the `204`/`401` split cannot be run as a free existence oracle; unauthenticated paths reject bodies over their cap (§4) before parsing; a key is only ever issued against a verified Apple transaction, so a spam campaign costs one subscription per 1,000 pushes a day (design-A §8); the household cannot reach the relay's push path at all, since only the server holds the key. App Attest on registration and the "another device registered under your server key" alert are v1.1 (ADR §4). No CORS headers are sent; no endpoint is callable from a browser page.

## 7. The APNs sending path

Everything in this section that names an Apple host, header, field or status is (unverified: from memory in design-A §2.3) unless marked otherwise, and is covered by ADR §6 gate 5: before `relay/apns.py` is written the builder reads Apple's "Sending notification requests to APNs" and "Establishing a token-based connection to APNs", corrects this section in the same PR, and stops if the pages cannot be read.

### 7.1 Provider token

An ES256 JWT signed with the `.p8`: header `{"alg": "ES256", "kid": "<APNS_KEY_ID>"}`, claims `{"iss": "<APNS_TEAM_ID>", "iat": <now>}`. Apple requires refreshing it at least once an hour and no more often than every 20 minutes (unverified). The relay caches one token per process, regenerates when older than 50 minutes, and regenerates once immediately on a `403` whose `reason` is `ExpiredProviderToken` or `InvalidProviderToken`, then retries that send once; after a `429 TooManyProviderTokenUpdates` it does not regenerate again for 20 minutes.

### 7.2 Request

`POST https://<host>/3/device/<apns_token>` over HTTP/2 with headers `authorization: bearer <jwt>`, `apns-topic: <APNS_TOPIC>` (the bundle id, from `AppIdentity` in the app), `apns-push-type: alert`, `apns-priority: 10`, `apns-expiration: <now + 24 h as Unix seconds>`, `apns-collapse-id: <collapse_id>`, and `apns-id` set to a UUID the relay generates and returns as `apns_id`. Body limit 4 KB for alert pushes (unverified). Connection timeout 5 s, read timeout 10 s.

### 7.3 Hosts and environments

`api.push.apple.com` for `production`, `api.sandbox.push.apple.com` for `sandbox`, port 443 (unverified; 2197 is the alternative port). The device's `environment` column chooses the host per send; the app reports it from its build's `aps-environment` entitlement (04-ios-app.md). Debug builds run from Xcode use sandbox; TestFlight and App Store builds use production (verified: Apple "Sending notification requests to APNs", 2026-09-22; TestFlight, Ad Hoc and App Store builds are distribution-signed with `aps-environment` = `production` and must be sent through `api.push.apple.com`; only a development-signed Xcode run on a device is `sandbox`). The ADR §7 phrase "a sandbox push to a TestFlight build" therefore means a push to a TestFlight build, which is a production send; the sandbox host is exercised by a push to a development-signed build run from Xcode. The app reports the environment it read from its embedded provisioning profile (04 §3.4). A send to the wrong host is refused by APNs with `400 BadDeviceToken` and deletes the row (§7.5), so the environment must come from the build, never from a default.

### 7.4 Payload

```json
{"aps": {"alert": {"title": "<title>", "body": "<body>"}, "sound": "default", "thread-id": "<srv>"},
 "ev": "<event>", "ref": "<ref or null>", "srv": "<srv>"}
```

No other keys. `thread-id` groups one server's notifications on the phone (unverified: `thread-id` is a documented `aps` key from memory). For `event == "test"`, `sound` is still `default`.

### 7.5 Responses

Status codes and reasons (verified: Apple "Handling notification responses from APNs", 2026-09-22); gate 5 still applies, the builder reads the page before writing `relay/apns.py`.

| APNs status | `reason` | Relay action | `POST /v1/push` answer |
|---|---|---|---|
| `200` | | `last_push_at`, `failures = 0` | `sent` |
| `410` | `Unregistered`, `ExpiredToken` | delete the device row | `unregistered` |
| `400` | `BadDeviceToken`, `DeviceTokenNotForTopic` | delete the device row | `unregistered` |
| `400` | any other (`BadCollapseId`, `TopicDisallowed`, `BadMessageId`, `MissingTopic`, …) | log at ERROR with the reason; `failures += 1`; this is a relay bug | `502 apns_unavailable` |
| `413` | `PayloadTooLarge` | log at ERROR; `failures += 1`; this is a relay bug (the §4.5 length limits keep the payload under 4 KB) | `502 apns_unavailable` |
| `403` | `ExpiredProviderToken`, `InvalidProviderToken` | regenerate the JWT, retry once; then ERROR | `sent` or `502` |
| `403` | `MissingProviderToken`, `UnrelatedKeyIdInToken`, `BadEnvironmentKeyIdInToken`, `BadCertificate`, `BadCertificateEnvironment`, `Forbidden` | configuration error: log at ERROR, no regeneration | `502 apns_unavailable` |
| `429` | `TooManyRequests` | `failures += 1` | `429 rate_limited`, `scope: "apns"` |
| `429` | `TooManyProviderTokenUpdates` | the token was regenerated too often: keep the current token for 20 minutes (§7.1); `failures += 1` | `502 apns_unavailable` |
| `500`, `503`, connection error, timeout | | retry once after 1 s | `sent` or `502 apns_unavailable` |

A device row with `failures >= 20` and no success for 30 days is purged nightly.

## 8. How the self-hosted server calls it: `api/relay.py`

This module lives in this repository (ADR §2) and is built in Phase 2 with the relay stubbed; the relay itself is Phase 6.

Configuration: `data/relay.json` under `PLEXGET_DATA`, mode 0600, `{"server_key", "relay_url", "expires_at", "active": bool}` (02-architecture.md §3.5), written by `POST /api/v1/owner/relay` and removed by `DELETE /api/v1/owner/relay` (03-server-api.md §3.12, §3.13). `active()` is true when the file exists, `active` is true and `expires_at` plus 16 days is in the future; `GET /api/v1/server` reports `push.delivery: "relay"` exactly then.

Functions:

| Function | Behaviour |
|---|---|
| `send(db, row, event, title, body, collapse_id, ref, srv, actor=None)` | Called once per `native_devices` row of the recipient from inside `send_event()` and `broadcast()` (`api/push.py:188, 225`; push-and-notifications.md §2.3) after the `events` row is written and after web push. `actor` is the `plex_user_id` that caused the event (the requester or poster; `None` for `arrived`, `file_missing`, `test`): sends caused by one actor are capped at 60 per hour in an in-process dict, above which the call is skipped with one INFO line and polling delivers the row (09-security.md §2.2; `test_relay_actor_budget`). Returns `True` on `sent`, else `False`. Never raises; every call site already wraps `send_event` in `try/except` (push-and-notifications.md §7.5), and this module adds its own |
| `post(url, key, payload)` | The single `requests.post` with `Authorization: Bearer <key>`, `timeout=5`, JSON body. Tests monkeypatch this name (03-server-api.md §6.4) |
| `mark_inactive(reason)` | Rewrites `data/relay.json` with `"active": false` and logs once |

Outcome handling, matching §4.5: `sent` → `native_devices.last_sent = now`, `failures = 0`; `unregistered` → delete the `native_devices` row (the web-push 404/410 rule, `api/push.py:141-142`, push-and-notifications.md §1.3); `expired` or HTTP `401`/`403` → `mark_inactive`, so `push.delivery` flips to `polling` and household apps schedule `Background` on their next `GET /api/v1/server` (02-architecture.md §2.15); `429` → log at INFO, `failures += 1`, continue with the next row; `502`, connection error or timeout → one retry after 2 s, then `failures += 1`.

When the relay is down: after three consecutive connection failures or timeouts across any rows, `send` skips the network for five minutes (an in-process timestamp, coherent because `waitress` runs one process, ADR §2) and returns `False` at once, so a broadcast to a household of six devices cannot hold a request thread for 42 s. Nothing is lost: the `events` row already exists and the app's `BGAppRefreshTask` poll of `GET /api/v1/changes` posts a local notification for it (02-architecture.md §2.13), de-duplicated on `(srv, event, ref)` if the push later arrives (03-server-api.md §3.11). `push.delivery` stays `relay` during an outage; only an entitlement answer changes it. `GET /api/v1/owner/status` exposes `push.relay_expires_at` (03-server-api.md §3.14).

GAP: 03-server-api.md §3.14 does not carry a "relay last error" field. If the Console's Server screen needs to show "the relay could not be reached", the builder adds `push.relay_last_error: str|null` to `GET /api/v1/owner/status` with a test rather than reading logs.

## 9. Deployment

One container, one volume, TLS terminated in front of it.

| Item | Value |
|---|---|
| Image | `python:3.12-slim`, non-root user `relay`, `pip install -r requirements.txt`, `VOLUME /data`, `HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/health').status==200 else 1)"`, `CMD ["waitress-serve", "--listen=0.0.0.0:8080", "--threads=8", "relay.wsgi:app"]` (`relay/wsgi.py` builds `app = create_app(Config.from_env())`; `relay/__init__.py` exposes only the factory so tests import it without secrets) |
| Listener | Plain HTTP on 8080 inside the container only; never exposed without TLS |
| TLS | Fly.io terminates TLS at its edge for `push.<developer-domain>` (`fly certs add`); on a VPS, a `caddy` service in `compose.yaml` with `reverse_proxy relay:8080` obtains the certificate. Either way `RELAY_TRUST_PROXY=1` |
| Database | `/data/relay.db`, WAL mode, `PRAGMA synchronous=NORMAL`; schema created at start by `relay/db.py`; migrations by version number in a `meta` table |
| Secrets | `APNS_KEY_P8` (the file's contents, PEM) or `APNS_KEY_PATH` to a mounted secret file; `APNS_KEY_ID`; `APNS_TEAM_ID`; `RELAY_QUOTA_SALT`. On Fly.io `fly secrets set`; on a VPS a `.env` file with mode 0600 outside the repository. Never in the image, never in git (`.gitignore` covers `*.p8`, `.env`) |
| Other environment | `APNS_TOPIC` (bundle id), `APPLE_BUNDLE_ID`, `APPLE_APP_APPLE_ID`, `APPLE_ROOT_CERTS_DIR=/app/apple-roots`, `RELAY_PRODUCT_IDS=owner.yearly,owner.monthly`, `RELAY_GRACE_DAYS=16`, `RELAY_DB=/data/relay.db`, `RELAY_TEAM_ID` (for the AASA `appID`), `RELAY_APP_STORE_URL`, `RELAY_LOG_LEVEL=INFO`, `RELAY_TRUST_PROXY`, `RELAY_ALLOW_SANDBOX_KEYS=1` (§5.1) |
| Hosting | One shared-CPU VM with 512 MB is enough to 10,000 owners (§10). Fly.io: `fly launch --no-deploy`, `fly volumes create relay_data --size 1`, `fly secrets set …`, `fly deploy`, with `fly.toml` mounting the volume at `/data` and `min_machines_running = 1` so the machine never sleeps (unverified: Fly.io command names from memory). VPS: `docker compose up -d` with `relay` and `caddy` services |
| Backups | A nightly `sqlite3 /data/relay.db ".backup /data/backups/relay-$(date -u +%F).db"` from a cron job or `fly ssh console`, keeping 14 files, plus a copy off the machine (object storage or `rsync`); the database holds only tokens, hashes and counters, so a leaked backup yields nothing about anyone's Plex (design-A §8). Litestream continuous replication is an acceptable substitute (unverified: tool name from memory) |
| Restore | Stop the container, copy the backup to `/data/relay.db`, start; keys and handles are unchanged, so no client notices |
| Updates | `docker pull` or `fly deploy`; the schema migrates forward at start; downgrades are not supported |
| Rotation of the APNs key | Revoke in the developer portal, create a new key, set the two secrets, redeploy; nothing customer-side changes (design-A §8) |

The Dockerfile bakes the Apple root certificates the verifier needs into `/app/apple-roots` from files the human downloads from Apple's PKI page (unverified: which roots the library wants is in its README, gate 4).

## 10. Cost per month

Figures assume a household generates tens of pushes a day (design-A §11), so 10,000 owners is on the order of 200,000 APNs requests a day, about 2.3 a second, which one instance handles. Prices are (unverified: provider list prices from memory, September 2026) and are the developer's to re-check.

| Owners | Hosting | Extras | Total |
|---|---|---|---|
| 100 | One 256 MB to 512 MB shared VM (Fly.io shared-cpu-1x about $2 to $5; or the smallest Hetzner or similar VPS about €4) plus a 1 GB volume | Domain about £1; Apple Developer Program £79 to £99 a year (about £8) | about £10 |
| 1,000 | One 1 GB VM about £8 to £12; the same single container | Uptime monitor (free tier), off-site backup storage about £1 | about £15 to £25 |
| 10,000 | One 2 GB VM about £20 to £30; SQLite still suffices at 2.3 requests a second, and a second warm standby VM restored from the nightly backup is the resilience step, not Postgres | Paid monitoring and paging about £20 to £40; log retention | about £60 to £100 (design-A §11 estimated £120 to £200 with a load balancer and managed Postgres, which this Python-and-SQLite shape does not need at this volume) |

Revenue net of Apple's cut is in 01-product-and-decisions.md §2; the relay is the only recurring cost the subscription must carry.

## 11. Monitoring

| Signal | Source | Alert when |
|---|---|---|
| Liveness | `GET /v1/health` from an external monitor every minute | non-200 for 3 minutes |
| APNs provider token rejected | `403 InvalidProviderToken` or `ExpiredProviderToken` after the retry (§7.1) | first occurrence: the key was revoked or the ids are wrong |
| Sends failing | ratio of `502` to `sent` over 15 minutes above 20% | APNs outage or a relay bug |
| Verification failures | count of `401 invalid_jws` and `401 invalid_signature` per hour | above 20: a forgery attempt or a certificate problem |
| Notification flow | no `apple_notifications` row for 7 days while `server_keys` has active rows | the App Store Connect URL is wrong or the endpoint broke |
| Disk | volume usage | above 80% |
| Daily digest | one INFO line at 00:05 UTC: devices, active keys (production and sandbox separately, §5.1), pushes by status, quota hits | read weekly |

Logs are JSON lines to stdout (`fly logs` or `docker logs`), one line per request with method, path, status, key id, handle prefix and duration; never a body, a full handle, a device secret, a server key or a raw IP. Log retention 30 days.

## 12. The pytest suite

`tests/conftest.py` builds `create_app` with `RELAY_DB=:memory:` (one connection shared through the app), a `FakeAPNs` implementing the `APNsClient` protocol (`send(environment, token, headers, payload) -> APNsResult(status, reason, apns_id)`) that records every call and answers from a script, a `FakeVerifier` that decodes a test JWS shape without signature checks, and a frozen clock. Fixtures never touch the network; `pytest.ini` sets `-p no:cacheprovider` and the tests fail if `httpx` opens a socket (a `socket.socket` monkeypatch that raises).

| Test file | Cases the phase gate requires |
|---|---|
| `test_devices.py` | registration returns two distinct values with the right prefixes; the secret is not stored in clear; re-registering a token replaces the row and the old handle answers `unregistered`; delete with the wrong secret is `401`; delete is idempotent; the 31st delete from one IP in an hour is `429`; the eleventh registration from one IP in an hour is `429` and the counter survives an app restart on the same database |
| `test_server_keys.py` | a valid JWS issues `{id, server_key, expires_at}`; a re-post with `rotate: true` rotates the key and both keys work for 24 hours, then only the new one; a re-post without `rotate` returns no key and changes no hash; a **forged JWS is refused** twice: the fake verifier raising gives `401 invalid_jws`, and a JWS signed with a locally generated EC key run through the real `SignedDataVerifier` with Apple's roots gives `401 invalid_jws` (the Phase 7 gate, ADR §7); the real verifier in this test is built with `enable_online_checks=False`, `Environment.PRODUCTION`, `bundle_id=APPLE_BUNDLE_ID` and an integer `app_apple_id` (the library refuses a production verifier without one, and online checks would trip the socket guard), and the forged JWS carries an `x5c` header holding the local self-signed certificate so the failure is a chain failure, not a header-format failure, with both shapes asserted to yield `401 invalid_jws`; with `RELAY_ALLOW_SANDBOX_KEYS=0` a sandbox JWS is `403 wrong_app`; wrong bundle id and unknown product are `403 wrong_app`; a revoked row is `403 revoked`; delete with a JWS for another transaction is `403 not_yours`; delete clears the hashes and later issue re-creates |
| `test_push.py` | `sent` carries the APNs headers of §7.2 and the payload of §7.4 and nothing else; `410` deletes the device and answers `unregistered`; `400 BadDeviceToken` likewise; `410 ExpiredToken` likewise; `400 BadCollapseId` is `502` and keeps the row; `413 PayloadTooLarge` is `502` and keeps the row; a `403 MissingProviderToken` is `502` without regenerating the token; a `429 TooManyProviderTokenUpdates` blocks regeneration for 20 minutes on the frozen clock; `403 ExpiredProviderToken` regenerates the token and retries once; a `503` retries once then `502`; a key past `expires_at` plus 16 days answers `expired` and spends no quota; a key one hour inside the window answers `sent`; unknown key is `401`; oversize body is `413`; a title of 101 characters is `400`; the log capture contains no title or body text |
| `test_quotas.py` | **quota exhaustion**: the 61st push in one minute is `429 key_minute`; the 1,001st in a day is `429 key_day`; the 101st to one handle is `429 handle_day`; windows roll over on the frozen clock; `expired` and `bad_key` answers do not increment counters; a request carrying `X-Forwarded-For: 1.2.3.4, 5.6.7.8` with `RELAY_TRUST_PROXY=1` is counted under `5.6.7.8`, and rotating the first entry does not reset the window |
| `test_apple_notifications.py` | each row of §5.2 changes `expires_at` and `status` as stated; **notification replay**: the same `notificationUUID` posted twice is stored once, applied once and answered `200` both times; an older `signedDate` does not move `expires_at` backwards; a `REFUND` applies regardless of order; a notification before any issue creates a `key_hash NULL` row that a later issue fills; an unverifiable payload is `401` and stores nothing; a 20 KB well-formed body is accepted, not `413` |
| `test_join.py` | `/join` is HTML without the fragment; the AASA file is JSON with the configured `appID` and `paths` |
| `test_health.py` | `200 ok` on a healthy app; `503 degraded` when the database is read-only |
| `test_admin.py` | `python -m relay.admin rotate-all-keys` clears `key_hash` and `previous_key_hash` on every `active` row, changes no `status`, and a push with the old key is then `401` (09-security.md §12) |

Gate for Phase 6 (ADR §7): `pytest -q` in `<AppName>-relay` green, `pytest -q` in this repository green with `relay.post` stubbed, and the human device step, a push reaching a TestFlight build with the relay deployed to `push.<developer-domain>` and the `.p8` set as a secret. Gate for Phase 7: the forged-JWS refusal above and a sandbox purchase on a device that ends with `data/relay.json` written on the owner's server. The builder does not write `relay/apns.py` or `relay/entitlements.py` until the two verification reads named in §2 and §7 are done and the corrections are in the same PR; if either Apple page cannot be read, the builder writes the deviation note and stops (ADR §6 items 4 and 5).
