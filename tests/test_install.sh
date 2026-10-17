#!/usr/bin/env bash
# check() evaluates its condition later, so conditions are single-quoted
# shellcheck disable=SC2016
# Tests for install.sh. Works entirely in a temporary directory; never
# touches the real ~/.local. Run from anywhere:  tests/test_install.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Run install.sh with throwaway BIN_DIR/APP_DIR; $1 = install.sh path, rest = args
run() {
  local script=$1
  shift
  BIN_DIR="$BIN" APP_DIR="$APP" "$script" "$@" >"$TMP/out" 2>&1
}

echo "install.sh tests"

# 1. Fresh install, re-run, uninstall
BIN="$TMP/one/bin"; APP="$TMP/one/apps"
run "$REPO/install.sh"
check "install links audio-recorder" '[[ "$(readlink -f "$BIN/audio-recorder")" == "$REPO/audio_recorder_gui.py" ]]'
check "install links record-audio" '[[ "$(readlink -f "$BIN/record-audio")" == "$REPO/record_audio.sh" ]]'
check "menu entry written" '[[ -f "$APP/audio-recorder.desktop" ]]'
if command -v desktop-file-validate >/dev/null; then
  check "menu entry valid" 'desktop-file-validate "$APP/audio-recorder.desktop"'
fi
run "$REPO/install.sh"
check "re-run succeeds and stays linked" '[[ -L "$BIN/audio-recorder" ]]'
run "$REPO/install.sh" --uninstall
check "uninstall removes everything it made" '[[ -z "$(find "$TMP/one" -type f -o -type l)" ]]'

# 2. A user's own file at a target path is never replaced
BIN="$TMP/two/bin"; APP="$TMP/two/apps"
mkdir -p "$BIN" "$APP"
echo mine > "$BIN/record-audio"
printf '[Desktop Entry]\nName=Mine\n' > "$APP/audio-recorder.desktop"
run "$REPO/install.sh"
check "existing user file kept" '[[ "$(cat "$BIN/record-audio")" == mine ]]'
check "existing menu entry kept" 'grep -q "Name=Mine" "$APP/audio-recorder.desktop"'
run "$REPO/install.sh" --uninstall
check "uninstall leaves user file" '[[ "$(cat "$BIN/record-audio")" == mine ]]'
check "uninstall leaves user menu entry" 'grep -q "Name=Mine" "$APP/audio-recorder.desktop"'

# 3. Installed through a symlinked checkout, uninstalled from the real path
BIN="$TMP/three/bin"; APP="$TMP/three/apps"
ln -s "$REPO" "$TMP/repolink"
run "$TMP/repolink/install.sh"
run "$REPO/install.sh" --uninstall
check "symlinked checkout: uninstall removes links" '[[ ! -e "$BIN/audio-recorder" && ! -e "$BIN/record-audio" ]]'

# 4. BIN_DIR with a space and a quote produces a valid, quoted Exec line
BIN="$TMP/fo\"ur dir/bin"; APP="$TMP/four/apps"
run "$REPO/install.sh"
check "Exec path quoted" 'grep -qF "Exec=\"$TMP/fo\\\"ur dir/bin/audio-recorder\"" "$APP/audio-recorder.desktop"'
if command -v desktop-file-validate >/dev/null; then
  check "quoted menu entry valid" 'desktop-file-validate "$APP/audio-recorder.desktop"'
fi

echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
