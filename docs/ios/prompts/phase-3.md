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

Environment: if `swift --version` fails (a fresh Linux session has no Swift toolchain), run every `swift`
command in this prompt through `docker run --rm -v "$PWD/PlexGetKit:/src" -w /src swift:6.0-noble <command>`
(Docker is present on the remote environment; the image tag is 07 §2's, unverified) or install the swift.org
6.0 Linux tarball, and record the wrapper or the tarball URL and the `swift --version` output in the pull
request.

1. `git checkout -b phase-3-plexgetkit main`. Refresh the plan copy first: `cp ../PlexGet/docs/ios/*.md
   docs/plan/` and commit `Phase 3: refresh docs/plan from the server`, so the amendments Phases 1 and 2 made to
   03 and 06 (the `401` codes, `tmdb_id`, the `push/test` limiter) are the ones read here; the Read first paths
   stay as they are. Run `scripts/sync-fixtures.sh ../PlexGet`; confirm
   `PlexGetKit/Tests/Fixtures/VERSION` is the 40-character hash of the server's `main` and that the directory
   holds the 77 files of 03 §6.4. Commit the fixtures and `VERSION`.
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
   `Summary`, `Library`, `NotificationCatalogue`, `Members`, `MemberPicks`, plus the wrapper shapes of 03 §2.13
   for the routes whose bodies are an object holding an array: `SessionList` (`{sessions: [Session]}`, 03 §3.5),
   `ThemeList` (`{themes: [Theme]}`, §4.1), `NoteList` (`{notes: [Note]}`, §4.13) and `WantedPage` (§4.16); the
   catalogue maps `api.v1.auth.sessions.*.json`, `themes.json`, `request.announcements.json` and
   `wanted.all.json` to those wrappers as §6.4's `Kit type` column names them), `Role`, `WantedStatus`,
   `MediaKind`, `ReleaseState`, `SQLiteDate.swift` (the three formats of 03 §1.6) and `Models.swift` holding
   `Models.decoder`. `Persona` has exactly `name`, `greeting`, `brushOffs`, `images`, `brushOffMoods`,
   `smartMode`. The `200`-with-`error` types of 03 §1.5 carry `error: String?`.
4. `Sources/PlexGetKit/ServerAPI/`: `Endpoint.swift` (the protocol, `Family`, `HTTPMethod`, `Tier`),
   `BaseURLSet.swift`, `ServerClientProtocol.swift`, `ServerClient.swift` (`actor`; local URLs at 1.5 s then
   remote; `.unreachable` only after the last URL; `.wrongServer` drops that URL; the header set of 03 §1.2
   applied once; `Accept: application/json` exactly; the bearer only when `tier != .open`; timeouts of 03 §1.9;
   the `urls` of a `GET /api/v1/server` answer replace the stored `BaseURLSet` lists only when the answer came
   over a URL already in them, 03 §1.1 and 02 §2.3 step 6, test `testUrlsAcceptedOnlyOverStoredURL`),
   `Endpoints.swift` (the only `URLRequest` builder: one static builder per route in 03 §3-§4, per plex.tv call
   in 04 §2.4 and per relay call in 05 §4.1-§4.2 (`POST /v1/devices`, `DELETE /v1/devices/<handle>`) only; the
   server-key builders of 05 §4.3-§4.4 are Phase 7's, and 07 §5 Phase 6 forbids them until then; none for 03
   §8), `ServerError.swift` (every case of 02 §6.1,
   with `.signInRequired(code: String?)`, `.undecodable(route: String, detail: String)` and
   `.unreachable(code: URLError.Code, description: String)`, so the enum is `Equatable`, 04 §2.2),
   `ErrorMapper.swift` (`map(status:data:route:)`; `401` → `.signInRequired` regardless of `auth_required`;
   `429` with `rate_limited` or `retry_after` → `.rateLimited`; `200` with `error` → `.serverSaidNo` only for the
   03 §1.5 routes; `500` → `.serverFault`, to which `api.v1.internal_error.json` maps), `RelayClient.swift` (the second actor of 04 §2.2 with `RelayError`, in this phase
   exposing only `registerDevice(apnsToken:environment:app:)` and `deleteDevice(handle:secret:)`;
   `issueServerKey` and `deleteServerKey` and their builders arrive in Phase 7).
5. `Sources/PlexGetKit/PlexTV/`: `PlexHeaders`, `PlexPin`, `PlexUser`, `PlexConnection`, `PlexResource`,
   `PlexTVClient` (04 §2.4) sending the `X-Plex-*` set including `X-Plex-Platform-Version` and `Accept:
   application/json`, decoding JSON; `PlexTVError` per 02 §6.1.
6. `Sources/PlexGetKit/SignIn/`: `SignInState`, `SignInEvent`, `SignInEffect`, `SignInMachine` (04 §2.5) with
   every transition listed there, `pollInterval = 2.5`, the 120 s countdown cap, and `handoffPinStrength` set
   from `../PlexGet/docs/build/gates.md` (`## Gate 1 (Phase 1)`, line `strong_pin_links`): `true` when `yes`,
   `false` when `no`; if the file or line is absent, stop with a deviation note naming it (a pull-request body
   is not on disk). Add the silent re-exchange rule of 02 §6.2 as a pure function the app can call
   (`SignInPolicy.shouldReExchange(code:lastAt:now:baseURL:)`, this prompt's name): true only for
   `session_expired` and `plex_token_dead`, more than 300 s after `lastAt`, and when `baseURL` is `https://`;
   false for `session_revoked` and over plain `http://` (09 §3, §11 `testNoSilentReExchangeOverPlainHTTP`).
   `SignInEvent.serverFound(_:source:)` carries the candidate's `ServerCandidate.Source`; only `.qr` reaches
   `exchanging` without `confirmed`, `.invite` (a tapped link) and every other source pass through
   `awaitingConfirmation` (04 §2.5; 02 §2.4 step 3).
7. `Sources/PlexGetKit/Pairing/`: `Invite` with `Invite.refusal(visible:storedMachineIDs:) -> InviteRefusal?`
   (`enum InviteRefusal { case notVisible, alreadyPaired }`: `m` not among the signed-in account's resources, or
   already stored, 04 §2.6 and 02 §2.4 step 3), `InviteParser`, `AddressClass` (`isPrivate(host:)` per 04 §2.6
   plus the `.local` suffix rule of 09 §4, with a test), `URLRanker`, `ServerCandidate` whose `Source` has the
   cases 04 §2.6 lists including `qr` (scanned in the app) and `invite` (a tapped link), `Prober` (`probeLocal`
   builds `http://<address>:<port>/api/v1/server` only from `connections[].local == true`; `verify`;
   `firstReachable`; an address outside the stored `BaseURLSet` is a candidate that needs confirmation before any
   bearer request, 09 §2.3a).
8. `Sources/PlexGetKit/Changes/`: `ChangeRouting` (`Destination` and `LocalNotification` are declared in
   `Protocols/Notifier.swift` since Phase 0 so that `Notifier` compiles; leave them there and do not redeclare
   them) (`ref` must match `^(wanted|message|note):[0-9]+$`;
   anything else routes `.home` and is never opened), `SeenRing` (capacity 200), `Changes.notifications(from:
   serverID:seen:)` skipping `test`, `nextCursor`.
9. `Sources/PlexGetKit/Themes/`: `RGB`, `Wallpaper`, `Look`, `BuiltInLooks` (the four palettes from
   `request.html:37-79`, digit for digit; `netflix` keeps its slug), `Look(theme:)`, `LookResolver`.
10. `Sources/PlexGetKit/Demo/`: `FixtureBundle.bundled()` over `Bundle.module` (the fixtures at
    `PlexGetKit/Tests/Fixtures/` are the `.copy("Tests/Fixtures")` resource of the `PlexGetKit` library target
    whose `path` is the package root, as Phase 0 laid it out, so `Bundle.module` in `Demo/` and in the tests is
    the library's bundle and the files are read with `subdirectory: "Fixtures"`; a resource of the test target
    alone would give the library no `Bundle.module`), `DemoState`, `actor
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
    `testBearerOnlyOnSignedRoutes`, `testNeverCallsForbiddenRoutes` (which also asserts that no `server-keys`
    builder exists yet);
    `ErrorMapperTests.testRevokedNeverReExchanges` (assert on `code == "session_revoked"`; 09 writes `reason:
    .revoked`, and 02 §6.1's `code: String?` is the shape);
    `SignInTests.testSilentReExchangeOncePerFiveMinutes`, `testAccountTokenNeverInExchangeBody`,
    `testTappedInviteRequiresConfirmation`, `testNoSilentReExchangeOverPlainHTTP`;
    `PairingTests.testProbeTargetsOnlyListedLocalAddresses`, `testWrongMachineIdDropsURL`,
    `testUninvitedServerRequiresConfirmation`, `testProbedAddressNotStoredRequiresConfirmation`,
    `testInviteForUnknownOrPairedServerIsRefused`; `ServerClientTests.testUrlsAcceptedOnlyOverStoredURL`;
    `ChangesTests.testRefParsingRejectsAnythingElse`; `RedactionTests.testDescriptionsHideSecrets`. `EndpointsTests`
    covers the routes of 03 §3-§4, the plex.tv calls and the two relay device calls; the relay server-key builders
    are asserted absent here and present from Phase 7.
12. Run the gate. If any fixture fails to decode, stop coding Swift: read the cited server code and the fixture,
    fix the server with a pytest test in `../PlexGet` (its own pull request), re-run `pytest -q` there, re-sync,
    commit the new `VERSION`, then resume (CLAUDE.md §9). If the right fix is not obvious, write the deviation
    note and stop.
13. Update `docs/plan/04-ios-app.md` in this pull request for any signature that changed while building, and
    mirror the change into `../PlexGet/docs/ios/04-ios-app.md` in the server pull request.

## Gate

```
scripts/sync-fixtures.sh ../PlexGet && git status --short PlexGetKit/Tests/Fixtures   # pass: only expected files
cd PlexGetKit && swift build -Xswiftc -warnings-as-errors    # pass: "Build complete!" (through the Docker wrapper above when there is no local toolchain)
cd PlexGetKit && swift test                                   # pass: "Test run with N tests passed after ..."
scripts/lint.sh && scripts/banned-words.sh                    # pass: no output, exit 0
cat PlexGetKit/Tests/Fixtures/VERSION                         # pass: a 40-character hash matching the server's main
```

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you
cannot, write docs/build/deviations.md and stop.

## Deliver

One pull request titled `Phase 3: PlexGetKit` from `phase-3-plexgetkit`, with the seven headings of
07-build-phases.md §1.2 in order. `## Gate output` carries the full `swift test` output, the `VERSION` hash, and
the `FixturesTests` count equal to the number of files under `Tests/Fixtures/` (state both
numbers). `## Changed`
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
- Do not edit a file under `Tests/Fixtures/` by hand or change `VERSION` to anything but the sync
  script's output.
- Do not silence a warning; `-warnings-as-errors` is the rule and a warning is a bug to fix.
