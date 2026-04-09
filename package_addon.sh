#!/usr/bin/env bash
# Creates novelviews_assets.zip ready to install in Blender.
# Run from the repo root: ./package_addon.sh
set -euo pipefail

OUT="novelviews_assets.zip"
rm -f "$OUT"

# Blender requires the zip to contain a folder whose name is the add-on module name.
# We create a temporary symlink so the zip entry is  novelviews_assets/__init__.py
TMP_DIR="$(mktemp -d)"
cp -r blender_addon "$TMP_DIR/novelviews_assets"
(cd "$TMP_DIR" && zip -r - novelviews_assets) > "$OUT"
rm -rf "$TMP_DIR"

echo "Created $OUT — install via Blender > Edit > Preferences > Add-ons > Install"
