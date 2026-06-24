import tkinter as tk
import tkinter.font as tkfont
import webbrowser

from .config import BADGE_BG, BADGE_FG, CARD_BG, CARD_HOVER, TEXT_BRIGHT, TEXT_DIM

SCROLL_PX = 1
SCROLL_INTERVAL_MS = 30
PAUSE_START_MS = 800
PAUSE_END_MS = 1500

_title_font = None
_desc_font = None


def _get_fonts():
    global _title_font, _desc_font
    if _title_font is None:
        _title_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        _desc_font = tkfont.Font(family="Segoe UI", size=8)
    return _title_font, _desc_font
PAUSE_RESET_MS = 1000


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
        if mod.get("old_version"):
            full_title += f" • was {mod['old_version']}"

        tf, df = _get_fonts()
        c_title = tk.Canvas(self, bg=CARD_BG, highlightthickness=0,
                            height=tf.metrics("linespace") + 2,
                            cursor="hand2")
        c_title.grid(row=1, column=1, sticky="ew")
        tid = c_title.create_text(0, 1, text=full_title, anchor="nw",
                                  font=tf, fill=TEXT_BRIGHT)
        self._canvases.append((c_title, tid, tf.measure(full_title)))
        self._widgets.append(c_title)

        # SPT version badge
        spt_ver = mod.get("spt_version", "")
        if spt_ver:
            badge = tk.Label(self, text=f" SPT {spt_ver} ", font=("Segoe UI", 7),
                             fg=BADGE_FG, bg=BADGE_BG, cursor="hand2")
            badge.grid(row=2, column=1, sticky="w", pady=(1, 0))
            self._widgets.append(badge)

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

        for w in [self, img_lbl, c_title, c_desc] + ([badge] if spt_ver else []):
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
