# 02 Architecture: the system as built

This document describes the system the builder is constructing, component by component, so that every
later document can refer to a part by its name and every phase prompt can say which part it touches. It
carries each flow the app performs as numbered steps, the database and file additions on the server with
their types, the state the app keeps and where, the concurrency rules Swift 6 strict mode enforces, the
error taxonomy the Kit produces and what each screen does with it, and the list of what stays on the web.
Names are the ADR's names verbatim (10-decision-record.md §2); endpoint shapes are defined in
03-server-api.md and only summarised here; anything the ADR or design-A does not define is marked `GAP:`
for the builder to add server-side with tests before use.

Read with: 10-decision-record.md (binding), 03-server-api.md (every shape), 04-ios-app.md (the Kit and the
app target in detail), 05-push-relay.md (the relay's contract), 09-security.md (the threat model behind the
rules here), 07-build-phases.md (which phase builds which part).

## 1. Components

**Server (this repository, Python, Flask).** The existing application built by `create_app()`
(`api/routes.py:110`) stays the household's single source of truth: wanted list, messages, announcements,
sessions, persona, looks. Its tier guard (`api/routes.py:1235-1273`, household-api.md §1) is unchanged; what
changes is that `current_user()` (`api/routes.py:943-951`, auth-and-tiers.md §6.1) reads
`Authorization: Bearer <token>` before the cookie, so every household and owner JSON route becomes usable by
the app with no other edit. It runs under `waitress` (one process, eight threads) whenever `flask.debug` is
false, so the in-memory limiters at `api/routes.py:969, 1013, 1029` and the roster cache
(`api/plex_auth.py:29`) stay coherent. It never talks to Apple and never sees a receipt.

**`/api/v1` blueprint (`api/native.py`).** The versioned surface: `GET /api/v1/server`,
`POST /api/v1/auth/plex`, the `/api/v1/auth/session[s]` routes, `/api/v1/push/devices`,
`/api/v1/push/test`, `GET /api/v1/changes`, `/api/v1/owner/relay`, `GET /api/v1/owner/status`
(10-decision-record.md §2). It also installs the JSON handler for unhandled exceptions on `/api/v1` paths
and on any bearer request (`/libraries` and `/summary` return Flask's HTML 500 today, owner-api.md §1.4) and
the `missing_files` stripping for non-owner sessions (household-api.md §2a, §6.10). Everything else the app
calls is an existing route listed in 03-server-api.md.

**Setup blueprint (`api/setup.py`, `dashboard/setup.html`).** Served only while `config.py` is in setup mode
(no `config.yaml` at `PLEXGET_CONFIG`), with `/health` returning `{"status": "setup"}` and
`GET /api/v1/server` reporting `"setup": true`. It runs the existing server-side pin flow
(`create_pin`/`check_pin`, `api/plex_auth.py:42-145`) in the owner's browser, writes `config.yaml`,
`data/secret.key`, `data/client_id` and `plex.webhook_key`, stores the pasted HTTPS address as
`app.urls.remote` with a self-check, and shows the invite QR. It is a web page, not an app screen;
06-install-and-setup.md owns it.

**Relay client (`api/relay.py`).** Called from inside `send_event()` and `broadcast()` (`api/push.py:188,
225`; push-and-notifications.md §2.3). When `data/relay.json` exists and is active it posts
`{handle, event, title, body, collapse_id, data: {ref, srv}}` to the relay's `POST /v1/push` with
`Authorization: Bearer <server_key>`, 5 s timeout, once per `native_devices` row of the recipient.
`unregistered` deletes that device row (the web-push 404/410 rule, push-and-notifications.md §1.3);
`expired` or a `401`/`403` marks the key inactive so `GET /api/v1/server` reports `push.delivery:
"polling"`. Tests stub it.

**PlexGetKit (Swift package, Linux-buildable).** Imports only Foundation (and `FoundationNetworking` on
Linux). Its modules and their single responsibilities:

| Module | Holds | Never holds |
|---|---|---|
| `ServerAPI` | `actor ServerClient` (base-URL set, bearer token, the request loop), `Endpoints.swift` (the only place a `URLRequest` is built), the error mapper (§6) | `URLSession`; any screen state |
| `Models` | Lenient `Codable` types: `Film` (shapes 2a and 2b as optionals, household-api.md §2a-2b), `Show` (§2c), `SearchResult` keyed on `kind` (§3.1), `WantedRow`, `Message`, `Note`, `Persona`, `Theme`, `ServerInfo`, `Session`, `ChangeEvent`; one `SQLiteDate` decoder for `YYYY-MM-DD HH:MM:SS` UTC and Unix integers (owner-api.md §4.10) | Any field the server does not send; `voice`/`examples` |
| `PlexTV` | Pin creation, `PUT /api/v2/pins/link`, pin polling, `GET /api/v2/user`, `GET /api/v2/resources`, with `Accept: application/json` and the full `X-Plex-*` header set including `X-Plex-Platform-Version` | XML parsing; any other plex.tv call |
| `SignIn` | The pure state machine `idle → pinIssued → waitingForPlex → exchanging → signedIn | denied | rateLimited(until)` driven by injected clocks | Any I/O |
| `Pairing` | Invite parsing, URL ranking, the machine-id probe, the private-address test | Bonjour (cut) |
| `Changes` | Cursor logic, `ChangeEvent` → local notification and deep-link destination, push de-duplication | Notification posting itself |
| `Themes` | The four built-in palettes copied from `request.html:37-79` (persona-themes-limits.md §4.1) and the twelve-key custom look (§5.2) | Theme editing |
| `Demo` | `DemoServerClient` over the vendored fixtures in `PlexGetKit/Tests/Fixtures/`, public-domain titles only | Network; Keychain |
| Protocols | `SecretStore`, `Entitlements`, `Transport`, `Notifier`, all `Sendable`, implemented only in the app target | Default implementations that touch a platform framework |

The Kit never imports `Security`, `StoreKit`, `UserNotifications`, `UIKit` or `CryptoKit`; the Plex machine
identifier is used raw because it is public (auth-and-tiers.md §1.5).

**App feature folders (`App/`, Swift 6 strict, iOS 17).** One folder per household screen: Welcome,
Pairing, Home, Results, Detail, Playlists, WantedList, Bell, Looks, Report, Help, Settings; the Console
behind the entitlement: Requests, Inbox, Announce, Discover, Overview, Notifications, Server, Sessions,
Paywall. Each folder holds its views and one `@MainActor @Observable` store (§5); none builds a `URLRequest`
or reads the Keychain. Screen behaviour follows screens-inventory.md §5 items 1-21 as 04-ios-app.md lays out.

**Platform glue (`App/`).** `Store` (StoreKit 2: products, purchase, `Transaction.currentEntitlements`,
`Transaction.updates`, `AppStore.sync()`, the `OwnerEntitlement` observable); `PushBridge`
(`UIApplicationDelegateAdaptor` for the APNs token, `UNUserNotificationCenterDelegate` for taps and foreground
display, deep-link routing by `ref`); `Background` (`BGAppRefreshTask` registration and the polling run);
`Keychain` (the `SecretStore` implementation, `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`);
`AppIdentity` (one constant: bundle display name, `X-Plex-Product`, push topic, invite domain, background
task identifier, Keychain service name, so the App Store rename is one line). `PrivacyInfo.xcprivacy`,
`Localizable.xcstrings` (en-GB base), `CLAUDE.md` and `docs/build/phase-N.md` live beside them.

**Push-relay service (`push-relay`, repository `<AppName>-relay`, Python, SQLite).** One HTTPS service on
`push.<developer-domain>` holding the APNs `.p8` key, its key id and team id, and nothing else worth
stealing. It registers devices (`POST /v1/devices` → `{handle, device_secret}`, two distinct random values),
issues server keys against a verified StoreKit 2 JWS (`POST /v1/server-keys`), receives App Store Server
Notifications V2 (`POST /v1/apple/notifications`), forwards pushes (`POST /v1/push`), hosts `/join` and the
`apple-app-site-association` file, and answers `GET /v1/health`. It never sees a Plex token, a session token
or a server address and never logs a body. It honours a key until `expires_at` plus 16 days, matched by the
Billing Grace Period the human sets in App Store Connect (10-decision-record.md §2; verified by the security
judge that Apple offers 3, 16 or 28 days).

## 2. Sequences

Each list is one flow; each line is one step. "App" means the app target calling into the Kit; "Server"
means this repository; "Relay" means `push-relay`. Every server call carries `Accept: application/json`
(owner-api.md §4.7: the guard `302`s HTML-accepting GETs) and, after sign-in, `Authorization: Bearer`.

### 2.1 Household sign-in with the pin hand-off

1. Welcome offers "Sign in with Plex", "I have an invite", "Try the demo"; the member taps Sign in.
2. `PlexTV` mints a pin: `POST https://plex.tv/api/v2/pins?strong=true` with the per-install
   `X-Plex-Client-Identifier` from the Keychain (§4.1) and the `X-Plex-*` header set; reads `id`, `code`.
3. App opens `https://app.plex.tv/auth#?clientID=…&code=…&context[device][product]=<AppIdentity>&forwardUrl=<scheme>://plex-auth-done`
   in `ASWebAuthenticationSession`; the typed alternative ("enter this code at plex.tv/link") sits under it.
4. On callback, every 2.5 s while the session is open, and once on foreground, `PlexTV` polls
   `GET /api/v2/pins/<id>` until `authToken` is present; `SignIn` moves `pinIssued → waitingForPlex`.
5. App stores the plex.tv account token in the Keychain and calls `GET /api/v2/user` (username, thumb) and
   `GET /api/v2/resources?includeHttps=1&includeRelay=0` (servers and their `connections[]`).
6. `Pairing` finds the server (§2.3 or §2.4); the app reads `GET <server>/api/v1/server`, pins
   `plex_machine_id`, and checks `setup`, `native_signin` and `min_app_version` first (§6.3).
7. `PlexTV` mints a second, fresh pin and links it to the account with `PUT https://plex.tv/api/v2/pins/link`
   (body `code=<pin code>` form-encoded with `Content-Type: application/x-www-form-urlencoded`, headers
   `X-Plex-Token: <account token>`, `X-Plex-Product: Plex SSO` and the per-install `X-Plex-Client-Identifier`;
   verified: `plexapi/myplex.py:122, 1144-1155` sends exactly this). Gate (10-decision-record.md
   §6.1): whether a strong pin links, and whether a linked pin's token is readable more than once, is
   confirmed by a human step in Phase 1 before this step is relied on.
8. `SignIn` enters `exchanging`; app posts `POST /api/v1/auth/plex` with
   `{"pin": {"id", "client_id"}, "device": {"id", "name", "model", "os", "app"}}` over HTTPS, or plain HTTP
   only to a private address the person confirmed (§2.3 step 5).
9. Server applies the exchange limiter: 12 per `device.id` per 10 minutes, 40 overall per 10 minutes, 6 per
   Plex account per hour once identity is known; `pin_allowed()`'s caller-address rule (`api/routes.py:980-1002`)
   is not applied here (10-decision-record.md §5 item 5).
10. Server reads the pin with `read_pin(pin_id, client_id)`: `GET https://plex.tv/api/v2/pins/<id>` with
    `X-Plex-Client-Identifier: <client_id>` and the server's usual headers (`api/plex_auth.py:32-39`, as
    `check_pin()` does at `:140-145`). It answers `401 pin_invalid` when plex.tv answers non-2xx or the pin's
    `expiresIn` is not positive, and `409 pin_not_linked` when `authToken` is absent (03-server-api.md §3.2).
    `pin_known()` and the `auth_pins` table are not consulted; they hold only pins the web flow minted
    (`remember_pins()` is called from `/auth/start` alone, `api/routes.py:1074`).
11. Server runs the unchanged chain: `plex_identity` (`api/plex_auth.py:148-159`) → `401 plex_token_invalid`;
    `authorise` against `allowed_users()` (`:162-200`, `:237-245`) → `403 not_shared`; `server_access_token`
    (`:203-234`) → `403 invite_pending` when `None`.
12. Server upserts the `sessions` row on `device_id` with `kind = 'ios'`, stores `sha256(token)`, encrypts the
    per-server token with Fernet as today (auth-and-tiers.md §3.4), discards the account token, and answers
    `{session: {token, expires_at}, user: {id, username, email, thumb, role}, server: <ServerInfo>}`.
13. App stores the session token in the Keychain under the machine id, loads `/persona`, `/themes`,
    `GET /api/v1/auth/session` and `/request/showcase`; `SignIn` is `signedIn`.
14. App asks for notification permission in context with one sentence saying what it is for, then runs
    §2.12 steps 1-4.

GAP: `POST /api/v1/auth/plex` needs error codes for a pin past `PIN_LIFE` and for a pin with no `authToken`
yet; neither is in design-A's list (`bad_request`, `plex_token_invalid`, `not_shared`, `invite_pending`,
`plex_tv_unreachable`). 03-server-api.md §3.2 names them: `401 pin_invalid` (plex.tv answered non-2xx for the
pin, or the pin has expired) and `409 pin_not_linked` (the pin exists but carries no `authToken` yet); Phase 1
tests both.

### 2.2 Owner sign-in

1. Steps 1-13 of §2.1, identically; nothing in the request says "owner".
2. Server derives `role` from the roster: the owner is whichever plex.tv account holds `plex.token`
   (`api/plex_auth.py:174-175`, auth-and-tiers.md §2.1); the app never claims a role, and `session_user()`
   re-derives it on every request (`:343-377`, auth-and-tiers.md §2.3), so a demotion applies within 600 s.
3. App reads `role == "owner"` and shows the Console tab; whether it opens is `OwnerEntitlement`'s decision
   (§2.14), and the Paywall is not shown until the household side has worked once.
4. The Server screen shows the pairing card, the invite link and QR (§2.4), the relay state from
   `GET /api/v1/owner/status`, and the remote-reach guide pointing at the setup page.

### 2.3 LAN probe and confirmation

1. Trigger: no candidate URL is known for a server the account can see, or the Home-network toggle is on
   (default on for owners and for anyone whose first probe succeeded, off otherwise).
2. `Pairing` takes each `connections[]` entry with `local == true` for that `clientIdentifier` from §2.1
   step 5; it never scans a range or a broadcast address. Gate (10-decision-record.md §6.6):
   `NSLocalNetworkUsageDescription` is present and the probe targets only these addresses.
3. For each address it requests `http://<address>:5050/api/v1/server` with a 1.5 s timeout; plain HTTP only
   because the address is RFC 1918 or link-local (`NSAllowsLocalNetworking`); the port is a setting for
   owners who changed `flask.port` (`config.py:69`).
4. A candidate is accepted when `plex_machine_id` equals that server's `clientIdentifier`: discovery, not
   trust (10-decision-record.md §5 item 4). HTTPS entries in the answer's `urls.remote` rank above plain HTTP.
5. Before the first exchange with a server not reached through an invite, the app shows `server_name`,
   `app_name` and the address and asks for confirmation; only then does §2.1 step 8 run.
6. On later launches the app tries the stored local URLs (1.5 s each) then the stored remote URLs with no
   probe. The plex.tv probe runs only when the Home-network toggle is on and no stored URL answered. A probed
   address that is not already in `server.<id>.urls.local` is a candidate only: the confirmation card of step 5
   is shown again before any request carrying the bearer, and the address joins the stored set only on
   confirmation. `urls` from a `GET /api/v1/server` answer replaces the stored sets only when that answer was
   received over a URL already in the stored set (so an owner who changes tunnel re-invites nobody); a URL that
   stops answering with the pinned machine id is dropped, never trusted.

### 2.4 Invite and QR pairing

1. The owner's Server screen (or the setup page's last step) renders
   `https://push.<domain>/join#v=1&m=<machine_id>&l=<urlencoded local>&r=<urlencoded remote>&n=<name>` as a
   link and as a QR; the fragment never reaches the relay's web server.
2. Member taps the link (universal link via Associated Domains and the relay's AASA file) or scans the QR
   from Welcome's "I have an invite" (VisionKit `DataScannerViewController`, no dependency). Without the app
   installed, `GET /join` sends the person to the App Store.
3. `Pairing` parses the fragment, rejects `v` other than 1, refuses an invite whose `m` is not among the
   `clientIdentifier`s of the signed-in account's `GET /api/v2/resources` once §2.1 step 5 has run ("That
   invite is for a server your Plex account cannot see yet; accept the Plex share first"), and refuses an
   invite whose `m` is already stored with `confirmed` or `invited` set ("You are already connected to this
   server"; changing addresses is Settings' "Type an address", which confirms). Otherwise it records `m`, `l`,
   `r`, `n` as the pinned id, the local set, the remote set and the display name. A QR scanned in person marks
   the server invited (no confirmation step); a tapped universal link shows the confirmation card of §2.3
   step 5 with the wording "Someone sent you a link to <server_name> at <address>. Connect?" before any
   exchange.
4. App requests `GET /api/v1/server` on the best-ranked URL and requires `plex_machine_id == m`; a mismatch
   drops that URL and tries the next. Then §2.1 from step 1, with step 6 already satisfied.

### 2.5 A search

1. Home's search field submits; the Results store cancels any in-flight search task and starts one.
2. `ServerClient` sends `POST /request/search {"query", "include_missing"}` with a 60 s timeout
   (household-api.md §3.1 warns of tens of seconds).
3. Server guard: `current_user()` reads the bearer; `session_user()` checks the hash, the sliding 180-day
   life for `ios` rows, roster membership (`api/plex_auth.py:343-377`), and updates `last_seen`.
4. Route order (`api/routes.py:1385-1574`): free title and person lookup; else allowance reserve; mood
   search; `unavailable` capture with a `file_missing` push to the owner through `send_event()`; did-you-mean
   and `close` for `title_missing`; TMDb `missing` when asked; refund when `translated_by != "model"`.
5. Response is the union keyed on `kind` (`title`, `cheeky`, `missing_only`, `mood`, `title_missing`); the
   Kit decodes it as `SearchResult` with `matches` as `Film` carrying either shape's optionals.
6. `missing_files` is absent for a non-owner session (stripped by the blueprint); the owner's client ignores
   it too. Results renders the five kinds, sort chips and the selection bar; `thumb` paths render through
   `GET /wanted/thumb?path=&width=&height=` (`api/routes.py:2077-2111`) via `URLCache`.
7. `429` → `.rateLimited(message, hint)`; the hint ("You can still search for a film by name…") is shown verbatim.

### 2.6 Adding to a playlist

1. Selection bar → "Add to playlist"; the Playlists store already holds `GET /request/playlists`
   (`{"playlists": [{title, items}], "default"}`) or its `200 {"error", "playlists": []}` form.
2. App posts `POST /request/playlist {"rating_keys": [...], "playlist": "<name or empty>"}`; the owner may add
   `"for_user": "<username>"` from `GET /members` (household-api.md §3.7).
3. Server decrypts the caller's per-server token from the session row and writes to the caller's own Plex
   account (`api/user_library.py:1-9`); with `for_user` the owner's request uses `token_for(db, for_user)`
   (`api/plex_auth.py:401-419`).
4. Responses: created `{playlist, added, created: true}`; existing `{playlist, added, already_there,
   created: false}`; gifting adds `for_user`; `200 {"error": "none of those films could be found"}` →
   `.serverSaidNo`; `409` when the member never signed in or their sign-in expired → `.conflict`.
5. The store refreshes `GET /request/playlists` and, for the open list, `GET /request/playlist/items?name=`.
   Removal is `POST /request/playlist/remove`; auto-build is `POST /request/playlist/auto`, allowance-counted.

### 2.7 Asking for a film

1. From a `title_missing` result (a did-you-mean guess), a Discover item, or a typed title, the app posts
   `POST /request/wanted {"title": "Title (Year)", "kind": "film" | "show", "season": n?}`.
2. Server applies `wanted_add_allowed()` (40 per user per hour, `api/routes.py:1015-1024`); over → `429`
   with `rate_limited: true`.
3. Film path: `search_and_capture` with the owner's token (`scanner/wanted_search.py:351-402`); a row is
   inserted only when nothing playable exists. Show path: no Plex search; row inserted with `kind`, `season`.
4. On insert the row gains `requested_by`, `requested_by_id`, `notes = "Requested by <username>"` and, new,
   `tmdb_id` when the ask came from a guess; `send_event("requested", owner)` runs unless the asker is the
   owner (`api/routes.py:2027-2045`), writing an `events` row and fanning out (§2.12 steps 5-9).
5. Response is the search dict with `wanted` embedded; the app shows "we already have that" on `found`,
   "asked for" on `added_to_wanted`, else a note keyed on `wanted.status` (household-api.md §3.2).
   WantedList refreshes `GET /request/wanted/list` and groups by `status` as Waiting / Arrived / Set aside.

GAP: `POST /request/wanted` must accept an optional `tmdb_id: int` so the app can pass the guess's id; the
ADR says `wanted.tmdb_id` is "set from did-you-mean and discover guesses" but no body field exists today
(household-api.md §3.2). 03-server-api.md defines it; Phase 2 adds it with a test.

### 2.8 "It arrived" via the webhook

1. Plex Media Server (Plex Pass) posts `POST /plex/webhook?key=<plex.webhook_key>` with multipart `payload`.
2. Server checks `key` against `plex.webhook_key` (new; the loopback door is off in the image) and ignores
   anything but `library.new` (`api/routes.py:669-695`).
3. `settle_wanted()` (`api/routes.py:625-656`) runs `recheck()`, which now matches each `wanted` row by Plex
   `Guid` (`tmdb://`, `imdb://`, `tvdb://`) when `tmdb_id` is set, else by folded title and year; searches
   show sections for `kind == "show"` rows and checks the season is present (verified: `plexapi/video.py:437`,
   `plexapi/media.py:937`).
4. Each match flips the row to `acquired` and calls `send_event("arrived", requested_by_id, film=name)`;
   the loop tries every device kind of the asker before falling back to the owner (the `settle_wanted()` fix).
5. `send_event()` writes the `events` row, delivers by web push to `push_subscriptions` rows, then by
   `api/relay.py` to `native_devices` rows (§2.12).
6. The asker's phone receives the push (`ev: "arrived"`, `ref: "wanted:<id>"`) or, in the polling state, the
   event on its next `GET /api/v1/changes`; the tap opens WantedList (owner: Requests) at that row.

### 2.9 "It arrived" via the settle scheduler

1. `main.py` starts one background thread when `app.settle_every_minutes` > 0 (image default 120 via
   `PLEXGET_SETTLE_MINUTES`; venv installs default to 0). Without it nothing shipped ever sends `arrived` on
   a non-Plex-Pass install (push-and-notifications.md §3.3).
2. Every interval the thread calls `settle_wanted()` exactly as §2.8 step 3 onward; the pytest gate drives
   it with a fake clock and `settle_wanted` stubbed.
3. `GET /api/v1/owner/status` reports `settle: {every_minutes, last_run_at}` for the Console's Server screen;
   the copy says "the server checks every two hours" on a non-Plex-Pass install.

### 2.10 A message to the owner

1. Report's "Something wrong?" (or the Results footer) posts `POST /request/message {"body", "context"}`;
   `context` is the current search text.
2. Server applies `MESSAGES_PER_HOUR = 10` (`api/routes.py:1028-1041`), inserts the `messages` row, and
   calls `send_event("message", owner)` unless the sender is the owner (`api/routes.py:1888-1895`), which
   writes an `events` row with `recipient_id = owner` and fans out (§2.12).
3. Response `{message, sent}`; the app says "Their phone has been told" when `sent > 0`, else "It's on their
   console", as the page does (household-api.md §3.14).
4. Owner's Inbox lists `GET /messages?unread=1` and marks with `POST /messages/<id>/read`; the badge is
   `unread_messages` from `GET /api/v1/changes`, present only for the owner.

### 2.11 An announcement

1. Console → Announce posts `POST /announce {"body"}` (280 characters, `api/routes.py:1839-1864`).
2. Server inserts the `announcements` row and calls `broadcast(title=app.name, body, url="/request",
   tag="note-<id>", exclude_user=owner)`, which writes one `events` row with `recipient_id NULL` and
   `actor_id = owner`, then delivers by web push and by relay to every device except the poster's.
3. Response `{note, sent}`; Announce lists the three newest live notes from `GET /request/announcements`
   (the owner may call household routes) and retires with `POST /announce/<id>/retire`.
4. Household phones receive `ev: "announcement"`, `ref: "note:<id>"`; the tap opens Bell. Read state is per
   device in `UserDefaults` (§4.2), as the web keeps it in `localStorage` (push-and-notifications.md §5).

### 2.12 Relay push end to end

1. After permission, `PushBridge` calls `registerForRemoteNotifications()` and receives the APNs token.
2. App posts `POST /v1/devices {"apns_token", "environment": "production" | "sandbox", "app"}` to the relay
   → `{handle, device_secret}`; both go in the Keychain (§4.1). Limited to 10 per IP per hour.
3. App posts `POST /api/v1/push/devices {"device_id", "relay_handle", "environment", "app"}` to the server →
   `{"delivery": "relay" | "polling"}`; at most five rows per user; the server never sees the secret.
4. `polling` → the app schedules `Background` (§2.13) and keeps the registration so the same handle starts
   receiving when the owner subscribes later. APNs token changes repeat steps 2-3.
5. A server event occurs (§2.7 to §2.11); `send_event()`/`broadcast()` writes the `events` row, delivers
   web push as today, then for each `native_devices` row of the recipient calls `relay.send(handle, event,
   title, body, collapse_id, ref, srv, actor)` (05-push-relay.md §8; `actor` is the `plex_user_id` that caused
   the event, budgeted at 60 relay sends an hour, 09-security.md §2.2).
6. `api/relay.py` posts `POST /v1/push` with `Authorization: Bearer <server_key>` and
   `{handle, event, title, body, collapse_id, data: {ref, srv}}`; `collapse_id` reuses the per-row `tag`
   (`arrived-<id>`, `request-<id>`, `message-<id>`, `note-<id>`, push-and-notifications.md §2.3).
7. Relay checks the key (`expires_at` + 16 days) and the quotas (1,000 per key per day, 60 per key per
   minute, 100 per handle per day), looks up the handle's APNs token and environment, and sends over HTTP/2
   with `apns-topic` = bundle id, `apns-push-type: alert`, `apns-priority: 10`, `apns-collapse-id`. Gate
   (10-decision-record.md §6.5): header names and the `410`/`400` semantics are checked against Apple's
   provider documentation before Phase 6 writes this.
8. Payload `{"aps": {"alert": {"title", "body"}, "sound": "default", "thread-id": "<srv>"}, "ev", "ref", "srv"}`;
   the relay answers `{status: sent | unregistered | rate_limited | expired}` and stores no body.
9. Server: `unregistered` → delete the `native_devices` row; `expired` → mark `data/relay.json` inactive →
   `push.delivery: "polling"`; `rate_limited` → log and continue.
10. `PushBridge` receives the notification; on tap it routes `wanted:<id>` → WantedList (owner: Requests),
    `message:<id>` → Inbox, `note:<id>` → Bell; `Changes` records `(srv, ev, ref)` for de-duplication. A push
    whose `srv` differs from `server.current` is shown as received but is not routed and is not recorded.

### 2.13 Polling fallback via `/api/v1/changes`

1. `Background` registers `BGAppRefreshTask` with identifier `AppIdentity.refreshTaskIdentifier` (listed in
   `BGTaskSchedulerPermittedIdentifiers`) at launch, earliest begin 15 minutes; iOS sets the real cadence.
2. On each run, and on every foreground, the app reads `cursor` from `UserDefaults` (§4.2) and calls
   `GET /api/v1/changes?since=<cursor>&limit=100`.
3. Server returns `{events: [{id, at, kind, ref, title, body}], cursor, unread_messages (owner only),
   wanted_counts}` from the `events` table for this user (own `recipient_id` plus `NULL` broadcasts not by
   this actor); rows older than 30 days are purged on insert.
4. `Changes` posts one `UNNotificationRequest` per event through `Notifier`, using the event's `title`/`body`
   and `ref` as the destination, skipping events whose `(srv, kind, ref)` was already shown by push and
   skipping `test` events; it stores the new `cursor`.
5. The task calls `setTaskCompleted(success:)` and schedules the next run; the expiration handler cancels the
   in-flight request. When `push.delivery` is `relay` the task stays registered but posts nothing push
   already delivered.
6. The only copy about latency is "notifications may arrive late", shown in Bell and Settings in the
   polling state and nowhere else.

### 2.14 Subscription purchase and server-key issuance

1. Console tab with `OwnerEntitlement.active == false` shows Paywall: `owner.yearly` first (7-day introductory
   trial) and `owner.monthly`, price, period, renewal terms, privacy and terms links, "Restore purchases".
2. `Store` loads `Product.products(for:)`, runs `purchase()`, accepts only `VerificationResult.verified`,
   calls `Transaction.finish()`, and sets `OwnerEntitlement { active, expiresAt, inGrace }`.
3. `Store` posts the transaction's `jwsRepresentation` to the relay: `POST /v1/server-keys` →
   `{server_key, expires_at}`, bound to `originalTransactionId`; a forged JWS is refused (Phase 7 gate).
4. App posts `POST /api/v1/owner/relay {"server_key", "relay_url", "expires_at"}`; the server writes
   `data/relay.json` (0600) and answers `{"delivery": "relay"}`; `GET /api/v1/server` now reports
   `push.delivery: "relay"` and every registered handle starts receiving on the next event.
5. `Transaction.updates` keeps listening for the app's lifetime; each renewal re-posts the JWS without
   `rotate` (the second entitlement path: it extends `expires_at` and issues no key, 05-push-relay.md §4.3).
   A new key (`rotate: true`) is requested only when `GET /api/v1/server` reports `polling` after the server
   marked the key dead, when no `relay.server-key-id.<machine_id>` exists, or after "Delete my relay data";
   the returned key is held in the Keychain as `relay.server-key.pending.<machine_id>` until
   `POST /api/v1/owner/relay` answers `200`, retried on every launch and foreground.
6. Independently, Apple posts App Store Server Notifications V2 to `POST /v1/apple/notifications`; the relay
   verifies with Apple's App Store Server Library and updates `server_keys.expires_at` (renewal, expiry,
   refund, grace). Gate (10-decision-record.md §6.4): the library's package name and field names are read
   from its README before Phase 6.

GAP: the ADR's `POST /v1/server-keys` response `{server_key, expires_at}` carries no `id`, yet
`DELETE /v1/server-keys/<id>` is keyed on one. 05-push-relay.md defines the response as
`{id, server_key, expires_at}` and the app stores `id` (§4.1).

### 2.15 Lapse

1. The subscription expires, is refunded, or enters billing retry; Apple posts the V2 notification to the
   relay, which sets `server_keys.expires_at`; the key is honoured until `expires_at` plus 16 days.
2. `OwnerEntitlement` reflects `Product.SubscriptionInfo.Status` (grace and billing retry count as active);
   when `currentEntitlements` no longer carries the product, the Console tab shows Paywall. Household
   screens are untouched for the owner and everyone else.
3. After the grace, the relay answers `expired` to `POST /v1/push`; `api/relay.py` marks `data/relay.json`
   inactive and `GET /api/v1/server` reports `push.delivery: "polling"`.
4. Household apps notice on their next `GET /api/v1/server` read and let `Background` post local
   notifications again (§2.13); nothing is deleted. Resubscribing repeats §2.14 steps 2-4; push resumes
   within one owner app launch.

### 2.16 Sign-out and per-device revocation

1. Settings → "Sign out": the app calls `DELETE /api/v1/push/devices/<device_id>` then
   `DELETE /api/v1/auth/session`; the server sets `sessions.revoked_at` on the bearer's row and answers `204`.
2. The app wipes that server's Keychain items (§4.1), caches (§4.3) and `UserDefaults` block. It keeps the
   plex.tv account token, the client identifier and the device id only when the person chose "keep me signed
   in to Plex"; otherwise it wipes those too. The relay registration is always ended: the app calls
   `DELETE /v1/devices/<handle>` with the device secret, clears `relay.handle` and `relay.device-secret`, and
   registers afresh (new handle) after the next sign-in, so a server the person left holds a dead handle.
3. Settings → Sessions lists `GET /api/v1/auth/sessions` (own rows; the owner sees every row with `username`),
   each with `device_name`, `platform`, `created_at`, `last_seen`, `current`; revoking another device calls
   `DELETE /api/v1/auth/sessions/<id>`; "sign out everywhere" calls `DELETE /api/v1/auth/sessions`.
4. The revoked device's next request gets `401`; the app reads the code and shows Welcome instead of
   silently re-exchanging (GAP below); its Keychain session item is cleared.
5. A withdrawn Plex share cuts every session of that account within 600 s through the roster check
   (auth-and-tiers.md §2.3); the rows stay until purge.
6. Relay records are separate: "Unlink and delete my relay data" on the Console's Server screen calls
   `DELETE /api/v1/owner/relay`, `DELETE /v1/server-keys/<id>` and `DELETE /v1/devices/<handle>`; a
   member's Settings offers the device delete alone.

GAP: the silent re-exchange on `401` (10-decision-record.md §5 item 5, once per five minutes per device)
would undo an owner's revocation unless the `401` body distinguishes the cases. 03-server-api.md defines
`code: "session_revoked"` (row has `revoked_at`), `code: "session_expired"` (row past its life or absent)
and `code: "plex_token_dead"` (today's `expired_session()`, `api/routes.py:1168-1184`); the app re-exchanges
silently only for `session_expired` and `plex_token_dead` and shows Welcome for `session_revoked`. Note:
design-A §3.4 has sessions expiring 90 days from creation with a re-exchange seven days before; the ADR's
sliding 180-day `ios` rows replace that, so the app has no scheduled re-exchange, only the `401` path.

## 3. Data model additions

All on the server unless marked relay. Types are SQLite's; `TIMESTAMP` values are `CURRENT_TIMESTAMP` text
(`YYYY-MM-DD HH:MM:SS` UTC) as everywhere in this database (owner-api.md §4.10). Migrations follow the
`wanted` pattern at `db/database.py:152-157` (`PRAGMA table_info`, then `ALTER TABLE ADD COLUMN`).

### 3.1 `sessions` (existing, `api/plex_auth.py:302-320`), new columns

| Column | Type | Meaning |
|---|---|---|
| `device_id` | `TEXT UNIQUE` | The app's per-install UUID; `NULL` for web rows; upsert key for the exchange |
| `device_name` | `TEXT` | e.g. "Jules's iPhone"; shown in Sessions |
| `platform` | `TEXT` | the `device.os` string, e.g. `"iOS 18.6"`; `NULL` for `web` rows (03-server-api.md §3.5) |
| `app_version` | `TEXT` | `"1.0 (42)"` |
| `kind` | `TEXT NOT NULL DEFAULT 'web'` | `web` (fixed 90 days from `created_at`) or `ios` (valid while `last_seen` is within 180 days) |
| `revoked_at` | `TIMESTAMP` | Set by the `DELETE /api/v1/auth/session[s]` routes; a set value fails lookup with `session_revoked` |
| `token_hashed` | `INTEGER NOT NULL DEFAULT 0` | `1` for rows written after Phase 1, whose `token` is `sha256(token)`; `0` for legacy raw rows |

`token` now holds `sha256(token)` for new rows; `session_user()` looks up `sha256(presented)` among rows with
`token_hashed = 1`, then the raw value among rows with `token_hashed = 0` only, so a stored digest never
authenticates as a bearer (09-security.md §3). The purge in `start_session()` (`api/plex_auth.py:329-332`) applies each kind's rule.

### 3.2 `native_devices` (new)

| Column | Type | Meaning |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | |
| `plex_user_id` | `TEXT NOT NULL` | Same string as `sessions.plex_user_id` (push-and-notifications.md §7.4) |
| `username` | `TEXT` | For the Notifications screen's device counts |
| `device_id` | `TEXT NOT NULL UNIQUE` | Matches `sessions.device_id` |
| `relay_handle` | `TEXT NOT NULL` | The relay's opaque handle; never the device secret |
| `environment` | `TEXT NOT NULL` | `production` or `sandbox` |
| `app_version` | `TEXT` | |
| `created_at` | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | |
| `last_sent` | `TIMESTAMP` | |
| `failures` | `INTEGER DEFAULT 0` | Incremented on non-`sent`, non-`unregistered` answers; never disables a row |

Separate from `push_subscriptions` because its `endpoint`/`keys_json NOT NULL` columns cannot hold a handle
(push-and-notifications.md §6.3). Insert refuses a sixth row per `plex_user_id` with `409`.

### 3.3 `events` (new, append-only)

| Column | Type | Meaning |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | The polling cursor |
| `at` | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | Serialised to the app as ISO 8601 `Z` |
| `kind` | `TEXT NOT NULL` | `arrived`, `requested`, `file_missing`, `message`, `announcement`, `test` |
| `recipient_id` | `TEXT` | Plex account id; `NULL` for a broadcast |
| `actor_id` | `TEXT` | Who caused it; excluded from their own broadcast |
| `ref` | `TEXT` | `wanted:<id>`, `message:<id>`, `note:<id>` or `NULL` (`file_missing`, `test`) |
| `title` | `TEXT NOT NULL` | The rendered title after `str.format` (push-and-notifications.md §2.1) |
| `body` | `TEXT NOT NULL` | The rendered body |

Written inside `send_event()` and `broadcast()` before any delivery; rows older than 30 days purged on insert.

### 3.4 `wanted` (existing, `db/database.py:131-171`), new column

| Column | Type | Meaning |
|---|---|---|
| `tmdb_id` | `INTEGER` | Set from a did-you-mean or discover guess; `recheck()` matches `Guid tmdb://<id>` first |

### 3.5 Files and configuration on the server

| Item | Where | Contents |
|---|---|---|
| `data/relay.json` | `PLEXGET_DATA`, mode 0600 | `{"server_key", "relay_url", "expires_at", "active": bool}` |
| `data/client_id` | `PLEXGET_DATA` | The per-install `X-Plex-Client-Identifier`; the old `uuid5` is kept where `data/` already exists (gate, 10-decision-record.md §6.3) |
| `config.yaml` keys | `app.urls: {local: [], remote: []}`, `app.loopback_owner`, `app.settle_every_minutes`, `app.native_signin` (default `true`), `app.min_app_version` (default `"1.0.0"`), `plex.webhook_key`, `notifications.announcement: {enabled, title, body}` | Read by `config.py`; environment `PLEXGET_CONFIG`, `PLEXGET_DATA`, `PLEXGET_LOOPBACK_OWNER`, `PLEXGET_SETTLE_MINUTES` override |
| `tests/fixtures/api/*.json` | this repository | One file per `/api/v1` and household route response, written by pytest, vendored by the iOS repository |

### 3.6 Relay tables (`push-relay`, SQLite)

The ADR names the tables; 05-push-relay.md carries the DDL. Minimum content implied by the endpoints:
`devices` (`handle TEXT PRIMARY KEY`, `secret_hash TEXT`, `apns_token TEXT`, `environment TEXT`,
`app_version TEXT`, `created_at`, `last_push_at`); `server_keys` (`id TEXT PRIMARY KEY`, `key_hash TEXT`,
`original_transaction_id TEXT UNIQUE`, `product_id TEXT`, `expires_at`, `revoked_at`); `apple_notifications`
(`notification_uuid TEXT PRIMARY KEY`, `original_transaction_id`, `type`, `subtype`, `received_at`);
`quota_counters` (`scope TEXT`, `key TEXT`, `window_start`, `count INTEGER`). No column ever holds a title,
a body, a Plex token, a session token or a server address.

## 4. State the app keeps and where

### 4.1 Keychain (`SecretStore` implementation `Keychain`)

Service `AppIdentity.keychainService` (the bundle identifier); every item
`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`; no iCloud sync; no access group in v1. Account names:

| Account | Scope | Value | Written | Cleared |
|---|---|---|---|---|
| `plex.client-identifier` | install | UUID sent as `X-Plex-Client-Identifier` | first launch | never (a new one would change the plex.tv device) |
| `plex.account-token` | install | plex.tv account token | §2.1 step 5 | sign-out without "keep me signed in to Plex"; plex.tv `401` |
| `device.id` | install | UUID sent as `device.id` | first launch | never |
| `relay.handle` | install | relay handle | §2.12 step 2 | sign-out (§2.16 step 2); device delete |
| `relay.device-secret` | install | relay device secret | §2.12 step 2 | sign-out (§2.16 step 2); device delete |
| `session.<machine_id>` | server | bearer session token | §2.1 step 13 | sign-out; `401` |
| `relay.server-key-id.<machine_id>` | server, owner | the `id` from `POST /v1/server-keys` | §2.14 step 3 | "delete my relay data" |
| `relay.server-key.pending.<machine_id>` | server, owner | an issued server key not yet acknowledged by the server | §2.14 step 5 | on `200` from `POST /api/v1/owner/relay` |

Nothing else is a secret. The Kit reads and writes these only through `SecretStore`; `Demo` never touches them.

### 4.2 UserDefaults (standard suite)

| Key | Type | Meaning |
|---|---|---|
| `server.current` | String | Machine id of the paired server; empty means Welcome |
| `server.<machine_id>.name` | String | `server_name` for the confirmation and Settings |
| `server.<machine_id>.urls.local` | [String] | Ranked local candidates (§2.3) |
| `server.<machine_id>.urls.remote` | [String] | Ranked remote candidates |
| `server.<machine_id>.invited` | Bool | Reached through a scanned QR, so no confirmation step; a tapped link still confirms (§2.4 step 3) |
| `server.<machine_id>.confirmed` | Bool | The confirmation in §2.3 step 5 was given |
| `server.<machine_id>.homeNetwork` | Bool | The Home-network toggle; default per 10-decision-record.md §4 |
| `server.<machine_id>.delivery` | String | Last `push.delivery` seen (`relay`/`polling`) |
| `changes.<machine_id>.cursor` | Int | Last `cursor` from `GET /api/v1/changes` |
| `push.seen` | [String] | Ring of the last 200 `"<srv>|<ev>|<ref>"` strings for de-duplication |
| `bell.<machine_id>.seenNotes` | [Int] | Announcement ids read on this device |
| `notifications.askedAt` | Date | When the in-context permission prompt was shown |
| `signIn.lastSilentExchangeAt` | Date | Enforces once per five minutes per device |
| `look.selected` | String | The `Look.id` of the chosen look (`plex`, `blockbuster`, `netflix`, `bruce`, or `c-<slug>` for a custom look; 04-ios-app.md §2.8) |
| `server.<machine_id>.email` | String | `user.email` from the §2.1 exchange, shown in Settings; wiped on sign-out (03-server-api.md §3.3) |
| `server.<machine_id>.thumb` | String | `user.thumb` from the exchange, the avatar URL shown in Settings; wiped on sign-out |
| `demo.active` | Bool | Demo mode survives relaunch until "Leave the demo" |

Every key is declared once in `App/Settings/DefaultsKeys.swift`. `UserDefaults` is a required-reason API;
`PrivacyInfo.xcprivacy` declares it with reason `CA92.1` (unverified; confirmed against Apple's list in Phase 9).

### 4.3 On-disk caches (`Library/Caches/<machine_id>/`)

| File | Source | Used for |
|---|---|---|
| `server.json` | last `GET /api/v1/server` | Launch before the first network answer; `min_app_version` check |
| `persona.json` | `GET /persona` | Home's greeting and faces offline |
| `themes.json` | `GET /themes` | Looks picker offline |
| `showcase.json` | `GET /request/showcase` | Home reels before refresh |
| `wanted-list.json` | `GET /request/wanted/list` | WantedList before refresh |
| `announcements.json` | `GET /request/announcements` | Bell before refresh |

Artwork (`/wanted/thumb`, `/themes/art/*`, `/icons/*`, TMDb `poster` URLs) goes through one shared
`URLCache` (50 MB disk) honouring the server's `Cache-Control` (`max-age=86400` on thumbs, `600` on art).
Caches hold no secret and no `missing_files`, are deleted on sign-out, and nothing is written under
`Documents`. The vendored fixtures are a bundle resource of the `PlexGetKit` library target (read by `Demo` and
the tests through its `Bundle.module`, 07-build-phases.md §5 Phase 0), not caches.

## 5. Concurrency model

1. **One actor for the network.** `actor ServerClient` owns `baseURLs`, the bearer token, the `Transport`
   and the request loop; every call is `await client.perform(Endpoint)`. Its state is mutated only inside the
   actor; the token is set by `SessionStore` after the exchange and cleared on `401`.
2. **`Transport` is the only I/O seam.** `protocol Transport: Sendable { func send(_ request: URLRequest)
   async throws -> (Data, HTTPURLResponse) }`. The app target's implementation wraps one `URLSession`
   (`waitsForConnectivity` false; timeouts 60 s for search routes, 15 s otherwise, 1.5 s for probes); tests
   inject fakes. No `URLSession` exists anywhere else; `PlexTV` takes a `Transport` too. The configuration
   sets `httpShouldSetCookies = false`, `httpCookieAcceptPolicy = .never`, `httpCookieStorage = nil` and
   `urlCredentialStorage = nil`, so no cookie is ever stored or sent and no credential is ever cached;
   `AppTests.URLSessionTransportTests.testNoCookiesNoCredentials` asserts the configuration.
3. **Stores are MainActor.** Each feature folder has one `@MainActor @Observable final class <Feature>Store`.
   Views read stores; stores call the actor; results land on the main thread without `DispatchQueue.main`.
   `DispatchQueue`, `@unchecked Sendable`, `nonisolated(unsafe)` and `Task.detached` are forbidden, except
   `Task.detached` inside `Background` for the refresh run.
4. **Models are `Sendable` value types.** Every `Models` type is a `struct` or `enum` with `Sendable`
   synthesised; the error enums are `Sendable`; nothing crosses an actor boundary that is not.
5. **One in-flight task per store operation.** A store keeps `private var task: Task<Void, Never>?` per
   operation, cancels it before starting another (search, showcase refresh, changes fetch), and calls
   `Task.checkCancellation()` after each await so a stale result never overwrites a newer one.
6. **`SignIn` is pure.** It takes a `Clock` and returns the next state; the Welcome store drives it, so every
   transition is tested on Linux with a manual clock and no network.
7. **Platform callbacks hop to MainActor.** `PushBridge`'s delegate methods are `@MainActor`; the APNs token
   and notification taps are forwarded to `SessionStore` and the router as values. `BGTaskScheduler`'s
   handler runs on its own queue; `Background` starts one `Task` using `ServerClient` and `Notifier` and
   sets the task's expiration handler to cancel it.
8. **`Store` listens once.** `Transaction.updates` is consumed by one long-lived `Task` created at launch;
   `OwnerEntitlement` is `@MainActor @Observable`; the `Entitlements` protocol the Kit sees exposes
   `isOwnerActive` and `serverKeyJWS() async -> String?` only.
9. **`Notifier` and `SecretStore` are `Sendable` protocols with `async` methods**; the Keychain
   implementation serialises on its own actor. **Demo mode swaps the client, not the stores**:
   `DemoServerClient` conforms to the same `ServerClientProtocol`; the stores do not know which they hold.

## 6. Error taxonomy and what each screen does

### 6.1 The Kit's errors

`enum ServerError: Error, Sendable`, produced only by the mapper in `ServerAPI`:

| Case | From | Notes |
|---|---|---|
| `.signInRequired(code: String?)` | any `401` | Regardless of `auth_required` (household-api.md §1: several `401`s lack it); `code` per the §2.16 GAP |
| `.ownerOnly` | `403` with `"that part is the owner's"` or `"owner only"` | A role change since launch |
| `.notShared` | `403 code: not_shared` | Exchange only |
| `.invitePending` | `403 code: invite_pending` | Exchange only |
| `.rateLimited(message, hint?, retryAfter?)` | `429` whose body has `rate_limited` or `retry_after` | `hint` only from `/request/search`; `retry_after` only from the exchange |
| `.plexUnavailable(message)` | `503` | `plex_trouble()` (`api/routes.py:1156-1166`) |
| `.serverSaidNo(message)` | `200` whose body carries `error` | `/request/playlists`, `/request/showcase`, `/request/suggestions`, `/request/nudges`, `/members/<u>/picks`, `/request/playlist/remove`, `/request/playlist` (household-api.md §6.7) |
| `.badRequest(message)` | `400` | Copy from the body, verbatim |
| `.notFound(message)` | `404` | `/request/detail`, `/request/playlist/items`, `/request/playlist/auto` |
| `.conflict(message)` | `409` | Gifting a playlist; the sixth push device |
| `.plexTVUnreachable(message)` | `502` | Exchange and `/wanted/thumb` |
| `.serverFault` | `500` from the JSON handler | Never shown raw |
| `.undecodable(route: String, detail: String)` | `DecodingError` | `detail` is `String(describing:)` of the error, so the case is `Equatable` (04-ios-app.md §2.2). Gate (10-decision-record.md §6.8): fixed by a server test and a fixture, never by a lenient guess |
| `.unreachable(code: URLError.Code, description: String)` | transport | Offline, timeout, TLS failure; the code and `localizedDescription`, not the `URLError` itself |
| `.wrongServer(expected, got)` | `plex_machine_id` mismatch | From `Pairing`; the URL is dropped |

`PlexTVError` (`.pinExpired`, `.unauthorised`, `.rateLimited(retryAfter)`, `.unreachable`) comes from
`PlexTV`; `RelayError` (`.unregistered`, `.expired`, `.rateLimited`, `.rejected`, `.unreachable`) from the
relay calls in `PushBridge` and `Store`.

### 6.2 Default handling, every screen

| Error | Default action |
|---|---|
| `.signInRequired` | `session_expired`/`plex_token_dead`, more than five minutes since the last attempt, and the current base URL is `https://`: silent re-exchange (§2.1 steps 7-13), retry once. When the current base URL is plain `http://`, or on `session_revoked`, or otherwise: clear the session item and present Welcome with "Sign in again to carry on"; the person taps Sign in and sees the home-network privacy copy (09-security.md §2.3) |
| `.ownerOnly` | Leave the Console; show Home with "That part is the owner's" |
| `.rateLimited` | Inline notice with the server's `message` and, when present, `hint`; disable the control until `retryAfter` or 60 s |
| `.plexUnavailable` | Inline "Plex is unavailable" with the server's clause; keep cached content visible; Retry |
| `.serverSaidNo` | Show the message where the result would have been; no alert |
| `.badRequest`, `.notFound`, `.conflict` | Show the message inline under the control that caused it |
| `.plexTVUnreachable` | "Couldn't reach plex.tv"; Retry |
| `.serverFault`, `.undecodable` | "Something went wrong on the server" plus the Report path; log the route with `os.Logger` (`privacy: .public` for the route, `.private` for bodies) |
| `.unreachable` | Show cached content with an "Offline" pill; `Pairing` retries the other URL set once |
| `.wrongServer` | Drop the URL; if none left, the Pairing screen |

### 6.3 Conditions from `ServerInfo` (not HTTP errors)

| Condition | Where checked | Action |
|---|---|---|
| `setup == true` | Every `GET /api/v1/server` | "This server is still being set up" with the setup URL; no sign-in |
| `native_signin == false` | Before §2.1 step 7 | "Sign-in from the app is paused; use the web page for now"; existing sessions keep working |
| `min_app_version` > build | Every `GET /api/v1/server` | Full-screen "Update needed" with an App Store link; nothing else |
| `push.delivery` changed | Every `GET /api/v1/server` | Update `server.<id>.delivery`; schedule or relax `Background` |

### 6.4 Screen-specific handling (deviations from §6.2 only)

| Screen | Error | Action |
|---|---|---|
| Welcome | `PlexTVError.pinExpired` | New pin, same screen, no message |
| Welcome | `PlexTVError.rateLimited`, `.rateLimited` | Countdown from `retryAfter`, capped at 120 s as the page does (auth-and-tiers.md §1.6); `SignIn` state `rateLimited(until)` |
| Welcome | `.notShared` | "That Plex account does not have access to this server" with "Try a different account" and the invite option |
| Welcome | `.invitePending` | The server's copy about accepting the Plex invite, with Retry |
| Pairing | `.unreachable` on every candidate | "Couldn't find your server at home" with "I have an invite" and "Type an address" |
| Home | `.serverSaidNo` from `/request/showcase` | Empty reels, greeting still shown; no message (the page behaves so) |
| Home | `.rateLimited` from `/request/nudges` (a `200`) | Nothing shown; nudges are silent |
| Results | `.rateLimited` | The `hint` is the primary line; the free title search stays enabled |
| Results | `.unreachable` mid-search | "The connection dropped; searching again is quick" with Retry (six-hour translation cache, household-api.md §6.11) |
| Detail | `.notFound` | "Not on the shelf"; close the sheet |
| Playlists | `.conflict` | The server's sentence about the member signing in first, verbatim |
| WantedList | `.rateLimited` from `/request/wanted` | "That's a lot of requests in one go" on the Ask button for 60 minutes |
| Bell | `RelayError.unregistered` | Re-register (§2.12 steps 1-3) silently, then show the state |
| Bell | `sent: 0` from `/api/v1/push/test` | "Nothing was sent; is the relay set up?" |
| Looks | any | Fall back to the built-in `plex` palette; never block |
| Report | `.rateLimited` from `/request/message` | Message kept in the field; "Give it an hour" |
| Settings/Sessions | `.notFound` on delete | Refresh the list silently |
| Console/Requests | `.plexUnavailable` on recheck or settle | Inline; the list stays |
| Console/Discover | `.badRequest` "no TMDb API key" | Replace the screen with "Add a TMDb key on the setup page" |
| Console/Overview | `.serverFault` from `/libraries` or `/summary` | Show the JSON handler's message; Overview is empty on a request-only install and says so |
| Console/Server | `RelayError.rejected` on `POST /v1/server-keys` | "Apple didn't recognise that purchase"; offer Restore purchases |
| Console/Server | `RelayError.expired` | The lapse copy and a Paywall link; household unaffected |
| Paywall | StoreKit `.userCancelled` / other | Nothing; stay / Apple's `localizedDescription` with Restore offered |
| Any screen in demo mode | any | Impossible by construction; `DemoServerClient` never throws |

## 7. What is web-only and stays on the web

| Mechanism | Where | Why the app never touches it |
|---|---|---|
| Cookie session `plexops_session` and `/auth/start`, `/auth/poll`, `/auth/me`, `/auth/logout` | `api/routes.py:941-1221` | The app uses bearer sessions and `POST /api/v1/auth/plex`; `/auth/me` costs a Plex probe per call (auth-and-tiers.md §6.14) |
| Home-screen handoff: `/manifest.webmanifest`, `/?handoff=` | `api/routes.py:711-779`, `api/plex_auth.py:255-299` | A PWA cookie-jar workaround (auth-and-tiers.md §3.6) |
| Service worker and Web Push: `/sw.js`, `/push/key`, `/push/subscribe`, `/push/unsubscribe` | `api/routes.py:859-897`, `api/push.py:92-148` | VAPID reaches browsers only (push-and-notifications.md §6); the app registers through `/api/v1/push/devices` |
| HTML shells `/`, `/request`, `/dashboard` and `localStorage` flags (`plexget-known`, `plexget-pin`, `plexget-skin-picked`, `plexget-tour`, `plexget-seen-notes`) | `dashboard/*.html` | The app has its own screens and `UserDefaults` |
| Look editing: `POST /themes/save`, `POST /themes/<slug>/delete` | `api/routes.py:799-827` | The app's Looks screen is a picker only (10-decision-record.md §2) |
| Notification catalogue editing | `config.yaml` `notifications.*` | `PUT /api/v1/owner/notifications` is cut; the Console shows `GET /notifications` read-only |
| The setup page `/setup` | `api/setup.py` | The owner's browser on the server's LAN, once |
| The loopback owner door | `api/routes.py:1263-1265` | Off in the image; a phone never qualifies |
| Owner-api.md class C: `/scan/*`, `/analyze*`, `/quality`, `/media`, `/reports/upgrades`, `/reports/posters`, `/fixer/*`, `/posters/*`, `/command`, `/search/tmdb`, `/gaps` | `api/routes.py:194-547` | Filesystem, ffprobe and Posteria tooling; not a phone's job. `GET /reports/gaps` is not in this class, but no v1 screen calls it either: the Kit decodes `reports.gaps.tv.json` for the contract test only (03-server-api.md §4.23; 04-ios-app.md §12 GAP 3) |
| Never called for App Review reasons: `/wanted/export`, `/export`, `/export/download`, `/sonarr/*`, `/radarr/*` | `api/routes.py:524-558, 613-623` | Guideline 5.2.3 (10-decision-record.md §6.10); the CI banned-word grep covers the app's copy |
| `/plex/webhook` | `api/routes.py:669-695` | Machine-to-machine from Plex, keyed by `plex.webhook_key` |
| Cookie `Secure` flag and `X-Forwarded-Proto` through Funnel | `api/routes.py:119, 1152` | Gate (10-decision-record.md §6.2) for the web page; the app sends no cookie. Note: `ProxyFix` defaults `x_for=1`, so deployment-and-security.md §4.2 is right and auth-and-tiers.md §0 is not (10-decision-record.md §5 item 24) |
