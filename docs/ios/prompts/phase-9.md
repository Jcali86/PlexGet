# Phase 9: Release

Machine: a Mac with Xcode 16 for the builder, plus the human owner for every App Store Connect step. Repository: `<AppName>-ios`. Branch: `phase-9-release` from `main`. Starts only when Phases 0 to 8 are merged, including Phase 8 in `<server-repo>` and the Phase 7 sandbox purchase recorded. `<server-repo>` is the PlexGet server checkout; the plan documents are under `<server-repo>/docs/ios/`.

## Read first

Read each file in full, in this order, before any other tool call.

1. `CLAUDE.md` (repository root).
2. `docs/build/phase-9.md` (this file).
3. `<server-repo>/docs/ios/07-build-phases.md` §1, §2 and §5 "Phase 9", §6.
4. `<server-repo>/docs/ios/08-app-store-and-release.md`, all of it.
5. `<server-repo>/docs/ios/04-ios-app.md` §1, §7, §10.3, §11, §12.
6. `<server-repo>/docs/ios/10-decision-record.md` §2 ("Demo environment"), §4 rows "App Store name", "Monetisation", §5 items 15 and 20, §6 items 10 and 11.
7. `<server-repo>/docs/ios/02-architecture.md` §4.2, §4.3 (the required-reason API facts the manifest states).
8. `<server-repo>/docs/ios/09-security.md` §2.7, §10.
9. `<server-repo>/docs/ios/01-product-and-decisions.md` §3, §7.
10. `<server-repo>/docs/ios/00-README.md` §5, §6.
11. `docs/build/deviations.md` if it exists, and every `docs/build/walk-phase-5.md` row marked pending.

## Goal

Make the build submittable without changing a feature: run the banned-word review over every fixture, string and release document; complete `App/Resources/PrivacyInfo.xcprivacy` with its required-reason entries checked against Apple's pages; write the review notes, the store metadata files and the release checklist; take the screenshots from demo mode; run TestFlight with two households for fourteen days and record their reports, including any abandonment at the remote-reach step; and hand the owner every human-only App Store Connect step by name with "done" or "pending". The pull request is the record the owner submits from.

## Constraints

- No code change beyond copy in `Localizable.xcstrings`, the privacy manifest, the release documents, the `store/metadata` files and the screenshot UI test of 08 §3.4; a fixture edit is only a banned-word fix re-recorded through `<server-repo>`'s pytest and re-synced, never a hand edit.
- The privacy manifest's key names and reason codes are copied from Apple's "Privacy manifest files" and "Describing use of required reason API" pages read in this session, each recorded as (verified: <url>, <date>) in the pull request; a code that is not on the page is a stop, not a guess.
- The banned-word list of `CLAUDE.md` §6 applies to every string, fixture, screenshot, review note and metadata file, with no allowlist; "download" appears nowhere the builder writes, and "Update needed" says "Get the update on the App Store".
- No Plex mark in the app name, `CFBundleDisplayName`, the icon or the keywords; "for Plex" only in the subtitle and only after the owner has read Plex's brand guidelines; the description carries the "not affiliated" line of 08 §6 item 4.
- The builder never touches App Store Connect, the developer portal, TestFlight configuration or the demo Plex server; each is recorded as `pending` until the owner writes "done" (`CLAUDE.md` §8).
- Forbidden in this phase (07 §5 Phase 9): any code change beyond copy, the privacy manifest and the release documents; a fixture edit that is not a banned-word fix re-recorded through the server's pytest; any banned word or mention of the web dashboard in metadata or notes; a Plex logo or colour scheme in the icon; the builder touching App Store Connect.

## Steps

1. `git checkout main && git pull && git checkout -b phase-9-release`; `xcodegen generate`; run the Phase 7 gate once and confirm green; `scripts/sync-fixtures.sh <server-repo>` and confirm `PlexGetKit/Tests/Fixtures/VERSION` equals `git -C <server-repo> rev-parse main`.
2. Banned-word review: run `scripts/banned-words.sh App PlexGetKit docs/release` and `grep -rIinwE 'download|torrent|usenet|nzb|magnet|indexer|tracker|sonarr|radarr|release group|seed(s|ed|ing)?' App/Resources/Localizable.xcstrings PlexGetKit/Tests/Fixtures`. If the script ignores its path arguments (08 §5 lists fixed paths while the 07 gates pass paths), extend it to accept them; that is the one script change permitted here. A hit in a string is fixed in `Localizable.xcstrings`; a hit in a fixture is fixed in `<server-repo>` by changing the stubbed data in `tests/conftest.py`, running `venv/bin/pytest -q` there so the fixture is re-recorded, committing it, and re-syncing. Record every hit and its fix in the PR.
3. `App/Resources/PrivacyInfo.xcprivacy`: the content of 08 §4.2 (`NSPrivacyTracking` false, the three collected data types Device ID, Purchase History, Other User Content, each not linked and not for tracking with purpose App Functionality; `NSPrivacyAccessedAPITypes` with `UserDefaults` reason `CA92.1`), after reading Apple's two pages named in Constraints and confirming each key and code. Confirm by grep that `App/` and `PlexGetKit/` contain no `contentModificationDate`, `st_mtime`, `attributesOfItem`, `systemUptime`, `mach_absolute_time`, `ProcessInfo.systemUptime`, `volumeAvailableCapacity` or `activeInputModes`; a hit means a further category and reason, stated in the PR. Run `plutil -lint`.
4. `docs/release/review-notes.md`: the text of 08 §2.2 with placeholders left as `<...>` for the owner (reviewer username, password, demo host, invite fragment, `supportEmail`), the 5.1.1(v) paragraph naming "Sign out everywhere", "Delete my relay data" and the handle removal, and the closed-household paragraph for 1.2. Note: 08 §2.2 and §8 place this file at `store/review-notes.md`; 07 §5 Phase 9 is binding for phase deliverables, so the notes live at `docs/release/review-notes.md` and `store/` holds only the metadata below.
5. `store/metadata/en-GB/{name,subtitle,description,keywords,release_notes}.txt` (08 §8 GAP): `name` is `<AppName>` as the owner confirmed it; `subtitle` describes the job unless the owner has approved "for Plex"; `description` states that the app needs the companion server the owner runs at home with a link to `docs/DEPLOYMENT.md` in `<server-repo>`, carries the "not affiliated" line and the privacy policy and terms links (3.1.2); `keywords` contain no banned word and no "Plex" unless approved; `release_notes` is the owner's What's New text or "First release." Extend `scripts/banned-words.sh`'s default paths to include `store` if Phase 0 left it out.
6. Version: `MARKETING_VERSION` `1.0.0` in `project.yml`; `CURRENT_PROJECT_VERSION` `1` locally and `$GITHUB_RUN_NUMBER` in `app.yml` on the archive job (08 §8); no other `project.yml` change.
7. Screenshots: add `AppUITests/Screenshots.swift` launching with `--demo --uitest`, visiting Home, Results, Detail, WantedList, Bell, Playlists, Console Requests, Console Inbox and Console Server and attaching a screenshot of each (08 §3.4); then, on a booted simulator of each size App Store Connect's upload page currently requires (the owner reads the page and tells the builder the sizes), run `xcrun simctl io booted screenshot docs/release/screenshots/<size>-<screen>.png` for the same screens with the demo banner visible and Dynamic Type at the default size. No screenshot shows Paywall or a real name. If the owner wants the banner hidden, the `--screenshots` argument is a GAP in 04 §7 and is added with a UI test only on the owner's instruction.
8. `docs/release/checklist.md`: one row per human step of 08 §1 (rows 1 to 16) plus the per-phase device steps, the demo server preparation of 08 §3.2 (seven steps), the Billing Grace Period of 16 days, the App Store Server Notifications V2 URL `https://push.<developer-domain>/v1/apple/notifications`, the privacy questionnaire of 08 §4.1, the guideline-number re-check (`10-decision-record.md` §5 item 15), the icon review (no Plex chevron, wordmark or amber-on-black scheme; 1024x1024 PNG without alpha), the App Store name and subtitle decision, and the two Tailscale checks; each "done: <date>" or "pending". The builder marks nothing "done" that it did not itself run.
9. Privacy policy and terms text: draft `docs/release/privacy-policy.md` from 08 §4.1's table and 09 §10 (what the relay holds, that it holds no Plex token, session token or server address, the pin hand-off bound, how to delete relay data from the app) for the owner to publish at `AppIdentity.privacyPolicyURL`; the terms may be Apple's standard EULA link (08 §1 row 12, unverified), which the owner decides.
10. HUMAN: the owner performs 08 §1 rows 1 to 16 as needed (Developer Program, App Store Connect record and bundle identifier, capabilities, the APNs key from Phase 6, products and the introductory offer from Phase 7, Billing Grace Period 16 days, the notifications URL, the Small Business Program, the privacy policy and terms URLs, the App Privacy questionnaire, the age rating, the demo Plex server and reviewer account, the App Store name and subtitle after reading Plex's brand guidelines), and marks each row of the checklist. Wait for the demo server row before step 11.
11. HUMAN: confirm from a phone off the LAN that the demo server and reviewer account work over HTTPS with the TestFlight build ("I have an invite" with the typed address reaches Home; `GET /api/v1/server` reports `native_signin: true`, `setup: false`), and record it in the checklist.
12. HUMAN: TestFlight with two households (one owner and at least two members each) for fourteen days per 08 §7; each owner installs the Phase 8 image from a clean machine using `docs/DEPLOYMENT.md` only; record per household the furthest owner step reached, the member steps, the four arrival timings of 01 §7, exchange failures by code, crashes, and every "Report to the developer" message, in `docs/release/testflight-report.md`. Abandonment at the remote-address step by more than one owner is written down as the trigger for reopening `10-decision-record.md` §4 "C's tunnel"; it changes nothing in this phase.
13. HUMAN: on a Mac with signing, run the `xcodebuild archive` line of the gate, generate the archive's privacy report in Xcode's Organizer, and confirm its API list equals the manifest's; a difference is a blocker and comes back to the builder as one row.
14. Run the builder's gate lines, commit `docs/release/` and `store/`, fill the pull request as under Deliver, and stop; the owner uploads, submits and answers App Review with 08 §9.

## Gate

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

Paste the full output of every gate command into the PR description. If any command fails, fix it; if you cannot, write docs/build/deviations.md and stop.

## Deliver

One pull request in `<AppName>-ios` from `phase-9-release`, labelled `mac-ci`, titled `Phase 9: Release`. The body carries the headings of `07-build-phases.md` §1.2 in order: `## Goal` (07's Phase 9 Goal paragraph), `## Changed` (07's Phase 9 deliverables table plus the `store/metadata` files, ticked or "not needed: <why>"), `## Gate output` (every command and its complete output, verbatim; the archive line under `## Not run here` when the session has no signing), `## Not run here`, `## Human steps` (every row of `docs/release/checklist.md` with "done: <date, by whom, result>" or "pending", the demo-server confirmation, the TestFlight reports linked, the privacy-report comparison), `## Deviations`, `## Forbidden` (each item with "confirmed absent" and the grep: `git diff main --stat -- App PlexGetKit | grep -v "Localizable.xcstrings\|PrivacyInfo.xcprivacy\|Screenshots.swift\|Fixtures"` printing nothing, `grep -rIin "dashboard" store docs/release` printing nothing, the banned-word run over `store`). Record the Apple pages read for the manifest as (verified: url, date). Then stop and wait for the owner, who tags `v1.0.0` on the archived commit, uploads and submits.

## Do not

- Do not change a feature, a store, a view's behaviour, an endpoint builder or a `Models` type; the only code files this phase touches are `Localizable.xcstrings`, `PrivacyInfo.xcprivacy`, `AppUITests/Screenshots.swift`, `project.yml` for the version fields and, if needed, `scripts/banned-words.sh`'s path handling.
- Do not edit a file under `PlexGetKit/Tests/Fixtures/` by hand; a banned word in a fixture is fixed in `<server-repo>`'s stub data, re-recorded by pytest and re-synced.
- Do not write a privacy-manifest key or reason code from memory; each comes from Apple's page read in this session, and a code that is not there stops the phase.
- Do not write "download", "torrent", "usenet", "nzb", "indexer", "tracker", "sonarr", "radarr", "seed", "magnet" or "release group" in any string, note, metadata file or screenshot caption, and do not add an allowlist to the grep.
- Do not put "Plex" in the name, `CFBundleDisplayName`, the icon or the keywords, or "for Plex" in the subtitle, before the owner has written in the checklist that the brand guidelines permit it.
- Do not mention the web dashboard, a website purchase or any unlock path other than StoreKit in the review notes, the description or the release notes.
- Do not take a screenshot from a live household, of the Paywall, or with a real person's name visible; every screenshot comes from demo mode on the submitted build.
- Do not mark any App Store Connect, developer-portal, TestFlight or demo-server row as done, and do not fill the reviewer credentials into the notes; the owner does both.
- Do not raise `min_app_version` on the server or change `MARKETING_VERSION` away from `1.0.0` in this phase.
- Do not treat a TestFlight household's abandonment at remote reach as something to fix here; record it and stop.
