"""Tkinter front end for the mic mixer.

Run:  .venv/Scripts/python gui.py
"""

import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import sounddevice as sd

import denoise
import theme
from mic_mixer import AUDIO_EXTS, AudioEngine, Mixer, list_sounds

RATE = 48000
BLOCK = 480
SETTINGS_PATH = Path(__file__).with_name("settings.json")
NO_MONITOR = "(none)"
MIC_DB_MIN, MIC_DB_MAX = -20.0, 30.0


def wasapi_devices():
    """Return ({name: index} inputs, {name: index} outputs, default input name).

    Windows lists each device several times, once per audio system (MME,
    DirectSound, WASAPI, WDM-KS). Only the WASAPI copies are shown, since
    they have the least delay. If WASAPI is missing, everything is listed.
    """
    hostapis = sd.query_hostapis()
    wasapi = next((i for i, h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
    inputs, outputs = {}, {}
    for i, d in enumerate(sd.query_devices()):
        if wasapi is not None and d["hostapi"] != wasapi:
            continue
        if d["max_input_channels"] > 0:
            inputs[d["name"]] = i
        if d["max_output_channels"] > 0:
            outputs[d["name"]] = i
    default_in = None
    if wasapi is not None:
        idx = hostapis[wasapi]["default_input_device"]
        if idx >= 0:
            default_in = sd.query_devices(idx)["name"]
    return inputs, outputs, default_in


class App:
    def __init__(self, root):
        self.root = root
        self.mixer = Mixer(RATE, mic_gain=1.0, sfx_gain=0.8)
        self.engine = None
        self.settings = self.load_settings()
        self.sounds_dir = Path(self.settings.get("sounds_dir", "sounds"))
        self.extra_files = [Path(p) for p in self.settings.get("extra_files", [])
                            if Path(p).exists()]
        self.sounds = []

        theme.apply(root)
        root.title("Mic Mixer")
        root.geometry("640x740")
        root.minsize(560, 660)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.build_header(root)
        main = ttk.Frame(root, padding=(16, 14, 16, 12))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        self.build_devices(main)
        self.build_sounds(main)
        self.build_controls(main)

        self.status = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.status, style="Faint.TLabel").grid(
            row=3, column=0, sticky="w", pady=(10, 0))

        self.refresh_sounds()
        self.update_meter()
        self.start()

    # ---- layout -----------------------------------------------------------

    def build_header(self, root):
        """Wordmark + status pill, underlined by the cyan-to-blue gradient."""
        bar = ttk.Frame(root, padding=(16, 12))
        bar.pack(fill="x")
        ttk.Label(bar, text=">", style="Prompt.TLabel").pack(side="left")
        ttk.Label(bar, text="mic-mixer", style="Title.TLabel").pack(side="left", padx=(8, 0))

        pill = tk.Frame(bar, bg=theme.BG, highlightthickness=1,
                        highlightbackground=theme.LINE, highlightcolor=theme.LINE)
        pill.pack(side="right")
        self.dot = tk.Canvas(pill, width=16, height=16, bg=theme.BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(10, 2), pady=4)
        self.pill_text = tk.StringVar(value="stopped")
        ttk.Label(pill, textvariable=self.pill_text, style="Pill.TLabel").pack(
            side="left", padx=(2, 12))

        line = tk.Canvas(root, height=2, bg=theme.BG, highlightthickness=0)
        line.pack(fill="x")
        line.bind("<Configure>", lambda e: theme.gradient(line, e.width, 2))

    def set_running(self, running):
        self.dot.delete("all")
        if running:
            self.dot.create_oval(0, 0, 16, 16, fill=theme.ACCENT_SOFT_BG, outline="")
            self.dot.create_oval(4, 4, 12, 12, fill=theme.ACCENT, outline="")
        else:
            self.dot.create_oval(4, 4, 12, 12, fill=theme.LINE_STRONG, outline="")
        self.pill_text.set("live" if running else "stopped")
        self.run_btn.configure(text="Stop" if running else "Start",
                               style="TButton" if running else "Accent.TButton")

    def build_devices(self, parent):
        outer, box = theme.card(parent, "~/devices")
        outer.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        self.inputs, self.outputs, default_in = wasapi_devices()
        out_names = list(self.outputs)

        self.mic_var = tk.StringVar(value=self.pick(
            self.settings.get("mic"), list(self.inputs), default_in))
        self.out_var = tk.StringVar(value=self.pick(
            self.settings.get("out"), out_names,
            next((n for n in out_names if "CABLE Input" in n), None)))
        self.mon_var = tk.StringVar(value=self.pick(
            self.settings.get("monitor"), [NO_MONITOR] + out_names, NO_MONITOR))

        rows = [
            ("Microphone", self.mic_var, list(self.inputs)),
            ("Virtual mic output", self.out_var, out_names),
            ("Monitor (hear sounds)", self.mon_var, [NO_MONITOR] + out_names),
        ]
        for r, (label, var, values) in enumerate(rows):
            ttk.Label(box, text=label, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", padx=(0, 12), pady=3)
            combo = ttk.Combobox(box, textvariable=var, values=values, state="readonly")
            combo.grid(row=r, column=1, sticky="ew", pady=3)
            combo.bind("<<ComboboxSelected>>", lambda e: self.restart())

        self.run_btn = ttk.Button(box, text="Start", style="Accent.TButton",
                                  command=self.toggle_run, width=7)
        self.run_btn.grid(row=0, column=2, sticky="ew", padx=(12, 0), pady=3)

    def build_sounds(self, parent):
        outer, box = theme.card(parent, "~/sounds")
        outer.grid(row=1, column=0, sticky="nsew", pady=12)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        # Styled like .terminal-body: page-dark inset, mono text, pink selection.
        self.listbox = tk.Listbox(
            box, activestyle="none", height=8, font=theme.MONO,
            bg=theme.BG, fg=theme.MUTED, bd=0, relief="flat",
            highlightthickness=1, highlightbackground=theme.LINE,
            highlightcolor=theme.FAINT, selectbackground=theme.ACCENT,
            selectforeground=theme.ACCENT_INK, selectborderwidth=0)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        self.listbox.bind("<Double-Button-1>", lambda e: self.play_selected())
        self.listbox.bind("<Return>", lambda e: self.play_selected())
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        playback = ttk.Frame(box, style="Card.TFrame")
        playback.grid(row=0, column=2, sticky="n", padx=(12, 0))
        ttk.Button(playback, text="▶  Play", command=self.play_selected,
                   style="Accent.TButton").pack(fill="x", pady=(0, 3))
        ttk.Button(playback, text="■  Stop all",
                   command=self.mixer.stop_all).pack(fill="x", pady=3)

        files = ttk.Frame(box, style="Card.TFrame")
        files.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        for text, cmd in [("Add files…", self.add_files), ("Remove", self.remove_selected),
                          ("Folder…", self.choose_folder), ("Rescan", self.refresh_sounds)]:
            ttk.Button(files, text=text, command=cmd).pack(side="left", padx=(0, 6))

        self.folder_label = tk.StringVar()
        ttk.Label(box, textvariable=self.folder_label, style="FaintCard.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def build_controls(self, parent):
        outer, box = theme.card(parent, "~/controls")
        outer.grid(row=2, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        # Mic gain is in dB: a quiet mic needs far more than the +6 dB a 0-200%
        # slider gives, and the limiter in Mixer keeps high gain from clipping.
        saved_db = self.settings.get("mic_gain_db")
        if saved_db is None:  # settings from before the dB slider stored a multiplier
            saved_db = 20 * math.log10(max(self.settings.get("mic_gain", 1.0), 0.1))
        self.mic_gain_db = tk.DoubleVar(value=min(max(saved_db, MIC_DB_MIN), MIC_DB_MAX))
        self.sfx_gain = tk.DoubleVar(value=self.settings.get("sfx_gain", 0.8))
        self.muted = tk.BooleanVar(value=False)

        sliders = [
            ("Mic gain", self.mic_gain_db, MIC_DB_MIN, MIC_DB_MAX, lambda v: f"{v:+.0f} dB"),
            ("Sound volume", self.sfx_gain, 0.0, 2.0, lambda v: f"{v:.0%}"),
        ]
        for r, (label, var, lo, hi, fmt) in enumerate(sliders):
            ttk.Label(box, text=label, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", padx=(0, 12), pady=4)
            theme.Slider(box, var, lo, hi, command=self.apply_gains).grid(
                row=r, column=1, sticky="ew", pady=4)
            value = ttk.Label(box, width=7, anchor="e", style="Value.TLabel")
            value.grid(row=r, column=2, padx=(10, 0))
            var.trace_add("write", lambda *a, v=var, l=value, f=fmt: l.configure(
                text=f(v.get())))
            value.configure(text=fmt(var.get()))

        ttk.Checkbutton(box, text="Mute mic", variable=self.muted, style="Chip.Toolbutton",
                        command=self.apply_gains).grid(row=0, column=3, sticky="ew", padx=(14, 0))

        self.denoise_on = tk.BooleanVar(value=self.settings.get("noise_suppression", True)
                                        and denoise.available())
        chip = ttk.Checkbutton(box, text="Noise suppression", variable=self.denoise_on,
                               style="Chip.Toolbutton", command=self.apply_gains)
        chip.grid(row=1, column=3, sticky="ew", padx=(14, 0), pady=(6, 0))
        if not denoise.available():
            chip.state(["disabled"])

        self.meters = []
        for r, (label, level) in enumerate([("Mic level", lambda: self.mixer.mic_level),
                                            ("Output level", lambda: self.mixer.level)],
                                           start=2):
            ttk.Label(box, text=label, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=(10 if r == 2 else 6, 0))
            meter = tk.Canvas(box, height=8, bg=theme.LINE, highlightthickness=0)
            meter.grid(row=r, column=1, columnspan=3, sticky="ew",
                       pady=(10 if r == 2 else 6, 0))
            meter.bind("<Configure>", self.draw_meter_track)
            self.meters.append((meter, level))
        self.apply_gains()

    @staticmethod
    def draw_meter_track(event):
        """Draw the full gradient, then a grey cover that update_meter slides
        aside to reveal the level."""
        meter = event.widget
        theme.gradient(meter, event.width, event.height)
        meter.delete("cover")
        meter.create_rectangle(0, 0, event.width, event.height,
                               fill=theme.LINE, outline="", tags="cover")

    # ---- behaviour --------------------------------------------------------

    @staticmethod
    def pick(saved, options, fallback):
        if saved in options:
            return saved
        if fallback in options:
            return fallback
        return options[0] if options else ""

    def apply_gains(self):
        self.mixer.mic_gain = 10 ** (self.mic_gain_db.get() / 20)
        self.mixer.sfx_gain = self.sfx_gain.get()
        self.mixer.mic_muted = self.muted.get()
        self.mixer.noise_suppression = self.denoise_on.get()

    def start(self):
        mic = self.inputs.get(self.mic_var.get())
        out = self.outputs.get(self.out_var.get())
        mon = self.outputs.get(self.mon_var.get())  # None for "(none)"
        if out is None:
            self.status.set("Pick a virtual mic output (install VB-Cable if none is listed).")
            self.set_running(False)
            return
        self.engine = AudioEngine(self.mixer, mic, out, mon, RATE, BLOCK)
        try:
            self.engine.start()
        except Exception as e:
            self.engine = None
            self.status.set(f"Could not start audio: {e}")
            self.set_running(False)
            return
        target = ("CABLE Output" if "CABLE Input" in self.out_var.get()
                  else f"the recording side of {self.out_var.get()}")
        hint = f"In Discord/OBS/etc., choose {target} as your mic."
        if not denoise.available():
            hint += f"  Noise suppression unavailable ({denoise.unavailable_reason()})."
        self.status.set(hint)
        self.set_running(True)

    def stop(self):
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.status.set("Audio stopped.")
        self.set_running(False)

    def restart(self):
        self.stop()
        self.start()

    def toggle_run(self):
        if self.engine:
            self.stop()
        else:
            self.start()

    def refresh_sounds(self):
        self.sounds_dir.mkdir(exist_ok=True)
        found = list_sounds(self.sounds_dir)
        self.sounds = found + [p for p in self.extra_files if p not in found]
        self.listbox.delete(0, "end")
        for p in self.sounds:
            self.listbox.insert("end", f" {p.name}" if p.parent == self.sounds_dir
                                else f" {p.name}   ({p.parent})")
        self.folder_label.set(f"Folder: {self.sounds_dir.resolve()}")
        self.mixer.preload(self.sounds)

    def play_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            self.status.set("Select a sound first (double-click also plays).")
            return
        self.mixer.play(self.sounds[sel[0]])  # load failures arrive via mixer.errors

    def add_files(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTS))
        paths = filedialog.askopenfilenames(
            title="Add sound files", filetypes=[("Audio", patterns), ("All files", "*.*")])
        for p in map(Path, paths):
            if p not in self.extra_files:
                self.extra_files.append(p)
        self.refresh_sounds()

    def remove_selected(self):
        """Hide an added file from the list. Files in the folder are never deleted."""
        sel = self.listbox.curselection()
        if not sel:
            return
        path = self.sounds[sel[0]]
        if path in self.extra_files:
            self.extra_files.remove(path)
            self.refresh_sounds()
        else:
            self.status.set("That file lives in the sounds folder; move it out to remove it.")

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose sounds folder",
                                         initialdir=self.sounds_dir.resolve())
        if folder:
            self.sounds_dir = Path(folder)
            self.refresh_sounds()

    def update_meter(self):
        """Meters use a decibel scale from -60 dB (near silent) to 0 dB (maximum)."""
        for meter, level in self.meters:
            peak = level()
            frac = 0.0 if peak <= 1e-3 else min(1.0, 1 + 20 * math.log10(peak) / 60)
            width = meter.winfo_width()
            meter.coords("cover", int(width * frac), 0, width, meter.winfo_height())
        # Load errors happen on background threads, but Tk can only be
        # updated from this one, so they're queued and shown here.
        while not self.mixer.errors.empty():
            path, e = self.mixer.errors.get()
            self.status.set(f"Could not load {Path(path).name}: {e}")
        self.root.after(50, self.update_meter)

    # ---- settings ---------------------------------------------------------

    def load_settings(self):
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (OSError, ValueError):
            return {}

    def on_close(self):
        self.stop()
        try:
            SETTINGS_PATH.write_text(json.dumps({
                "mic": self.mic_var.get(),
                "out": self.out_var.get(),
                "monitor": self.mon_var.get(),
                "mic_gain_db": round(self.mic_gain_db.get(), 1),
                "sfx_gain": round(self.sfx_gain.get(), 3),
                "noise_suppression": self.denoise_on.get(),
                "sounds_dir": str(self.sounds_dir),
                "extra_files": [str(p) for p in self.extra_files],
            }, indent=2))
        except OSError:
            pass
        self.mixer.close()
        self.root.destroy()


def main():
    theme.enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
