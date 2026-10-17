# Audio Output Recorder

Record what your Linux desktop is playing (speakers, headphones, YouTube, music, meetings) to `.mp3`, `.wav`, `.flac`, `.m4a` or `.ogg`, with a one-button GUI or a terminal command.

It is a thin layer over `ffmpeg` capturing the PulseAudio / PipeWire monitor source (`@DEFAULT_SINK@.monitor`), so it works on any desktop that runs PulseAudio or PipeWire with its PulseAudio compatibility layer (Ubuntu, Fedora, Debian and most others).

## Requirements

- `ffmpeg` (built with PulseAudio input, as the distribution packages are)
- `setpriv` from util-linux, for the terminal recorder (installed by default on virtually every distribution)
- `python3` with Tk, for the GUI only

```bash
sudo apt install ffmpeg python3-tk      # Debian / Ubuntu
sudo dnf install ffmpeg python3-tkinter # Fedora (ffmpeg from RPM Fusion)
```

## Install

```bash
git clone https://github.com/quantember-demos/audio-recorder.git
cd audio-recorder
./install.sh
```

This links two commands into `~/.local/bin` and adds **Audio Output Recorder** to your applications menu:

| Command | What it runs |
|---|---|
| `audio-recorder` | the GUI |
| `record-audio` | the terminal recorder |

`./install.sh --uninstall` removes exactly those three items. The installer never replaces a file it didn't create; if one is in the way, it says so and skips it. You can also skip installing and run `./audio_recorder_gui.py` or `./record_audio.sh` from the checkout.

## GUI

```bash
audio-recorder
```

- **Start / Stop** with one button; a live timer shows elapsed time.
- **Sources:** system output, microphone, or both mixed together.
- **Formats:** MP3, WAV, FLAC, M4A/AAC, OGG Vorbis.
- **Save folder** picker, plus *Open Save Directory* and *Play Last File*.
- Closing the window while recording stops and saves the recording, and so does logging out or `kill`-ing the GUI.
- If ffmpeg stops on its own (device removed, disk full), the GUI says so and shows ffmpeg's error.

Files are named by source and time, e.g. `system_audio_2026-10-17_14-05-09.mp3` or `microphone_….wav`. Existing files are never overwritten: a second recording in the same second gets `-2`, `-3` … appended.

## Terminal

```bash
record-audio                              # timestamped MP3 in ~/Recordings
record-audio ~/Music/my_song.mp3          # choose the file
record-audio -f wav                       # timestamped WAV in ~/Recordings
record-audio -o ~/Recordings/talk.flac    # same as passing the path
```

Stop with **any key** or **Ctrl+C** (pressing Ctrl+C twice is fine). Closing the terminal also stops and saves the recording. Without a keyboard, e.g. under `nohup`, stop it with `kill <pid>`.

- The codec follows the file extension. A name without a supported extension gets the `-f` format's, so `record-audio -f wav take` writes `take.wav`.
- An existing file is never overwritten; the command refuses and exits 1.
- The command exits non-zero if no usable file was written, and deletes the empty file.

## Notes

- **Combined mode** mixes the two inputs with ffmpeg's `amix`, which scales each input down to avoid clipping, so combined recordings are quieter than single-source ones.
- If the GUI is killed with `kill -9`, ffmpeg still stops, but occasionally the file isn't finalized. [docs/FLOW.md](docs/FLOW.md) explains why. Closing the window or a normal `kill` don't have this problem.
- Recording other people's audio may require their consent where you live.

## Tests

```bash
python3 -m unittest discover -s tests -v   # GUI: unit + integration
tests/test_cli.sh                          # terminal recorder: integration
tests/test_install.sh                      # installer (temp dirs only)
```

Integration tests record a few seconds of real audio and skip themselves when no display or PulseAudio/PipeWire capture is available. The GUI tests open windows briefly; to keep them off your screen, run them on a virtual display: `xvfb-run -a python3 -m unittest discover -s tests` (package `xvfb`). How the program flows, and what each test covers, is in [docs/FLOW.md](docs/FLOW.md).

## License

MIT, see [LICENSE](LICENSE).
