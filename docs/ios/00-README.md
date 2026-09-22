# The PlexGet iOS build plan: read this first

This plan tells one model, Claude Opus running in Claude Code, how to build a native iOS app for a PlexGet
server, the push relay that app needs, and the server changes and install artefacts that make both possible. It
is an operating procedure, not a wish list: every phase names its files, its gate commands and its forbidden
actions, and the builder is expected to stop rather than guess. The human owner reads this file, confirms the
decisions in §5, performs the steps in §6, and then runs the builder one phase at a time as §3 describes. The
architecture is fixed by the Architecture Decision Record, reproduced verbatim as 10-decision-record.md; this
file summarises and points, it does not decide.

Read with: 10-decision-record.md (binding), 07-build-phases.md (the phase gates), ios-CLAUDE.md and
relay-CLAUDE.md (the standing rules in full), 08-app-store-and-release.md (the human-only steps).

## 1. What this plan is and is not

It is the complete instruction set for building what 10-decision-record.md §1 and §2 decide: the existing Flask
server in this repository becomes the app's backend behind a versioned `/api/v1` surface with bearer sessions;
the app is a native SwiftUI client of it, with its platform-independent core in a Swift package, `PlexGetKit`,
that builds and tests on Linux; the developer runs exactly one cloud component, a push-only relay holding the
APNs key; remote reach is the owner's job, guided by the setup page with Tailscale Funnel as the recommended
path; the server ships as a published Docker image with a first-run setup page.

It is not a design discussion. Design A won and the record grafted named items from Designs B and C onto it
(10-decision-record.md §1, §3); the rejected shapes are not reopened by this plan or by the builder. It is not
a product roadmap beyond v1: items marked "deferred" or "cut" in 10-decision-record.md §4 (Plex Home profiles,
hosted smart search, the tunnel, App Attest, the Notification Service Extension, Bonjour, `PUT
/api/v1/owner/notifications`) are out of scope for every phase. It is not a substitute for the human steps in
§6: no document here can enrol in the Developer Program, upload an APNs key or submit for review.

The server it modifies has no tests today (`CONTRIBUTING.md:46`; deployment-and-security.md §9 lists the manual
steps the documentation currently expects), runs Werkzeug's development server in one process (`main.py:10-14`,
deployment-and-security.md §1) and authenticates only by cookie (`api/routes.py:941-951`,
deployment-and-security.md §4.4). Those three facts are why Phases 1, 2 and 8 exist.

## 2. Document map

| File | Contents | Reader |
|---|---|---|
| 00-README.md | This file: map, operating procedure, owner decisions, glossary | both |
| 01-product-and-decisions.md | What the app does for the household and the owner, what is paid, what is free, what is deferred | both |
| 02-architecture.md | The three components, the data that crosses each boundary, sign-in, pairing, push and fallback | builder |
| 03-server-api.md | Every `/api/v1` endpoint and every existing route the app calls, with shapes, errors and the routes never called | builder |
| 04-ios-app.md | `PlexGetKit` modules, the app target's feature folders, the four protocols, screens and navigation | builder |
| 05-push-relay.md | The `push-relay` service: endpoints, tables, quotas, APNs, App Store Server Notifications V2 | builder |
| 06-install-and-setup.md | `Dockerfile`, `docker-compose.yml`, `install.sh`, the GHCR workflow, setup mode and the setup page | builder |
| 07-build-phases.md | Phases 0 to 9 with files, definition of done, gate commands, forbidden actions and human steps | both |
| 08-app-store-and-release.md | Human-only steps, App Store Connect configuration, review notes, demo server, TestFlight | owner |
| 09-security.md | Threat model, token handling, the loopback door, rate limits, what the relay never sees | builder |
| 10-decision-record.md | The ADR, verbatim; the only source of names, numbers and phase order | both |
| ios-CLAUDE.md | Standing rules for the `<AppName>-ios` repository; copied there as `CLAUDE.md` in Phase 0 | builder |
| relay-CLAUDE.md | Standing rules for the `<AppName>-relay` repository; copied there as `CLAUDE.md` in Phase 0 | builder |
| prompts/phase-0.md … prompts/phase-9.md | One self-contained prompt per phase, pasted by the owner | both |

Where two documents disagree, 10-decision-record.md wins; where a research citation in any document disagrees
with the record, the record wins and the document says so with a "Note:". A missing endpoint, field or rule is
marked `GAP:` in the document that needs it, and the builder adds it server-side with tests before using it.

## 3. How the owner runs the builder

1. **Machine.** Phases 0, 1, 2, 3 and 8 are Linux phases and run anywhere Claude Code runs, including the
   remote environment. Phases 4, 5, 7 and 9 need a Mac with Xcode 16 because the app target cannot be built on
   Linux (constraints brief, fact 13). Phase 6 is split: the relay on Linux, `PushBridge` and `Background` on a
   Mac. Phase 0 is Linux with one Mac step, the simulator build. The machine for each phase is stated in
   10-decision-record.md §7 and repeated at the top of each prompt.
2. **One phase per session.** Start a fresh Claude Code session in the repository the phase names, paste the
   whole of `prompts/phase-N.md` as the first message, and nothing else. The prompt carries its own context;
   the repository's `CLAUDE.md` carries the standing rules. Phase 0 copies `prompts/` into `<AppName>-ios` as
   `docs/build/phase-N.md` so later Mac sessions find them (10-decision-record.md §2).
3. **The builder ends with a pull request** whose description contains the gate commands and their output
   pasted verbatim, matching CONTRIBUTING.md's "say in the pull request what you actually exercised"
   (`CONTRIBUTING.md:47`). Diffs are about one thing (`CONTRIBUTING.md:81`).
4. **Do not start Phase N+1 until Phase N's gate has passed and the owner has read the PR.** "Passed" means
   every command in 07-build-phases.md for that phase exited zero and, for Phases 4 to 7 and 9, the human
   device step named for that phase was done and recorded in the PR (10-decision-record.md §6 item 9). The
   only permitted overlap is Phase 8, which may run in parallel with Phases 4 to 7 (10-decision-record.md §7).
5. **When the builder stops** with a deviation note (§4), the owner reads the note, decides, edits the plan
   document the note names if the plan was wrong, and starts a new session with the same prompt plus one line
   saying what was decided.

## 4. Standing rules and deviations

The rules below are the summary; ios-CLAUDE.md and relay-CLAUDE.md are the full lists and are what the builder
is bound by in each repository. Server-side rules are in 03-server-api.md and 07-build-phases.md.

| Scope | Rule | Source |
|---|---|---|
| All | Use only endpoints, fields and names in 03-server-api.md and 10-decision-record.md; if one is missing, stop, add it server-side with pytest tests, update the document | constraints brief, "No invention" |
| All | On contradiction with reality, write the deviation note and stop; never improvise | constraints brief, "Stop on contradiction" |
| All | Never write "download", "torrent", "usenet", "Sonarr", "Radarr" or "indexer" in any user-facing string, fixture or metadata; never call `/wanted/export`, `/export*`, `/sonarr/*`, `/radarr/*` | constraints brief, fact 8; 10-decision-record.md §6 item 10 |
| All | Small diffs, one thing each; pytest for every server change | `CONTRIBUTING.md:81`; constraints brief, fact 17 |
| iOS | No third-party Swift dependencies; Swift 6 strict; iOS 17 minimum; XcodeGen from `project.yml`, never hand-edit `.pbxproj` | constraints brief, facts 11-13 |
| iOS | Tokens only in the Keychain with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`; never in `UserDefaults` or logs | constraints brief, fact 15 |
| iOS | No force unwraps, no `try!`, `os.Logger` not `print`, `URLRequest` built only in `Endpoints.swift`, `URLSession` only inside `ServerClient` and `PlexTV`; all copy in `Localizable.xcstrings` | 10-decision-record.md §2; constraints brief, "A CLAUDE.md" |
| iOS | `PlexGetKit` never imports `Security`, `StoreKit`, `UserNotifications`, `UIKit` or `CryptoKit`; platform code lives behind `SecretStore`, `Entitlements`, `Transport`, `Notifier` | 10-decision-record.md §2 |
| iOS | Persona: render `name`, `greeting`, `brush_offs`, `images`, `brush_off_moods`; never carry `voice` or `examples` | 10-decision-record.md §4, persona row |
| Relay | Never log a push body; never accept or store a Plex token, a session token or a server address; the `.p8` key is never in the repository | 10-decision-record.md §2, cloud paragraph |
| Server | Every `/api/v1` change ships with tests under `tests/` and regenerates `tests/fixtures/api/`; the cookie flow's tests keep passing | 10-decision-record.md §2, §4 fixtures row |

**Deviations.** When the plan and reality disagree (an endpoint returns a shape the Kit cannot decode, an Apple
or Plex behaviour differs from what a document states, a gate command cannot pass for a reason the plan did not
foresee), the builder writes `docs/build/deviations.md` in the repository the current phase is working in,
appends one entry, commits it on the phase branch, and stops. The entry has six lines in this order: the date;
the phase number and name; the document and section that made the claim; what was observed, with the command
or file that shows it; the source that settles it, marked (verified: how) or (unverified); the smallest change
to the plan that would resolve it. The builder does not make that change itself. The gates in
10-decision-record.md §6 items 1 and 4 say explicitly that the deviation note, not a workaround, is the
required output when they fail; the same applies to every other gate.

## 5. Decisions the owner confirms before Phase 0

These are the rows of 10-decision-record.md §4 marked "owner confirms". The plan builds to the default until
the owner says otherwise; changing a default after Phase 0 is a deviation for the affected phases.

| Decision | Default that applies meanwhile | Why it must be settled early |
|---|---|---|
| App Store name | No default exists. The plan writes `<AppName>` everywhere the name appears; the owner supplies a name that does not contain "Plex", and "for Plex" appears only in the subtitle and only after the owner has read Plex's brand guidelines (unverified whether descriptive use is permitted). The in-app name is always the server's `app_name` | `AppIdentity` (bundle display name, `X-Plex-Product`, push topic, invite domain), the bundle identifier and both repository names derive from it in Phase 0 |
| Price | Products are `owner.yearly` (default, 7-day introductory trial) and `owner.monthly`, Family Sharing off. The yearly worked example in the record is £24.99, which nets nearer £17-18 after UK VAT and the 15% small-business commission; no monthly figure is recorded | Phase 7's `.storekit` configuration and 08-app-store-and-release.md's App Store Connect steps carry the figures; the purchase screen must state the trial length and the price billed afterwards |
| Repositories | `<AppName>-ios` and `<AppName>-relay`, both private, under the owner's GitHub account; server changes and install artefacts in this public MIT repository | Phase 0 creates the skeletons and CI; the image name `ghcr.io/<org>/plexget` needs `<org>` |

Two further values are inputs rather than decisions and are needed by the same phases: `<org>` for the GHCR
image (Phase 8) and `<developer-domain>` for `push.<developer-domain>`, the invite universal link and the
apple-app-site-association file (Phase 6). The owner writes all five values into the Phase 0 prompt's
fill-in block before pasting it.

## 6. Human-only steps

The following cannot be done by the builder and are not attempted by it; 08-app-store-and-release.md lists
each with its App Store Connect location and the phase that depends on it. Apple Developer Program enrolment;
the App Store Connect app record and bundle identifier; capabilities (Push Notifications, Associated Domains,
Background Modes); the APNs key and its upload to the relay's secret store; the subscription group, products and
introductory offer; the Billing Grace Period set to 16 days so it matches the relay (10-decision-record.md §2);
the App Store Server Notifications V2 URL; the privacy policy URL; the demo Plex server and reviewer account;
screenshots and review notes; TestFlight with two households; the App Store name. Also human: the per-phase
device steps (a Plex sign-in on a device in Phase 4, a sandbox push to a TestFlight build and a forced
`BGAppRefreshTask` in Phase 6, a sandbox purchase in Phase 7) and the two checks with real accounts in
10-decision-record.md §6 items 1, 2 and 7 (pin linking, Funnel on a Personal plan, Plex Home users).

## 7. Glossary

| Term | Meaning in this plan |
|---|---|
| household | The people a Plex server is shared with, as plex.tv reports them through `account.users()` filtered to this server's `machineIdentifier` (`api/plex_auth.py:162-200`, deployment-and-security.md §5). They use the app free |
| member | One person in the household who is not the owner; their session has `role == "shared"` |
| owner | The plex.tv account holding `plex.token` in `config.yaml` (`api/plex_auth.py:174-175`). The only role that can open the Console and the only buyer |
| Console | The owner's tab in the app: Requests, Inbox, Announce, Discover, Overview, Notifications, Server, Sessions, Paywall. Opens only with an active entitlement |
| relay | `push-relay`, the developer's one cloud service on `push.<developer-domain>`. Holds the APNs key; turns `{handle, event, title, body, collapse_id, data}` from a server into an APNs push; never sees a Plex token, a session token or a server address |
| handle | The opaque value `POST /v1/devices` returns for an APNs token, stored by the server in `native_devices` and readable by the owner. Distinct from the device secret, which only the phone presents |
| server key | The credential `POST /v1/server-keys` issues against a verified StoreKit 2 transaction, bound to `originalTransactionId`; the server sends it as a bearer on `POST /v1/push`. Written to `data/relay.json`. Honoured until `expires_at` plus 16 days |
| pin hand-off | The default sign-in exchange: the app mints a pin under its own client identifier, links it to the signed-in account with `PUT https://plex.tv/api/v2/pins/link`, and sends `{"pin": {"id", "client_id"}}` to `POST /api/v1/auth/plex`; the server reads the pin as `check_pin()` does and discards the account token. Gated by 10-decision-record.md §6 item 1 |
| invite | The universal link `https://push.<developer-domain>/join#…` (also a QR code) carrying the server's machine identifier, local and remote URLs and name in the fragment, which never reaches the relay's web server |
| Funnel | Tailscale Funnel, `tailscale funnel 5050`: the recommended way for an owner to give the server a public HTTPS address. Whether the free Personal plan permits it is a gate (10-decision-record.md §6 item 2) |
| loopback door | The rule at `api/routes.py:1261-1265` that makes any request from `127.0.0.1` with no `X-Forwarded-For` the owner (deployment-and-security.md §4.3). Off in the image (`PLEXGET_LOOPBACK_OWNER=0`) and turned off by the setup page |
| setup page | `/setup`, served by `api/setup.py` when `config.py` finds no `config.yaml` and enters setup mode instead of `SystemExit(1)` (`config.py:116-126`) |
| fixtures | `tests/fixtures/api/*.json`, written by pytest from every `/api/v1` and household route, vendored under `PlexGetKit/Tests/Fixtures/` and decoded in full by `swift test`. The contract test between the two languages |
| gate | The commands and human steps a phase must pass before the next phase starts; listed per phase in 07-build-phases.md and taken from 10-decision-record.md §7. The open risks in §6 are also gates |
| deviation note | An entry in `docs/build/deviations.md` written when reality contradicts the plan, after which the builder stops (§4) |
| demo mode | `DemoServerClient` over the shared fixtures: no network, no Keychain writes, an open Console marked as demo. Reachable from the Welcome screen for App Review |
| Kit | `PlexGetKit`, the Foundation-only Swift package that builds and tests on Linux; everything that can be verified without a Mac lives there |
