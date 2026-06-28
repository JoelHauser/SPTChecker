import tkinter as tk
import tkinter.font as tkfont
import webbrowser

from .config import (
    ACCENT_UPD, BG, CARD_BG, CARD_HOVER,
    TEXT_BRIGHT, TEXT_DIM,
)

SCROLL_PX = 1
SCROLL_INTERVAL_MS = 30
PAUSE_START_MS = 800
PAUSE_END_MS = 1500
PAUSE_RESET_MS = 1000

_title_font = None
_desc_font = None


def _get_fonts():
    global _title_font, _desc_font
    if _title_font is None:
        _title_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        _desc_font = tkfont.Font(family="Segoe UI", size=8)
    return _title_font, _desc_font


class ModCard(tk.Frame):
    def __init__(self, parent, mod, accent, photo):
        super().__init__(parent, bg=CARD_BG, padx=6, pady=4,
                         highlightbackground=accent, highlightthickness=1)
        self._mod = mod
        self._photo = photo
        self._widgets = []
        self._canvases = []
        self._scroll_id = None
        self._scroll_offset = 0
        self._leave_id = None

        self.rowconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        img_lbl = tk.Label(self, image=photo, bg=CARD_BG, cursor="hand2")
        img_lbl.grid(row=0, column=0, rowspan=5, padx=(0, 6))
        self._widgets.append(img_lbl)

        # Title + meta line
        full_title = mod["title"]
        if mod.get("version"):
            full_title += f"  {mod['version']}"
        full_title += f"  —  {mod['author']}"
        if mod.get("category"):
            full_title += f" • {mod['category']}"

        tf, df = _get_fonts()
        c_title = tk.Canvas(self, bg=CARD_BG, highlightthickness=0,
                            height=tf.metrics("linespace") + 2,
                            cursor="hand2")
        c_title.grid(row=1, column=1, sticky="ew")
        tid = c_title.create_text(0, 1, text=full_title, anchor="nw",
                                  font=tf, fill=TEXT_BRIGHT)
        self._canvases.append((c_title, tid, tf.measure(full_title)))
        self._widgets.append(c_title)

        # Description line
        full_desc = mod.get("description", "")
        c_desc = tk.Canvas(self, bg=CARD_BG, highlightthickness=0,
                           height=df.metrics("linespace") + 2,
                           cursor="hand2")
        c_desc.grid(row=3, column=1, sticky="ew", pady=(1, 0))
        did = c_desc.create_text(0, 1, text=full_desc, anchor="nw",
                                 font=df, fill=TEXT_DIM)
        self._canvases.append((c_desc, did, df.measure(full_desc)))
        self._widgets.append(c_desc)

        self.columnconfigure(1, weight=1)

        for w in [self, img_lbl, c_title, c_desc]:
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _click(self, _e):
        webbrowser.open(self._mod["link"])

    def _set_bg(self, color):
        self.configure(bg=color)
        for child in self.winfo_children():
            try:
                child.configure(bg=color)
            except tk.TclError:
                pass

    # ── Hover with debounce ────────────────────────────────────────────

    def _enter(self, _e):
        if self._leave_id:
            self.after_cancel(self._leave_id)
            self._leave_id = None
        self._set_bg(CARD_HOVER)
        if not self._scroll_id:
            self._start_scroll()

    def _leave(self, _e):
        if self._leave_id:
            self.after_cancel(self._leave_id)
        self._leave_id = self.after(50, self._do_leave)

    def _do_leave(self):
        self._leave_id = None
        self._set_bg(CARD_BG)
        self._stop_scroll()

    # ── Scroll logic ───────────────────────────────────────────────────

    def _max_overflow(self):
        best = 0
        for canvas, _tid, text_w in self._canvases:
            cw = canvas.winfo_width()
            if cw > 1 and text_w > cw:
                best = max(best, text_w - cw + 10)
        return best

    def _start_scroll(self):
        self._scroll_offset = 0
        self._scroll_id = self.after(PAUSE_START_MS, self._scroll_tick)

    def _stop_scroll(self):
        if self._scroll_id:
            self.after_cancel(self._scroll_id)
            self._scroll_id = None
        self._scroll_offset = 0
        for canvas, tid, _ in self._canvases:
            canvas.coords(tid, 0, 1)

    def _scroll_tick(self):
        overflow = self._max_overflow()
        if overflow <= 0:
            self._scroll_id = None
            return

        self._scroll_offset += SCROLL_PX
        for canvas, tid, text_w in self._canvases:
            cw = canvas.winfo_width()
            if cw <= 1 or text_w <= cw:
                continue
            limit = text_w - cw + 10
            canvas.coords(tid, -min(self._scroll_offset, limit), 1)

        if self._scroll_offset >= overflow:
            self._scroll_id = self.after(PAUSE_END_MS, self._reset_scroll)
        else:
            self._scroll_id = self.after(SCROLL_INTERVAL_MS, self._scroll_tick)

    def _reset_scroll(self):
        self._scroll_offset = 0
        for canvas, tid, _ in self._canvases:
            canvas.coords(tid, 0, 1)
        self._scroll_id = self.after(PAUSE_RESET_MS, self._scroll_tick)


class IntervalSlider(tk.Canvas):
    TRACK_COLOR = CARD_BG
    FILL_COLOR = ACCENT_UPD
    KNOB_COLOR = "#eeeef4"
    KNOB_HOVER = ACCENT_UPD
    TRACK_H = 4
    KNOB_R = 7

    TICK_COLOR = "#444466"
    TICK_H = 6

    def __init__(self, parent, from_=5, to=60, step=5, variable=None, command=None, **kw):
        kw.setdefault("bg", BG)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("height", self.KNOB_R * 2 + 4)
        kw.setdefault("width", 120)
        super().__init__(parent, **kw)

        self._min = from_
        self._max = to
        self._step = step
        self._var = variable
        self._command = command
        self._dragging = False
        self._hovering = False

        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda _: self._set_hover(True))
        self.bind("<Leave>", lambda _: self._set_hover(False))

        if self._var:
            self._var.trace_add("write", lambda *_: self._draw())

    def _val_to_x(self, val):
        pad = self.KNOB_R + 2
        w = self.winfo_width() - pad * 2
        ratio = (val - self._min) / max(1, self._max - self._min)
        return pad + ratio * w

    def _x_to_val(self, x):
        pad = self.KNOB_R + 2
        w = self.winfo_width() - pad * 2
        ratio = max(0.0, min(1.0, (x - pad) / max(1, w)))
        raw = self._min + ratio * (self._max - self._min)
        return round(raw / self._step) * self._step

    def _draw(self, _event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 2:
            return

        cy = h // 2
        pad = self.KNOB_R + 2
        val = self._var.get() if self._var else self._min
        knob_x = self._val_to_x(val)

        r = self.TRACK_H // 2
        self.create_round_rect(pad, cy - r, w - pad, cy + r, r, fill=self.TRACK_COLOR, outline="")
        self.create_round_rect(pad, cy - r, knob_x, cy + r, r, fill=self.FILL_COLOR, outline="")

        color = self.KNOB_HOVER if (self._hovering or self._dragging) else self.KNOB_COLOR
        kr = self.KNOB_R
        self.create_oval(knob_x - kr, cy - kr, knob_x + kr, cy + kr,
                         fill=color, outline="")

    def create_round_rect(self, x1, y1, x2, y2, r, **kw):
        if x2 - x1 < r * 2:
            r = max(0, (x2 - x1) // 2)
        pts = [
            x1 + r, y1, x2 - r, y1,
            x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(pts, smooth=True, **kw)

    def _set_hover(self, state):
        self._hovering = state
        self._draw()

    def _set_val(self, x):
        val = self._x_to_val(x)
        if self._var:
            self._var.set(val)
        if self._command:
            self._command(val)
        self._draw()

    def _on_press(self, e):
        self._dragging = True
        self._set_val(e.x)

    def _on_drag(self, e):
        if self._dragging:
            self._set_val(e.x)

    def _on_release(self, _e):
        self._dragging = False
        self._draw()
