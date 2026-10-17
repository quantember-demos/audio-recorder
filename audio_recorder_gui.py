#!/usr/bin/env python3
"""
Audio Recorder GUI
A modern Tkinter graphical interface to capture system audio output, microphone, or both.
Uses ffmpeg + PulseAudio/PipeWire.
"""

import ctypes
import os
import subprocess
import signal
import tempfile
import time
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

SYSTEM_MONITOR = "@DEFAULT_SINK@.monitor"
DEFAULT_MIC = "@DEFAULT_SOURCE@"

CODEC_ARGS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
}

# Filename prefix per source, so microphone takes are not labelled as system output
FILE_PREFIX = {"system": "system_audio", "mic": "microphone", "combined": "combined"}

PR_SET_PDEATHSIG = 1


def stop_with_parent():
    """preexec_fn: ffmpeg gets SIGINT (finish the file and exit) if the GUI dies,
    even by SIGKILL, which no handler in the GUI can catch.

    The signal is tied to the parent *thread*. Tk runs extra threads, so after
    a SIGKILL ffmpeg can be handed between dying threads and receive SIGINT
    more than once, which makes it exit without finalizing the file. ffmpeg
    always stops, but the file may be damaged in that one case."""
    try:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGINT)
    except (OSError, AttributeError):
        pass


def unique_path(directory, stem, ext):
    """directory/stem.ext, or stem-2.ext, stem-3.ext ... if it already exists."""
    path = os.path.join(directory, f"{stem}.{ext}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{stem}-{n}.{ext}")
        n += 1
    return path


def build_ffmpeg_command(source, ext, output_path):
    """Return the ffmpeg argv for a source ("system" | "mic" | "combined") and extension."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]

    if source == "combined":
        cmd += [
            "-f", "pulse", "-i", SYSTEM_MONITOR,
            "-f", "pulse", "-i", DEFAULT_MIC,
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first[aout]",
            "-map", "[aout]",
        ]
    elif source == "mic":
        cmd += ["-f", "pulse", "-i", DEFAULT_MIC]
    else:
        cmd += ["-f", "pulse", "-i", SYSTEM_MONITOR]

    cmd += CODEC_ARGS.get(ext, CODEC_ARGS["mp3"])
    cmd += ["-n", output_path]  # never overwrite
    return cmd


class AudioRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Output Recorder")
        self.root.geometry("520x460")
        self.root.resizable(False, False)

        # State variables
        self.is_recording = False
        self.process = None
        self.stderr_file = None
        self.last_error = ""
        self.closing = False
        self.tick_job = None
        self.start_time = None
        self.elapsed_seconds = 0
        self.timer_job = None
        self.last_file = None
        self.output_filename = ""

        # Default save directory
        self.save_dir = os.path.expanduser("~/Recordings")
        os.makedirs(self.save_dir, exist_ok=True)

        self.setup_styles()
        self.build_ui()

        # Closing the window, logging out (SIGHUP/SIGTERM) or Ctrl+C in a
        # launching terminal must all stop ffmpeg, or it keeps recording unseen
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: self.on_close())
        self.signal_tick()

    def signal_tick(self):
        # Python runs signal handlers only between bytecodes; while Tk waits
        # for events no Python code runs, so wake up regularly
        self.tick_job = self.root.after(250, self.signal_tick)

    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Color palette. Record/stop buttons use the Okabe-Ito blue/vermillion
        # pair (colorblind-safe); their labels also differ, so color is never
        # the only cue.
        self.bg_color = "#1e1e2e"
        self.card_bg = "#2b2b3b"
        self.text_color = "#cdd6f4"
        self.accent_blue = "#89b4fa"
        self.accent_orange = "#fab387"
        self.start_bg, self.start_active = "#0072B2", "#005a8c"
        self.stop_bg, self.stop_active = "#D55E00", "#a84a00"

        self.root.configure(bg=self.bg_color)

        # Custom ttk styles
        self.style.configure(".", background=self.bg_color, foreground=self.text_color)
        self.style.configure("Card.TFrame", background=self.card_bg, relief="flat", borderwidth=0)
        self.style.configure("Header.TLabel", font=("Helvetica", 16, "bold"), background=self.bg_color, foreground="#f5e0dc")
        self.style.configure("SubHeader.TLabel", font=("Helvetica", 10), background=self.bg_color, foreground="#a6adc8")
        self.style.configure("Status.TLabel", font=("Helvetica", 10, "italic"), background=self.card_bg, foreground=self.accent_blue)
        self.style.configure("Timer.TLabel", font=("Monospace", 32, "bold"), background=self.card_bg, foreground="#f5e0dc")

    def build_ui(self):
        # Container
        main_frame = ttk.Frame(self.root, padding="20 15 20 15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header_lbl = ttk.Label(main_frame, text="🎙️ System Audio Recorder", style="Header.TLabel")
        header_lbl.pack(anchor="w")
        sub_lbl = ttk.Label(main_frame, text="Capture system output audio, microphone, or both to file", style="SubHeader.TLabel")
        sub_lbl.pack(anchor="w", pady=(0, 15))

        # Settings Card Frame
        settings_card = ttk.Frame(main_frame, style="Card.TFrame", padding="15")
        settings_card.pack(fill=tk.X, pady=(0, 15))

        # Audio Source Selection
        src_label = ttk.Label(settings_card, text="Audio Source:", font=("Helvetica", 10, "bold"), background=self.card_bg)
        src_label.grid(row=0, column=0, sticky="w", pady=5)

        self.source_var = tk.StringVar(value="system")
        self.src_combo = ttk.Combobox(settings_card, textvariable=self.source_var, state="readonly", width=32)
        self.src_combo['values'] = (
            "🔊 System Audio Output (Speakers / Desktop)",
            "🎙️ Microphone Input",
            "🎛️ Combined Output + Microphone"
        )
        self.src_combo.current(0)
        self.src_combo.grid(row=0, column=1, sticky="e", pady=5, padx=(10, 0))

        # Format Selection
        fmt_label = ttk.Label(settings_card, text="Format:", font=("Helvetica", 10, "bold"), background=self.card_bg)
        fmt_label.grid(row=1, column=0, sticky="w", pady=5)

        self.format_var = tk.StringVar(value="MP3 (.mp3)")
        self.fmt_combo = ttk.Combobox(settings_card, textvariable=self.format_var, state="readonly", width=32)
        self.fmt_combo['values'] = ("MP3 (.mp3)", "WAV (.wav)", "FLAC (.flac)", "M4A/AAC (.m4a)", "OGG Vorbis (.ogg)")
        self.fmt_combo.current(0)
        self.fmt_combo.grid(row=1, column=1, sticky="e", pady=5, padx=(10, 0))

        # Output Folder Picker
        dir_label = ttk.Label(settings_card, text="Save To:", font=("Helvetica", 10, "bold"), background=self.card_bg)
        dir_label.grid(row=2, column=0, sticky="w", pady=5)

        dir_frame = ttk.Frame(settings_card, style="Card.TFrame")
        dir_frame.grid(row=2, column=1, sticky="e", pady=5, padx=(10, 0))

        self.dir_entry = ttk.Entry(dir_frame, width=22)
        self.dir_entry.insert(0, self.save_dir)
        self.dir_entry.pack(side=tk.LEFT, padx=(0, 5))

        self.browse_btn = ttk.Button(dir_frame, text="Browse...", command=self.browse_directory)
        self.browse_btn.pack(side=tk.RIGHT)

        # Recording Status & Timer Display Card
        timer_card = ttk.Frame(main_frame, style="Card.TFrame", padding="15")
        timer_card.pack(fill=tk.X, pady=(0, 15))

        self.timer_label = ttk.Label(timer_card, text="00:00:00", style="Timer.TLabel")
        self.timer_label.pack(anchor="center")

        self.status_label = ttk.Label(timer_card, text="Ready to record", style="Status.TLabel")
        self.status_label.pack(anchor="center", pady=(5, 0))

        # Action Button Frame
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill=tk.X, pady=(5, 0))

        self.rec_btn = tk.Button(
            action_frame,
            text="⏺ START RECORDING",
            font=("Helvetica", 13, "bold"),
            bg=self.start_bg,
            fg="white",
            activebackground=self.start_active,
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=20,
            pady=12,
            cursor="hand2",
            command=self.toggle_recording
        )
        self.rec_btn.pack(fill=tk.X, pady=(0, 10))

        # Bottom controls (Open Folder & Open File)
        btn_bar = ttk.Frame(action_frame)
        btn_bar.pack(fill=tk.X)

        self.open_dir_btn = ttk.Button(btn_bar, text="📁 Open Save Directory", command=self.open_save_directory)
        self.open_dir_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.play_last_btn = ttk.Button(btn_bar, text="▶ Play Last File", state="disabled", command=self.play_last_file)
        self.play_last_btn.pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(5, 0))

    def browse_directory(self):
        chosen = filedialog.askdirectory(initialdir=self.dir_entry.get())
        if chosen:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, chosen)
            self.save_dir = chosen

    def open_save_directory(self):
        path = self.dir_entry.get()
        if os.path.exists(path):
            subprocess.Popen(["xdg-open", path])
        else:
            messagebox.showerror("Error", f"Directory does not exist:\n{path}")

    def play_last_file(self):
        if self.last_file and os.path.exists(self.last_file):
            subprocess.Popen(["xdg-open", self.last_file])
        else:
            messagebox.showerror("Error", "No recent recording file found.")

    def get_extension(self):
        fmt = self.format_var.get()
        if "WAV" in fmt: return "wav"
        if "FLAC" in fmt: return "flac"
        if "M4A" in fmt: return "m4a"
        if "OGG" in fmt: return "ogg"
        return "mp3"

    def get_source(self):
        selection = self.source_var.get()
        if "Combined" in selection: return "combined"
        if "Microphone" in selection: return "mic"
        return "system"

    def set_controls_enabled(self, enabled):
        combo_state = "readonly" if enabled else "disabled"
        self.src_combo.configure(state=combo_state)
        self.fmt_combo.configure(state=combo_state)
        self.dir_entry.configure(state="normal" if enabled else "disabled")
        self.browse_btn.configure(state="normal" if enabled else "disabled")

    def toggle_recording(self):
        if not self.is_recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        try:
            subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception:
            messagebox.showerror("Error", "ffmpeg is required but not installed.")
            return

        save_dir = os.path.expanduser(self.dir_entry.get().strip())
        if not save_dir:
            save_dir = self.save_dir
        try:
            os.makedirs(save_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Error", f"Cannot create save directory:\n{e}")
            return

        ext = self.get_extension()
        source = self.get_source()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.output_filename = unique_path(save_dir, f"{FILE_PREFIX[source]}_{timestamp}", ext)

        cmd = build_ffmpeg_command(source, ext, self.output_filename)

        # ffmpeg errors go to a temp file (not a pipe, which could fill and
        # block ffmpeg) so a failure can be shown to the user.
        self.stderr_file = tempfile.TemporaryFile()
        try:
            # Own session: a Ctrl+C in a launching terminal must not reach ffmpeg
            self.process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=self.stderr_file, start_new_session=True,
                                            preexec_fn=stop_with_parent)
        except Exception as e:
            self.stderr_file.close()
            self.stderr_file = None
            messagebox.showerror("Recording Failed", f"Could not start ffmpeg:\n{e}")
            return

        self.is_recording = True
        self.start_time = time.time()

        # Update UI state
        self.rec_btn.configure(text="⏹ STOP RECORDING", bg=self.stop_bg, activebackground=self.stop_active)
        self.status_label.configure(text=f"Recording to: {os.path.basename(self.output_filename)}", foreground=self.accent_orange)
        self.set_controls_enabled(False)

        self.update_timer()

    def read_ffmpeg_errors(self):
        if not self.stderr_file:
            return ""
        self.stderr_file.seek(0)
        text = self.stderr_file.read().decode(errors="replace").strip()
        self.stderr_file.close()
        self.stderr_file = None
        return text

    def update_timer(self):
        if not (self.is_recording and self.start_time):
            return

        # ffmpeg exiting on its own (device gone, disk full) ends the recording
        if self.process and self.process.poll() is not None:
            code = self.process.returncode
            self.stop_recording()
            messagebox.showerror("Recording Stopped",
                                 f"ffmpeg exited unexpectedly (status {code}).\n\n{self.last_error or 'No error output.'}")
            return

        self.elapsed_seconds = int(time.time() - self.start_time)
        hrs = self.elapsed_seconds // 3600
        mins = (self.elapsed_seconds % 3600) // 60
        secs = self.elapsed_seconds % 60
        self.timer_label.configure(text=f"{hrs:02d}:{mins:02d}:{secs:02d}")
        self.timer_job = self.root.after(500, self.update_timer)

    def stop_recording(self):
        if not self.is_recording:
            return

        self.is_recording = False
        if self.timer_job:
            self.root.after_cancel(self.timer_job)
            self.timer_job = None

        if self.process:
            if self.process.poll() is None:
                # One SIGINT lets ffmpeg finalize the file; a second would abort it
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
            self.process = None
        self.last_error = self.read_ffmpeg_errors()

        # Update UI state
        self.rec_btn.configure(text="⏺ START RECORDING", bg=self.start_bg, activebackground=self.start_active)
        self.set_controls_enabled(True)

        if os.path.exists(self.output_filename) and os.path.getsize(self.output_filename) > 0:
            size_mb = os.path.getsize(self.output_filename) / (1024 * 1024)
            self.last_file = self.output_filename
            self.play_last_btn.configure(state="normal")
            self.status_label.configure(
                text=f"Saved: {os.path.basename(self.output_filename)} ({size_mb:.2f} MB)",
                foreground=self.accent_blue
            )
        else:
            self.status_label.configure(text="Recording stopped — no audio was saved.", foreground=self.accent_orange)

    def on_close(self):
        # A second signal while closing must not destroy the window twice
        if self.closing:
            return
        self.closing = True
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
            self.tick_job = None
        try:
            self.stop_recording()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    AudioRecorderApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
