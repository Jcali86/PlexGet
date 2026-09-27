# Phase 0: Scaffold

Machine: Linux, plus one Mac step (the simulator build). Repositories: this server repository (public),
`<AppName>-ios` and `<AppName>-relay` (private), the latter two created empty by the owner and cloned beside this
one at `../<AppName>-ios` and `../<AppName>-relay`. The owner fills the block below before pasting; if any `<...>`
placeholder is still unfilled, stop and say which.

```
APP_NAME:          <AppName>                 # App Store name; must not contain "Plex" (00-README.md §5)
BUNDLE_ID:         <bundle identifier>       # reverse-DNS, e.g. com.example.<appname>
ORG:               <org>                     # GitHub organisation or user; image ghcr.io/<org>/plexget
DEVELOPER_DOMAIN:  <developer-domain>        # relay at push.<developer-domain>; invite host
PRICE_YEARLY:      <price>                   # owner.yearly, with the 7-day introductory trial
PRICE_MONTHLY:     <price>                   # owner.monthly
SUPPORT_EMAIL, PRIVACY_URL, TERMS_URL: support@<developer-domain>, https://<developer-domain>/privacy,
                   https://<developer-domain>/terms   # AppIdentity placeholders until Phase 9
```

## Read first

Read each file in full, in this order, before any other tool call. Paths are relative to this server repository.

1. `docs/ios/00-README.md` §3-§5 (running the builder, the deviation note, the fill-in values)
2. `docs/ios/10-decision-record.md` §2 (every name), §5 item 16 (simulator destination), §7 (phase list)
3. `docs/ios/07-build-phases.md` §1, §2, §4 (the three CI workflows, verbatim), §5 "Phase 0, Scaffold"
4. `docs/ios/04-ios-app.md` §1 (layout, Info.plist keys), §2.1 (the four protocols), §2.2 (the endpoint-path
   literal list `lint.sh` greps for), §2.9 (the `Bundle.module` gate), §3.1 (`AppIdentity`), §10.2, §10.3
5. `docs/ios/03-server-api.md` §6.1-§6.2 (the fixture envelope and the `record` writer)
6. `docs/ios/05-push-relay.md` §2 (relay stack and layout), §9 (`relay/wsgi.py`), §12 (test harness)
6a. `docs/ios/08-app-store-and-release.md` §5 (the `scripts/banned-words.sh` listing, copied verbatim)
7. `docs/ios/ios-CLAUDE.md` and `docs/ios/relay-CLAUDE.md` (copied verbatim as `CLAUDE.md` in this phase)
8. `config.py` lines 15-30 and 110-130; `api/routes.py` lines 125-135; `CONTRIBUTING.md` (house style)

## Goal

Create the skeleton of the three repositories so that every later phase starts from a passing gate: a `CLAUDE.md`
loaded on every session, a generated Xcode project that builds on a simulator, a Kit package that tests on Linux, a
pytest pattern that survives `config.py`'s import-time validation, and CI on all three. No feature code exists at
the end; the only production change is the one-line read of `PLEXGET_CONFIG` in `config.py`. Note:
10-decision-record.md §7 lists `PLEXGET_CONFIG` under Phase 2; `tests/conftest.py` cannot work without it, so that
line moves here (07-build-phases.md §5 Phase 0) and `PLEXGET_DATA` and setup mode stay in Phase 2.

## Constraints

- Forbidden in this phase (07-build-phases.md §5 Phase 0): any feature code (no `/api/v1` route, no Kit module
  beyond the four protocols, no screen); any package-level `.package(` dependency in `Package.swift` (the test
  target's `dependencies: ["PlexGetKit"]` is not one) or a second `packages` entry in `project.yml`; hand-editing or
  committing a `.pbxproj`; any change to `config.py` beyond the one line, or under `api/`; `print(`; a string
  outside `Localizable.xcstrings`.
- `project.yml` is the only project description; `.xcodeproj` is generated and gitignored (ios-CLAUDE.md §1).
- Swift 6 language mode, `swift-tools-version: 6.0`, strict concurrency, `-warnings-as-errors`, iOS 17.0 (§2).
- Every product, bundle, topic and domain constant lives in `App/AppIdentity.swift` and nowhere else (§1).
- Never commit a `.p8`, `.env` or key material in the relay repository (relay-CLAUDE.md §4).
- No network call in any test; the relay `conftest.py` installs a `socket.socket` monkeypatch that raises (§9).
- Commits are small, one thing each, imperative first line naming the phase, never "we" (`CONTRIBUTING.md:81`).

## Steps

Note: this server part of Phase 0 already exists, added with the library index: the `PLEXGET_CONFIG` line in `config.py`, `requirements-dev.txt` (pinned to `pytest==9.1.1`, the current release, not 8.x), `pytest.ini`, `tests/__init__.py`, `tests/conftest.py` (with `app`, `client`, `record`, plus `index_path` and `plex` fixtures and an `index` block in the temporary config), `tests/fake_plex.py`, `tests/test_smoke.py` and `tests/fixtures/api/.gitkeep`. Check each against this specification and fix any difference; do not recreate them.

1. In this repository: `git checkout -b phase-0-scaffold main`. Confirm `config.yaml` is absent from the
   checkout; if present, do not delete it, note it, and make sure no test reads it.
2. Edit `config.py` line 21 (`CONFIG_PATH = ROOT / "config.yaml"`) to `CONFIG_PATH = Path(os.environ.get("PLEXGET_CONFIG") or ROOT / "config.yaml")`,
   adding `import os` beside `import sys`. Nothing else in the file changes.
3. Create `requirements-dev.txt` containing the current `pytest==8.<x>` pin (record the exact version you
   install) and `pytest.ini` with `[pytest]`, `testpaths = tests`, `addopts = -p no:cacheprovider`.
4. Create `tests/__init__.py` (empty), `tests/conftest.py` and `tests/test_smoke.py`. `conftest.py`, at module
   level before any project import, writes a valid `config.yaml` into a directory from `tempfile.mkdtemp`
   (fixtures cannot run before imports, so `tmp_path_factory` is not usable here) with `plex.url:
   http://plex.test:32400`, `plex.token: FIXTURE-PLEX-TOKEN`, `flask.host: 127.0.0.1` and `database.path` in the
   same directory (this prompt's addition, so no test touches `plex_ops.db`), sets `os.environ["PLEXGET_CONFIG"]`
   to it, and only then imports `config` and `api`. Provide fixtures `app` (`create_app()`, `app.testing = True`),
   `client` and `record(name, response)` implementing 03-server-api.md §6.2 exactly (the §6.1 envelope,
   `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)` plus a newline, the four normalisation rules).
   `test_smoke.py` asserts `GET /health` is `200` with `status == "ok"` and a `build` key (`api/routes.py:127-132`).
   Create `tests/fixtures/api/.gitkeep`.
5. Create `.github/workflows/tests.yml` as 07-build-phases.md §4.1 without the `banned words` step (the script
   arrives in Phase 1). Add `.pytest_cache/` to `.gitignore`. Install `requirements-dev.txt`; run the gate.
6. In `../<AppName>-ios`: `git checkout -b phase-0-scaffold`. Copy `docs/ios/ios-CLAUDE.md` to `CLAUDE.md`
   verbatim. Copy `docs/ios/prompts/phase-N.md` (N = 0..9) to `docs/build/phase-N.md` verbatim, and copy every
   `docs/ios/*.md` to `docs/plan/` with the same file names, so Mac sessions have the plan on disk (later prompts
   cite `docs/plan/<file>` when read from that repository). Create `.gitignore` with `*.xcodeproj`, `build/`,
   `DerivedData/`, `.build/`.
7. Create `PlexGetKit/Package.swift`: `swift-tools-version: 6.0`, `swiftLanguageModes: [.v6]`, `platforms:
   [.iOS(.v17)]`; no package-level `.package(` dependency. The vendored fixtures live at
   `PlexGetKit/Tests/Fixtures/` (10-decision-record.md §2 fixes that path) and must be a resource of the
   `PlexGetKit` library target, not of the test target, so that `Bundle.module` exists in the library, where
   Phase 3's `Demo/` reads them, and the tests read the same files through the library's bundle. Layout, as
   07-build-phases.md §5 Phase 0 gives it (unverified: SwiftPM is believed to refuse a resource path outside the
   target directory, which is why the target's `path` is the package root): library target `PlexGetKit` with
   `path: "."`, `sources: ["Sources/PlexGetKit"]`, `exclude: ["Tests/PlexGetKitTests"]`,
   `resources: [.copy("Tests/Fixtures")]`; test target `PlexGetKitTests` with `path: "Tests/PlexGetKitTests"`,
   `dependencies: ["PlexGetKit"]` and no resources. `.copy` keeps the directory, so the files sit under
   `Fixtures/` inside the bundle and are read with `subdirectory: "Fixtures"`. If `swift build` refuses this
   layout, try `exclude` without `sources` or the reverse and record under `## Changed` the layout it accepted; if
   no layout gives the library target a `Bundle.module` holding `Fixtures/VERSION`, write the deviation note and
   stop, and never keep a second copy of the fixtures or read them from the file system. Create
   `PlexGetKit/Sources/PlexGetKit/Protocols/` with `Transport.swift`, `SecretStore.swift`, `Entitlements.swift`,
   `Notifier.swift` holding exactly the declarations of 04-ios-app.md §2.1 with doc comments and
   `FoundationNetworking` behind `#if canImport(FoundationNetworking)`; `Notifier.swift` also declares
   `NotificationAuthorisation` (04 §2.1) and `LocalNotification` plus `Destination` (04 §2.7, verbatim) so the
   package compiles, and Phase 3 moves none of them; `Tests/PlexGetKitTests/SmokeTests.swift` with one passing
   swift-testing test that, with `@testable import PlexGetKit`, asserts
   `Bundle.module.url(forResource: "VERSION", withExtension: nil, subdirectory: "Fixtures")` is not nil (the
   Phase 0 gate of 04-ios-app.md §2.9); and `Tests/Fixtures/VERSION` containing the single line `unset`.
8. Create `project.yml`: targets `App` (iOS application, `deploymentTarget` 17.0), `AppTests` (unit test bundle),
   `AppUITests` (UI test bundle); `packages: PlexGetKit: path: PlexGetKit`; settings `SWIFT_VERSION: 6.0`,
   `SWIFT_STRICT_CONCURRENCY: complete`, `OTHER_SWIFT_FLAGS: -warnings-as-errors`, `PRODUCT_BUNDLE_IDENTIFIER`
   from `BUNDLE_ID`; the Info.plist keys of 04-ios-app.md §1 with placeholder values (the local-network sentence
   arrives in Phase 4), `CFBundleDisplayName` from `APP_NAME`; `App/App.entitlements` with
   `aps-environment` `development` and `com.apple.developer.associated-domains` `applinks:push.<DEVELOPER_DOMAIN>`;
   a scheme `App` whose test action runs `AppTests` and `AppUITests`.
9. Create `App/AppMain.swift` (a `@main` `App` showing one `String(localized:)` string), `App/AppIdentity.swift`
   (every constant of 04-ios-app.md §3.1 from the fill-in block; URL literals through `URL(string:)` in a closure
   calling `preconditionFailure` on `nil`), `App/Resources/Localizable.xcstrings` (en-GB base, that one string),
   `App/Resources/Assets.xcassets` (empty `AppIcon.appiconset` and `AccentColor`),
   `App/Resources/PrivacyInfo.xcprivacy` (`NSPrivacyTracking: false`, empty arrays elsewhere),
   `AppTests/SmokeTests.swift` and `AppUITests/SmokeUITests.swift` (one passing XCTest each; the UI test launches
   with `--demo --uitest`).
10. Create `scripts/lint.sh` (the greps of 07-build-phases.md §4.2, exit 1 on any hit, including the endpoint-path
    grep: over `PlexGetKit/Sources` and `App/**/*.swift` only, never `App/Resources/` or the fixtures, the exact
    literal list of 04-ios-app.md §2.2, where a path that is a suffix of a permitted route carries its opening
    double quote, such as `"/push/test"`, so `"/api/v1/push/test"` is not a hit), `scripts/banned-words.sh`
    (the listing of 08-app-store-and-release.md §5 verbatim: the words of 04-ios-app.md §10.3 over the given
    paths, default `App PlexGetKit` plus `store` only when that directory exists, so the default never names a
    path that is absent before Phase 9; a named path that does not exist is exit `2`), `scripts/sync-fixtures.sh` (copies `<arg>/tests/fixtures/api/*.json`
    into `PlexGetKit/Tests/Fixtures/` and writes `git -C <arg> rev-parse HEAD` to `VERSION` there);
    `chmod +x` all three.
11. Create `.github/workflows/kit.yml` and `app.yml` exactly as 07-build-phases.md §4.2 and §4.3; leave
    `Xcode_16.2.app` as the placeholder until the Mac step records the runner's version, then pin it.
12. In `../<AppName>-relay`: `git checkout -b phase-0-scaffold`. Copy `docs/ios/relay-CLAUDE.md` to `CLAUDE.md`
    verbatim and `docs/ios/*.md` to `docs/plan/`. Create `relay/__init__.py` (`create_app(config) -> Flask` and
    nothing else), `relay/config.py` (a `Config` dataclass and `Config.from_env()`, which reads the environment;
    only `RELAY_DB` is required in this phase; tests build a `Config` directly and never call `from_env()`),
    `relay/wsgi.py` (`app = create_app(Config.from_env())`, the `waitress-serve` target of 05 §9), `relay/routes.py`
    (`GET /v1/health` → `200 {"status": "ok"}`), `tests/conftest.py` (`RELAY_DB=:memory:`, the `socket.socket`
    monkeypatch), `tests/test_health.py`, `pytest.ini`, `requirements.txt` (`Flask` and `waitress` pinned),
    `requirements-dev.txt` (`pytest` 8.x), `.github/workflows/ci.yml` (07 §4.1 with `python-version: "3.12"` and
    only the pytest step), `.gitignore` with `*.p8`, `.env`, `venv/`, `.pytest_cache/`, `*.db`.
    `python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-dev.txt`.
13. Swift toolchain: run `swift --version`. If it prints nothing or a version below 6.0, either run every Kit
    command through Docker (present on the remote environment), as `docker run --rm -v
    "$PWD/PlexGetKit:/src" -w /src swift:6.0-noble sh -c 'swift build -Xswiftc -warnings-as-errors && swift
    test'` (07-build-phases.md §2; unverified image tag), or install the Swift 6.0 toolchain per 07 §2 (the
    swift.org Linux tarball for this machine's Ubuntu release; read swift.org/install/linux for the current file
    name and the apt packages it lists; unverified URL: record the one used), add its `usr/bin` to `PATH` for
    this session and record the version. Paste the `swift --version` output either way. If neither is possible
    in this environment, list every `swift` gate line under `## Not run here` and rely on `kit.yml` (07 §4.2) as
    the Kit gate; do not skip the rest of the phase. Then run every Linux gate command below in each repository.
    Commit in small steps. Push all three branches.
14. HUMAN: run the Mac gate commands in `<AppName>-ios` (Xcode 16, `xcodegen`), paste the output into that pull
    request, record the versions, pin the Xcode path in `app.yml`, add the `mac-ci` label. Wait for green.

## Gate

```
# this repository, Linux
python3 --version && venv/bin/pytest --version
venv/bin/pytest -q                        # pass: "N passed", exit 0
# <AppName>-ios, Linux (each swift line through the step 13 Docker wrapper when there is no local toolchain)
swift --version                           # pass: 6.0 or later
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

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you
cannot, write docs/build/deviations.md and stop.

## Deliver

Three pull requests, one per repository, each titled `Phase 0: Scaffold`, from branch `phase-0-scaffold`. Each
body has the seven headings of 07-build-phases.md §1.2 in order: `## Goal` (the paragraph above), `## Changed`
(that repository's rows of the Phase 0 deliverables table, ticked or "not needed: <why>"), `## Gate output`
(every command and its complete output), `## Not run here` (the Mac commands, when this session is Linux),
`## Human steps` (the Mac step "done: <date>, <who>, <versions>" or "pending"), `## Deviations` ("none" or the
entry), `## Forbidden` (each item above with "confirmed absent" and the grep used). Record the tool versions the
gate printed. Then stop and wait for the owner; do not start Phase 1.

## Do not

- Do not write any `/api/v1` route, Kit module, screen or relay endpoint other than `GET /v1/health`; a later
  phase owns each of those.
- Do not commit `config.yaml`, a real Plex token, or the temporary file `conftest.py` writes.
- Do not add a package-level `.package(` dependency to `Package.swift` or a second `packages` entry in
  `project.yml`, whatever a build error suggests; a dependency is a deviation note, not a fix (the test target's
  local `dependencies: ["PlexGetKit"]` is required, not a dependency in this sense).
- Do not create or commit an `.xcodeproj`; if `xcodegen generate` fails, fix `project.yml`.
- Do not change `config.py` beyond the `CONFIG_PATH` line, and do not touch `api/`, `db/` or `scanner/`.
- Do not put "Plex" in `APP_NAME` or `CFBundleDisplayName`, or a URL or product string outside `AppIdentity`.
- Do not claim the Mac gate passed from a Linux session; list it under `## Not run here` and wait.
