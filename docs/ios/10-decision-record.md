# 10. Decision record

<!-- Copied verbatim from the design panel synthesis. Paths of the form scratchpad/... refer to the research and design files produced during planning; their substance is carried into 02-architecture.md and 03-server-api.md. -->

# Architecture Decision Record: the PlexGet iOS app

Status: decided. Inputs: the constraints brief (`scratchpad/constraints.md`), the seven research files under
`scratchpad/research/`, the three designs (`design-A.md`, `design-B.md`, `design-C.md`) and three judges'
verdicts (solo-developer lens, security and App Review lens, user lens). Citations of the form
`(household-api.md §3.1)` are to the research files. Apple, Plex, Tailscale and library facts not in the brief
are marked (verified: how) or (unverified). Where this record checked something itself, the check is named.

## 1. Decision

Build Design A: the existing Python server stays and becomes the app's backend behind a small versioned
`/api/v1` surface with bearer sessions; the app is a native SwiftUI client of it; the developer runs exactly
one cloud component, a push-only relay that holds the APNs key and never sees a Plex token, a session token or
a server address; remote reach is the owner's job, guided by the setup page with Tailscale Funnel as the
recommended path; the server ships as a published Docker image with a first-run setup page. Two of three
judges chose A outright and the third chose C only for the owner's networking step and the strength of the
paid tier, which this record answers by deferring C's tunnel to a later version rather than putting a
multi-tenant reverse proxy on every remote request in v1. Onto A are grafted, from C, the pin hand-off as the
default sign-in exchange, the cross-language fixtures contract test, owner-wide session listing and
revocation, the five-device cap and configurable announcements, App Store Server Notifications V2 at the
relay, the feature flag in `/api/v1/server`, the Kit's Linux boundary as protocols, the `AppIdentity`
constant, the Home-network toggle, an explicit "delete my relay data" action and the `settle_wanted()` loop
fix; and from B, TMDb ids on wanted rows with Plex `Guid` arrival matching, `recheck` honouring `kind` and
`season`, the `X-Plex-Platform-Version` header, the honest polling-latency copy and a report path that reaches
the developer. B's CloudKit shape is rejected because it has no always-on component and gives every member
write access to the whole household (both judges' fatal flaws); C's tunnel is rejected for v1 because it puts
the developer's service on the critical path of every remote request and adds a third language and a Go agent
to every owner's install. Every default in the brief is confirmed except the install command, which becomes a
`docker run` or `docker compose up` of a published image rather than a locally built one, because no
Dockerfile exists today (deployment-and-security.md §2.4).

## 2. The shape

The names below are the names the plan documents use verbatim.

**iOS repository `<AppName>-ios` (private).** Generated from `project.yml` by XcodeGen; two products.
`PlexGetKit` is a Swift package importing only Foundation (and `FoundationNetworking` on Linux) that builds
and tests with `swift build` / `swift test` on Linux. Its modules are `ServerAPI` (an `actor ServerClient`
owning the base-URL set and bearer token, `Endpoints.swift` as the only place a `URLRequest` is built, and one
error mapper turning every `401` into `.signInRequired`, `403` into `.ownerOnly`, `429` with `rate_limited`
into `.rateLimited`, `503` into `.plexUnavailable`, and a `200` body carrying `error` into `.serverSaidNo`,
per household-api.md §6.7), `Models` (lenient `Codable` types for both film shapes, the show shape, the search
union keyed on `kind`, wanted rows, messages, notes, persona, themes, `ServerInfo`, `Session`, `ChangeEvent`,
with one `SQLiteDate` decoder for `YYYY-MM-DD HH:MM:SS` UTC and Unix integers, owner-api.md §4.10), `PlexTV`
(pin creation, pin linking, pin polling, `user`, `resources`), `SignIn` (a pure state machine driven by
injected clocks), `Pairing` (invite parsing, URL ranking, the machine-id probe), `Changes` (cursor and
event-to-notification mapping), `Themes` (the four built-in palettes copied from `request.html:37-79`,
persona-themes-limits.md §4.1, and the twelve-key custom look), `Demo` (`DemoServerClient` over the shared
fixtures), and four protocols implemented only in the app target: `SecretStore`, `Entitlements`, `Transport`,
`Notifier`. The Kit never imports `Security`, `StoreKit`, `UserNotifications`, `UIKit` or `CryptoKit`; the
machine identifier is used raw, so no hashing is needed on the phone. The app target `App/` (Swift 6 strict,
iOS 17) has one feature folder per household screen (Welcome, Pairing, Home, Results, Detail, Playlists,
WantedList, Bell, Looks, Report, Help, Settings), the Console behind the entitlement (Requests, Inbox,
Announce, Discover, Overview, Notifications, Server, Sessions, Paywall), and the platform glue `Store`,
`PushBridge`, `Background`, `Keychain` and `AppIdentity` (bundle display name, `X-Plex-Product`, push topic,
invite domain, all from one constant). `PrivacyInfo.xcprivacy`, `Localizable.xcstrings` (en-GB base),
`CLAUDE.md` and `docs/build/phase-N.md` prompts live here.

**Server changes, this repository (public, MIT).** A blueprint `api/native.py` mounted at `/api/v1`, a setup
blueprint `api/setup.py` with `dashboard/setup.html`, a relay client `api/relay.py`, and small edits to
`api/routes.py`, `api/plex_auth.py`, `api/push.py`, `scanner/wanted_search.py`, `config.py`, `main.py`,
`requirements.txt`. `current_user()` (`api/routes.py:943-951`) reads `Authorization: Bearer <token>` first and
the cookie second, which makes every existing household and owner route usable by the app unchanged
(auth-and-tiers.md §6.1). The `sessions` table gains `device_id TEXT UNIQUE`, `device_name`, `platform`,
`app_version`, `kind` (`web`|`ios`) and `revoked_at`; new rows store `sha256(token)` with a raw-value fallback
for old rows; `ios` rows slide (valid while `last_seen` is within 180 days) while `web` rows keep the fixed 90
days. Endpoints: `GET /api/v1/server` (open: `api`, `build`, `features`, `app_name`, `server_name`,
`plex_machine_id`, `urls: {local, remote}`, `push: {delivery}`, `setup`, `native_signin`, `min_app_version`);
`POST /api/v1/auth/plex` (open, rate-limited: body `{"pin": {"id", "client_id"}, "device": {...}}` or, for
tests and typed HTTPS addresses only, `{"plex_token": ...}`); `GET`/`DELETE /api/v1/auth/session`; `GET
/api/v1/auth/sessions` (own rows; every row with `username` for the owner); `DELETE
/api/v1/auth/sessions/<id>` (own, or any for the owner); `DELETE /api/v1/auth/sessions` (sign out everywhere);
`POST /api/v1/push/devices` and `DELETE /api/v1/push/devices/<device_id>` (table `native_devices`, at most
five per user); `POST /api/v1/push/test`; `GET /api/v1/changes?since=&limit=` (table `events`, written inside
`send_event()` and `broadcast()`, returning `events`, `cursor`, `unread_messages` for the owner and
`wanted_counts`); `POST`/`DELETE /api/v1/owner/relay` (writes `data/relay.json`); `GET /api/v1/owner/status`.
Other edits: a JSON handler for unhandled exceptions on `/api/v1` and bearer requests (owner-api.md §1.4);
`missing_files` stripped for non-owner sessions (household-api.md §6.10); `wanted.tmdb_id` set from
did-you-mean and discover guesses; `recheck()` matching by `Guid` (`tmdb://`, `imdb://`, `tvdb://`) before
folded title and year, searching show sections for `kind == "show"` rows and checking the season is present;
`settle_wanted()` delivering to every device kind before falling back to the owner;
`notifications.announcement` as a configurable block; config keys `app.urls`, `app.loopback_owner`,
`app.settle_every_minutes`, `plex.webhook_key`; environment `PLEXGET_CONFIG`, `PLEXGET_DATA`,
`PLEXGET_LOOPBACK_OWNER`, `PLEXGET_SETTLE_MINUTES`; a per-install `X-Plex-Client-Identifier` in
`data/client_id` with the old `uuid5` kept where `data/` already exists; `config.py` entering setup mode
instead of `SystemExit(1)` when `config.yaml` is absent; and `main.py` serving through `waitress` (one
process, eight threads, so the in-memory limiters at `api/routes.py:969, 1013, 1029` stay coherent) whenever
`flask.debug` is false. The pytest suite in `tests/` writes the JSON each `/api/v1` and household route
returns into `tests/fixtures/api/`, which the iOS repository vendors under `PlexGetKit/Tests/Fixtures/` and
decodes in full in `swift test`.

**Cloud: the push relay, repository `<AppName>-relay` (private), service name `push-relay`.** One small HTTPS
service on `push.<developer-domain>`, written in Python with Apple's App Store Server Library (verified from
my knowledge that Apple publishes it for Python, Swift, Java and Node; the human confirms the package name
before Phase 6), an HTTP/2 APNs client and SQLite, tested with pytest against a fake APNs endpoint. It holds
the `.p8` key, its key id and team id, and nothing else worth stealing. Endpoints: `POST /v1/devices` →
`{handle, device_secret}` (two distinct random values); `DELETE /v1/devices/<handle>` with the device secret;
`POST /v1/server-keys` with a StoreKit 2 `jwsRepresentation` → `{server_key, expires_at}` bound to
`originalTransactionId`; `DELETE /v1/server-keys/<id>` ("delete my relay data"); `POST /v1/push` with the
server key and `{handle, event, title, body, collapse_id, data: {ref, srv}}` → `{status:
sent|unregistered|rate_limited|expired}`; `POST /v1/apple/notifications` for App Store Server Notifications
V2; `GET /v1/health`; `GET /join` and `/.well-known/apple-app-site-association` for the invite universal link.
Tables: `devices`, `server_keys`, `apple_notifications`, `quota_counters`. Quotas: 10 registrations per IP per
hour, 1,000 pushes per key per day, 60 per key per minute, 100 per handle per day. Bodies are never logged.
Entitlement: `expires_at` is updated from server notifications (renewal, expiry, refund, grace) and, as the
second path, from any JWS the owner's app re-posts; a key is honoured until `expires_at` plus 16 days, which
the human configures as the Billing Grace Period in App Store Connect so the two agree (verified by the
security judge: Apple offers 3, 16 or 28 days).

**Install artefacts, this repository.** `Dockerfile` (`python:3.12-slim`, non-root, `waitress`, `VOLUME
/data`, `HEALTHCHECK` on `/health`, `PLEXGET_LOOPBACK_OWNER=0`, `PLEXGET_SETTLE_MINUTES=120`, `flask.host`
`0.0.0.0`); `docker-compose.yml` rewritten with service `plexget` (image `ghcr.io/<org>/plexget`, `5050:5050`,
`plexget-data:/data`, `extra_hosts: host.docker.internal:host-gateway`) and the existing Posteria service kept
under `profiles: [posters]`; `install.sh`; `.github/workflows/image.yml` publishing amd64 and arm64 images on
tag; `docs/DEPLOYMENT.md` rewritten Docker-first with the venv and systemd path second; `tests/conftest.py`
writing a temporary `config.yaml` and pointing `PLEXGET_CONFIG` at it before `config` is imported.

**Demo environment for App Review.** A Plex Media Server holding public-domain films, shared with a reviewer
Plex account, with a PlexGet instance in front of it over HTTPS; credentials go in the review notes. The
in-app demo mode covers the no-network case.

## 3. Rejected alternatives

**Design B (no server, Plex direct plus CloudKit).** Rejected on the fatal flaws every judge found: there is
no process that runs when no phone is awake, so "it arrived" depends on a household phone opening the app and
a withdrawn Plex share keeps read-write access until the owner's app next reconciles (B §3.2 step 11, §5, §7);
and a zone-wide `CKShare` with `publicPermission .readWrite` lets any member rewrite the persona voice, mark
themselves verified, post announcements and read every message, with no per-record ACL to fix it (B §2.3,
§8.1). It also discards fixed facts 1, 4 and 16 and cannot share a wanted list with the existing web page.
Grafted from B: `tmdb_id` on wanted rows and `Guid` matching in `recheck()` (B §5), which is a better matcher
than the title-only search that ignores `kind` (owner-api.md §8; push-and-notifications.md §3.1);
`X-Plex-Platform-Version` in the header set (B §3.1); the honest latency wording for the polling state (B §5);
the per-household model-cost arithmetic (B §11), recorded in §4 against the deferred hosted-search option; and
a "Report to the developer" path (B §9). Plex Home profile switching (B §3.1 step 7; `POST
/api/home/users/{id}/switch`, verified: `venv/lib/python3.11/site-packages/plexapi/myplex.py:500-525`) is
deferred to v1.1 behind the gate in §6.

**Design C (multi-tenant relay carrying API traffic).** Rejected for v1 because the relay is a
man-in-the-middle for every session token, search and message of every household (C §2.2, §8 "Compromised
relay node"), because it is on the critical path of every remote request so a relay outage or a lapsed
subscription removes remote reach rather than only push (C §6, §12.1), because it adds Go, yamux over
WebSocket, Postgres, DNS-01 wildcard issuance and an agent binary shipped to every owner (three languages, a
three-week phase C itself expects to slip), and because its lookup-driven auto-trust lets a paying attacker
register a victim's non-secret machine identifier (security judge). C's C1/C2 comparison table (C §2.1) is
kept as the record of why the server is never hosted in the cloud, and the tunnel is named in §4 as the v2
candidate if owners abandon at the Funnel step. Grafted from C, with the section it came from: the pin
hand-off as the default exchange (C §3 step 7, corrected in §5 below); the fixtures contract test (C §2.5);
owner-wide session listing and revocation and sliding iOS sessions (C §2.4 items 1, 4); five push devices per
user and the `notifications.announcement` block (item 6); the JSON handler and `missing_files` stripping,
which A also had; App Store Server Notifications V2 (C §2.2); `native_signin` and `min_app_version` flags (C
§12.3); the protocol boundary for the Kit (C §2.5, §14.5); `AppIdentity` (C §9); Posteria under `profiles:
[posters]`, multi-arch images and the setup page's health panel built from `test_connections.py` (C §2.6, §7);
the Home-network toggle (C §4); "Unlink and delete my relay data" (C §9); and the `settle_wanted()` loop fix
(C §5).

## 4. Decisions table

Rows marked **owner confirms** are the owner's to settle before the plan is handed to the builder; the plan
builds to the stated default meanwhile.

| Decision | Final choice | Reason |
|---|---|---|
| Architecture | Confirmed: native client of the existing server plus a push-only relay | Maximum reuse of working code; smallest cloud blast radius; both A-favouring judges; C's tunnel deferred to v2 |
| Monetisation | Confirmed: auto-renewable "Owner" group, `owner.yearly` (default, 7-day introductory trial) and `owner.monthly`; Family Sharing off; **owner confirms price** | Relay is an ongoing cost. Note the net: Apple deducts UK VAT before its 15% small-business commission, so £24.99 nets nearer £17-18, not £21 (judge correction of A §11) |
| What is paid | Confirmed and made explicit: the Console tab, and through the server key, relay push for the whole household; everything on the household page, including gifting and "for whom", stays free for the owner too | The buyer is the owner; the household never unlocks anything by purchase (guideline 3.1.1). The user judge rated this the weakest renewal of the three; the honest answer is native push plus the Console, and the v2 tunnel is the upgrade path if that proves insufficient |
| App Store name | Confirmed: a new name, not "PlexGet"; "for Plex" only in the subtitle and only after the owner reads Plex's brand guidelines (unverified whether descriptive use is permitted); in-app name from the server's `app_name`; **owner confirms** | Trademark (brief fact 9). One `AppIdentity` constant makes the rename one line |
| Repository | Confirmed: `<AppName>-ios` and `<AppName>-relay` private; server changes in this public repository; **owner confirms** | Paid client and relay must not be MIT by accident; no agent ships to owners, so nothing else needs to be public |
| Remote reach | Confirmed with wording fixed: Tailscale **Funnel** documented as the primary path (`tailscale funnel 5050`), reverse proxy and Cloudflare Tunnel as alternatives; the setup page stores the pasted HTTPS address as `app.urls.remote` and self-checks it; the invite is a universal link `https://push.<domain>/join#…` also shown as a QR on the setup page and the app's Server screen | Tailnet membership would put an install on every member (A §14.1). The Funnel Personal-plan discrepancy is a gate (§6) |
| Push | Confirmed: relay first, `BGAppRefreshTask` polling `GET /api/v1/changes` plus local notifications as the fallback and for non-subscribers | Brief fact 2; the `events` table is what makes polling possible at all (push-and-notifications.md §6.5) |
| Server install | **Overturned in form, not intent**: one `docker run` (or `docker compose up` with the shipped file) of a published GHCR image, plus the first-run `/setup` page; the image ships the loopback door off, the settle scheduler on, the webhook key generated, and `waitress` | No Dockerfile exists (deployment-and-security.md §2.4) and a compose file that builds locally would make the owner clone the repository; `app.run()` is Werkzeug's development server (`main.py:10`); without the scheduler and key nothing shipped ever sends `arrived` in Docker (push-and-notifications.md §3.3; deployment-and-security.md §4.3) |
| iOS minimum | Confirmed: 17.0 | Brief |
| Concurrency | Confirmed: Swift 6 strict | Brief |
| Sign-in exchange (new) | Pin hand-off: the app mints a fresh pin under its own client identifier, links it to the signed-in account with `PUT https://plex.tv/api/v2/pins/link` (`code`, `X-Plex-Token`; verified: `plexapi/myplex.py:122, 1144-1155` does exactly this), and sends `{"pin": {"id", "client_id"}}` to `POST /api/v1/auth/plex`; the server reads the pin as `check_pin()` does today with the app's identifier, runs the unchanged `plex_identity`, `authorise`, `server_access_token` chain and discards the account token. `plex_token` in the body is accepted only in tests and over HTTPS to a typed address | Bounds what a LAN observer captures to the pin's life instead of the device's Plex login (security judge). Whether `pins/link` accepts a strong pin, and whether a linked pin's token is readable more than once, are Phase 1 gates (§6) |
| Session lifetime (new) | `ios` rows slide, 180 days from `last_seen`; `web` rows unchanged at 90 days from `created_at`; tokens hashed at rest | A phone re-exchanging every 90 days adds plex.tv calls for nothing; the roster check on every request already handles withdrawal (auth-and-tiers.md §2.3); revocation is per device |
| Exchange rate limiter (new) | `POST /api/v1/auth/plex` is keyed on `device.id` (12 per 10 minutes) and the global 40 per 10 minutes, plus 6 per Plex account per hour after identity is known; the caller-address rule is not applied to this route | Behind Funnel or a proxy every caller shares one address bucket (auth-and-tiers.md §6.7; both judges' corrections of A §3.1) |
| LAN probe and trust (new) | The probe of plex.tv's `connections[].local` addresses at port 5050 runs whenever no candidate URL is known; afterwards the Home-network toggle governs it (default on for owners and for anyone whose first probe succeeded, off otherwise). Before the first exchange with a server not reached through an invite the app shows the Plex server name and address and asks for confirmation. Plain HTTP only to private addresses; HTTPS preferred when reachable | The machine identifier is public (auth-and-tiers.md §1.5), so the probe is discovery, not trust; members should not be asked for Local Network permission unless they need it (solo-developer judge) |
| Bind address (new) | Image binds `0.0.0.0`; the setup page writes `flask.host: 0.0.0.0` and `app.loopback_owner: false` together and says why; existing venv installs without setup keep `127.0.0.1` and the door as today | The probe only works when the server binds beyond loopback (`config.py:69`; judge correction of A §4.1); the door must never be open on a LAN-bound server |
| Relay language (new) | Python (pytest, Apple's App Store Server Library, HTTP/2 APNs client, SQLite) rather than A's Go | Two languages instead of three for one builder; the server's test pattern carries over; Apple's library does JWS and server-notification verification |
| Relay entitlement source (new) | App Store Server Notifications V2 first, re-posted JWS second, 16-day grace configured to match App Store Connect | Without notifications a renewal whose owner does not open the app within 16 days loses push silently and a refund keeps it (security judge) |
| Relay data deletion (new) | "Delete my relay data" in the Console's Server screen calls `DELETE /v1/server-keys/<id>` and the device delete; members delete their handle from Settings | Guideline 5.1.1(v) answered rather than argued |
| Notification permission (new) | Asked in context immediately after the first successful sign-in, with a sentence explaining what it is for | Arrival is the point of the app; 4.5.4 is satisfied by an explained prompt (user judge) |
| Arrival matching (new) | `Guid` match on `tmdb_id` when known, then folded title and year; `kind` and `season` respected | Today's `recheck` searches film sections only (owner-api.md §8); plexapi exposes `guids` on videos (verified: `plexapi/video.py:437`, `plexapi/media.py:937`) |
| Fixtures (new) | pytest writes `tests/fixtures/api/*.json`; iOS vendors them; `swift test` decodes every file | Turns "the shapes are inconsistent" (household-api.md §6.7-6.8) into a Linux-runnable failing test |
| Feature flag (new) | `native_signin` and `min_app_version` in `GET /api/v1/server`; the app shows "update needed" rather than failing | A plex.tv change is met by a flag, not an emergency release |
| Persona (confirmed fact 16) | The app renders `name`, `greeting`, `brush_offs`, `images`, `brush_off_moods`; never carries `voice` or `examples` | persona-themes-limits.md §1.3, §8 |
| Plex Home profiles | Deferred to v1.1 behind a gate | Whether `account.users()` lists managed home users, and therefore whether the roster admits them, is unverified |
| Hosted smart search | Deferred; recorded cost from B §11 and C §11: about $0.004 per Sonnet 5 call, roughly $0.54 per household per month at forty calls per person, so it needs its own monthly cap | Not part of v1's subscription; owners paste their own key on the setup page |
| C's tunnel | Deferred to v2, to be reconsidered only if abandonment at the Funnel step is observed | The user judge's case for it is real; the security and operations cost is what v1 cannot carry |
| App Attest on relay registration; "another device registered under your server key" alert | v1.1 | Security judge's promotion, with quotas as the v1 defence |
| Notification Service Extension (push carries only `{ev, ref}`) | v2 | Adds an extension target and an access group the builder would have to get right |
| Bonjour | Cut | The Plex-address probe covers the same case without a Docker bridge caveat |
| `PUT /api/v1/owner/notifications` | Cut; the catalogue is shown read-only | Editing stays in `config.yaml` |

## 5. Corrections adopted

Each correction the judges made, and what the final design does.

1. **The pin hand-off does not keep the token off a LAN or out of a relay** (C §1, §2.2, §3 step 7, §4, §8,
   §12.4; all three judges). The pair `{pin id, client_id}` is redeemable at plex.tv for the pin's life
   (`check_pin`, auth-and-tiers.md §1.5). The ADR adopts the hand-off for the narrower claim only: what a LAN
   observer captures expires with the pin instead of living until the device is removed at plex.tv. The
   privacy copy says so. Because the push relay carries no API traffic, the relay-transit half of the
   correction does not arise here.
2. **The server cannot bound on a pin's claim time** (C §3 step 7). It bounds on creation age: pins older than
   `PIN_LIFE` are refused, as `pin_known()` does today (auth-and-tiers.md §1.3). plex.tv's pin object reports
   `expiresIn`/`expiresAt` only (unverified for v2 pins beyond what `api/plex_auth.py:77` reads).
3. **Sending the plex.tv account token over LAN HTTP is a new exposure, not an inherited one** (A §8, §3.1
   step 7, §4.1; both judges). The web flow never puts the account token on the LAN wire; the server fetches
   it (auth-and-tiers.md §1.5). Adopted: the hand-off replaces the token body, the confirmation step precedes
   the first exchange with an un-invited server, and A's rule against plain HTTP to a non-private address
   stands.
4. **A machine-id match is discovery, not trust** (A §3.1 step 7, §4.1). The identifier is public. Adopted as
   in item 3; the design's text no longer presents the match as a trust signal.
5. **`pin_allowed()` keyed on the caller address collapses a household behind a tunnel into one bucket** (A
   §3.1 step 7; auth-and-tiers.md §6.7). Adopted: the new limiter is keyed on device id and Plex account id,
   the address rule is dropped for `/api/v1/auth/plex`, and the silent re-exchange on `401` is limited to once
   per five minutes per device so it cannot compete with real sign-ins.
6. **Handle and device secret must be distinct values** (A §2.3). Adopted; the handle is what the server
   stores and the owner can read, the secret is only ever presented by the phone.
7. **The LAN probe only works when the server binds beyond loopback** (A §4.1; `config.py:69`). Adopted in the
   bind-address decision; the setup page sets it and the README says so for venv installs.
8. **Tailscale Funnel headers** (A §4.2, §14.1). The solo-developer judge believes `tailscaled` sets
   `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host` on proxied requests and adds
   `Tailscale-User-*` only for tailnet traffic (unverified by them); the security judge re-fetched the Serve
   KB and found it lists only `Tailscale-User-*`. Both agree the decision does not depend on the answer: the
   door ships off in the image and the setup page turns it off. Adopted; the header question stays a gate (§6)
   because it also decides whether `request.is_secure` and the web cookie's `Secure` flag are right behind
   Funnel.
9. **Tailscale Funnel on the Personal plan** (A §4.2). The pricing page lists Funnel under paid tiers while
   the Funnel KB says all plans; the six-user Personal figure is correct. Adopted as a gate before the owner
   copy is written.
10. **`ASWebAuthenticationSession` runs in the simulator** (A §10 Phase 4). Adopted: the simulator test is the
    gate and the device sign-in is kept as a human step for the Plex side.
11. **One introductory offer per subscription group is imprecise** (A §6). Each subscription may carry its own
    introductory offer; a customer is eligible for one per group. No design consequence; the plan's wording
    follows the judge.
12. **Net revenue after VAT** (A §11). Adopted in the monetisation row.
13. **`POST /v1/devices` returns two values, not "the same"** (A §2.3). Adopted (item 6).
14. **5.1.1(v) and relay records** (A §9). The relay holds per-owner keys bound to a transaction id; adopted
    C's explicit deletion path.
15. **Guideline numbering** (C §9 "5.2.1 / 5.2.5"). Third-party marks are 5.2.1; 5.2.5 concerns resemblance to
    Apple products (both judges from memory). The plan cites 5.2.1 only, and the human re-checks numbering
    before submission.
16. **A generic-device `xcodebuild` build needs signing** (C §10 phase 0). The Phase 0 gate uses a simulator
    destination, as A's does.
17. **Go's standard library has no WebSocket** (C §2.2). Moot: no tunnel, no Go.
18. **`machine_hash` on the phone contradicts "no hashing on the phone"** (C §2.5). Moot: the raw identifier
    is used and is public.
19. **The open tier is wider than C §8 lists** (auth-and-tiers.md §4). The setup page and README copy say that
    publishing 5050 on the LAN also exposes `/`, `/request`, `/auth/*`, `/manifest.webmanifest`, `/sw.js` and
    `/themes/art/*`, and that `/auth/start` mints pins under the server's identifier for anyone on the LAN.
20. **Sixteen days is not Apple's longest grace period** (C §6, §11). Options are 3, 16 or 28 (verified by the
    security judge from App Store Connect help). Adopted: 16 is chosen and configured to match, not described
    as the maximum.
21. **C's lookup lets a paying attacker register a victim's machine identifier** (C §2.2, §4). Moot for v1;
    recorded so the v2 tunnel design, if built, requires first contact through an owner invite or an LAN probe
    before binding a tenant.
22. **`GET /api/v2/resources` returns JSON with `Accept: application/json`** (B §2.1). Resolved by
    `api/plex_auth.py:32-39, 223-229` (auth-and-tiers.md §1.1, §1.5). The Kit's `PlexTV` sends the header and
    decodes JSON; no XML parsing is planned.
23. **`myplex.py:109` is the invite constant, not `shared_servers`** (B §3.2). Moot; the server reads the
    roster through `account.users()` (verified: `plexapi/myplex.py:693-697`), which is what `allowed_users()`
    uses.
24. **`ProxyFix` defaults `x_for=1`** (auth-and-tiers.md §0 versus deployment-and-security.md §4.2). Verified
    here: `venv/lib/python3.11/site-packages/werkzeug/middleware/proxy_fix.py:92`. So behind one proxy
    `request.remote_addr` is the forwarded client; the loopback door reads the raw header
    (`api/routes.py:1263-1265`) and is unaffected. The plan's API document carries a note telling the builder
    which file is right.
25. **The repository has no tests; `scratchpad/test2.py` exists** (A §14.2, C §2.4). Confirmed:
    `CONTRIBUTING.md:46` ("There are none") and the file is present in the session scratchpad. Phase 1 defines
    the pattern with `tests/conftest.py`.
26. **Persona voice and examples must stay off the wire** (B §2.3, §2.5, §14; persona-themes-limits.md §1.3).
    Confirmed as fact 16 in the decisions table; the app never sees them.
27. **Members cannot be trusted with self-asserted identity** (B §3.3, §14). Confirmed as fact 4: the server
    verifies against plex.tv and never trusts a username the app sends.
28. **Model prices quoted in B §11 match the current table** (verified by the solo-developer judge against the
    `claude-api` skill's cached pricing). Recorded in the hosted-search row.

## 6. Open risks the plan must carry as explicit gates

1. **Pin linking behaviour** (Phase 1, human step with a real Plex account). Confirm that `PUT
   /api/v2/pins/link` accepts a strong pin's code as well as a short one, and whether `GET /api/v2/pins/{id}`
   returns `authToken` on a second read within the pin's life. If only short pins link, the hand-off uses a
   short pin (the linking happens within milliseconds of minting, so the four-character code is not a
   practical target). If a claimed pin cannot be re-read at all, the hand-off is impossible and the exchange
   falls back to the `plex_token` body, restricted to HTTPS and to LAN exchanges the person has confirmed; the
   builder stops and writes the deviation note rather than improvising.
2. **Tailscale Funnel: plan availability and forwarded headers** (before Phase 8's copy is written). Test with
   a free Personal account whether `tailscale funnel` is permitted, and capture the headers Flask receives
   through it. The door is off regardless; the header result decides whether `X-Forwarded-Proto` arrives and
   therefore whether the web cookie is `Secure` behind Funnel.
3. **Per-install Plex client identifier migration** (Phase 2). Whether existing per-server tokens keep working
   after the server's `X-Plex-Client-Identifier` changes is unverified; the migration keeps the old `uuid5`
   where `data/` already exists, and the test asserts that.
4. **App Store Server Notifications V2 and the App Store Server Library** (Phase 6). The library's Python
   package name and the notification payload's field names are from memory; the builder reads the library's
   README first and stops if verification cannot be done with it.
5. **APNs provider details** (Phase 6). Header names, the `410` and bad-token `400` semantics and the
   sandbox/production hosts are from memory in A §2.3 and must be checked against Apple's "Sending
   notification requests to APNs" before the relay is written.
6. **`NSLocalNetworkUsageDescription` and the probe's targets** (Phase 4 gate). The string is present, the
   probe only targets addresses plex.tv listed as local connections for that machine identifier, never a
   broadcast scan, and the Home-network toggle default is as decided.
7. **Plex Home users on the roster** (Phase 1, human step). Record what `account.users()` returns for a
   managed home user; if they appear with a server entry, v1.1 adds profile switching; if not, the plan says
   so and members use the account that holds the share.
8. **Response-shape drift** (every phase). The fixtures contract test is the gate; a shape the Kit cannot
   decode is fixed by a server test and a document update, never by a lenient guess in Swift.
9. **The macOS runner and physical devices** (Phases 4-7, 9). The builder cannot run Xcode; each Mac phase
   names its human device step (Plex sign-in on a device, a sandbox push to a TestFlight build, a forced
   `BGAppRefreshTask`, a sandbox purchase) and the phase does not pass without it.
10. **App Review on 5.2.3 and 5.2.1** (Phase 9). The banned-word grep gate in CI, no Plex mark in name or
    icon, the demo server and reviewer account prepared before submission, and the export and Sonarr/Radarr
    routes never called.
11. **Owner abandonment at remote reach** (after launch). The setup page's guided Funnel step with a
    self-check and the paywall shown only after the app has worked at home are the v1 mitigations; if
    TestFlight households abandon there, the v2 tunnel decision is reopened with C §2.1 and the security
    judge's tenant-binding correction as its starting point.

## 7. Phase list

The plan documents and the per-phase prompts use these numbers and names verbatim.

- **Phase 0, Scaffold** (Linux, one Mac step): the three repositories' skeletons, `CLAUDE.md`, `project.yml`,
  `PlexGetKit/Package.swift`, `tests/conftest.py` and `pytest.ini` in this repository, CI workflows; gate
  `swift test`, `pytest`, `xcodebuild … -destination 'platform=iOS Simulator,name=iPhone 16' build`.
- **Phase 1, Server: bearer sessions and sign-in exchange** (Linux): `current_user()` bearer path, session
  columns and hashing, sliding iOS sessions, `GET /api/v1/server` with flags, `POST /api/v1/auth/plex` with
  the pin hand-off and its limiter, the sessions endpoints, the JSON error handler, `missing_files` stripping,
  the fixtures writer; gate `pytest -q`.
- **Phase 2, Server: events, devices, arrivals and install switches** (Linux): `events` table and `GET
  /api/v1/changes`, `native_devices` and the push endpoints, `api/relay.py` fan-out inside
  `send_event()`/`broadcast()`, `notifications.announcement`, `wanted.tmdb_id` and `Guid`/`kind`/`season` in
  `recheck()`, the `settle_wanted()` loop fix, the settle scheduler, `plex.webhook_key`, `app.loopback_owner`,
  `app.urls`, `data/client_id`, `waitress` in `main.py`, setup mode in `config.py`; gate `pytest -q` with the
  relay and web push stubbed.
- **Phase 3, PlexGetKit** (Linux): `Models`, `ServerAPI`, `PlexTV`, `SignIn`, `Pairing`, `Changes`, `Themes`,
  `Demo`, the four protocols, decoding every vendored fixture; gate `swift test`.
- **Phase 4, App: welcome to results** (Mac): Welcome, sign-in, pairing and the confirmation step, Home,
  search, the five result kinds, sort chips, selection bar; gate `xcodebuild test` with demo-mode UI smoke
  tests, the `NSLocalNetworkUsageDescription` check, and a human device sign-in.
- **Phase 5, App: the rest of the household** (Mac): Playlists, WantedList, Detail, Bell, Looks, Report (owner
  and developer paths), Help, Settings with sessions and the Home-network toggle; gate `xcodebuild test` and a
  recorded walk of screens-inventory.md §5 items 1-21.
- **Phase 6, Relay and push** (relay on Linux, app on Mac): `push-relay` with its pytest suite against a fake
  APNs endpoint and the server-notifications endpoint, `PushBridge`, `Background` polling, deep links,
  in-context permission; gate `pytest` for the relay and the server client, a sandbox push to a TestFlight
  build, a forced background refresh.
- **Phase 7, StoreKit and Console** (Mac): `Store`, the `.storekit` configuration, server-key issuance and
  deletion, the Console screens; gate `xcodebuild test` with StoreKit testing for purchase, lapse and restore,
  a sandbox purchase on a device, a relay test that a forged JWS is refused.
- **Phase 8, Install: image, compose and setup page** (Linux; may run in parallel with Phases 4-7):
  `Dockerfile`, `docker-compose.yml`, `install.sh`, the GHCR multi-arch workflow, `api/setup.py` and
  `dashboard/setup.html` with the health panel, `docs/DEPLOYMENT.md`; gate `docker build`, `/health` returning
  `{"status": "setup"}` on a fresh volume, `pytest tests/test_setup.py`, a first run recorded on Linux and on
  Docker Desktop.
- **Phase 9, Release** (Mac plus human): banned-word review of fixtures and copy, privacy manifest reasons,
  screenshots from demo mode, review notes, TestFlight with two households, and the human-only steps
  (Developer Program, App Store Connect record, capabilities, APNs key, products and introductory offer,
  Billing Grace Period set to 16 days, the server-notifications URL, the demo server and reviewer account, the
  App Store name).

