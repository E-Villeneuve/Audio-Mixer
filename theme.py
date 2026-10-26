"""Dark theme for the Tkinter GUI, using the colour tokens from style.css."""

import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

# ---- tokens (style.css :root) ---------------------------------------------
BG = "#0d0b14"
SURFACE = "#15121e"
RAISED = "#1c1828"
LINE = "#2a2439"
LINE_STRONG = "#3b3350"
TEXT = "#eef1ff"
MUTED = "#a9a5c0"
FAINT = "#8e89a8"
ACCENT = "#ff5ce1"
ACCENT_STRONG = "#ff8deb"
ACCENT_INK = "#1a0530"
CYAN = "#22d3ff"
BLUE = "#3503ff"


def blend(fg, bg, alpha):
    """Tk has no see-through colours, so mix a CSS rgba() colour onto its background."""
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[i] + (f[i] - b[i]) * alpha):02x}" for i in range(3))


ACCENT_SOFT = blend(ACCENT, SURFACE, 0.14)  # --accent-soft on a card
ACCENT_SOFT_BG = blend(ACCENT, BG, 0.14)    # --accent-soft on the page

# Filled in by apply() once Tk is running and font families can be queried.
FONT = MONO = MONO_SMALL = SMALL = TITLE = None


def enable_dpi_awareness():
    """Call before creating Tk, otherwise Windows bitmap-scales it (blurry)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def _family(root, *candidates):
    available = set(tkfont.families(root))
    return next((c for c in candidates if c in available), "TkDefaultFont")


def _dark_title_bar(root):
    """Windows 11: dark title bar tinted to --bg. Silently skipped elsewhere."""
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dark = ctypes.c_int(1)
        set_attr(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))  # immersive dark mode
        colorref = ctypes.c_int(int(BG[5:7] + BG[3:5] + BG[1:3], 16))  # 0x00BBGGRR
        set_attr(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))  # caption colour
    except (AttributeError, OSError):
        pass


def apply(root):
    global FONT, MONO, MONO_SMALL, SMALL, TITLE
    sans = _family(root, "Geist", "Segoe UI Variable Text", "Segoe UI")
    mono = _family(root, "Geist Mono", "Cascadia Code", "Consolas")
    FONT = (sans, 10)
    SMALL = (sans, 9)
    TITLE = (mono, 11)
    MONO = (mono, 10)
    MONO_SMALL = (mono, 9)

    root.configure(bg=BG)
    root.option_add("*Font", FONT)
    # Combobox drop-down lists are plain Tk listboxes.
    root.option_add("*TCombobox*Listbox.background", RAISED)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ACCENT_INK)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    s = ttk.Style(root)
    s.theme_use("clam")  # the only built-in theme that honours custom colours
    s.configure(".", background=BG, foreground=TEXT, font=FONT,
                bordercolor=LINE_STRONG, lightcolor=BG, darkcolor=BG,
                troughcolor=LINE, fieldbackground=RAISED, focuscolor=ACCENT,
                selectbackground=ACCENT, selectforeground=ACCENT_INK,
                insertcolor=TEXT, arrowcolor=MUTED)

    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=SURFACE)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Card.TLabel", background=SURFACE, foreground=MUTED)
    s.configure("Path.TLabel", background=SURFACE, foreground=CYAN, font=MONO_SMALL)
    s.configure("Value.TLabel", background=SURFACE, foreground=FAINT, font=MONO_SMALL)
    s.configure("Faint.TLabel", background=BG, foreground=FAINT, font=SMALL)
    s.configure("FaintCard.TLabel", background=SURFACE, foreground=FAINT, font=SMALL)
    s.configure("Title.TLabel", background=BG, foreground=TEXT, font=TITLE)
    s.configure("Prompt.TLabel", background=BG, foreground=ACCENT, font=TITLE)
    s.configure("Pill.TLabel", background=BG, foreground=MUTED, font=SMALL)

    # .button-ghost
    s.configure("TButton", background=SURFACE, foreground=TEXT, bordercolor=LINE_STRONG,
                lightcolor=SURFACE, darkcolor=SURFACE, padding=(14, 6), focusthickness=1)
    s.map("TButton",
          background=[("pressed", LINE), ("active", RAISED)],
          lightcolor=[("pressed", LINE), ("active", RAISED)],
          darkcolor=[("pressed", LINE), ("active", RAISED)],
          bordercolor=[("active", FAINT)])
    # .button (accent)
    s.configure("Accent.TButton", background=ACCENT, foreground=ACCENT_INK,
                bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                focuscolor=ACCENT_INK, font=(sans, 10, "bold"))
    s.map("Accent.TButton",
          background=[("pressed", ACCENT), ("active", ACCENT_STRONG)],
          lightcolor=[("active", ACCENT_STRONG)],
          darkcolor=[("active", ACCENT_STRONG)],
          bordercolor=[("active", ACCENT_STRONG)])

    # Toggle chip (button.chip / [aria-pressed="true"])
    s.configure("Chip.Toolbutton", background=SURFACE, foreground=TEXT,
                bordercolor=LINE_STRONG, lightcolor=SURFACE, darkcolor=SURFACE,
                relief="solid", borderwidth=1, padding=(12, 4), anchor="center")
    s.map("Chip.Toolbutton",
          background=[("selected", ACCENT_SOFT), ("active", RAISED)],
          lightcolor=[("selected", ACCENT_SOFT), ("active", RAISED)],
          darkcolor=[("selected", ACCENT_SOFT), ("active", RAISED)],
          foreground=[("disabled", FAINT), ("selected", ACCENT_STRONG)],
          bordercolor=[("disabled", LINE), ("selected", ACCENT), ("active", ACCENT)],
          relief=[("selected", "solid"), ("!selected", "solid")])

    s.configure("TCombobox", background=RAISED, fieldbackground=RAISED, foreground=TEXT,
                bordercolor=LINE_STRONG, lightcolor=RAISED, darkcolor=RAISED,
                arrowcolor=MUTED, padding=(8, 4), arrowsize=14)
    s.map("TCombobox",
          fieldbackground=[("readonly", RAISED)],
          background=[("active", LINE), ("readonly", RAISED)],
          foreground=[("readonly", TEXT)],
          selectbackground=[("readonly", RAISED)],
          selectforeground=[("readonly", TEXT)],
          bordercolor=[("focus", ACCENT), ("active", FAINT)],
          arrowcolor=[("active", TEXT)])

    s.configure("Vertical.TScrollbar", background=LINE_STRONG, troughcolor=BG,
                bordercolor=BG, lightcolor=LINE_STRONG, darkcolor=LINE_STRONG,
                arrowcolor=FAINT, gripcount=0, arrowsize=12)
    s.map("Vertical.TScrollbar",
          background=[("active", FAINT)],
          lightcolor=[("active", FAINT)],
          darkcolor=[("active", FAINT)])

    s.configure("TSeparator", background=LINE)
    _dark_title_bar(root)


def gradient(canvas, width, height, start=CYAN, end=BLUE, tag="gradient"):
    """Paint --gradient-x (left to right) onto a canvas."""
    canvas.delete(tag)
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    steps = max(width, 1)
    for x in range(steps):
        t = x / max(steps - 1, 1)
        colour = "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))
        canvas.create_line(x, 0, x, height, fill=colour, tags=tag)


class Slider(tk.Canvas):
    """Thin track, pink fill, round thumb. ttk.Scale can't be styled this way."""

    R = 7  # thumb radius

    def __init__(self, parent, variable, from_=0.0, to=1.0, command=None, bg=SURFACE):
        super().__init__(parent, height=2 * self.R + 8, bg=bg, highlightthickness=0,
                         cursor="hand2", takefocus=1)
        self.var, self.lo, self.hi, self.command = variable, from_, to, command
        self.hover = False
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<FocusIn>", lambda e: self.redraw())
        self.bind("<FocusOut>", lambda e: self.redraw())
        step = (to - from_) / 40
        self.bind("<Left>", lambda e: self._set(self.var.get() - step))
        self.bind("<Right>", lambda e: self._set(self.var.get() + step))
        self.var.trace_add("write", lambda *a: self.redraw())

    def _set(self, value):
        self.var.set(min(max(value, self.lo), self.hi))
        if self.command:
            self.command()

    def _drag(self, event):
        self.focus_set()
        span = max(self.winfo_width() - 2 * self.R, 1)
        frac = min(max((event.x - self.R) / span, 0.0), 1.0)
        self._set(self.lo + frac * (self.hi - self.lo))

    def _set_hover(self, on):
        self.hover = on
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h, r = self.winfo_width(), self.winfo_height(), self.R
        y = h / 2
        frac = (self.var.get() - self.lo) / (self.hi - self.lo)
        x = r + frac * (w - 2 * r)
        self.create_line(r, y, w - r, y, width=4, fill=LINE, capstyle="round")
        self.create_line(r, y, x, y, width=4, fill=ACCENT, capstyle="round")
        if self.hover or self.focus_get() is self:
            self.create_oval(x - r - 4, y - r - 4, x + r + 4, y + r + 4,
                             fill=ACCENT_SOFT, outline="")
        fill = ACCENT_STRONG if self.hover else ACCENT
        self.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline="")


def card(parent, path):
    """A bordered surface panel with a cyan `~/path` heading, like .skill-group."""
    outer = tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                     highlightbackground=LINE, highlightcolor=LINE)
    ttk.Label(outer, text=path, style="Path.TLabel").pack(anchor="w", padx=14, pady=(10, 6))
    body = ttk.Frame(outer, style="Card.TFrame", padding=(14, 0, 14, 14))
    body.pack(fill="both", expand=True)
    return outer, body
