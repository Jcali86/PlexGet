# Review notes: what the plan review changed, what it left open

Written 2026-09-22 after the review-and-fix pass over 00-09, ios-CLAUDE.md, relay-CLAUDE.md and prompts/. The
Architecture Decision Record (10-decision-record.md) was not under review and was not changed; where a document
contradicted it, the document was corrected. Read this before Phase 0; it is not a phase input and no prompt cites it.

## 1. What changed, by theme

| Theme | Change | Where |
|---|---|---|
| Fixtures and `Bundle.module` | The vendored fixtures stay at `PlexGetKit/Tests/Fixtures/` (ADR §2). They are a resource of the `PlexGetKit` library target, whose `path` is the package root (`path: "."`, `sources: ["Sources/PlexGetKit"]`, `resources: [.copy("Tests/Fixtures")]`), read with `subdirectory: "Fixtures"`; never a second copy, never a test-target-only resource. Phase 0's `SmokeTests` gates it | 07 §5 Phase 0; 04 §2.9; prompts 0, 3, 5, 7, 9 |
| Sign-in exchange | `401` codes `session_revoked` / `session_expired` / `plex_token_dead` fixed in 03 §1.4a; pin codes are `pin_invalid` and `pin_not_linked`; `pin.id` and `client_id` validated before plex.tv; a pin id is exchangeable once (`exchanged_pins`, `test_pin_exchanged_once`); `pins/link` is form-encoded with `X-Plex-Product: Plex SSO`; silent re-exchange only over `https://` and never on `session_revoked` | 03 §1.4a, §3.2; 02 §2.1, §6.2; 09 §3, §5; prompts 1, 3, 4 |
| Sessions | `token_hashed` column with a two-bucket lookup; a stored hash never authenticates; `DELETE /api/v1/auth/session` sets `revoked_at`; `email`/`thumb` live in `UserDefaults`, not the Keychain | 02 §3.1, §4.2; 03 §3.3-§3.4; 09 §3, §11 |
| Fixtures count | 03 §6.4 is 77 rows (three `204` files and `api.v1.push.test.rate_limited.json` included); wrapper shapes `SessionList`, `ThemeList`, `NoteList`, `WantedPage` (03 §2.13); `.serverFault` everywhere | 03 §2.13, §6.4; 07 Phase 2 gate; prompts 2, 3 |
| Pairing and trust | A tapped invite link confirms, only an in-app QR scan skips the card; invites for an unknown or already-paired `m` are refused; a probed or typed address outside the stored set confirms before any bearer request; `urls` replace the stored set only when received over a stored URL | 02 §2.3, §2.4; 03 §1.1; 04 §2.5, §2.6, §4.1; 09 §2.3a, §2.6; prompts 3, 4 |
| Push and relay | TestFlight is a production APNs send; `aps-environment` read from the embedded profile; `UIBackgroundModes` is `fetch` only; APNs response table per Apple; `relay.wsgi:app`; body caps 64 KB / 8 KB; `rotate: true` only in three cases with a pending-key retry; sign-out always deletes the relay handle; a push whose `srv` is not the paired server is neither routed nor recorded; a 60-per-actor-per-hour relay budget in `api/relay.py`; `relay/admin.py rotate-all-keys` and `scripts/request-test-notification.py` built in Phase 6 | 05 §2, §4.3, §7, §8, §9, §12; 02 §2.12, §2.14, §2.16; 04 §3.3, §3.4; 09 §2.2, §2.6, §12; 08 §7; prompts 2, 6, 7 |
| Loopback door and setup page | The door applies only when `flask.host` is loopback; the webhook key check lives in `guard()`; `/setup` needs the printed setup code until Save; `GET /setup/ping` is nonce-gated; setup `POST`s are JSON-only with `X-Requested-With: setup`; `app.urls.local` and the webhook URL come from the owner's `Host` header | 06 §5.2, §5.4, §7, §8; 09 §7; prompts 2, 8 |
| App target hygiene | `openURL` allow-list (Report, Paywall, Welcome, Bell, Pairing, Settings) with fixed arguments; `AppIdentity.appStoreURL`; `NSCameraUsageDescription`; `InfoPlistTests` in `AppTests`; `PendingNotifier` until Phase 6; `LocalNotification`/`Destination` declared in `Protocols/Notifier.swift` from Phase 0; `lint.sh` owns the endpoint-path grep, `banned-words.sh` is the 08 §5 listing and adds `store` to its default paths only once the directory exists | 04 §1, §2.7, §3.1, §3.6, §11; 07 §4.2; 08 §5; 09 §11; ios-CLAUDE §4; prompts 0, 4, 5 |
| StoreKit | Only `.subscribed` and `.inGracePeriod` are active; `serverKeyJWS()` uses `Transaction.latest(for:)`; the `.storekit` configuration is on the test action only, with an `App-StoreKitLocal` scheme for manual runs; the sandbox purchase comes from the `App` scheme or TestFlight | 01 §2.2; 04 §3.3; 07 Phase 7; prompts 7 |
| Release | Age tiers, subscription level, TEST notification via the App Store Server API, ITMS-91053/91056 post-upload check, "Ready to Submit", `docs/release/review-notes.md`, `store/metadata/en-GB/` | 08 §1, §4.2, §5; 07 Phase 9; prompts 9 |
| Environment | Swift toolchain on Linux (swift.org tarball or the `swift:6.0-noble` Docker wrapper, else `## Not run here` plus `kit.yml`); GitHub Free macOS-minute budget; `config.py:21` is the `CONFIG_PATH` line | 00 §3; 07 §2, §4; prompts 0, 3 |

## 2. Findings the fixers rejected, and why

1. Move the vendored fixtures to `PlexGetKit/Sources/PlexGetKit/Fixtures/` (raised against 04, 07 and the prompts):
   the ADR §2 fixes `PlexGetKit/Tests/Fixtures/`. The prompts had applied the move; this pass reverted them to the
   ADR path and the library-target layout above, so all documents agree. The underlying feasibility question is
   real and is gated in Phase 0 (a deviation note if no layout gives the library a `Bundle.module`).
2. A second copy of the fixtures under `Demo/`: 04 §2.9 forbids it; same resolution as item 1.
3. Restrict a sandbox server key to `sandbox` devices at the relay: TestFlight devices register as `production`
   while TestFlight purchases are sandbox, so the rule would break the Phase 7 and Phase 9 TestFlight flows. Replaced
   by a WARNING log, a digest count and the `RELAY_ALLOW_SANDBOX_KEYS` switch (05 §5.1, §9).
4. `--call relay:create_app` with a zero-argument `create_app()` reading the environment, and a module-level `app`
   in `relay/__init__.py`: both would make `import relay` require secrets in tests. Replaced by `relay/wsgi.py`.
5. `device_id` composite uniqueness and a `relay_handle` unique index: the ADR fixes `sessions.device_id TEXT UNIQUE`
   and 03 §3.8 fixes the `native_devices` upsert on `device_id`. The takeover concern (an upsert keyed on a
   client-chosen value) stands and needs an ADR-level decision; see §4.
6. Wrapper type names `Sessions`/`Themes`/`Announcements`: `Themes` is a Kit module and `Sessions` a screen name;
   03 §2.13's `SessionList`/`ThemeList`/`NoteList`/`WantedPage` were used instead.
7. The webhook-key check repeated inside `plex_webhook()`: one check in `guard()` avoids divergence (06 §8).
8. "Relay handle kept across sign-out when 'keep me signed in to Plex' is chosen" (09 wording): 02 §2.16 step 2
   ends the relay registration on every sign-out; 09 and the prompts now say the same.
9. `#if DEBUG` / `sandboxReceipt` as the APNs environment rule: wrong for a Release build signed with a development
   profile; the embedded-profile read stays.

## 3. Unverified claims that remain, and the phase that checks each

| Claim | Checked in |
|---|---|
| SwiftPM accepts a library target with `path: "."`, explicit `sources` and a resource under `Tests/Fixtures`; `Bundle.module` on Linux holds `Fixtures/VERSION` (07 §5 Phase 0; 04 §2.9) | Phase 0 `swift test` (`SmokeTests`); deviation note before Phase 3 if not |
| swift.org tarball file name; `swift:6.0-noble` image tag; `Xcode_16.2.app` on the `macos-15` runner; XcodeGen version; GitHub Free macOS-minute budget (07 §2, §4) | Phase 0 gate output records and pins them |
| plex.tv v2 pins: a strong pin links via `PUT /api/v2/pins/link`; `authToken` readable on a second read; whether the pin JSON carries a creation time (ADR §6 item 1; 09 §5; prompts/phase-1 step 6) | Phase 1 HUMAN step, written to `docs/build/gates.md` |
| `Product.SubscriptionInfo.Status` / `RenewalState` semantics and the `SKTestSession` API names (04 §3.3; 07 Phase 7) | Phase 7 build and `StoreTests`; the builder confirms against the SDK |
| The embedded `embedded.mobileprovision` layout and `aps-environment` read; TestFlight registering `production` (04 §3.4; prompts/phase-6 step 17) | Phase 6 device step records both values |
| APNs: hosts and ports, header names, `apns-collapse-id` limit, `thread-id`, the response table (05 §7); App Store Server Library package, module, verifier constructor, root certificates, notification field names (05 §5); `httpx.Client` thread safety; the AASA shape (05 §4.8) | Phase 6 `docs/verified.md` before `relay/apns.py` and `relay/entitlements.py` are written (ADR §6 items 4 and 5) |
| The `BGTaskScheduler` `_simulateLaunchForTaskWithIdentifier:` command (07 Phase 6) | Phase 6 HUMAN step records the command that worked |
| `CIFilter.qrCodeGenerator()` (04 §4.2) | Phase 7 build |
| Tailscale install, `funnel --bg`, `tailscale serve`, `cloudflared`, Docker Desktop `host-gateway`; Funnel on the Personal plan and the forwarded headers (06 §6; ADR §6 item 2) | Phase 8 vendor-page checks and the HUMAN Funnel step |
| GHCR workflow action majors and default `GITHUB_TOKEN` permissions (06 §1; prompts/phase-8 step 5) | Phase 8 `image.yml` run |
| Privacy manifest key names and reason codes (`CA92.1`, `C617.1`); required screenshot sizes; App Store Connect field names, subscription levels, age tiers, icon alpha rule, `CURRENT_PROJECT_VERSION` handling; Beta App Review requirements; sandbox renewal timings (08 §1, §3.4, §4.2, §7, §8) | Phase 9 reads of Apple's pages, the upload processing email and the App Store Connect pages |
| `.local` names under `NSAllowsLocalNetworking`; ATS key names other than `NSAllowsLocalNetworking`; the `ThisDeviceOnly` accessibility class semantics (09 §3, §4) | Phase 4 `ATSTests`, `KeychainTests` and the device sign-in |
| Xcode 15 string-catalogue plural variation (04 §9) | Phase 4 build |
| Whether Plex's brand guidelines permit "for Plex" in the subtitle (00 §5; 08 §6) | Owner, before Phase 9 |
| Fly.io command names, Litestream, hosting prices (05 §9, §10) | Phase 6 deployment; not gated |

## 4. Owner decisions still open

1. The App Store name (`<AppName>`), the bundle identifier, `<org>`, `<developer-domain>`, the two prices and the
   support/privacy/terms placeholders: the Phase 0 fill-in block (00 §5). Nothing starts without them.
2. Whether "for Plex" may appear in the subtitle, after reading Plex's brand guidelines (00 §5; 08 §6).
3. The `plex_token` fallback if gate 1 shows a claimed pin cannot be re-read (07 Phase 1 HUMAN step; 09 §5): the
   builder stops with a deviation note and the owner decides.
4. Whether `POST /api/v1/push/devices` and `sessions.device_id` should be made robust against a client-chosen
   `device_id` colliding across users (rejected finding 5 above). The ADR fixes the columns; changing them is an ADR
   amendment. Nothing in v1 is blocked by leaving it.
5. Whether to keep `RELAY_ALLOW_SANDBOX_KEYS=1` after the TestFlight period (05 §5.1, §9; 08 §7).
6. Whether the demo banner may be hidden for screenshots (`--screenshots`, 04 §7 GAP; prompts/phase-9 step 7).
7. Whether the Console's Sessions screen needs an owner-wide `DELETE /api/v1/auth/sessions?all=1` (09 §12 GAP 4)
   and whether the owner should be able to delete another member's `native_devices` row (09 §12 GAP 5); both are
   recorded, not built.
8. Reopening the tunnel decision if more than one TestFlight owner abandons at the remote-address step (ADR §6
   item 11; 08 §7); a Phase 9 observation, not a Phase 9 change.

## 5. Changed since the review

The server gained a library index (`scanner/library_index.py`) that searches ask before Plex. It changes no
response shape the app decodes. Two things the builder should know: a `title_missing` search answer can now
arrive with `translated_by: "title"` and empty `matches` when TMDb names the film exactly (03 §4, the search
table), and the Phase 0 test harness in this repository already exists (07 §5 Phase 0 and prompts/phase-0.md
carry a note saying what to check rather than create). The new routes `GET /index/status` and `POST
/index/sync` are owner-tier console routes the app does not call.
