#!/usr/bin/env bash
# Install (or remove) Audio Output Recorder for the current user.
#
#   ./install.sh              link commands into ~/.local/bin and add a menu entry
#   ./install.sh --uninstall  remove exactly what install.sh created
#
# Idempotent: re-running replaces its own links and menu entry in place.
# Anything at those paths that install.sh did not create is left untouched.
# Override locations with BIN_DIR / APP_DIR environment variables.

set -euo pipefail

# Physical path, so links compare equal however the checkout was reached
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
APP_DIR="${APP_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/applications}"
DESKTOP="$APP_DIR/audio-recorder.desktop"
MARKER="X-Installed-By=audio-recorder-install.sh"

# command name -> file in this repo
declare -A LINKS=(
  [audio-recorder]="audio_recorder_gui.py"
  [record-audio]="record_audio.sh"
)

# True if $1 is a symlink resolving to $2
links_to() {
  [[ -L "$1" && "$(readlink -f -- "$1")" == "$(readlink -f -- "$2")" ]]
}

# Quote a path for a .desktop Exec key (Desktop Entry spec: double quotes,
# with " ` $ \ escaped by a backslash)
desktop_quote() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//\`/\\\`}
  s=${s//\$/\\\$}
  printf '"%s"' "$s"
}

uninstall() {
  local name
  for name in "${!LINKS[@]}"; do
    if links_to "$BIN_DIR/$name" "$REPO/${LINKS[$name]}"; then
      rm -- "$BIN_DIR/$name"
      echo "removed $BIN_DIR/$name"
    elif [[ -e "$BIN_DIR/$name" || -L "$BIN_DIR/$name" ]]; then
      echo "left $BIN_DIR/$name alone: not created by this install"
    fi
  done
  if [[ -f "$DESKTOP" ]] && grep -qx "$MARKER" "$DESKTOP"; then
    rm -- "$DESKTOP"
    echo "removed $DESKTOP"
  fi
}

install() {
  local name dest missing=0 line
  for dep in ffmpeg python3 setpriv; do
    command -v "$dep" >/dev/null || { echo "missing dependency: $dep" >&2; missing=1; }
  done
  python3 -c "import tkinter" 2>/dev/null || { echo "missing dependency: python3-tk" >&2; missing=1; }
  [[ $missing -eq 0 ]] || { echo "install the packages above (e.g. sudo apt install ffmpeg python3-tk util-linux), then re-run" >&2; exit 1; }

  mkdir -p "$BIN_DIR" "$APP_DIR"
  for name in "${!LINKS[@]}"; do
    dest="$BIN_DIR/$name"
    if [[ -L "$dest" ]] && ! links_to "$dest" "$REPO/${LINKS[$name]}" && [[ "$(readlink -- "$dest")" != */"${LINKS[$name]}" ]]; then
      echo "skipped $dest: it is a link to something else" >&2
      continue
    elif [[ -e "$dest" && ! -L "$dest" ]]; then
      echo "skipped $dest: a file with that name already exists" >&2
      continue
    fi
    ln -sfn -- "$REPO/${LINKS[$name]}" "$dest"
    echo "linked $dest -> $REPO/${LINKS[$name]}"
  done

  if [[ -e "$DESKTOP" ]] && ! grep -qx "$MARKER" "$DESKTOP"; then
    echo "skipped $DESKTOP: a menu entry not created by this install exists" >&2
  else
    # The menu entry needs an absolute Exec path: desktop sessions often do
    # not have ~/.local/bin on PATH
    while IFS= read -r line; do
      if [[ $line == Exec=* ]]; then
        printf 'Exec=%s\n' "$(desktop_quote "$BIN_DIR/audio-recorder")"
      else
        printf '%s\n' "$line"
      fi
    done < "$REPO/audio-recorder.desktop" > "$DESKTOP"
    echo "$MARKER" >> "$DESKTOP"
    echo "wrote $DESKTOP"
  fi

  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "note: $BIN_DIR is not on your PATH; add it to use the commands from a terminal" ;;
  esac
}

case "${1:-}" in
  --uninstall) uninstall ;;
  "") install ;;
  *) echo "usage: $0 [--uninstall]" >&2; exit 2 ;;
esac
