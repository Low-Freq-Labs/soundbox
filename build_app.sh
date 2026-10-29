#!/usr/bin/env bash
# Build a standalone Soundbox.app (macOS) with PyInstaller.
set -e
cd "$(dirname "$0")"

# Stable self-signed code-signing identity. Signing every build with the SAME cert
# gives the app a stable identity, so macOS remembers TCC grants (removable-volume
# access) across rebuilds instead of re-prompting every time. Falls back to ad-hoc
# if the cert isn't present. Create it once via Keychain Access → Certificate
# Assistant → Create a Certificate ("Soundbox Self-Signed", Self-Signed Root,
# type Code Signing).
SIGN_IDENTITY="Soundbox Self-Signed"

# Compile the arm64 CoreAudio + Metal deck before PyInstaller collects it.
./build_native.sh
./build_cdj.sh

# No argument preserves the full personal build. Public is always explicit.
PROFILE="${1:-personal}"
case "$PROFILE" in
  personal|public) ;;
  *) echo "Usage: $0 [personal|public]" >&2; exit 2 ;;
esac
APP="dist/$PROFILE/Soundbox.app"
SOUNDBOX_BUILD_PROFILE="$PROFILE" ./.venv/bin/pyinstaller \
  --noconfirm --clean --distpath "dist/$PROFILE" --workpath "build/$PROFILE" Soundbox.spec

# Declare the same bundle-local key used by the pre-Qt entrypoint. AppKit's
# press-and-hold service must never claim C before Soundbox's momentary-CUE
# handler. This does not alter the user's global accent-entry preference.
/usr/libexec/PlistBuddy \
  -c "Delete :ApplePressAndHoldEnabled" \
  "$APP"/Contents/Info.plist >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy \
  -c "Add :ApplePressAndHoldEnabled bool false" \
  "$APP"/Contents/Info.plist

# Record the exact Git revision and whether the source tree contained local
# edits. These keys are covered by the final code signature. They describe the
# checkout at stamping time; they are not independent source-to-binary proof.
./.venv/bin/python scripts/app_provenance.py \
  --stamp "$APP"

# Re-sign the finished bundle with the stable self-signed identity so macOS remembers
# TCC grants (USB + Desktop file access) across rebuilds instead of re-prompting.
# Done here rather than via PyInstaller's --codesign-identity because PyInstaller adds
# --timestamp (needs the network) and --options=runtime (hardened runtime), which fail
# with a local self-signed cert. A stable signature — no timestamp, no hardened runtime
# — is exactly what TCC persistence needs. No -v on find-identity: a self-signed cert is
# untrusted (CSSMERR_TP_NOT_TRUSTED), which the -v filter hides; codesign uses it fine.
if security find-identity -p codesigning 2>/dev/null | grep -q "$SIGN_IDENTITY"; then
  echo "Signing $APP with: $SIGN_IDENTITY"
  codesign --force --deep --timestamp=none --sign "$SIGN_IDENTITY" "$APP"
else
  echo "NOTE: '$SIGN_IDENTITY' not found — signing ad-hoc (macOS will re-prompt for USB/"
  echo "      Desktop permission on each rebuild). Create the cert to make it stick."
  codesign --force --deep --timestamp=none --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"
echo "Signature verified."
./.venv/bin/python scripts/build_composition.py "$APP" --profile "$PROFILE"

echo
echo "Built: $APP"
echo "First launch: right-click the app → Open (unsigned, Gatekeeper)."
