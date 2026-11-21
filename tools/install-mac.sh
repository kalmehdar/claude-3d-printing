#!/bin/bash
# Install or update every tool this repo uses on a Mac, to the newest release.
#   bash tools/install-mac.sh            OrcaSlicer, Snapmaker Orca, OpenSCAD snapshot, Blender
#   bash tools/install-mac.sh --alpha    also put Snapmaker Orca's newest pre-release in ~/Applications (stable stays in /Applications)
set -euo pipefail
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }

brew update
for c in orcaslicer snapmaker-orca openscad@snapshot blender; do
  if brew list --cask "$c" >/dev/null 2>&1; then brew upgrade --cask --greedy "$c" || true; else brew install --cask "$c"; fi
done

if [ "${1:-}" = "--alpha" ]; then
  command -v gh >/dev/null || brew install gh
  # the newest pre-release (the stable one is already in /Applications from Homebrew)
  tag=$(gh api repos/Snapmaker/OrcaSlicer/releases --jq '[.[]|select((.draft|not) and .prerelease)][0].tag_name // empty')
  [ -n "$tag" ] || { echo "Snapmaker Orca has no pre-release right now"; exit 0; }
  dmg=$(gh api "repos/Snapmaker/OrcaSlicer/releases/tags/$tag" --jq '[.assets[].name|select(test("Mac.*\\.dmg$"))][0] // empty')
  [ -n "$dmg" ] || { echo "no Mac download in $tag"; exit 1; }
  tmp=$(mktemp -d); mnt=""
  trap '[ -n "$mnt" ] && hdiutil detach "$mnt" >/dev/null 2>&1; rm -rf "$tmp"' EXIT
  gh release download "$tag" -R Snapmaker/OrcaSlicer -p "$dmg" -D "$tmp"
  mnt=$(hdiutil attach -nobrowse -readonly "$tmp/$dmg" | awk -F'\t' '/\/Volumes\//{print $NF}')
  app=$(ls -d "$mnt"/*.app | head -1)
  mkdir -p ~/Applications && rm -rf ~/Applications/"Snapmaker Orca $tag.app"
  cp -R "$app" ~/Applications/"Snapmaker Orca $tag.app"
  echo "Snapmaker Orca $tag is in ~/Applications"
fi

echo
echo "Installed:"
for a in OrcaSlicer "Snapmaker Orca" OpenSCAD Blender; do
  v=$(defaults read "/Applications/$a.app/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo "missing")
  echo "  $a $v"
done
