"""Mic mixer prototype: real mic + sound files -> virtual cable.

Captures your microphone, mixes in audio files on demand, and writes the
result to a playback device (e.g. VB-Audio "CABLE Input"). Other apps then
select the cable's recording side ("CABLE Output") as their microphone.

Usage:
    python mic_mixer.py --list
    python mic_mixer.py --mic "Microphone" --out "CABLE Input"
"""

import argparse
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

import denoise
from resample import OVERSAMPLE, kernel_table, resample

AUDIO_EXTS = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}


class RingBuffer:
    """A first-in, first-out audio buffer: one device writes, another reads.

    If it gets too full, the oldest audio is dropped. If it runs empty, reads
    are filled with silence instead of waiting. DriftReader normally keeps it
    well away from both.
    """

    def __init__(self, max_frames):
        self.buf = np.zeros(max_frames, dtype=np.float32)
        self.max = max_frames
        self.start = 0
        self.size = 0
        self.lock = threading.Lock()

    def write(self, data):
        with self.lock:
            n = len(data)
            if n >= self.max:
                data = data[-self.max:]
                n = self.max
            overflow = self.size + n - self.max
            if overflow > 0:
                self.start = (self.start + overflow) % self.max
                self.size -= overflow
            end = (self.start + self.size) % self.max
            first = min(n, self.max - end)
            self.buf[end:end + first] = data[:first]
            self.buf[:n - first] = data[first:]
            self.size += n

    def read(self, n):
        out = np.zeros(n, dtype=np.float32)
        with self.lock:
            take = min(n, self.size)
            first = min(take, self.max - self.start)
            out[:first] = self.buf[self.start:self.start + first]
            out[first:take] = self.buf[:take - first]
            self.start = (self.start + take) % self.max
            self.size -= take
        return out

    def discard(self, n):
        with self.lock:
            n = min(n, self.size)
            self.start = (self.start + n) % self.max
            self.size -= n


class DriftReader:
    """Reads audio that a device with a slightly different clock wrote.

    Every audio device's clock is a tiny bit faster or slower than every
    other's. So the mic produces audio slightly faster or slower than the
    cable uses it, and a plain buffer would slowly fill up (growing delay,
    then skipped audio) or run empty (dropouts).

    This reader keeps the buffer at a steady level by playing very slightly
    faster when it's too full, and slower when it's too empty.
    """

    MAX_ADJUST = 0.005  # largest speed change (0.5%), far too small to hear
    KP = 0.004          # how strongly to react to the buffer being off target right now
    KI = KP * KP / 4    # how fast to learn a steady clock difference; this value
                        # settles on the right speed without overshooting
    SMOOTHING = 0.02    # averages the buffer level over ~0.5 s, so jitter doesn't cause wobble
    # Changing speed means making new samples between the original ones. Each
    # new sample averages 32 neighbours on each side, the same way
    # resample.py does. Using fewer made "s" and "t" sounds dull.
    HALF_TAPS = 32
    CUTOFF = 0.94  # keep sounds up to 22.6 kHz, above the range of hearing
    TABLE = kernel_table(CUTOFF, HALF_TAPS).astype(np.float32)  # the averaging weights
    TAPS = np.arange(-HALF_TAPS + 1, HALF_TAPS + 1)  # neighbour offsets: -31 .. +32

    def __init__(self, ring, block, target_blocks=3):
        # The level is checked just before each read, so a target of 3 blocks
        # leaves 2 in reserve. That covers a mic block arriving late; with
        # a target of 2, real devices ran dry regularly.
        self.ring = ring
        self.block = block
        self.target = block * target_blocks
        self.underruns = 0  # times the buffer ran dry and had to re-fill
        self.resyncs = 0    # times it was far too full and was cut back
        self.reset()

    def reset(self):
        # Audio taken from the buffer, including the last few samples already
        # played, which the averaging still needs.
        self.src = np.zeros(self.HALF_TAPS, dtype=np.float32)
        self.phase = float(self.HALF_TAPS)        # position in src of the next output sample
        self.ratio = 1.0                          # current playback speed (1.0 = normal)
        self.integral = 0.0
        self.fill_avg = 0.0
        self.primed = False

    def read(self, frames):
        fill = self.ring.size
        if not self.primed:
            # Wait until the buffer reaches its target before playing. The
            # learned speed is reset too, since running empty threw it off.
            if fill < self.target:
                return np.zeros(frames, dtype=np.float32)
            self.primed = True
            self.fill_avg = float(fill)
            self.integral = 0.0
        if fill > self.target + 4 * self.block:
            # Far too much buffered (e.g. after a hiccup): skip ahead instead
            # of spending many seconds catching up at 0.5% faster.
            self.ring.discard(fill - self.target)
            fill = self.target
            self.fill_avg = float(fill)
            self.resyncs += 1

        # Pick a speed from how far the buffer is from its target (in blocks),
        # plus a slowly learned correction for the steady clock difference.
        self.fill_avg += self.SMOOTHING * (fill - self.fill_avg)
        error = (self.fill_avg - self.target) / self.block
        self.integral = float(np.clip(self.integral + self.KI * error,
                                      -self.MAX_ADJUST, self.MAX_ADJUST))
        self.ratio = 1.0 + float(np.clip(self.KP * error + self.integral,
                                         -self.MAX_ADJUST, self.MAX_ADJUST))

        # Where each output sample falls in the input, at the chosen speed.
        positions = self.phase + np.arange(frames) * self.ratio
        need = int(positions[-1]) + self.HALF_TAPS + 1 - len(self.src)
        if need > 0:
            if self.ring.size < need:
                self.underruns += 1
                self.primed = False  # refill the buffer after this block
            self.src = np.concatenate((self.src, self.ring.read(need)))

        # Each new sample is a weighted average of its neighbours. Closer
        # neighbours count more; the weights come from TABLE.
        base = positions.astype(np.int64)
        frac = (positions - base).astype(np.float32)
        idx = (self.TAPS - frac[:, None] + self.HALF_TAPS) * OVERSAMPLE
        i = idx.astype(np.int64)
        a = idx - i
        w = self.TABLE[i] * (1.0 - a) + self.TABLE[i + 1] * a
        samples = self.src[base[:, None] + self.TAPS]
        out = (w * samples).sum(axis=1) / w.sum(axis=1)  # dividing keeps the volume the same

        next_pos = positions[-1] + self.ratio
        cut = int(next_pos) - (self.HALF_TAPS - 1)  # forget old samples, except the ones still needed
        self.src = self.src[cut:]
        self.phase = next_pos - cut
        return out.astype(np.float32, copy=False)


class Limiter:
    """Stops loud audio from distorting by turning it down automatically.

    If a block would go over `limit`, the volume drops instantly to fit.
    It then eases back up, changing gradually across each block so there
    are no clicks.
    """

    def __init__(self, limit=0.89, release=0.05):
        self.limit = limit      # highest allowed peak, just under the maximum (-1 dB)
        self.release = release  # how fast volume recovers: 5% of the way per block (~200 ms)
        self.gain = 1.0

    def process(self, x):
        peak = float(np.abs(x).max()) if len(x) else 0.0
        target = self.limit / peak if peak > self.limit else 1.0
        if target < self.gain:
            self.gain = target
            return x * target
        new = self.gain + (target - self.gain) * self.release
        ramp = np.linspace(self.gain, new, len(x), dtype=np.float32)
        self.gain = new
        return x * ramp


class Mixer:
    def __init__(self, rate, mic_gain, sfx_gain):
        self.rate = rate
        self.mic_gain = mic_gain
        self.sfx_gain = sfx_gain
        self.mic_muted = False
        self.noise_suppression = True  # only applied when RNNoise is available
        self.level = 0.0      # peak of the last output block, for meters
        self.mic_level = 0.0  # peak of the mic after gain, before the limiter
        self.limiter = Limiter()
        self.voices = []  # list of [samples, position, tag]
        self.lock = threading.Lock()
        self.generation = 0  # raised by stop_all, so sounds still loading don't start afterwards
        # Decoding happens on worker threads so Play never blocks the UI. Each
        # file's Future is cached; once done it holds the decoded samples.
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sound-loader")
        self.loads = {}  # (resolved path, mtime_ns) -> Future
        self.loads_lock = threading.Lock()
        self.errors = queue.SimpleQueue()  # (path, exception) from failed loads

    def _decode(self, path):
        data, file_rate = sf.read(path, dtype="float32", always_2d=True)
        return resample(data.mean(axis=1), file_rate, self.rate)

    def _load(self, path):
        path = Path(path)
        key = (path.resolve(), path.stat().st_mtime_ns)  # edited files re-decode
        with self.loads_lock:
            future = self.loads.get(key)
            if future is None:
                future = self.pool.submit(self._decode, path)
                self.loads[key] = future
        return key, future

    def preload(self, paths):
        """Start decoding files in the background so they play instantly."""
        for path in paths:
            try:
                self._load(path)
            except OSError as e:
                self.errors.put((path, e))

    def play(self, path, tag=None):
        """Play a sound as soon as it's decoded: immediately if preloaded.

        `tag` identifies the sound in playing(); it defaults to the path.
        Failures are reported through self.errors, not raised.
        """
        tag = str(path) if tag is None else tag
        generation = self.generation
        try:
            key, future = self._load(path)
        except OSError as e:
            self.errors.put((path, e))
            return

        def start(done):
            try:
                samples = done.result()
            except Exception as e:
                with self.loads_lock:
                    self.loads.pop(key, None)  # allow a retry
                self.errors.put((path, e))
                return
            with self.lock:
                if generation == self.generation:
                    self.voices.append([samples, 0, tag])

        future.add_done_callback(start)

    def playing(self):
        """Tags of the sounds playing right now (see play)."""
        with self.lock:
            return {voice[2] for voice in self.voices}

    def stop_all(self):
        with self.lock:
            self.generation += 1
            self.voices.clear()

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

    def render(self, mic, frames):
        """Return (limited mic + sfx mix for the cable, raw sfx for monitoring)."""
        sfx = np.zeros(frames, dtype=np.float32)
        with self.lock:
            alive = []
            for voice in self.voices:
                samples, pos = voice[0], voice[1]
                chunk = samples[pos:pos + frames]
                sfx[:len(chunk)] += chunk * self.sfx_gain
                voice[1] = pos + frames
                if voice[1] < len(samples):
                    alive.append(voice)
            self.voices = alive
        if self.mic_muted:
            self.mic_level = 0.0
            mix = sfx
        else:
            mic = mic * self.mic_gain
            self.mic_level = float(np.abs(mic).max()) if frames else 0.0
            mix = sfx + mic
        # One limiter on the whole mix, so voice and sounds together get loud
        # instead of clipping. The clip is only a safety net.
        mix = np.clip(self.limiter.process(mix), -1.0, 1.0)
        self.level = float(np.abs(mix).max()) if frames else 0.0
        return mix, sfx


def wasapi_settings(dev):
    """Have Windows convert sample rates for us.

    Without this, WASAPI devices refuse any sample rate except their own.
    Other audio systems don't need it.
    """
    if dev is None:
        return None
    api = sd.query_hostapis()[sd.query_devices(dev)["hostapi"]]["name"]
    return sd.WasapiSettings(auto_convert=True) if "WASAPI" in api else None


class AudioEngine:
    """Owns the mic, cable and optional monitor streams around a Mixer."""

    def __init__(self, mixer, mic_dev, out_dev, mon_dev=None, rate=48000,
                 block=480, on_status=None, latency_blocks=3):
        self.mixer = mixer
        self.mic_dev = mic_dev
        self.out_dev = out_dev
        self.mon_dev = mon_dev
        self.rate = rate
        self.block = block
        self.on_status = on_status
        self.ring = RingBuffer(max_frames=block * 8)
        self.mon_ring = RingBuffer(max_frames=block * 8)
        # Each buffer sits between two devices with separate clocks, so each
        # gets a DriftReader to make up the difference. It keeps about
        # latency_blocks buffered (3 blocks is about 25 ms of delay).
        self.mic_reader = DriftReader(self.ring, block, latency_blocks)
        self.mon_reader = DriftReader(self.mon_ring, block, latency_blocks)
        self.mon_limiter = Limiter()
        self.streams = []
        self.denoiser = None

    def _report(self, status):
        if status and self.on_status:
            self.on_status(status)

    def _on_input(self, indata, frames, time, status):
        self._report(status)
        mic = indata[:, 0].copy()
        # Suppress noise on the mic only, before sound files are mixed in, so
        # they reach the cable untouched.
        if self.denoiser and self.mixer.noise_suppression:
            mic = self.denoiser.process(mic)
        self.ring.write(mic)

    def _on_output(self, outdata, frames, time, status):
        self._report(status)
        mixed, sfx = self.mixer.render(self.mic_reader.read(frames), frames)
        outdata[:] = mixed[:, None]
        if self.mon_dev is not None:
            self.mon_ring.write(sfx)

    def _on_monitor(self, outdata, frames, time, status):
        out = self.mon_limiter.process(self.mon_reader.read(frames))
        outdata[:] = np.clip(out, -1.0, 1.0)[:, None]

    def _output_stream(self, dev, callback):
        return sd.OutputStream(
            device=dev, channels=min(2, sd.query_devices(dev)["max_output_channels"]),
            samplerate=self.rate, blocksize=self.block, dtype="float32",
            latency="low", callback=callback, extra_settings=wasapi_settings(dev))

    def start(self):
        if denoise.available() and self.rate == denoise.RATE:
            self.denoiser = denoise.Denoiser()
        try:
            self.streams = [
                sd.InputStream(device=self.mic_dev, channels=1, samplerate=self.rate,
                               blocksize=self.block, dtype="float32", latency="low",
                               callback=self._on_input,
                               extra_settings=wasapi_settings(self.mic_dev)),
                self._output_stream(self.out_dev, self._on_output),
            ]
            if self.mon_dev is not None:
                self.streams.append(self._output_stream(self.mon_dev, self._on_monitor))
            for stream in self.streams:
                stream.start()
        except Exception:
            self.stop()
            raise

    def stop(self):
        for stream in self.streams:
            stream.stop()
            stream.close()
        self.streams = []
        if self.denoiser:
            self.denoiser.close()
            self.denoiser = None
        self.mixer.level = 0.0
        self.mixer.mic_level = 0.0


def find_device(query, kind):
    """Resolve a device by index or case-insensitive name substring."""
    if query is None:
        return None
    if query.isdigit():
        return int(query)
    channels_key = f"max_{kind}_channels"
    matches = [
        i for i, d in enumerate(sd.query_devices())
        if query.lower() in d["name"].lower() and d[channels_key] > 0
    ]
    if not matches:
        sys.exit(f"No {kind} device matching {query!r}. Run with --list.")
    # Prefer WASAPI, which gives the lowest latency on Windows.
    hostapis = sd.query_hostapis()
    for i in matches:
        if "WASAPI" in hostapis[sd.query_devices(i)["hostapi"]]["name"]:
            return i
    return matches[0]


def list_devices():
    hostapis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        kinds = []
        if d["max_input_channels"] > 0:
            kinds.append("in")
        if d["max_output_channels"] > 0:
            kinds.append("out")
        api = hostapis[d["hostapi"]]["name"]
        print(f"{i:3d}  [{'/'.join(kinds):6s}] {d['name']}  ({api})")


def list_sounds(folder):
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXTS)


def print_help(sounds):
    print("\nSounds:")
    if not sounds:
        print("  (none - drop audio files into the sounds folder, then type 'r')")
    for i, p in enumerate(sounds, 1):
        print(f"  {i:2d}  {p.name}")
    print("\nCommands: <number> play | s stop | m mute mic | n noise suppression"
          " | v <0-2> sfx volume | g <x> mic gain multiplier (e.g. 8 = +18 dB)"
          " | r rescan | q quit\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="list audio devices and exit")
    parser.add_argument("--mic", help="input device (index or name substring)")
    parser.add_argument("--out", default="CABLE Input",
                        help="output device (default: 'CABLE Input')")
    parser.add_argument("--monitor",
                        help="also play the sound files (not your mic) on this "
                             "device, e.g. your headset, so you can hear them")
    parser.add_argument("--sounds", default="sounds", help="folder of audio files")
    parser.add_argument("--rate", type=int, default=48000)
    parser.add_argument("--block", type=int, default=480, help="frames per callback")
    parser.add_argument("--latency-blocks", type=int, default=3,
                        help="mic buffering in blocks; raise if you hear dropouts")
    parser.add_argument("--mic-gain", type=float, default=1.0)
    parser.add_argument("--sfx-gain", type=float, default=0.8)
    parser.add_argument("--no-denoise", action="store_true",
                        help="start with RNNoise mic noise suppression off")
    args = parser.parse_args()

    if args.list:
        list_devices()
        return

    mic_dev = find_device(args.mic, "input")
    out_dev = find_device(args.out, "output")
    mon_dev = find_device(args.monitor, "output")

    sounds_dir = Path(args.sounds)
    sounds_dir.mkdir(exist_ok=True)
    sounds = list_sounds(sounds_dir)

    mixer = Mixer(args.rate, args.mic_gain, args.sfx_gain)
    mixer.noise_suppression = not args.no_denoise
    mixer.preload(sounds)
    engine = AudioEngine(mixer, mic_dev, out_dev, mon_dev, args.rate, args.block,
                         on_status=lambda s: print(s, file=sys.stderr),
                         latency_blocks=args.latency_blocks)

    print(f"Mic: {sd.query_devices(mic_dev if mic_dev is not None else sd.default.device[0])['name']}")
    print(f"Out: {sd.query_devices(out_dev)['name']}")
    if mon_dev is not None:
        print(f"Monitor: {sd.query_devices(mon_dev)['name']}")

    engine.start()
    if engine.denoiser:
        print(f"Noise suppression: {'on' if mixer.noise_suppression else 'off'}")
    elif not denoise.available():
        print(f"Noise suppression unavailable ({denoise.unavailable_reason()})")
    else:
        print(f"Noise suppression unavailable (needs --rate {denoise.RATE})")
    print_help(sounds)
    try:
        while True:
            try:
                cmd = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            while not mixer.errors.empty():
                path, e = mixer.errors.get()
                print(f"error loading {Path(path).name}: {e}")
            if not cmd:
                continue
            parts = cmd.split()
            try:
                if cmd == "q":
                    break
                elif cmd == "s":
                    mixer.stop_all()
                elif cmd == "m":
                    mixer.mic_muted = not mixer.mic_muted
                    print("mic muted" if mixer.mic_muted else "mic live")
                elif cmd == "n":
                    mixer.noise_suppression = not mixer.noise_suppression
                    state = "on" if mixer.noise_suppression else "off"
                    print(f"noise suppression {state}" if engine.denoiser
                          else "noise suppression unavailable")
                elif parts[0] == "v" and len(parts) == 2:
                    mixer.sfx_gain = float(parts[1])
                elif parts[0] == "g" and len(parts) == 2:
                    mixer.mic_gain = float(parts[1])
                elif cmd == "r":
                    sounds = list_sounds(sounds_dir)
                    mixer.preload(sounds)
                    print_help(sounds)
                elif cmd.isdigit() and 1 <= int(cmd) <= len(sounds):
                    mixer.play(sounds[int(cmd) - 1])
                else:
                    print_help(sounds)
            except ValueError as e:
                print(f"error: {e}")
    finally:
        engine.stop()
        mixer.close()


if __name__ == "__main__":
    main()
