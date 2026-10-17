"""Run record_audio.sh the way a terminal would, deliver a signal, report the outcome.

Usage: signal_harness.py SCRIPT OUTFILE MODE

Modes:
  ctrlc         one SIGINT to the process group (a terminal Ctrl+C)
  double-ctrlc  two SIGINTs to the group 50 ms apart
  hup           SIGHUP to the group (terminal window closed)
  kill9         SIGKILL to the script only (no trap can run)
  eof-term      stdin at EOF (cron/nohup), then SIGTERM to the script

Prints "<returncode> <timer redraws seen>" and saves the script's output
to OUTFILE.log. The script starts in a new
session with SIGINT at its default: a background job of a non-interactive
shell would start with SIGINT ignored, which a real terminal never does.
"""

import os
import signal
import subprocess
import sys
import time

script, outfile, mode = sys.argv[1:4]

stdin = subprocess.DEVNULL if mode == "eof-term" else subprocess.PIPE
p = subprocess.Popen(["bash", script, outfile], stdin=stdin, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, start_new_session=True,
                     preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
time.sleep(3)

if mode == "ctrlc":
    os.killpg(p.pid, signal.SIGINT)
elif mode == "double-ctrlc":
    os.killpg(p.pid, signal.SIGINT)
    time.sleep(0.05)
    os.killpg(p.pid, signal.SIGINT)
elif mode == "hup":
    os.killpg(p.pid, signal.SIGHUP)
elif mode == "kill9":
    p.kill()
elif mode == "eof-term":
    p.terminate()
else:
    sys.exit(f"unknown mode {mode}")

try:
    out, _ = p.communicate(timeout=15)
except subprocess.TimeoutExpired:
    os.killpg(p.pid, signal.SIGKILL)
    out, _ = p.communicate()
    with open(outfile + ".log", "wb") as log:
        log.write(out)
    print("timeout", 0)
    sys.exit()

text = out.decode(errors="replace")
with open(outfile + ".log", "w") as log:
    log.write(text)
print(p.returncode, text.count("RECORDING ["))
