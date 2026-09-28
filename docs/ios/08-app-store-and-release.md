# 08 App Store and release

Purpose: everything Apple, in the order the human owner does it. The builder cannot enrol, create records, upload
keys, set prices or answer App Review, and must not pretend to; this document names each of those steps, where it
happens, what value it produces and which phase depends on it, so the builder can ask for a value by name and the
owner can supply it. It also fixes the release-facing artefacts the builder does produce in Phase 9 (10-decision-record.md
§7): the privacy manifest, the banned-word gate, the review notes, the screenshots and the version scheme. Apple facts
not in the ADR or the constraints brief are marked (verified: how) or (unverified); the owner re-checks every
(unverified) item on the App Store Connect page it names before relying on it.

Read with: 00-README.md, 01-product-and-decisions.md, 04-ios-app.md, 05-push-relay.md, 07-build-phases.md,
09-security.md, 10-decision-record.md, prompts/phase-9.md.

## 1. The human-only checklist, in order

Each row is a step the owner performs. "Value" is what the step yields and where the builder expects it; the five
fill-in values (`<AppName>`, bundle identifier, `<org>`, `<developer-domain>`, Team ID) are the ones 00-README.md §5
puts in the Phase 0 prompt. Nothing in this table is done by the builder.

| # | Step | Where | Value produced, and where it goes | Needed by |
|---|---|---|---|---|
| 1 | Enrol in the Apple Developer Program as an individual or organisation; wait for approval | developer.apple.com/programs | Team ID (10 characters). Goes to `RELAY_TEAM_ID` and `APNS_TEAM_ID` (05-push-relay.md §9) and to the AASA `appID` (05 §4.8) | Phase 0 (signing on the macOS runner), Phase 6 |
| 2 | Decide the App Store name (not "PlexGet", 10-decision-record.md §4 "App Store name") and the bundle identifier (reverse-DNS, for example `<tld>.<developer-domain>.<appname>`; cannot be changed after the first upload (unverified)) | none yet; the owner writes both into the Phase 0 prompt | `AppIdentity.displayName`, `AppIdentity.bundleIdentifier` (04-ios-app.md §3.1); `APNS_TOPIC`, `APPLE_BUNDLE_ID` on the relay | Phase 0 |
| 3 | Register the App ID with capabilities Push Notifications and Associated Domains | Certificates, Identifiers & Profiles → Identifiers | The App ID. Background Modes is not a portal capability; it is the `UIBackgroundModes` Info.plist key (`fetch` only; `remote-notification` is not needed for alert pushes, §2.1) that `project.yml` already sets (04-ios-app.md §1) (unverified that no portal action is needed) | Phase 0 (the entitlements file exists from Phase 0) |
| 4 | Create the App Store Connect app record: platform iOS, name from step 2, primary language English (UK), bundle ID from step 3, SKU (any stable string, for example the bundle ID) | App Store Connect → Apps → + | The App Apple ID (numeric). Goes to `APPLE_APP_APPLE_ID` on the relay (05 §5.1) | Phase 6 |
| 5 | Create an APNs authentication key: Keys → +, tick Apple Push Notifications service (APNs), download the `.p8` once (it cannot be downloaded again (unverified: from memory; the portal's own warning at download time is the check)), note the Key ID | Certificates, Identifiers & Profiles → Keys | The `.p8` file contents go to the relay's secret store as `APNS_KEY_P8` or a mounted file at `APNS_KEY_PATH`; the Key ID to `APNS_KEY_ID` (05 §9). The key never touches this repository, the server image, `config.yaml` or an owner's machine (constraints brief fact 2). One key serves sandbox and production (unverified) | Phase 6 |
| 6 | Sign the Paid Applications agreement and complete Tax and Banking; until this is active, products cannot be sold and may not load in the sandbox (unverified) | App Store Connect → Business (Agreements, Tax, and Banking) | Nothing for the builder; without it Phase 7's sandbox purchase cannot pass | Phase 7 |
| 7 | Create the subscription group `Owner` and, in it, `owner.yearly` (Product ID exactly `owner.yearly`, duration 1 year) and `owner.monthly` (`owner.monthly`, 1 month); place `owner.yearly` and `owner.monthly` at the same subscription level, since they buy the same service (unverified: the owner reads the level help text in App Store Connect; if they differ, a switch between them is an upgrade or downgrade rather than a crossgrade); localise both in English (UK); set prices (**owner confirms**, 10-decision-record.md §4); Family Sharing off for both | App Store Connect → the app → Subscriptions | Product IDs match `AppIdentity.productIDs` and `RELAY_PRODUCT_IDS=owner.yearly,owner.monthly` (05 §9); the group name matches `AppIdentity.subscriptionGroup`. Each subscription needs a localised display name and description, a Review Information screenshot of the Paywall showing that product (taken on the owner's device from the Phase 7 build, since demo mode never shows the Paywall, 04 §7) and a review note; the group needs a localised display name; the status column must read "Ready to Submit" (unverified: the owner re-checks the Subscriptions page's status column before submission) | Phase 7 |
| 8 | Add an introductory offer to `owner.yearly` only: type Free, duration 7 days, all countries. Each subscription may carry its own offer and a customer is eligible for one per group (10-decision-record.md §5 item 11); `owner.monthly` gets none | The `owner.yearly` product page → Introductory Offers | The `.storekit` configuration in `App/Store/Products.storekit` mirrors it exactly so the Phase 7 tests match the store | Phase 7 |
| 9 | Set the Billing Grace Period to 16 days (the options are 3, 16 or 28, 10-decision-record.md §5 item 20), for all renewals | App Store Connect → the app → Subscriptions → Billing Grace Period (app-level setting) | Must equal `RELAY_GRACE_DAYS=16` (05 §9); if the owner ever changes one, the other changes in the same day | Phase 7 |
| 10 | Enter the App Store Server Notifications V2 URL for production and for sandbox: `https://push.<developer-domain>/v1/apple/notifications` in both fields; choose Version 2 | App Store Connect → the app → App Information → App Store Server Notifications | The relay must already answer `GET /v1/health` at that host, or Apple's test notification fails; a TEST notification is requested with the App Store Server API `POST https://api.storekit.apple.com/inApps/v1/notifications/test` (sandbox: `api.storekit-sandbox.apple.com`), authorised by a JWT signed with an In-App Purchase key created under App Store Connect → Users and Access → Integrations → In-App Purchase (not the APNs key); the builder adds `scripts/request-test-notification.py` to `<AppName>-relay` using the App Store Server Library's `AppStoreServerAPIClient.request_test_notification()` and `get_test_notification_status()`, with the key path, key id and issuer id taken from environment variables and never committed (verified: Apple "Enabling App Store Server Notifications" and "Request a Test Notification", 2026-09-22). GAP: 05-push-relay.md and 07 §5 do not list this script; it is a Phase 6 relay deliverable | Phase 6 (deploy) then Phase 7 |
| 11 | Apply to the App Store Small Business Program if eligible (15 % commission from the first year; the net after UK VAT is nearer £17-18 on £24.99, 10-decision-record.md §4) | developer.apple.com/app-store/small-business-program | None for the builder | Before launch |
| 12 | Publish a privacy policy page and a terms-of-use page at stable URLs on `<developer-domain>` (the content is drafted by the builder in Phase 9 from §4 and 09-security.md; the owner publishes it). Apple's standard EULA may be used for terms by linking `https://www.apple.com/legal/internet-services/itunes/dev/stdeula/` (unverified) | The owner's web host; App Store Connect → App Information (privacy policy URL) and → the version page (EULA) | `AppIdentity.privacyPolicyURL`, `AppIdentity.termsURL`; both appear on the Paywall (04 §6 Paywall) and in the metadata (guideline 3.1.2) | Phase 7 (Paywall), Phase 9 |
| 13 | Fill in the App Privacy questionnaire from §4.1 | App Store Connect → the app → App Privacy | Must agree with `PrivacyInfo.xcprivacy` (§4.2); a mismatch is a review finding | Phase 9 |
| 14 | Set the age rating (no objectionable content; the app shows film titles and artwork the owner's Plex holds; answer the questionnaire honestly, the tiers are 4+, 9+, 13+, 16+ and 18+ (12+ and 17+ were retired in 2025); expect 4+ or 13+ depending on the answers about user-generated messages (verified: Apple Developer News, July 2025)) | The version page → Age Rating | None | Phase 9 |
| 15 | Prepare the demo Plex server and the reviewer Plex account (§3) | The owner's hardware and plex.tv | The address, invite link and credentials for §3.2 | Phase 9 |
| 16 | Upload the Phase 9 build, add screenshots (§3.4), the description, keywords, subtitle and review notes (§3.2); run TestFlight (§7); on the version page, under In-App Purchases and Subscriptions, add `owner.yearly` and `owner.monthly` so they are reviewed with this version (the first subscriptions of an app can only be submitted with a version) (unverified: re-check the version page); submit | App Store Connect → TestFlight, then the version page | The submission | Phase 9 |

Values the builder needs before Phase 6 and cannot invent: Team ID, bundle identifier, App Apple ID, `<developer-domain>`,
the `.p8` key and its Key ID (set as relay secrets by the owner, never pasted into a prompt), and the prices (shown only
by StoreKit at run time; not in code).

## 2. The review submission

### 2.1 Guidelines that apply, and how the app satisfies each

Guideline text was checked by design-A §9 (verified: developer.apple.com/app-store/review/guidelines, fetched 2026-09-21);
numbering is re-checked by the owner on submission day (10-decision-record.md §5 item 15).

| Guideline | One sentence on how the app satisfies it |
|---|---|
| 2.1 App Completeness | Demo mode is on the Welcome screen with no account (04 §7), the demo server is reachable over HTTPS during review (§3) and the reviewer account is in the notes (§2.2). |
| 2.1 (crashes, placeholders) | No placeholder text ships: the Phase 9 banned-word review treats a placeholder as a blocker (01 §2.6), and TestFlight's 14 days show no reproducible crash (01 §7). |
| 2.3 Accurate Metadata | Screenshots come from demo mode on the submitted build (§3.4), the description says the app needs the owner's own server, and no feature is described that the build lacks. |
| 2.5.4 Background modes | Only `fetch` is declared, for `BGAppRefreshTask` polling of `GET /api/v1/changes`; alert pushes need no background mode and `remote-notification` is not declared because the relay never sends `content-available` (05 §7.4). |
| 3.1.1 In-App Purchase | The Console unlocks only through StoreKit 2 `Transaction.currentEntitlements`; there is no code, link, or mention of the free web dashboard anywhere in the app (10-decision-record.md §4 "What is paid"). |
| 3.1.2 Subscriptions | The Paywall states the trial length, the price billed afterwards, the period, the auto-renewal terms and links to the privacy policy and terms (04 §6 Paywall); the same links are in the metadata; the ongoing value is the relay-delivered push and the Console. |
| 4.2 Minimum Functionality | It is a native SwiftUI app with native navigation, offline demo, push, background refresh and StoreKit; nothing is a wrapped web page (design-A §9). |
| 4.5.4 Push Notifications | Permission is asked in context after the first successful sign-in with one sentence saying what it is for; push carries arrivals, requests, messages and announcements, never marketing (10-decision-record.md §4 "Notification permission"). |
| 4.8 Login Services | The exemption applies: the app is a client of a specific third-party service and the person signs in to Plex directly, so Sign in with Apple is not offered (constraints brief fact 5). |
| 5.1.1 Data Collection and Storage | `NSLocalNetworkUsageDescription` explains the probe of the addresses plex.tv listed (10-decision-record.md §6 item 6); the privacy label and manifest declare only what transits the relay (§4); nothing is collected for tracking. |
| 5.1.1(v) Account deletion | The app creates no account of its own; "Sign out everywhere" (`DELETE /api/v1/auth/sessions`), "Delete my relay data" (`DELETE /v1/server-keys/<id>`) and the member's handle deletion from Settings remove everything the developer or the server holds (10-decision-record.md §4 "Relay data deletion"). |
| 5.1.2 Data Use and Sharing | Nothing is shared with third parties; the relay forwards a notification title and body to APNs and stores neither (05 §1). |
| 5.2.1 Intellectual Property | No Plex mark in the name or icon, "for Plex" only in the subtitle once the owner has read Plex's brand guidelines (unverified whether descriptive use is permitted), and a "not affiliated with Plex" line in the description. |
| 5.2.3 Audio/Video Downloading | The app never mentions downloading and never calls `/wanted/export`, `/export*`, `/sonarr/*` or `/radarr/*` (02-architecture.md §7); the wanted list is a wish list, and the CI grep gate (§5) proves the copy. |
| 5.2.5 Apple Products | The name, icon and copy do not resemble Apple's products or services; the plan cites 5.2.1 for third-party marks and this row only so the owner does not confuse the two (10-decision-record.md §5 item 15). |
| 1.2 User-Generated Content | Messages and announcements are text inside a closed household; the owner can retire announcements, members can only message the owner, and "Something wrong?" plus the published contact address is the report path (design-A §9). |

### 2.2 Review notes text to paste

Paste the following into App Review Information → Notes, with the placeholders filled. Keep it under Apple's field
limit (4,000 characters (unverified)). Use the reviewer account of §3.2, not the owner's personal account.

```
WHAT THIS APP IS
<AppName> is a companion for a household that shares one Plex Media Server. Members sign in with their
own Plex account, ask for films and series, keep a wish list, build playlists in their own Plex account,
and are told when something they asked for is on the shelf. The person who runs the server (the owner)
also uses it; the owner's Console tab is an auto-renewable subscription. Every household feature is free.

The app talks to a small open-source server the owner runs at home beside Plex. It is a client of that
server; nothing is hosted by us except a push relay that forwards notifications to APNs.

THE APP DOES NOT
- fetch, copy or play any media, and has no file access to the Plex library;
- link to, mention or control any acquisition tool; the wish list is a list of titles, nothing more;
- create accounts; sign-in is plex.tv's own PIN flow in ASWebAuthenticationSession;
- unlock anything by any means other than In-App Purchase.

HOW TO TRY IT WITHOUT AN ACCOUNT
Welcome screen -> "Try the demo". Demo mode is offline, uses public-domain titles, and opens the Console
marked "(demo)" so every screen can be seen. A banner says nothing there is real.

HOW TO TRY IT AGAINST A LIVE SERVER
1. Welcome screen -> "Sign in with Plex". Plex username: <reviewer username>  Password: <password>
   (two-factor authentication is off on this account).
2. The app will not find the server on your network. Tap "I have an invite" and enter this address:
   https://<demo host>   (or paste this link into the Notes app and tap it there, which opens the app
   directly: https://push.<developer-domain>/join#<invite fragment>).
3. Home appears. Search for "Charade" to see results and add it to the list. The wish list, Bell,
   Playlists and Settings are all live against the demo server.
4. This account is the demo server's owner, so the Console tab appears with the subscription screen.
   A sandbox purchase of "Owner yearly" (7-day trial) opens it; the demo server then receives push
   through our relay. "Restore purchases" and "Delete my relay data" are in Console -> Server.

PERMISSIONS
- Local Network: at home the app probes the local addresses plex.tv lists for the person's own Plex
  server on port 5050 to find the companion server. It never scans. Away from home it is not needed.
- Notifications: asked once, after sign-in, so the person hears when a title arrives.
- Background App Refresh: when the owner has no subscription the app polls the server for changes.

CONTACT
<supportEmail>. The demo server is monitored and will stay up throughout review.
```

The builder writes this text into `docs/release/review-notes.md` in `<AppName>-ios` (07 §5 Phase 9) with the placeholders left as
`<...>`; the owner fills them at submission. The file is covered by the banned-word gate (§5).

## 3. The demo server

### 3.1 What it is

A Plex Media Server holding only public-domain films, shared with the reviewer account, with a PlexGet instance in
front of it over HTTPS (10-decision-record.md §2 "Demo environment"). It is a demo, not a product component; it can be
the relay's VM or a machine at the owner's home behind Tailscale Funnel (06-install-and-setup.md). The reviewer's phone
talks to plex.tv for sign-in and to the PlexGet instance for everything else; the Plex Media Server itself needs no
Remote Access, because artwork is proxied through `/wanted/thumb` (owner-api.md §2 `/wanted/thumb`).

### 3.2 How it is prepared

1. Create a dedicated plex.tv account (the "demo owner"), with two-factor authentication off so the reviewer can
   sign in (unverified: whether Plex enforces 2FA in the PIN flow; off is the safe setting). This account owns the
   demo Plex Media Server and is the `plex.token` holder in the PlexGet instance's `config.yaml`, so its role is
   `owner` (`api/plex_auth.py:174-175`). It is the account given in §2.2.
2. Optionally create a second account, share the server with it, and accept the share; it exercises the `shared`
   role. Not required for review.
3. Load six to ten films whose public-domain status the owner has confirmed (the fixtures' titles, 03-server-api.md
   §6.2, are the natural set so the demo mode and the demo server agree; the owner verifies each title's status
   before loading, (unverified) for any given title).
4. Install PlexGet with the Phase 8 image, complete `/setup`, set `app.name` to something that is not "PlexGet",
   set `app.urls.remote` to the HTTPS address, and run the self-check.
5. Seed the state so the Console is not empty: three wanted rows (one `acquired`, two `wanted`, one of kind
   `show`), one unread message, one announcement, all with public-domain titles and without the banned words (§5).
6. Confirm from a phone off the LAN that "I have an invite" with the typed HTTPS address reaches Home, that
   `GET /api/v1/server` reports `native_signin: true` and `setup: false`, and that a sandbox purchase on the demo
   owner's device writes `data/relay.json` (Phase 7 gate).
7. Record the address, the invite link from the Console's Server screen, and the credentials in the review notes;
   the notes tell the reviewer to paste the link into the Notes app and tap it there, because a universal link typed
   or pasted into Safari's address bar loads the `/join` page (05 §4.8) instead of opening the app (unverified: from
   memory of universal-link routing).

### 3.3 Reachable during review

App Review may open the app days after submission and more than once. The owner keeps the machine awake
(deployment-and-security.md §2.3), checks `https://<demo host>/health` and `https://push.<developer-domain>/v1/health`
daily from submission until approval, and does not rotate the reviewer password or the Funnel address in that window.
If the demo server must move, the owner updates the notes and replies in Resolution Center before the reviewer looks
again. A reviewer who cannot reach it will reject under 2.1 (§9).

### 3.4 Screenshots from demo mode

Screenshots are taken from the submitted build in demo mode, never from a live household (2.3, privacy). The builder
adds a UI test target run `AppUITests/Screenshots.swift` that launches with `--demo`, visits Home, Results, Detail,
WantedList, Bell, Playlists, Console Requests, Console Inbox and Console Server, and attaches a screenshot of each;
the owner exports the attachments from the `.xcresult` or runs `xcrun simctl io booted screenshot` on the same screens.
Device sizes are whatever App Store Connect's upload page requires for the submission (unverified: the required sizes
change; read the page). Rules: Dynamic Type at the default size; light and dark both acceptable; the "Demo — nothing here
is real" banner may stay, and if the owner wants it hidden, GAP: a `--screenshots` launch argument that hides only the
banner in demo mode is not defined in 04-ios-app.md §7 and is added there with a UI test if used. No screenshot shows
the Paywall (demo never shows it, 04 §7) or a real person's name.

## 4. Privacy

### 4.1 Nutrition label answers

The app has no analytics, no ads and no third-party SDK (constraints brief fact 14). Data the phone sends to the owner's
self-hosted server is sent to a server the developer does not operate or receive from; the label describes what reaches
the developer's relay. The mapping of the relay's fields onto Apple's categories below is the conservative reading and
is (unverified); the owner confirms it against App Store Connect's category help before answering.

| App Store Connect question | Answer |
|---|---|
| Do you or your third-party partners collect data from this app? | Yes |
| Device ID | Collected: the APNs device token, stored by the relay under a random handle (05 §3.1). Purpose: App Functionality. Not linked to identity. Not used for tracking |
| Purchase History | Collected: the subscription's `originalTransactionId` and `expires_at`, held by the relay to issue a server key (05 §4.4). Purpose: App Functionality. Not linked to identity. Not used for tracking |
| Other User Content | Collected in transit only: a notification title and body (a film title, or a household message's first line) pass through the relay to APNs and are not stored (05 §1). Purpose: App Functionality. Not linked to identity. Not used for tracking |
| Every other category (contact info, location, contacts, health, financial, browsing, search history, identifiers other than the above, usage data, diagnostics) | Not collected. Search text goes only to the owner's server; crash data comes only from Apple's TestFlight and App Store crash reports, which Apple collects and need not be declared (unverified) |
| Tracking | No |
| Privacy policy URL | `AppIdentity.privacyPolicyURL` from checklist step 12 |

The privacy policy page says, in plain words: what the relay holds (the table above), that it holds no Plex token,
session token or server address (05 §1), that the pin hand-off bounds what a home-network observer could capture to
the pin's life (10-decision-record.md §5 item 1), that the owner's server is the owner's, and how to delete relay data
from the app.

### 4.2 `PrivacyInfo.xcprivacy` contents

The file lives at `App/Resources/PrivacyInfo.xcprivacy` from Phase 0 (04 §1) and is completed in Phase 9. Key names
and reason codes are Apple's (unverified: from memory; the builder checks each against Apple's "Privacy manifest
files" and "Describing use of required reason API" pages and stops if a code is not listed there).

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSPrivacyTracking</key><false/>
  <key>NSPrivacyTrackingDomains</key><array/>
  <key>NSPrivacyCollectedDataTypes</key>
  <array>
    <dict>
      <key>NSPrivacyCollectedDataType</key><string>NSPrivacyCollectedDataTypeDeviceID</string>
      <key>NSPrivacyCollectedDataTypeLinked</key><false/>
      <key>NSPrivacyCollectedDataTypeTracking</key><false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array><string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string></array>
    </dict>
    <dict>
      <key>NSPrivacyCollectedDataType</key><string>NSPrivacyCollectedDataTypePurchaseHistory</string>
      <key>NSPrivacyCollectedDataTypeLinked</key><false/>
      <key>NSPrivacyCollectedDataTypeTracking</key><false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array><string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string></array>
    </dict>
    <dict>
      <key>NSPrivacyCollectedDataType</key><string>NSPrivacyCollectedDataTypeOtherUserContent</string>
      <key>NSPrivacyCollectedDataTypeLinked</key><false/>
      <key>NSPrivacyCollectedDataTypeTracking</key><false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array><string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string></array>
    </dict>
  </array>
  <key>NSPrivacyAccessedAPITypes</key>
  <array>
    <dict>
      <key>NSPrivacyAccessedAPIType</key><string>NSPrivacyAccessedAPICategoryUserDefaults</string>
      <key>NSPrivacyAccessedAPITypeReasons</key><array><string>CA92.1</string></array>
    </dict>
  </array>
</dict>
</plist>
```

Required-reason API rules for the builder, so the manifest stays this short:

| API category | Used? | Rule |
|---|---|---|
| User defaults | Yes, the keys in 02-architecture.md §4.2 | Reason `CA92.1` (access from the app itself only; 02 §4.2 marks it unverified; confirmed here in Phase 9) |
| File timestamps | No | The caches in 02 §4.3 carry their fetch time inside the JSON; the app never reads `contentModificationDate`, `st_mtime` or `attributesOfItem`. If a later change needs it, add `C617.1` and say why in the PR |
| System boot time | No | Elapsed time in the Kit uses injected clocks (`ContinuousClock` in the app); no `systemUptime`, `mach_absolute_time` or `ProcessInfo.systemUptime` call exists |
| Disk space | No | Never queried |
| Active keyboards | No | Never queried |

Verification: (1) the grep of prompts/phase-9.md step 3 over `App/` and `PlexGetKit/` for the required-reason API
symbols is the pre-upload check; (2) after the first TestFlight upload the owner waits for App Store Connect's
processing email and confirms it carries no ITMS-91053 (Missing API declaration) or ITMS-91056 (Invalid privacy
manifest) warning; either warning is a Phase 9 blocker that comes back to the builder as one row naming the category.
The Organizer's "Generate Privacy Report" only aggregates bundled manifests and is used to confirm the manifest was
bundled, not that it is complete (verified: Apple "Privacy manifest files", 2026-09-22). `PlexGetKit` has no manifest of its own because it is a local package compiled into
the app and imports only Foundation (04 §2).

## 5. Banned words and the CI grep gate

The list, from constraints brief fact 8 and 04-ios-app.md §10.3, applied to every user-facing string, fixture, store
metadata file and review note, in any letter case and as a substring unless noted:

| Word | Matching | Say instead |
|---|---|---|
| `download` | substring (catches "downloads", "downloaded") | "get", "add", "it's here", "on the shelf"; the App Store is "the App Store" |
| `torrent`, `usenet`, `nzb`, `magnet` | substring | never needed |
| `indexer`, `tracker` | substring | never needed |
| `sonarr`, `radarr` | substring | never needed; the routes are never called (02 §7) |
| `seed` | whole word and `seeds`, `seeded`, `seeding` | "fixed data", "prepared data" in tests; "starter" in copy |
| `release group` | phrase | never needed |
| `Plex` in the app name, icon or store name | human check, not grep | the in-app header uses the server's `app_name` |

Where "download" is unavoidable: only in Apple's own interface, which is not in the repository — the App Store's Get
button, TestFlight's Install, the iOS Settings "Offload App" text, and the StoreKit purchase sheet. The app's "Update
needed" screen says "Get the update on the App Store" (02 §6.3). No exception is coded into the gate; the gate has no
allowlist.

`scripts/banned-words.sh` in `<AppName>-ios` (04 §1), run by `kit.yml`, `app.yml` (default paths) and the Phase 9
review (`scripts/banned-words.sh App PlexGetKit docs/release store`):

```sh
#!/bin/sh
# Fails when a review-facing word appears anywhere it could ship. No allowlist by design.
set -eu
cd "$(dirname "$0")/.."
# arguments override the default paths; the server repository passes tests/fixtures/api and dashboard/setup.html.
# The default adds store/ only once Phase 9 has created it, so kit.yml and app.yml pass from Phase 0 on.
if [ $# -gt 0 ]; then paths="$*"; else paths="App PlexGetKit"; test -d store && paths="$paths store"; fi
for p in $paths; do test -e "$p" || { echo "banned-words: no such path: $p" >&2; exit 2; }; done
exclude='--exclude-dir=.git --exclude-dir=.build --exclude-dir=docs --exclude=banned-words.sh --exclude=CLAUDE.md'
fail=0
for w in download torrent usenet nzb magnet indexer tracker sonarr radarr "release group"; do
  if grep -rIin $exclude -- "$w" $paths; then fail=1; fi
done
if grep -rIinwE $exclude -- 'seed(s|ed|ing)?' $paths; then fail=1; fi
exit $fail
```

Arguments override the default path list, which is how the server repository runs it over `tests/fixtures/api`,
`dashboard/setup.html` and `docs/DEPLOYMENT.md` (07 §4.1, Phase 1, 2 and 8 gates); a named path that does not exist
is exit `2`, never a vacuous pass, which is why the default list adds `store` only when the directory exists (it
appears in Phase 9; the workflows call the script with no arguments from Phase 0). `--exclude-dir=docs` skips `docs/` only during recursion; a `docs/release` path given as an
argument is searched (checked with GNU grep 3.11). It also runs in this repository's CI over `tests/fixtures/api/`
(03 §6.2), because the fixtures are the demo data.
`Localizable.xcstrings` is inside `App/Resources/`, so every translation is covered (04 §9). The UI test
`testNoBannedWordsInAccessibilityTree` (04 §11) is the runtime half: it walks every demo screen and fails on a label
containing any word above, which catches text assembled at run time. Names inside code (`PlexGetKit`, `X-Plex-Product`)
are not user-facing and are not in scope; `docs/` and `CLAUDE.md` are excluded because they state the rule.

## 6. Name and icon rules

1. The App Store name is the owner's, is not "PlexGet" and does not contain "Plex" (constraints brief fact 9; 10-decision-record.md
   §4). It is used for `CFBundleDisplayName`, `X-Plex-Product` and the push topic only (design-A §9); inside the app
   the header is the server's `app_name`.
2. The subtitle may say "for Plex" only after the owner has read Plex's brand guidelines and found descriptive use
   permitted (unverified); otherwise the subtitle describes the job ("Ask for films, hear when they arrive").
3. The icon contains no Plex chevron, no Plex wordmark and no imitation of Plex's amber-on-black scheme (unverified as
   a brand rule; avoided regardless), no Apple product imagery (5.2.5), no film-studio marks and no text. One
   1024×1024 PNG without alpha in `Assets.xcassets` (unverified: from memory that App Store Connect rejects an icon with an
   alpha channel; the first upload is the check).
4. The description carries the line "Not affiliated with Plex, Inc. Plex is a trademark of Plex, Inc." (unverified
   wording; the owner adapts to the brand guidelines' own required form if one exists) and says the app needs the
   companion server the owner runs at home, with a link to `docs/DEPLOYMENT.md`.
5. Keywords do not include "Plex" unless step 2 permits it; they never include any word in §5.

## 7. TestFlight plan

Two households, one owner and at least two members each, for 14 days (01 §7; ADR §7 Phase 9). Internal testing (the
owner's own devices) runs first on every Phase 6 and Phase 7 build; external testing needs Beta App Review, which reads
the same notes as §2.2 (unverified: Beta App Review's exact requirements; give it the demo account anyway).

| Item | Rule |
|---|---|
| Builds | The Phase 7 gate build first, then the Phase 9 candidate; each is the exact archive that will be submitted; a TestFlight build is distribution-signed and Xcode sets `aps-environment` to `production`, so its pushes go through `api.push.apple.com` (05 §7.3; verified: Apple "Sending notification requests to APNs", 2026-09-22). Only a development-signed Debug build run from Xcode uses the sandbox host. The Phase 6 device step is therefore a push through the production host to the TestFlight build; a development-signed Xcode run on the device is the only way to exercise the sandbox host, and the owner does both and records in the PR the relay `environment` each device registered with |
| Purchases | TestFlight uses the sandbox environment: the trial and renewals run on the compressed sandbox clock and charge nothing (unverified: sandbox renewal timings). The relay must have the sandbox verifier enabled and `RELAY_ALLOW_SANDBOX_KEYS=1` (05 §5.1, §9) during the TestFlight period or server-key issuance fails for testers; the owner may set it to `0` after launch |
| Households | Each owner installs the Phase 8 image from a clean machine following `docs/DEPLOYMENT.md` only, with no help from the developer until they report being stuck; that is the abandonment measurement |
| What to observe, owner | The furthest step reached of: Docker installed; container answers `/health` not in setup; setup saved; `app.urls.remote` set and self-check passed; first member signed in; trial started; first arrival delivered (01 §7 "Owner abandonment"). Abandonment at "remote address" by more than one owner reopens the tunnel decision (10-decision-record.md §6 item 11) |
| What to observe, member | The step of 01 §6.1 they stopped at, if any (steps 3, 5, 6, 7); the Local Network prompt's timing and wording; whether the probe found the server at home without typing |
| Timings to record | Arrival on the relay path (under 60 s from the `events` row), on the webhook path (under 5 min), on the scheduler path (under 125 min), and on the polling path (next foreground), all per 01 §7 |
| Exchange failures | Any `POST /api/v1/auth/plex` answer other than `200` for a real person, by `code`, from the server log |
| Crashes | TestFlight crash reports; any crash that reproduces blocks submission |
| Feedback route | TestFlight's screenshot feedback plus the app's "Report to the developer" (04 §5 Report); the owner reads both daily |
| Exit | Both households reach "first member signed in" and "first arrival delivered", no reproducible crash, zero banned words in anything a tester saw, and the two Tailscale checks (10-decision-record.md §6 item 2) recorded |

## 8. Version and build numbering

| Value | Rule |
|---|---|
| `MARKETING_VERSION` → `CFBundleShortVersionString` | Always three components `MAJOR.MINOR.PATCH`, starting `1.0.0`, because the server's `min_app_version` is compared numerically against it (03 §1.8). Set in `project.yml`; changed only by a PR titled "Release x.y.z" |
| `CURRENT_PROJECT_VERSION` → `CFBundleVersion` | An integer that strictly increases across every upload of the app, whatever the marketing version. CI sets it on the command line (`xcodebuild … CURRENT_PROJECT_VERSION=$GITHUB_RUN_NUMBER`); `project.yml` holds `1` for local builds. Never reused, never decreased (unverified: from memory; the upload is the check) |
| The `device.app` string | `"<CFBundleShortVersionString> (<CFBundleVersion>)"`, for example `"1.0.0 (42)"` (03 §3 `device.app`; 05 §4.1 `app`); the `User-Agent` header is 03 §1.2's `<AppIdentity.product>/<version> (<build>; iOS <version>)` |
| Git tag in `<AppName>-ios` | `v1.0.0` on the commit that was archived; the tag message holds the build number and the server commit hash in `PlexGetKit/Tests/Fixtures/VERSION` (03 §6.3) |
| Server image tags | Independent: `ghcr.io/<org>/plexget:<tag>` from this repository's tags (06-install-and-setup.md); `BUILD` (`api/routes.py:80`) is shown, never compared (03 §1.8) |
| Relay version | The git sha in `GET /v1/health` (05 §4.7) |
| Raising `min_app_version` | Only when a response shape changed so that older apps cannot decode it; the server release note says which version and why; the app shows "update needed" (02 §6.3) |
| What's New text | Written by the owner, kept in `store/metadata/en-GB/release_notes.txt` so §5's gate covers it |

GAP: 04-ios-app.md §1 does not list a `store/` directory; Phase 9 adds
`store/metadata/en-GB/{name,subtitle,description,keywords,release_notes}.txt` to `<AppName>-ios` so the metadata is
versioned and grep-gated; the review notes live at `docs/release/review-notes.md` (07 §5 Phase 9). No server or
fixture change.

## 9. What to do on rejection

Rules: reply in Resolution Center within one working day; never argue guideline numbers, answer the reviewer's stated
problem; attach a screen recording when the reviewer could not find something; resubmit the same build unless a code
change is needed, in which case Phase 9 reopens with the change as one small PR and the gates rerun. The prepared
responses below are pasted with the placeholders filled and nothing invented.

| Rejection | Likely cause | Prepared response and action |
|---|---|---|
| 2.1 "We were unable to sign in / the server did not respond" | Demo server down, Funnel address changed, reviewer typed the address wrongly, 2FA on the account | Check §3.3 immediately and fix; reply: "The demo server at https://<demo host> is up (health checked at <time UTC>). Steps: Welcome → I have an invite → enter the address above → Sign in with Plex with <username>/<password>. Demo mode (Welcome → Try the demo) shows every screen offline. A screen recording is attached." |
| 2.1 "The app crashed" | A crash in a state TestFlight did not reach | Reproduce from the reviewer's device model and iOS version in the report; fix as one PR; new build with a higher build number; reply with the fix summary |
| 2.3.3 / 2.3.10 screenshots or metadata | A screenshot not from the app, or a mention of a platform or feature not in the build | Replace from §3.4; reply naming the replaced items |
| 3.1.1 "Unlocks features through a mechanism other than IAP" | The reviewer inferred the web dashboard from the description or the setup page | Reply: "The Console is unlocked only by the auto-renewable subscription through StoreKit. The app contains no link, code or reference to any other unlock. The self-hosted server's own web page is a separate product the app never mentions." Remove any description text that mentioned the web page |
| 3.1.2 "Subscription terms missing" | Price, period, trial or the two links absent from the Paywall or metadata | Verify the Paywall (04 §6) shows all five items and the metadata description carries the links; fix and reply with a screenshot |
| 4.2 "Minimum functionality / requires external server" | The reviewer judged the app useless without a server | Reply with the demo-mode path and that the server is a free open-source companion the person runs themselves, like other self-hosted companions; point to the live demo server |
| 4.8 "Offer Sign in with Apple" | The exemption was not recognised | Reply: "The app is a client for a specific third-party service (Plex) and users sign in to their Plex account directly, which guideline 4.8 lists as exempt. No other login service is offered." |
| 5.1.1 "Purpose string insufficient" | `NSLocalNetworkUsageDescription` or the notification explanation judged vague | Rewrite the string to name the action ("finds your household's companion server on this Wi-Fi") as one PR; reply with the new text |
| 5.1.1(v) "No account deletion" | The reviewer expects a delete-account button | Reply: "The app creates no accounts. Sign-in is the person's existing Plex account. Settings → Sign out everywhere ends every session on the server; Console → Server → Delete my relay data removes the only record our service holds, and Settings → Notifications removes the device's handle." Attach a recording |
| 5.2.1 "Uses Plex's mark" | "for Plex" in the subtitle, or Plex named in keywords | Remove "for Plex" from the subtitle and keywords and resubmit metadata only; reply that the app is an unaffiliated client and the description states so |
| 5.2.3 "Facilitates downloading" | The reviewer read "wish list" or "arrived" as acquisition | Reply: "The app fetches, copies and plays no media and has no access to any media file or acquisition tool. A wish list entry is a title and a name; the notification says a title is now on the household's own Plex server, which the owner administers as they always have. The app's strings contain no reference to downloading and the source is scanned for such words in CI." Attach the grep output and the Console Requests screen |
| 2.5.4 "Background mode not used" | Fetch was doubted | Reply describing `BGAppRefreshTask` polling `GET /api/v1/changes` when the owner has no subscription; attach a recording of the forced refresh producing a local notification |
| "Guideline numbering or text differs from this table" | Apple revised the guidelines | The owner re-reads the cited section on the day, and the plan's wording follows Apple's, not this table (10-decision-record.md §5 item 15) |

After approval: the owner runs `scripts/request-test-notification.py` against production once (§1 row 10), watches the relay's
`apple_notifications` table for the first production notification, and only then announces the app to the two
TestFlight households as released.
