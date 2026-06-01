# macOS Signing And Notarization

Local Story Archive macOS releases should be signed and notarized before distribution. Unsigned downloaded `.dmg` files can show macOS Gatekeeper warnings such as “the app is damaged and can’t be opened,” even when the build itself is valid.

## Required Apple Account

- Active Apple Developer Program membership.
- Developer ID Application certificate.
- App-specific password for the Apple ID used for notarization.
- Apple Team ID.

## GitHub Secrets

Add these repository secrets before running the desktop installer workflow:

- `APPLE_CERTIFICATE`: Base64-encoded `.p12` Developer ID Application certificate.
- `APPLE_CERTIFICATE_PASSWORD`: Password used when exporting the `.p12` certificate.
- `APPLE_SIGNING_IDENTITY`: Optional Developer ID Application identity, for example `Developer ID Application: Your Name (TEAMID)`. If omitted, CI tries to detect the first imported Developer ID Application identity.
- `APPLE_ID`: Apple ID email used for notarization.
- `APPLE_PASSWORD`: App-specific password for `APPLE_ID`.
- `APPLE_TEAM_ID`: Apple Developer Team ID.

## Export Certificate

1. Open Keychain Access on macOS.
2. Select the Developer ID Application certificate and its private key.
3. Export as a `.p12` file with a strong password.
4. Base64 encode the exported file:

```bash
base64 -i DeveloperIDApplication.p12 | pbcopy
```

5. Save the copied value as the `APPLE_CERTIFICATE` GitHub secret.

## Build Behavior

- When all signing and notarization secrets are present, the GitHub Actions desktop workflow imports the Developer ID certificate into a temporary keychain, then asks Tauri to sign and notarize macOS artifacts.
- The desktop build also signs the bundled Python backend executable before Tauri packages the `.app`; unsigned nested executables can make an installed app fail to open even when the DMG was created successfully.
- Tagged macOS release builds fail when required Apple signing/notarization secrets are missing, because unsigned downloaded `.dmg` files are commonly blocked by Gatekeeper.
- Non-tag macOS builds can still run unsigned for CI diagnostics, but they emit warnings and should not be published to users.
- Release artifacts upload the signed/notarized `.dmg` only; the intermediate `.app` bundle is not uploaded.
- Windows builds are not affected by these Apple-specific secrets.

## Local Developer Preview Workaround

For a locally trusted unsigned preview build, users can remove the quarantine flag after copying the app to Applications:

```bash
xattr -dr com.apple.quarantine "/Applications/Local Story Archive.app"
```

This workaround is only for development/testing. Public releases should use signed and notarized artifacts.

## Troubleshooting Installed Apps

If the DMG opens but the copied app will not launch, check both the app bundle and bundled backend executable:

```bash
spctl -a -vvv -t install "/Applications/Local Story Archive.app"
codesign -dv --verbose=4 "/Applications/Local Story Archive.app"
codesign --verify --verbose=2 "/Applications/Local Story Archive.app/Contents/Resources/bin/local-story-archive-desktop-backend"
```

The app should be accepted as `Notarized Developer ID`, and the backend helper should verify successfully.
