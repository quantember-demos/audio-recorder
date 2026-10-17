#!/usr/bin/env bash

# ==============================================================================
# System Audio Output Recorder Script
# Captures system playback (speakers/headphones/loopback) to an audio file.
# ==============================================================================

set -e

DEFAULT_DIR="${HOME}/Recordings"
SUPPORTED="mp3 wav flac m4a ogg"

FORMAT="mp3"
FILENAME=""
SOURCE="@DEFAULT_SINK@.monitor"

usage() {
  echo "Usage: $0 [options] [filename]"
  echo ""
  echo "Records system audio output until you press a key or Ctrl+C."
  echo "Without a filename, saves to ~/Recordings/system_audio_<timestamp>.<format>."
  echo "An existing file is never overwritten."
  echo ""
  echo "Options:"
  echo "  -f, --format FORMAT   Audio format (mp3, wav, flac, m4a, ogg). Default: mp3"
  echo "                        The extension of an explicit filename takes precedence."
  echo "  -o, --output FILE     Output file path (same as passing it as an argument)"
  echo "  -h, --help            Show this help message"
}

die_usage() {
  echo "❌ Error: $1" >&2
  usage >&2
  exit 2
}

set_filename() {
  [[ -z "$FILENAME" ]] || die_usage "more than one output file given"
  FILENAME="$1"
}

# Parse command line options
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--format)
      [[ -n "${2:-}" ]] || die_usage "$1 needs a value"
      FORMAT="${2,,}"
      shift 2
      ;;
    -o|--output)
      [[ -n "${2:-}" ]] || die_usage "$1 needs a value"
      set_filename "$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      [[ $# -le 1 ]] || die_usage "more than one output file given"
      [[ $# -eq 0 ]] || set_filename "$1"
      break
      ;;
    -*)
      die_usage "unknown option '$1'"
      ;;
    *)
      set_filename "$1"
      shift
      ;;
  esac
done

[[ " $SUPPORTED " == *" $FORMAT "* ]] || die_usage "unsupported format '$FORMAT' (use ${SUPPORTED// /, })"

# Verify dependencies. setpriv (util-linux) ties ffmpeg's lifetime to this
# script, so even a `kill -9` of the script cannot leave ffmpeg recording.
for dep in ffmpeg setpriv; do
  if ! command -v "$dep" &>/dev/null; then
    echo "❌ Error: '$dep' command was not found. Please install it." >&2
    exit 1
  fi
done

if [[ -z "$FILENAME" ]]; then
  # Timestamped name; add -2, -3 ... if two recordings start in the same second
  mkdir -p "$DEFAULT_DIR"
  base="${DEFAULT_DIR}/system_audio_$(date +"%Y-%m-%d_%H-%M-%S")"
  FILENAME="${base}.${FORMAT}"
  n=2
  while [[ -e "$FILENAME" ]]; do
    FILENAME="${base}-${n}.${FORMAT}"
    n=$((n + 1))
  done
else
  # A name without a supported extension gets the chosen format's extension;
  # ffmpeg picks the container from the extension and fails without one
  ext="${FILENAME##*/}"
  ext="${ext##*.}"
  if [[ "$FILENAME" != *.* || " $SUPPORTED aac " != *" ${ext,,} "* ]]; then
    FILENAME="${FILENAME}.${FORMAT}"
  fi
  if [[ -e "$FILENAME" ]]; then
    echo "❌ Error: '$FILENAME' already exists; refusing to overwrite it." >&2
    exit 1
  fi
  mkdir -p "$(dirname -- "$FILENAME")"
fi

# Codec follows the file extension
EXT="${FILENAME##*.}"
case "${EXT,,}" in
  wav)     CODEC_ARGS=(-c:a pcm_s16le) ;;
  flac)    CODEC_ARGS=(-c:a flac) ;;
  m4a|aac) CODEC_ARGS=(-c:a aac -b:a 192k) ;;
  ogg)     CODEC_ARGS=(-c:a libvorbis -q:a 5) ;;
  *)       CODEC_ARGS=(-c:a libmp3lame -b:a 192k) ;;
esac

FFMPEG_PID=""
FFMPEG_STATUS=0
STOPPED=0

# Reap ffmpeg and keep its exit status. A trapped signal (a second Ctrl+C)
# interrupts `wait` with a status above 128 while ffmpeg is still finishing
# the file; waiting again returns ffmpeg's own status.
collect_status() {
  while kill -0 "$FFMPEG_PID" 2>/dev/null; do
    wait "$FFMPEG_PID" 2>/dev/null || true
  done
  FFMPEG_STATUS=0
  wait "$FFMPEG_PID" 2>/dev/null || FFMPEG_STATUS=$?
}

stop_recording() {
  [[ $STOPPED -eq 0 ]] || return 0
  STOPPED=1
  # A signal before ffmpeg's PID is known is handled right after launch
  [[ -n "$FFMPEG_PID" ]] || return 0
  echo -e "\n\nStopping recording gracefully..."
  # Exactly one SIGINT: ffmpeg treats a second one as "immediate exit"
  # and abandons the file
  kill -INT "$FFMPEG_PID" 2>/dev/null || true
  collect_status
}

on_exit() {
  stty echo 2>/dev/null || true
  # Last resort if the script is leaving with ffmpeg still running
  if [[ -n "$FFMPEG_PID" ]] && kill -0 "$FFMPEG_PID" 2>/dev/null; then
    kill -INT "$FFMPEG_PID" 2>/dev/null || true
    collect_status
  fi
}

# Traps go in before ffmpeg starts, so no signal can arrive unhandled.
# HUP covers a closed terminal window or a dropped SSH session.
trap stop_recording INT TERM HUP
trap on_exit EXIT

echo "=================================================="
echo "          AUDIO OUTPUT RECORDER"
echo "=================================================="
echo " Output File : $FILENAME"
echo " Audio Source: System Output (Speakers / Loopback)"
echo "=================================================="
echo " Press any key or Ctrl+C to STOP."
echo ""

# Own process group (job control on for this one fork) so a terminal Ctrl+C
# reaches only this script; --pdeathsig sends ffmpeg SIGINT if the script
# dies without cleaning up. -nostdin keeps ffmpeg from competing with `read`
# for keypresses; -n refuses to overwrite.
set -m
setpriv --pdeathsig INT ffmpeg -nostdin -hide_banner -loglevel error \
  -f pulse -i "$SOURCE" \
  "${CODEC_ARGS[@]}" \
  -n "$FILENAME" &
FFMPEG_PID=$!
set +m

# Stopped by a signal that arrived during the launch
if [[ $STOPPED -eq 1 ]]; then
  STOPPED=0
  stop_recording
fi

START_TIME=$(date +%s)
INPUT_OPEN=1

# Live update timer loop
stty -echo 2>/dev/null || true
while [[ $STOPPED -eq 0 ]] && kill -0 "$FFMPEG_PID" 2>/dev/null; do
  if [[ $INPUT_OPEN -eq 1 ]]; then
    # Single keypress with 1 s timeout: 0 = key, >128 = timeout, else EOF
    rc=0
    read -r -t 1 -N 1 _ 2>/dev/null || rc=$?
    if [[ $rc -eq 0 ]]; then
      stop_recording
      break
    elif [[ $rc -le 128 ]]; then
      # stdin is closed (cron, nohup, </dev/null): only a signal can stop us
      INPUT_OPEN=0
      echo -e "\nNo keyboard input available; stop with Ctrl+C or: kill $$"
    fi
  else
    sleep 1
  fi

  NOW=$(date +%s)
  ELAPSED=$((NOW - START_TIME))
  MINS=$((ELAPSED / 60))
  SECS=$((ELAPSED % 60))
  printf "\r🔴 RECORDING [%02d:%02d]  |  Press any key to stop..." "$MINS" "$SECS"
done
stty echo 2>/dev/null || true

# ffmpeg exited on its own (device error, disk full)
if [[ $STOPPED -eq 0 ]]; then
  STOPPED=1
  collect_status
  echo ""
  echo "⚠️ ffmpeg stopped unexpectedly (exit status $FFMPEG_STATUS)." >&2
fi

echo ""
# ffmpeg 6.1 exits 0 after a SIGINT stop; 255 is also accepted because some
# builds report a signal-ended run that way. The size check guards both.
if [[ -s "$FILENAME" ]] && [[ $FFMPEG_STATUS -eq 0 || $FFMPEG_STATUS -eq 255 ]]; then
  SIZE=$(du -h "$FILENAME" | cut -f1)
  echo "✅ Recording complete!"
  echo "  📁 Path: $FILENAME"
  echo "  📊 Size: $SIZE"
else
  if [[ -s "$FILENAME" ]]; then
    echo "❌ Recording failed (ffmpeg status $FFMPEG_STATUS); the partial file may be damaged:" >&2
    echo "  📁 Path: $FILENAME" >&2
  else
    rm -f -- "$FILENAME"
    echo "❌ Recording failed: no audio was written (ffmpeg status $FFMPEG_STATUS)." >&2
  fi
  exit 1
fi
