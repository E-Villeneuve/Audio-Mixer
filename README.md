# mic-mixer

Play sound files through your microphone on Windows. mic-mixer mixes your live
mic with sound effects and sends the result to a virtual audio cable. Discord,
OBS, games or any other app then use the cable as their microphone.

```
your mic ──► noise suppression ──► gain ──┐
                                          ├──► limiter ──► CABLE Input ══► "CABLE Output" (pick this as your mic)
sound files ──► resample ──► volume ──────┘
                    └──► headset monitor (optional, so you hear your own sounds)
```

Features:

- A dark-themed GUI with device pickers, a sound list, gain sliders and live level meters.
- Optional **RNNoise** noise suppression on your voice. The sound effects are left untouched.
- Plays WAV, FLAC, OGG, MP3 and AIFF files, at any sample rate. Sounds load in the background, so playback starts instantly.
- A drift-corrected buffer keeps latency around 25 ms without clicks, even though your mic and the cable run on separate clocks.
- A limiter on the whole mix, so loud sounds on top of your voice don't clip.
- A command-line mode for when you don't want the GUI.

## Requirements

- **Windows 10 or 11.** The audio path uses WASAPI.
- **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/). Its installer includes Tkinter, which the GUI needs.
- **[VB-Audio Virtual Cable](https://vb-audio.com/Cable/)**, which is free. It provides the virtual microphone. Writing a virtual audio device needs a signed kernel driver, so mic-mixer uses VB-Cable instead of creating its own device. Install it and reboot.

## Setup

```bash
git clone <this repo>
cd mic-mixer
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

### GUI

```bash
python gui.py
```

1. **Microphone:** pick your real mic.
2. **Output:** pick `CABLE Input (VB-Audio Virtual Cable)`.
3. **Monitor (optional):** pick your headphones if you want to hear the sounds you play. Your own voice is not sent here.
4. Press **Start**.
5. In Discord or another app, set the input device to **`CABLE Output (VB-Audio Virtual Cable)`**.

To add sounds, drop files into the `sounds/` folder, use **Add files…**, or point **Folder…** at another directory. Double-click a sound, or select it and press **Play**.

| Control | What it does |
|---|---|
| Mic gain | Boosts or cuts your voice, from −20 to +30 dB |
| Sound volume | Volume of the sound effects |
| Mute mic | Sends only the sound effects |
| Noise suppression | Turns RNNoise on or off. It's greyed out if the DLL isn't installed, as explained below. |

Your device choices, sliders and sound folder are saved to `settings.json` next to the script.

### Command line

```bash
python mic_mixer.py --list                      # show audio devices
python mic_mixer.py --mic "USB" --monitor "Headphones"
```

`--mic`, `--out` and `--monitor` accept a device index or part of a device name. `--out` defaults to `CABLE Input`. Other options:

| Option | Default | |
|---|---|---|
| `--sounds DIR` | `sounds` | Folder of audio files |
| `--mic-gain X` | `1.0` | Mic gain multiplier, e.g. `8` ≈ +18 dB |
| `--sfx-gain X` | `0.8` | Sound effect volume |
| `--latency-blocks N` | `3` | Buffer target in 10 ms blocks. Raise it if you hear crackles. |
| `--no-denoise` | | Start with noise suppression off |

While it's running, type a sound's number to play it, or use: `s` stop all · `m` mute mic · `n` toggle noise suppression · `v <0-2>` sound volume · `g <x>` mic gain · `r` rescan sounds · `q` quit.

## Noise suppression (optional): building `rnnoise.dll`

Noise suppression uses [RNNoise](https://github.com/xiph/rnnoise). This repo doesn't include a prebuilt `rnnoise.dll`, because you shouldn't have to trust a binary from a stranger. mic-mixer runs fine without it; only the Noise suppression toggle is disabled. If you want it, build the DLL from the official source in a few minutes:

**1. Install the tools.** You need:
- [Git for Windows](https://git-scm.com/download/win). It includes **Git Bash**, which provides `sh`, `curl`, `tar` and `sha256sum`.
- The **Zig** C compiler, used as a self-contained Windows C compiler (no Visual Studio needed). The simplest way to get it is from PyPI, into a local folder:

  ```bash
  pip install --target zig ziglang==0.15.2
  ```

  This puts the compiler at `zig\ziglang\zig.exe`. You can also download it from [ziglang.org](https://ziglang.org/download/).

**2. Run the build script from Git Bash**, in the project folder:

```bash
ZIG=zig/ziglang/zig.exe sh native/build_rnnoise.sh
```

[`native/build_rnnoise.sh`](native/build_rnnoise.sh) does the following:

1. Clones `https://github.com/xiph/rnnoise` and checks out a pinned commit (`70f1d256acd4b34a572f999a05c87bf00b67730d`), so everyone builds the same code.
2. Downloads the matching pretrained model from `media.xiph.org`. It **verifies the model's SHA-256** against the `model_version` file in that commit, and stops if they don't match.
3. Compiles the same source files as the upstream `librnnoise` build into `native/rnnoise.dll`, then prints the DLL's SHA-256.
4. Deletes its temporary working folder. The `zig` folder from step 1 can be deleted too.

**3. Restart mic-mixer.** The Noise suppression toggle becomes available.

Notes:
- The DLL is about 15 MB, mostly the built-in model weights. It's portable C, with no SSE4.1 or AVX2 code paths.
- RNNoise processes audio in 10 ms frames and adds about 20 ms of delay to your voice. Sound effects bypass it, so they play without that delay.
- If you build it another way (MSVC, MSYS2 or CMake), any 64-bit `rnnoise.dll` that exports the standard `rnnoise_*` API will work. Put it at `native/rnnoise.dll`.

## Troubleshooting

- **Nobody hears me / the sounds:** the other app must use **CABLE *Output*** as its mic. mic-mixer writes to CABLE *Input*.
- **My voice is quiet:** raise **Mic gain**. Many USB mics need +15 to +25 dB.
- **Crackling:** use `--latency-blocks 4` (or higher) on the command line, and close other apps that hold the audio device in exclusive mode.
- **The sound effects are being filtered:** turn external noise suppression off for this input and use mic-mixer's own noise suppression instead. It cleans your voice before the sounds are mixed in, so they aren't treated as background noise.
- **A sound won't play:** the status bar shows the error. Malformed MP3 tags are a common cause; re-saving the file in an audio editor usually fixes it.

## Project layout

| File | |
|---|---|
| `gui.py` | Tkinter GUI |
| `theme.py` | Colours, fonts and custom widgets for the GUI |
| `mic_mixer.py` | Audio engine (mixer, drift-corrected buffers, limiter) and command-line interface |
| `resample.py` | Windowed-sinc resampler for sound files |
| `denoise.py` | ctypes wrapper around `rnnoise.dll` |
| `native/build_rnnoise.sh` | Reproducible RNNoise build script |
| `sounds/` | Default sound folder |

## Credits

### RNNoise

Noise suppression is provided by **[RNNoise](https://github.com/xiph/rnnoise)** by Jean-Marc Valin, developed with Xiph.Org and Mozilla. mic-mixer only loads a DLL that you build yourself from the unmodified upstream source; none of RNNoise's code or model is included in this repository.

> J.-M. Valin, *A Hybrid DSP/Deep Learning Approach to Real-Time Full-Band Speech Enhancement*, Proceedings of IEEE Multimedia Signal Processing (MMSP) Workshop, 2018. [arXiv:1709.08243](https://arxiv.org/abs/1709.08243)

RNNoise is distributed under the BSD 3-Clause license:

```
Copyright (c) 2007-2017, 2024 Jean-Marc Valin
Copyright (c) 2023 Amazon
Copyright (c) 2017, Mozilla
Copyright (c) 2005-2017, Xiph.Org Foundation
Copyright (c) 2003-2004, Mark Borgerding

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

- Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

- Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

- Neither the name of the Xiph.Org Foundation nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE FOUNDATION
OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Other

- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) by VB-Audio Software provides the virtual microphone. It is donationware and installed separately.
- [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) (PortAudio), [python-soundfile](https://github.com/bastibe/python-soundfile) (libsndfile) and [NumPy](https://numpy.org/) do the audio I/O, file decoding and DSP.
