"""RNNoise noise suppression for the mic signal.

Loads native/rnnoise.dll, built from the official xiph/rnnoise source by
native/build_rnnoise.sh. If the DLL is missing, available() is False and the
app simply runs without suppression.
"""

import ctypes
from pathlib import Path

import numpy as np

DLL_PATH = Path(__file__).with_name("native") / "rnnoise.dll"
FRAME = 480     # RNNoise works on 10 ms frames...
RATE = 48000    # ...at 48 kHz only.

_lib = None
_load_error = None


def _load():
    global _lib, _load_error
    if _lib is not None or _load_error is not None:
        return _lib
    try:
        lib = ctypes.CDLL(str(DLL_PATH))
    except OSError as e:
        _load_error = f"{DLL_PATH.name} not loaded: {e}"
        return None
    lib.rnnoise_create.argtypes = [ctypes.c_void_p]
    lib.rnnoise_create.restype = ctypes.c_void_p
    lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
    lib.rnnoise_destroy.restype = None
    lib.rnnoise_process_frame.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
    lib.rnnoise_process_frame.restype = ctypes.c_float
    lib.rnnoise_get_frame_size.restype = ctypes.c_int
    if lib.rnnoise_get_frame_size() != FRAME:
        _load_error = "unexpected RNNoise frame size"
        return None
    _lib = lib
    return _lib


def available():
    return _load() is not None


def unavailable_reason():
    _load()
    return _load_error


class Denoiser:
    """Feeds audio of any block size to RNNoise, which only takes 480 samples at a time.

    At the app's default block size of 480 this adds no delay. With other
    sizes, a little silence is added once at the start, so every call can
    return exactly as many samples as it was given.
    """

    def __init__(self):
        lib = _load()
        if lib is None:
            raise RuntimeError(_load_error)
        self.lib = lib
        self.state = lib.rnnoise_create(None)
        self.frame = np.zeros(FRAME, dtype=np.float32)
        self.ptr = self.frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        self.pending = np.zeros(0, dtype=np.float32)
        self.ready = np.zeros(0, dtype=np.float32)
        self.speech_prob = 0.0

    def process(self, block):
        """Denoise float32 samples in [-1, 1]; returns the same number of samples."""
        self.pending = np.concatenate((self.pending, block))
        done = []
        while len(self.pending) >= FRAME:
            # RNNoise expects samples from -32768 to 32767 rather than -1 to 1.
            # It writes its result back into the same buffer.
            self.frame[:] = self.pending[:FRAME] * 32768.0
            self.speech_prob = self.lib.rnnoise_process_frame(self.state, self.ptr, self.ptr)
            done.append(self.frame / 32768.0)
            self.pending = self.pending[FRAME:]
        if done:
            self.ready = np.concatenate([self.ready, *done])
        if len(self.ready) < len(block):
            pad = np.zeros(len(block) - len(self.ready), dtype=np.float32)
            self.ready = np.concatenate((pad, self.ready))
        out, self.ready = self.ready[:len(block)], self.ready[len(block):]
        return out.astype(np.float32, copy=False)

    def close(self):
        if self.state:
            self.lib.rnnoise_destroy(self.state)
            self.state = None

    def __del__(self):
        self.close()
