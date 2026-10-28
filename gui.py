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
BASE_DIR = Path(__file__).parent
SETTINGS_PATH = BASE_DIR / "settings.json"
NO_MONITOR = "(none)"
MIC_DB_MIN, MIC_DB_MAX = -20.0, 30.0
NAME_MAX = 32       # longest sound name allowed
LABEL_MAX = 18      # longer names are shortened with "…" on their button
PAD_WIDTH = 150     # soundboard buttons are at least this wide, in pixels


def path_to_setting(path):
    """Files inside the project are saved relative to it, so the board still
    works if the project folder is moved."""
    try:
        return path.resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except ValueError:
        return str(path)


def path_from_setting(text):
    path = Path(text)
    return path if path.is_absolute() else BASE_DIR / path


def short_label(name):
    return name if len(name) <= LABEL_MAX else name[:LABEL_MAX - 1].rstrip() + "…"


def ask_name(parent, title, initial=""):
    """Show a small dialog asking for a sound's name. Returns None if cancelled."""
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=theme.BG)
    top.resizable(False, False)
    top.transient(parent)
    result = []

    frame = ttk.Frame(top, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Sound name").grid(row=0, column=0, columnspan=2, sticky="w")
    var = tk.StringVar(value=initial[:NAME_MAX])
    var.trace_add("write", lambda *a: len(var.get()) > NAME_MAX and var.set(var.get()[:NAME_MAX]))
    entry = ttk.Entry(frame, textvariable=var, width=34)
    entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 14))

    def save(event=None):
        name = var.get().strip()
        if not name:
            top.bell()
            return
        result.append(name)
        top.destroy()

    ttk.Button(frame, text="Cancel", command=top.destroy).grid(row=2, column=0, sticky="e")
    ttk.Button(frame, text="Save", style="Accent.TButton", command=save).grid(
        row=2, column=1, sticky="e", padx=(8, 0))
    frame.columnconfigure(0, weight=1)
    top.bind("<Return>", save)
    top.bind("<Escape>", lambda e: top.destroy())

    # Centre over the main window.
    top.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - top.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - top.winfo_height()) // 3
    top.geometry(f"+{x}+{y}")
    theme.dark_title_bar(top)
    entry.focus_set()
    entry.select_range(0, "end")
    top.grab_set()
    top.wait_window()
    return result[0] if result else None


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
        self.board = self.load_board()  # list of {"name": str, "path": Path}
        self.pads = []                  # (button, board entry) pairs currently shown

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

        self.render_board()
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
        outer, box = theme.card(parent, "~/soundboard")
        outer.grid(row=1, column=0, sticky="nsew", pady=12)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)

        bar = ttk.Frame(box, style="Card.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Button(bar, text="+  Add sound", style="Accent.TButton",
                   command=self.add_sounds).pack(side="left")
        ttk.Button(bar, text="■  Stop all", command=self.mixer.stop_all).pack(side="right")
        ttk.Label(bar, text="Right-click a sound to rename or remove it",
                  style="FaintCard.TLabel").pack(side="left", padx=(12, 0))

        # The buttons sit in a frame inside a canvas, so the board can scroll.
        self.board_canvas = tk.Canvas(box, bg=theme.SURFACE, highlightthickness=0, height=180)
        self.board_canvas.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.board_canvas.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(6, 0))
        self.board_canvas.configure(yscrollcommand=scroll.set)
        self.board_frame = ttk.Frame(self.board_canvas, style="Card.TFrame")
        window = self.board_canvas.create_window(0, 0, anchor="nw", window=self.board_frame)
        self.board_frame.bind("<Configure>", lambda e: self.board_canvas.configure(
            scrollregion=self.board_canvas.bbox("all")))

        def on_resize(event):
            self.board_canvas.itemconfigure(window, width=event.width)
            self.layout_board()
        self.board_canvas.bind("<Configure>", on_resize)

        # The mouse wheel scrolls the board while the pointer is over it.
        def wheel(event):
            if self.board_frame.winfo_height() > self.board_canvas.winfo_height():
                self.board_canvas.yview_scroll(-event.delta // 120, "units")
        self.board_canvas.bind("<Enter>", lambda e: self.root.bind_all("<MouseWheel>", wheel))
        self.board_canvas.bind("<Leave>", lambda e: self.root.unbind_all("<MouseWheel>"))

        self.menu = tk.Menu(self.root, tearoff=False)
        self.empty_label = ttk.Label(
            self.board_frame, style="FaintCard.TLabel",
            text="No sounds yet. Click  + Add sound  to import one.")

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

    # ---- soundboard --------------------------------------------------------

    def render_board(self):
        """Rebuild the soundboard buttons from self.board."""
        for button, _ in self.pads:
            button.destroy()
        self.pads = []
        for entry in self.board:
            missing = not entry["path"].exists()
            button = ttk.Button(
                self.board_frame, text=short_label(entry["name"]),
                style="Missing.Pad.TButton" if missing else "Pad.TButton",
                command=lambda e=entry: self.play(e))
            button.bind("<Button-3>", lambda ev, e=entry: self.show_menu(ev, e))
            self.pads.append((button, entry))
        self.layout_board()
        self.mixer.preload([e["path"] for e in self.board if e["path"].exists()])

    def layout_board(self):
        """Place the buttons in as many equal columns as fit the width."""
        width = self.board_canvas.winfo_width()
        cols = max(1, width // PAD_WIDTH)
        # Columns in use share equal widths. Unused ones must leave the
        # "uniform" group, or Tk squashes every column to nothing.
        for c in range(max(cols, 12)):
            used = c < cols
            self.board_frame.columnconfigure(c, weight=int(used), uniform="pad" if used else "")
        if not self.pads:
            self.empty_label.grid(row=0, column=0, columnspan=cols, pady=30)
            return
        self.empty_label.grid_forget()
        for n, (button, _) in enumerate(self.pads):
            button.grid(row=n // cols, column=n % cols, sticky="ew", padx=3, pady=3)

    def play(self, entry):
        if not entry["path"].exists():
            self.status.set(f"Can't find the file for \"{entry['name']}\": {entry['path']}")
            return
        # Tagged by board entry, so only this button lights up even if another
        # button uses the same file. Load failures arrive via mixer.errors.
        self.mixer.play(entry["path"], tag=id(entry))

    def add_sounds(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTS))
        paths = filedialog.askopenfilenames(
            title="Add sounds", filetypes=[("Audio", patterns), ("All files", "*.*")])
        if not paths:
            return
        if len(paths) == 1:
            # One file: ask for a name, like Discord does.
            name = ask_name(self.root, "Add sound", Path(paths[0]).stem)
            if name is None:
                return
            self.board.append({"name": name, "path": Path(paths[0])})
        else:
            # Several files: name them after their files; they can be renamed later.
            self.board += [{"name": Path(p).stem[:NAME_MAX], "path": Path(p)} for p in paths]
        self.board_changed()

    def show_menu(self, event, entry):
        self.menu.delete(0, "end")
        self.menu.add_command(label="Play", command=lambda: self.play(entry))
        self.menu.add_command(label="Rename…", command=lambda: self.rename(entry))
        self.menu.add_separator()
        self.menu.add_command(label="Remove", command=lambda: self.remove(entry))
        self.menu.tk_popup(event.x_root, event.y_root)

    def rename(self, entry):
        name = ask_name(self.root, "Rename sound", entry["name"])
        if name is not None:
            entry["name"] = name
            self.board_changed()

    def remove(self, entry):
        """Take a sound off the board. The file itself is left alone."""
        self.board.remove(entry)
        self.board_changed()

    def board_changed(self):
        self.render_board()
        self.save_settings()

    def update_meter(self):
        """Meters use a decibel scale from -60 dB (near silent) to 0 dB (maximum)."""
        for meter, level in self.meters:
            peak = level()
            frac = 0.0 if peak <= 1e-3 else min(1.0, 1 + 20 * math.log10(peak) / 60)
            width = meter.winfo_width()
            meter.coords("cover", int(width * frac), 0, width, meter.winfo_height())
        # Light up the buttons whose sounds are playing.
        playing = self.mixer.playing()
        for button, entry in self.pads:
            if not entry["path"].exists():
                continue
            style = "Playing.Pad.TButton" if id(entry) in playing else "Pad.TButton"
            if button.cget("style") != style:
                button.configure(style=style)
        # Load errors happen on background threads, but Tk can only be
        # updated from this one, so they're queued and shown here.
        while not self.mixer.errors.empty():
            path, e = self.mixer.errors.get()
            self.status.set(f"Could not load {Path(path).name}: {e}")
        self.meter_job = self.root.after(50, self.update_meter)

    # ---- settings ---------------------------------------------------------

    def load_settings(self):
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (OSError, ValueError):
            return {}

    def load_board(self):
        saved = self.settings.get("board")
        if saved is not None:
            return [{"name": e["name"], "path": path_from_setting(e["path"])}
                    for e in saved if e.get("name") and e.get("path")]
        # First run, or settings from before the soundboard: start with the
        # files from the old sounds folder and added-files list.
        folder = path_from_setting(self.settings.get("sounds_dir", "sounds"))
        files = list_sounds(folder) if folder.is_dir() else []
        files += [Path(p) for p in self.settings.get("extra_files", []) if Path(p) not in files]
        return [{"name": p.stem[:NAME_MAX], "path": p} for p in files]

    def save_settings(self):
        try:
            SETTINGS_PATH.write_text(json.dumps({
                "mic": self.mic_var.get(),
                "out": self.out_var.get(),
                "monitor": self.mon_var.get(),
                "mic_gain_db": round(self.mic_gain_db.get(), 1),
                "sfx_gain": round(self.sfx_gain.get(), 3),
                "noise_suppression": self.denoise_on.get(),
                "board": [{"name": e["name"], "path": path_to_setting(e["path"])}
                          for e in self.board],
            }, indent=2))
        except OSError:
            pass

    def on_close(self):
        self.root.after_cancel(self.meter_job)
        self.stop()
        self.save_settings()
        self.mixer.close()
        self.root.destroy()


def main():
    theme.enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
