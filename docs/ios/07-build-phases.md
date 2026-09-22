# 07. Build phases: the operating procedure

Purpose. This document is the procedure the builder follows, phase by phase, from an empty iOS repository to a
TestFlight build. It fixes the phase model (one phase per session, one pull request per phase, a gate of exact
commands whose output is pasted into the pull request, a human look before the next phase, a deviation note
instead of improvisation), the environment each phase needs, the order in which phases may run, and for each of
Phases 0 to 9 the goal, inputs, deliverables by path, a numbered definition of done, gate commands with their
pass signal, human steps, forbidden actions and exit criteria. Phase numbers and names are those of
10-decision-record.md §7; its §6 open risks appear as gates in the phase that must carry them (index in §6).

Read with: 10-decision-record.md (binding), 00-README.md §3-§4 (how the owner runs the builder, the deviation
note), 03-server-api.md (every shape and fixture name), 04-ios-app.md (the iOS repository layout),
05-push-relay.md (the relay layout and tests), 06-install-and-setup.md (the Phase 8 artefacts),
08-app-store-and-release.md (the human-only steps Phases 6, 7 and 9 depend on), ios-CLAUDE.md and
relay-CLAUDE.md (the standing rules), prompts/phase-N.md (the pasted prompt for each phase).

## 1. The phase model

### 1.1 Rules that hold in every phase

1. **One phase per Claude Code session.** The session starts in the repository the phase names, with the whole
   of `prompts/phase-N.md` as its first message; a phase is split across sessions only after a deviation note
   (00-README.md §3 item 5), the second session resuming on the same branch.
2. **A phase is a pull request.** Branch `phase-N-<slug>` from `main`, the slug being the phase name of
   10-decision-record.md §7 lower-cased and hyphenated; Phase 6 has one branch per repository. Title `Phase N:
   <name>`. Commits are small and about one thing (`CONTRIBUTING.md:81`). The human merges, after reading.
3. **The gate is a set of commands.** Each phase lists them in a code block with the line that proves a pass.
   The builder runs every command from a clean checkout of the branch and pastes command and complete output
   verbatim under `## Gate output`. A command the builder's machine cannot run (a Mac command in a Linux
   session) goes under `## Not run here` with the reason; the human runs it before the phase can pass.
4. **The human looks before the next phase.** Phase N+1 starts only when every gate command of Phase N exited
   zero, Phase N's human device step (when it has one) is recorded, and the pull request is merged. The one
   permitted overlap is Phase 8 with Phases 4 to 7 (§3).
5. **Deviation notes.** When reality contradicts the plan, the builder appends one entry to
   `docs/build/deviations.md` in the current phase's repository, in the six-line form of 00-README.md §4, commits
   it on the phase branch, and stops; it never works around the contradiction. That is the required output when
   any gate here fails (10-decision-record.md §6 items 1 and 4 say so explicitly).
6. **No invention.** Endpoints, fields, columns, file names and identifiers come from 10-decision-record.md,
   03-server-api.md, 04-ios-app.md, 05-push-relay.md and 06-install-and-setup.md. A missing one is a `GAP:` line
   in one of those documents; the builder adds it server-side with pytest tests in the phase that document
   names, and never fakes a response outside `DemoServerClient`.
7. **Every server change ships with tests** under `tests/` (constraints brief, fact 17) and re-records
   `tests/fixtures/api/` (03-server-api.md §6.2). The cookie flow's tests keep passing in every server phase.
8. **Copy rules bind every string, fixture and commit message**: no word of 04-ios-app.md §10.3 appears in
   `App/`, `PlexGetKit/`, `tests/fixtures/api/`, `dashboard/setup.html` or store metadata.

### 1.2 The pull request body

These headings, in this order, all present even when a section says "none":

```
## Goal            one paragraph, copied from this document
## Changed         this document's deliverables table with a tick or "not needed: <why>" per row
## Gate output     every gate command and its full output, verbatim
## Not run here    Mac or device commands the builder could not run, for the human
## Human steps     each HUMAN item of this phase with "done: <date, by whom, result>" or "pending"
## Deviations      "none" or the path and entry number in docs/build/deviations.md
## Forbidden       each forbidden item of this phase with "confirmed absent" and the grep used where one exists
```

## 2. Environment matrix

"Linux" is the remote Claude Code environment or any Linux machine: Phases 0 (except one step), 1, 2, 3, 8 and
the relay half of 6. "Mac" is a Mac with Xcode or the macOS GitHub Actions runner: Phase 0's simulator build,
Phases 4, 5, 7, 9 and the app half of 6. The builder cannot run Xcode on Linux (constraints brief, fact 13), so a
Mac phase is run by the builder on a Mac or gated by `app.yml` (§4.3) plus the human device step. Rows marked
(unverified) are the plan's expectation; Phase 0's gate records the actual versions, which are then pinned. Before
Phase 0: `python3.11 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-dev.txt` on Linux;
Xcode 16 with the iOS platform, `xcodegen` and `xcode-select` set to it on the Mac.

| Tool | Version | Used by | Source |
|---|---|---|---|
| Python, pytest | Python 3.11 for the server's tests (`README.md:180` says 3.11 or newer; the venv is 3.11.15), 3.12 inside the image (10-decision-record.md §2) and for the relay (05-push-relay.md §2); pytest 8.x pinned in `requirements-dev.txt` (Phase 0 adds it; `requirements.txt:1-4` stays runtime-only) | 0, 1, 2, 6, 8 | deployment-and-security.md §2.1 |
| Swift toolchain | 6.0 or later, `swift-tools-version 6.0`, language mode 6 (04-ios-app.md §1); on Linux the official `swift:6.0-noble` container image or a swift.org tarball (unverified: image tag) | 0, 3 | brief fact 11 |
| Xcode | 16.x on macOS 15 (brief fact 11: swift-testing needs Xcode 16 or later; the point release on `macos-15` runners is unverified); ships Swift 6 and the "iPhone 16" simulator (unverified) | 0, 4-7, 9 | brief |
| XcodeGen | from `brew install xcodegen`; version recorded in Phase 0 and pinned in `app.yml` thereafter (unverified) | 0, 4-7, 9 | brief fact 13 |
| Docker | Engine 24 or later with `buildx` and QEMU for arm64 (unverified); Docker Desktop for the human's second first run | 8 | 06-install-and-setup.md §1 |
| A physical iPhone | iOS 17 or later, signed in to a Plex account the test server is shared with | 4-7, 9 | 10-decision-record.md §6 item 9 |

## 3. Dependency graph

```
Phase 0 --> Phase 1 --> Phase 2 --> Phase 3 --> Phase 4 --> Phase 5 --> Phase 6 (app half) --> Phase 7 --> Phase 9
                          |                                                  ^                              ^
                          +--> Phase 6 (relay half, Linux, any time after 2) -+                              |
                          |                                                                                 |
                          +--> Phase 8 (Linux, in parallel with Phases 4 to 7) -----------------------------+
```

Phases 0 to 3 are strictly sequential: the Kit decodes the fixtures Phases 1 and 2 record. Phases 4 to 7 are
sequential on the Mac, each building on the previous screens and glue. Phase 6's relay half depends only on Phase
0 and the `api/relay.py` contract of Phase 2 (05-push-relay.md §8), so a Linux session may build it while Phase 4
or 5 runs on the Mac; its app half waits for Phase 5. Phase 8 depends on Phase 2 (setup mode, `waitress`,
`PLEXGET_DATA`) and on nothing in the iOS repository, so it runs alongside Phases 4 to 7 (10-decision-record.md
§7). Phase 9 waits for everything. Parallel builders never share a branch.

## 4. CI workflows

The Linux runner gates the server, the Kit and the relay on every push; the macOS runner gates the app target
on pushes to `main`, on pull requests labelled `mac-ci`, and on demand, because macOS minutes cost about ten
times Linux minutes (design-A §10, verified there against GitHub's billing page). Action versions are the current
majors (unverified). The relay's `ci.yml` is §4.1 with `python-version: "3.12"` and only the pytest step.

### 4.1 Linux runner: this repository, `.github/workflows/tests.yml` (Phase 0)

```yaml
name: tests
on:
  push:
  pull_request:
jobs:
  pytest:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest -q
      - name: fixtures are committed
        run: git diff --exit-code -- tests/fixtures/api
      - name: banned words
        run: scripts/banned-words.sh tests/fixtures/api dashboard/setup.html
```

`scripts/banned-words.sh` is the iOS repository's script (04-ios-app.md §10.3), added here in Phase 1. Phase 8
adds `image.yml` (06-install-and-setup.md §1): `docker build` on pull requests, a pushed amd64 and arm64 manifest
to `ghcr.io/<org>/plexget` on tags `v*`.

### 4.2 Linux runner: `<AppName>-ios`, `.github/workflows/kit.yml` (Phase 0)

```yaml
name: kit
on:
  push:
  pull_request:
jobs:
  swift-linux:
    runs-on: ubuntu-24.04
    container: swift:6.0-noble
    steps:
      - uses: actions/checkout@v4
      - run: swift build -Xswiftc -warnings-as-errors
        working-directory: PlexGetKit
      - run: swift test
        working-directory: PlexGetKit
      - name: fixtures carry a pinned server commit
        run: test -s PlexGetKit/Tests/Fixtures/VERSION
      - name: standing rules
        run: scripts/lint.sh
      - name: banned words
        run: scripts/banned-words.sh
```

`scripts/lint.sh` holds the greps of 04-ios-app.md §10.2: `print(` in `App/` or `PlexGetKit/`; `URLSession`
outside `App/Session/URLSessionTransport.swift`; `URLRequest(` outside `ServerAPI/Endpoints.swift`; `try!`,
`as!`, `fatalError(` outside `App/AppIdentity.swift`; `import UIKit`, `StoreKit`, `Security`, `UserNotifications`,
`CryptoKit` under `PlexGetKit/`; `dependencies:` in `Package.swift`; `packages:` in `project.yml` beyond the Kit.

### 4.3 macOS runner: `<AppName>-ios`, `.github/workflows/app.yml` (Phase 0)

```yaml
name: app
on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, labeled]
  workflow_dispatch:
jobs:
  xcodebuild:
    if: github.event_name != 'pull_request' || contains(github.event.pull_request.labels.*.name, 'mac-ci')
    runs-on: macos-15
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - run: sudo xcode-select -s /Applications/Xcode_16.2.app
      - run: brew install xcodegen
      - run: xcodegen generate
      - run: xcodebuild -version && xcodegen --version && swift --version
      - name: test on the simulator
        run: |
          set -o pipefail
          xcodebuild test -scheme App \
            -destination 'platform=iOS Simulator,name=iPhone 16' \
            -derivedDataPath build CODE_SIGNING_ALLOWED=NO | tee xcodebuild.log
      - name: local network string present
        run: plutil -extract NSLocalNetworkUsageDescription raw -o - build/Build/Products/Debug-iphonesimulator/App.app/Info.plist
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: xcodebuild-log
          path: xcodebuild.log
```

`Xcode_16.2.app` is a placeholder until Phase 0's Mac step records what the runner has (unverified). `xcodebuild
test` runs `AppTests` and `AppUITests` (demo mode, 04-ios-app.md §11); from Phase 7 it also runs StoreKit testing.

## 5. The phases

### Phase 0, Scaffold (Linux, one Mac step)

**Goal.** Create the skeleton of the three repositories so that every later phase starts from a passing gate: a
`CLAUDE.md` loaded on every session, a generated Xcode project that builds on a simulator, a Kit package that
tests on Linux, a pytest pattern that survives `config.py`'s import-time validation, and CI on all three. No
feature code exists at the end; the only production change here is the one-line read of `PLEXGET_CONFIG`.

**Inputs.** 00-README.md §5 (the fill-in values); 10-decision-record.md §2, §7; 04-ios-app.md §1, §3.1;
05-push-relay.md §2; 03-server-api.md §6.2; ios-CLAUDE.md; relay-CLAUDE.md; `config.py:20-22, 116-126`.

**Deliverables.**

| Repository | Path | Contents |
|---|---|---|
| this | `tests/conftest.py`, `tests/test_smoke.py`, `pytest.ini`, `requirements-dev.txt` | `conftest` writes a valid temporary `config.yaml` (`plex.url`, `plex.token`, `flask.host: 127.0.0.1`) under `tmp_path_factory`, sets `PLEXGET_CONFIG` to it before `config` is imported, and provides `app`, `client` and `record` (03-server-api.md §6.2); the smoke test asserts `GET /health` is `200` with `status: "ok"` and `build` (`api/routes.py:127-132`); `testpaths = tests`; `pytest==8.<x>` |
| this | `config.py` | `CONFIG_PATH = Path(os.environ.get("PLEXGET_CONFIG") or ROOT / "config.yaml")` replacing `config.py:22`; nothing else. Note: 10-decision-record.md §7 lists `PLEXGET_CONFIG` under Phase 2, but `tests/conftest.py` cannot work without it, so this one line moves to Phase 0; `PLEXGET_DATA` and setup mode stay in Phase 2 |
| this | `.github/workflows/tests.yml`, `.gitignore` | §4.1 without the banned-words step (the script arrives in Phase 1); `.pytest_cache/` ignored |
| `<AppName>-ios` | `project.yml`, `CLAUDE.md` | targets `App`, `AppTests`, `AppUITests`; local package `PlexGetKit`; iOS 17; Swift 6 language mode; `SWIFT_STRICT_CONCURRENCY = complete`; `-warnings-as-errors`; the Info.plist keys and entitlements of 04-ios-app.md §1 with placeholder values; `CFBundleDisplayName` from the fill-in block; a verbatim copy of ios-CLAUDE.md |
| `<AppName>-ios` | `PlexGetKit/Package.swift`, `PlexGetKit/Sources/PlexGetKit/Protocols/`, `PlexGetKit/Tests/PlexGetKitTests/SmokeTests.swift`, `PlexGetKit/Tests/Fixtures/VERSION` | `swift-tools-version: 6.0`, `swiftLanguageModes: [.v6]`, `platforms: [.iOS(.v17)]`, targets `PlexGetKit` and `PlexGetKitTests` with `resources: [.copy("../Fixtures")]`, no `dependencies`; `SecretStore`, `Entitlements`, `Transport`, `Notifier` as protocol declarations with doc comments from 04-ios-app.md §2; one passing swift-testing test; `VERSION` containing `unset` |
| `<AppName>-ios` | `App/AppMain.swift`, `App/AppIdentity.swift`, `App/App.entitlements`, `App/Resources/{Localizable.xcstrings, Assets.xcassets, PrivacyInfo.xcprivacy}` | a `@main` app showing one localised string; `AppIdentity` with every constant of 04-ios-app.md §3.1 from the fill-in block; a manifest with `NSPrivacyTracking: false` |
| `<AppName>-ios` | `AppTests/SmokeTests.swift`, `AppUITests/SmokeUITests.swift`, `scripts/banned-words.sh`, `scripts/lint.sh`, `scripts/sync-fixtures.sh` | one passing XCTest each, the UI test launched with `--demo --uitest`; the scripts of §4.2; `sync-fixtures.sh <server-repo>` copies `tests/fixtures/api/*.json` into `PlexGetKit/Tests/Fixtures/` and writes the server's `git rev-parse HEAD` to `VERSION` |
| `<AppName>-ios` | `.github/workflows/{kit,app}.yml`, `.gitignore`, `docs/build/phase-0.md` … `phase-9.md` | §4.2, §4.3; `*.xcodeproj`, `build/`, `DerivedData/` ignored; verbatim copies of `prompts/phase-N.md` |
| `<AppName>-relay` | `relay/__init__.py`, `relay/config.py`, `relay/routes.py`, `tests/conftest.py`, `tests/test_health.py`, `pytest.ini`, `requirements.txt`, `requirements-dev.txt`, `CLAUDE.md`, `.github/workflows/ci.yml`, `.gitignore` | `create_app(config)` serving only `GET /v1/health` → `200 {"status": "ok"}`; `conftest` with `RELAY_DB=:memory:` (05-push-relay.md §12); the health test; `Flask` and `waitress` pinned; a verbatim copy of relay-CLAUDE.md; §4 |

**Definition of done.**

1. `pytest -q` passes in this repository with no `config.yaml` in the checkout, and in `<AppName>-relay`.
2. `swift build -Xswiftc -warnings-as-errors` and `swift test` pass in `PlexGetKit/` on Linux.
3. `xcodegen generate` produces a gitignored `.xcodeproj`, and `xcodebuild build` succeeds on the simulator
   destination without signing (10-decision-record.md §5 item 16).
4. All workflows are green on the phase branches (`app.yml` through `mac-ci`); tool versions are recorded.

**Gate commands.**

```
# this repository, Linux
python3 --version && venv/bin/pytest --version && swift --version
venv/bin/pytest -q                        # pass: "N passed", exit 0
# <AppName>-ios, Linux
cd PlexGetKit && swift build -Xswiftc -warnings-as-errors && swift test   # pass: "Test run with N tests passed"
cd .. && scripts/lint.sh && scripts/banned-words.sh                     # pass: no output, exit 0
# <AppName>-relay, Linux
venv/bin/pytest -q                        # pass: "1 passed"
# <AppName>-ios, Mac
xcodebuild -version && xcodegen --version
xcodegen generate
xcodebuild -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' build CODE_SIGNING_ALLOWED=NO
                                          # pass: "** BUILD SUCCEEDED **"
```

**Human steps.** HUMAN: fill the five values of 00-README.md §5 into `prompts/phase-0.md` before pasting it;
create the two private repositories, push the skeletons, add the `mac-ci` label; run the Mac gate commands when
the builder's session is Linux and paste the output into the pull request.

**Forbidden in this phase.** Any feature code (no `/api/v1` route, no Kit module beyond the four protocols, no
screen); any dependency in `Package.swift` or `project.yml`; hand-editing or committing `.pbxproj`; any change to
`config.py` beyond the one line above or under `api/`; `print(`; a string outside `Localizable.xcstrings`.

**Exit criteria.** Three pull requests (one per repository): gate output for both machines, recorded versions.

### Phase 1, Server: bearer sessions and sign-in exchange (Linux)

**Goal.** Make every existing household and owner route usable with `Authorization: Bearer <token>`, add the open
`GET /api/v1/server`, the pin hand-off exchange `POST /api/v1/auth/plex` with its device-keyed limiter, the
session endpoints, the JSON error handler, `missing_files` stripping for members, and the fixtures writer, all
with tests, so that Phase 3 can decode a recorded response for every route the app will call. The cookie flow is
untouched and its own tests prove it.

**Inputs.** 03-server-api.md §1, §3.1-§3.7, §3.15, §6, §7; 02-architecture.md §2.1-§2.2, §3.1; 09-security.md;
10-decision-record.md §4 rows "Sign-in exchange", "Session lifetime", "Exchange rate limiter", §5 items 2 and 5,
§6 items 1, 7 and 8; auth-and-tiers.md §1.3, §1.5, §2.3, §3.2, §6.1, §6.7; household-api.md §6.7, §6.10;
owner-api.md §1.4; the file:line citations in the deliverables table.

**Deliverables.**

| Path | Change |
|---|---|
| `api/native.py` | blueprint `native`, url prefix `/api/v1`; routes §3.1-§3.7 of 03-server-api.md; registered in `create_app()` |
| `api/routes.py` | `current_user()` reads the bearer first, cookie second (`api/routes.py:943-951`); `OPEN_PATHS` gains `/api/v1/server` and `/api/v1/auth/plex`; the guard's household tier gains the `/api/v1/` prefix; the JSON handler (03-server-api.md §3.15); a response hook stripping `missing_files` for non-owner sessions (03-server-api.md §7) |
| `api/plex_auth.py` | `sessions` columns `device_id TEXT UNIQUE`, `device_name`, `platform`, `app_version`, `kind`, `revoked_at` (migration in the `db/database.py:152-157` style); `sha256(token)` at rest with the raw-value fallback; `ios` rows valid while `last_seen` is within 180 days, `web` rows unchanged; the pin read with the app's client identifier; the limiter keyed on `device.id` (12 per 10 minutes), global 40 per 10 minutes, 6 per Plex account per hour; a pin older than `PIN_LIFE` refused |
| `scripts/banned-words.sh`, `.github/workflows/tests.yml` | the script of 04-ios-app.md §10.3; the banned-words step of §4.1 |
| `tests/test_bearer.py` | bearer accepted on `/request/wanted/list`, `/wanted`, `/persona`; cookie still accepted; bearer wins when both present; hashed row found; legacy raw row still valid; `revoked_at` set → `401`; sliding `ios` and fixed `web` expiry on a frozen clock |
| `tests/test_server_info.py` | §3.1 body with every key, `setup: false`, `native_signin: true`, `min_app_version` |
| `tests/test_auth_plex.py` | every response of 03-server-api.md §3.2: success as shared and owner, `pin_not_linked`, `pin_expired`, `pin_unclaimed`, `not_shared`, `invite_pending`, `plex_tv_unreachable`, the three limiter buckets, `plex_token` accepted in tests only; one row per `device.id`; the account token never stored |
| `tests/test_sessions.py` | §3.3-§3.7: own rows for a member, every row with `username` for the owner, another user's row `403` for a member and `204` for the owner, sign out everywhere |
| `tests/test_errors.py`, `tests/test_missing_files.py` | an injected exception on `/api/v1/...` and on a bearer request to `/libraries` returns the JSON envelope, not HTML (owner-api.md §1.4), while `/dashboard` with a cookie still renders HTML; `missing_files` present for the owner, absent for a member on `/request/wanted` and `/wanted/search` |
| `tests/test_cookie_flow.py` | `POST /auth/start`, `GET /auth/poll/<id>`, `GET /auth/me`, `POST /auth/logout` as auth-and-tiers.md §1.3-§3.4 describe, plex.tv stubbed |
| `tests/fixtures/api/*.json` | every file of 03-server-api.md §6.4 that needs no Phase 2 column (`tmdb_id`, `unread_messages`, `wanted_counts`, `native_devices` are Phase 2) |

**Definition of done.**

1. `pytest -q` passes; `git diff --exit-code tests/fixtures/api` is clean after a second run.
2. Every route in 03-server-api.md §4 answers a bearer request as it answers a cookie request.
3. `sessions.token` for new rows is a 64-character hex digest; a pre-change row still signs in.
4. The seventh exchange for one Plex account within an hour is `429` with `retry_after`; the caller-address
   rule is never applied to this route (10-decision-record.md §5 item 5).
5. No response to a member session contains the key `missing_files`.

**Gate commands.**

```
venv/bin/pytest -q                                                     # pass: "N passed", exit 0
venv/bin/pytest -q && git diff --exit-code -- tests/fixtures/api      # pass: no diff
scripts/banned-words.sh tests/fixtures/api                             # pass: no output, exit 0
grep -n "plex_token" api/native.py | grep -v "testing\|https"          # pass: no line that stores it
```

**Human steps.** HUMAN (10-decision-record.md §6 item 1): with a real Plex account and the headers of
03-server-api.md §3.2, mint a strong pin, link it with `PUT https://plex.tv/api/v2/pins/link`, read `GET
/api/v2/pins/{id}` twice, and record whether a strong pin links, whether a short pin links, and whether
`authToken` is present on the second read. If only short pins link, the builder switches the hand-off to a short
pin in this phase. If a claimed pin cannot be re-read, the builder writes the deviation note and stops; the
`plex_token` fallback (HTTPS and confirmed LAN exchanges only) is the human's decision. HUMAN (item 7): with the
owner's token, print `account.users()` (`api/plex_auth.py:162-200`) for a managed Plex Home user and record
whether that user appears with a server entry.

**Forbidden in this phase.** Changing the body of any `/request/*` or owner route (the strip is a response hook);
changing the cookie flow or `SESSION_DAYS` for `web` rows; any new dependency; anything listed under Phase 2;
UI code; a network call in a test; editing a fixture by hand.

**Exit criteria.** One pull request: full `pytest -q` output, fixtures committed, both HUMAN results recorded.

### Phase 2, Server: events, devices, arrivals and install switches (Linux)

**Goal.** Give the server what push and polling need (`events`, `GET /api/v1/changes`, `native_devices`, the push
endpoints, `api/relay.py` and the fan-out, the configurable announcement), make arrivals correct (`wanted.tmdb_id`,
`Guid`/`kind`/`season` in `recheck()`, the `settle_wanted()` loop fix, the settle scheduler), and add the install
switches (`plex.webhook_key`, `app.loopback_owner`, `app.urls`, `data/client_id`, `waitress`, `PLEXGET_DATA`,
setup mode), all tested with the relay and web push stubbed.

**Inputs.** 03-server-api.md §3.8-§3.14, §4.3 (`tmdb_id` GAP), §4.19, §6.4; 02-architecture.md §2.8-§2.13,
§3.2-§3.5; 05-push-relay.md §8; 06-install-and-setup.md §5.1, §7, §8, §11, §13 (webhook-key GAP, identifier
gate); 10-decision-record.md §2, §4 rows "Arrival matching", "Bind address", §6 item 3; `api/push.py:188, 225`,
`api/routes.py:625-656, 669-695, 1261-1265`, `api/plex_auth.py:23-39`, `main.py:9-14`, `config.py:20-71,
116-157`; push-and-notifications.md §2.3, §3.1, §3.3, §6.3, §6.5; owner-api.md §8; deployment-and-security.md §1.

**Deliverables.**

| Path | Change |
|---|---|
| `api/native.py` | §3.8-§3.14 of 03-server-api.md; the five-device cap; `GET /api/v1/changes` with `cursor`, `events`, `unread_messages` (owner), `wanted_counts` |
| `api/relay.py` | `post(handle, event, title, body, collapse_id, ref)` reading `data/relay.json`; 5 s timeout; `401`/`403` marks the key inactive, `410` deletes the device row (05-push-relay.md §8) |
| `api/push.py`, `db/database.py` | tables `events`, `native_devices` and column `wanted.tmdb_id`; an `events` row written inside `send_event()` and `broadcast()` before delivery; relay fan-out to `native_devices` rows; `notifications.announcement` as a configurable block; rows older than 30 days purged on insert |
| `api/routes.py` | `POST /request/wanted` accepts `tmdb_id`; did-you-mean and discover guesses set it; `settle_wanted()` delivers to every device kind before the owner fallback; `POST /plex/webhook?key=` accepted with the door closed, `403` without the key (06-install-and-setup.md §8); the door behind `app.loopback_owner` |
| `scanner/wanted_search.py` | `recheck()` matches `Guid` (`tmdb://`, `imdb://`, `tvdb://`) before folded title and year; searches show sections for `kind == "show"`; checks the season |
| `config.py` | `PLEXGET_DATA`, `PLEXGET_LOOPBACK_OWNER`, `PLEXGET_SETTLE_MINUTES`; keys `app.urls`, `app.loopback_owner`, `app.settle_every_minutes`, `plex.webhook_key`; `SETUP_MODE = True` instead of `SystemExit(1)` when `config.yaml` is absent (06-install-and-setup.md §5.1) |
| `api/plex_auth.py` | `X-Plex-Client-Identifier` from `data/client_id`; the old `uuid5` kept when `data/` already exists |
| `main.py`, `requirements.txt` | `waitress` (one process, eight threads) whenever `flask.debug` is false; `waitress` pinned |
| `tests/test_changes.py`, `test_push_devices.py`, `test_relay_client.py`, `test_recheck.py`, `test_settle.py`, `test_webhook_key.py`, `test_config_env.py`, `test_client_id.py` | one file per row above; the scheduler on a fake clock; `relay.post` and `web_push.send_to_user` monkeypatched in `conftest.py`; the `waitress` branch asserted on the branch, not a socket |
| `tests/fixtures/api/*.json` | the remaining files of 03-server-api.md §6.4; Phase 1 files re-recorded where a Phase 2 key was added |

**Definition of done.**

1. `pytest -q` passes with no network access (run with the proxy variables pointed at a closed port).
2. A member's `GET /api/v1/changes` never returns another member's `message` event; the owner's carries
   `unread_messages`; the sixth `POST /api/v1/push/devices` for one user is `409` with `code: "device_limit"`.
3. `recheck()` marks a `kind == "show"` row acquired only when the requested season is present, and a film row
   with `tmdb_id` on a `Guid` match or, failing that, on folded title and year.
4. `settle_wanted()` sends `arrived` to the requester's native and web devices and falls back to the owner only
   when the requester has none.
5. With `data/` pre-existing the client identifier is `uuid5(NAMESPACE_DNS, "plex-ops.local")`; with a fresh
   `PLEXGET_DATA` it is a new `uuid4` persisted in `data/client_id` (10-decision-record.md §6 item 3).
6. Importing `config` with no `config.yaml` and no `PLEXGET_CONFIG` file sets `SETUP_MODE` and does not exit.

**Gate commands.**

```
HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 venv/bin/pytest -q   # pass: "N passed"
venv/bin/pytest -q && git diff --exit-code -- tests/fixtures/api                  # pass: no diff
scripts/banned-words.sh tests/fixtures/api                                        # pass: exit 0
ls tests/fixtures/api | wc -l                          # pass: equals the row count of 03-server-api.md §6.4
```

**Human steps.** HUMAN (10-decision-record.md §6 item 3): restart an existing venv install, confirm a member's web sign-in, record it.

**Forbidden in this phase.** Any change to notification wording or catalogue keys (`config.example.yaml` gains
only `announcement`, the `app.*` keys and `plex.webhook_key`); a network call in a test; anything listed under
Phase 8; UI code; a dependency other than `waitress`; a second worker process anywhere (the in-memory limiters
at `api/routes.py:969, 1013, 1029` need one process).

**Exit criteria.** One pull request: proxy-blocked `pytest -q` output, fixture count, gate 3 result, `grep -n
"processes\|workers" main.py` empty.

### Phase 3, PlexGetKit (Linux)

**Goal.** Build the Foundation-only Swift package: `Models`, `ServerAPI`, `PlexTV`, `SignIn`, `Pairing`, `Changes`,
`Themes`, `Demo`, and the four protocols in their final shape, with tests that decode every vendored fixture in
full, exercise every error mapping, drive `SignIn` through every transition on injected clocks, and rank pairing
candidates against fake probes.

**Inputs.** 04-ios-app.md §2, §10.1; 03-server-api.md §1-§4, §6.3, §6.4; 02-architecture.md §2.1-§2.7, §2.13,
§5, §6.1; 10-decision-record.md §2 (the Kit paragraph), §4 rows "LAN probe and trust", "Feature flag", "Persona";
persona-themes-limits.md §1.3, §4.1, §5.2; owner-api.md §4.10; `dashboard/request.html:37-79`; merged Phase 2.

**Deliverables.**

| Path (under `PlexGetKit/`) | Contents |
|---|---|
| `Sources/PlexGetKit/Protocols/` | `SecretStore`, `Entitlements`, `Transport`, `Notifier` as 04-ios-app.md §2 defines them |
| `Sources/PlexGetKit/ServerAPI/` | `ServerClient.swift` (`actor`; base-URL set; bearer token; `Accept: application/json`; 60 s search timeout), `Endpoints.swift` (the only `URLRequest` builder), `ServerError.swift` (`401` → `.signInRequired`, `403` → `.ownerOnly`, `429` with `rate_limited` → `.rateLimited`, `503` → `.plexUnavailable`, `200` with `error` → `.serverSaidNo`), `ServerClientProtocol.swift` |
| `Sources/PlexGetKit/Models/` | one file per type in 03-server-api.md §2-§3 and the `Kit type` column of §6.4; `SQLiteDate.swift` |
| `Sources/PlexGetKit/PlexTV/` | pin creation, linking, polling, `user`, `resources`, with the header set of 03-server-api.md §3.2 including `X-Plex-Platform-Version` |
| `Sources/PlexGetKit/SignIn/` | the state machine, pure, clock and client injected |
| `Sources/PlexGetKit/Pairing/` | invite fragment parsing; URL ranking (HTTPS first, private-address HTTP allowed, public HTTP refused); the machine-id probe over `Transport` |
| `Sources/PlexGetKit/Changes/`, `Sources/PlexGetKit/Themes/` | cursor logic and `ChangeEvent` → notification content and deep-link destination; the four palettes and the twelve-key custom look |
| `Sources/PlexGetKit/Demo/` | `DemoServerClient` reading the fixtures, with `role:` (04-ios-app.md §12 GAP 1) |
| `Tests/PlexGetKitTests/` | `FixturesTests.swift` with the `FixtureCatalogue`; one test file per module; `Fakes/` (`FakeTransport`, `FakeClock`, `FakeSecretStore`) |
| `Tests/Fixtures/` | the vendored files; `VERSION` holding the merged Phase 2 commit hash |

**Definition of done.**

1. `swift build -Xswiftc -warnings-as-errors` and `swift test` pass on Linux; `scripts/lint.sh` is clean.
2. `FixturesTests` enumerates `Tests/Fixtures/*.json`, finds a catalogue entry for every file and a file for
   every entry; each `2xx` body decodes with its `Kit type`; each error file maps to the named case.
3. `SignIn` tests cover every transition of 02-architecture.md §2.1, including `rateLimited(until)` and the
   once-per-five-minutes silent re-exchange (02-architecture.md §2.1 GAP).
4. `Pairing` refuses plain HTTP to a non-private address and prefers HTTPS when both answer.
5. `DemoServerClient` answers every route in 03-server-api.md §4 from a fixture, for both roles.

**Gate commands.**

```
scripts/sync-fixtures.sh <path-to-server-repo> && git status --short PlexGetKit/Tests/Fixtures   # pass: only expected files
cd PlexGetKit && swift build -Xswiftc -warnings-as-errors    # pass: "Build complete!"
cd PlexGetKit && swift test                                   # pass: "Test run with N tests passed after ..."
scripts/lint.sh && scripts/banned-words.sh                    # pass: no output, exit 0
cat PlexGetKit/Tests/Fixtures/VERSION                         # pass: a 40-character hash matching the server's main
```

**Human steps.** None; a shape the Kit cannot decode is a deviation note naming the server test to add
(10-decision-record.md §6 item 8), never a lenient guess in Swift.

**Forbidden in this phase.** `import UIKit`, `StoreKit`, `Security`, `UserNotifications`, `CryptoKit` under
`PlexGetKit/`; `URLSession` in the Kit; `URLRequest(` outside `Endpoints.swift`; force unwraps, `try!`, `as!`,
`print(`; any file under `App/`; editing a fixture by hand; a type that decodes `persona.voice` or
`persona.examples`; hashing the machine identifier.

**Exit criteria.** One pull request: gate outputs, the `VERSION` hash, `FixturesTests` count equal to the file count.

### Phase 4, App: welcome to results (Mac)

**Goal.** Build the app target from launch to a rendered search result: `AppMain`, `Session` (`SessionStore`,
`Router`, `URLSessionTransport`), `Keychain`, Welcome (sign in, invite, demo), Pairing with the LAN probe and the
confirmation step, Home (search bar, persona greeting, showcase reels, hero), Results (the five `kind` outcomes,
sort chips, selection bar), and the demo-mode UI smoke tests, against `DemoServerClient` and a Phase 2 server.

**Inputs.** 04-ios-app.md §1, §3.1-§3.2, §4-§7, §9, §11; 02-architecture.md §2.1-§2.6, §4, §5, §6.2-§6.4;
screens-inventory.md §5 items 1-8, §2.1-§2.8; 10-decision-record.md §4 row "LAN probe and trust", §5 items 3, 4
and 10, §6 items 6 and 9; 09-security.md.

**Deliverables.**

| Path (under `<AppName>-ios/`) | Contents |
|---|---|
| `App/Session/SessionStore.swift`, `Router.swift`, `URLSessionTransport.swift` | the only `URLSession` in the repository; `Transport` conformance; private HTTP through `NSAllowsLocalNetworking` |
| `App/Keychain/Keychain.swift` | `SecretStore` with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, items keyed by machine id |
| `App/Welcome/`, `App/Pairing/`, `App/Home/`, `App/Results/`, `App/Shared/` | one `@MainActor @Observable final class <Feature>Store` per folder plus views; cards, chips, persona views, the artwork loader, `Identifiers.swift`; the "update needed" screen for `min_app_version` |
| `project.yml`, `App/Resources/Localizable.xcstrings` | `NSLocalNetworkUsageDescription` with the sentence of 04-ios-app.md §1; `CFBundleURLTypes` for the sign-in callback; every string of these screens |
| `AppTests/KeychainTests.swift`, `AppTests/RouterTests.swift` | write, read, overwrite, delete on the simulator; deep-link routing |
| `AppUITests/` | the Phase 4 rows of 04-ios-app.md §11, all launched with `--demo --uitest` |

**Definition of done.**

1. `xcodebuild test` passes with `AppTests` and `AppUITests`; `scripts/lint.sh` and `scripts/banned-words.sh`
   are clean; no string in `App/` is outside `Localizable.xcstrings`.
2. The built `Info.plist` contains `NSLocalNetworkUsageDescription`; the probe targets only addresses plex.tv
   listed as `local` connections for the pinned machine identifier, never a scan; the Home-network toggle
   defaults on for owners and for anyone whose first probe succeeded, off otherwise (10-decision-record.md §6.6).
3. Before the first exchange with a server not reached through an invite, the app shows the Plex server name
   and address and waits for confirmation (10-decision-record.md §5 item 3).
4. `ASWebAuthenticationSession` completes in the simulator against a real plex.tv pin (10-decision-record.md §5
   item 10); the result is recorded.

**Gate commands.**

```
xcodegen generate
xcodebuild test -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' -derivedDataPath build CODE_SIGNING_ALLOWED=NO   # pass: "** TEST SUCCEEDED **"
plutil -extract NSLocalNetworkUsageDescription raw -o - build/Build/Products/Debug-iphonesimulator/App.app/Info.plist
                                                          # pass: prints the sentence
scripts/lint.sh && scripts/banned-words.sh                # pass: exit 0
grep -rn "URLSession" App PlexGetKit | grep -v "App/Session/URLSessionTransport.swift"   # pass: no output
```

**Human steps.** HUMAN (10-decision-record.md §6 item 9): on a physical iPhone, sign in with a Plex account the
test server is shared with, at home through the LAN probe with no invite (confirm the server-name prompt and the
Local Network string), and reach a search result; repeat away from home with an invite; record both with screenshots.

**Forbidden in this phase.** Any Console screen; `StoreKit`; `PushBridge`, `Background`, `UserNotifications` (Phase
6); an endpoint or field not in 03-server-api.md; `URLRequest(` in `App/`; a `UserDefaults` write of a token; a
banned word; hand-editing `.pbxproj`; a store `Task` without cancellation.

**Exit criteria.** One pull request: `xcodebuild test` output, the `plutil` line, simulator and device sign-ins.

### Phase 5, App: the rest of the household (Mac)

**Goal.** Complete every household screen: Detail (long-press peek), Playlists (list, items, remove, auto-build),
WantedList (Waiting / Arrived / Set aside), Bell (notes, unread dot, the notification state without push yet),
Looks (four built-ins plus owner-made looks, `user.skin`), Report (owner and developer paths), Help and the tour,
and Settings (sessions list, sign out, sign out everywhere, the Home-network toggle), so that a member can do
everything the web page does today except install it to the home screen.

**Inputs.** 04-ios-app.md §4-§8, §11; screens-inventory.md §5 items 9-21, §2.9-§2.20, §2.22; 03-server-api.md
§3.3-§3.7, §4.3-§4.15; 02-architecture.md §2.6, §2.7, §2.16, §6.4; 10-decision-record.md §1 ("a report path that
reaches the developer"), §4 rows "Persona", "LAN probe and trust".

**Deliverables.**

| Path (under `<AppName>-ios/`) | Contents |
|---|---|
| `App/Detail/`, `Playlists/`, `WantedList/`, `Bell/`, `Looks/`, `Report/`, `Help/`, `Settings/` | one store and its views per folder, each screen's states as 04-ios-app.md §4-§8 list them |
| `App/Shared/LookEnvironment.swift` | the twelve-key look applied through the environment |
| `App/Resources/Localizable.xcstrings` | every new string, including the honest polling-latency sentence shown only in the polling state |
| `AppUITests/` | the Phase 5 rows of 04-ios-app.md §11 |
| `docs/build/walk-phase-5.md` | the recorded walk (definition of done, item 2) |

**Definition of done.**

1. `xcodebuild test` passes; no response shape differs from 03-server-api.md, and any drift found was fixed by
   a server test and a document change, not in Swift.
2. Each of screens-inventory.md §5 items 1-21 has a row in `docs/build/walk-phase-5.md` with the item number,
   the screen, the action taken in demo mode and against the real server, and the observed result; items 2, 3
   and 21 in their native form (the hand-off exchange, `401` → sign-in, `DELETE /api/v1/auth/session`).
3. Report's developer path sends to `AppIdentity.supportAddress` (04-ios-app.md §3.1) and never through the
   server; Looks honours `user.skin` unless the person chose their own, remembered per device in
   `UserDefaults`.
4. The sessions list shows own rows for a member; "sign out everywhere" calls `DELETE /api/v1/auth/sessions`.

**Gate commands.**

```
xcodegen generate
xcodebuild test -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' -derivedDataPath build CODE_SIGNING_ALLOWED=NO   # pass: "** TEST SUCCEEDED **"
scripts/lint.sh && scripts/banned-words.sh                # pass: exit 0
test $(grep -c '^| ' docs/build/walk-phase-5.md) -ge 22   # pass: exit 0 (21 rows plus the header)
```

**Human steps.** HUMAN: walk items 9, 13, 14, 16 and 21 on a device against the real server; mark those rows.

**Forbidden in this phase.** A new endpoint or field; a deviation from a 03-server-api.md shape without a server
pull request first; Console screens; `StoreKit`; push and background code; a banned word; a `.storekit` file.

**Exit criteria.** One pull request: test output, `docs/build/walk-phase-5.md` complete, device rows marked.

### Phase 6, Relay and push (relay on Linux, app on Mac)

**Goal.** Build `push-relay` in `<AppName>-relay` with its pytest suite against a fake APNs endpoint and a fake
verifier, deploy it to `push.<developer-domain>`, and give the app `PushBridge`, `Background` polling of `GET
/api/v1/changes` with local notifications, deep links by `ref`, the in-context permission prompt after the first
sign-in, and the Settings action that deletes a member's relay handle.

**Inputs.** 05-push-relay.md (all; §12 is the test contract); relay-CLAUDE.md; 04-ios-app.md §3.3-§3.4, §6;
02-architecture.md §2.12-§2.13; 03-server-api.md §3.8-§3.11; 10-decision-record.md §2 (cloud paragraph), §4 rows
"Push", "Relay language", "Relay data deletion", "Notification permission", §5 items 6 and 13, §6 items 4, 5 and
9; 08-app-store-and-release.md (APNs key, capabilities, TestFlight).

**Deliverables.**

| Repository | Path | Contents |
|---|---|---|
| `<AppName>-relay` | `relay/db.py`, `relay/apns.py`, `relay/entitlements.py`, `relay/quotas.py`, `relay/routes.py`, `relay/join.py` | the layout of 05-push-relay.md §2; tables `devices`, `server_keys`, `apple_notifications`, `quota_counters`; every endpoint of §4 |
| `<AppName>-relay` | `tests/test_devices.py`, `test_server_keys.py`, `test_push.py`, `test_apple_notifications.py`, `test_quotas.py`, `test_join.py`, `test_health.py` | the cases of 05-push-relay.md §12, by name |
| `<AppName>-relay` | `Dockerfile`, `compose.yaml`, `fly.toml`, `docs/verified.md` | 05-push-relay.md §9; the two gate records (definition of done, item 1) |
| `<AppName>-ios` | `App/PushBridge/`, `App/Background/`, `App/Session/UserNotificationsNotifier.swift`, `project.yml`, `App/App.entitlements` | APNs token → `POST /v1/devices` → `POST /api/v1/push/devices`; `BGAppRefreshTask` with `AppIdentity.refreshTaskIdentifier`; foreground display and tap routing; de-duplication on event id; `UIBackgroundModes` `remote-notification` and `fetch`, `BGTaskSchedulerPermittedIdentifiers`, `aps-environment` |
| `<AppName>-ios` | `App/Welcome/`, `App/Bell/`, `App/Settings/` | the in-context prompt with its explaining sentence; push state and `POST /api/v1/push/test`; "delete my handle": `DELETE /v1/devices/<handle>` then `DELETE /api/v1/push/devices/<device_id>` |
| this repository | none | `api/relay.py` is from Phase 2; a change 05-push-relay.md §8 requires is a separate pull request here, with tests |

**Definition of done.**

1. Before `relay/entitlements.py` and `relay/apns.py` are written, the builder has read the App Store Server
   Library's README (PyPI name, module, verifier class) and Apple's "Sending notification requests to APNs"
   (header names, `410` and `400 BadDeviceToken` semantics, the two hosts) and recorded both in `docs/verified.md`
   as (verified: <url>, <date>); a disagreement with 05-push-relay.md §5 or §7 is a deviation note and the
   phase stops (10-decision-record.md §6 items 4 and 5).
2. `pytest -q` in `<AppName>-relay` passes with every case of 05-push-relay.md §12 present by name; `POST
   /v1/devices` returns two distinct random values; the secret is never stored in clear; nothing logs a body.
3. `pytest -q` in this repository still passes with `relay.post` stubbed.
4. `xcodebuild test` passes; the app posts the handle, never the secret, to the server; the polling cursor is
   the only push state in `UserDefaults`.
5. A push from a Phase 2 server through the deployed relay reaches a TestFlight build on a device, and a
   forced `BGAppRefreshTask` fetches `/api/v1/changes` and posts a local notification for a new event.

**Gate commands.**

```
# <AppName>-relay, Linux
venv/bin/pytest -q -v                                       # pass: "N passed", every §12 case listed by name
grep -rn "logger\|logging" relay/ | grep -i "body\|title"   # pass: no output
# this repository, Linux
venv/bin/pytest -q                                          # pass: "N passed"
# <AppName>-ios, Mac
xcodegen generate
xcodebuild test -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' -derivedDataPath build CODE_SIGNING_ALLOWED=NO   # pass: "** TEST SUCCEEDED **"
scripts/lint.sh && scripts/banned-words.sh                  # pass: exit 0
```

**Human steps.** HUMAN: create the APNs key, set `APNS_KEY_P8`, `APNS_KEY_ID`, `APNS_TEAM_ID`, `RELAY_QUOTA_SALT`
(05-push-relay.md §9), deploy to `push.<developer-domain>`, and enable Push Notifications, Background Modes and
Associated Domains on the app record (08-app-store-and-release.md). HUMAN (10-decision-record.md §6 item 9):
install a TestFlight build on a device, register, send a test from the Bell, and paste the relay's `sent`
response and a screenshot of the notification. HUMAN: with the app attached to Xcode on the device, pause and run
`e -l objc -- (void)[[BGTaskScheduler sharedScheduler] _simulateLaunchForTaskWithIdentifier:@"<AppIdentity.refreshTaskIdentifier>"]`
(unverified: Apple's BGTaskScheduler documentation as remembered; the builder confirms and records the command),
then paste the `os.Logger` lines showing the fetch and the local notification.

**Forbidden in this phase.** Logging or storing a push body or title in the relay; a Plex token, session token or
server address in any relay request, table or log; a Notification Service Extension; App Attest; a `.p8` file or
its contents in either repository; `StoreKit`; the `POST /v1/server-keys` client (Phase 7); a second relay worker.

**Exit criteria.** Two pull requests (relay, app): gate output, `docs/verified.md`, the TestFlight push and the
forced refresh recorded with screenshots and log lines.

### Phase 7, StoreKit and Console (Mac)

**Goal.** Add `Store` (StoreKit 2 wrapper, `OwnerEntitlement`, `Products.storekit`), server-key issuance at the
relay and forwarding to `POST /api/v1/owner/relay`, "Unlink and delete my relay data", and the Console tab (Requests,
Inbox, Announce, Discover, Overview, Notifications, Server, Sessions, Paywall) behind the entitlement, with
purchase, lapse and restore proven by StoreKit testing and a sandbox purchase.

**Inputs.** 04-ios-app.md §3.2, §8, §11; 02-architecture.md §2.14-§2.16; 03-server-api.md §3.12-§3.14,
§4.16-§4.28; 05-push-relay.md §4.3-§4.4, §5, §12; 10-decision-record.md §4 rows "Monetisation", "What is paid",
"Relay entitlement source", "Relay data deletion", "`PUT /api/v1/owner/notifications`" (cut), §5 items 11, 14
and 20, §6 item 9; 08-app-store-and-release.md; design-A §6.

**Deliverables.**

| Path (under `<AppName>-ios/`) | Contents |
|---|---|
| `App/Store/Store.swift`, `OwnerEntitlement.swift`, `Products.storekit`, `project.yml` (the `App` scheme's run and test actions use the configuration) | `Transaction.currentEntitlements` at launch; one `Transaction.updates` listener; `Product.SubscriptionInfo.Status` for grace and billing retry; only `VerificationResult.verified`; `AppStore.sync()` behind "Restore purchases"; both products with the 7-day introductory offer on `owner.yearly` |
| `App/Console/ConsoleTab.swift`, `ConsoleGate.swift`, `Requests/`, `Inbox/`, `Announce/`, `Discover/`, `Overview/`, `Notifications/`, `Server/`, `Sessions/`, `Paywall/` | the screens of 04-ios-app.md §8; Notifications read-only; Server shows the pairing card, the invite QR, relay state from `GET /api/v1/owner/status`, and "Unlink and delete my relay data" |
| `App/Session/RelayClient.swift` | `POST /v1/server-keys` with `jwsRepresentation` → `{server_key, relay_url, expires_at}` to `POST /api/v1/owner/relay`; `DELETE /v1/server-keys/<id>` and `DELETE /api/v1/owner/relay` |
| `AppTests/StoreTests.swift`, `AppUITests/` | purchase, lapse, restore, grace through the configuration; the Phase 7 rows of 04-ios-app.md §11 with the demo Console open and marked "demo" |
| `<AppName>-relay/tests/test_server_keys.py` | the real-verifier forged-JWS case, if Phase 6 left it pending |

**Definition of done.**

1. `xcodebuild test` passes with the StoreKit configuration, covering purchase of each product, a lapse that
   closes the Console and keeps every household screen, restore, and the grace state.
2. The Console opens only with `OwnerEntitlement.active` and role `owner`; the demo Console only via `DemoServerClient`.
3. The Paywall states the trial length, the price billed afterwards, the period, the auto-renewal terms and
   links to the privacy policy and terms (design-A §9, 3.1.2).
4. After a purchase the app has posted the JWS to the relay and the server key to the server, and `GET
   /api/v1/owner/status` reports `push.delivery: "relay"`; "Unlink" calls both deletes and the server reports `polling`.
5. The relay refuses a forged JWS through the real `SignedDataVerifier` with `401 invalid_jws`.

**Gate commands.**

```
xcodegen generate
xcodebuild test -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' -derivedDataPath build CODE_SIGNING_ALLOWED=NO   # pass: "** TEST SUCCEEDED **", StoreTests listed
grep -rn "VerificationResult" App/Store | grep -c "verified"   # pass: at least 1; no ".unverified" branch grants
grep -rn "api/v1/owner/notifications" App PlexGetKit          # pass: no output
cd <AppName>-relay && venv/bin/pytest -q tests/test_server_keys.py -k forged   # pass: "2 passed"
scripts/lint.sh && scripts/banned-words.sh                # pass: exit 0
```

**Human steps.** HUMAN: create the "Owner" group, `owner.yearly` with the 7-day introductory offer, `owner.monthly`,
Family Sharing off, and a sandbox tester (08-app-store-and-release.md). HUMAN (10-decision-record.md §6 item 9):
on a device signed in as the owner, make a sandbox purchase, confirm the Console opens, and paste `ls -l
data/relay.json` (mode 0600) and the `GET /api/v1/owner/status` body.

**Forbidden in this phase.** An entitlement decision on a transaction that is not `VerificationResult.verified`;
an unlock that is not StoreKit (no code, no link, no mention of the web dashboard); an open Console outside
`DemoServerClient`; `PUT /api/v1/owner/notifications`; any call to `/wanted/export`, `/export*`, `/sonarr/*`,
`/radarr/*`; editing a household screen except to add the Console tab; a receipt or JWS reaching the server.

**Exit criteria.** One pull request in `<AppName>-ios` (one in `<AppName>-relay` if the forged-JWS test moved here):
gate output, the sandbox purchase with both pasted bodies, the two greps empty.

### Phase 8, Install: image, compose and setup page (Linux; may run in parallel with Phases 4-7)

**Goal.** Ship the server as a published image with a first-run setup page: `Dockerfile`, `docker-compose.yml`
with the `plexget` service and Posteria under `profiles: [posters]`, `install.sh`, `.github/workflows/image.yml`
publishing amd64 and arm64 on tag, `api/setup.py` and `dashboard/setup.html` with the five steps and the health
panel (`api/checks.py`, from `test_connections.py`), `docs/DEPLOYMENT.md` Docker-first, and `tests/test_setup.py`.

**Inputs.** 06-install-and-setup.md (all; §5.4 and §5.5 are the GAP shapes to add, §13 the gates); 03-server-api.md
§5; 10-decision-record.md §2 ("Install artefacts"), §4 rows "Remote reach", "Server install", "Bind address", §5
items 7, 8, 9 and 19, §6 items 2 and 3; deployment-and-security.md §2.4, §3, §4.2-§4.3, §9, §10;
`test_connections.py:48-232`; `DEPLOYMENT.md:208-270`.

**Deliverables.**

| Path | Contents |
|---|---|
| `Dockerfile` | 06-install-and-setup.md §2: `python:3.12-slim`, user `plexget` 1000:1000, `PLEXGET_CONFIG=/data/config.yaml`, `PLEXGET_DATA=/data`, `PLEXGET_LOOPBACK_OWNER=0`, `PLEXGET_SETTLE_MINUTES=120`, `VOLUME /data`, `HEALTHCHECK` on `/health`, `CMD ["python", "main.py"]` |
| `docker-compose.yml` | 06-install-and-setup.md §3, replacing the Posteria-only file in place. Note: design-A §2.4 names `compose.yaml` and `compose.posteria.yml`; the ADR keeps `docker-compose.yml` with `profiles: [posters]`, and the ADR wins |
| `install.sh`, `.github/workflows/image.yml` | 06-install-and-setup.md §4.4; build on pull requests, push on tags `v*`, `linux/amd64,linux/arm64` |
| `api/setup.py`, `dashboard/setup.html`, `api/checks.py` | the setup routes of 06-install-and-setup.md §5.4; the health panel of §5.5 sharing functions with `GET /api/v1/owner/status`; `/setup` bound to the first Plex account that completes it |
| `config.py`, `api/routes.py` | in setup mode the guard serves only `/setup/*`, `/health` and `/api/v1/server`; the setup save writes `flask.host: 0.0.0.0` and `app.loopback_owner: false` together |
| `docs/DEPLOYMENT.md`, `README.md`, `SECURITY.md` | Docker first, venv and systemd second; the LAN-exposure paragraph of 10-decision-record.md §5 item 19; the door's status per install path |
| `tests/test_setup.py` | every route of §5.4 with plex.tv, `PlexServer`, TMDb and the model stubbed; the health panel; the client-identifier rule re-asserted; `/health` → `{"status": "setup"}`; after save, `SETUP_MODE` false and the page requiring an owner session |

**Definition of done.**

1. `docker build` succeeds locally; `image.yml` is green on the pull request for both architectures; the image
   contains no secret and no `config.yaml`; `pytest tests/test_setup.py` and `pytest -q` pass.
2. A container with an empty volume answers `GET /health` with `{"status": "setup"}` and `GET /api/v1/server`
   with `"setup": true`; `GET /libraries` from the host is `403` (the door is off).
3. Before the Funnel copy in `dashboard/setup.html` and `docs/DEPLOYMENT.md` is final, the Funnel check below is
   in the pull request and the copy follows 06-install-and-setup.md §6.3 if Funnel is unavailable (§6 item 2).
4. The venv path is unchanged: `python main.py` with `config.yaml` beside `config.py` and no environment
   variables binds `127.0.0.1` with the door on.

**Gate commands.**

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

**Human steps.** HUMAN (10-decision-record.md §6 item 2): with a free Tailscale Personal account, run `tailscale
funnel 5050` on a machine running the image, record whether it is permitted, and paste the request headers Flask
received through it (a temporary header dump, removed before merge); the result decides the `X-Forwarded-Proto`
sentence in the documentation. HUMAN: a first run recorded on a Linux box and on Docker Desktop, from `install.sh`
to a completed setup page and a household sign-in, with screenshots.

**Forbidden in this phase.** Baking a secret, `config.yaml` or `.env` into the image; changing any `/api/v1` shape
or fixture; changing the venv path's defaults (`127.0.0.1`, door on); a compose file that builds locally;
`network_mode: host` as the compose default; a second worker process; `api/native.py` beyond setup-mode serving.

**Exit criteria.** One pull request: Docker gate output, both pytest outputs, the Funnel result and headers, the
two recorded first runs.

### Phase 9, Release (Mac plus human)

**Goal.** Make the build submittable: the banned-word review of every fixture and string, the privacy manifest
with its required-reason entries, screenshots from demo mode, review notes with the demo server and reviewer
account, TestFlight with two households, and the human-only App Store Connect work. No feature changes.

**Inputs.** 08-app-store-and-release.md (all); 04-ios-app.md §10.3, §11; design-A §9; 10-decision-record.md §2
("Demo environment"), §4 rows "App Store name", "Monetisation", §5 items 15 and 20, §6 items 10 and 11;
constraints brief facts 7, 8, 9 and 14.

**Deliverables.**

| Path (under `<AppName>-ios/`) | Contents |
|---|---|
| `App/Resources/PrivacyInfo.xcprivacy` | `NSPrivacyAccessedAPITypes` for `UserDefaults` and file timestamps with their reason codes (unverified: the builder copies the code strings from Apple's privacy manifest documentation and records the page); `NSPrivacyCollectedDataTypes` for notification content transiting the relay, not linked, not for tracking; `NSPrivacyTracking: false` |
| `docs/release/review-notes.md` | the App Store Connect text: demo mode from Welcome, the demo server address, the reviewer Plex account, sign-out and device removal (5.1.1(v)), the closed household (1.2) |
| `docs/release/screenshots/` | one per required device size, taken in demo mode with `xcrun simctl io booted screenshot` |
| `docs/release/checklist.md` | every human step of 08-app-store-and-release.md with "done" or "pending" |
| `App/Resources/Localizable.xcstrings`, fixtures | only copy fixes the review found |

**Definition of done.**

1. `scripts/banned-words.sh` is clean over `App/`, `PlexGetKit/`, `PlexGetKit/Tests/Fixtures/`, `docs/release/`
   and `review-notes.md` (10-decision-record.md §6 item 10); no Plex mark in the app name, in the subtitle beyond
   "for Plex" if the owner allowed it, or in the icon (the human reviews `Assets.xcassets/AppIcon`).
2. `xcodebuild archive` produces no privacy-manifest warning (a signed archive is a human step).
3. The demo server and reviewer account work over HTTPS from outside the house with the TestFlight build.
4. Two households have used the TestFlight build for at least a week; their reports, including any abandonment
   at the remote-reach step, are in the pull request (10-decision-record.md §6 item 11).
5. Guideline numbering in `review-notes.md` was re-checked by the human against the current guidelines
   (10-decision-record.md §5 item 15).

**Gate commands.**

```
scripts/banned-words.sh App PlexGetKit docs/release       # pass: exit 0
grep -rn -i "plex" App/Resources/Assets.xcassets/AppIcon.appiconset/Contents.json   # pass: no output
plutil -lint App/Resources/PrivacyInfo.xcprivacy          # pass: "OK"
xcodegen generate
xcodebuild test -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' -derivedDataPath build CODE_SIGNING_ALLOWED=NO   # pass: "** TEST SUCCEEDED **"
xcrun simctl io booted screenshot docs/release/screenshots/<size>-<screen>.png     # pass: files exist, demo banner visible
# human, on a Mac with signing
xcodebuild -scheme App -destination 'generic/platform=iOS' archive -archivePath build/App.xcarchive   # pass: "** ARCHIVE SUCCEEDED **"
```

**Human steps.** HUMAN, each from 08-app-store-and-release.md: Developer Program enrolment; the App Store Connect
record and bundle identifier; capabilities (Push Notifications, Associated Domains, Background Modes); the APNs
key (Phase 6); products and the introductory offer (Phase 7); Billing Grace Period set to 16 days to match the
relay (10-decision-record.md §2); the App Store Server Notifications V2 URL
`https://push.<developer-domain>/v1/apple/notifications`; the privacy policy URL; the demo Plex server and
reviewer account; screenshots and notes uploaded; TestFlight with two households; the App Store name and subtitle
(after reading Plex's brand guidelines); the guideline-number check; archive, upload, submission.

**Forbidden in this phase.** Any code change beyond copy, the privacy manifest and the release documents; a fixture
edit that is not a banned-word fix re-recorded through the server's pytest; any banned word or mention of the web
dashboard in metadata or notes; a Plex logo or colour scheme in the icon; the builder touching App Store Connect.

**Exit criteria.** One pull request: gate output, `docs/release/checklist.md` with every human step marked, the
TestFlight reports, the review notes; then the human submits.

## 6. Gate index

Where each open risk of 10-decision-record.md §6 is carried: item 1 (pin linking) and item 7 (Plex Home users)
are Phase 1's HUMAN steps; item 2 (Funnel) is Phase 8's HUMAN step; item 3 (client identifier) is
`tests/test_client_id.py` in Phase 2, re-run in Phase 8; items 4 and 5 (Apple's library, APNs) are Phase 6's
`docs/verified.md`; item 6 (local-network string and probe) is Phase 4's `plutil` line and `Pairing` test; item
8 (shape drift) is `FixturesTests` and the fixtures diff in every phase; item 9 (devices) is the named HUMAN
step of Phases 4, 5, 6, 7 and 9; item 10 (App Review) is `scripts/banned-words.sh` in every workflow and Phase
9's icon and name review; item 11 (abandonment) is Phase 9's TestFlight reports and the note after launch.
