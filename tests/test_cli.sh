#!/usr/bin/env bash
# Integration tests for record_audio.sh. Records real audio, so it needs
# ffmpeg, ffprobe, setpriv, python3 and PulseAudio/PipeWire capture.
# Run from anywhere:  tests/test_cli.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../record_audio.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }

# Show the recorder's own output under a failure, so it explains itself
explain() {
  [[ -f "$1" ]] && sed -n '/Stopping\|Error\|error\|failed\|unexpectedly\|⚠\|❌/p' "$1" | sed 's/^/        | /' | tail -6
}

duration() {
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null
}

# Pass if the file is at least $2 seconds long
check_duration() {
  local d
  d="$(duration "$1")"
  awk -v d="${d:-0}" -v min="$2" 'BEGIN { exit !(d + 0 >= min) }'
}

for dep in ffmpeg ffprobe setpriv python3; do
  command -v "$dep" >/dev/null || { echo "SKIP: $dep not installed"; exit 0; }
done
# Probe the real capture path rather than a client tool such as pactl
if ! ffmpeg -nostdin -loglevel error -f pulse -i @DEFAULT_SINK@.monitor -t 0.3 -f null - 2>/dev/null; then
  echo "SKIP: cannot capture from PulseAudio/PipeWire"; exit 0
fi

echo "record_audio.sh integration tests"

# 1. Enter stops cleanly, every format
for f in mp3 wav flac m4a ogg; do
  (sleep 2; echo) | "$SCRIPT" "$TMP/enter.$f" >"$TMP/enter.$f.log" 2>&1
  rc=$?
  if [[ $rc -eq 0 ]] && check_duration "$TMP/enter.$f" 1; then
    ok "Enter stop writes valid .$f (rc=$rc, $(duration "$TMP/enter.$f")s)"
  else
    bad "Enter stop .$f (rc=$rc, duration=$(duration "$TMP/enter.$f"))"
    explain "$TMP/enter.$f.log"
  fi
done

# PIDs of ffmpeg processes writing into $TMP (by process name, so this
# never matches the shell running the test)
ffmpeg_pids() {
  ps -C ffmpeg -o pid=,args= 2>/dev/null | awk -v t="$TMP" 'index($0, t) { print $1 }'
}

# Pass if no ffmpeg is still writing into $TMP after up to 3 s
check_no_orphan() {
  local leftover
  for _ in $(seq 1 12); do
    leftover="$(ffmpeg_pids)"
    [[ -z $leftover ]] && { ok "$1: no ffmpeg left running"; return; }
    sleep 0.25
  done
  bad "$1: ffmpeg left running (pid $leftover)"
  # shellcheck disable=SC2086
  kill -KILL $leftover
}

# 2. Signals, delivered as a terminal or the system would (tests/signal_harness.py)
#    mode | expected script rc | what it guards
signal_case() {
  local mode=$1 want_rc=$2 out="$TMP/sig-$1.mp3" rc redraws
  read -r rc redraws < <(python3 "$HERE/signal_harness.py" "$SCRIPT" "$out" "$mode")
  sleep 0.5
  if [[ $rc == "$want_rc" ]] && check_duration "$out" 2; then
    ok "$mode: valid file (rc=$rc, $(duration "$out")s)"
  else
    bad "$mode: rc=$rc (want $want_rc), duration=$(duration "$out")"
    explain "$out.log"
  fi
  check_no_orphan "$mode"
  LAST_REDRAWS=$redraws
}
# Regression: ffmpeg got two SIGINTs and left a 0-byte file
signal_case ctrlc 0
# Regression: second Ctrl+C interrupted `wait`, script reported failure
signal_case double-ctrlc 0
# Regression: closing the terminal left ffmpeg recording forever
signal_case hup 0
# SIGKILL cannot be trapped; --pdeathsig must make ffmpeg finish the file
signal_case kill9 -9
# Regression: EOF on stdin made the loop spin ~300 redraws/s
signal_case eof-term 0
if [[ $LAST_REDRAWS -le 6 ]]; then
  ok "eof-term: loop paced at 1/s ($LAST_REDRAWS redraws in ~3 s)"
else
  bad "eof-term: loop spinning ($LAST_REDRAWS redraws in ~3 s)"
fi

# 3. ffmpeg dying mid-recording is reported as a failure, not success
"$SCRIPT" "$TMP/killed.mp3" </dev/null >"$TMP/killed.log" 2>&1 &
sleep 2
# shellcheck disable=SC2046
kill -KILL $(ffmpeg_pids)
wait $!
rc=$?
if [[ $rc -ne 0 ]] && grep -q "unexpectedly" "$TMP/killed.log"; then
  ok "killed ffmpeg reported as failure (rc=$rc)"
else
  bad "killed ffmpeg not reported (rc=$rc)"
fi

# 4. File naming
echo precious > "$TMP/exists.mp3"
"$SCRIPT" "$TMP/exists.mp3" </dev/null >/dev/null 2>&1
rc=$?
if [[ $rc -eq 1 && "$(cat "$TMP/exists.mp3")" == precious ]]; then
  ok "existing file is not overwritten (rc=$rc)"
else
  bad "existing file overwritten or wrong rc (rc=$rc)"
fi
(sleep 2; echo) | "$SCRIPT" -f wav "$TMP/noext" >/dev/null 2>&1
rc=$?
if [[ $rc -eq 0 ]] && check_duration "$TMP/noext.wav" 1; then
  ok "name without extension gets the -f format (noext.wav)"
else
  bad "name without extension (rc=$rc)"
fi

# 5. Argument validation
expect_rc() {  # expected-rc label args...
  local want=$1 label=$2 rc
  shift 2
  "$SCRIPT" "$@" >/dev/null 2>&1
  rc=$?
  if [[ $rc -eq $want ]]; then ok "$label (rc=$rc)"; else bad "$label (rc=$rc, want $want)"; fi
}
expect_rc 2 "unsupported format rejected" -f xyz
expect_rc 2 "missing option value rejected" -f
expect_rc 2 "unknown option rejected" --bogus
expect_rc 2 "two output files rejected" "$TMP/a.mp3" "$TMP/b.mp3"
expect_rc 0 "--help exits 0" --help

echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
