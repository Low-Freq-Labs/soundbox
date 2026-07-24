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

ARGS=(
  --windowed
  --name Soundbox
  --noconfirm
  --clean
  --osx-bundle-identifier com.lowfreqlabs.soundbox
  --collect-all pyrekordbox
  --collect-all sqlcipher3
  --collect-submodules sqlalchemy
  --collect-all yt_dlp
  --collect-all PIL
  --collect-all soundfile
  --collect-all zstandard
  --collect-submodules PySide6.QtMultimedia
  --add-binary "build/native/libsoundbox_deck.dylib:native"
)

[ -f assets/Soundbox.icns ] && ARGS+=(--icon assets/Soundbox.icns)

./.venv/bin/pyinstaller "${ARGS[@]}" soundbox_app.py

# Re-sign the finished bundle with the stable self-signed identity so macOS remembers
# TCC grants (USB + Desktop file access) across rebuilds instead of re-prompting.
# Done here rather than via PyInstaller's --codesign-identity because PyInstaller adds
# --timestamp (needs the network) and --options=runtime (hardened runtime), which fail
# with a local self-signed cert. A stable signature — no timestamp, no hardened runtime
# — is exactly what TCC persistence needs. No -v on find-identity: a self-signed cert is
# untrusted (CSSMERR_TP_NOT_TRUSTED), which the -v filter hides; codesign uses it fine.
if security find-identity -p codesigning 2>/dev/null | grep -q "$SIGN_IDENTITY"; then
  echo "Signing dist/Soundbox.app with: $SIGN_IDENTITY"
  codesign --force --deep --timestamp=none --sign "$SIGN_IDENTITY" dist/Soundbox.app
  codesign --verify --deep --strict dist/Soundbox.app && echo "Signature verified."
else
  echo "NOTE: '$SIGN_IDENTITY' not found — leaving ad-hoc (macOS will re-prompt for USB/"
  echo "      Desktop permission on each rebuild). Create the cert to make it stick."
fi

echo
echo "Built: dist/Soundbox.app"
echo "First launch: right-click the app → Open (unsigned, Gatekeeper)."
