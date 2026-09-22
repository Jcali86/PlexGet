# 01. Product and decisions

Purpose: this document says what is being built and why, for the owner who will fund and name it and for the
builder who will write it. It fixes the product boundary (one binary, a free household side, a paid Console), the
money (exact StoreKit identifiers, trial, grace period, what lapses and what does not), the App Store name problem
and the `AppIdentity` constant, what is deliberately out of v1 and what brings each item back, the decisions table
from the Architecture Decision Record (10-decision-record.md §4) with two columns added, the two first-run stories
with time estimates, and the measurable launch criteria. Where a source disagrees with the ADR, a "Note:" says so
and the ADR wins.

Read with: 00-README.md, 02-architecture.md, 03-server-api.md, 04-ios-app.md, 05-push-relay.md, 06-install-and-setup.md, 08-app-store-and-release.md, 10-decision-record.md.

## 1. The product in one page

One native iOS app (SwiftUI, Swift 6 strict concurrency, iOS 17 or later) distributed on the App Store as a
single binary. It is a client of one self-hosted PlexGet server, the Python service in this repository, which stays
open source under MIT and keeps serving the existing web pages unchanged. The server gains a small versioned
surface at `/api/v1` with bearer sessions (ADR §2), and the developer runs exactly one cloud component, a push-only
relay that holds the APNs key and never sees a Plex token, a session token or a server address (ADR §1).

Two kinds of person use the same binary:

| Person | How they are identified | What they get | What they pay |
|---|---|---|---|
| Household member | A Plex account the owner has shared the server with; the server checks the roster on every request (`api/plex_auth.py:343-377`; screens-inventory.md §1.1) | Every household screen: Welcome, Pairing, Home, Results, Detail, Playlists, WantedList, Bell, Looks, Report, Help, Settings (ADR §2); told when a request arrives | Nothing, ever |
| Owner | The Plex account whose token is in `plex.token`; the roster's owner (`api/plex_auth.py:174-175`) | Everything a member gets, plus the Console tab: Requests, Inbox, Announce, Discover, Overview, Notifications, Server, Sessions (ADR §2); relay push for the whole household through the server key | The "Owner" subscription (§2) |

The household side replaces the PWA at `/request` for people who install the app; the web page keeps working for
everyone else. The Console takes the phone-appropriate parts of `/dashboard` (screens-inventory.md §3.1, §3.3
first panel, §3.5, §3.6); the desktop maintenance tabs stay web-only (screens-inventory.md §6 item 10).

What the app promises, in the order a household feels it:

1. Sign in with Plex and nothing else. No account is created by the app; the server verifies identity against
   plex.tv and never trusts a username the app sends (constraints fact 4; ADR §5 item 27).
2. Find the server at home without typing. The app probes the addresses plex.tv already lists as local
   connections for that machine identifier at port 5050 (ADR §4 "LAN probe and trust"). Away from home the owner's
   invite link, tapped once, does the same job.
3. Ask for a film or a series. The wanted list is a wish list of titles. The app never mentions downloading,
   torrents, usenet, Sonarr, Radarr or indexers and never calls the export or Sonarr/Radarr routes (constraints
   fact 8; ADR §6 item 10).
4. Hear when it arrives. Through the relay when the owner subscribes; otherwise through `BGAppRefreshTask` polling
   of `GET /api/v1/changes` with local notifications, worded "notifications may arrive late" (ADR §4 "Push").
5. Keep the persona's voice. The app renders `name`, `greeting`, `brush_offs`, `images` and `brush_off_moods`
   from `GET /persona` and never carries `voice` or `examples`; the off-topic guard and the brush-off choice stay
   on the server (persona-themes-limits.md §1.3, §1.6, §8 item 1).

## 2. Who pays and for what

The buyer is the owner; the household never unlocks anything by purchase (guideline 3.1.1; ADR §4 "What is paid").
Everything on the household page is free for the owner too, including "Make someone a playlist" and the "for whom"
selector, which live on the household page today (screens-inventory.md §2.22, §2.7).

### 2.1 Products

| Item | Value | Source |
|---|---|---|
| Subscription group | `Owner` | ADR §4 "Monetisation" |
| Yearly product identifier | `owner.yearly` (default, shown first) | ADR §4; design-A §6 |
| Monthly product identifier | `owner.monthly` | ADR §4 |
| Introductory offer | 7-day free trial on `owner.yearly`; each product may carry its own offer and a customer is eligible for one per group (ADR §5 item 11) | ADR §4, §5 |
| Family Sharing | Off | ADR §4 |
| Price | Owner confirms. Working figure £24.99 a year (design-A §11); Apple deducts UK VAT before its 15% small-business commission, so that nets nearer £17-18 (ADR §4). No monthly figure exists in any source; the owner sets it in App Store Connect at Phase 9 | ADR §4, §7 |
| Billing Grace Period | 16 days, set by the human in App Store Connect so it matches the relay (ADR §2, §7 Phase 9). Apple offers 3, 16 or 28 (verified by the security judge, ADR §5 item 20); 16 is a choice, not the maximum | ADR §2, §5 |
| Purchase screen | States the trial length, the price billed afterwards, the period, auto-renewal terms and links to the privacy policy and terms of use (design-A §9, guideline 3.1.2) | design-A §6, §9 |
| Restore | "Restore purchases" calls `AppStore.sync()` (design-A §6; verified there from the framework as documented) | design-A §6 |

### 2.2 The entitlement, on the phone and at the relay

On the phone, `Store` reads `Transaction.currentEntitlements` at launch, listens to `Transaction.updates` for the
life of the app, reads `Product.SubscriptionInfo.Status` for grace and billing-retry states, and counts only
`VerificationResult.verified` transactions (design-A §6; StoreKit 2 names marked verified there), producing
`@Observable OwnerEntitlement { active, expiresAt, inGrace }`. Phase 7 forbids any other entitlement check and any
non-IAP unlock outside the demo client (ADR §7 Phase 7).

At the relay, the owner's app posts the transaction's `jwsRepresentation` to `POST /v1/server-keys`, receives
`{server_key, expires_at}` bound to `originalTransactionId`, and forwards the key to `POST /api/v1/owner/relay`,
which writes `data/relay.json` (ADR §2). The PlexGet server never sees a receipt and never talks to Apple. The
relay learns of renewal, expiry, refund and grace from App Store Server Notifications V2 first and from any JWS the
owner's app re-posts second, and honours a key until `expires_at` plus 16 days (ADR §4 "Relay entitlement source").
The library that verifies these is a Phase 6 gate (ADR §6 item 4).

### 2.3 What lapses and what does not

The moment is `expires_at` plus 16 days at the relay. During grace the Console stays open and push continues;
`inGrace` is exposed for 04-ios-app.md to surface.

| Thing | After the lapse | Source |
|---|---|---|
| Console tab | Shows the paywall | design-A §6 |
| Relay pushes for the household | Relay answers `expired`; the server marks the key inactive and `GET /api/v1/server` reports `push: {delivery: "polling"}` | ADR §2; design-A §2.2, §6 |
| Household phones | Notice the flip on their next `/api/v1/server` read and schedule `BGAppRefreshTask`; the Bell shows the polling wording | design-A §5.3, §6 |
| Owner-only pushes (`requested`, `file_missing`, `message`) | Arrive by polling only, like everything else | design-A §5.2 |
| `native_devices` rows, relay handles, the server key record | Kept; nothing is deleted, so resubscribing restores push within one app launch of the owner | design-A §6 |
| Every household screen, for the owner too | Unaffected | ADR §4 "What is paid" |
| Wanted list, messages, announcements, playlists | Unaffected; they are server data | ADR §4 |
| The web dashboard | Unaffected and never mentioned in the app | design-A §6 |

Deletion is separate from lapse: "Delete my relay data" in the Console's Server screen calls
`DELETE /v1/server-keys/<id>` and the device delete; members delete their handle from Settings (ADR §4 "Relay
data deletion"; the guideline 5.1.1(v) answer, ADR §5 item 14).

### 2.4 Demo mode and the paid tier

"Try the demo" swaps in `DemoServerClient` with the fixed persona "Reel", canned responses, no network, no Keychain
writes, an "Owner console (demo)" open without purchase, and a banner saying so (design-A §3.5). It exists for
guideline 2.1 (constraints fact 7) and for screenshots (ADR §7 Phase 9); the demo client is the only place the
Console opens without a verified transaction (ADR §7 Phase 7).

## 3. The App Store name and `AppIdentity`

"PlexGet" contains "Plex". Apple asks for authorisation when an app name contains a third-party mark, and Plex's
brand guidelines restrict use of the name (constraints fact 9; guideline 5.2.1, ADR §5 item 15). The owner decides:
a new name, not "PlexGet"; "for Plex" only in the subtitle and only after reading Plex's brand guidelines
(unverified whether descriptive use is permitted); no Plex logo or colour scheme as the icon (ADR §4; design-A §9).

Two names exist and must not be confused:

| Name | Where it comes from | Where it appears |
|---|---|---|
| The App Store name (`<AppName>` in every plan document until the owner decides) | The owner | Bundle display name, `X-Plex-Product` on every plex.tv call, the push topic, the invite domain `push.<developer-domain>`, the repository names `<AppName>-ios` and `<AppName>-relay` (ADR §2) |
| The in-app name | The server's `app.name`: `"Plex Requests"` when absent in code (`api/user_library.py:19-20`), `"PlexGet"` in `config.example.yaml:285`; served as `app_name` on `GET /auth/me` (`api/routes.py:1199, 1208`), on `GET /api/v1/server` and on `GET /api/v1/auth/session` (ADR §2) | The header wordmark and titles inside the app, whatever the store listing says (design-A §9 "Naming") |

`AppIdentity` is one constant in the app target holding the bundle display name, `X-Plex-Product`, the push topic
and the invite domain, so the rename is one line (ADR §2, §4). Nothing else in the app target or in `PlexGetKit`
may spell the name; `PlexGetKit` takes the product string as a parameter. The hard-coded "PlexGet" wordmark
(`dashboard/request.html:786, 2467`) and the push title in `dashboard/sw.js:83` are web-only and are not copied.

Until the owner decides, the builder uses the literal placeholder `AppName` for every `AppIdentity` value that
needs a string, and the Phase 9 banned-word review (ADR §7) treats the placeholder as a blocker alongside the
banned words, so nothing is submitted with it.

## 4. Deliberately out of v1

Only the ADR's deferred and cut rows are out. Design-A §13 lists further optional cuts (Discover, Overview, the
monthly plan, the session list, nudges and the starter card, owner-made looks, universal links); the ADR did not
adopt them, so those stay in v1. Note: design-A §4.1 placed Bonjour in v1.1; the ADR cuts it.

| Item | Status | What brings it back | Source |
|---|---|---|---|
| Plex Home profile switching (`POST /api/home/users/{id}/switch`, verified: `venv/lib/python3.11/site-packages/plexapi/myplex.py:500-525`) | Deferred to v1.1 | The Phase 1 human step records what `account.users()` returns for a managed home user; if they appear with a server entry, v1.1 adds switching; if not, members use the account that holds the share | ADR §3, §6 item 7 |
| Hosted smart search (the developer paying for model calls) | Deferred | Only with its own monthly cap: about $0.004 per Sonnet 5 call, roughly $0.54 per household per month at forty calls per person (ADR §4). In v1 owners paste their own key on the setup page | ADR §4 |
| Design C's tunnel (relay carrying API traffic) | Deferred to v2 | Reopened only if TestFlight households abandon at the Funnel step (§7); the starting point is C §2.1 and the tenant-binding correction (ADR §5 item 21) | ADR §4, §6 item 11 |
| App Attest on relay registration; "another device registered under your server key" alert | v1.1 | Evidence that the v1 quotas (10 registrations per IP per hour, 1,000 pushes per key per day, 60 per key per minute, 100 per handle per day) are being abused | ADR §2, §4 |
| Notification Service Extension (push carrying only `{ev, ref}`) | v2 | A decision to stop rendered titles and bodies transiting the relay; costs an extension target and a Keychain access group | ADR §4; design-A §5.2 |
| Bonjour (`_plexget._tcp`) | Cut | Nothing planned; the Plex-address probe covers the case without a Docker bridge caveat | ADR §4 |
| `PUT /api/v1/owner/notifications` | Cut; catalogue shown read-only | Nothing planned; editing stays in `config.yaml` | ADR §4 |
| Owner-made looks from the phone (`POST /themes/save`, `POST /themes/<slug>/delete`) | Not in v1; Looks is a picker and custom looks are read-only | Nothing planned; the web page keeps the maker (persona-themes-limits.md §5.4) | design-A §2.1 "Looks (picker only)" |

## 5. Decisions table

ADR §4 reproduced with "Who decides" and "Default until decided" added. Rows marked **owner confirms** are the
owner's to settle before the plan is handed to the builder; the plan builds to the default meanwhile. "Gate" names
the ADR §6 risk whose outcome can change the row.

| Decision | Final choice | Reason | Who decides | Default until decided |
|---|---|---|---|---|
| Architecture | Confirmed: native client of the existing server plus a push-only relay | Maximum reuse of working code; smallest cloud blast radius; both A-favouring judges; C's tunnel deferred to v2 | Decided (ADR §1) | As stated |
| Monetisation | Confirmed: auto-renewable "Owner" group, `owner.yearly` (default, 7-day introductory trial) and `owner.monthly`; Family Sharing off; **owner confirms price** | Relay is an ongoing cost. Note the net: Apple deducts UK VAT before its 15% small-business commission, so £24.99 nets nearer £17-18, not £21 (judge correction of A §11) | Owner (price only); identifiers and group decided | £24.99 a year (design-A §11); monthly price unset until Phase 9 |
| What is paid | Confirmed and made explicit: the Console tab, and through the server key, relay push for the whole household; everything on the household page, including gifting and "for whom", stays free for the owner too | The buyer is the owner; the household never unlocks anything by purchase (guideline 3.1.1). The user judge rated this the weakest renewal of the three; the honest answer is native push plus the Console, and the v2 tunnel is the upgrade path if that proves insufficient | Decided | As stated |
| App Store name | Confirmed: a new name, not "PlexGet"; "for Plex" only in the subtitle and only after the owner reads Plex's brand guidelines (unverified whether descriptive use is permitted); in-app name from the server's `app_name`; **owner confirms** | Trademark (brief fact 9). One `AppIdentity` constant makes the rename one line | Owner | Placeholder `AppName` (§3); submission blocked while it remains |
| Repository | Confirmed: `<AppName>-ios` and `<AppName>-relay` private; server changes in this public repository; **owner confirms** | Paid client and relay must not be MIT by accident; no agent ships to owners, so nothing else needs to be public | Owner (names and hosting); privacy decided | Private repositories named after the placeholder |
| Remote reach | Confirmed with wording fixed: Tailscale **Funnel** documented as the primary path (`tailscale funnel 5050`), reverse proxy and Cloudflare Tunnel as alternatives; the setup page stores the pasted HTTPS address as `app.urls.remote` and self-checks it; the invite is a universal link `https://push.<domain>/join#…` also shown as a QR on the setup page and the app's Server screen | Tailnet membership would put an install on every member (A §14.1). The Funnel Personal-plan discrepancy is a gate (§6) | Decided; gate §6 item 2 before Phase 8 copy | Funnel copy written after the gate; door off regardless |
| Push | Confirmed: relay first, `BGAppRefreshTask` polling `GET /api/v1/changes` plus local notifications as the fallback and for non-subscribers | Brief fact 2; the `events` table is what makes polling possible at all (push-and-notifications.md §6.5) | Decided | As stated |
| Server install | **Overturned in form, not intent**: one `docker run` (or `docker compose up` with the shipped file) of a published GHCR image, plus the first-run `/setup` page; the image ships the loopback door off, the settle scheduler on, the webhook key generated, and `waitress` | No Dockerfile exists (deployment-and-security.md §2.4) and a compose file that builds locally would make the owner clone the repository; `app.run()` is Werkzeug's development server (`main.py:10`); without the scheduler and key nothing shipped ever sends `arrived` in Docker (push-and-notifications.md §3.3; deployment-and-security.md §4.3) | Decided; owner confirms the GHCR `<org>` | `ghcr.io/<org>/plexget` with the placeholder until the owner names the organisation |
| iOS minimum | Confirmed: 17.0 | Brief | Decided | As stated |
| Concurrency | Confirmed: Swift 6 strict | Brief | Decided | As stated |
| Sign-in exchange (new) | Pin hand-off: the app mints a fresh pin under its own client identifier, links it to the signed-in account with `PUT https://plex.tv/api/v2/pins/link` (`code`, `X-Plex-Token`; verified: `plexapi/myplex.py:122, 1144-1155` does exactly this), and sends `{"pin": {"id", "client_id"}}` to `POST /api/v1/auth/plex`; the server reads the pin as `check_pin()` does today with the app's identifier, runs the unchanged `plex_identity`, `authorise`, `server_access_token` chain and discards the account token. `plex_token` in the body is accepted only in tests and over HTTPS to a typed address | Bounds what a LAN observer captures to the pin's life instead of the device's Plex login (security judge). Whether `pins/link` accepts a strong pin, and whether a linked pin's token is readable more than once, are Phase 1 gates (§6) | Decided; gate §6 item 1 (Phase 1 human step) | Strong pin; short pin if only short pins link; `plex_token` body over HTTPS or confirmed LAN only if a claimed pin cannot be re-read, and the builder stops with a deviation note |
| Session lifetime (new) | `ios` rows slide, 180 days from `last_seen`; `web` rows unchanged at 90 days from `created_at`; tokens hashed at rest | A phone re-exchanging every 90 days adds plex.tv calls for nothing; the roster check on every request already handles withdrawal (auth-and-tiers.md §2.3); revocation is per device | Decided | As stated |
| Exchange rate limiter (new) | `POST /api/v1/auth/plex` is keyed on `device.id` (12 per 10 minutes) and the global 40 per 10 minutes, plus 6 per Plex account per hour after identity is known; the caller-address rule is not applied to this route | Behind Funnel or a proxy every caller shares one address bucket (auth-and-tiers.md §6.7; both judges' corrections of A §3.1) | Decided | As stated |
| LAN probe and trust (new) | The probe of plex.tv's `connections[].local` addresses at port 5050 runs whenever no candidate URL is known; afterwards the Home-network toggle governs it (default on for owners and for anyone whose first probe succeeded, off otherwise). Before the first exchange with a server not reached through an invite the app shows the Plex server name and address and asks for confirmation. Plain HTTP only to private addresses; HTTPS preferred when reachable | The machine identifier is public (auth-and-tiers.md §1.5), so the probe is discovery, not trust; members should not be asked for Local Network permission unless they need it (solo-developer judge) | Decided; gate §6 item 6 (Phase 4) | As stated |
| Bind address (new) | Image binds `0.0.0.0`; the setup page writes `flask.host: 0.0.0.0` and `app.loopback_owner: false` together and says why; existing venv installs without setup keep `127.0.0.1` and the door as today | The probe only works when the server binds beyond loopback (`config.py:69`; judge correction of A §4.1); the door must never be open on a LAN-bound server | Decided | As stated |
| Relay language (new) | Python (pytest, Apple's App Store Server Library, HTTP/2 APNs client, SQLite) rather than A's Go | Two languages instead of three for one builder; the server's test pattern carries over; Apple's library does JWS and server-notification verification | Decided; human confirms the library's package name before Phase 6 (gate §6 item 4) | Python; the builder reads the library README before writing verification |
| Relay entitlement source (new) | App Store Server Notifications V2 first, re-posted JWS second, 16-day grace configured to match App Store Connect | Without notifications a renewal whose owner does not open the app within 16 days loses push silently and a refund keeps it (security judge) | Decided; gate §6 item 4 | As stated |
| Relay data deletion (new) | "Delete my relay data" in the Console's Server screen calls `DELETE /v1/server-keys/<id>` and the device delete; members delete their handle from Settings | Guideline 5.1.1(v) answered rather than argued | Decided | As stated |
| Notification permission (new) | Asked in context immediately after the first successful sign-in, with a sentence explaining what it is for | Arrival is the point of the app; 4.5.4 is satisfied by an explained prompt (user judge) | Decided | As stated |
| Arrival matching (new) | `Guid` match on `tmdb_id` when known, then folded title and year; `kind` and `season` respected | Today's `recheck` searches film sections only (owner-api.md §8); plexapi exposes `guids` on videos (verified: `plexapi/video.py:437`, `plexapi/media.py:937`) | Decided | As stated |
| Fixtures (new) | pytest writes `tests/fixtures/api/*.json`; iOS vendors them; `swift test` decodes every file | Turns "the shapes are inconsistent" (household-api.md §6.7-6.8) into a Linux-runnable failing test | Decided; gate §6 item 8 (every phase) | As stated |
| Feature flag (new) | `native_signin` and `min_app_version` in `GET /api/v1/server`; the app shows "update needed" rather than failing | A plex.tv change is met by a flag, not an emergency release | Decided | As stated |
| Persona (confirmed fact 16) | The app renders `name`, `greeting`, `brush_offs`, `images`, `brush_off_moods`; never carries `voice` or `examples` | persona-themes-limits.md §1.3, §8 | Decided | As stated |
| Plex Home profiles | Deferred to v1.1 behind a gate | Whether `account.users()` lists managed home users, and therefore whether the roster admits them, is unverified | Gate §6 item 7 (Phase 1 human step) | Out of v1; members use the account that holds the share |
| Hosted smart search | Deferred; recorded cost from B §11 and C §11: about $0.004 per Sonnet 5 call, roughly $0.54 per household per month at forty calls per person, so it needs its own monthly cap | Not part of v1's subscription; owners paste their own key on the setup page | Owner, after launch | Out of v1 |
| C's tunnel | Deferred to v2, to be reconsidered only if abandonment at the Funnel step is observed | The user judge's case for it is real; the security and operations cost is what v1 cannot carry | Owner, on the §7 abandonment evidence (gate §6 item 11) | Out of v1 |
| App Attest on relay registration; "another device registered under your server key" alert | v1.1 | Security judge's promotion, with quotas as the v1 defence | Decided | Out of v1 |
| Notification Service Extension (push carries only `{ev, ref}`) | v2 | Adds an extension target and an access group the builder would have to get right | Decided | Out of v1 |
| Bonjour | Cut | The Plex-address probe covers the same case without a Docker bridge caveat | Decided | Out |
| `PUT /api/v1/owner/notifications` | Cut; the catalogue is shown read-only | Editing stays in `config.yaml` | Decided | Out |

## 6. First-run stories

Minutes are planning estimates; §7 says how they are measured. Shapes are in 03-server-api.md and 05-push-relay.md.

### 6.1 A household member, at home

1. Installs the app from the App Store. 1 minute.
2. Opens it. The Welcome screen offers "Sign in with Plex", "I have an invite" and "Try the demo" (design-A §3.1
   step 1). No network call has been made yet.
3. Taps "Sign in with Plex". The app creates a pin at `POST https://plex.tv/api/v2/pins?strong=true` with the
   header set from constraints fact 4 and its per-install `X-Plex-Client-Identifier`, then opens
   `https://app.plex.tv/auth#?clientID=…&code=…&context[device][product]=…&forwardUrl=…` in
   `ASWebAuthenticationSession`, with the typed `plex.tv/link` code underneath (screens-inventory.md §2.2). 1-2 minutes.
4. The app polls `GET https://plex.tv/api/v2/pins/<id>` until `authToken` is present, stores the token in the
   Keychain (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, constraints fact 15), and calls
   `GET /api/v2/user` and `GET /api/v2/resources?includeHttps=1&includeRelay=0` with `Accept: application/json`
   (ADR §5 item 22). Under 10 seconds.
5. No candidate URL is known, so the probe runs: each `connections[]` entry with `local == true` is tried at
   `http://<address>:5050/api/v1/server` with a 1.5 s timeout, and the first answer whose `plex_machine_id` equals
   that server's `clientIdentifier` wins (design-A §4.1; ADR §4). iOS shows its Local Network prompt with the
   `NSLocalNetworkUsageDescription` string (ADR §6 item 6; unverified exactly when). Under 10 seconds.
6. Because the server was not reached through an invite, the app shows the Plex server name and the address and
   asks for confirmation (ADR §4 "LAN probe and trust"). 10 seconds.
7. The hand-off: the app mints a fresh pin under its own identifier, links it with
   `PUT https://plex.tv/api/v2/pins/link`, and sends `{"pin": {"id", "client_id"}, "device": {…}}` to
   `POST /api/v1/auth/plex`. The server applies the device-keyed limiter, reads the pin as `check_pin()` does,
   runs `plex_identity`, `authorise` and `server_access_token` unchanged, discards the account token and returns
   the session (ADR §4 "Sign-in exchange"). A `403` with code `not_shared` or `invite_pending` (design-A §2.2)
   ends the story with the reason on screen. Under 5 seconds.
8. The session token goes in the Keychain. The app loads `/persona`, `/themes`, `GET /api/v1/auth/session` and
   `/request/showcase`; Home appears with the greeting line and the reels (screens-inventory.md §2.3, §2.8). Under
   5 seconds.
9. The notification permission prompt appears in context with one sentence saying what it is for (ADR §4
   "Notification permission"). 10 seconds.
10. On grant, `registerForRemoteNotifications()`, `POST /v1/devices` at the relay for `{handle, device_secret}`,
    then `POST /api/v1/push/devices` at the server, which answers `delivery: "relay"` or `"polling"`. In the
    polling case the app schedules `BGAppRefreshTask` and the Bell shows the "may arrive late" wording; the
    registration is kept so a later subscription delivers without the member doing anything (design-A §5.1). Under 5 seconds.
11. Types a title or a mood and searches, or taps a poster. Done. Total about 3-5 minutes, most of it Plex sign-in.

Away from home for the very first sign-in: at step 2 the member taps the owner's invite link
`https://push.<developer-domain>/join#v=1&m=<machine_id>&l=<local url>&r=<remote url>&n=<name>` or scans the QR
(design-A §4.1); the fragment never reaches the relay's web server. Step 5 uses the invite's URLs and step 6 is
skipped, because the invite is how the server was reached (ADR §4). On every later launch the app tries the local
URLs first (1.5 s) and falls back to the remote ones; the Home-network toggle governs whether the probe runs again.

"Try the demo" at step 2 skips every network step and opens Home on `DemoServerClient` (§2.4). Under 1 minute.

### 6.2 The owner, from nothing to a household with push

1. Installs Docker: Docker Desktop on a Mac or Windows PC, or `curl -fsSL https://get.docker.com | sh` on Linux
   (design-A §7 step 1). This is the hard step and the app cannot remove it. 10-30 minutes.
2. Runs one command from the README: on Linux `docker run -d --name plexget --restart unless-stopped
   --network host -v plexget-data:/data ghcr.io/<org>/plexget:latest`; on Docker Desktop the same with
   `-p 5050:5050` in place of `--network host` and `plex.url` defaulting to `http://host.docker.internal:32400`;
   or `docker compose up -d` with the shipped `docker-compose.yml`; or `install.sh`, which chooses (ADR §2;
   design-A §7 step 2). 2-5 minutes, mostly the image pull.
3. Opens `http://<that machine>:5050/setup`. The server is in setup mode: `/health` answers `{"status": "setup"}`
   and only `/setup/*`, `/health` and `/api/v1/server` with `"setup": true` are served (design-A §7 step 3). The
   page is open to anyone on the LAN until the first save, so the copy says to do it now (design-A §8). 1 minute.
4. "Connect Plex": the existing server-side PIN flow (`create_pin`/`check_pin`, `api/plex_auth.py:42-145`) with
   `forwardUrl` set to the setup page; the page lists the owned servers from `/api/v2/resources`, tests
   `PlexServer(url, token)` and writes `plex.url` and `plex.token` (design-A §7 step 4). 2-3 minutes.
5. "Name and voice": `app.name` and one of the three shipped personas, `bruce` ("Bruce"), `dry-and-minimal`
   ("Quill"), `warm-and-chatty` ("Bertie") (persona-themes-limits.md §1.1). 1 minute.
6. "Optional keys": a TMDb key and a model key, with the note that without a key search is keyword-only
   (design-A §7 step 4; persona-themes-limits.md §6.4). 0-5 minutes; skippable.
7. "Reach it from outside": install Tailscale on the server machine, run `tailscale funnel 5050`, paste the
   `https://<machine>.<tailnet>.ts.net` address; the page stores it as `app.urls.remote` and fetches its own
   `/health` through it as a self-check (ADR §4 "Remote reach"; design-A §4.2), with the reverse-proxy and
   Cloudflare Tunnel alternatives beside it. 10-20 minutes; skippable, returnable, and the likeliest abandonment
   point (ADR §6 item 11).
8. "Tell Plex when things arrive": the webhook URL with its generated `plex.webhook_key` for Plex Pass owners;
   otherwise the page says the server checks every two hours (`PLEXGET_SETTLE_MINUTES=120`, ADR §2). 2-5 minutes.
9. Save writes `/data/config.yaml` with `flask.host: 0.0.0.0` and `app.loopback_owner: false` together (ADR §4
   "Bind address"), generates `data/secret.key`, `data/client_id` and the webhook key, and the process exits so
   the restart policy brings it up configured (design-A §7 step 4). 1 minute.
10. On the phone, steps 1-11 of §6.1. The role is `owner` because the roster's owner is whichever plex.tv account
    holds `plex.token` (`api/plex_auth.py:174-175`); the app cannot claim it (design-A §3.2). The Console tab is
    visible. 3-5 minutes.
11. Uses the household side at home first: a search, an ask. The paywall is not shown until the app has worked
    at home (ADR §6 item 11).
12. Opens the Console tab and sees the paywall: `owner.yearly` first with its 7-day trial, the price billed
    afterwards, the period, the terms and the two links (§2.1). On purchase the app posts the transaction's
    `jwsRepresentation` to `POST /v1/server-keys`, forwards `{server_key, expires_at}` to
    `POST /api/v1/owner/relay`, and the server answers `{"delivery": "relay"}` (§2.2). 2 minutes.
13. Console, Server screen: shows the invite link and QR; "Send a test" on the Notifications screen calls
    `POST /api/v1/push/test` and the phone buzzes (design-A §5.4). Shares the link with the household. 2 minutes.

Total 35-80 minutes, dominated by Docker and Tailscale. Without step 7 the household works at home only, and push
still works because the server reaches the relay outbound; without a Plex Pass webhook, "it arrived" is delayed by
up to the settle interval (ADR §4 "Server install"; push-and-notifications.md §3.3).

## 7. Success criteria for launch

No analytics, no tracking and no third-party SDK ship (constraints fact 14), so every number below comes from the
server's `events` table and `GET /api/v1/owner/status` (ADR §2), the relay's counters (counts, never bodies, ADR
§2), TestFlight feedback and the household's own reports. Targets are set by this document; the owner may tighten
them, and missing one is a reason to stop and look.

| Criterion | Measure | Target | Where the number comes from |
|---|---|---|---|
| TestFlight households | Households with one owner and at least two members, running the build for 14 days | At least 2 (ADR §7 Phase 9); 3 if a third volunteers | TestFlight tester list; each owner's `native_devices` count from `GET /api/v1/owner/status` |
| Arrival, relay path | Time from the `events` row `at` for an `arrived` event to the notification appearing on the asker's phone | Under 60 s for every arrival observed; APNs delivery itself is typically seconds (unverified) | The `events` row versus the phone's notification time, reported by the tester |
| Arrival, webhook path | Time from the file appearing in Plex to the `arrived` event, on a Plex Pass install | Under 5 minutes | Plex's `library.new` hit (`webhook.last_hit_at` in `GET /api/v1/owner/status`) versus the `events` row |
| Arrival, scheduler path | The same, without Plex Pass | Under `app.settle_every_minutes` plus 5 minutes, that is 125 minutes at the shipped default | `settle.last_run_at` versus the `events` row |
| Arrival, polling path | Time from the `events` row to a local notification on a phone whose server reports `polling` | Visible on the next foreground every time; a background local notification within 24 hours in most cases (iOS decides the cadence; unverified) | Tester report; the app's cursor in `UserDefaults` is not a secret and can be read in a debug screen |
| Owner abandonment | The furthest of these steps each TestFlight owner completes: Docker installed; container answers `/health` not in setup; setup saved; `app.urls.remote` set and self-check passed; first member signed in; trial started; first arrival delivered | Every owner reaches "first member signed in"; abandonment at "remote address" by more than one owner reopens the tunnel decision (ADR §6 item 11) | The setup page's own state, `GET /api/v1/owner/status`, `GET /api/v1/auth/sessions`, and the owner's report; with two households these are counts, not rates |
| Member abandonment | Members who install but never reach Home, and the step they stopped at (§6.1 steps 3, 5, 6, 7) | None stop at step 5 or 6 at home; none stop at step 7 with an accepted share | Members' `sessions` rows of kind `ios` versus the owner's roster; the member's report |
| Exchange failures | `POST /api/v1/auth/plex` answers other than `200` per household over 14 days, by code | No `429` for a real person; every `403` explained by a real share state | Server log counts by code |
| Shape drift | Fixtures the Kit cannot decode | Zero at every phase gate (ADR §6 item 8) | `swift test` in CI |
| Review copy | Banned words in fixtures, strings and metadata; Plex mark in name or icon | Zero (ADR §6 item 10) | The CI grep gate; the Phase 9 human review |
| App Review | Submissions before approval | Approved by the second submission with no 5.2.3 or 5.2.1 finding | App Store Connect |
| Stability | Crashes reported by TestFlight over the 14 days | No crash that reproduces | TestFlight crash reports |
| Relay | `GET /v1/health` availability during the 14 days; quota refusals for real households | No quota refusal for a real household; any outage is visible as a `polling` flip, not as lost data | The relay's counters and its host's uptime record |

The one criterion that changes the roadmap is owner abandonment at remote reach (ADR §6 item 11); its consequence
is reopening the deferred tunnel row in §4, not a change to v1.

## 8. Notes on the sources

- Note: design-A §2.2 keeps sessions at 90 days with a silent re-exchange; the ADR slides `ios` rows to 180 days
  from `last_seen` and hashes tokens. Design-A §3.1 step 7 sends the plex.tv token to `POST /api/v1/auth/plex`;
  the ADR replaces it with the pin hand-off. Design-A §2.3 writes the relay in Go; the ADR chooses Python.
  Design-A §2.4 names `compose.yaml` and `compose.posteria.yml`; the ADR rewrites `docker-compose.yml` with
  Posteria under `profiles: [posters]`.
- Note: screens-inventory.md §5 items 2 and 21 sign in through `POST /auth/start` and `GET /auth/poll` and sign
  out through `POST /auth/logout`; the app never calls `/auth/*` (design-A §2.2) and signs out with
  `DELETE /api/v1/auth/session` (design-A §3.4). Item 15 describes the Bell in Web Push terms; the app's Bell
  shows the relay or polling state from `GET /api/v1/server` and `POST /api/v1/push/devices` instead.
- Note: persona-themes-limits.md §3 finds `app_name` only on `GET /auth/me`; the ADR adds it to
  `GET /api/v1/server` and `GET /api/v1/auth/session`, which are what the app reads.
