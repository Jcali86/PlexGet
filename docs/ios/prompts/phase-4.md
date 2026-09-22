# Phase 4: App: welcome to results

Machine: Mac with Xcode 16 and `xcodegen`. Repository: `<AppName>-ios` (private), Phase 3 merged; the server
repository at `../PlexGet` with Phase 2 merged, and a running Phase 2 server shared with a test Plex account. In a
Linux session the code can be written but no gate can run: push the branch, label the pull request `mac-ci`,
list every Mac command under `## Not run here`, and wait for the human.

## Read first

Read each file in full, in this order, before any other tool call.

1. `CLAUDE.md` (all of it; §4, §5 and §6 are the rules this phase most often breaks)
2. `docs/plan/00-README.md` §3-§4
3. `docs/plan/10-decision-record.md` §2 (the app target paragraph), §4 rows "LAN probe and trust", "Feature
   flag", "Notification permission", §5 items 3, 4 and 10, §6 items 6 and 9
4. `docs/plan/07-build-phases.md` §4.3 (`app.yml`), §5 "Phase 4, App: welcome to results"
5. `docs/plan/04-ios-app.md` §1, §3.1-§3.2, §3.6-§3.8, §4 (Welcome, Pairing, Home, Results entries), §5 (persona
   rendering), §6 (looks), §7 (demo mode), §8, §9, §10, §11 (the UI tests), §12
6. `docs/plan/02-architecture.md` §2.1-§2.6, §2.16, §4 (Keychain and `UserDefaults` tables), §5, §6.2-§6.4
7. `docs/plan/09-security.md` §3, §4 (ATS keys exactly), §11 (the App tests by name)
8. `docs/plan/03-server-api.md` §1.1 (URL set rules), §1.8 (`ServerInfo` conditions), §4.2, §4.3, §4.5, §4.8,
   §4.11-§4.12, §4.15, §4.28
9. `project.yml`, `App/AppIdentity.swift`, `App/AppMain.swift`, and under `PlexGetKit/Sources/PlexGetKit/` the
   `SignIn/`, `Pairing/` and `Demo/` folders

## Goal

Build the app target from launch to a rendered search result: `AppMain`, `Session` (`SessionStore`, `Router`,
`URLSessionTransport`), `Keychain`, Welcome (sign in, invite, demo), Pairing with the LAN probe and the
confirmation step, Home (search bar, persona greeting, showcase reels, hero), Results (the five `kind` outcomes,
sort chips, selection bar), and the demo-mode UI smoke tests, against `DemoServerClient` and a Phase 2 server
(07-build-phases.md §5 Phase 4).

## Constraints

- Forbidden in this phase (07-build-phases.md §5 Phase 4): any Console screen; `StoreKit`; `PushBridge`,
  `Background`, `UserNotifications` (Phase 6); an endpoint or field not in 03-server-api.md; `URLRequest(` in
  `App/`; a `UserDefaults` write of a token; a banned word; hand-editing `.pbxproj`; a store `Task` without
  cancellation.
- `URLSession` exists only in `App/Session/URLSessionTransport.swift`; every request comes from `Endpoints.swift`.
- Every secret goes through `SecretStore` into the Keychain with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`,
  service `AppIdentity.keychainService`, no synchronisation, no access group; never in `UserDefaults`, a file or a
  log (CLAUDE.md §5). Every `UserDefaults` key is declared once in `App/Settings/DefaultsKeys.swift` (02 §4.2).
- Every user-facing string lives in `Localizable.xcstrings` (en-GB) with a translator comment; server text
  (`app_name`, persona, `interpretation`, error `message` and `hint`) is shown verbatim (CLAUDE.md §6).
- ATS is exactly `{ NSAllowsLocalNetworking: true }`; no server-trust delegate (09 §4).
- Plain `http://` only to a host `AddressClass.isPrivate(host:)` accepts; the probe targets only plex.tv's
  `local == true` addresses (10-decision-record.md §6 item 6).

## Steps

1. `git checkout -b phase-4-app-welcome-to-results main`. Refresh the plan copy: `cp ../PlexGet/docs/ios/*.md
   docs/plan/` and commit the refresh, so the `docs/plan/` paths in Read first carry the Phase 1-2 amendments
   (03 §1.4 `401` codes, `tmdb_id`, the `push/test` limiter) and read the same contract Phases 5-9 read from
   `../PlexGet/docs/ios/`. Then `xcodegen generate`; confirm the Phase 0 smoke tests
   pass on the simulator before adding code.
2. `project.yml`: set `NSLocalNetworkUsageDescription` to "Finds your household's server on this Wi-Fi using the
   addresses Plex already knows." (the sentence of 04-ios-app.md §1); `NSAppTransportSecurity` with only
   `NSAllowsLocalNetworking: true`; `CFBundleURLTypes` with `AppIdentity.urlScheme` for the `plex-auth-done`
   callback; `BGTaskSchedulerPermittedIdentifiers` with `AppIdentity.refreshTaskIdentifier` (declared now, used in
   Phase 6, so `testLocalNetworkUsageDescriptionPresent` passes); `NSCameraUsageDescription` set to "Scans the
   invite QR code the owner shares." (the scanner in step 7 opens the camera; without this key iOS terminates the
   app).
3. `App/Session/URLSessionTransport.swift`: the only `URLSession` in the repository, conforming to `Transport`,
   `waitsForConnectivity` false, per-request timeouts from the endpoint (02 §5 item 2), no delegate that answers a
   server-trust challenge. `App/Keychain/Keychain.swift`: `final class Keychain: SecretStore` serialised on a
   private actor as 04 §3.2 specifies, accounts per 02 §4.1, a missing item reads as `nil`,
   `errSecInteractionNotAllowed` retried once after 250 ms.
4. `App/Settings/DefaultsKeys.swift` (only this file under `Settings/` in this phase): every key of 02 §4.2 and
   the 04 §4 additions these screens use (`results.sort`, `playlist.<id>.last`, `home.<id>.nudgedOn`,
   `home.<id>.shut.<card>`).
5. `App/Session/SessionStore.swift`: `@MainActor @Observable final class SessionStore` with `phase`, `client`,
   `server`, `user`, `persona`, `themes`, `look`, `delivery` (04 §3.6); launch (read `server.current`, load the
   02 §4.3 caches, verify the stored URL set through `Prober.firstReachable`, `GET /api/v1/auth/session`, `GET
   /persona`, `GET /themes`); the `ServerInfo` conditions of 02 §6.3 (`setup`, `native_signin == false`,
   `min_app_version` → the "Update needed" screen); the `.signInRequired` policy of 02 §6.2 through the Kit's
   `SignInPolicy.shouldReExchange(code:lastAt:now:baseURL:)` and `signIn.lastSilentExchangeAt` (never over a
   plain `http://` base URL, never on `session_revoked`); sign-out per 02 §2.16 steps 1-2 (the push-device and
   relay deletes are no-ops until Phase 6); demo entry per 04 §7 (`DemoServerClient(fixtures: .bundled(), role:
   .owner)`, or `.shared` under `--demo-role shared`). `App/Session/Router.swift`: `selectedTab`, one
   `NavigationPath` per tab, `presentedSheet`, `open(_ destination:)`, `onOpenURL` for `/join` universal links
   (handed to Pairing as `source: .invite`) and for a `plex-auth-done` URL opened from outside the session; the
   ordinary `plex-auth-done` callback arrives in the `ASWebAuthenticationSession` completion handler (04 §3.6-§3.7; the tab order of §3.7; Console tab visibility through a `ConsoleGate`
   stub that is never visible outside demo in this phase).
6. `App/Welcome/`: `WelcomeStore` driving `SignInMachine` and executing its effects: `createBrowserPin`
   (`strong: true`), `openAuthURL` in `ASWebAuthenticationSession` with the typed alternative under it,
   `pollPin` every 2.5 s and once on foreground, `readUser` and `resources`, `runPairing`, `askConfirmation`,
   `linkAndExchange` (the second pin, `linkPin`, then `POST /api/v1/auth/plex` with `{"pin", "device"}`;
   `plex_token` never in the body); the three doors; the pin card; the states and errors of 04 §4.1 Welcome and
   02 §6.4. After a successful exchange store `session.<machine_id>` and `plex.account-token` in the Keychain,
   `server.current` and the `server.<id>.*` keys in `UserDefaults` (including `server.<id>.email` and `.thumb`
   from the exchange's `user`, 02 §4.2; never the Keychain), then load persona, themes and the session.
   The notification permission prompt of 02 §2.1 step 14 is Phase 6; leave the call site marked.
7. `App/Pairing/`: `PairingStore` and views: the QR scanner (VisionKit `DataScannerViewController`, QR only;
   guard the scanner door with `DataScannerViewController.isSupported && isAvailable`, so on the simulator only
   "Paste the link" and "Type an address" are offered), "Paste the link", "Type an address" (a public `http://` address refused before any request), the LAN probe
   through `Prober.probeLocal` over the plex.tv resources, and the confirmation card "Connect to <server_name>
   at <address>?" shown before the first exchange with any server not reached through an invite (02 §2.3 step 5),
   also for a probed or typed address that is not already in the stored URL set (09 §2.3a). An invite is refused
   before any request when `Invite.refusal` answers `.notVisible` ("That invite is for a server your Plex account
   cannot see") or `.alreadyPaired` ("You are already connected to this server"); a QR scanned in the app
   (`Source.qr`) marks the server invited and exchanges without the card; a tapped or pasted link
   (`Source.invite`) shows "Someone sent you a link to <server_name> at <address>. Connect?" first (02 §2.4 step
   3; 04 §4.1 Pairing). Write `server.<id>.invited`, `.confirmed`, `.name`, `.urls.local`, `.urls.remote` and `.homeNetwork` (default
   on for owners and for anyone whose first probe succeeded, off otherwise; the toggle itself is Phase 5).
8. `App/Home/`: `HomeStore` and views per 04 §4.1 Home: search field, the greeting line and `greeting` face
   (04 §5), the doors (owner-only door hidden for `shared`), the hero and showcase reels from `GET
   /request/showcase`, suggestions and nudges under their rules, the quick add through `POST /request/playlist`,
   the caches of 02 §4.3, and the empty and offline states.
9. `App/Results/`: `ResultsStore` and views: `POST /request/search` with the thinking block, one view per
   `SearchResult` case (title, cheeky, mood, missingOnly, titleMissing) with the brush-off card of 04 §5, sort
   chips over two or more films, multi-select cards, the selection bar with "for whom" for the owner (`GET
   /members`), "Ask for this one" through `POST /request/wanted` with `tmdb_id` when the ask came from a guess,
   the `.rateLimited` handling with `hint` as the primary line, and the dropped-connection copy of 02 §6.4.
10. `App/Shared/`: cards, chips, `PersonaFace(mood:size:)` per 04 §5, the artwork loader through
    `client.artworkURL(path:width:height:)` and one shared `URLCache` (50 MB), `LookEnvironment.swift` applying
    the twelve keys of the current `Look` (04 §6), `Identifiers.swift` with every `accessibilityIdentifier` the UI
    tests use, and the demo banner "Demo — nothing here is real" with "Leave the demo".
11. `App/Resources/Localizable.xcstrings`: every string of these screens, plus "Update needed" and "This server
    is still being set up"; run `scripts/banned-words.sh` before committing.
12. `AppTests/KeychainTests.swift` (write, read, overwrite, delete, missing, and by name
    `testAccessibilityIsAfterFirstUnlockThisDeviceOnly`, `testNotSynchronizable`), `AppTests/RouterTests.swift`
    (deep-link routing of each `Destination`), `AppTests/ATSTests.swift` (`testOnlyLocalNetworkingKey`) and
    `AppTests/InfoPlistTests.swift` (`testLocalNetworkUsageDescriptionPresent` with the assertions of 04 §11:
    `NSLocalNetworkUsageDescription`, `NSCameraUsageDescription` and a `BGTaskSchedulerPermittedIdentifiers` entry
    equal to `AppIdentity.refreshTaskIdentifier`; it reads `Bundle.main.infoDictionary`, which is the app's only
    in a unit test hosted by the App target; in a UI test target `Bundle.main` is the `AppUITests-Runner` bundle
    and the key is absent, so it lives in `AppTests/`, not in `AppUITests/`).
13. `AppUITests/`: `testWelcomeShowsThreeDoors`, `testHomeGreetingAndReels`, `testSearchTitleRendersFilmCards`,
    `testSearchCheekyShowsBrushOff`, `testSearchTitleMissingShowsGuesses`, `testSelectionBarAddsToPlaylist`,
    `testMemberRoleHidesConsole`, `testDynamicTypeAccessibility3` (Home and Results only in this phase),
    `testNoBannedWordsInAccessibilityTree`, all launched with `--demo --uitest` except
    `testWelcomeShowsThreeDoors`, which launches with `--uitest` alone (04 §11: `--demo` enters demo mode and
    skips Welcome) and taps the demo door itself. `testDetailSheet` is Phase 5's (04 §11).
14. Run the gate on the Mac; label the pull request `mac-ci`; wait for `app.yml` to go green.
15. HUMAN (10-decision-record.md §5 item 10): in the simulator, complete `ASWebAuthenticationSession` against a
    real plex.tv pin with the test account and record the result. HUMAN (§6 item 9): on a physical iPhone, sign
    in with a Plex account the test server is shared with, at home through the LAN probe with no invite
    (confirm the server-name prompt and the Local Network prompt showing the string), reach a search result;
    repeat away from home with an invite; record both with screenshots. Wait for both.

## Gate

```
xcodegen generate
xcodebuild test -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' -derivedDataPath build CODE_SIGNING_ALLOWED=NO
                                                          # pass: "** TEST SUCCEEDED **"
plutil -extract NSLocalNetworkUsageDescription raw -o - build/Build/Products/Debug-iphonesimulator/App.app/Info.plist
                                                          # pass: prints the sentence
scripts/lint.sh && scripts/banned-words.sh                # pass: exit 0
grep -rnw "URLSession" App PlexGetKit | grep -v "App/Session/URLSessionTransport.swift"   # pass: no output (whole-word match; the type name URLSessionTransport at its call site is not a hit)
```

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you
cannot, write docs/build/deviations.md and stop.

## Deliver

One pull request titled `Phase 4: App: welcome to results` from `phase-4-app-welcome-to-results`, labelled
`mac-ci`, with the seven headings of 07-build-phases.md §1.2 in order. `## Gate output` carries the full
`xcodebuild test` output (or the `app.yml` log artefact, verbatim), the `plutil` line and the two greps;
`## Not run here` the Mac commands when the session was Linux; `## Human steps` the simulator sign-in and the
two device sign-ins with screenshots, or "pending". Confirm the four definition-of-done items of 07 §5 Phase 4,
naming the test or screenshot that proves each. Then stop and wait for the owner; do not start Phase 5.

## Do not

- Do not write a token, pin, handle or account token to `UserDefaults`, a cache file, a log line or a
  `description`; the Keychain through `SecretStore` is the only place.
- Do not probe any address plex.tv did not list as a `local` connection for that machine identifier; never scan.
- Do not skip the confirmation card for a server found by the probe, typed, or reached by a tapped or pasted
  invite link; only a QR scanned in the app exchanges without asking.
- Do not send `plex_token` in the exchange body; the hand-off sends `pin` and `device`, and the gate-1 fallback
  is the owner's decision, not this phase's.
- Do not add `NSAllowsArbitraryLoads`, `NSExceptionDomains` or a certificate-accepting delegate to make a test
  server work; use a private address or a real certificate.
- Do not call the client from a view, build a `URLRequest` in `App/`, or start a store `Task` that is not
  cancelled before the next and checked with `Task.checkCancellation()` after each await.
- Do not claim the simulator or device steps done from a Linux session; record them as pending and wait.
