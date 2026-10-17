"""Tests for audio_recorder_gui.

Unit tests always run. Integration tests record real audio and need a display,
and ffmpeg able to capture from PulseAudio/PipeWire; they
skip otherwise.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import audio_recorder_gui as gui  # noqa: E402


def have_audio_stack():
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False
    # Probe the real capture path rather than a client tool such as pactl
    probe = ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "pulse", "-i", gui.SYSTEM_MONITOR,
             "-t", "0.3", "-f", "null", "-"]
    return subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


class BuildCommandTests(unittest.TestCase):
    def test_system_source_uses_sink_monitor(self):
        cmd = gui.build_ffmpeg_command("system", "mp3", "/tmp/x.mp3")
        self.assertIn(gui.SYSTEM_MONITOR, cmd)
        self.assertNotIn(gui.DEFAULT_MIC, cmd)

    def test_mic_source_uses_default_source(self):
        cmd = gui.build_ffmpeg_command("mic", "wav", "/tmp/x.wav")
        self.assertIn(gui.DEFAULT_MIC, cmd)
        self.assertNotIn(gui.SYSTEM_MONITOR, cmd)

    def test_combined_mixes_both_inputs(self):
        cmd = gui.build_ffmpeg_command("combined", "flac", "/tmp/x.flac")
        self.assertIn(gui.SYSTEM_MONITOR, cmd)
        self.assertIn(gui.DEFAULT_MIC, cmd)
        self.assertTrue(any("amix=inputs=2" in a for a in cmd))

    def test_codec_matches_extension(self):
        for ext, codec in [("mp3", "libmp3lame"), ("wav", "pcm_s16le"), ("flac", "flac"),
                           ("m4a", "aac"), ("ogg", "libvorbis")]:
            cmd = gui.build_ffmpeg_command("system", ext, f"/tmp/x.{ext}")
            self.assertEqual(cmd[cmd.index("-c:a") + 1], codec, ext)

    def test_unknown_extension_falls_back_to_mp3(self):
        cmd = gui.build_ffmpeg_command("system", "xyz", "/tmp/x.xyz")
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "libmp3lame")

    def test_output_path_is_last_never_overwritten_stdin_disabled(self):
        cmd = gui.build_ffmpeg_command("system", "mp3", "/tmp/out.mp3")
        self.assertEqual(cmd[-2:], ["-n", "/tmp/out.mp3"])
        self.assertIn("-nostdin", cmd)
        self.assertNotIn("-y", cmd)


class UniquePathTests(unittest.TestCase):
    def test_suffixes_until_free(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gui.unique_path(d, "take", "mp3"), os.path.join(d, "take.mp3"))
            open(os.path.join(d, "take.mp3"), "w").close()
            self.assertEqual(gui.unique_path(d, "take", "mp3"), os.path.join(d, "take-2.mp3"))
            open(os.path.join(d, "take-2.mp3"), "w").close()
            self.assertEqual(gui.unique_path(d, "take", "mp3"), os.path.join(d, "take-3.mp3"))


@unittest.skipUnless(have_audio_stack(), "needs display + ffmpeg + PulseAudio/PipeWire")
class RecordingIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.tk = tk
        self.tmp = tempfile.mkdtemp(prefix="audio_recorder_test_")
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.AudioRecorderApp(self.root)
        self.app.dir_entry.delete(0, tk.END)
        self.app.dir_entry.insert(0, self.tmp)
        self.errors = []
        patcher = mock.patch.object(gui.messagebox, "showerror",
                                    side_effect=lambda *a, **k: self.errors.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self.app.process and self.app.process.poll() is None:
            self.app.process.kill()
        # Close the way a user does; a no-op if the test already closed it
        self.app.on_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.05)

    def test_start_stop_writes_valid_file(self):
        for label in self.app.fmt_combo["values"]:
            self.app.format_var.set(label)
            self.app.start_recording()
            self.pump(2)
            self.app.stop_recording()
            path = self.app.output_filename
            # The check is that the file is valid; PulseAudio can take most of a
            # second to start delivering audio, so do not demand the full 2 s
            self.assertGreater(probe_duration(path), 0.5,
                               f"{label}: {path} ffmpeg said: {self.app.last_error!r}")
        self.assertEqual(self.errors, [])

    def test_closing_window_stops_ffmpeg(self):
        self.app.start_recording()
        pid = self.app.process.pid
        self.pump(2)
        self.app.on_close()
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertGreater(probe_duration(self.app.output_filename), 1.0)

    def test_ffmpeg_dying_is_reported(self):
        self.app.start_recording()
        self.pump(1)
        self.app.process.send_signal(signal.SIGKILL)
        self.pump(1.5)
        self.assertFalse(self.app.is_recording)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("unexpectedly", self.errors[0][1])

    def test_mic_file_is_named_for_its_source(self):
        self.app.source_var.set(self.app.src_combo["values"][1])
        self.app.start_recording()
        self.pump(1)
        self.app.stop_recording()
        self.assertTrue(os.path.basename(self.app.output_filename).startswith("microphone_"))


# Child process for the signal tests: a real GUI that starts recording and
# reports ffmpeg's pid and output file on stdout
GUI_CHILD = r"""
import sys, tkinter as tk
sys.path.insert(0, sys.argv[1])
import audio_recorder_gui as gui
root = tk.Tk(); app = gui.AudioRecorderApp(root)
app.dir_entry.delete(0, tk.END); app.dir_entry.insert(0, sys.argv[2])
app.start_recording()
print(app.process.pid, app.output_filename, flush=True)
root.mainloop()
"""


@unittest.skipUnless(have_audio_stack(), "needs display + ffmpeg + PulseAudio/PipeWire")
class GuiDeathTests(unittest.TestCase):
    """Whatever ends the GUI process, ffmpeg must finish the file and exit."""

    def run_and_signal(self, sig, expect_valid_file=True):
        repo = os.path.join(os.path.dirname(__file__), "..")
        with tempfile.TemporaryDirectory() as d:
            with subprocess.Popen([sys.executable, "-c", GUI_CHILD, repo, d],
                                  stdout=subprocess.PIPE, text=True) as child:
                pid_s, path = child.stdout.readline().split(" ", 1)
                ffmpeg_pid, path = int(pid_s), path.strip()
                time.sleep(2.5)
                child.send_signal(sig)
                child.wait(timeout=15)
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    os.kill(ffmpeg_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                os.kill(ffmpeg_pid, signal.SIGKILL)
                self.fail(f"ffmpeg still running after GUI got {sig.name}")
            if expect_valid_file:
                self.assertGreater(probe_duration(path), 1.0, sig.name)

    def test_sigterm(self):
        self.run_and_signal(signal.SIGTERM)

    def test_sighup(self):
        self.run_and_signal(signal.SIGHUP)

    def test_sigint(self):
        self.run_and_signal(signal.SIGINT)

    def test_sigkill(self):
        # ffmpeg must stop. The file is not checked: Tk's extra threads can make
        # the parent-death SIGINT arrive twice (see stop_with_parent)
        self.run_and_signal(signal.SIGKILL, expect_valid_file=False)


if __name__ == "__main__":
    unittest.main()
