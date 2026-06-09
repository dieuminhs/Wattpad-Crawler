# Release Notes

## 0.2.2 - Reader, Library, And Cookie Polish

This patch release collects the UI and reliability fixes made after 0.2.1, with a focus on safer cookie handling, clearer library navigation, and a more useful story information page.

### Highlights

- Fixed Wattpad cookie validation so setup can detect rejected cookies more reliably before archive work starts.
- Accepted common Wattpad cookie paste shapes in Setup, including raw token values, `token=...`, and full `Cookie:` headers.
- Changed Wattpad HTTP requests to use browser-shaped headers so working browser cookies are less likely to be rejected by API probes.
- Improved settings/setup polish around saved cookies, offline mode, and cookie removal feedback.
- Fixed Library story-card navigation so clicking a story opens story information first, while the cover Continue pill remains the chapter shortcut.
- Added a richer story information view with cover/placeholder, story metadata, description, tags, export links, and a clearer chapter list.
- Simplified the story information view into a Wattpad-like cover-first intro layout before actions and chapters.
- Added a clearer chapter-page `Back to story info` navigation button.
- Constrained reader images and figures to the reader width so large chapter images no longer overflow on macOS.
- Tightened the backup/restore controls so the actions, ZIP badge, and file picker read cleanly in the Config page.
- Restyled Job History into a contained table with readable timestamps, status badges, and a proper empty state.
- Fixed the Setup cookie label so the `token` word no longer renders smaller than the surrounding label text.
- Kept the release discussion notes for future web enhancement groups while excluding a separate primary Continue button and reader image display modes.

### Validation

- Focused web route suite passed: `94 passed`.
- Focused reader polish and web route suites passed: `99 passed`.
- Focused backup and web route suites passed: `98 passed`.

## 0.2.1 - Speed, Storage, And Desktop Release Fixes

This patch release improves long-story archive performance, reduces local SQLite storage growth, improves desktop distribution readiness, and polishes library/remove flows.

### Highlights

- Made comment fetching opt-in during normal story archiving to avoid hundreds of empty comment API requests on long stories.
- Added archive duration and SQLite size reporting to story completion progress/logs.
- Reduced duplicate SQLite paragraph storage and expanded archive compaction to reclaim existing duplicate paragraph text.
- Made EPUB output optional for archive health, so missing EPUB files no longer mark stories as warnings or repair-needed.
- Simplified Library story cards to a single cover overlay Continue button.
- Added an obvious **Bookmarked** badge on Library story cards so saved stories are visible outside the bookmarked filter.
- Replaced the browser-native remove confirmation with an in-app dialog that matches the app UI.
- Fixed removing a story so both archive databases are cleaned up; re-archiving a removed story now refetches chapter content instead of skipping stale `done` parts and producing empty chapters.
- Hardened archive skip logic so chapters are only skipped when existing DB or file content is actually present.
- Improved cookie/setup handling with encrypted saved cookies, clearer cookie removal, and better offline behavior.
- Added macOS signing/notarization support in the desktop build workflow when Apple Developer secrets are configured, including temporary keychain certificate import and release-time secret checks.
- Signed the bundled macOS desktop backend helper before packaging so installed apps can launch after Gatekeeper verification.
- Stopped uploading the intermediate macOS `.app` bundle; macOS release artifacts now publish the `.dmg` installer only.

### Validation

- Full unit suite passed: `392 passed`.

## 0.2.0 - Local Story Archive

This release focuses on making the app feel safer, more polished, and more dependable as a local-first desktop archive tool.

### Highlights

- Renamed the app to **Local Story Archive** for a clearer, safer user-facing identity.
- Added a first-run welcome wizard that explains acceptable use, archive location, and cookie setup.
- Added local backup and restore so archives can be exported to a portable `.zip` and safely restored later.
- Improved the offline reader with browser-local theme, typography, resume, and scroll-position preferences.
- Added global export style presets for future EPUB and standalone HTML output.
- Improved archive health and repair behavior so warning-only items are no longer incorrectly shown as needing repair.
- Fixed a macOS desktop startup race that could leave the app on a blank white window before the local backend was ready.
- Added GitHub Actions support for signing and notarizing macOS desktop builds when Apple Developer secrets are configured.

### Safety And Privacy

- Backups intentionally exclude `_config.toml` and the Wattpad cookie.
- Restore uses safe merge behavior and does not delete existing local stories.
- Reader preferences and resume state stay local to the browser/device.
- The desktop app continues to run against a local backend only; no cloud sync or credential export was added.
- macOS public release builds should be signed and notarized to avoid Gatekeeper warnings on downloaded `.dmg` files.

### Validation

- Full unit suite passed: `368 passed`.
- Desktop wrapper check passed: `cargo check --manifest-path src-tauri\Cargo.toml`.
- Desktop unit tests passed: `5 passed`.
