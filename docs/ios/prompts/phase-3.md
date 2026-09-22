# Phase 3: PlexGetKit

Machine: Linux. Repository: `<AppName>-ios` (private). The server repository is cloned beside it at
`../PlexGet` with Phase 2 merged on `main`. Phase 0 is merged here: `PlexGetKit/Package.swift`, the four
protocol files, `scripts/`, `CLAUDE.md` and `docs/plan/` exist. The plan documents are read from `docs/plan/`.

## Read first

Read each file in full, in this order, before any other tool call.

1. `CLAUDE.md` (the standing rules; §4 code rules and §9 are the ones this phase tests)
2. `docs/plan/00-README.md` §3-§4
3. `docs/plan/10-decision-record.md` §2 (the Kit paragraph), §4 rows "LAN probe and trust", "Feature flag",
   "Persona", "Fixtures", §5 items 4, 5, 18 and 22, §6 items 6 and 8
4. `docs/plan/07-build-phases.md` §5 "Phase 3, PlexGetKit"
5. `docs/plan/04-ios-app.md` §2 (every module, verbatim signatures), §10.1, §12 (GAP 1: `DemoServerClient(role:)`)
6. `docs/plan/03-server-api.md` §1 (conventions: URL set, headers, tiers, error envelope, dates, timeouts), §2
   (every shared shape, field by field), §3, §4, §6.3-§6.4 (the catalogue), §7, §8
7. `docs/plan/02-architecture.md` §2.1 (transitions), §2.3-§2.4, §2.7, §2.13, §4.1, §5, §6.1, §6.3
8. `docs/plan/09-security.md` §4 (the `.local` rule), §11 (the Kit tests by name)
9. `docs/plan/05-push-relay.md` §4.1-§4.4 (the relay calls `RelayClient` builds)
10. `../PlexGet/dashboard/request.html` lines 37-79 (the four palettes, copied digit for digit)
11. `PlexGetKit/Package.swift`, `PlexGetKit/Sources/PlexGetKit/Protocols/*.swift`, `scripts/lint.sh`

## Goal

Build the Foundation-only Swift package: `Models`, `ServerAPI`, `PlexTV`, `SignIn`, `Pairing`, `Changes`,
`Themes`, `Demo`, and the four protocols in their final shape, with tests that decode every vendored fixture in
full, exercise every error mapping, drive `SignIn` through every transition on injected clocks, and rank pairing
candidates against fake probes (07-build-phases.md §5 Phase 3).

## Constraints

- Forbidden in this phase (07-build-phases.md §5 Phase 3): `import UIKit`, `StoreKit`, `Security`,
  `UserNotifications`, `CryptoKit` under `PlexGetKit/`; `URLSession` in the Kit; `URLRequest(` outside
  `Endpoints.swift`; force unwraps, `try!`, `as!`, `print(`; any file under `App/`; editing a fixture by hand; a
  type that decodes `persona.voice` or `persona.examples`; hashing the machine identifier.
- Decode with `Models.decoder` (`.convertFromSnakeCase`), ignore unknown keys, make every required field
  non-optional, give `Film` no `missingFiles` property (CLAUDE.md §4). A fixture the Kit cannot decode is a
  server test and a document change, never a lenient guess (CLAUDE.md §9).
- No third-party package; `dependencies` stays absent from `Package.swift` (CLAUDE.md §3).
- Every public type is `Sendable`; every model is a `struct` or `enum`; no `@unchecked Sendable`,
  `nonisolated(unsafe)`, `DispatchQueue`, `NSLock`, `Thread` (CLAUDE.md §4).
- The Kit has no `os` import and logs nothing; `RedactionTests` proves descriptions hide secrets (CLAUDE.md §5).
- No string literal for an endpoint path outside `Endpoints.swift`; no builder for any path in 03 §8.

## Steps

1. `git checkout -b phase-3-plexgetkit main`. Run `scripts/sync-fixtures.sh ../PlexGet`; confirm
   `PlexGetKit/Tests/Fixtures/VERSION` is the 40-character hash of the server's `main` and that the directory
   holds the 73 files of 03 §6.4 plus the three `204` files of 04 §12 GAP 2. Commit the fixtures and `VERSION`.
2. `Sources/PlexGetKit/Protocols/`: confirm the four protocols match 04 §2.1 exactly; add
   `NotificationAuthorisation` and `LocalNotification` if Phase 0 left them out.
3. `Sources/PlexGetKit/Models/`: one file per Kit type named in 03 §2 and the third column of §6.4 (`Film`
   merging shapes 2a and 2b with shape-specific fields optional; `Show`, `FilmGuess`, `ShowGuess`,
   `DiscoveryItem`, `WantedRow`, `Filters`, `Message`, `Note`, `Pick`, `Persona`, `Theme`, `ServerInfo`,
   `AuthPlexResponse`, `SessionInfo`, `Session`, `PushRegistration`, `PushTestResult`, `ChangesPage`,
   `ChangeEvent`, `RelayState`, `OwnerStatus`, `SearchResult` with its five answers and `SearchCommon`,
   `WantedResponse`, `WantedList`, `Playlists`, `PlaylistItems`, `RemoveResult`, `PlaylistResult`,
   `AutoPlaylistResult`, `Detail`, `Showcase`, `Suggestions`, `Nudges`, `MessageResult`, `RecheckResult`,
   `SettleResult`, `Messages`, `MessageReadResult`, `AnnounceResult`, `Genres`, `Discovery`, `GapReport`,
   `Summary`, `Library`, `NotificationCatalogue`, `Members`, `MemberPicks`), `Role`, `WantedStatus`,
   `MediaKind`, `ReleaseState`, `SQLiteDate.swift` (the three formats of 03 §1.6) and `Models.swift` holding
   `Models.decoder`. `Persona` has exactly `name`, `greeting`, `brushOffs`, `images`, `brushOffMoods`,
   `smartMode`. The `200`-with-`error` types of 03 §1.5 carry `error: String?`.
4. `Sources/PlexGetKit/ServerAPI/`: `Endpoint.swift` (the protocol, `Family`, `HTTPMethod`, `Tier`),
   `BaseURLSet.swift`, `ServerClientProtocol.swift`, `ServerClient.swift` (`actor`; local URLs at 1.5 s then
   remote; `.unreachable` only after the last URL; `.wrongServer` drops that URL; the header set of 03 §1.2
   applied once; `Accept: application/json` exactly; the bearer only when `tier != .open`; timeouts of 03 §1.9),
   `Endpoints.swift` (the only `URLRequest` builder: one static builder per route in 03 §3-§4, per plex.tv call
   in 04 §2.4 and per relay call in 05 §4.1-§4.4; none for 03 §8), `ServerError.swift` (every case of 02 §6.1,
   with `.signInRequired(code: String?)`), `ErrorMapper.swift` (`map(status:data:route:)`; `401` →
   `.signInRequired` regardless of `auth_required`; `429` with `rate_limited` or `retry_after` → `.rateLimited`;
   `200` with `error` → `.serverSaidNo` only for the 03 §1.5 routes; `500` → `.serverFault`; note: 03 §6.4 names
   that case `.serverError` and 02 §6.1 `.serverFault`; use `.serverFault` and map `api.v1.internal_error.json`
   to it, as 04 §2.2 says), `RelayClient.swift` (the second actor of 04 §2.2 with `RelayError`).
5. `Sources/PlexGetKit/PlexTV/`: `PlexHeaders`, `PlexPin`, `PlexUser`, `PlexConnection`, `PlexResource`,
   `PlexTVClient` (04 §2.4) sending the `X-Plex-*` set including `X-Plex-Platform-Version` and `Accept:
   application/json`, decoding JSON; `PlexTVError` per 02 §6.1.
6. `Sources/PlexGetKit/SignIn/`: `SignInState`, `SignInEvent`, `SignInEffect`, `SignInMachine` (04 §2.5) with
   every transition listed there, `pollInterval = 2.5`, the 120 s countdown cap, and `handoffPinStrength` set
   from the Phase 1 human result recorded in the server's Phase 1 pull request (`true` unless only short pins
   linked). Add the once-per-five-minutes silent re-exchange rule from 02 §2.1 GAP and §6.2 as a pure function
   the app can call (`SignInPolicy.shouldReExchange(code:lastAt:now:)`, this prompt's name).
7. `Sources/PlexGetKit/Pairing/`: `Invite`, `InviteParser`, `AddressClass` (`isPrivate(host:)` per 04 §2.6 plus
   the `.local` suffix rule of 09 §4, with a test), `URLRanker`, `ServerCandidate`, `Prober` (`probeLocal` builds
   `http://<address>:<port>/api/v1/server` only from `connections[].local == true`; `verify`; `firstReachable`).
8. `Sources/PlexGetKit/Changes/`: `Destination`, `ChangeRouting` (`ref` must match `^(wanted|message|note):[0-9]+$`;
   anything else routes `.home` and is never opened), `SeenRing` (capacity 200), `Changes.notifications(from:
   serverID:seen:)` skipping `test`, `nextCursor`.
9. `Sources/PlexGetKit/Themes/`: `RGB`, `Wallpaper`, `Look`, `BuiltInLooks` (the four palettes from
   `request.html:37-79`, digit for digit; `netflix` keeps its slug), `Look(theme:)`, `LookResolver`.
10. `Sources/PlexGetKit/Demo/`: `FixtureBundle.bundled()` over `Bundle.module`, `DemoState`, `actor
    DemoServerClient: ServerClientProtocol` with `init(fixtures:role:)` (04 §2.9, §12 GAP 1) answering every
    builder from the fixture whose `request.method` and `request.path` match, applying the listed mutations to
    `DemoState`, never throwing, never touching `Transport` or `SecretStore`.
11. `Tests/PlexGetKitTests/`: `Fakes/` (`FakeTransport` keyed by path and recording every URL, `FakeClock`,
    `MemorySecretStore`, `FixedEntitlements`, `RecordingNotifier`); `FixturesTests.swift` with the
    `FixtureCatalogue` (one entry per file, decode `body` with the Kit type or assert the mapper's case; one test
    that every file has an entry and one that every entry has a file; `Persona` exposes no `voice` or
    `examples`; no `Film` exposes `missing_files`); `ErrorMapperTests`, `ServerClientTests`, `EndpointsTests`,
    `PlexTVTests`, `SignInMachineTests`, `PairingTests`, `ChangesTests`, `ThemesTests` (including the WCAG
    contrast assertion of 04 §8 item 7), `DemoTests`, each proving what 04 §2.10 lists; and, by name, the Kit
    tests of 09 §11: `TransportPolicyTests.testPrivateHostsAllowPlainHTTP`, `testPublicHostRefusesPlainHTTP`,
    `testHTTPSPreferredWhenBothAnswer`; `EndpointsTests.testNoCookieNoPlexHeadersToServer`,
    `testBearerOnlyOnSignedRoutes`, `testNeverCallsForbiddenRoutes`;
    `ErrorMapperTests.testRevokedNeverReExchanges` (assert on `code == "session_revoked"`; 09 writes `reason:
    .revoked`, and 02 §6.1's `code: String?` is the shape);
    `SignInTests.testSilentReExchangeOncePerFiveMinutes`, `testAccountTokenNeverInExchangeBody`;
    `PairingTests.testProbeTargetsOnlyListedLocalAddresses`, `testWrongMachineIdDropsURL`,
    `testUninvitedServerRequiresConfirmation`; `ChangesTests.testRefParsingRejectsAnythingElse`;
    `RedactionTests.testDescriptionsHideSecrets`.
12. Run the gate. If any fixture fails to decode, stop coding Swift: read the cited server code and the fixture,
    fix the server with a pytest test in `../PlexGet` (its own pull request), re-run `pytest -q` there, re-sync,
    commit the new `VERSION`, then resume (CLAUDE.md §9). If the right fix is not obvious, write the deviation
    note and stop.
13. Update `docs/plan/04-ios-app.md` in this pull request for any signature that changed while building, and
    mirror the change into `../PlexGet/docs/ios/04-ios-app.md` in the server pull request.

## Gate

```
scripts/sync-fixtures.sh <path-to-server-repo> && git status --short PlexGetKit/Tests/Fixtures   # pass: only expected files
cd PlexGetKit && swift build -Xswiftc -warnings-as-errors    # pass: "Build complete!"
cd PlexGetKit && swift test                                   # pass: "Test run with N tests passed after ..."
scripts/lint.sh && scripts/banned-words.sh                    # pass: no output, exit 0
cat PlexGetKit/Tests/Fixtures/VERSION                         # pass: a 40-character hash matching the server's main
```

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you
cannot, write docs/build/deviations.md and stop.

## Deliver

One pull request titled `Phase 3: PlexGetKit` from `phase-3-plexgetkit`, with the seven headings of
07-build-phases.md §1.2 in order. `## Gate output` carries the full `swift test` output, the `VERSION` hash, and
the `FixturesTests` count equal to the number of files under `Tests/Fixtures/` (state both numbers). `## Changed`
ticks every row of the Phase 3 deliverables table. Confirm the five definition-of-done items of 07 §5 Phase 3 in
the body, each with the test name that proves it. `## Human steps` is "none". Then stop and wait for the owner;
do not start Phase 4.

## Do not

- Do not make a required field optional, add a default, or catch a `DecodingError` to get a fixture to decode;
  the fixture is the contract and the server is where a shape is fixed.
- Do not add a `voice` or `examples` property, a `missingFiles` property, or any type that could carry them.
- Do not build a `URLRequest` anywhere but `Endpoints.swift`, and do not add a builder for `/auth/*`,
  `/push/*`, `/wanted/export`, `/export*`, `/sonarr/*`, `/radarr/*`, `/setup*` or any other 03 §8 path.
- Do not import `Security`, `StoreKit`, `UserNotifications`, `UIKit`, `CryptoKit` or `os` in the Kit, and do not
  hash the machine identifier; it is public and used raw.
- Do not build a plain-HTTP URL to any address other than a plex.tv `local == true` connection or a private
  host that `isPrivate(host:)` accepts.
- Do not put a timer, a sleep or a `URLSession` in `SignInMachine`; it is pure and the app executes its effects.
- Do not write a file under `App/`; the app target is Phase 4.
- Do not edit a file under `Tests/Fixtures/` by hand or change `VERSION` to anything but the sync script's output.
- Do not silence a warning; `-warnings-as-errors` is the rule and a warning is a bug to fix.
