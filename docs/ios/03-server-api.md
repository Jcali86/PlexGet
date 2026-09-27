# 03 Server API: the contract between the app and a PlexGet server

Purpose. This document is the complete list of HTTP endpoints the iOS app may call on a PlexGet server, with the request and response shape of each, its tier, its errors, its side effects and its rate limits. The builder calls nothing that is not on this list. Where an endpoint is new it is specified here in full and the server work is Phase 1 or Phase 2 of `07-build-phases.md`; where an endpoint already exists it is described as the code behaves today, with `file:line` citations into this repository and `(name.md §n)` citations into the research files, and the app takes it as-is. Where the app needs something neither the ADR nor this document defines, the line is marked `GAP:` so it can be grepped; the builder adds it server-side with tests and updates this file before using it. Shapes are listed field by field and never summarised.

Read with: `02-architecture.md` (why the surface is shaped this way), `04-ios-app.md` (the `PlexGetKit` modules that consume these shapes), `05-push-relay.md` (the relay endpoints, which are not on this server), `06-install-and-setup.md` (the setup page, whose routes the app never calls), `07-build-phases.md` (which phase builds each new endpoint), `09-security.md` (the strip table in §7 is repeated there as a threat), `10-decision-record.md` (the ADR; its names are used verbatim here).

Note on a research-file contradiction: auth-and-tiers.md §0 says `ProxyFix` is built without `x_for`, so `request.remote_addr` is always the TCP peer. The ADR (§5 item 24) verified that werkzeug's `ProxyFix` defaults `x_for=1` (`venv/lib/python3.11/site-packages/werkzeug/middleware/proxy_fix.py:92`), so behind one proxy `remote_addr` is the forwarded client. The loopback door at `api/routes.py:1263-1265` reads the raw `X-Forwarded-For` header and is unaffected either way. Follow the ADR.

## 1. Conventions

### 1.1 Base URL set

A server is identified by its Plex `machineIdentifier` (`plex_machine_id` in §3.1), never by a URL. The app keeps, per server, a pinned machine id, a list of local URLs and a list of remote URLs (design-A §4.1; ADR §4 "LAN probe and trust"). Rules the `ServerClient` actor applies:

| Rule | Value |
|---|---|
| Order of attempts on each launch and after any transport failure | local URLs (1.5 s timeout each), then remote URLs |
| Plain `http://` permitted to | RFC 1918 addresses (`10/8`, `172.16/12`, `192.168/16`), link-local `169.254/16`, loopback, `.local` hostnames; never to any other address (brief fact 10) |
| Preference when both answer | `https://` over `http://` even at home |
| Refresh of the set | `urls.local` and `urls.remote` from a `GET /api/v1/server` answer replace the stored lists only when the answer came over a URL already in the stored lists (or the confirmed or invited URL that began the session). A probed address not yet stored, a typed address, and an invite's `l`/`r` for a machine id the app already has stored are candidates that require the confirmation card (02 §2.3 step 5) before any bearer request; they are merged only on confirmation. The route is open and its `urls` field is whatever the answering host says, so an unconfirmed answer never redirects a bearer |
| Drop rule | a URL whose `GET /api/v1/server` answers with a `plex_machine_id` different from the pinned one is removed from the set, never trusted |
| Where a URL may come from | an invite universal link, the plex.tv `connections[].local` probe, a typed address, the `urls` field |
| Port default | 5050 (`config.py:69`); a typed address may carry another port |

### 1.2 Headers on every request to a PlexGet server

| Header | Value | Reason |
|---|---|---|
| `Authorization` | `Bearer <session token>` on every household and owner route; absent on open routes | after Phase 1, `current_user()` (`api/routes.py:943-951`, today cookie-only) reads the bearer first, then the cookie (ADR §2; auth-and-tiers.md §6.1) |
| `Accept` | `application/json`, exactly; never `text/html`, never `*/*` | the guard answers an unsigned `GET` whose `Accept` contains `text/html` with `302` to `/request` (`api/routes.py:1269-1272`; owner-api.md §4.7) |
| `Content-Type` | `application/json; charset=utf-8` on every request with a body | every route reads `request.get_json()` |
| `User-Agent` | `<AppIdentity.product>/<CFBundleShortVersionString> (<build>; iOS <version>)` | log hygiene; not read by the server |
| `X-Plex-*` | never sent to a PlexGet server | those headers belong to plex.tv calls made by the Kit's `PlexTV` module (brief fact 4) |
| `Cookie` | never sent | the app is not a cookie client; the `plexops_session` cookie is the web page's credential (`api/routes.py:941`) |

The Kit's `Endpoints.swift` is the only place a `URLRequest` is built (ADR §2); the header set above is applied there once.

### 1.3 Tiers

`guard()` at `api/routes.py:1235-1273` sorts every path (household-api.md §1; auth-and-tiers.md §4):

| Tier | Rule | Refusal the app can receive |
|---|---|---|
| Open | `OPEN_PATHS = {"/", "/request", "/health", "/manifest.webmanifest", "/persona", "/themes", "/sw.js"}` and `OPEN_PREFIXES = ("/auth/", "/icons/", "/themes/art/")` (`api/routes.py:1229-1231`), plus `GET /api/v1/server` and `POST /api/v1/auth/plex` (new; §3) | none |
| Household | `HOUSEHOLD_PATHS = {"/wanted/thumb"}`, `HOUSEHOLD_PREFIXES = ("/request/", "/push/")` (`api/routes.py:1232-1233`), plus every other `/api/v1/*` route | `401 {"error": "sign in with Plex to use this", "auth_required": true}` (`api/routes.py:1246-1247`); after Phase 1 the body on `/api/v1` paths and bearer requests also carries `code` (§1.4a) |
| Owner | everything else, including `/api/v1/owner/*`; passes when the session's role is `"owner"` (`api/routes.py:1261`) | `403 {"error": "that part is the owner's"}` (`api/routes.py:1273`) |

Roles on the wire are exactly `"owner"` and `"shared"` (`api/plex_auth.py:175, 195`). The role is re-derived from the live roster on every request (10-minute cache, `api/plex_auth.py:343-377`), so a withdrawn Plex share turns every call into `401` within 600 s (auth-and-tiers.md §2.3). The loopback door (`api/routes.py:1263-1265`) never applies to a phone and is off in the shipped image (ADR §4 "Bind address").

### 1.4 Status codes and the error envelope

Every handled error is a JSON object with `error: str` and optional extras (owner-api.md §1.4):

| Extra | Type | Where |
|---|---|---|
| `code` | str | every `/api/v1` error; a stable machine key listed per endpoint in §3 |
| `code` on `401` | str | on `/api/v1` paths and on any request carrying a bearer (Phase 1): `session_revoked` (row has `revoked_at`; the app never re-exchanges), `session_expired` (row past its life or absent, or no bearer sent), `plex_token_dead` (today's `expired_session()`, `api/routes.py:1168-1184`); body `{"error", "code", "auth_required": true}`; the three codes and their `error` strings are in §1.4a |
| `auth_required` | bool | some `401`s (`api/routes.py:1179-1184, 1246-1247`); several `401`s lack it (household-api.md §1) |
| `rate_limited` | bool | `429`s from household routes and `200`s from `/request/nudges` (household-api.md §4) |
| `retry_after` | int seconds | `/api/v1/auth/plex` `429`; `/auth/start` `429` (`api/routes.py:1050-1055`) |
| `hint` | str | `/request/search` `429` |
| `example`, `expected` | object, list | `400`s on owner routes (`api/routes.py:562-588, 595-611`) |
| `status` | str | `/auth/poll` only; not used by the app |

One error mapper in `ServerAPI` (ADR §2) turns status codes into Kit errors, regardless of which route produced them:

| Status | Kit error | Rule |
|---|---|---|
| `401` | `.signInRequired(code:)` | always, whether or not `auth_required` is present. The body on `/api/v1` paths and on any request carrying a bearer is `{"error": str, "code": str, "auth_required": true}` with `code` one of `session_revoked` (the row has `revoked_at`: the app clears the session item and shows Welcome, never re-exchanges), `session_expired` (row absent, past its life, or no bearer sent) or `plex_token_dead` (today's `expired_session()`, `api/routes.py:1168-1184`); for the last two the app clears the session token only and re-exchanges silently (§3.2), at most once per five minutes per device (ADR §5 item 5). A `401` from a legacy route without `code` is treated as `session_expired`. Codes and strings: §1.4a |
| `403` with `code: not_shared` | `.notShared` | exchange only (§3.2) |
| `403` with `code: invite_pending` | `.invitePending` | exchange only (§3.2) |
| any other `403` | `.ownerOnly` | includes `{"error": "owner only"}` from `/notifications` (`api/routes.py:903-904`) and the `for_user` refusal on `/request/playlist` |
| `429` with `rate_limited: true` or `code: "rate_limited"` | `.rateLimited(message, hint?, retryAfter?)` | |
| `400` | `.badRequest(message)` | the message is shown verbatim; it is written for people |
| `404` | `.notFound(message)` | as above |
| `409` | `.conflict(message)` | as above; also `code: device_limit` (§3.8) and `pin_not_linked` (§3.2) |
| `502` | `.plexTVUnreachable(message)` | plex.tv or TMDb unreachable |
| `503` | `.plexUnavailable(message)` | `plex_trouble()` (`api/routes.py:1156-1166`) |
| `500` | `.serverFault` | JSON on `/api/v1` and bearer requests (§3.15); Flask's HTML page elsewhere (owner-api.md §1.4) |
| `200` with an `error` key and an empty payload | `.serverSaidNo(message)` | the routes in §1.5 only |
| `204` | success, no body | the `DELETE` routes in §3 |

Case names are 02 §6.1's verbatim; that table and this one describe one mapper.

### 1.4a The `401` codes

Set by the guard (Phase 1) on `/api/v1` paths and on any request carrying `Authorization: Bearer`:

| `code` | `error` | When | App |
|---|---|---|---|
| `session_revoked` | `"This device was signed out."` | the bearer names a row with `revoked_at` set (§3.4, §3.6, §3.7) | clear the session item; show Welcome; never re-exchange |
| `session_expired` | `"sign in with Plex to use this"` | no bearer, no row for the bearer, or an `ios` row whose `last_seen` is older than 180 days | silent re-exchange (§3.2), at most once per five minutes per device |
| `plex_token_dead` | `"Your Plex sign-in has expired. Sign in again to carry on."` | today's `expired_session()` (`api/routes.py:1168-1184`): the per-server Plex token no longer works | silent re-exchange as above |

Phase 1 tests one case per code (`tests/test_bearer.py`; 09 §11).

### 1.5 The `200`-with-`error` legacy cases

These routes return `200` and put the failure in `error` (household-api.md §6.7; owner-api.md §2). The Kit's decoder for each of these response types has `error: String?` and the mapper raises `.serverSaidNo` when it is non-nil and the payload field is empty:

| Route | Body on failure | Payload field that is empty |
|---|---|---|
| `GET /request/playlists` | `{"error": str, "playlists": []}` (`api/routes.py:1275-1286`) | `playlists` |
| `GET /request/showcase` | `{"rows": [], "error": str}` (`api/routes.py:1766-1775`) | `rows` |
| `GET /request/suggestions` | `{"error": str, "picks": []}` (`api/routes.py:1777-1786`) | `picks` |
| `GET /request/nudges` | `{"picks": [], "error": str}` or `{"picks": [], "rate_limited": true, "error": str}` (`api/routes.py:1941-1979`) | `picks`; the `rate_limited` variant maps to `.rateLimited` |
| `POST /request/playlist/remove` | `{"error": "no playlist called '<name>'"}` (`api/routes.py:1337`) | `removed` absent |
| `POST /request/playlist` | `{"error": "none of those films could be found"}` (`api/routes.py:1576-1627`) | `added` absent |
| `POST /request/playlist/auto` | `{"kind": "cheeky", "interpretation": str, "matches": [], "added": 0}` is not an error; it is the brush-off | none |
| `GET /members/<username>/picks` | `{"picks": [], "error": str}` (`api/routes.py:1815-1837`) | `picks` |
| `GET /auth/poll/<id>` | `{"status": "unknown", "error": str}` | not called by the app |

No new `/api/v1` route may use this pattern; every `/api/v1` failure is a non-`2xx` status with `code`.

### 1.6 Dates and times

Three formats occur and one `SQLiteDate` decoder in `Models` handles all three (ADR §2; owner-api.md §4.10):

| Format | Example | Where |
|---|---|---|
| SQLite text, UTC, no zone suffix | `"2026-09-21 08:12:03"` | every column written by `CURRENT_TIMESTAMP`: `added_at` on wanted rows, `posted_at`, `read_at`, `retired_at`, `last_seen`, `created_at`, `analyzed_at` (`db/database.py:114, 127, 138`) |
| Unix integer seconds | `1700000000` | `added_at` on every film, show, pick and detail dict (`scanner/wanted_search.py:330`); `0` when unknown |
| ISO 8601 UTC with `Z` | `"2026-09-21T08:12:03Z"` | every timestamp a `/api/v1` route emits: `expires_at`, `at`, `last_hit_at`, `last_run_at`, `relay_expires_at`, and `created_at`/`last_seen` when re-emitted by `/api/v1/auth/sessions` |

`YYYY-MM-DD` date-only strings (`available_from`, `release_date`) are decoded as strings, not dates.

### 1.7 Rate-limiting signals

| Route | Signal | Ceiling | Storage |
|---|---|---|---|
| `POST /api/v1/auth/plex` | `429 {"error", "code": "rate_limited", "rate_limited": true, "retry_after": int}` | 12 per `device.id` per 600 s; 40 overall per 600 s; 6 per Plex account per 3600 s once identity is known; the caller-address rule of `pin_allowed()` is not applied here (ADR §4 "Exchange rate limiter") | in-process memory, coherent because `waitress` runs one process (ADR §2) |
| `POST /request/search` | `429 {"error", "rate_limited": true, "hint"}` | `app.limits.per_hour` 30, `per_day` 100, `server_per_day` 400 model calls (`api/limits.py:31-51`) | `ai_usage` table |
| `POST /request/playlist/auto` | `429 {"error", "rate_limited": true}` | same allowance, kind `playlist` | same |
| `GET /request/nudges` | `200 {"picks": [], "rate_limited": true, "error"}` | same allowance, kind `nudge` | same |
| `POST /request/wanted` | `429 {"error": "That's a lot of requests in one go - give it an hour.", "rate_limited": true}` | `app.limits.wanted_per_hour` 40 per user (`api/routes.py:1015-1030`) | in-process memory |
| `POST /request/message` | `429 {"error": "That's a lot of messages in one go - give it an hour.", "rate_limited": true}` | `MESSAGES_PER_HOUR = 10` per user (`api/routes.py:1028-1041`) | in-process memory |
| `POST /api/v1/push/devices` | `409 {"error", "code": "device_limit", "limit": 5}` | five `native_devices` rows per user (ADR §2) | table |
| `POST /api/v1/push/test` (and `POST /push/test`) | `429 {"error": "That's a lot of test pushes - give it an hour.", "code": "rate_limited", "rate_limited": true}` | 5 per `plex_user_id` per 3600 s (09 §6; Phase 2) | in-process memory |

No route sets a `Retry-After` header and none reports remaining allowance (household-api.md §4). The app shows the `error` text and, where `retry_after` is present, a countdown. A limited person can still name a film or a person (the free path of `/request/search`) and can still add to the wanted list.

### 1.8 Versioning

`GET /api/v1/server` (§3.1) is the only capability endpoint the app reads. The rules:

| Field | The app's rule |
|---|---|
| `api` | integer, `1`. A server answering with a value the app does not know shows "update needed" and stops |
| `build` | the `BUILD` string (`api/routes.py:80`, `"missing-search-4"` today); shown on the Console Server screen, never compared |
| `features` | the `FEATURES` list (`api/routes.py:81-88`); the app hides "not in Plex" suggestions unless `"missing-search"` is present and otherwise ignores it |
| `min_app_version` | string `MAJOR.MINOR.PATCH`; compared numerically against `CFBundleShortVersionString`; when the app is older it shows "update needed" and disables sign-in (ADR §4 "Feature flag") |
| `native_signin` | boolean; when `false` the app does not attempt `POST /api/v1/auth/plex` and shows the same "update needed" screen with the server's own copy (ADR §4 "Feature flag") |
| `setup` | boolean; when `true` the app shows "this server has not been set up yet; open http://<host>:5050/setup on a computer" and attempts nothing else |

### 1.9 Timeouts and latency

| Routes | Client timeout | Why |
|---|---|---|
| `GET /api/v1/server` during the pairing probe | 1.5 s | design-A §4.1; the route never waits on Plex: it serves the cached `server_name` and `plex_machine_id` (§3.1) |
| `POST /request/search`, `POST /request/playlist/auto`, `GET /request/nudges` | 60 s | up to one model call (`ai.timeout` 15 s) plus TMDb lookups at 12 s each; tens of seconds are normal (household-api.md §3.1) |
| `GET|POST /wanted/recheck`, `POST /wanted/settle`, `GET /discover/missing`, `GET /members/<u>/picks` | 60 s | one Plex search per section per row, or a cold owned-index rebuild (owner-api.md §2) |
| everything else | 15 s | |

There is no pagination anywhere (owner-api.md §4.11): `/wanted` 500 rows, `/messages` 200 rows, `/request/wanted/list` 500 rows, `/api/v1/changes` at most 100 per call with a cursor.

## 2. Shared JSON shapes

Every shape the endpoints below refer to. Types are as produced by the Python; anything marked `|null` can be `null` and the Kit decodes it as an optional. The Kit's `Models` module has one lenient type per shape and decodes unknown keys by ignoring them.

### 2.1 Film, "library search" shape (`Film`, shape 2a)

Built by `search_plex` (`scanner/wanted_search.py:296-341`; household-api.md §2a). Used for `matches` on the title path of `POST /request/search`, for `matches` and `close` on `POST /request/wanted` and `GET /wanted/search`, and inside `now_in_plex` on `/wanted/recheck` and `/wanted/settle`.

| Field | Type | Notes |
|---|---|---|
| `title` | str | |
| `year` | int\|null | |
| `rating_key` | str | digits |
| `thumb` | str\|null | Plex-relative path, e.g. `/library/metadata/1234/thumb/1700000000`; render only through `GET /wanted/thumb` |
| `summary` | str | |
| `content_rating` | str | `""` when absent |
| `duration_ms` | int\|null | |
| `libraries` | [str] | section titles |
| `file_missing` | bool | |
| `missing_files` | [str] | absolute server paths; **stripped for non-owner sessions** (§7); the Kit field is `missingFiles: [String]?` |
| `added_at` | int | Unix seconds, `0` when unknown |
| `confidence` | int | `2` = it is that title, `1` = maybe (`scanner/wanted_search.py:263-293`) |

### 2.2 Film, "mood/person" shape (`Film`, shape 2b)

Built by `_movie_dict` (`api/request_assistant.py:653-670`; household-api.md §2b). Used for `matches` and `unavailable` on the mood, person and TV-title paths of `POST /request/search`.

| Field | Type | Notes |
|---|---|---|
| `title` | str | |
| `year` | int\|null | |
| `rating_key` | str | |
| `rating` | float\|null | |
| `runtime_minutes` | int\|null | |
| `genres` | [str] | |
| `content_rating` | str\|null | |
| `summary` | str | |
| `thumb` | str\|null | Plex path |
| `library` | str | one section title, not a list |
| `added_at` | int | Unix seconds |
| `file_missing` | bool | |
| `missing_files` | [str] | stripped for non-owner sessions (§7) |

The Kit has one `Film` type whose shape-2a-only fields (`duration_ms`, `libraries`, `confidence`, `content_rating` as non-optional) and shape-2b-only fields (`rating`, `runtime_minutes`, `genres`, `library`) are all optionals (ADR §2). A film card reads `rating` and `runtime_minutes` when present and shows nothing in their place when absent, as `request.html:1288-1292` does.

### 2.3 Show shape (`Show`, shape 2c)

`search_shows` (`scanner/wanted_search.py:83-126`) and `_show_dict` (`api/request_assistant.py:721-739`), extended by `add_season_detail` (`api/routes.py:1339-1354`); household-api.md §2c.

| Field | Type | Notes |
|---|---|---|
| `title` | str | |
| `year` | int\|null | |
| `rating_key` | str | |
| `thumb` | str\|null | Plex path |
| `art` | str\|null | Plex path |
| `summary` | str | |
| `seasons` | int\|null | `childCount` |
| `episodes` | int\|null | `leafCount` |
| `watched_episodes` | int | |
| `libraries` | [str] | |
| `confidence` | int | |
| `kind` | str | always `"show"` |
| `held_seasons` | [int] | present only on `shows` of the title path (`api/request_assistant.py:947-948`) and as `[]` from `_show_dict` (TV-mood path); absent from `search_shows` output, from shows inside `close` (`close_matches()`, `api/request_assistant.py:1081-1097`) and from `wanted.recheck.json` show hits; Kit field optional |
| `content_rating` | str\|null | only from `_show_dict`; absent from `search_shows` and from shows inside `close`; Kit field optional |
| `all_seasons` | [int] | from TMDb; present only on `result["shows"]` after `add_season_detail` (`api/routes.py:1339-1354`), `[]` when TMDb is unconfigured or errors; absent inside `close` and on `wanted.recheck.json` show hits; Kit field optional |
| `missing_seasons` | [int] | `all_seasons` minus `held_seasons`; same presence rule as `all_seasons`; on the TV-mood path this equals `all_seasons` because `held_seasons` is empty |

### 2.4 TMDb film guess (`FilmGuess`, shape 2d)

`tmdb_lookup.suggest` (`api/tmdb_lookup.py:109-165`; household-api.md §2d). Used for `did_you_mean`.

| Field | Type | Notes |
|---|---|---|
| `tmdb_id` | int\|null | passed back on `POST /request/wanted` (§4.2) |
| `title` | str | |
| `year` | int\|null | |
| `overview` | str | |
| `poster` | str | absolute `https://image.tmdb.org/t/p/w185/...` or `""`; loaded directly, not through `/wanted/thumb` |
| `language` | str | ISO 639-1 |
| `popularity` | float | |
| `original_title` | str | |
| `release_state` | str | `"out"`, `"cinema"` or `"unreleased"`; only on the first three guesses (`api/routes.py:1486-1492`) |
| `available_from` | str\|null | `YYYY-MM-DD`; same three guesses |

### 2.5 TMDb show guess (`ShowGuess`, shape 2e)

`tmdb_lookup.suggest_shows` (`api/tmdb_lookup.py:167-212`). Used for `did_you_mean_shows`.

| Field | Type | Notes |
|---|---|---|
| `title` | str | |
| `year` | int\|null | first air date year |
| `overview` | str | |
| `poster` | str | absolute or `""` |
| `language` | str | |
| `popularity` | float | |
| `tmdb_id` | int\|null | |
| `all_seasons` | [int] | first three guesses only (`api/routes.py:1496-1502`); `[]` on error |

### 2.6 TMDb discovery item (`DiscoveryItem`, shape 2f)

`tmdb_lookup.discover` / `discover_shows` (`api/tmdb_lookup.py:285-419, 456-536`). Used for `missing` on `POST /request/search` and `also_wanted` on `POST /request/playlist/auto`.

| Field | Type | Notes |
|---|---|---|
| `title` | str | |
| `year` | int\|null | |
| `overview` | str | |
| `poster` | str | |
| `rating` | float | one decimal place |
| `kind` | str | `"show"` only from `discover_shows`; absent for films |

### 2.7 Wanted row (`WantedRow`, shape 2g)

`dict(row)` of the `wanted` table (`db/database.py:131-171`; household-api.md §2g). Whole rows appear on the owner routes; `GET /request/wanted/list` builds a shorter dict (§4.3).

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `title` | str | |
| `year` | int\|null | |
| `search_query` | str | the raw text that created the row; owner routes only |
| `status` | str | `"wanted"`, `"acquired"`, `"dismissed"` (`WANTED_STATUSES`, `db/database.py:341`) |
| `notes` | str\|null | `"Requested by <username>"`, `"Plex lists this but the file is missing from disk"`, `"File missing from disk; surfaced by a search (<username>)"` or `"Suggested while building “<name>” for <username>"` |
| `added_at` | str | SQLite text |
| `requested_by_id` | str\|null | plex.tv account id as a string; owner routes only |
| `requested_by` | str\|null | username |
| `kind` | str | `"film"` or `"show"` |
| `season` | int\|null | shows only |
| `tmdb_id` | int\|null | new in Phase 2 (ADR §2 "wanted.tmdb_id"); absent on servers older than Phase 2, so the Kit field is optional |

Uniqueness is `(kind, lower(title), year, season)` (`db/database.py:166-171`).

### 2.8 `filters` dict (`Filters`, shape 2h)

`MovieFilters.model_dump()` (`api/request_assistant.py:186-241`). `{}` on the title, person and cheeky paths.

| Field | Type |
|---|---|
| `genres` | [str] |
| `exclude_genres` | [str] |
| `min_rating` | float\|null |
| `year_from` | int\|null |
| `year_to` | int\|null |
| `max_runtime_minutes` | int\|null |
| `min_runtime_minutes` | int\|null |
| `min_resolution` | str\|null (`"720"`, `"1080"`, `"4k"`) |
| `hdr` | bool |
| `atmos` | bool |
| `unwatched_only` | bool |
| `playlist_name` | str |
| `only_missing` | bool |
| `origin_country` | str (ISO 3166-1 alpha-2 or `""`) |
| `is_title` | bool |
| `interpretation` | str |

### 2.9 Message row (`Message`)

`messages` table (`db/database.py:121-129`): `id` int, `body` str (≤500), `context` str|null (≤200), `posted_by` str|null, `posted_by_id` str|null, `posted_at` str (SQLite), `read_at` str|null (SQLite).

### 2.10 Note row (`Note`)

`announcements` table (`db/database.py:109-116`): `id` int, `body` str (≤280), `url` str|null (always `null` from `/announce`, push-and-notifications.md §7.3), `posted_by` str|null, `posted_at` str, `retired_at` str|null.

### 2.11 Pick (`Pick`)

`api/suggestions.py:132-141, 173-181`: `title` str, `year` int|null, `rating_key` str, `rating` float|null, `thumb` str|null (Plex path), `library` str, `because` str, `collection` str. The nudge variant (§4.12) replaces `library` and `collection` with `content_rating` str|null and `line` str (≤160).

### 2.12 Persona (`Persona`)

`GET /persona` (`api/routes.py:829-857`; persona-themes-limits.md §1.3): `name` str (never empty, ≤40), `greeting` str (never empty, ≤200), `brush_offs` [str] (1 to 40), `images` {mood: str} (values are `/icons/<file>` paths; may be `{}`), `brush_off_moods` [str] (0 to 8), `smart_mode` bool. `voice` and `examples` are never on the wire (ADR §4 "Persona"; §7 of this document).

### 2.13 Wrapper shapes

Four bodies are objects wrapping a list; each has its own Kit type, named in §6.4: `SessionList` `{"sessions": [Session]}` (§3.5), `ThemeList` `{"themes": [Theme]}` (§4.1, §4.27), `NoteList` `{"notes": [Note]}` (§4.13), `WantedPage` `{"items": [WantedRow], "counts": {"wanted": int, "acquired": int, "dismissed": int}}` (§4.16). `GET /libraries` (§4.24) is a bare array and decodes as `[Library]`.

### 2.14 Theme (`Theme`)

`api/themes.py:62-80`: `slug` str (`^[a-z0-9][a-z0-9-]{0,30}$`), `name` str, `colors` object with exactly twelve `#rrggbb` keys: the six chosen `bg`, `surface`, `border`, `text`, `accent`, `red` and the six derived `surface2`, `dim`, `accent_dark`, `on_accent`, `top`, `green`; `wallpaper` str (`/themes/art/<slug>.png` or `""`). The four built-in palettes are not served by any endpoint; the Kit's `Themes` module carries them from `request.html:37-79` (ADR §2).

## 3. New `/api/v1` endpoints

All live in the blueprint `api/native.py` mounted at `/api/v1` (ADR §2). Every response is JSON; every failure is a non-`2xx` status with `error` and `code`. The guard treats `GET /api/v1/server` and `POST /api/v1/auth/plex` as open, `/api/v1/owner/*` as owner tier and everything else under `/api/v1` as household tier. Phase 1 builds §3.1 to §3.7 and §3.15; Phase 2 builds §3.8 to §3.14 (ADR §7).

### 3.1 `GET /api/v1/server`

Open. No parameters. Never fails except in the ways every route can (§1.4). Also served in setup mode (ADR §2 "config.py entering setup mode").

Response `200` (`ServerInfo`):

| Field | Type | Source |
|---|---|---|
| `api` | int | constant `1` |
| `build` | str | `BUILD` (`api/routes.py:80`) |
| `features` | [str] | `FEATURES` (`api/routes.py:81-88`) |
| `app_name` | str | `app.name` (default `"Plex Requests"`, `api/user_library.py:19-20`) |
| `server_name` | str\|null | `PlexServer(config['plex']['url'], config['plex']['token'], timeout=3).friendlyName`, read at most once per 600 s and kept in a module-level cache in `api/native.py` (same pattern as `_allowed_cache`, `api/plex_auth.py:29`); the cached pair is served while fresh and the route never blocks on Plex (plexapi's default connect timeout is 30 s, `venv/lib/python3.11/site-packages/plexapi/__init__.py:20`, longer than the 1.5 s probe of §1.9); `null` only when no read has ever succeeded or in setup mode |
| `plex_machine_id` | str\|null | `.machineIdentifier` from the same cached read; `null` as above. Not secret: every shared user sees it in their own `/api/v2/resources` (auth-and-tiers.md §1.5) |
| `urls` | object | `{"local": [str], "remote": [str]}` from config `app.urls`; both lists may be empty |
| `push` | object | `{"delivery": "relay" \| "polling"}`; `"relay"` when `data/relay.json` exists, is unexpired and has not been marked inactive by a relay `401`/`403` (design-A §2.2) |
| `setup` | bool | `true` only in setup mode |
| `native_signin` | bool | config `app.native_signin`, default `true` |
| `min_app_version` | str | config `app.min_app_version`, default `"1.0.0"` |

Rate limit: none. Side effects: none. Cache: `Cache-Control: no-store`.

Example:

```
GET /api/v1/server
Accept: application/json

200
{"api": 1, "build": "missing-search-4",
 "features": ["mood-search", "missing-search", "keyword-notice", "ask-companion-guard", "account-skins", "persona-by-name"],
 "app_name": "Plex Requests", "server_name": "Living room", "plex_machine_id": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
 "urls": {"local": ["http://192.168.1.10:5050"], "remote": ["https://nas.tail1234.ts.net"]},
 "push": {"delivery": "polling"}, "setup": false, "native_signin": true, "min_app_version": "1.0.0"}
```

### 3.2 `POST /api/v1/auth/plex`

Open, rate-limited (§1.7). The sign-in exchange (ADR §4 "Sign-in exchange"). The app has already minted a pin under its own `X-Plex-Client-Identifier` and linked it to the signed-in account with `PUT https://plex.tv/api/v2/pins/link` (verified: `plexapi/myplex.py:122, 1144-1155`); it then hands the pin to the server.

Request JSON:

| Field | Type | Required | Limits |
|---|---|---|---|
| `pin` | object | one of `pin` or `plex_token` | `{"id": int, "client_id": str}`; `client_id` is the app's per-install identifier that created the pin and is copied into the outbound `X-Plex-Client-Identifier` header, so it must match `^[A-Za-z0-9-]{8,64}$` and `pin.id` must be a positive integer; anything else is `400 bad_request` before plex.tv is contacted |
| `plex_token` | str | one of `pin` or `plex_token` | accepted only when `app.testing` is true or `request.is_secure` is true; otherwise `400 pin_required`. The app sends it only in the gate-1 fallback (ADR §6 item 1) and only to a typed HTTPS address. `is_secure` follows one hop of `X-Forwarded-Proto` (`ProxyFix(x_proto=1)`, `api/routes.py:119`, no trusted-proxy list) and is spoofable by a direct client, so this check is a guard against accidental misuse, not a boundary; the app's rule (send `plex_token` only to a typed `https://` address) is the control |
| `device` | object | yes | `id` str (UUID, ≤64, stable per install), `name` str (≤80, `UIDevice.current.name`), `model` str (≤40, e.g. `iPhone16,1`), `os` str (≤40, e.g. `iOS 18.6`), `app` str (≤40, e.g. `1.0 (42)`) |

Server-side order (ADR §4; design-A §3.1 step 7): limiter on `device.id` and overall; with `pin`, read `GET https://plex.tv/api/v2/pins/<id>` sending `X-Plex-Client-Identifier: <client_id>` and the server's usual headers (`api/plex_auth.py:32-39, 140-145`), refusing a pin whose `expiresIn` is not positive or which has no `authToken`; `plex_identity(token)` (`api/plex_auth.py:148-159`); the per-account limiter; `authorise(identity)` against `allowed_users()` (`api/plex_auth.py:162-200, 237-245`); `server_access_token(token)` (`api/plex_auth.py:203-234`); `start_session()` with the device columns, the per-server token encrypted as today (`api/plex_auth.py:323-340`), the session token stored as `sha256(token)`. The plex.tv account token is discarded. One session row per `device.id` (upsert), so a re-exchange never accumulates rows. A pin id is exchangeable once: after a successful `start_session()` the server inserts `pin.id` into a table `exchanged_pins (pin_id INTEGER PRIMARY KEY, at REAL)` purged of rows older than `PIN_LIFE`; a later exchange naming a pin id in that table is refused `401 pin_invalid` before plex.tv is contacted, so a LAN observer who replays the `{pin id, client_id}` pair with a `device.id` of their own gets nothing. The app mints a fresh hand-off pin for every exchange attempt. Phase 1 test: `test_pin_exchanged_once`.

Response `200`:

| Field | Type | Notes |
|---|---|---|
| `session.token` | str | 43 URL-safe characters (`secrets.token_urlsafe(32)`, `api/plex_auth.py:334`); shown once; stored in the Keychain |
| `session.expires_at` | str | ISO 8601; `last_seen` + 180 days for `ios` rows (ADR §4 "Session lifetime") |
| `session.device_id` | str | echo of `device.id` |
| `session.kind` | str | `"ios"` |
| `user.id` | str | plex.tv account id as a string |
| `user.username` | str | |
| `user.email` | str | from plex.tv, returned here only (auth-and-tiers.md §6.15) |
| `user.thumb` | str | avatar URL from plex.tv, returned here only |
| `user.role` | str | `"owner"` or `"shared"` |
| `server` | object | the `GET /api/v1/server` body (§3.1) |

Errors:

| Status | `code` | `error` |
|---|---|---|
| `400` | `bad_request` | `"device.id required"` and the like |
| `400` | `pin_required` | `"send a pin"`: neither field, or `plex_token` over plain HTTP outside tests |
| `401` | `pin_invalid` | `"Plex did not recognise that sign-in."`: plex.tv answered non-2xx for the pin, or the pin has expired |
| `409` | `pin_not_linked` | `"That sign-in has not finished at Plex yet."`: the pin exists but carries no `authToken` |
| `401` | `plex_token_invalid` | `"Plex did not recognise that sign-in."`: `/api/v2/user` refused the token |
| `403` | `not_shared` | `"That Plex account does not have access to this server."` (wording from `api/routes.py:1121-1124`) |
| `403` | `invite_pending` | `"Nearly there - Plex sent you an invite to this server that hasn't been accepted yet. Find it in your email or at plex.tv, accept it, then sign in here again."` (`api/routes.py:1133-1138`) |
| `429` | `rate_limited` | plus `rate_limited: true`, `retry_after` int |
| `502` | `plex_tv_unreachable` | `"could not reach plex.tv: <text>"` |
| `503` | `plex_unavailable` | the Plex server itself is unreachable so the roster cannot be built |

Side effects: a `sessions` row inserted or replaced; `last_seen` set. Rate limit: §1.7.

Example:

```
POST /api/v1/auth/plex
{"pin": {"id": 123456789, "client_id": "7A1E0B2C-4D5F-4E6A-8B9C-0D1E2F3A4B5C"},
 "device": {"id": "0C9F1E2D-3B4A-4C5D-8E7F-6A5B4C3D2E1F", "name": "Jules's iPhone",
            "model": "iPhone16,1", "os": "iOS 18.6", "app": "1.0 (42)"}}

200
{"session": {"token": "k3J9...43chars...Qw", "expires_at": "2027-03-20T09:14:00Z",
             "device_id": "0C9F1E2D-3B4A-4C5D-8E7F-6A5B4C3D2E1F", "kind": "ios"},
 "user": {"id": "12345678", "username": "jules", "email": "jules@example.com",
          "thumb": "https://plex.tv/users/abc/avatar", "role": "shared"},
 "server": { ...as §3.1... }}
```

### 3.3 `GET /api/v1/auth/session`

Household. The per-launch identity check. Unlike `GET /auth/me` it does not probe Plex with the person's token (auth-and-tiers.md §6.14); a dead per-server token surfaces as `401` from the first real call, as it does today (`expired_session`, `api/routes.py:1168-1184`).

Response `200`:

| Field | Type | Notes |
|---|---|---|
| `user.id` | str | |
| `user.username` | str | |
| `user.role` | str | current roster role |
| `user.default_playlist` | str | `"<username>'s Picks"` (`api/user_library.py:23-24`) |
| `user.skin` | str | look slug from config `skins`, or `""` (`api/routes.py:91-107`) |
| `app_name` | str | |
| `session.device_id` | str | |
| `session.kind` | str | `"ios"` or `"web"` |
| `session.created_at` | str | ISO 8601 |
| `session.last_seen` | str | ISO 8601 |
| `session.expires_at` | str | ISO 8601 |
| `server` | object | §3.1 body |

Note: design-A §2.2 lists `email` and `thumb` here; the ADR's session columns (§2) do not store them, so they are returned only by §3.2 and the app keeps them in `UserDefaults` under `server.<machine_id>.email` and `server.<machine_id>.thumb` (02 §4.2; not the Keychain, which holds secrets only).

Errors: `401` only. Side effects: `last_seen` updated (which slides an `ios` session).

Example: `GET /api/v1/auth/session` → `200 {"user": {"id": "12345678", "username": "jules", "role": "shared", "default_playlist": "jules's Picks", "skin": ""}, "app_name": "Plex Requests", "session": {"device_id": "0C9F…", "kind": "ios", "created_at": "2026-09-21T08:00:00Z", "last_seen": "2026-09-21T08:12:03Z", "expires_at": "2027-03-20T08:12:03Z"}, "server": {…}}`.

### 3.4 `DELETE /api/v1/auth/session`

Household. Signs out this device: sets `revoked_at` on the row named by the bearer (as §3.6 does); the next request with that bearer is `401 session_revoked`. Response `204`. Errors: `401`. The app then wipes the Keychain items for that server. `POST /auth/logout` is not used.

### 3.5 `GET /api/v1/auth/sessions`

Household. Own rows for a member; every row for the owner, each with `username` (ADR §2).

Response `200 {"sessions": [Session]}` where `Session` is:

| Field | Type | Notes |
|---|---|---|
| `id` | int | row id used by §3.6 |
| `kind` | str | `"ios"` or `"web"` |
| `device_name` | str\|null | `null` for `web` rows |
| `platform` | str\|null | the `device.os` string |
| `app_version` | str\|null | the `device.app` string |
| `created_at` | str | ISO 8601 |
| `last_seen` | str | ISO 8601 |
| `expires_at` | str | ISO 8601 |
| `current` | bool | true for the row that made this request |
| `username` | str | present only when the caller is the owner |

Note: design-A §2.2 puts `"ios" | "web"` in `platform`; the ADR's column list names that column `kind`, so `kind` carries it and `platform` is the OS string. Revoked rows (`revoked_at` set) are not listed.

Example: `200 {"sessions": [{"id": 12, "kind": "ios", "device_name": "Jules's iPhone", "platform": "iOS 18.6", "app_version": "1.0 (42)", "created_at": "2026-09-21T08:00:00Z", "last_seen": "2026-09-21T08:12:03Z", "expires_at": "2027-03-20T08:12:03Z", "current": true}, {"id": 9, "kind": "web", "device_name": null, "platform": null, "app_version": null, "created_at": "2026-08-01T19:20:00Z", "last_seen": "2026-09-20T22:01:10Z", "expires_at": "2026-10-30T19:20:00Z", "current": false}]}`.

### 3.6 `DELETE /api/v1/auth/sessions/<int:id>`

Household. Sets `revoked_at` on the named row when it belongs to the caller, or to anyone when the caller is the owner (ADR §2). Response `204`. Errors: `404 {"error": "no session with id N", "code": "not_found"}` (also for another person's row when the caller is not the owner, so existence is not leaked); `401`. A revoked session's next request receives `401`. Deleting the current row is allowed and equals §3.4.

### 3.7 `DELETE /api/v1/auth/sessions`

Household. "Sign out everywhere": sets `revoked_at` on every row of the caller, including the current one. Response `204`. Errors: `401`.

### 3.8 `POST /api/v1/push/devices`

Household. Registers this device for relay push (design-A §5.1). The app has already posted its APNs token to the relay (`05-push-relay.md`, `POST /v1/devices`) and holds the handle.

Request JSON:

| Field | Type | Required | Limits |
|---|---|---|---|
| `device_id` | str | yes | the same value as `device.id` in §3.2; ≤64 |
| `relay_handle` | str | yes | opaque, ≤128; the handle, never the device secret |
| `environment` | str | yes | `"production"` or `"sandbox"` |
| `app` | str | yes | ≤40 |

Table `native_devices` (design-A §2.2): `id`, `plex_user_id`, `username`, `device_id UNIQUE`, `relay_handle`, `environment`, `app_version`, `created_at`, `last_sent`, `failures`. Upsert on `device_id`; a row is always bound to the session's user id, never to a body field. At most five rows per user (ADR §2).

Response `200 {"delivery": "relay" | "polling"}`: the current value of `push.delivery` (§3.1). Errors: `400 bad_request`; `409 {"error": "That is five devices already - remove one first.", "code": "device_limit", "limit": 5}`; `401`. Side effects: the row. Rate limit: none beyond the cap.

Example: `POST /api/v1/push/devices {"device_id": "0C9F…", "relay_handle": "h_7Yk…", "environment": "production", "app": "1.0 (42)"}` → `200 {"delivery": "relay"}`.

### 3.9 `DELETE /api/v1/push/devices/<device_id>`

Household. Deletes the caller's own row with that `device_id`. Response `204`. Errors: `404 not_found`; `401`. Called from Settings ("stop notifications on this phone") after the app has deleted its relay registration (ADR §4 "Relay data deletion").

### 3.10 `POST /api/v1/push/test`

Household. Sends the `test` event to the caller through every channel: `send_event(db, "test", user["id"], url="/", tag="test")` as `POST /push/test` does today (`api/routes.py:919-930`), which after Phase 2 writes an `events` row, delivers by web push to the caller's browser rows and by the relay to the caller's `native_devices` rows.

Response `200`:

| Field | Type | Notes |
|---|---|---|
| `sent` | int | deliveries that reported `sent` across both channels |
| `failed` | [str] | one entry per failed delivery, as `api/push.py:216-222` builds them |
| `delivery` | str | `"relay"` or `"polling"` |
| `skipped` | str | present only when config has no `notifications.test` block or it is disabled (`api/push.py:194-196`); the app then says so |

In the polling state `sent` is `0` for a phone but the `events` row exists, so the next `GET /api/v1/changes` returns it and the app posts it as a local notification; that is how "Send a test" proves the fallback path. Errors: `401`; `429 {"error": "That's a lot of test pushes - give it an hour.", "code": "rate_limited", "rate_limited": true}` after five calls by one `plex_user_id` in an hour (§1.7; 09 §6), which the Bell screen shows as `.rateLimited`.

### 3.11 `GET /api/v1/changes?since=<int>&limit=<int>`

Household. The polling fallback, the deep-link resolver and the only record that a notification happened (design-A §2.2; push-and-notifications.md §7.5).

Query: `since` int, default `0`, the `cursor` from the previous call; `limit` int, default `50`, maximum `100` (larger values are clamped).

Table `events` (append-only): `id`, `at`, `kind`, `recipient_id` (nullable), `actor_id`, `ref`, `title`, `body`. Written inside `send_event()` and `broadcast()` (`api/push.py:188, 225`), before any delivery is attempted, with the rendered title and body. Rows older than 30 days are purged on insert. Visibility: a row is returned when `recipient_id` equals the caller's id, or when `recipient_id` is `null` (a broadcast) and `actor_id` is not the caller. Owner-only kinds (`requested`, `file_missing`, `message`; `OWNER_ONLY`, `api/push.py:180`) can therefore only ever reach the owner.

Response `200`:

| Field | Type | Notes |
|---|---|---|
| `cursor` | int | the highest `events.id` in the table at the time of the call, whether or not it was visible to the caller; stored by the app in `UserDefaults` and sent as `since` next time |
| `events` | [ChangeEvent] | ascending by `id`, at most `limit`, all with `id > since` |
| `unread_messages` | int | present only for the owner: `messages` rows with `read_at IS NULL` |
| `wanted_counts` | object | `{"wanted": int, "acquired": int, "dismissed": int}` (`db.count_wanted()`) |

`ChangeEvent`:

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `at` | str | ISO 8601 |
| `kind` | str | `"arrived"`, `"requested"`, `"file_missing"`, `"message"`, `"announcement"`, `"test"` |
| `ref` | str\|null | `"wanted:<id>"` (arrived, requested), `"message:<id>"`, `"note:<id>"`; `null` for `test` and `file_missing` (its tag `missing-<title>` carries no id, push-and-notifications.md §7.1) |
| `title` | str | rendered from the catalogue (`api/push.py:202-205`) |
| `body` | str | rendered |

The app routes `wanted:<id>` to the wanted list (owner: Requests), `message:<id>` to the Inbox and `note:<id>` to the Bell (design-A §5.2), and de-duplicates on the key `<srv>|<kind>|<ref>` (`ChangeRouting.pushKey`, 04 §2.7; a push carries `ev`, `ref`, `srv` and never `events.id`, 05 §7.4) against pushes already received. When more than `limit` rows are pending the app calls again with the returned cursor until `events` is empty.

Errors: `400 bad_request` for a non-integer `since` or `limit`; `401`. Side effects: none. Rate limit: none; the app calls it on every foreground and from `BGAppRefreshTask`.

Example:

```
GET /api/v1/changes?since=900&limit=50

200
{"cursor": 918,
 "events": [{"id": 917, "at": "2026-09-21T08:12:03Z", "kind": "arrived", "ref": "wanted:42",
             "title": "It's here", "body": "Dune (2021) is now on Plex - the film you asked for."}],
 "unread_messages": 2, "wanted_counts": {"wanted": 3, "acquired": 9, "dismissed": 1}}
```

### 3.12 `POST /api/v1/owner/relay`

Owner. Stores the relay server key the owner's app obtained from the relay's `POST /v1/server-keys` (`05-push-relay.md`). Written to `data/relay.json` with mode `0600` (created with `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` as `api/secrets_store.py:24-29` does, never `write_text` then `chmod` as `api/push.py:44-46` does; `mark_inactive()` rewrites it the same way).

Request JSON: `server_key` str (required, ≤256), `relay_url` str (required, `https://` only, ≤200), `expires_at` str (required, ISO 8601). Response `200 {"delivery": "relay", "relay_expires_at": str}`. Errors: `400 bad_request` (missing field, non-HTTPS `relay_url`, unparseable `expires_at`); `403`; `401`. Side effects: the file; `push.delivery` flips to `"relay"`; the inactive mark set by an earlier relay `401`/`403` is cleared.

Example: `POST /api/v1/owner/relay {"server_key": "sk_…", "relay_url": "https://push.example.com", "expires_at": "2027-09-21T00:00:00Z"}` → `200 {"delivery": "relay", "relay_expires_at": "2027-09-21T00:00:00Z"}`.

### 3.13 `DELETE /api/v1/owner/relay`

Owner. Removes `data/relay.json`. Response `204`; also `204` when no file exists. Side effects: `push.delivery` becomes `"polling"`. This only forgets the key locally; "delete my relay data" is the app calling the relay's `DELETE /v1/server-keys/<id>` first (ADR §4 "Relay data deletion").

### 3.14 `GET /api/v1/owner/status`

Owner. The Console's Server and Notifications screens (design-A §2.2, §5.4).

Response `200`:

| Field | Type | Notes |
|---|---|---|
| `plex.reachable` | bool | one `PlexServer(...)` connect with the owner token |
| `plex.name` | str\|null | friendly name |
| `plex.version` | str\|null | |
| `push.delivery` | str | `"relay"` or `"polling"` |
| `push.native_devices` | int | rows in `native_devices` |
| `push.web_devices` | int | rows in `push_subscriptions` |
| `push.relay_expires_at` | str\|null | from `data/relay.json` |
| `webhook.configured` | bool | `plex.webhook_key` is set |
| `webhook.last_hit_at` | str\|null | ISO 8601; the last accepted `POST /plex/webhook` |
| `settle.every_minutes` | int | `app.settle_every_minutes`; `0` means off |
| `settle.last_run_at` | str\|null | ISO 8601 |
| `build` | str | `BUILD` |

The server key itself is never returned. Errors: `403`, `401`. Side effects: none.

### 3.15 The JSON error handler

An unhandled exception on any `/api/v1` path, or on any request carrying `Authorization: Bearer`, is answered `500 {"error": "something went wrong on the server", "code": "internal"}` rather than Flask's HTML page (ADR §2; owner-api.md §1.4, which names `/libraries` and `/summary` as routes with no `try/except`). The exception text is logged server-side, never sent. The guard's `401` on the same paths carries `code` per §1.4a.

## 4. Existing endpoints the app calls unchanged

Each entry gives the tier, the request, the response field by field, the errors, the side effects and the rate limit as the code behaves today, with the research citation. Phase 1 and 2 touch none of these bodies except where a line says "Phase 2:".

### 4.1 Open routes

**`GET /persona`** (`api/routes.py:829-857`; persona-themes-limits.md §1.3). No parameters. Always `200` with the `Persona` shape (§2.12); `Cache-Control: no-store`. The app fetches it once per launch and after every sign-in. Example: `200 {"name": "Reel", "greeting": "Name a film, a series, or just say what you fancy.", "brush_offs": ["Not that. Name a film."], "images": {"greeting": "/icons/reel-waving.png"}, "brush_off_moods": [], "smart_mode": true}`.

**`GET /themes`** (`api/routes.py:781-787`; owner-api.md §2). No parameters. `200 {"themes": [Theme]}` (`ThemeList`, §2.13; `Theme` is §2.14); `Cache-Control: no-store`. The list may be empty.

**`GET /themes/art/<slug>.png`** (`api/routes.py:789-797`). PNG bytes, `Cache-Control: public, max-age=600`; `404 {"error": "no such look"}`. Loaded through the Kit's image loader, not `URLSession` elsewhere.

**`GET /icons/<path>`** (`api/routes.py:932-936`). Persona artwork named in `images`; PNG bytes, `Cache-Control: public, max-age=600`; `404` when absent. The app removes a face whose image fails, as `request.html:965-971` does.

**`GET /health`** (`api/routes.py:127-132`). `200 {"status": "ok", "build": str, "features": [str]}`, or `{"status": "setup"}` in setup mode (ADR §7 Phase 8). Not called by the app; `GET /api/v1/server` replaces it. Listed so the builder does not add it to the Kit.

### 4.2 `POST /request/search` (household; `api/routes.py:1385-1574`; household-api.md §3.1)

Request JSON:

| Field | Type | Required | Limits |
|---|---|---|---|
| `query` | str | yes | whitespace collapsed, cut to `app.limits.max_query_chars` (200) (`api/limits.py:138-141`); empty → `400 {"error": "tell me what you fancy watching"}` |
| `include_missing` | bool | no | truthy also runs TMDb discovery (`api/routes.py:1523`) |
| `who` | str | no | ignored whenever a session exists (`api/routes.py:1389`); the app never sends it |

Order of work: free title lookup, then person lookup, on a hit returned without spending allowance; else the allowance is reserved (`ai_limits.check`/`record`, `api/routes.py:1412-1416`) and `mood_search` runs; a refund follows whenever `translated_by != "model"` (`api/routes.py:1552-1558`).

Response `200`, a union keyed on `kind`. Always present: `request` str, `kind` str, `translated_by` str, `filters` (§2.8), `interpretation` str, `playlist_name` str, `matches` [Film], `unavailable` [Film], `captured_missing` [{`title` str, `year` int|null}], `smart_mode` bool. By path:

| `kind` | `translated_by` | Extra fields | Built at |
|---|---|---|---|
| `"title"` (film or series named) | `"title"` | `shows` [Show 2c with `held_seasons`, `all_seasons`, `missing_seasons`]; `matches` in shape 2a | `api/request_assistant.py:927-978` |
| `"title"` (person named) | `"person"` | `shows: []`; `matches` in shape 2b; `playlist_name` = the names | `api/request_assistant.py:1023-1078` |
| `"title"` (television mood) | `"model"`, `"cache"` or `"keywords"` | `for_age` int; `shows` [Show 2c]; `matches: []` | `api/request_assistant.py:1141-1162` |
| `"cheeky"` | `"cheeky"` | `interpretation` is a `persona.brush_offs` line; `matches: []`; no `shows` key | `api/request_assistant.py:1119-1129` |
| `"missing_only"` | model/cache/keywords | `for_age`; `shows: []`; `held_titles` [str]; `wants_shows` bool; `missing` [DiscoveryItem 2f]; `missing_are_shows` bool | `api/request_assistant.py:1164-1181`; `api/routes.py:1520-1549` |
| `"mood"` | model/cache/keywords | `for_age`; no `shows` key; `missing` and `missing_are_shows` only when `include_missing` or `filters.only_missing` | `api/request_assistant.py:1183-1230` |
| `"title_missing"` | model/cache/keywords, or `title` when TMDb names the film exactly and the model is skipped (`names_a_film()`, `api/request_assistant.py`; then `matches` is `[]` and `filters` is the defaults with `is_title: true`) | as `"mood"` plus `did_you_mean` [FilmGuess 2d, ≤5], `did_you_mean_shows` [ShowGuess 2e, ≤5], `close` [Film 2a and Show 2c mixed; the Show entries come from `search_shows` through `close_matches()` (`api/request_assistant.py:1081-1097`) and carry none of `held_seasons`, `all_seasons`, `missing_seasons`, `content_rating`] | `api/routes.py:1484-1511` |

The Kit's `SearchResult` is an enum on `kind` with the extras as optionals; `close` decodes as an array of a `FilmOrShow` enum keyed on the presence of `kind == "show"`. `translated_by == "keywords"` with `smart_mode == true` means the model fell back this once; with `smart_mode == false` the server has no key (`request.html:1675-1694`).

Errors: `400`; `401` with `auth_required` from `expired_session()` when the person's Plex token is dead; `429 {"error", "rate_limited": true, "hint": "You can still search for a film by name, and ask for anything we don't have."}`; `503`; `500 {"error": str}`. Side effects (mood, `title_missing` and `missing_only` paths only; the title and person paths return at `api/routes.py:1406-1409` before the capture): `unavailable` films newly captured into `wanted` with note `"File missing from disk; surfaced by a search (<username>)"` and one `file_missing` push to the owner (`api/routes.py:1446-1481`). Rate limit: §1.7.

Example (title path):

```
POST /request/search  {"query": "Dune 2021"}

200
{"request": "Dune 2021", "kind": "title", "translated_by": "title", "filters": {},
 "interpretation": "We have that.", "playlist_name": "",
 "matches": [{"title": "Dune", "year": 2021, "rating_key": "5123", "thumb": "/library/metadata/5123/thumb/1700000000",
              "summary": "…", "content_rating": "12A", "duration_ms": 9330000, "libraries": ["Films"],
              "file_missing": false, "added_at": 1700000000, "confidence": 2}],
 "unavailable": [], "shows": [], "captured_missing": [], "smart_mode": true}
```

### 4.3 `POST /request/wanted` (household; `api/routes.py:1981-2050`; household-api.md §3.2)

Request JSON:

| Field | Type | Required | Limits |
|---|---|---|---|
| `title` | str | yes | collapsed, cut to 200; the app sends `"Title (Year)"` so the year parses (`request.html:1763`) |
| `kind` | str | no | exactly `"show"` → show; anything else → film |
| `season` | int | no | kept only when `0 < season < 100`; ignored for films |
| `tmdb_id` | int | no | GAP: Phase 2 addition; stored on the row so `recheck()` can match by `Guid` (ADR §4 "Arrival matching"). Sent when the request came from a `did_you_mean`, `did_you_mean_shows` or `missing` item |

Film path (`search_and_capture`, `scanner/wanted_search.py:351-402`) searches Plex with the owner's token and inserts a row only when nothing playable was found. Response `200`:

| Field | Type | Notes |
|---|---|---|
| `query` | str | |
| `title` | str | |
| `year` | int\|null | |
| `found` | bool | |
| `matches` | [Film 2a] | confidence 2 only |
| `close` | [Film 2a] | confidence 1 with a file; absent when the title parses to nothing |
| `file_missing` | bool | listed in Plex, every file gone; absent as `close` |
| `added_to_wanted` | bool | |
| `already_wanted` | bool | |
| `wanted` | WantedRow\|null | the row before the requester update; `requested_by`/`requested_by_id` read `null` here (`api/routes.py:2023`) |

Show path (`api/routes.py:2005-2016`): no Plex search; `200 {"found": false, "kind": "show", "season": int|null, "title": str, "year": int|null, "added_to_wanted": bool, "already_wanted": bool, "wanted": WantedRow, "matches": []}`.

Errors: `400 {"error": "which film?"}`; `429` (§1.7); `503`. Side effects when `added_to_wanted`: `notes` set to `"Requested by <username>"`, `requested_by` and `requested_by_id` set, a `requested` push to the owner unless the requester is the owner (`api/routes.py:2017-2045`). The app shows "we already have that" on `found`, "asked for" on `added_to_wanted`, else a line keyed on `wanted.status` (`request.html:1907-1958`).

Example: `POST /request/wanted {"title": "Chef (2014)", "tmdb_id": 212778}` → `200 {"query": "Chef (2014)", "title": "Chef", "year": 2014, "found": false, "matches": [], "close": [], "file_missing": false, "added_to_wanted": true, "already_wanted": false, "wanted": {"id": 42, "title": "Chef", "year": 2014, "search_query": "Chef (2014)", "status": "wanted", "notes": "Requested by jules", "added_at": "2026-09-21 08:12:03", "requested_by_id": null, "requested_by": null, "kind": "film", "season": null, "tmdb_id": 212778}}`.

### 4.4 `GET /request/wanted/list` (household; `api/routes.py:2052-2075`; household-api.md §3.3)

No parameters. Every row of every status, newest first, at most 500, for the whole household. Response `200 {"items": [WantedListItem], "counts": {"wanted": int, "acquired": int, "dismissed": int}}` where `WantedListItem` is exactly `id` int, `title` str, `year` int|null, `kind` str, `season` int|null, `status` str, `requested_by` str|null, `added_at` str (SQLite) (`api/routes.py:2065-2074`). No `requested_by_id`, `notes`, `search_query` or `tmdb_id`. The app groups by status as Waiting, Arrived, Set aside (`request.html:1869-1904`). Errors: `401`. No side effects.

### 4.5 `GET /request/playlists` (household; `api/routes.py:1275-1286`; household-api.md §3.4)

No parameters. Uses the person's own Plex token. `200 {"playlists": [{"title": str, "items": int}], "default": "<username>'s Picks"}`. Any exception, including a missing token, is `200 {"error": str, "playlists": []}` (§1.5).

### 4.6 `GET /request/playlist/items?name=<title>` (household; `api/routes.py:1305-1320`; household-api.md §3.5)

`name` matched case-insensitively; only `movie` items are listed. `200 {"playlist": str, "films": [{"title": str, "year": int|null, "rating_key": str, "thumb": str|null, "rating": float|null, "runtime_minutes": int|null, "added_at": int}]}`. Errors: `400 {"error": "which playlist?"}`; `401 {"error": "sign in again to see your playlists"}` (no `auth_required`); `404 {"error": "no playlist called '<name>'", "films": []}`; `500 {"error": str}`.

### 4.7 `POST /request/playlist/remove` (household; `api/routes.py:1322-1337`; household-api.md §3.6)

Body `{"playlist": str (≤80), "rating_keys": [str] (≤60)}`. `200 {"playlist": str, "removed": int}`; an unknown playlist is `200 {"error": "no playlist called '<name>'"}` (§1.5). `400` when either field is empty; `401` without `auth_required`; `500`. Scoped to the person's own token.

### 4.8 `POST /request/playlist` (household with an owner branch; `api/routes.py:1576-1627`; household-api.md §3.7)

| Field | Type | Required | Limits |
|---|---|---|---|
| `rating_keys` | [str] | yes | first 60; keys that do not fetch are skipped |
| `playlist` | str | no | ≤80; empty → `"<username>'s Picks"` |
| `for_user` | str | no | ≤60; a username from `GET /members`; owner only for anyone but oneself |

Responses: created `{"playlist": str, "added": int, "created": true}`; existing `{"playlist": str, "added": int, "already_there": int, "created": false}`; either gains `"for_user": str` when gifting; nothing fetched `200 {"error": "none of those films could be found"}` (§1.5). Errors: `400 {"error": "pick at least one film"}`; `401 {"error": "sign in again to use your own playlists"}`; `403 {"error": "only the owner can build a list for somebody else"}`; `409 {"error": "<name> has not signed in here yet, so there is nowhere to put it. Ask them to open the app once."}`; `409 {"error": "<name>'s sign-in has expired. Ask them to open the app once and try again."}`; `500`. Side effects: a playlist in the caller's, or the member's, Plex account; a new gifted playlist gets the summary `"Made for you by <owner username>"` (`api/routes.py:1608`). Gifting is free for the owner (ADR §4 "What is paid").

### 4.9 `POST /request/playlist/auto` (household; `api/routes.py:1629-1764`; household-api.md §3.8)

| Field | Type | Required | Limits |
|---|---|---|---|
| `query` | str | yes | cleaned as §4.2 |
| `count` | int | no | default 12, cap 40; the app always sends an integer because a non-numeric value is a `500` |
| `playlist` | str | no | falls back to the search's `playlist_name` |
| `include_missing` | bool | no | round the list out from TMDb |

`200 {"playlist": str, "added": int, "created": bool, "already_there": int (only when created is false), "interpretation": str, "films": [{"title": str, "year": int|null}], "also_wanted": [{"title", "year", "poster", "rating"}], "wanted_are_shows": bool (only when rounding out)}`; or the brush-off `200 {"kind": "cheeky", "interpretation": str, "matches": [], "added": 0}`. Errors: `400 {"error": "say what the playlist should be"}`; `400 {"error": "Say what the playlist should be - a mood, a genre, an occasion."}`; `429 {"error", "rate_limited": true}`; `404 {"error": "nothing in the library matches that", "interpretation": str}`; `503` (a dead token also surfaces as `503` here, `api/routes.py:1675-1677`). Side effects: the playlist; up to 5 `wanted` rows with `notes = "Suggested while building “<name>” for <username>"` and `requested_by` set, no owner push. Always the caller's own playlist.

### 4.10 `GET /request/detail?key=<rating_key>` (household; `api/routes.py:1288-1303`; household-api.md §3.9)

`key` all digits else `400 {"error": "which one?"}`. Fetched with the person's own token. `200`:

| Field | Type | Notes |
|---|---|---|
| `kind` | str | `"movie"`, `"show"` or other |
| `title`, `year`, `summary`, `tagline` | str, int\|null, str, str | |
| `rating`, `critic_rating` | float\|null | |
| `content_rating`, `studio` | str\|null | |
| `genres` | [str] | ≤8 |
| `thumb`, `art` | str\|null | Plex paths |
| `rating_key` | str | |
| `runtime_minutes` | int\|null | |
| `added_at` | int | |
| `directors`, `writers`, `cast` | [str] | movie: ≤3, ≤3, ≤8 |
| `quality` | str | movie only, e.g. `"1080 · H264 · AAC"` or `""` |
| `cast`, `seasons`, `episodes`, `watched_episodes` | [str], int\|null, int\|null, int | show only |
| `season_list` | [{`season` int, `episodes` int\|null}] | show only |

Errors: `401 {"error": "sign in again"}`; `404 {"error": "not on the shelf"}`; `500 {"error": str}`.

### 4.11 `GET /request/showcase` (household; `api/routes.py:1766-1775`; household-api.md §3.10)

No parameters, no allowance. `200 {"rows": [{"title": str, "films": [{"title": str, "year": int|null, "rating_key": str, "thumb": str, "art": str|null, "tagline": str, "rating": float|null}]}]}`; rows in the order `"Just added"`, `"Best of the shelf"`, `"Series on the shelf"`, `"Nobody has watched these yet"` when non-empty, 14 per row; a series' `tagline` is `"<n> seasons"`. Failure is `200 {"rows": [], "error": str}` (§1.5).

### 4.12 `GET /request/suggestions` and `GET /request/nudges?age=<int>` (household; household-api.md §3.11-3.12)

`/request/suggestions` (`api/routes.py:1777-1786`): `200 {"picks": [Pick], "reason": "following on from what you have watched" | "well regarded and unwatched", "playlist": "<username>'s Picks"}`; failure `200 {"error": str, "picks": []}`. Shown only when the person has no playlists (`request.html:3015-3030`).

`/request/nudges` (`api/routes.py:1941-1979`): costs a model call (kind `nudge`). Always `200`: `{"picks": [{"title", "year", "rating_key", "rating", "thumb", "content_rating", "because": str, "line": str}]}`, or `{"picks": [], "rate_limited": true, "error": str}`, or `{"picks": [], "error": str}`. The app asks at most once per calendar day per device, as `request.html:2982-3013` does.

### 4.13 `GET /request/announcements` (household; `api/routes.py:1788-1796`; household-api.md §3.13)

`200 {"notes": [Note]}`: the three newest un-retired notes (`db/database.py:427-432`). Read state is per device in the app (`UserDefaults`), as the page keeps it in `localStorage` (push-and-notifications.md §5).

### 4.14 `POST /request/message` (household; `api/routes.py:1866-1902`; household-api.md §3.14)

Body `{"body": str (≤500, required), "context": str (≤200, optional; the current search text)}`. `200 {"message": Message, "sent": int}`. Errors: `400 {"error": "say what went wrong first"}`; `429` (§1.7). Side effects: a `messages` row; a `message` push to the owner unless the sender is the owner. The app says "Their phone has been told" when `sent > 0`, else "It's on their console" (`request.html:2286-2288`).

### 4.15 `GET /wanted/thumb?path=&width=&height=` (household; `api/routes.py:2077-2111`; household-api.md §3.17)

`path` must match `^/library/metadata/\d+/(thumb|art|poster|composite)/` with no `..`, else `400 {"error": "not a Plex artwork path"}`; `width` default 200 max 1200; `height` default 300 max 800. Image bytes with Plex's `content-type` (default `image/jpeg`) and `Cache-Control: public, max-age=86400`; `502 {"error": "Plex is unavailable: ..."}`. Uses the owner's token, so any signed-in member can fetch any artwork path. The app only ever passes `thumb`/`art` values it received from a response.

### 4.16 `GET /wanted?status=` (owner; `api/routes.py:578-593`; owner-api.md §2)

`status` default `"wanted"`; `"all"` for every row; other values `400 {"error": "unknown status 'x'", "expected": ["wanted", "acquired", "dismissed", "all"]}`. `200 {"items": [WantedRow], "counts": {...}}`, newest first, 500 rows. Whole rows, including `requested_by_id`, `notes`, `search_query` and (Phase 2) `tmdb_id`.

### 4.17 `GET /wanted/search?q=` (owner; `api/routes.py:562-576`; owner-api.md §2)

Blank `q` → `400 {"error": "q required", "example": {"q": "Chef (2014)"}}`. `200 {"query": str, "title": str, "year": int|null, "found": bool, "matches": [Film 2a], "close": [Film 2a], "added_to_wanted": bool, "already_wanted": bool, "wanted": WantedRow|null, "file_missing": bool}` (`close`, `file_missing` absent when nothing parsed). Side effect: a `wanted` row of kind `film` when nothing playable matches, which is how Discover's "Want it" works (`dashboard/index.html:1430-1434`). `503`. GAP: `&tmdb_id=<int>` query parameter so a Discover pick stores its `tmdb_id` on the row (same Phase 2 change as §4.3).

### 4.18 `POST /wanted/<int:id>/status` (owner; `api/routes.py:595-611`; owner-api.md §2)

Body `{"status": "acquired" | "dismissed" | "wanted"}`. `200` is the updated `WantedRow` itself, not wrapped. Errors: `400 {"error": "status required", "example": {"status": "acquired"}}`; `400 {"error": "Unknown status 'x'; expected one of wanted, acquired, dismissed"}`; `404 {"error": "no wanted entry with id N"}`. No notification is sent.

### 4.19 `GET|POST /wanted/recheck` and `POST /wanted/settle` (owner; `api/routes.py:697-707, 658-667`; owner-api.md §2)

`/wanted/recheck`: `200 {"checked": int, "now_in_plex": [WantedRow + {"matches": [Film 2a | Show 2c]}]}`; no side effects; `503`. Phase 2: `recheck()` matches by `Guid` (`tmdb://`, `imdb://`, `tvdb://`) before folded title and year, searches show sections for `kind == "show"` rows and checks the season is present (ADR §2), so `matches` for a show row is in shape 2c. `/wanted/settle`: runs `recheck`, marks each hit `acquired`, sends `arrived` to `requested_by_id` and then to every device kind before falling back to the owner (ADR §2 "settle_wanted() loop fix"); `200 {"checked": int, "settled": ["Title (Year)"]}`. Both are synchronous and slow (§1.9); the app calls `/wanted/settle` from the Requests screen and never on a timer, because the settle scheduler does that (ADR §2).

### 4.20 `GET /messages?unread=1` and `POST /messages/<int:id>/read` (owner; `api/routes.py:1904-1929`; owner-api.md §2)

`/messages`: `unread` true for `1|true|yes`; `200 {"messages": [Message], "unread": int}`, newest first, 200 rows. `/messages/<id>/read`: body `{"read": false}` marks unread, anything else marks read; `200 {"message": Message, "unread": int}`; `404 {"error": "no message with id N"}`.

### 4.21 `POST /announce` and `POST /announce/<int:id>/retire` (owner; `api/routes.py:1839-1864, 1931-1939`; owner-api.md §2)

`/announce`: body `{"body": str}`, collapsed and cut to 280; `400 {"error": "say something first"}`; `200 {"note": Note, "sent": int}`. Side effects: an `announcements` row; `broadcast()` to everyone except the poster, titled with `app.name`, tag `note-<id>`, and (Phase 2) an `events` row of kind `announcement` and relay deliveries. Phase 2 also makes the title and body configurable under `notifications.announcement` (ADR §2). `/announce/<id>/retire`: no body; always `200 {"status": "ok"}`, even for an unknown id.

### 4.22 `GET /discover/genres` and `GET /discover/missing` (owner; `api/routes.py:294-333`; owner-api.md §2)

`/discover/genres`: `200 {"genres": [{"id": int, "name": str}], "configured": bool}`; a hard-coded list of 19 when TMDb is unconfigured.

`/discover/missing`: requires `tmdb.api_key`, else `400 {"error": "no TMDb API key in config.yaml - this needs one"}`. Query: `genres` (required, comma-separated TMDb ids; missing → `400 {"error": "pick at least one genre", "example": {"genres": "27,35", "match": "all"}}`), `match` `all|any` default `all`, `min_rating` float default 6.0, `min_votes` int default 200, `year_from`, `year_to` int, `sort` `rating|popularity|newest` default `rating`, `pages` int default 5 clamped 1..25, `refresh` `1|true|yes`. `200`:

| Field | Type |
|---|---|
| `films` | [{`tmdb_id` int, `title` str, `original_title` str (`""` when same), `year` int\|null, `rating` float, `votes` int, `popularity` float, `genres` [str], `overview` str, `poster` str, `language` str, `wanted_status` `"wanted"`\|`"acquired"`\|`"dismissed"`\|null}] |
| `considered`, `owned_of_considered`, `library_films`, `total_matching`, `pages_read`, `pages_available` | int |
| `rating_source` | str, `"tmdb"` |

Errors: `503`; `502 {"error": "TMDb is unavailable: ..."}`. No side effects. Slow on a cold cache (§1.9).

### 4.23 `GET /reports/gaps?type=&library=&limit=` (owner; `api/routes.py:374-383`; owner-api.md §2)

`type` `tv|franchise|alternate` (anything else yields `{}`); `limit` default 500. `200` keyed on `type`: `tv_gaps` [{`title` str, `library` str|null, `tmdb_id` int|null, `missing_seasons` [{`season` int}], `missing_episodes` [{`season` int, `episode` int, `episode_title` str|null}], `total_missing` int}]; `franchise_gaps` [{`collection` str, `library` str|null, `missing_count` int, `missing_entries` [{`title` str, `tmdb_id` int|null, `release_date` str|null}]}]; `alternate_versions` [{`title` str, `library` str|null, `tmdb_id` int|null, `versions` [{`detail` str}]}]. Empty until the gap analysis has run, which needs the filesystem scanner. No v1 screen calls this route; the Kit decodes `reports.gaps.tv.json` for the contract test only (04 §12 GAP 3), and nothing offers to run the analysis (`POST /analyze/gaps` is class C, §8).

### 4.24 `GET /summary` and `GET /libraries` (owner; `api/routes.py:134-192`; owner-api.md §2)

`/summary`: `200 {"total_files": int, "avg_score": float|null, "files_with_issues": int, "tv_gaps": int, "franchise_gaps": int, "alt_versions": int, "by_library": [{"library": str|null, "total": int, "with_issues": int, "avg_score": float|null, "worst_score": int|null}], "distribution": [{"range": str|null, "count": int}]}`; all zeros and nulls on a request-only install. `/libraries`: `200 [{"title": str, "type": str}]`, a bare array; `type` is `movie`, `show`, `artist` or `photo`. Both have no `try/except`; Phase 1's handler (§3.15) turns their failures into JSON `500`.

### 4.25 `GET /notifications` (owner, handler-checked; `api/routes.py:899-917`; push-and-notifications.md §1.5)

`200 {"notifications": [{"key": str, "goes_to": str, "enabled": bool, "title": str, "body": str}], "devices": [{"username": str, "devices": int}], "edit_in": "config.yaml, under notifications"}`. `devices` counts web-push rows only; the Console shows native counts from §3.14 beside them. Refusal is `403 {"error": "owner only"}` (`api/routes.py:903-904`). Read-only in the app (ADR §4 "PUT /api/v1/owner/notifications: cut"). Phase 2 adds the `announcement` key to the catalogue.

### 4.26 `POST /push/test` (household; `api/routes.py:919-930`)

`200 {"sent": int, "failed": [str]}` or `{"sent": 0, "failed": [], "skipped": "test"}`; web-push rows only. Not called by the app; `POST /api/v1/push/test` (§3.10) supersedes it. Listed so the builder does not add it.

### 4.27 `POST /themes/save` and `POST /themes/<slug>/delete` (owner; `api/routes.py:799-827`; owner-api.md §2)

`/themes/save`: body `{"name": str (≤40), "colors": {"bg", "surface", "border", "text", "accent", "red"} (each "#rrggbb"), "wallpaper": "data:image/png;base64,..." (optional, ≤2 MB PNG)}`; `200 {"slug": str, "themes": [Theme]}`; `400` with one of `"that wallpaper would not decode"`, `"keep the wallpaper under 2MB"`, `"the wallpaper has to be a PNG"`, `"give the look a name"`, `"that name does not make a usable identifier"`, `"six colours are needed, each as #rrggbb"`. `/themes/<slug>/delete`: no body; always `200 {"themes": [Theme]}`. Owner-made looks are in the Console; the household Looks screen is a picker only (design-A §2.1).

### 4.28 `GET /members` and `GET /members/<username>/picks` (owner; `api/routes.py:1798-1837`; household-api.md §3.15-3.16)

`/members`: `200 {"members": [{"username": str, "last_seen": str}]}`, every username with a stored Plex token except the caller's, newest sign-in first. `/members/<username>/picks`: `200 {"picks": [Pick], "reason": str, "for_user": str}`; `409 {"picks": [], "error": "<username> has not signed in here yet."}`; `409 {"picks": [], "error": "<username>'s sign-in has expired."}`; other failures `200 {"picks": [], "error": str}` (§1.5). 2 to 10 s (§1.9). Both feed the gift flow of §4.8, which is free for the owner.

## 5. Setup-page endpoints

The ADR names a setup blueprint `api/setup.py` serving `/setup` and `/setup/*` in setup mode, with `/health` returning `{"status": "setup"}` and `GET /api/v1/server` returning `"setup": true` (ADR §2, §7 Phase 8). The app calls none of the setup routes: the setup page is a browser page the owner opens on a computer, and the only thing the app observes is `setup` in §3.1. The setup routes' shapes are specified in `06-install-and-setup.md`. GAP: their request and response shapes are not in the ADR; Phase 8 defines them there with `tests/test_setup.py`, and nothing in the Kit decodes them.

## 6. The fixtures contract

The ADR (§4 "Fixtures") makes recorded JSON the gate for shape drift (ADR §6 item 8): pytest writes what every `/api/v1` and household route returns, the iOS repository vendors the files, and `swift test` decodes each one in full. This section fixes the file names, the envelope, the writer and the reader so that both repositories agree without a conversation.

### 6.1 Envelope

Each file in `tests/fixtures/api/` is one JSON object:

| Key | Type | Meaning |
|---|---|---|
| `request.method` | str | `GET`, `POST`, `DELETE` |
| `request.path` | str | path with query string, e.g. `/api/v1/changes?since=0&limit=50` |
| `request.body` | object\|null | the JSON sent, or `null` |
| `request.role` | str | `"none"`, `"shared"` or `"owner"`: the session the test used |
| `status` | int | HTTP status |
| `body` | object\|array\|null | the decoded JSON body; `null` for `204` and for image routes |

### 6.2 Writer (pytest)

`tests/conftest.py` provides a `record` fixture: `record(name, response)` writes `tests/fixtures/api/<name>.json` with `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)` and a trailing newline. Before writing it normalises values so the output is deterministic:

| Pattern | Replacement |
|---|---|
| any string matching `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$` | `"2026-01-02 03:04:05"` |
| any string matching `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` | `"2026-01-02T03:04:05Z"` |
| the value of any key named `token` | `"FIXTURE-TOKEN-00000000000000000000000000000"` (43 chars: 14 plus 29 zeros) |
| the value of any key named `server_key` or `relay_handle` | `"FIXTURE-KEY"` |

Everything else comes from stubs, which are fixed data (design-A §10 Phase 1-2). `tests/conftest.py` stubs, by import site:

| Seam | Stub |
|---|---|
| `api.plex_auth.plex_identity`, `server_access_token`, `allowed_users`, `read_pin` | fixed identities and a pin that carries an `authToken` |
| `PlexServer` as imported in `api.user_library`, `api.suggestions`, `api.request_assistant`, `scanner.wanted_search` and `api.plex_auth` (`api/user_library.py:36`, `api/suggestions.py:37-55`, `api/request_assistant.py:391`, `scanner/wanted_search.py:47, 64`, `api/plex_auth.py:182, 220`) | one `FakePlexServer` class in `tests/fake_plex.py` exposing `machineIdentifier`, `friendlyName`, `version`, `library.sections()`, `library.section(name).search(...)`, `library.search(...)`, `fetchItem(key)`, `playlists()`, `playlist(title)`, `createPlaylist(...)`, `myPlexAccount()`, with items carrying `guids`, `title`, `year`, `ratingKey`, `thumb`, `art`, `summary`, `addedAt`, `media`, `roles`, `directors`, `writers`, `genres`, `seasons()`; the module caches `api.user_library._servers`, `scanner.wanted_search._server` and `_as_user`, and `api.request_assistant._plex` are reset before each test |
| `api.ai.provider` | a fake adapter whose `structured(...)` method (`api/request_assistant.py:610`) returns a fixed filters JSON, so `translated_by == "model"` and `smart_mode` is true; the test config sets `ai.api_key: FIXTURE-AI-KEY` |
| `api.tmdb_lookup.suggest`, `suggest_shows`, `discover`, `discover_shows`, `availability`, `genre_ids`, `seasons_that_exist` | fixed public-domain data; the test config sets `tmdb.api_key: FIXTURE-TMDB-KEY` so `/discover/missing` runs |
| `api.limits` | driven to its ceiling for the `rate_limited` fixtures |
| `api.relay.post` (Phase 2) and `api.push.send_to_user` | record the call, deliver nothing |

Every fixture in §6.4 is recorded through these seams and no test opens a socket. Every test that exercises a route named in §6.4 calls `record`, so a run of `pytest -q` rewrites every fixture. CI then runs `git diff --exit-code tests/fixtures/api`, so a shape change that was not committed fails the build. Fixture data uses public-domain titles only, because the files are also the demo data and are screenshotted (ADR §7 Phase 9 banned-word review).

### 6.3 Reader (the Kit)

The iOS repository vendors the directory as `PlexGetKit/Tests/Fixtures/` (a copy, refreshed by `scripts/sync-fixtures.sh <path-to-server-repo>`, committed with the server commit hash in `PlexGetKit/Tests/Fixtures/VERSION`). `PlexGetKit/Tests/PlexGetKitTests/FixturesTests.swift` holds a `FixtureCatalogue`: a dictionary from file name to a closure that decodes `body` with the named `Models` type and, when `status` is not `2xx` or the body carries `error`, asserts the error mapper's result. One test enumerates the directory and fails when a file has no catalogue entry; one test iterates the catalogue and fails when a file is missing. Decoding is strict on the fields listed in §2-§4 (a missing required field fails) and lenient on unknown keys (ignored). A `204` file has body `null` and its catalogue closure asserts the status only. No fixture is ever edited by hand in either repository; a shape the Kit cannot decode is fixed by a server test and a change to this document (ADR §6 item 8). `DemoServerClient` reads the same files, so demo mode and the contract test cannot drift apart.

### 6.4 File list

77 rows; the Phase 2 gate counts the directory against this number. Kit error case names are 02 §6.1's verbatim.

| File | Route and case | Kit type |
|---|---|---|
| `api.v1.server.json` | §3.1, configured server | `ServerInfo` |
| `api.v1.server.setup.json` | §3.1 in setup mode | `ServerInfo` |
| `api.v1.auth.plex.ok.shared.json` | §3.2 success, shared user | `AuthPlexResponse` |
| `api.v1.auth.plex.ok.owner.json` | §3.2 success, owner | `AuthPlexResponse` |
| `api.v1.auth.plex.pin_not_linked.json` | §3.2 `409` | `.conflict` |
| `api.v1.auth.plex.not_shared.json` | §3.2 `403 not_shared` | `.notShared` |
| `api.v1.auth.plex.invite_pending.json` | §3.2 `403 invite_pending` | `.invitePending` |
| `api.v1.auth.plex.rate_limited.json` | §3.2 `429` | error mapper `.rateLimited` |
| `api.v1.auth.session.json` | §3.3 | `SessionInfo` |
| `api.v1.auth.session.unauthorised.json` | §3.3 with no bearer | `.signInRequired(code: "session_expired")` |
| `api.v1.auth.session.delete.json` | §3.4 `204` | `.none` (body `null`) |
| `api.v1.auth.sessions.shared.json` | §3.5 as a member | `SessionList` |
| `api.v1.auth.sessions.owner.json` | §3.5 as the owner, with `username` | `SessionList` |
| `api.v1.auth.sessions.delete.json` | §3.7 `204` | `.none` |
| `api.v1.push.devices.json` | §3.8 | `PushRegistration` |
| `api.v1.push.devices.limit.json` | §3.8 sixth device | `.conflict` |
| `api.v1.push.devices.delete.json` | §3.9 `204` | `.none` |
| `api.v1.push.test.json` | §3.10 | `PushTestResult` |
| `api.v1.push.test.rate_limited.json` | §3.10 `429` | `.rateLimited` |
| `api.v1.changes.shared.json` | §3.11 as a member | `ChangesPage` |
| `api.v1.changes.owner.json` | §3.11 as the owner with `unread_messages` | `ChangesPage` |
| `api.v1.owner.relay.json` | §3.12 | `RelayState` |
| `api.v1.owner.status.json` | §3.14 | `OwnerStatus` |
| `api.v1.owner.status.forbidden.json` | §3.14 as a member | `.ownerOnly` |
| `api.v1.internal_error.json` | §3.15 | `.serverFault` |
| `persona.json` | §4.1 | `Persona` |
| `themes.json` | §4.1 with one owner-made look | `ThemeList` |
| `request.search.title.json` | §4.2 film named | `SearchResult.title` |
| `request.search.title_show.json` | §4.2 series named | `SearchResult.title` with `shows` |
| `request.search.person.json` | §4.2 person | `SearchResult.title` shape 2b |
| `request.search.tv_mood.json` | §4.2 television mood | `SearchResult.title` with `for_age` |
| `request.search.cheeky.json` | §4.2 brush-off | `SearchResult.cheeky` |
| `request.search.mood.json` | §4.2 mood | `SearchResult.mood` |
| `request.search.missing_only.json` | §4.2 | `SearchResult.missingOnly` |
| `request.search.title_missing.json` | §4.2 with `did_you_mean`, `did_you_mean_shows`, `close` | `SearchResult.titleMissing` |
| `request.search.rate_limited.json` | §4.2 `429` | `.rateLimited` with hint |
| `request.search.expired.json` | §4.2 `401` with `auth_required` (with a bearer, `code: plex_token_dead`, §1.4a) | `.signInRequired(code: "plex_token_dead")` |
| `request.wanted.film_added.json` | §4.3 | `WantedResponse` |
| `request.wanted.film_found.json` | §4.3 `found: true` | `WantedResponse` |
| `request.wanted.film_missing.json` | §4.3 `file_missing: true` as a member (no `missing_files`) | `WantedResponse` |
| `request.wanted.show.json` | §4.3 show path | `WantedResponse` |
| `request.wanted.list.json` | §4.4 | `WantedList` |
| `request.playlists.json` | §4.5 | `Playlists` |
| `request.playlists.error.json` | §4.5 `200` with `error` | `.serverSaidNo` |
| `request.playlist.items.json` | §4.6 | `PlaylistItems` |
| `request.playlist.remove.json` | §4.7 | `RemoveResult` |
| `request.playlist.created.json` | §4.8 | `PlaylistResult` |
| `request.playlist.existing.json` | §4.8 with `already_there` | `PlaylistResult` |
| `request.playlist.gift.json` | §4.8 with `for_user` | `PlaylistResult` |
| `request.playlist.none_found.json` | §4.8 `200` with `error` | `.serverSaidNo` |
| `request.playlist.auto.json` | §4.9 | `AutoPlaylistResult` |
| `request.playlist.auto.cheeky.json` | §4.9 brush-off | `AutoPlaylistResult` |
| `request.detail.movie.json` | §4.10 | `Detail` |
| `request.detail.show.json` | §4.10 | `Detail` |
| `request.showcase.json` | §4.11 | `Showcase` |
| `request.suggestions.json` | §4.12 | `Suggestions` |
| `request.nudges.json` | §4.12 | `Nudges` |
| `request.nudges.rate_limited.json` | §4.12 `200` with `rate_limited` | `.rateLimited` |
| `request.announcements.json` | §4.13 | `NoteList` |
| `request.message.json` | §4.14 | `MessageResult` |
| `wanted.all.json` | §4.16 `?status=all`, owner, with `missing_files` present on nothing and `tmdb_id` on rows | `WantedPage` |
| `wanted.search.json` | §4.17 owner, with `missing_files` present | `WantedResponse` |
| `wanted.status.json` | §4.18 | `WantedRow` |
| `wanted.recheck.json` | §4.19 with one film and one show hit | `RecheckResult` |
| `wanted.settle.json` | §4.19 | `SettleResult` |
| `messages.json` | §4.20 | `Messages` |
| `messages.read.json` | §4.20 | `MessageReadResult` |
| `announce.json` | §4.21 | `AnnounceResult` |
| `discover.genres.json` | §4.22 | `Genres` |
| `discover.missing.json` | §4.22 | `Discovery` |
| `reports.gaps.tv.json` | §4.23 | `GapReport` |
| `summary.json` | §4.24 | `Summary` |
| `libraries.json` | §4.24 | `[Library]` |
| `notifications.json` | §4.25 with the `announcement` key | `NotificationCatalogue` |
| `members.json` | §4.28 | `Members` |
| `members.picks.json` | §4.28 | `MemberPicks` |
| `members.picks.not_signed_in.json` | §4.28 `409` | `.conflict` |

## 7. What the server must strip or never send to members

A "member" is any session whose role is `"shared"`. The Phase 1 test suite asserts every row of this table with a member session and again with an owner session where the owner may see it (ADR §7 Phase 1 "`missing_files` stripping").

| Data | Where it appears today | Rule | Citation |
|---|---|---|---|
| `missing_files` (absolute server paths) | every Film dict (shapes 2a, 2b), including inside `close`, `matches`, `unavailable`, `now_in_plex` | key removed from every response to a non-owner session; `file_missing` stays | household-api.md §2a, §6.10; owner-api.md §2 "Flag" |
| `search_query`, `notes`, `requested_by_id` on wanted rows | `GET /wanted`, `GET /wanted/search`, `/wanted/recheck`, `/wanted/settle`, `POST /wanted/<id>/status` | owner tier already; `GET /request/wanted/list` keeps its short dict (§4.4) and never gains them | push-and-notifications.md §3.4 |
| `persona.voice`, `persona.examples` | never on the wire | `GET /persona` emits only the five keys plus `smart_mode`; no route may add them | persona-themes-limits.md §1.3; ADR §4 "Persona" |
| `plex_token`, `plex_token_enc` | the internal `user` dict (`api/plex_auth.py:373-378`) and the `sessions` row | never serialised; `/api/v1/auth/session` and `/api/v1/auth/sessions` build explicit dicts | `api/routes.py:1204` strips it from `/auth/me` |
| session `token` / its hash | `sessions` | returned once by §3.2; never by §3.3 or §3.5 | ADR §2 |
| other people's sessions | `sessions` | §3.5 lists own rows for a member; `username` only for the owner; §3.6 answers `404` for another person's row | ADR §2 |
| `email`, `thumb` of other people | plex.tv | never served; a member's own come from §3.2 only | auth-and-tiers.md §6.15 |
| events addressed to others | `events` | §3.11 filters by `recipient_id`; owner-only kinds never reach a member | `OWNER_ONLY`, `api/push.py:180` |
| `unread_messages`, `messages` rows | `messages` | owner only (`/messages` is owner tier; §3.11 omits the field for members) | owner-api.md §2 |
| the relay `server_key`, `data/relay.json`, `plex.token`, `tmdb.api_key`, `ai.api_key`, `plex.webhook_key`, `data/secret.key`, `data/vapid.json` | config and data files | never in any response, including §3.1 and §3.14 | ADR §2 |
| `native_devices` rows of others, `relay_handle` values | `native_devices` | never listed; §3.14 returns counts only | design-A §2.2 |
| raw file names, release tags, `path` values | `/reports/upgrades`, `/media`, `/quality`, `/export*` | routes never called (§8) | owner-api.md §6 |
| Sonarr/Radarr listings | `/sonarr/series`, `/radarr/movies` | never called (§8) | brief fact 8 |

## 8. Routes the app never calls

The Kit has no endpoint builder for these and CI greps the iOS repository for their paths (ADR §6 item 10):

| Group | Routes | Why |
|---|---|---|
| Cookie sign-in | `POST /auth/start`, `GET /auth/poll/<id>`, `GET /auth/me`, `POST /auth/logout`, `GET /?handoff=` | replaced by §3.2-§3.4 (auth-and-tiers.md §6) |
| Web push | `GET /push/key`, `POST /push/subscribe`, `POST /push/unsubscribe`, `POST /push/test` | replaced by §3.8-§3.10 (push-and-notifications.md §6) |
| Web shell | `/`, `/request`, `/dashboard`, `/sw.js`, `/manifest.webmanifest`, `/health` | HTML and service-worker plumbing |
| Export and downloader-adjacent | `/wanted/export`, `/export`, `/export/download`, `/sonarr/series`, `/radarr/movies` | brief fact 8; App Review 5.2.3 |
| Filesystem and maintenance (owner-api.md class C) | `/scan/*`, `/analyze`, `/analyze/quality`, `/analyze/gaps`, `/quality`, `/media`, `/reports/upgrades`, `/reports/posters`, `/fixer/*`, `/posters/*`, `/command`, `/search/tmdb`, `/gaps`, `/plex/webhook` | need the server's filesystem or are machine-to-machine |
| Setup | `/setup`, `/setup/*` | browser page (§5) |

## 9. GAP index

Lines marked `GAP:` in this document, for the builder to add server-side with tests before use:

1. §4.3 `tmdb_id` on `POST /request/wanted` (Phase 2, required by ADR §4 "Arrival matching").
2. §4.17 `tmdb_id` query parameter on `GET /wanted/search` (Phase 2, same reason).
3. §5 the setup routes' shapes, owned by `06-install-and-setup.md` (Phase 8); not decoded by the Kit.

Everything else the app calls is either specified in §3 or exists today as cited in §4.
