# 04 iOS app: the Kit and the app target

This document is the builder's map of the iOS repository `<AppName>-ios`: where every file lives, what each
`PlexGetKit` module exposes (public Swift signatures and what its tests prove), what the app target's platform glue
does, how the tabs are arranged, and, for every screen in the ADR's feature list, its purpose, the endpoints it calls,
the state it reads and writes, the errors it must handle and its empty, loading and offline states. It also fixes how
persona text and artwork are drawn, how the four built-in looks and owner-made looks become SwiftUI colours, what demo
mode is and is not, the accessibility, localisation and Swift 6 rules, and the UI smoke tests. Names are the ADR's
names verbatim (10-decision-record.md §2); endpoint shapes live in 03-server-api.md and are referenced, not restated;
the error taxonomy and the `UserDefaults`/Keychain key tables live in 02-architecture.md §4 and §6 and are referenced
by section. Anything the ADR or design-A does not define is marked `GAP:`.

Read with: 10-decision-record.md (binding), 02-architecture.md (sequences, state, errors), 03-server-api.md (every
shape and fixture name), 05-push-relay.md (the relay calls the app makes), 07-build-phases.md (Phases 0, 3, 4, 5, 6, 7
build what is here), 09-security.md (Keychain and transport rules), ios-CLAUDE.md (the standing rules restated for
every session).

## 1. Repository layout
```
<AppName>-ios/
  project.yml                      XcodeGen spec: one app target "App", one test target "AppTests",
                                   one UI test target "AppUITests"; PlexGetKit as a local package
  CLAUDE.md                        the standing rules (ios-CLAUDE.md in this plan)
  scripts/sync-fixtures.sh         copies <server-repo>/tests/fixtures/api into PlexGetKit/Tests/Fixtures
  scripts/banned-words.sh          greps App/, PlexGetKit/ and fixtures for the words in §10.3; CI gate
  .github/workflows/kit.yml        Linux: swift build, swift test, banned-words.sh
  .github/workflows/app.yml        macOS: xcodegen generate, xcodebuild test (push to main, PR label)
  PlexGetKit/
    Package.swift                  swift-tools-version 6.0, swiftLanguageModes [.v6], platforms [.iOS(.v17)]
    Sources/PlexGetKit/            Protocols/ ServerAPI/ Models/ PlexTV/ SignIn/ Pairing/ Changes/ Themes/ Demo/
                                   (one folder per §2 module; file names are the type names in §2)
    Tests/PlexGetKitTests/         one test file per module plus FixturesTests.swift and Fakes/ (03 §6.3)
    Tests/Fixtures/                vendored tests/fixtures/api/*.json and VERSION (03 §6.3)
  App/
    AppMain.swift                  @main App; installs PushBridge, Background, Store, Router
    AppIdentity.swift              §3.1
    Keychain/ Store/ PushBridge/ Background/            §3.2-§3.5 (Store/ also holds Products.storekit)
    Session/                       SessionStore.swift, Router.swift, URLSessionTransport.swift, UserNotificationsNotifier.swift
    Welcome/ Pairing/ Home/ Results/ Detail/ Playlists/ WantedList/ Bell/ Looks/ Report/ Help/ Settings/
    Console/                       ConsoleTab.swift, ConsoleGate.swift, then Requests/ Inbox/ Announce/ Discover/
                                   Overview/ Notifications/ Server/ Sessions/ Paywall/
    Shared/                        Cards, chips, persona views, artwork loader, LookEnvironment.swift, Identifiers.swift
    Resources/                     Localizable.xcstrings (en-GB base), Assets.xcassets, PrivacyInfo.xcprivacy
  AppTests/  AppUITests/           XCTest for the glue (Router, Keychain on the simulator); §11
  docs/build/phase-0.md … phase-9.md   the per-phase prompts (prompts/phase-N.md in this plan)
```
`project.yml` is the only project description; `.xcodeproj` is generated, gitignored and never hand-edited (brief fact
13). Info.plist keys set from `project.yml`: `NSLocalNetworkUsageDescription` ("Finds your household's server on
this Wi-Fi using the addresses Plex already knows."), `NSCameraUsageDescription` ("Scans the invite QR code the owner
shares."; the Pairing scanner, §4.1), `NSAllowsLocalNetworking` under `NSAppTransportSecurity`,
`BGTaskSchedulerPermittedIdentifiers` (`AppIdentity.refreshTaskIdentifier`), `UIBackgroundModes` (`fetch` only;
`remote-notification` is not declared because the relay sends alert pushes without `content-available`, 05 §7.4),
`CFBundleURLTypes` (`AppIdentity.urlScheme`), `CFBundleDisplayName` (`AppIdentity.displayName`). Entitlements file: `aps-environment`,
`com.apple.developer.associated-domains` (`applinks:AppIdentity.inviteHost`). Both are the human's capabilities work
in Phase 9; the files exist from Phase 0 so the simulator build is unchanged later.

## 2. PlexGetKit

Imports only `Foundation` (and `FoundationNetworking` behind `#if canImport(FoundationNetworking)`). Never imports
`Security`, `StoreKit`, `UserNotifications`, `UIKit` or `CryptoKit` (10-decision-record.md §2). Every public type is
`Sendable`; every model is a `struct` or `enum`. `swift build` and `swift test` pass on Linux (Phase 3 gate).

### 2.1 The four protocols (`Protocols/`)

Implemented only in the app target; the Kit ships no default implementation.
```swift
public protocol Transport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}
public protocol SecretStore: Sendable {
    func read(_ account: String) async throws -> String?
    func write(_ value: String, account: String) async throws
    func delete(_ account: String) async throws
}
public protocol Entitlements: Sendable {
    var isOwnerActive: Bool { get async }
    func serverKeyJWS() async -> String?          // the newest verified transaction's jwsRepresentation, expired or not (§3.3)
}
public enum NotificationAuthorisation: Sendable { case notAsked, granted, denied }
public protocol Notifier: Sendable {
    func authorisationState() async -> NotificationAuthorisation
    func requestAuthorisation() async -> Bool
    func post(_ notification: LocalNotification) async
    func clearDelivered(threadID: String) async
}
```
Tests inject `FakeTransport` (answers keyed by path), `MemorySecretStore`, `FixedEntitlements` and `RecordingNotifier`
from `Tests/PlexGetKitTests/Fakes/`.

### 2.2 ServerAPI
```swift
public protocol Endpoint: Sendable {
    associatedtype Response: Decodable & Sendable
    var family: Family { get }                 // .server | .plexTV | .relay
    var method: HTTPMethod { get }             // .get | .post | .put | .delete
    var path: String { get }
    var query: [URLQueryItem] { get }
    var body: (any Encodable & Sendable)? { get }
    var tier: Tier { get }                     // .open | .household | .owner
    var timeout: TimeInterval { get }          // 15, 60 or 1.5 per 03-server-api.md §1.9
}
public struct BaseURLSet: Sendable, Equatable { public var local: [URL]; public var remote: [URL]; public var pinnedMachineID: String }
public protocol ServerClientProtocol: Sendable {
    var isDemo: Bool { get }
    func perform<E: Endpoint>(_ endpoint: E) async throws -> E.Response
    func setBearer(_ token: String?) async
    func setBaseURLs(_ urls: BaseURLSet) async
    func currentBaseURL() async -> URL?
    func artworkURL(path: String, width: Int, height: Int) async -> URL?   // GET /wanted/thumb on the current base URL
}

public actor ServerClient: ServerClientProtocol {
    public init(transport: any Transport, userAgent: String, baseURLs: BaseURLSet, bearer: String?)
    // perform: builds the request through Endpoints, applies the header set (03 §1.2), tries local URLs
    // then remote URLs on `.unreachable`, runs ErrorMapper, decodes Response with the Kit's JSONDecoder.
}
```
`Endpoints.swift` is the only file in either product that constructs a `URLRequest`; it holds one static builder per
route in 03-server-api.md §3 and §4, one per plex.tv call in §2.4 below, and one per relay call in 05-push-relay.md,
as `enum Endpoints { static func server() -> ServerEndpoint<ServerInfo> … }`. A route in 03-server-api.md §8 has no
builder, and `scripts/lint.sh` greps Swift sources (never `App/Resources/` or the fixtures) for these exact literals
from 03 §8: `/auth/start`, `/auth/poll`,
`/auth/me`, `/auth/logout`, `handoff=`, `/push/key`, `/push/subscribe`, `/push/unsubscribe`, `"/push/test"`,
`/wanted/export`, `"/export"`, `/export/download`, `/sonarr/`, `/radarr/`, `/scan/`, `/analyze`, `"/quality"`,
`"/media"`, `/reports/upgrades`, `/reports/posters`, `/fixer/`, `/posters/`, `"/command"`, `/search/tmdb`, `"/gaps"`,
`/plex/webhook`, `/setup`, `/sw.js`, `/manifest.webmanifest`, `/dashboard` (never `/`, `/request` or `/health`, which
are prefixes of permitted routes). Every server-family request carries `Accept:
application/json`, `Content-Type: application/json; charset=utf-8` when it has a body, `User-Agent`, and
`Authorization: Bearer` when `tier != .open` (03 §1.2). No `X-Plex-*` header ever goes to a PlexGet server. Phase 3
writes every server, plex.tv and relay-device builder; the two relay server-key builders (`POST`/`DELETE
/v1/server-keys`, 05 §4.3-§4.4) are Phase 7's, and `EndpointsTests` asserts them absent until then.

`ErrorMapper.map(status:data:route:) -> ServerError?` produces exactly the cases tabled in 02-architecture.md §6.1
(`401` → `.signInRequired` regardless of `auth_required`, household-api.md §1; `200` with `error` → `.serverSaidNo`
only for the routes in household-api.md §6.7). `ServerError` is `enum ServerError: Error, Sendable, Equatable`; its
payloads are value types that are `Equatable`: `.undecodable(route: String, detail: String)` carries
`String(describing:)` of the `DecodingError`, and `.unreachable(code: URLError.Code, description: String)` carries the
code and `localizedDescription` rather than the `URLError` itself (02 §6.1 tables the same two cases).
`api.v1.internal_error.json` maps to `.serverFault` (03 §6.4).

`RelayClient` is a second actor in the module, taking a `Transport` and the relay base URL, with
`registerDevice(apnsToken:environment:app:) -> DeviceRegistration`, `deleteDevice(handle:secret:)`,
`issueServerKey(jws:) -> ServerKey` and `deleteServerKey(id:jws:)` (05 §4.4 requires the JWS for the same
`originalTransactionId`), throwing `RelayError` (02 §6.1); shapes per 05-push-relay.md. `registerDevice` and
`deleteDevice` are Phase 3; `issueServerKey` and `deleteServerKey` with their builders are Phase 7 (07 §5).

### 2.3 Models

One `struct` or `enum` per Kit type named in 03-server-api.md §2 and §6.4 (`Film` merges shapes 2a and 2b with every
shape-specific field optional; `Show`, `FilmGuess`, `ShowGuess`, `WantedRow`, `Persona`, `Theme`, `ServerInfo`,
`AuthPlexResponse`, `SessionInfo`, `ChangesPage`, `ChangeEvent`, `SearchResult`, the wrapper shapes `SessionList`,
`ThemeList`, `NoteList` and `WantedPage` of 03 §2.13, and the rest of that table's third column), each in its own
file.
```swift
public enum SearchResult: Decodable, Sendable {      // keyed on "kind" (household-api.md §3.1)
    case title(TitleAnswer), cheeky(CheekyAnswer), mood(MoodAnswer),
         missingOnly(MissingOnlyAnswer), titleMissing(TitleMissingAnswer)
    public var common: SearchCommon { get }         // request, translatedBy, interpretation, playlistName, smartMode, capturedMissing
}
public enum Role: String, Codable, Sendable { case owner, shared }   // likewise WantedStatus, MediaKind, ReleaseState
public struct SQLiteDate: Codable, Sendable, Equatable { public let date: Date }   // "YYYY-MM-DD HH:MM:SS" UTC, ISO 8601 "Z", or Unix Int
```
Decoding rules: `keyDecodingStrategy` is `.convertFromSnakeCase` set once in `Models.decoder`; unknown keys are
ignored; a field 03-server-api.md lists as required is non-optional and its absence fails decoding (ADR §6 item 8:
fixed by a server test, never by a lenient guess); `missing_files` has no property at all, so it can never be
displayed. `Persona` has exactly `name`, `greeting`, `brushOffs`, `images`, `brushOffMoods`, `smartMode`
(persona-themes-limits.md §1.3, §8 item 1); there is no property for `voice` or `examples` and the fixtures test
asserts the decoded struct has no such key path.

### 2.4 PlexTV
```swift
public struct PlexHeaders: Sendable {
    public var product: String            // AppIdentity.plexProduct
    public var clientIdentifier: String   // Keychain plex.client-identifier
    public var version: String            // CFBundleShortVersionString
    public var platform: String = "iOS"
    public var platformVersion: String    // UIDevice.current.systemVersion, passed in by the app (X-Plex-Platform-Version, ADR §1)
    public var device: String             // "iPhone" | "iPad"
    public var deviceName: String
}
public struct PlexPin: Sendable, Equatable { let id: Int; let code: String; let expiresAt: Date; let authToken: String? }
public struct PlexUser: Sendable, Equatable { let id: String; let username: String; let thumb: URL? }
public struct PlexConnection: Sendable, Equatable { let uri: URL; let address: String; let port: Int; let local: Bool; let relay: Bool }
public struct PlexResource: Sendable, Equatable { let clientIdentifier: String; let name: String; let owned: Bool; let connections: [PlexConnection] }

public actor PlexTVClient {
    public init(transport: any Transport, headers: PlexHeaders)
    public func createPin(strong: Bool) async throws -> PlexPin              // POST /api/v2/pins?strong=
    public func linkPin(code: String, accountToken: String) async throws    // PUT /api/v2/pins/link (ADR §4 "Sign-in exchange")
        // body `code=<code>` as `application/x-www-form-urlencoded`, header `X-Plex-Token: <accountToken>` plus the
        // usual `X-Plex-*` set; any 2xx with an empty body is success, nothing is decoded (plexapi/myplex.py:1144-1155).
        // Whether plex.tv requires `X-Plex-Product: Plex SSO` on this call, as plexapi sends, is part of the gate-1 human check.
    public func readPin(id: Int) async throws -> PlexPin                    // GET /api/v2/pins/{id}
    public func user(accountToken: String) async throws -> PlexUser         // GET /api/v2/user
    public func resources(accountToken: String) async throws -> [PlexResource]   // GET /api/v2/resources?includeHttps=1&includeRelay=0
    public nonisolated func authURL(pin: PlexPin, forwardURL: URL) -> URL   // https://app.plex.tv/auth#?clientID=…&code=…&context[device][product]=…&forwardUrl=…
}
```
The other four calls send `Accept: application/json` and decode JSON (ADR §5 item 22; brief fact 4). Errors are
`PlexTVError` (02 §6.1). Whether `strong: true` pins link (ADR §6 item 1) is a Phase 1 human gate; the Welcome store
passes `strong: true` for the browser pin and reads `SignInMachine.handoffPinStrength` for the second pin so the
gate's answer is one constant.

### 2.5 SignIn

A pure state machine; no I/O, no timers. The app drives it and executes the effects it returns.
```swift
public enum SignInState: Sendable, Equatable {
    case idle
    case pinIssued(PlexPin)
    case waitingForPlex(PlexPin, nextPollAt: Date)
    case awaitingServer(accountToken: String)                 // Pairing runs
    case awaitingConfirmation(ServerCandidate)                // un-invited server (ADR §4 "LAN probe and trust") or a tapped invite link
    case exchanging(ServerCandidate)
    case signedIn(AuthPlexResponse)
    case denied(SignInDenial)                                 // .notShared, .invitePending, .nativeSignInOff, .setupMode(URL), .updateNeeded(min: String)
    case rateLimited(until: Date)
    case failed(SignInFailure)                                // .plexTV(PlexTVError), .server(ServerError)
}
public enum SignInEvent: Sendable {
    case start, pinCreated(PlexPin), tick(Date), pinRead(PlexPin), pinExpired
    case serverFound(ServerCandidate, source: ServerCandidate.Source), noServerFound, confirmed, cancelled
    case exchangeSucceeded(AuthPlexResponse), exchangeFailed(ServerError), plexTVFailed(PlexTVError)
}
public enum SignInEffect: Sendable, Equatable {
    case createBrowserPin, openAuthURL(PlexPin), pollPin(id: Int, at: Date), readUser, runPairing
    case askConfirmation(ServerCandidate), linkAndExchange(ServerCandidate), showCountdown(until: Date), none
}
public struct SignInMachine: Sendable {
    public static let pollInterval: TimeInterval = 2.5
    public static let handoffPinStrength: Bool = true       // Phase 1 gate may set false (ADR §6 item 1)
    public init(now: @Sendable () -> Date)
    public private(set) var state: SignInState
    public mutating func apply(_ event: SignInEvent) -> SignInEffect
}
```
Transitions: `idle --start--> pinIssued` (effect `.createBrowserPin`), `pinIssued --pinCreated--> waitingForPlex`
(`.openAuthURL`), `waitingForPlex --tick--> waitingForPlex` (`.pollPin` when `now >= nextPollAt`),
`--pinRead(authToken != nil)--> awaitingServer` (`.readUser` then `.runPairing`), `--pinExpired--> pinIssued` (new
pin, no message), `awaitingServer --serverFound(source: .qr)--> exchanging` (`.linkAndExchange`; a QR scanned in the
app is the one source that skips confirmation), `--serverFound(source: .invite | .plexLocal | .typed)-->
awaitingConfirmation` (`.askConfirmation`; a tapped link is a phishing surface, so it confirms like a probe),
`awaitingConfirmation --confirmed-->
exchanging`, `exchanging --exchangeSucceeded--> signedIn`, `--exchangeFailed(.notShared | .invitePending)--> denied`,
`--exchangeFailed(.rateLimited(retryAfter)) --> rateLimited(until: now + min(retryAfter, 120))` (auth-and-tiers.md
§1.6 cap, as the page does), `rateLimited --tick(past until)--> awaitingServer` (retry the exchange without a new
browser pin), anything else `--exchangeFailed--> failed`, `--cancelled--> idle`. `ServerInfo` conditions (`setup`,
`native_signin == false`, `min_app_version`) are checked by the Welcome store before `serverFound` and delivered as
`denied` states, never as network errors (02 §6.3).

### 2.6 Pairing
```swift
public struct Invite: Sendable, Equatable { public let version: Int; public let machineID: String; public let local: URL?; public let remote: URL?; public let name: String }
public enum InviteParser {
    public static func parse(_ url: URL) throws(InviteError) -> Invite   // https://<AppIdentity.inviteHost>/join#v=1&m=&l=&r=&n=; same fragment from a QR
}
public enum AddressClass {
    public static func isPrivate(host: String) -> Bool   // RFC 1918, 169.254/16, 127/8, ::1, fc00::/7, fe80::/10; plus hostnames ending `.local` (09 §4)
}
public enum URLRanker {
    public static func rank(_ urls: [URL]) -> [URL]      // https first; then http to a private host; http to anything else is dropped
}
public struct ServerCandidate: Sendable, Equatable {
    public enum Source: Sendable, Equatable { case qr, invite, plexLocal, typed, stored }   // qr: scanned in the app; invite: a tapped link
    public let url: URL; public let source: Source; public let info: ServerInfo
}
public enum InviteRefusal: Sendable, Equatable { case notVisible, alreadyPaired }
public extension Invite {
    func refusal(visible: [PlexResource], storedMachineIDs: Set<String>) -> InviteRefusal?
        // .notVisible when no resource's clientIdentifier == machineID; .alreadyPaired when machineID is stored; else nil
}
public struct Prober: Sendable {
    public init(transport: any Transport, port: Int = 5050, timeout: TimeInterval = 1.5)
    public func probeLocal(_ resource: PlexResource) async -> ServerCandidate?
        // http://<connection.address>:<port>/api/v1/server for each connection with local == true, in order;
        // accepts the first whose plex_machine_id == resource.clientIdentifier; never any other address
    public func verify(_ url: URL, expecting machineID: String) async throws(ServerError) -> ServerCandidate
        // GET /api/v1/server; throws .wrongServer(expected:got:) on mismatch, .unreachable on transport failure
    public func firstReachable(_ set: BaseURLSet) async -> ServerCandidate?   // local list then remote list
}
```
The probe is discovery, not trust (ADR §5 item 4); the Home-network toggle and the confirmation step are the app's
(§4, Pairing and Settings). `probeLocal` is the only code that builds a plain-HTTP URL to an address the app did not
receive from the server or an invite, and it only builds them from plex.tv's `connections[].local == true` entries
(ADR §6 item 6).

### 2.7 Changes
```swift
public enum Destination: Sendable, Equatable { case wanted(id: Int?), inbox(messageID: Int?), bell(noteID: Int?), home }
public struct LocalNotification: Sendable, Equatable {
    public let id: String            // "<srv>|<kind>|<ref ?? eventID>"
    public let title: String; public let body: String; public let threadID: String; public let destination: Destination
}
public enum ChangeRouting {
    public static func destination(kind: String, ref: String?) -> Destination
        // "wanted:<id>" → .wanted, "message:<id>" → .inbox, "note:<id>" → .bell, nil → .home
    public static func pushKey(srv: String, ev: String, ref: String?) -> String
}
public struct SeenRing: Sendable, Codable, Equatable {
    public init(capacity: Int = 200)
    public mutating func insert(_ key: String) -> Bool    // false when already present
}
public enum Changes {
    public static func notifications(from page: ChangesPage, serverID: String, seen: inout SeenRing) -> [LocalNotification]
        // one per event, ascending; skips kind "test"; skips keys already in `seen`; inserts the rest
    public static func nextCursor(after page: ChangesPage, previous: Int) -> Int   // max(previous, page.cursor)
}
```
The app decides which tab `.wanted` opens (WantedList for a member, Console/Requests for the owner; ADR §2, design-A
§5.2) in `Router`, not here. `Destination`, `LocalNotification` and `NotificationAuthorisation` (§2.1) are declared
in `Protocols/Notifier.swift` from Phase 0, because `Notifier` names them and the package must build before
`Changes/` exists (07 §5 Phase 0); `Changes/` produces them and never redeclares them.

### 2.8 Themes
```swift
public struct RGB: Sendable, Equatable, Hashable, Codable { public let r, g, b: UInt8; public init?(hex: String) }
public enum Wallpaper: Sendable, Equatable {
    case tiled(path: String, tileWidth: Double, opacity: Double)          // custom look: /themes/art/<slug>.png, 230, 0.3
    case washedTile(path: String, tileWidth: Double, wash: Double)        // bruce: /icons/bruce-logo.png, 220, 0.93
}
public struct Look: Sendable, Equatable, Identifiable {
    public let id: String            // "plex" | "blockbuster" | "netflix" | "bruce" | "c-<slug>"
    public let name: String          // picker label
    public let bg, surface, surface2, border, text, dim, accent, accentDark, onAccent, top, green, red: RGB
    public let barAlpha: Double      // 0.97
    public let posterRadius: Double  // 6 | 3 | 4 | 6; custom looks 6
    public let wallpaper: Wallpaper?
}
public enum BuiltInLooks { public static let plex, blockbuster, netflix, bruce: Look; public static let all: [Look] }
public extension Look { init(theme: Theme) }                     // id "c-" + slug; twelve keys straight across
public enum LookResolver {
    public static func select(chosen: String?, assigned: String?, custom: [Look]) -> Look
        // chosen wins when it exists; else assigned when it exists; else BuiltInLooks.plex
        // `assigned` (user.skin) is a bare slug: it matches a built-in id or a custom look whose id is "c-" + slug
}
```
The four built-in values are copied digit for digit from `dashboard/request.html:37-79` (persona-themes-limits.md §4.1
table) because no endpoint serves them (§7). The Kit test compares each palette against a literal table in the test
file so a drift in either copy fails. `netflix` keeps its slug and takes the picker label "Streaming" (§4.1); labels
are localised strings in the app, not in the Kit.

### 2.9 Demo
```swift
public actor DemoServerClient: ServerClientProtocol {
    public init(fixtures: FixtureBundle, role: Role = .owner)
    public let isDemo = true
    // perform: answers each Endpoint from the fixture whose request.method and request.path match
    // (03 §6.1 envelope); POST /request/wanted, /request/playlist, /request/playlist/remove,
    // /request/message, /announce, /wanted/<id>/status and /messages/<id>/read also update DemoState so
    // the next GET reflects the change within the run. Never throws; never touches Transport, SecretStore
    // or the network; artworkURL returns bundled placeholder images.
}
public struct FixtureBundle: Sendable { public static func bundled() throws -> FixtureBundle }   // Bundle.module
```
(unverified: `Bundle.module` exists only in a target that declares `resources`, and SwiftPM is believed to refuse a
resource path outside the target's directory; the ADR §2 fixes the vendored copy under `PlexGetKit/Tests/Fixtures/`,
outside `Sources/PlexGetKit/`, so 07 §5 Phase 0 gives the library target `path: "."`, `sources:
["Sources/PlexGetKit"]` and `resources: [.copy("Tests/Fixtures")]`, and the files are read with `subdirectory:
"Fixtures"`. Gate: Phase 0 `swift build` must give the `PlexGetKit` library a `Bundle.module` that holds
`Fixtures/VERSION`, or the layout is amended before `Demo/` is written; the builder never keeps a second copy of
the fixtures and never reads them from the file system.)

Fixtures are the same files the contract test decodes (03 §6.3), so demo data and the shapes cannot drift. Titles are
public domain (03 §6.2). Demo role is `owner` so the reviewer sees the Console; `role: .shared` exists for the UI
tests of member-only behaviour.

### 2.10 What the Kit's tests prove

| Test file | Proves |
|---|---|
| `FixturesTests` | Every file under `Tests/Fixtures/` decodes with its catalogued type and every catalogued name exists (03 §6.3); the decoded `Persona` exposes no `voice` or `examples`; no decoded `Film` exposes `missing_files` |
| `ErrorMapperTests` | Each row of 02 §6.1 from a literal body; `401` without `auth_required` still maps; `200` with `error` maps only for the listed routes; unknown `4xx` maps to `.badRequest`/`.notFound`/`.conflict` by status |
| `ServerClientTests` | Header set per request (03 §1.2); bearer absent on open routes; local URLs tried before remote; `.unreachable` on the last URL only; a `.wrongServer` answer drops that URL; decode failure is `.undecodable(route:)` |
| `EndpointsTests` | Every route in 03 §3-§4 has one builder with the right method, path, tier and timeout; no builder exists for any path in 03 §8 (relay server-key builders from Phase 7) |
| `PlexTVTests` | Header set including `X-Plex-Platform-Version`; `Accept: application/json`; `authURL` shape; JSON decoding of pins, user and resources from literal bodies |
| `SignInMachineTests` | Every transition in §2.5 with a manual clock; the 2.5 s poll cadence; the 120 s countdown cap; `pinExpired` issues a new pin without leaving the screen; `cancelled` from every state returns to `idle`; `testTappedInviteRequiresConfirmation` (`source: .invite` reaches `awaitingConfirmation`, only `.qr` reaches `exchanging` directly) |
| `PairingTests` | Invite parsing (valid, wrong `v`, missing `m`, QR text form); `isPrivate` on a literal table of hosts including a `.local` name; `rank` drops public plain HTTP; `probeLocal` calls only `local == true` addresses and accepts only a matching machine id, with a fake transport recording every URL; `testInviteForUnknownOrPairedServerIsRefused` (`Invite.refusal` answers `.notVisible` and `.alreadyPaired`) |
| `ChangesTests` | Routing of each `ref` form; `test` events skipped; the ring de-duplicates and evicts at 200; cursor never goes backwards |
| `ThemesTests` | The four palettes equal the literal table; `Look(theme:)` maps all twelve keys; `LookResolver` precedence (persona-themes-limits.md §4.2) |
| `DemoTests` | Every endpoint builder gets an answer from `DemoServerClient`; a wanted add then a list read shows the row; it never throws for any builder |

## 3. The app target

### 3.1 AppIdentity

One `enum AppIdentity` with static constants and nothing else; renaming the app is an edit to this file and to
`project.yml` (ADR §4 "App Store name"). `displayName` (the App Store name, owner confirms), `bundleIdentifier`,
`plexProduct` (= `displayName`, sent as `X-Plex-Product`), `urlScheme` (the `forwardUrl` scheme; callback path
`plex-auth-done`), `pushTopic` (= `bundleIdentifier`), `inviteHost` (`push.<developer-domain>`), `relayBaseURL`,
`refreshTaskIdentifier` (`<bundleIdentifier>.refresh`), `keychainService` (= `bundleIdentifier`), `supportEmail`,
`privacyPolicyURL`, `termsURL`, `appStoreURL` (the App Store page, opened by Welcome's "Get the update" and named in
09 §11's `openURL` allow-list), `productIDs` (`["owner.yearly", "owner.monthly"]`), `subscriptionGroup` (`"Owner"`),
`probePort` default (`5050`). URL literals are built with `URL(string:)` inside a closure that calls
`preconditionFailure` on `nil`; this is the only permitted `preconditionFailure` in the app and the only place a
literal URL exists. The in-app header uses the server's `app_name`, never `displayName` (design-A §9).

### 3.2 Keychain

`final class Keychain: SecretStore`, serialised on a private actor, `kSecClassGenericPassword`, service
`AppIdentity.keychainService`, `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, no synchronisation, no access group
(brief fact 15). Accounts and lifetimes are the table in 02-architecture.md §4.1. Values are UTF-8 strings; a read of
a missing item returns `nil`, never throws; `errSecInteractionNotAllowed` is retried once after 250 ms (device still
locked at a background launch) and then thrown. `AppTests` runs it on the simulator: write, read, overwrite, delete,
missing.

### 3.3 Store

`final class Store: Entitlements` plus `@MainActor @Observable final class OwnerEntitlement { var active: Bool; var
expiresAt: Date?; var inGrace: Bool; var productID: String? }`. Functions: `loadProducts() async throws -> [Product]`
(both ids, yearly first), `purchase(_ product: Product) async throws -> PurchaseOutcome` (`.purchased`, `.pending`,
`.cancelled`), `restore() async` (`AppStore.sync()`), `startListening()` (one `Task` over `Transaction.updates` for
the app's lifetime), `refreshEntitlement() async` (`active` is true when a verified transaction is in
`Transaction.currentEntitlements` or the product's `Product.SubscriptionInfo.Status` has `state == .subscribed` or
`.inGracePeriod`; `.inBillingRetryPeriod`, `.expired` and `.revoked` are not active, per Apple's `RenewalState`
documentation as read for this plan on 2026-09-22, which matches 01 §2.3's `expires_at` plus 16 days; `inGrace` is
`state == .inGracePeriod`; a `.inBillingRetryPeriod` state shows the Paywall with the sentence "Your subscription
couldn't be renewed. Update your payment details in Settings > Apple Account."), `serverKeyJWS() async -> String?`
(returns the `jwsRepresentation` of the newest `VerificationResult.verified` transaction among
`Transaction.latest(for:)` over `AppIdentity.productIDs`, whether or not it is expired, so the relay deletion of
05 §4.4 stays possible after a lapse or refund; nil only when no transaction has ever existed). Only
`VerificationResult.verified` transactions count; every verified transaction is finished. After a purchase or
restore that makes `active` true the owner's app runs 02 §2.14 steps 3-4 through `RelayClient.issueServerKey` and
`Endpoints.ownerRelay`, sending `rotate: true` only in the three cases of 02 §2.14 step 5 and holding an
un-forwarded key as `relay.server-key.pending.<machine_id>` until the server answers `200`; a renewal re-posts the
JWS without `rotate` (05 §4.3).
`Products.storekit` lists both products with the 7-day introductory offer on `owner.yearly` for `xcodebuild test`
(Phase 7 gate). Demo mode never touches `Store` (§7).

### 3.4 PushBridge

`final class PushBridge: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate`, installed with
`@UIApplicationDelegateAdaptor`; every method hops to `@MainActor`. `didRegisterForRemoteNotifications` hex-encodes
the token and runs 02 §2.12 steps 2-3 (`RelayClient.registerDevice`, then `Endpoints.pushDevices`); the `environment`
is `sandbox` only when the build's `aps-environment` entitlement is `development` (a development-signed build run from
Xcode on a device) and `production` otherwise, TestFlight, Ad Hoc and App Store builds included (05 §7.3, gate 5).
`PushBridge` decides by reading `Bundle.main.path(forResource: "embedded", ofType: "mobileprovision")`, taking the
bytes between `<plist` and `</plist>`, decoding them with `PropertyListSerialization` and reading `Entitlements` →
`aps-environment`; a missing profile (App Store builds strip it) means `production`; on the simulator no relay
registration is attempted (unverified: the embedded-profile layout is from memory; the Phase 6 device step records
which value the TestFlight device registered with, and the ADR §6 item 9 "sandbox push to a TestFlight build" is
satisfied by a push reaching that build whichever host carried it). `willPresent` returns `[.banner, .list, .sound]`.
`didReceive` reads `ev`, `ref`, `srv` from `userInfo`; when `srv` is not a stored server it routes nothing and
records nothing (02 §2.12 step 10; `PushBridgeTests.testPushForUnpairedServerIgnored`, 09 §11); otherwise it
records `ChangeRouting.pushKey` in the `SeenRing` and calls `Router.open(destination)`. `didFailToRegister` logs and leaves `push.delivery` handling to `Background`.
`requestPermission()` is called once by `SessionStore` after the first successful sign-in with the sentence
"Notifications are how it tells you when something you asked for has arrived." shown first (ADR §4 "Notification
permission"); the prompt time goes in `notifications.askedAt`.

### 3.5 Background

`enum Background` registers `AppIdentity.refreshTaskIdentifier` in `AppMain.init` (before the scene exists), schedules
with `earliestBeginDate = now + 15 minutes` after every run and every foreground, and runs
`ChangesFetcher.run(client:notifier:defaults:)` which is 02 §2.13 steps 2-5. `submit` is wrapped in `do/catch`;
`BGTaskScheduler.Error.unavailable` (simulator, or Background App Refresh off) is logged once at `.info` and ignored,
never retried in a loop, and the foreground fetch still runs; `AppTests` asserts `Background.schedule()` does not
throw on the simulator. The same fetcher runs on every
`scenePhase == .active` transition. When `server.<id>.delivery` is `relay` the task stays registered and the fetcher
still posts nothing already in the `SeenRing`. `Task.detached` is permitted only inside `Background.run(task:)` and
its expiration handler cancels that task (02 §5 item 7).

### 3.6 Session-level stores and the router

`@MainActor @Observable final class SessionStore` holds `phase` (`.launching`, `.welcome`, `.demo`, `.signedIn`),
`client: any ServerClientProtocol`, `server: ServerInfo?`, `user: SessionUser?` (id, username, role, defaultPlaylist,
skin, email, thumb), `persona: Persona?`, `themes: [Theme]`, `look: Look`, `delivery: PushDelivery`, and performs
launch (read `server.current`, load caches from 02 §4.3, verify the stored URL set, `GET /api/v1/auth/session`, `GET
/persona`, `GET /themes`), sign-out (02 §2.16) and the `.signInRequired` policy of 02 §6.2. Its `Notifier` is
`PendingNotifier` (Phase 5, answers `.notAsked` and does nothing) until Phase 6 replaces it with
`UserNotificationsNotifier`. `@MainActor @Observable
final class Router` holds `selectedTab`, a `NavigationPath` per tab, a `presentedSheet`, and `open(_ destination:
Destination)`. The `plex-auth-done` callback arrives in the `ASWebAuthenticationSession` completion handler owned by
the Welcome store (which then issues `SignInEvent.tick` to poll at once); `onOpenURL` on the root view receives only
universal links (`/join`, handed to Pairing as `source: .invite`) and a `plex-auth-done` URL opened from outside the
session, which is treated the same way as the completion handler. `URLSessionTransport`'s `URLSession` is configured
with the shared 50 MB `URLCache` of 02 §4.3, so artwork responses are cached by URL under the bearer-carrying request.

### 3.7 Navigation shape

The root is a `switch` on `SessionStore.phase`: `.launching` shows the launch background only; `.welcome` shows
Welcome as a full-screen `NavigationStack` with Pairing pushed on it; `.signedIn` and `.demo` show the `TabView`.
Tabs, in order, with `tag` values from `enum Tab`:

| Tab | Root screen | Also reachable from it |
|---|---|---|
| Home | Home | Results (pushed by a search), Detail (sheet), Bell (toolbar bell button, sheet), Looks (toolbar), Report and Help (doors), Playlists auto-build (door), the gift flow (owner door) |
| Playlists | Playlists | Playlist items (pushed), Detail (sheet), "Make someone a playlist" (owner, pushed) |
| Wanted | WantedList | nothing pushed; ask buttons live in Results |
| Settings | Settings | Sessions (own rows, pushed), Looks, Help, Report, About |
| Console | ConsoleTab | Requests, Inbox, Announce, Discover, Overview, Notifications, Server, Sessions (owner rows) pushed from a list; Paywall in place of the list |

Four tabs for a member, five for the owner, so the system "More" tab never appears. Bell is a sheet from Home's
toolbar rather than a tab, as the web header does (screens-inventory.md §2.3), and it carries the unread dot. The
`TabView` uses the default style on every device.

### 3.8 Console tab visibility

One type, `ConsoleGate`, answers two questions and nothing else in the app decides them:

1. `isVisible`: true when `SessionStore.user?.role == .owner`, or when `client.isDemo`. False otherwise, including
   while signed out and for every `shared` role. A `.ownerOnly` error from any owner route sets it false until the
   next `GET /api/v1/auth/session` says `owner` again (02 §6.2).
2. `isOpen`: true when `client.isDemo`, or when `OwnerEntitlement.active`. When false the tab's root is Paywall. The
   app never presents Paywall on its own; it is reached only by tapping the Console tab (ADR §6 item 11: the paywall
   appears only after the app has worked at home). The tab label is "Console"; in demo mode it is "Console (demo)" and
   the Console screens carry the demo banner (§7).

## 4. Screens

Each entry has five parts: Purpose; Calls (endpoints, by 03-server-api.md section); State (reads → writes, keys per 02
§4); Errors (only what differs from 02 §6.2; the defaults always apply); States. The default states apply everywhere
and are not repeated: loading shows the cached content of 02 §4.3 where one exists, else a progress indicator in place
of the list; offline shows cached content with the "Offline" pill and disables every mutating control (02 §6.2
`.unreachable`); a screen with nothing cached shows its empty state and a Retry. Each entry lists its empty state and
only the deviations. `UserDefaults` keys named below that 02 §4.2 does not list (`results.sort`, `playlist.<id>.last`,
`home.<id>.nudgedOn`, `home.<id>.shut.<card>`, `help.tourSeen`, `requests.filter`, `discover.filters`,
`console.firstOpenAt`) are this document's additions to that table, declared in the same `DefaultsKeys.swift`, none
secret. Copy in quotation marks is the base en-GB string; server-provided strings are shown verbatim and are never
localised. Every artwork path renders through `ArtworkImage(path:width:height:)` in `Shared/`, a view whose loader
fetches `client.artworkURL(path:width:height:)` (03 §4.15) through `Transport` with the bearer header (`GET
/wanted/thumb` is household tier, 03 §1.3; `URLSessionTransport`'s `URLSession` carries the shared 50 MB `URLCache`,
§3.6, so responses are cached by URL); `AsyncImage` is used only for absolute TMDb `poster` URLs and the plex.tv
avatar. A session token never appears in a query string (§10.2 lint).

### 4.1 Household screens

**Welcome** (`App/Welcome/`). Purpose: the entry screen with "Sign in with Plex", "I have an invite", "Try the demo",
the persona's `welcome` face when the last-known persona has one, the tagline, the three "what it does" rows and "Your
password stays with Plex" (screens-inventory.md §2.2); the pin card shows the `code`, "plex.tv/link", "Waiting for
you…" and "Get a fresh one". Calls: plex.tv pins, user, resources (§2.4); `GET /api/v1/server` (03 §3.1) on each
candidate; `POST /api/v1/auth/plex` (03 §3.2); after success `GET /persona`, `GET /themes`, `GET
/api/v1/auth/session`. State: reads `demo.active`, `server.current`; writes Keychain `plex.account-token`,
`session.<id>`, `UserDefaults` `server.<id>.*`, `server.current`, `notifications.askedAt`. Errors: 02 §6.4 Welcome
rows; `.denied(.updateNeeded)` replaces the screen with "Update needed" and an App Store link; `.denied(.setupMode)`
shows "This server is still being set up" with the setup URL. States: loading is the pin card's pulsing status; no
empty state; offline keeps "Try the demo" enabled and disables the other two doors with "No connection".

**Pairing** (`App/Pairing/`). Purpose: find the server when the probe found nothing or the person tapped "I have an
invite": a QR scanner (VisionKit `DataScannerViewController`, `.barcode(symbologies: [.qr])`), "Paste the link", "Type
an address", and the confirmation card ("Connect to <server_name> at <address>?" with `app_name`) for a server not
reached through an invite (ADR §4 "LAN probe and trust") and for a tapped invite link, which shows "Someone sent you
a link to <server_name> at <address>. Connect?" (§2.5: only a QR scanned in the app skips the card). The scanner
needs `NSCameraUsageDescription` (§1); `DataScannerViewController.isSupported` is false on the simulator, so the
scanner door shows "Paste the link" there and the UI tests never open it. Calls: `GET /api/v1/server` via
`Prober.verify` and `Prober.probeLocal`. State: reads the plex.tv resources held by the Welcome store; writes
`server.<id>.urls.local`, `.urls.remote`, `.invited`, `.confirmed`, `.name`, `.homeNetwork` (default on for owners
and when the first probe succeeded, off otherwise). Errors: `.wrongServer` drops the URL and says "That address
answers for a different server"; an invite with a wrong `v` says "That invite is from a newer version"; an invite
whose `m` is not in the signed-in account's resources (`Invite.refusal` → `.notVisible`, §2.6) is refused before any
request with "That invite is for a server your Plex account cannot see"; an invite for an `m` already stored
(`.alreadyPaired`) is refused with "You are already connected to this server"; a typed `http://` address to a public
host is refused before any request with "Use https:// for an address outside your home". States: loading shows
"Looking for your server at home…" for at most 1.5 s per address; empty (nothing found) and offline both show the
three doors with the copy from 02 §6.4; when the probe ran and every address failed, the empty state adds the line
"If you said no to Local Network access, turn it on in Settings > Privacy & Security > Local Network" with a button
that opens `UIApplication.openSettingsURLString` (Pairing joins the `openURL` allow-list of 09 §11).

**Home** (`App/Home/`). Purpose: the signed-in front page: search field ("A film, a series, or the mood you're in"),
the persona line "Hi, I'm <name> — <greeting>" with the `greeting` face, the doors "Build a playlist", "My playlists",
"Make someone a playlist" (owner only), the "include ones we haven't got" toggle, "Something wrong?" and "Help", the
hero reel and the showcase reels, the starter card and the daily nudges card (screens-inventory.md §2.3, §2.8, §2.19).
Calls: `GET /request/showcase` (03 §4.11), `GET /request/suggestions` (only when `Playlists` reports zero lists), `GET
/request/nudges` (once per calendar day per device), `POST /request/playlist` for the "+" quick add and the starter
card (03 §4.8), `GET /api/v1/changes` on foreground through `ChangesFetcher`. State: reads `persona.json`,
`showcase.json` caches, `look.selected`, `home.<id>.nudgedOn` (`YYYY-MM-DD`), `home.<id>.shut.starter`,
`home.<id>.shut.nudges`, `playlist.<id>.last` (last list added to); writes the same plus the caches. `include_missing`
is not remembered (§2.20). Errors: 02 §6.4 Home rows; a quick-add `.serverSaidNo` shows the message under the poster.
States: the greeting line paints from cache before the reels; empty (no rows) shows the greeting and the doors only;
offline also disables search submit.

**Results** (`App/Results/`). Purpose: one search and its five outcomes (screens-inventory.md §2.4), the "thinking"
state with rotating lines, the keyword notice when `translated_by == "keywords"` (wording keyed on `smart_mode`), sort
chips over two or more films, multi-select film cards, the selection bar ("None", "for whom" for the owner, target
list, "Add <n>"), series cards with season chips, guess cards with release state, and the missing block. Calls: `POST
/request/search` (03 §4.2), `POST /request/wanted` (03 §4.3, with `tmdb_id` when the ask came from a guess: 03 GAP 1),
`POST /request/playlist` (03 §4.8), `GET /request/playlists` (03 §4.5), `GET /members` (owner, 03 §4.28). State: reads
`results.sort` (`best|new|az|added`, default `best`), `playlist.<id>.last`; writes `results.sort`,
`playlist.<id>.last` only when the list is the person's own. Errors: 02 §6.4 Results rows; a `403` on `for_user` is
`.ownerOnly`. States: loading is the thinking block (lines every 2.1 s, patience lines after 9 s with the `thinking`
face, an aside after 14 s); empty is "Nothing here matches that." with "Ask for it instead"; offline mid-search is 02
§6.4's dropped- connection copy with Retry.

**Detail** (`App/Detail/`). Purpose: the long-press peek as a sheet (also a tap on a playlist item): backdrop from
`art` at 900x500, facts line, tagline, summary, genres, directors, writers, cast, studio, quality, watched count,
season list for shows, "Add to my list" (screens-inventory.md §2.9). Calls: `GET /request/detail?key=` (03 §4.10),
`POST /request/playlist` for the add. State: reads `playlist.<id>.last`; writes nothing. Errors: `.notFound` closes
the sheet with "Not on the shelf". States: the poster already on screen stays while loading; no empty state.

**Playlists** (`App/Playlists/`). Purpose: "My playlists" with "<title>" and "<n> films", a list's items with sort
chips, "Remove" per film and "Playing order lives in Plex"; "Build a playlist" from a phrase (`count: 12`,
`include_missing`) with the success card, the plain titles and the `also_wanted` warn card; for the owner, "Make
someone a playlist": pick a member, describe the list or "Use what <username> has been watching", name it (default
"From <owner username>"), "Put it in <username>'s Plex" (screens-inventory.md §2.10, §2.11, §2.22). Calls: `GET
/request/playlists`, `GET /request/playlist/items?name=`, `POST /request/playlist/remove`, `POST
/request/playlist/auto`, `POST /request/playlist` with `for_user`, `GET /members`, `GET /members/<username>/picks`,
`POST /request/search` for "Find them" (03 §4.5-§4.9, §4.28, §4.2). State: reads and writes `playlist.<id>.last`,
`results.sort`. Errors: 02 §6.4 Playlists row; the auto-build `404` shows the server's `interpretation` with "Try
another phrase"; the auto-build `200 kind: "cheeky"` is a persona card, not a success card, and no playlist name is
printed (the page's "undefined" quirk, screens-inventory.md §2.10, is not reproduced); `409` on gifting shows the
server's sentence verbatim. States: empty is the `chilling` face with "No lists yet.", inside a list the `sitting`
face with "Nothing in this one yet.", and "Nobody has signed in yet." for an empty member list.

**WantedList** (`App/WantedList/`). Purpose: the household's wish list grouped Waiting / Arrived / Set aside by
`status`, each row "<Title (year)> — series <n>" and "<word> · Asked by <requested_by>", with the counts in the
section headers; read-only for everyone (screens-inventory.md §2.12; the owner's actions are in Console/Requests).
Calls: `GET /request/wanted/list` (03 §4.4). State: reads `wanted-list.json` cache; writes it. Errors: none beyond the
defaults. States: empty is the `looking` face with "Nothing on the list yet. Search for something and ask for it."

**Bell** (`App/Bell/`). Purpose: the three newest announcements ("From <posted_by>"), the notification state
("Notifications are on for this device." / "Turn on notifications" / "Notifications are off in Settings" with a link
to the system Settings app), "Send a test", "Re-register this device", and, only in the polling state, "Notifications
may arrive late" (screens-inventory.md §2.14; design-A §5.3). Calls: `GET /request/announcements` (03 §4.13), `POST
/api/v1/push/test` (03 §3.10), the registration sequence through `PushBridge` (02 §2.12). State: reads
`bell.<id>.seenNotes`, `server.<id>.delivery`, `Notifier.authorisationState()`; writes `bell.<id>.seenNotes` (highest
id on open) and the `announcements.json` cache. The dot on Home's bell button shows when any note id exceeds the seen
set or when authorisation is `.notAsked`. Errors: 02 §6.4 Bell rows. States: empty is "Nothing from the owner yet.";
offline keeps the push state visible with "Send a test" disabled.

**Looks** (`App/Looks/`). Purpose: the picker: four built-ins (labels "Plex", "Blockbuster", "Streaming", "Bruce" with
their descriptions from persona-themes-limits.md §4.1) then "Made here" for owner-made looks, each chip showing `bg`,
`border`, `accent`; picker only, no "Make a look" and no remove (ADR §2 "Looks"; 02 §7). Calls: `GET /themes` (03
§4.1). State: reads `themes.json`, `user.skin`, `look.selected`; writes `look.selected` (a person's own pick always
wins over `user.skin`, persona-themes-limits.md §4.2). Errors: 02 §6.4 Looks row. States: the four built-ins show at
once; an empty "Made here" section is omitted.

**Report** (`App/Report/`). Purpose: "Something wrong?" with two paths. Owner path: a 500-character field, "Attached:
your search "<current search text>"" and Send, then "Sent to the owner" with "Their phone has been told" or "It's on
their console" by `sent` (screens-inventory.md §2.17). Developer path: "Report to the developer" opens the Mail
composer (`MFMailComposeViewController`; when Mail is unavailable, a share sheet with the same text) to
`AppIdentity.supportEmail` with a body of the app version, iOS version and the server `build`, and nothing else; no
endpoint is involved (ADR §3 "a report path that reaches the developer"; design-A §9 1.2). Calls: `POST
/request/message` (03 §4.14). State: reads the search text from the Results store; writes nothing. Errors: 02 §6.4
Report row; Send is disabled while the field is empty. States: no empty state; offline keeps the text.

**Help** (`App/Help/`). Purpose: the six static paragraphs (Search; Make a playlist from this; If we don't have it;
Your playlists are yours; About the AI, shown only when `persona.smart_mode` is true; Something not right?) and "Show
me around", a three-page tour sheet (search, the doors, the bell) shown once on the first signed-in launch
(screens-inventory.md §2.16). Calls: none. State: reads and writes `help.tourSeen`. Errors: none. States: none; the
text is bundled and localised.

**Settings** (`App/Settings/`). Purpose: account (username, `thumb`), server (name, current address, "Home network"
toggle with the sentence "Look for the server on this Wi-Fi using the addresses Plex knows"), notifications (state,
"Notifications may arrive late" in the polling state, "Remove this device's push registration"), "Your devices" (own
sessions with device name, platform, last seen, "current"; swipe to sign out that device; "Sign out everywhere"),
Looks, Help, Report, "Sign out" with the "Keep me signed in to Plex" switch, About (version, privacy policy, terms,
licences). Calls: `GET /api/v1/auth/sessions`, `DELETE /api/v1/auth/sessions/<id>`, `DELETE /api/v1/auth/sessions`,
`DELETE /api/v1/auth/session`, `DELETE /api/v1/push/devices/<device_id>` (03 §3.4-§3.9), relay `DELETE
/v1/devices/<handle>` (05-push-relay.md). State: reads `server.<id>.homeNetwork`, `server.<id>.delivery`; writes
`server.<id>.homeNetwork`; sign-out wipes per 02 §2.16 step 2. Errors: 02 §6.4 Settings/Sessions row; a failed device
delete on sign-out is logged and the sign-out continues. States: the sessions list is never empty (the current row is
always present); offline still allows "Sign out" (local wipe only; the server row expires or is revoked from another
device).

### 4.2 Console screens (owner, behind `ConsoleGate`)

**ConsoleTab**. A `List` of the eight screens with badges (Inbox: `unread_messages` from the last `ChangesPage`;
Requests: `wanted_counts.wanted`), or Paywall when `isOpen` is false. Calls: `GET /api/v1/changes` (already fetched by
`ChangesFetcher`). State: reads the last `ChangesPage`.

**Requests** (`Console/Requests/`). Purpose: triage. Segmented filter `wanted` (default) / `acquired` / `dismissed` /
`all`; rows with title, year, kind and season, who asked, when; swipe actions Acquired, Dismiss, Back to wanted; a
"Search Plex" field ("Already in your library" with match cards, "Not in your library — added to your wanted list",
"Already on your wanted list since <date>"); toolbar "Recheck" and "Settle" (screens-inventory.md §3.5; settle has no
web button and is added here because it is what sends `arrived`, design-A §5.2). Calls: `GET /wanted?status=`, `POST
/wanted/<id>/status`, `GET /wanted/search?q=` (with `tmdb_id` when known, 03 GAP 2), `POST /wanted/recheck`, `POST
/wanted/settle` (03 §4.16-§4.19). State: reads `requests.filter`; writes it. Errors: 02 §6.4 Console/Requests row;
`.notFound` on a status change refreshes the list. States: empty per filter is "Nothing waiting." / "Nothing has
arrived yet." / "Nothing set aside."

**Inbox** (`Console/Inbox/`). Purpose: messages from the household with who, when, body, "They had searched:
"<context>"", unread/all filter, swipe Mark read / Mark unread (screens-inventory.md §3.6). Calls: `GET
/messages?unread=1`, `GET /messages`, `POST /messages/<id>/read` (03 §4.20). State: none beyond the list. Errors:
`.notFound` on read refreshes. States: empty is "No messages." / "Nothing unread."

**Announce** (`Console/Announce/`). Purpose: a 280-character field and "Tell everyone", then "Told everyone — notified
<sent> devices"; below it the three newest live notes with swipe "Retire" (screens-inventory.md §2.18; `POST
/announce/<id>/retire` exists without a web button). Calls: `POST /announce`, `POST /announce/<id>/retire`, `GET
/request/announcements` (03 §4.21, §4.13). State: none. Errors: `.badRequest` inline. States: empty is "Nothing
announced yet."; offline keeps the text.

**Discover** (`Console/Discover/`). Purpose: "Films you don't have": genre chips, match all/any, rating floor (6),
vote floor (200), year from/to, sort, depth; result cards with poster, title, year, rating, overview, `wanted_status`,
and "Want it" (screens-inventory.md §3.3 first panel). Calls: `GET /discover/genres`, `GET /discover/missing?…`, `GET
/wanted/search?q=&tmdb_id=` (03 §4.22, §4.17). State: reads and writes `discover.filters` (the last filter set).
Errors: 02 §6.4 Console/Discover row; `502` `.plexTVUnreachable` is shown as "Couldn't reach TMDb". States: loading
adds "This can take a while on the first run"; empty is "Nothing matches those filters."; `configured == false`
replaces the screen as in 02 §6.4.

**Overview** (`Console/Overview/`). Purpose: the six stat cards from `/summary`, the by-library table, and the library
list with types (screens-inventory.md §3.1). Calls: `GET /summary`, `GET /libraries` (03 §4.24). State: none. Errors:
02 §6.4 Console/Overview row. States: a request-only install shows the libraries and "Scan results appear here once
the server has scanned the shelves".

**Notifications** (`Console/Notifications/`). Purpose: the catalogue read-only (`arrived`, `requested`,
`file_missing`, `message`, `test`, `announcement`: enabled, title, body), device counts (native and web), relay state
with expiry, "Send a test" (design-A §5.4; ADR §4 cuts editing). Calls: `GET /notifications` (03 §4.25), `GET
/api/v1/owner/status` (03 §3.14), `POST /api/v1/push/test` (03 §3.10). State: none. Errors: defaults. States: no empty
state (the catalogue always has `message`).

**Server** (`Console/Server/`). Purpose: status (Plex reachable, name, version; webhook configured and last hit;
settle interval and last run; build), the pairing card (name, machine id, local and remote addresses), the invite as a
share link and a QR (`CIFilter.qrCodeGenerator()`; unverified: from memory, Core Image's built-in filter), the relay
state (delivery, expiry) with "Unlink and delete my relay data", the remote-reach guide (the copy from
06-install-and-setup.md pointing at the setup page), and `min_app_version`. Calls: `GET /api/v1/owner/status`, `GET
/api/v1/server`, `DELETE /api/v1/owner/relay` (03 §3.14, §3.1, §3.13); relay `DELETE /v1/server-keys/<id>` and `DELETE
/v1/devices/<handle>` (05-push-relay.md). "Unlink and delete my relay data" is enabled whenever `serverKeyJWS()` is
non-nil, including after a lapse or refund (§3.3), because 05 §4.4 needs that JWS. State: reads Keychain
`relay.server-key-id.<id>`, `relay.handle`; the delete clears them. Errors: 02 §6.4 Console/Server rows. States: empty `urls.remote` shows "No outside address yet" with the
guide; offline keeps the pairing card and invite from cache.

**Sessions** (`Console/Sessions/`). Purpose: every session row with `username`, device name, platform, created, last
seen; swipe to revoke any (ADR §2 "own, or any for the owner"). Calls: `GET /api/v1/auth/sessions`, `DELETE
/api/v1/auth/sessions/<id>` (03 §3.5-§3.6). State: none. Errors: `.notFound` refreshes. States: never empty.

**Paywall** (`Console/Paywall/`). Purpose: what the Owner subscription is (the Console and native push for the
household), `owner.yearly` first with the 7-day introductory trial and the price billed afterwards, `owner.monthly`,
period, auto-renewal terms, links to `privacyPolicyURL` and `termsURL`, "Restore purchases" (design-A §6, §9 3.1.2).
Calls: none to any server; `Store` only. State: `OwnerEntitlement`. Errors: 02 §6.4 Paywall row. States: "Products
aren't available right now" when `loadProducts` returns fewer than two; offline shows the copy with the buttons
disabled.

## 5. Persona rendering

The app renders exactly the six keys of `GET /persona` (persona-themes-limits.md §1.3, §8 item 1) and nothing else. It
never requests, stores, sends or synthesises `voice` or `examples`, never implements an off-topic filter or a
brush-off selection of its own, and shows `interpretation` from the server as-is (§1.6; ADR §4 "Persona").

| Element | Rule | Source |
|---|---|---|
| Greeting line | "Hi, I'm **<name>** — <greeting>" with `name` in the accent colour, `greeting` face before it, height 132 pt | persona-themes-limits.md §2.2 |
| Faces | `PersonaFace(mood:size:)` draws `images[mood]` from `<server origin>/icons/…`; when the mood has no entry or the image fails to load the view is empty and takes no space ("no face, no hollow column") | §2.3 |
| Sizes | default 132 pt, `.tall` 168, `.small` 96, `.tiny` 78; at a compact width (≤ 380 pt) default 108 and tall 132; drop shadow 6/14 at 45 % black; bottom-aligned | §2.3 |
| Mood by place | The table at persona-themes-limits.md §2.3 verbatim: `greeting` default size, `welcome` tall, `searching`/`thinking` small, the other nine tiny, each at the place that table names; §4 above names the mood each screen draws | §2.3 table |
| Brush-off card | `kind == "cheeky"`: "<name> says" and `interpretation`, accent left border, no results, no ask button; the `brush_offs` list is used only when `interpretation` is empty | §2.4, §8 item 2 |
| `brush_off_moods` | honoured: the brush-off card's face is a random entry of `brush_off_moods` that has an image, else one of `cheeky`, `shrug`, `unsure` (the web page drops the field; the app does not) | §2.1, §8 item 9 |
| `smart_mode` | chooses the Welcome card ("There's an AI in here…" versus "Search the shelves…"), the keyword-notice wording, and whether Help shows "About the AI" | §2.5, §6.4 |
| Accessibility | faces are decorative: `accessibilityHidden(true)`; the greeting reads as one sentence | §8 |

Persona images are the only `/icons/*` the app loads besides `bruce-logo.png`; both are open tier and cached 600 s
(§1.4).

## 6. Looks in SwiftUI

The app runs in `.preferredColorScheme(.dark)` because every palette is dark (persona-themes-limits.md §4.1); the
system Dynamic Type and Reduce Motion settings still apply. The selected `Look` is injected as an `EnvironmentKey`
(`\.look`) and every colour in the app comes from it; no view names a `Color` literal.

| Look key | SwiftUI use |
|---|---|
| `bg` | window and scroll-view background (`.background`), sheet backgrounds |
| `surface` | cards, list rows, the pin card |
| `surface2` | chips, secondary fills, the search field |
| `border` | one-point strokes, list separators |
| `text` | `.foregroundStyle` primary |
| `dim` | secondary text, meta lines, placeholders |
| `accent` | `.tint`, filled buttons, the accent name in the greeting, links, selection ticks |
| `accentDark` | pressed state of filled buttons |
| `onAccent` | text and icons on `accent` fills |
| `top` | navigation and tab bar backgrounds (`.toolbarBackground(look.top, for: .navigationBar, .tabBar)`) |
| `green` | success cards, "Added" states, the Arrived section |
| `red` | warn cards, destructive swipe actions, "Set aside" |
| `bg` at `barAlpha` | the selection bar's background |
| `posterRadius` | corner radius of every poster and backdrop |
| `wallpaper` | `.tiled`: a full-screen `Image(...).resizable(resizingMode: .tile)` scaled so a tile is 230 pt wide at 30 % opacity beneath content, above `bg`; `.washedTile` (bruce): the same at 220 pt under a 93 % black wash. Loaded from the server; absent or failing wallpaper draws nothing (the Bruce emblem is not in the repository, §4.1) |

Custom looks come from `GET /themes` as `Look(theme:)` with all twelve colours provided by the server (six stored, six
derived, §5.2); the app derives nothing. `LookResolver` precedence is §4.2: the person's pick, else `user.skin` when
it names an available look, else `plex`. `UIColor`/`Color` are made with `Color(red:green:blue:)` from `RGB`; the
conversion lives in `Shared/LookEnvironment.swift` only.

## 7. Demo mode

Entered from Welcome's "Try the demo" (brief fact 7) or the launch argument `--demo` used by the UI tests.
`SessionStore` sets `phase = .demo`, `demo.active = true`, and `client = DemoServerClient(fixtures: .bundled(), role:
.owner)`; the stores are the same objects the live app uses (02 §5 item 9). It shows a persistent top banner "Demo —
nothing here is real" on every screen, the Console tab as "Console (demo)" open without purchase (ConsoleGate
`isOpen`), and a "Leave the demo" button in the banner that clears `demo.active` and returns to Welcome. Persona is
the fixtures' ("Reel"), household and titles are the fixtures' public-domain data. Demo mode never: opens a network
connection (no `Transport` exists in the client), writes the Keychain, requests notification permission, registers for
push, schedules `Background`, calls `Store` or StoreKit, shows Paywall, or issues a server key. Screenshots for the
store listing come from it (ADR §7 Phase 9).

## 8. Accessibility and Dynamic Type minimums

1. Every text uses a semantic font (`.body`, `.headline`, `.caption` …); no `.system(size:)` and no fixed sizes except
   the persona face heights, which scale with `@ScaledMetric`.
2. Every screen is checked at `.accessibility3` in the UI tests (§11) with no clipped or overlapping text; reels wrap
   to vertical lists at `.accessibility1` and above.
3. Hit targets are at least 44×44 pt; icon-only buttons (bell, looks, "+", chips, ticks, "None") carry
   `accessibilityLabel`; posters carry "<title>, <year>" and `accessibilityAddTraits(.isButton)`; selected cards carry
   `.isSelected`.
4. Persona faces and wallpapers are `accessibilityHidden(true)`; cards read their heading then body.
5. Reduce Motion disables the hero cross-fade, the reel auto-drift and the thinking-line animation; the content still
   changes without animation.
6. Colour is never the only signal: Waiting/Arrived/Set aside sections have headings, the unread dot has a label,
   rate-limited and error cards have a leading symbol.
7. Contrast of `text` and `dim` on `bg` and `surface` for the four built-ins is asserted once in `ThemesTests` (WCAG
   AA 4.5:1 for `text`, 3:1 for `dim`, using the relative-luminance formula in the test); custom looks are the owner's
   responsibility and are not checked.
8. VoiceOver order follows reading order; the selection bar announces "<n> selected" on change.

## 9. Localisation

`Localizable.xcstrings` with `en-GB` as the base and only language in v1; every user-facing string in `App/` is a
`String(localized:)` or a `LocalizedStringKey` with a comment for the translator; plurals use the catalogue's plural
variants per key (unverified: string catalogue plural variation as documented for Xcode 15). No sentence is assembled
by concatenation; interpolations are named (`"Added \(count) to \(list)"`). Strings that arrive from the server
(`app_name`, persona text, `interpretation`, error `message` and `hint`, notification titles and bodies, playlist
names, announcements) are shown verbatim and never localised. Dates use `Date.FormatStyle` with the current locale;
the Kit's `SQLiteDate` parses UTC and the app formats. The banned-word grep (§10.3) runs over the catalogue too.

## 10. Swift 6 concurrency and the coding rules

### 10.1 Concurrency

The rules in 02-architecture.md §5 apply verbatim: one `actor ServerClient`; `Transport` the only I/O seam; one
`@MainActor @Observable final class <Feature>Store` per feature folder; every `Models` type a `Sendable` value; one
in-flight `Task` per store operation with `Task.checkCancellation()` after each await; `SignIn` pure; platform
callbacks hop to `MainActor`; `Store` listens once. Additionally:

1. `NSLock`, semaphores and `Thread` join 02 §5's forbidden list; no global mutable state (`AppIdentity` holds `let`
   constants only).
2. A view never calls the client; it calls its store. A store never builds a `URLRequest` or reads the Keychain; it
   calls `SessionStore` or the Kit.
3. `strict-concurrency=complete` and `-warnings-as-errors` are set in `Package.swift` and `project.yml`; a warning
   fails CI.

### 10.2 Coding rules (enforced by ios-CLAUDE.md and CI)

| Rule | How it is checked |
|---|---|
| No third-party dependencies (brief fact 12) | `Package.swift` has no `dependencies`; `project.yml` has no `packages` beyond the local Kit; CI greps |
| Tokens only in the Keychain through `SecretStore`; never in `UserDefaults`, files or logs (brief fact 15) | Review; `os.Logger` calls use `privacy: .private` for anything not a route name |
| No force unwraps, no `try!`, no `as!`, no `fatalError` except `AppIdentity`'s URL precondition | CI grep for `!` patterns and the two keywords |
| `os.Logger` not `print` | CI grep for `print(` |
| No `URLSession` outside `URLSessionTransport` | CI grep |
| No `URLRequest(` outside `Endpoints.swift` | CI grep |
| No token in a query string (§4 artwork) | `grep -rn "token=" App PlexGetKit` must be empty |
| XcodeGen only; `.xcodeproj` gitignored | `.gitignore`; CI regenerates |
| Swift 6 strict, iOS 17 | `project.yml`, `Package.swift` |
| Every string localised (§9) | Review; the UI tests' banned-word walk also fails on an untranslated key name |
| Small diffs, one thing each | PR template |

### 10.3 Review-facing copy rules

No string in `App/`, `PlexGetKit/`, the fixtures or the store metadata contains, in any case: `download`, `torrent`,
`usenet`, `nzb`, `indexer`, `sonarr`, `radarr`, `tracker`, `seed`, `magnet`, `release group`, `Plex` in the app name
or icon (brief facts 8 and 9; ADR §6 item 10). The wanted list is "the list", "the wish list" or "what you've asked
for"; arrival is "it's here" or "on the shelf". `scripts/banned-words.sh` is the gate in both CI workflows and in
Phase 9's fixture review.

## 11. UI smoke tests (`AppUITests`, demo mode)

Every test launches with `--demo --uitest` (the second disables animations and the tour) and uses
`accessibilityIdentifier`s declared in `Shared/Identifiers.swift`. Each asserts what is listed and nothing else; none
touches the network, the Keychain, StoreKit or notifications.

| Test | Asserts |
|---|---|
| `testWelcomeShowsThreeDoors` | Without `--demo`: "Sign in with Plex", "I have an invite", "Try the demo" exist; tapping the demo door shows the demo banner and the Home tab |
| `testHomeGreetingAndReels` | The greeting contains the fixtures' persona name; at least one showcase reel is present; the bell button exists |
| `testSearchTitleRendersFilmCards` | Typing the fixtures' title-search query and submitting shows the head card and at least one film card; the sort chips appear when two or more films |
| `testSearchCheekyShowsBrushOff` | The cheeky query shows "<name> says" and no Ask button |
| `testSearchTitleMissingShowsGuesses` | The title-missing query shows "Which one do you mean?" with a guess card and an "Ask for this one" button; tapping it changes the button to "On the list" |
| `testSelectionBarAddsToPlaylist` | Selecting two cards shows "Add 2"; tapping shows "Added 2 to" |
| `testDetailSheet` | A long press on a card opens Detail with the fixture's title and "Add to my list" |
| `testPlaylistsListAndItems` | Playlists tab lists a fixture playlist; opening it shows film rows and "Remove" |
| `testWantedListSections` | Wanted tab shows the Waiting, Arrived and Set aside headers with the fixture counts |
| `testBellShowsNotes` | The bell sheet lists the fixture announcement and the demo's "Notifications are off in the demo" line |
| `testLooksPickerSwitchesPalette` | Choosing "Blockbuster" changes the Home background colour identifier to `look-blockbuster` |
| `testReportSendsToOwner` | Typing in Report and sending shows "Sent to the owner" |
| `testSettingsSessionsAndHomeNetwork` | Settings shows one "current" device row and the Home network toggle |
| `testConsoleOpenInDemo` | The "Console (demo)" tab exists and lists the eight screens; Paywall is absent |
| `testConsoleRequestsTriage` | Requests shows fixture rows; swiping Acquired moves a row to the acquired filter |
| `testConsoleInboxMarksRead` | Inbox shows the fixture message; Mark read clears the unread badge |
| `testMemberRoleHidesConsole` | With `--demo-role shared`: no Console tab; no "Make someone a playlist" door; no "for whom" picker |
| `testDynamicTypeAccessibility3` | With `-UIPreferredContentSizeCategoryName UICTContentSizeCategoryAccessibilityXL` (`.accessibility3` is `accessibilityExtraLarge`): Home, Results and WantedList render without a truncated headline (`label.isHittable` for each heading identifier) |
| `testNoBannedWordsInAccessibilityTree` | Walks every screen above and asserts no label contains a word from §10.3 |

`InfoPlistTests.testLocalNetworkUsageDescriptionPresent` is an `AppTests` unit test (hosted by the App target, so
`Bundle.main` is the app; in `AppUITests` it would read the test runner's Info.plist), not a UI test: it asserts
`Bundle.main.infoDictionary` carries `NSLocalNetworkUsageDescription`, `NSCameraUsageDescription` and a
`BGTaskSchedulerPermittedIdentifiers` entry equal to `AppIdentity.refreshTaskIdentifier` (Phase 4 gate, ADR §6 item
6).

Phase 4 runs `testWelcomeShowsThreeDoors`, `testHomeGreetingAndReels`, `testSearchTitleRendersFilmCards`,
`testSearchCheekyShowsBrushOff`, `testSearchTitleMissingShowsGuesses`, `testSelectionBarAddsToPlaylist`,
`testMemberRoleHidesConsole`, `testDynamicTypeAccessibility3` (Home and Results only in that phase),
`testNoBannedWordsInAccessibilityTree` and the `AppTests` case above; Phase 5 adds `testDetailSheet` (Detail is a
Phase 5 screen, 07 §5), `testPlaylistsListAndItems`, `testWantedListSections`, `testBellShowsNotes`,
`testLooksPickerSwitchesPalette`, `testReportSendsToOwner`, `testSettingsSessionsAndHomeNetwork` and extends the two
walks to the new screens; Phase 7 adds `testConsoleOpenInDemo`, `testConsoleRequestsTriage` and
`testConsoleInboxMarksRead`. The human device steps per phase (a real Plex sign-in, a sandbox push, a forced
`BGAppRefreshTask`, a sandbox purchase) are in 07-build-phases.md and are not UI tests (ADR §6 item 9).

## 12. GAP index for this document

1. GAP: `--demo-role shared` and `DemoServerClient(role:)` are this document's additions for the member-role UI test;
   no server or fixture change; the Phase 3 prompt names them so the builder invents no second mechanism.
2. Closed: 03-server-api.md §6.4 now lists `api.v1.push.devices.delete.json`, `api.v1.auth.session.delete.json` and
   `api.v1.auth.sessions.delete.json` (all `204`, body `null`) among its 77 rows; Phase 1 records the two session
   files and Phase 2 the device file.
3. GAP: `GET /reports/gaps` has a fixture and a Kit type (`GapReport`) in 03 §6.4 but no Console screen in the ADR's
   list consumes it; the Kit decodes it for the contract test and no screen calls it in v1.

Everything else here is defined by the ADR, design-A, 02-architecture.md or 03-server-api.md, or is a presentation
decision that changes no shape and no rule.
