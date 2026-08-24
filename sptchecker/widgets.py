import re
import threading
import tkinter as tk
import webbrowser
import weakref
from datetime import datetime, timezone
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageTk

from .config import (
    ACCENT_DANGER, ACCENT_NEW, ACCENT_NEW_AUTHOR, ACCENT_UPD, BG, BORDER,
    CARD_BG, CARD_HOVER, CATEGORY_COLOR_DEFAULT, CATEGORY_COLORS,
    ENDORSE_ENABLED, FORGE_USER_URL, NEW_AUTHOR_DAYS, SEPARATOR, STATUS_BG,
    TEXT, TEXT_BRIGHT, TEXT_DIM, TEXT_FAINT, TREND_WINDOW_DAYS,
)
from .feed import fetch_author_id
from .localmods import validate_spt_root
from .state import placeholder_thumb
from .theme import (
    CAP_W, CARD_BORDER_W, RoundedPanel, ToggleSwitch, blend, card_caps, chip,
    dot, ellipsize, endorse_glyph, flat_button, font, lighten, notes_glyph, pill,
    rounded_photo, rounded_rect,
)
from .utils import parse_dt

# Re-exported so app.py and the popups below keep importing their buttons from
# one place even though the implementation now lives in theme.
__all__ = [
    "ChangeNotesWindow", "ContextMenu", "FramelessPopup", "LocalScanSettingsWindow",
    "MiniScrollbar", "ModCard", "StatsWindow", "build_scroll_area", "card_pitch",
    "flat_button", "render_markdown", "section_heading",
]


class ContextMenu(tk.Toplevel):
    """Styled frameless context menu matching the dark theme."""

    def __init__(self, parent, items):
        super().__init__(parent)
        self.wm_overrideredirect(True)
        self.configure(bg=BORDER)

        inner = tk.Frame(self, bg=CARD_BG, padx=4, pady=4)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        fnt = font(9)
        for label, command in items:
            if label == "-":
                tk.Frame(inner, bg=SEPARATOR, height=1).pack(fill="x", padx=8, pady=4)
                continue
            row = tk.Label(inner, text=label, font=fnt, fg=TEXT,
                           bg=CARD_BG, anchor="w", padx=14, pady=6, cursor="hand2")
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.configure(bg=CARD_HOVER, fg=TEXT_BRIGHT))
            row.bind("<Leave>", lambda e, r=row: r.configure(bg=CARD_BG, fg=TEXT))
            row.bind("<Button-1>", lambda e, cmd=command: self._run(cmd))

        self.bind("<FocusOut>", lambda _: self.destroy())
        self.bind("<Escape>", lambda _: self.destroy())

    def _run(self, command):
        self.destroy()
        command()

    def show(self, x, y):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        x = min(x, sw - w - 4)
        y = min(y, sh - h - 4)
        self.wm_geometry(f"+{x}+{y}")
        self.deiconify()
        self.focus_force()


SCROLL_PX = 1
SCROLL_INTERVAL_MS = 30
PAUSE_START_MS = 800
PAUSE_END_MS = 1500
PAUSE_RESET_MS = 1000

_bold_font = None
_italic_font = None
_header_font = None
_code_font = None
_emoji_font = None

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF"  # pictographs, emoticons, transport, supplemental
    "\U00002300-\U000027BF"   # misc technical, symbols, dingbats
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U000020E3"              # combining enclosing keycap
    "]+",
    flags=re.UNICODE,
)


def _get_markdown_fonts():
    global _bold_font, _italic_font, _header_font, _code_font, _emoji_font
    if _bold_font is None:
        _bold_font = font(9, "bold")
        _italic_font = font(9, slant="italic")
        _header_font = font(10, "bold")
        _code_font = font(8, family="Consolas")
        _emoji_font = font(9, family="Segoe UI Emoji")
    return _bold_font, _italic_font, _header_font, _code_font


def _apply_emoji_tags(text_widget):
    text_widget.tag_configure("md_emoji", font=_emoji_font)
    content = text_widget.get("1.0", "end-1c")
    for m in _EMOJI_RE.finditer(content):
        text_widget.tag_add("md_emoji", f"1.0+{m.start()}c", f"1.0+{m.end()}c")


_INLINE_RE = re.compile(r"\*\*.+?\*\*|\*[^*\n]+\*|`[^`\n]+`|\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def _open_link(url):
    webbrowser.open(url)
    return "break"


def _insert_inline(text_widget, line, base_tags):
    pos = 0
    for m in _INLINE_RE.finditer(line):
        if m.start() > pos:
            text_widget.insert("end", line[pos:m.start()], base_tags)
        token = m.group(0)
        if token.startswith("**"):
            text_widget.insert("end", token[2:-2], base_tags + ("md_bold",))
        elif token.startswith("`"):
            text_widget.insert("end", token[1:-1], base_tags + ("md_code",))
        elif token.startswith("["):
            label, url = _LINK_RE.match(token).groups()
            tag = f"md_link{text_widget._md_link_count}"
            text_widget._md_link_count += 1
            text_widget.tag_configure(tag, foreground=ACCENT_UPD, underline=1)
            text_widget.tag_bind(tag, "<Button-1>", lambda _e, u=url: _open_link(u))
            text_widget.tag_bind(tag, "<Enter>", lambda _e: text_widget.configure(cursor="hand2"))
            text_widget.tag_bind(tag, "<Leave>", lambda _e: text_widget.configure(cursor="arrow"))
            text_widget.insert("end", label or url, base_tags + (tag,))
        else:
            text_widget.insert("end", token[1:-1], base_tags + ("md_italic",))
        pos = m.end()
    text_widget.insert("end", line[pos:], base_tags)


def render_markdown(text_widget, raw):
    """Render a markdown string into a tk.Text widget using tags (bold/italic/headers/
    bullets/blockquotes/links). Replaces any prior content."""
    bold_f, italic_f, header_f, code_f = _get_markdown_fonts()

    was_disabled = text_widget.cget("state") == "disabled"
    text_widget.configure(state="normal")
    text_widget.delete("1.0", "end")
    text_widget._md_link_count = 0

    text_widget.tag_configure("md_bold", font=bold_f, foreground=TEXT_BRIGHT)
    text_widget.tag_configure("md_italic", font=italic_f)
    text_widget.tag_configure("md_code", font=code_f, background=SEPARATOR,
                              foreground=TEXT_BRIGHT)
    text_widget.tag_configure("md_header", font=header_f, foreground=TEXT_BRIGHT,
                              spacing1=6, spacing3=3)
    text_widget.tag_configure("md_quote", foreground=TEXT_DIM, lmargin1=12, lmargin2=12)
    text_widget.tag_configure("md_bullet", lmargin1=14, lmargin2=28, spacing1=2)

    blocks = []
    blank_before = False
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            blank_before = True
            continue
        blocks.append((stripped, blank_before))
        blank_before = False

    for i, (stripped, had_blank) in enumerate(blocks):
        if i > 0:
            text_widget.insert("end", "\n\n" if had_blank else "\n")

        hr_m = re.match(r"^([-*_])\1{2,}$", stripped)
        if hr_m:
            text_widget.insert("end", "─" * 40, ("md_quote",))
            continue

        header_m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if header_m:
            _insert_inline(text_widget, header_m.group(2), ("md_header",))
            continue

        quote_m = re.match(r"^>\s?(.*)", stripped)
        if quote_m:
            _insert_inline(text_widget, quote_m.group(1), ("md_quote",))
            continue

        bullet_m = re.match(r"^[-*+]\s+(.*)", stripped)
        if bullet_m:
            text_widget.insert("end", "•  ", ("md_bullet",))
            _insert_inline(text_widget, bullet_m.group(1), ("md_bullet",))
            continue

        _insert_inline(text_widget, stripped, ())

    _apply_emoji_tags(text_widget)
    if was_disabled:
        text_widget.configure(state="disabled")


# ── Shared layout helpers ──────────────────────────────────────────────

def section_heading(parent, text, color=TEXT_DIM, bg=BG, count=None, pady=(0, 6)):
    """A small uppercase label with a hairline rule running to the right edge.

    The rule is what makes a heading read as the top of a section rather than
    just another line of text -- without it, a bare uppercase label floating
    over a list has no visible scope.
    """
    row = tk.Frame(parent, bg=bg)
    row.pack(fill="x", pady=pady)
    tk.Label(row, text=text.upper(), font=font(8, "bold"), fg=color, bg=bg,
             anchor="w").pack(side="left")
    if count is not None:
        tk.Label(row, text=str(count), font=font(8, "bold"), fg=TEXT_FAINT,
                 bg=bg).pack(side="left", padx=(6, 0))
    rule = tk.Frame(row, bg=SEPARATOR, height=1)
    rule.pack(side="left", fill="x", expand=True, padx=(10, 0), pady=(1, 0))
    return row


# Every scrollable canvas in the app, so one application-wide wheel handler can
# find the right one to scroll. Binding <Enter>/<Leave> on the canvas itself --
# the obvious approach -- breaks as soon as the canvas holds embedded windows:
# Tk reports a Leave on the canvas the moment the pointer moves onto a child, so
# the wheel would unbind over the very cards that fill the column.
_scroll_canvases = weakref.WeakSet()
_wheel_bound = set()


def _on_global_wheel(e):
    widget = e.widget
    while widget is not None:
        if widget in _scroll_canvases:
            widget.yview_scroll(int(-e.delta / 40), "units")
            return "break"
        widget = getattr(widget, "master", None)
    return None


def build_scroll_area(parent, bg=BG):
    """Canvas + MiniScrollbar + inner-frame plumbing shared by every scrollable
    region. The scrollbar only appears once content actually overflows (see
    _update_canvas_scrollbar)."""
    # width=1: a tk.Canvas otherwise requests a default 378px, which is wider
    # than the column it sits in -- the packer then hands it the entire cavity
    # and the scrollbar, packed afterwards, is left with nothing and never
    # appears however much the content overflows.
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0, width=1, height=1)
    scrollbar = MiniScrollbar(parent, bg=bg)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.set_command(canvas.yview)
    canvas.pack(side="left", fill="both", expand=True)

    inner = tk.Frame(canvas, bg=bg)
    inner_id = canvas.create_window(0, 0, window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: _update_canvas_scrollbar(canvas, scrollbar))
    canvas.bind("<Configure>", lambda e: (
        canvas.itemconfigure(inner_id, width=e.width),
        _update_canvas_scrollbar(canvas, scrollbar),
    ))

    _scroll_canvases.add(canvas)
    root = canvas.winfo_toplevel()
    key = str(root.winfo_toplevel())
    if key not in _wheel_bound:
        _wheel_bound.add(key)
        canvas.bind_all("<MouseWheel>", _on_global_wheel, add="+")
    return inner


class FramelessPopup(tk.Toplevel):
    """Base for frameless, dark-themed popups with a hand-drawn draggable title bar.

    Relying on Windows to theme a native title bar turned out to be unreliable for
    owned Toplevels, so drawing it ourselves guarantees the color always matches.
    """

    WIDTH, HEIGHT = 480, 420

    def __init__(self, parent, title_text, anchor=None):
        super().__init__(parent)
        self.overrideredirect(True)
        # A 1px outer frame in place of the window border Windows would have
        # drawn: without it a dark frameless popup has no edge at all against a
        # dark parent window.
        self.configure(bg=BORDER)
        if anchor is not None:
            self._position_near(anchor)
        else:
            self._position_over(parent)
        self.transient(parent)

        self._drag_x = 0
        self._drag_y = 0

        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        self._shell = shell

        title_bar = tk.Frame(shell, bg=STATUS_BG, height=36)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        title_lbl = tk.Label(title_bar, text=title_text, font=font(9, "bold"),
                             fg=TEXT, bg=STATUS_BG, anchor="w")
        title_lbl.pack(side="left", fill="x", expand=True, padx=(14, 4))

        close_btn = tk.Label(title_bar, text="✕", font=font(10),
                             fg=TEXT_DIM, bg=STATUS_BG, cursor="hand2", padx=14)
        close_btn.pack(side="right", fill="y")
        close_btn.bind("<Enter>", lambda _e: close_btn.configure(bg=ACCENT_DANGER, fg="#ffffff"))
        close_btn.bind("<Leave>", lambda _e: close_btn.configure(bg=STATUS_BG, fg=TEXT_DIM))
        # Closes on release, not on press. Destroying the window under the
        # pointer on mouse-down drops the implicit grab, and the release then
        # lands on whatever the popup was covering -- with these popups centred
        # over the window, that is usually one of the header buttons. Waiting
        # for the release keeps both halves of the click inside this window.
        close_btn.bind("<ButtonRelease-1>", self._close_from_release)

        tk.Frame(shell, bg=SEPARATOR, height=1).pack(fill="x")

        for w in (title_bar, title_lbl):
            w.bind("<Button-1>", self._start_move)
            w.bind("<B1-Motion>", self._on_move)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Alt-F4>", lambda _e: self.destroy())

    def _close_from_release(self, e):
        """Close only if the pointer is still on the close button, so pressing
        it and sliding off cancels the way any other button does."""
        w = e.widget
        if 0 <= e.x < w.winfo_width() and 0 <= e.y < w.winfo_height():
            self.destroy()

    def _position_over(self, parent):
        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = px + (pw - self.WIDTH) // 2
        y = py + (ph - self.HEIGHT) // 2
        x = max(0, min(x, sw - self.WIDTH))
        y = max(0, min(y, sh - self.HEIGHT))
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _position_near(self, widget):
        """Sit directly below `widget` (or above it, against the bottom of the
        screen), horizontally centred on it and clamped to the display.

        Used instead of centring when the widget that opened this popup is also
        the control that closes it. Centred over the window, the popup is wider
        than the space beside a column, so it covered the left column's icons
        outright -- there is no centred position that leaves them clickable.
        Anchoring below the trigger keeps it visible whatever the window size.
        """
        widget.update_idletasks()
        wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        margin = 8
        x = wx + widget.winfo_width() // 2 - self.WIDTH // 2
        y = wy + widget.winfo_height() + margin
        if y + self.HEIGHT + margin > sh:
            y = wy - self.HEIGHT - margin  # flip above rather than run off-screen

        # Held inside the app window horizontally, so a popup opened from the
        # left column doesn't hang off the side onto the desktop. Vertically it
        # is allowed past the window edge: a popup this tall cannot clear a
        # card in the middle of a column and still fit inside the window, and
        # clearing the card is the whole point of anchoring it here.
        top = widget.winfo_toplevel()
        left = max(margin, top.winfo_rootx())
        right = min(sw - margin, top.winfo_rootx() + top.winfo_width()) - self.WIDTH
        x = max(left, min(x, right)) if right >= left else max(
            margin, min(x, sw - self.WIDTH - margin))
        y = max(margin, min(y, sh - self.HEIGHT - margin))
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _start_move(self, e):
        # Screen-absolute coordinates, not widget-relative -- the title bar and
        # its label are two different widgets with different origins, so e.x/e.y
        # would jump every time the cursor crosses from one to the other mid-drag.
        self._drag_x = e.x_root
        self._drag_y = e.y_root
        self._drag_win_x = self.winfo_x()
        self._drag_win_y = self.winfo_y()

    def _on_move(self, e):
        x = self._drag_win_x + (e.x_root - self._drag_x)
        y = self._drag_win_y + (e.y_root - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def resurface(self):
        """Bring this popup back above the main window. Needed at first show
        and after anything that can steal z-order from a frameless
        (overrideredirect) window: a native dialog closing, or a background
        thread's results arriving via root.after() once the main window has
        re-taken focus."""
        if self.winfo_exists():
            self.lift()
            self.focus_force()

    def make_scroll_area(self, parent):
        return build_scroll_area(parent)


class ChangeNotesWindow(FramelessPopup):
    """Popup window showing a mod's change notes / description as rendered markdown."""

    WIDTH, HEIGHT = 500, 440

    # At most one notes popup is on screen at a time, tracked on the class
    # rather than per card: opening notes from one card should replace whatever
    # another card left open rather than stacking a second window behind it.
    _open = None

    @classmethod
    def toggle(cls, parent, mod, anchor=None):
        """Open this mod's notes, or close them if they are the ones already
        showing -- so the icon that opened the popup also dismisses it.

        Returns the window, or None when the call closed one.
        """
        current = cls._open
        if current is not None and current.winfo_exists():
            showing_same = current.mod_link == mod.get("link")
            current.destroy()
            if showing_same:
                return None
        return cls(parent, mod, anchor=anchor)

    @classmethod
    def show(cls, parent, mod, anchor=None):
        """Open this mod's notes, raising them if they are already open.

        The always-open counterpart to toggle(), for entry points where
        dismissing would be surprising -- picking "View Change Notes" off a
        context menu should never be the action that closes them.
        """
        current = cls._open
        if current is not None and current.winfo_exists():
            if current.mod_link == mod.get("link"):
                current.resurface()
                return current
            current.destroy()
        return cls(parent, mod, anchor=anchor)

    def __init__(self, parent, mod, anchor=None):
        super().__init__(parent, "Change Notes", anchor=anchor)
        shell = self._shell
        # Identifies which mod is on screen, so toggle() can tell "close this"
        # from "switch to a different mod's notes".
        self.mod_link = mod.get("link")
        ChangeNotesWindow._open = self
        self.bind("<Destroy>", self._forget_open)

        head = tk.Frame(shell, bg=BG)
        head.pack(fill="x", padx=16, pady=(16, 0))
        tk.Label(head, text=mod.get("title", ""), font=font(13, "bold"),
                 fg=TEXT_BRIGHT, bg=BG, anchor="w", wraplength=self.WIDTH - 44,
                 justify="left").pack(fill="x")

        meta = tk.Frame(head, bg=BG)
        meta.pack(fill="x", pady=(6, 0))
        version = mod.get("version", "")
        if mod.get("prev_version") and version:
            chip(meta, f"{mod['prev_version']}  →  {version}", ACCENT_UPD,
                 surface=BG, font_size=8).pack(side="left", padx=(0, 8))
        elif version:
            chip(meta, f"v{version}", ACCENT_NEW, surface=BG,
                 font_size=8).pack(side="left", padx=(0, 8))
        if mod.get("author"):
            tk.Label(meta, text=f"by {mod['author']}", font=font(9), fg=TEXT_DIM,
                     bg=BG, anchor="w").pack(side="left")

        panel = RoundedPanel(shell, fill=CARD_BG, bg=BG, padx=4, pady=4, height=10)
        panel.pack(fill="both", expand=True, padx=16, pady=(12, 12))

        text = tk.Text(panel.body, bg=CARD_BG, fg=TEXT, font=font(9),
                       wrap="word", bd=0, highlightthickness=0, cursor="arrow",
                       padx=12, pady=10, insertwidth=0, state="disabled",
                       spacing1=1, spacing2=2)
        scrollbar = MiniScrollbar(panel.body, bg=CARD_BG)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.set_command(text.yview)
        scrollbar.pack(side="right", fill="y", pady=6)
        text.pack(side="left", fill="both", expand=True)
        text.bind("<MouseWheel>", lambda e: text.yview_scroll(int(-e.delta / 40), "units"))

        changelog = mod.get("changelog", "").strip()
        if changelog:
            header = f"**What changed in {version}:**" if version else "**What changed:**"
            content = f"{header}\n\n{changelog}"
        else:
            content = (mod.get("full_description", "").strip()
                       or "No update notes available for this version.")
        render_markdown(text, content)

        btn_bar = tk.Frame(shell, bg=BG)
        btn_bar.pack(fill="x", padx=16, pady=(0, 16))
        flat_button(btn_bar, "Open on Forge",
                    lambda: webbrowser.open(mod.get("link", "")),
                    accent=ACCENT_NEW).pack(side="right")

        self.resurface()

    def _forget_open(self, e):
        """Drop the class-level reference once this window is gone, however it
        was closed -- the icon, the ✕, or Escape. Guarded on e.widget because
        destroying a Toplevel fires <Destroy> for each of its children too, and
        those events reach this binding."""
        if e.widget is self and ChangeNotesWindow._open is self:
            ChangeNotesWindow._open = None


def _blend(fg_hex, bg_hex, alpha):
    """Kept as a module-level name because the stats chart reaches for it."""
    return blend(fg_hex, bg_hex, alpha)


def _format_day(iso_date):
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %d")


def _catmull_rom(points, segments=8):
    """Interpolate a smooth curve through points -- Pillow draws straight
    segments only, so this replaces the softening Tk's smooth=True used to do."""
    if len(points) < 3:
        return points
    pts = [points[0]] + points + [points[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(segments):
            t = s / segments
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(points[-1])
    return out


def _update_canvas_scrollbar(canvas, scrollbar):
    """Sync a canvas's scrollregion to its content and show/hide the paired
    scrollbar based on whether the content actually overflows -- an empty,
    non-functional strip when everything already fits looks like a broken
    control, not a nice one.

    Coalesced via after_idle: packing N result rows fires N <Configure>
    events, and doing a forced layout flush (update_idletasks) for each one
    visibly stalls the UI while a long list builds."""
    if getattr(canvas, "_scroll_sync_pending", False):
        return
    canvas._scroll_sync_pending = True

    def sync():
        canvas._scroll_sync_pending = False
        if not canvas.winfo_exists():
            return
        canvas.update_idletasks()
        bbox = canvas.bbox("all")
        canvas.configure(scrollregion=bbox)
        content_h = (bbox[3] - bbox[1]) if bbox else 0
        if content_h > canvas.winfo_height():
            if not scrollbar.winfo_ismapped():
                # before=canvas so the scrollbar claims its width ahead of the
                # expanding canvas; packed after it, it is allocated nothing.
                scrollbar.pack(side="right", fill="y", before=canvas)
                scrollbar.update_idletasks()
        elif scrollbar.winfo_ismapped():
            scrollbar.pack_forget()

    canvas.after_idle(sync)


class _RankRow(tk.Canvas):
    """One row of a top-N list: a proportional bar with the rank, name and
    count drawn over it.

    Drawn on a canvas rather than assembled from Labels because a Tk Label is
    always opaque -- laying labels over a partial-width bar punched three
    rectangles of card color straight through it.
    """

    HEIGHT = 26

    def __init__(self, parent, rank, name, count_text, fraction, accent, on_click=None):
        super().__init__(parent, bg=CARD_BG, height=self.HEIGHT,
                         highlightthickness=0, bd=0)
        self._rank = str(rank)
        self._name = name
        self._count_text = count_text
        self._fraction = max(0.0, min(1.0, fraction))
        self._accent = accent
        self._on_click = on_click
        self._hover = False
        self._bar_img = None
        self.bind("<Configure>", lambda _e: self._draw())
        if on_click:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda _e: on_click())
            self.bind("<Enter>", lambda _e: self._set_hover(True))
            self.bind("<Leave>", lambda _e: self._set_hover(False))

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _draw(self):
        w = self.winfo_width()
        if w < 2:
            return
        self.delete("all")
        bar_w = int(w * self._fraction)
        if bar_w > 4:
            tint = 0.24 if self._hover else 0.15
            self._bar_img = rounded_rect(bar_w, self.HEIGHT - 4, 5,
                                         blend(self._accent, CARD_BG, tint))
            self.create_image(0, 2, anchor="nw", image=self._bar_img)
        mid = self.HEIGHT / 2
        self.create_text(9, mid, anchor="w", text=self._rank, font=font(8, "bold"),
                         fill=self._accent if self._hover else TEXT_FAINT)
        self.create_text(26, mid, anchor="w", text=self._name, font=font(9),
                         fill=self._accent if self._hover else TEXT_BRIGHT)
        self.create_text(w - 9, mid, anchor="e", text=self._count_text,
                         font=font(8), fill=TEXT_DIM)


class StatsWindow(FramelessPopup):
    """Popup showing a summary of the full mod-tracking history."""

    WIDTH, HEIGHT = 390, 646

    def __init__(self, parent, stats):
        super().__init__(parent, "Mod Tracker Stats")

        container = tk.Frame(self._shell, bg=BG)
        container.pack(fill="both", expand=True)
        inner = build_scroll_area(container)

        self._build_tiles(inner, stats)
        self._build_trend(inner, stats.get("daily_counts", []), stats.get("daily_dates", []))
        self._build_section(inner, "Top Authors · 30 days", stats["top_authors"], "mod",
                            ACCENT_NEW, link_ids=stats.get("author_ids"),
                            link_fallback=stats.get("author_links"))
        self._build_section(inner, "Top Categories · 30 days", stats["top_categories"],
                            "mod", ACCENT_UPD)
        tk.Frame(inner, bg=BG, height=10).pack(fill="x")

        self.resurface()

    # -- headline numbers ---------------------------------------------

    def _build_tiles(self, parent, stats):
        """Three equal tiles rather than one sentence of prose: the point of
        opening this window is comparing the numbers, and a row of tiles is the
        shape that actually lets you do that at a glance."""
        daily = stats.get("daily_counts") or []
        per_day = round(sum(daily) / len(daily), 1) if daily else 0
        tiles = [
            (f"{stats['total']:,}", "TRACKED", TEXT_BRIGHT),
            (str(stats["added_this_week"]), "LAST 7 DAYS", ACCENT_NEW),
            (f"{per_day:g}", "PER DAY", ACCENT_UPD),
        ]
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=16, pady=(16, 12))
        for i, (value, label, color) in enumerate(tiles):
            row.columnconfigure(i, weight=1, uniform="tile")
            panel = RoundedPanel(row, fill=CARD_BG, bg=BG, padx=10, pady=9)
            panel.grid(row=0, column=i, sticky="ew",
                       padx=(0 if i == 0 else 5, 0 if i == 2 else 5))
            tk.Label(panel.body, text=value, font=font(16, "bold"), fg=color,
                     bg=CARD_BG, anchor="w").pack(fill="x")
            tk.Label(panel.body, text=label, font=font(7, "bold"), fg=TEXT_FAINT,
                     bg=CARD_BG, anchor="w").pack(fill="x", pady=(2, 0))

    def _build_trend(self, parent, daily_counts, daily_dates):
        if not daily_counts:
            return
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=16)
        section_heading(head, f"Added per day · last {TREND_WINDOW_DAYS} days")

        height = 76
        pad_top, pad_bottom = 10, 8
        panel = RoundedPanel(parent, fill=CARD_BG, bg=BG, padx=0, pady=0,
                             height=height)
        panel.pack(fill="x", padx=16, pady=(0, 14))
        spark = tk.Canvas(panel.body, bg=CARD_BG, height=height,
                          highlightthickness=0, bd=0)
        spark.pack(fill="both", expand=True)

        peak = max(daily_counts) or 1
        n = len(daily_counts)
        fill_color = blend(ACCENT_NEW, CARD_BG, 0.22)
        layout = {}

        def draw(_e=None):
            spark.delete("all")
            w = spark.winfo_width()
            if w < 2 or n < 2:
                return
            usable_h = height - pad_top - pad_bottom
            step = w / (n - 1)
            points = [
                (i * step, pad_top + usable_h - (count / peak) * usable_h)
                for i, count in enumerate(daily_counts)
            ]
            layout["points"] = points
            layout["step"] = step

            baseline = height - pad_bottom
            smooth_pts = _catmull_rom(points)

            # Tk's canvas has no anti-aliasing, so a 2px diagonal line comes out
            # visibly stair-stepped. Render the curve at 4x with Pillow and
            # downsample -- the resample filter does the anti-aliasing for free.
            SS = 4
            img = Image.new("RGB", (w * SS, height * SS), CARD_BG)
            idraw = ImageDraw.Draw(img)
            idraw.line([(0, baseline * SS), (w * SS, baseline * SS)],
                       fill=SEPARATOR, width=SS)

            ss_pts = [(x * SS, y * SS) for x, y in smooth_pts]
            area_pts = [(points[0][0] * SS, baseline * SS), *ss_pts,
                        (points[-1][0] * SS, baseline * SS)]
            idraw.polygon(area_pts, fill=fill_color)
            idraw.line(ss_pts, fill=ACCENT_NEW, width=2 * SS, joint="curve")

            img = img.resize((w, height), Image.LANCZOS)
            layout["photo"] = ImageTk.PhotoImage(img)
            spark.create_image(0, 0, anchor="nw", image=layout["photo"])

        spark.bind("<Configure>", draw)

        hover_items = []

        def clear_hover():
            for item in hover_items:
                spark.delete(item)
            hover_items.clear()

        def on_motion(e):
            points = layout.get("points")
            if not points:
                return
            idx = max(0, min(n - 1, round(e.x / layout["step"])))
            x, y = points[idx]
            clear_hover()
            w = spark.winfo_width()

            hover_items.append(spark.create_line(x, 0, x, height, fill=BORDER, dash=(2, 3)))
            hover_items.append(spark.create_oval(x - 3, y - 3, x + 3, y + 3,
                                                 fill=ACCENT_NEW, outline=CARD_BG, width=1))

            count = daily_counts[idx]
            label = f"{_format_day(daily_dates[idx])}  ·  {count} mod{'' if count == 1 else 's'}"
            text_x = min(max(x, 48), max(48, w - 48))
            text_y = 11 if y > 22 else height - 11
            tid = spark.create_text(text_x, text_y, text=label, fill=TEXT_BRIGHT,
                                    font=font(7, "bold"))
            bbox = spark.bbox(tid)
            rid = spark.create_rectangle(bbox[0] - 6, bbox[1] - 3, bbox[2] + 6, bbox[3] + 3,
                                         fill=STATUS_BG, outline="")
            spark.tag_raise(tid, rid)
            hover_items.extend([rid, tid])

        spark.bind("<Motion>", on_motion)
        spark.bind("<Leave>", lambda _e: clear_hover())

    def _build_section(self, parent, heading, entries, unit, accent,
                       link_ids=None, link_fallback=None):
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=16)
        section_heading(head, heading)

        panel = RoundedPanel(parent, fill=CARD_BG, bg=BG, padx=4, pady=6)
        panel.pack(fill="x", padx=16, pady=(0, 14))
        section = panel.body

        if not entries:
            tk.Label(section, text="Not enough data yet", font=font(9),
                     fg=TEXT_DIM, bg=CARD_BG, anchor="w").pack(fill="x", padx=10, pady=6)
            return

        top = max(count for _n, count in entries) or 1
        for i, (name, count) in enumerate(entries):
            author_id = link_ids.get(name) if link_ids else None
            fallback_link = link_fallback.get(name) if link_fallback else None
            open_author = None
            if author_id or fallback_link:
                open_author = (lambda n=name, aid=author_id, link=fallback_link:
                               self._open_author(n, aid, link))
            _RankRow(section, i + 1, name, f"{count} {unit}{'' if count == 1 else 's'}",
                     count / top, accent, open_author).pack(fill="x", padx=6, pady=1)

    def _open_author(self, name, author_id, fallback_link):
        if author_id:
            webbrowser.open(f"{FORGE_USER_URL}/{author_id}/{name.lower()}")
        elif fallback_link:
            threading.Thread(
                target=self._resolve_and_open_author, args=(name, fallback_link), daemon=True,
            ).start()

    @staticmethod
    def _resolve_and_open_author(name, fallback_link):
        resolved_id = fetch_author_id(fallback_link)
        if resolved_id:
            webbrowser.open(f"{FORGE_USER_URL}/{resolved_id}/{name.lower()}")
        else:
            webbrowser.open(fallback_link)


_local_scan_placeholder_photo = None


def _local_scan_placeholder():
    """Shared placeholder thumbnail for local-scan result cards -- these mods
    aren't fetched from the Forge feed, so there's no thumbnail URL to use."""
    global _local_scan_placeholder_photo
    if _local_scan_placeholder_photo is None:
        _local_scan_placeholder_photo = ImageTk.PhotoImage(
            rounded_photo(placeholder_thumb()))
    return _local_scan_placeholder_photo


class LocalScanSettingsWindow(FramelessPopup):
    """Popup for the opt-in local-install scan: enable/disable, pick the SPT
    folder, trigger a scan, and show matched/unmatched results.

    Kept dumb by design, matching how the rest of this app's popups work --
    it owns no scan/network logic itself, only widgets + callbacks. The app
    (which already owns state + threading for the Forge poll) drives the
    actual scan and calls back into whichever of set_scanning/set_results/
    set_error applies once it's done.
    """

    WIDTH, HEIGHT = 470, 580

    def __init__(self, parent, enabled, spt_path, on_toggle, on_path_change,
                 on_scan_now, on_endorse=None, endorsed=()):
        super().__init__(parent, "Local Mod Scan")
        self._on_toggle = on_toggle
        self._on_path_change = on_path_change
        self._on_scan_now = on_scan_now
        self._on_endorse = on_endorse
        self._endorsed = set(endorsed)
        self._spt_path = spt_path
        self._photos = []
        shell = self._shell

        setup = RoundedPanel(shell, fill=CARD_BG, bg=BG, padx=14, pady=12)
        setup.pack(fill="x", padx=16, pady=(16, 12))
        form = setup.body

        self._enabled_var = tk.BooleanVar(value=enabled)
        ToggleSwitch(form, "Enable local mod scanning", self._enabled_var,
                     command=self._toggle, bg=CARD_BG, fg=TEXT).pack(anchor="w")

        tk.Label(form, text="SPT INSTALL FOLDER", font=font(7, "bold"), fg=TEXT_FAINT,
                 bg=CARD_BG, anchor="w").pack(fill="x", pady=(14, 4))

        path_row = tk.Frame(form, bg=CARD_BG)
        path_row.pack(fill="x")
        # Packed before the label (and without fill/expand) so it always
        # claims its space first -- a long install path can no longer push
        # it out past the edge of this fixed-width window the way it could
        # when the label (which has no length limit) was packed first.
        flat_button(path_row, "Browse…", self._browse, bg=CARD_BG,
                    padx=10, pady=4).pack(side="right", padx=(10, 0))
        field = RoundedPanel(path_row, fill=BG, outline=BORDER, bg=CARD_BG,
                             padx=10, pady=5, radius=6)
        field.pack(side="left", fill="x", expand=True)
        self._path_lbl = tk.Label(field.body, text=self._display_path(), font=font(8),
                                  fg=TEXT_DIM, bg=BG, anchor="w")
        self._path_lbl.pack(fill="x")

        self._validation_lbl = tk.Label(form, text="", font=font(8), bg=CARD_BG,
                                        anchor="w")
        self._validation_lbl.pack(fill="x", pady=(8, 12))

        self._scan_btn = flat_button(form, "Scan Now", self._scan_now,
                                     accent=ACCENT_NEW, bg=CARD_BG, padx=16, pady=6)
        self._scan_btn.pack(anchor="w")

        # Progress bar -- hidden until a scan is actually running (see
        # set_progress); packed/unpacked rather than left empty so it takes
        # zero space between scans instead of leaving an odd gap.
        self._progress_frac = 0.0
        self._progress_frame = tk.Frame(shell, bg=BG)
        self._progress_lbl = tk.Label(self._progress_frame, text="", font=font(8),
                                      fg=TEXT_DIM, bg=BG, anchor="w")
        self._progress_lbl.pack(fill="x", pady=(0, 5))
        self._progress_canvas = tk.Canvas(self._progress_frame, height=5, bg=BG,
                                          highlightthickness=0, bd=0)
        self._progress_canvas.pack(fill="x")
        self._progress_canvas.bind("<Configure>", lambda _e: self._draw_progress())

        # Scrollable results area, shared plumbing with StatsWindow.
        container = tk.Frame(shell, bg=BG)
        self._container = container
        container.pack(fill="both", expand=True, padx=(16, 4), pady=(0, 16))
        self._results_frame = build_scroll_area(container)

        self._update_validation()
        self._set_message("Click Scan Now to check your installed mods against the Forge.")
        self.resurface()

    def _display_path(self):
        if not self._spt_path:
            return "No folder selected"
        # The tail of the path (e.g. the SPT folder's own name) is what
        # actually identifies which install this is -- truncate from the
        # front so a long path can't visually crowd out the Browse button.
        max_len = 46
        if len(self._spt_path) <= max_len:
            return self._spt_path
        return "…" + self._spt_path[-(max_len - 1):]

    def _toggle(self):
        enabled = self._enabled_var.get()
        self._on_toggle(enabled)
        self._update_validation()

    def _browse(self):
        # Deliberately NOT using -topmost to survive the native folder
        # picker: that would keep this popup above the picker too, forcing
        # the user to drag the picker out from under it just to see their
        # folders. resurface() afterwards is enough.
        chosen = filedialog.askdirectory(
            parent=self, initialdir=self._spt_path or None, title="Select your SPT install folder",
        )
        self.resurface()
        if not chosen:
            return
        self._spt_path = chosen
        self._path_lbl.configure(text=self._display_path())
        self._on_path_change(chosen)
        self._update_validation()

    def _update_validation(self):
        if not self._spt_path:
            self._validation_lbl.configure(text="", fg=TEXT_DIM)
            valid = False
        elif validate_spt_root(self._spt_path):
            self._validation_lbl.configure(text="✓  Looks like a valid SPT install",
                                           fg=ACCENT_NEW)
            valid = True
        else:
            self._validation_lbl.configure(
                text="⚠  Couldn't find BepInEx/plugins or SPT/user/mods here", fg=ACCENT_UPD)
            valid = False
        self._scan_btn.configure(state="normal" if (valid and self._enabled_var.get()) else "disabled")

    def _scan_now(self):
        self.set_scanning()
        self._on_scan_now()

    def _clear_results(self):
        for w in self._results_frame.winfo_children():
            w.destroy()
        self._photos.clear()

    def _set_message(self, text):
        """Centered rather than left-aligned: with the results list empty this
        is the only thing in a tall pane, and a line of text pinned to the top
        left corner of all that space reads as a leftover label."""
        self._clear_results()
        holder = tk.Frame(self._results_frame, bg=BG)
        holder.pack(fill="both", expand=True, pady=(46, 0))
        tk.Label(holder, text=text, font=font(9), fg=TEXT_FAINT, bg=BG,
                 wraplength=self.WIDTH - 90, justify="center").pack()

    def set_scanning(self):
        """Put the whole window into its scanning state: button disabled,
        message shown, progress bar visible. Also called by the app when the
        window is opened while a scan is already running."""
        self._scan_btn.configure(state="disabled", text="Scanning…")
        self._set_message("Scanning your mod folders…")
        self._progress_frac = 0.0
        self._progress_lbl.configure(text="Reading installed mods…")
        self._draw_progress()
        self._show_progress_frame()

    def set_error(self, msg):
        self._scan_btn.configure(text="Scan Now")
        self._update_validation()
        self._progress_frame.pack_forget()
        self._set_message(f"Scan failed: {msg}")
        self.resurface()

    # ── Progress bar ──────────────────────────────────────────────────

    def _show_progress_frame(self):
        # Packed/unpacked rather than left empty so it takes zero space
        # between scans instead of leaving an odd gap.
        if not self._progress_frame.winfo_ismapped():
            self._progress_frame.pack(fill="x", padx=16, pady=(0, 10),
                                      before=self._container)

    def _draw_progress(self):
        self._progress_canvas.delete("all")
        w = self._progress_canvas.winfo_width()
        h = self._progress_canvas.winfo_height()
        if w < 2 or h < 2:
            return
        self._track_img = pill(w, h, SEPARATOR)
        self._progress_canvas.create_image(0, 0, anchor="nw", image=self._track_img)
        filled = int(w * self._progress_frac)
        if filled >= h:
            self._fill_img = pill(filled, h, ACCENT_UPD)
            self._progress_canvas.create_image(0, 0, anchor="nw", image=self._fill_img)

    def set_progress(self, done, total):
        # Matching runs on a background thread paced by a per-mod network
        # lookup, so this is the part of a scan actually worth showing
        # progress for -- the file-discovery phase before it is fast enough
        # not to need it.
        if total <= 0:
            return
        self._show_progress_frame()
        self._progress_lbl.configure(text=f"Checking mod {done} of {total} against the Forge…")
        self._progress_frac = min(1.0, done / total)
        self._draw_progress()

    def _add_section_header(self, text, color=TEXT_BRIGHT, count=None):
        row = tk.Frame(self._results_frame, bg=BG)
        row.pack(fill="x", pady=(14, 6))
        tk.Label(row, image=dot(7, color), bg=BG).pack(side="left", padx=(0, 7))
        row._dot = dot(7, color)
        tk.Label(row, text=text.upper(), font=font(8, "bold"), fg=color,
                 bg=BG, anchor="w").pack(side="left")
        if count is not None:
            tk.Label(row, text=str(count), font=font(8, "bold"), fg=TEXT_FAINT,
                     bg=BG).pack(side="left", padx=(6, 0))
        return row

    def _open_all(self, links):
        # Staggered rather than fired in a tight loop -- opening a dozen tabs
        # in the same instant is what gets browsers/OS popup-blockers to
        # start silently dropping some of them.
        for i, link in enumerate(links):
            self.after(i * 200, webbrowser.open, link)

    def _add_plain_row(self, text, link=None, wrap=False):
        row = tk.Label(self._results_frame, text=text, font=font(9),
                       fg=TEXT_DIM, bg=BG, anchor="w", cursor="hand2" if link else "arrow",
                       wraplength=self.WIDTH - 60 if wrap else 0, justify="left")
        row.pack(fill="x", padx=(2, 0), pady=2, anchor="w")
        if link:
            row.bind("<Button-1>", lambda _e: webbrowser.open(link))
            row.bind("<Enter>", lambda _e: row.configure(fg=TEXT_BRIGHT))
            row.bind("<Leave>", lambda _e: row.configure(fg=TEXT_DIM))

    def _mark_endorsed(self, link):
        self._endorsed.add(link)
        if self._on_endorse:
            self._on_endorse(link)

    def set_results(self, results):
        self._scan_btn.configure(text="Scan Now")
        self._update_validation()
        self._progress_frame.pack_forget()
        self._clear_results()

        updates = [r for r in results if r["update_available"]]
        up_to_date = [r for r in results if r["forge"] and not r["update_available"]]
        # A rate-limited lookup comes back shaped exactly like a genuine miss:
        # no forge match. Split the two apart before display, so a throttled
        # scan reports what actually happened instead of accusing a pile of
        # perfectly normal installed mods of not existing.
        unchecked = [r for r in results if not r["forge"] and r.get("lookup_failed")]
        unmatched = [r for r in results if not r["forge"] and not r.get("lookup_failed")]

        summary = tk.Frame(self._results_frame, bg=BG)
        summary.pack(fill="x", pady=(10, 0))
        tk.Label(summary, text=f"{len(results)} mods scanned", font=font(11, "bold"),
                 fg=TEXT_BRIGHT, bg=BG, anchor="w").pack(fill="x")
        breakdown = tk.Frame(summary, bg=BG)
        breakdown.pack(fill="x", pady=(6, 0))
        parts = [(len(updates), "to update", ACCENT_UPD),
                 (len(up_to_date), "up to date", ACCENT_NEW),
                 (len(unmatched), "not on Forge", TEXT_DIM)]
        if unchecked:
            parts.append((len(unchecked), "unchecked", ACCENT_DANGER))
        for n, label, color in parts:
            chip(breakdown, f"{n} {label}", color, surface=BG,
                 font_size=8, weight="normal").pack(side="left", padx=(0, 6))

        if updates:
            header_row = self._add_section_header("Updates Available", ACCENT_UPD,
                                                  len(updates))
            links = [r["forge"]["link"] for r in updates]
            flat_button(header_row, "Open All", lambda links=links: self._open_all(links),
                        font_size=8, padx=8, pady=2).pack(side="right", padx=(0, 10))

            for r in updates:
                forge = r["forge"]
                mod = {
                    "title": forge["title"],
                    "link": forge["link"],
                    "author": forge["author"],
                    "category": forge.get("category", ""),
                    "version": r["available_version"],
                    "prev_version": r["current_version"],
                    "description": forge.get("description", ""),
                    "local_update_available": True,
                    "endorsed": forge["link"] in self._endorsed,
                }
                accent = CATEGORY_COLORS.get(mod["category"], CATEGORY_COLOR_DEFAULT)
                pil = r.get("_pil")
                photo = ImageTk.PhotoImage(rounded_photo(pil)) if pil else _local_scan_placeholder()
                self._photos.append(photo)
                ModCard(self._results_frame, mod, accent, photo,
                        on_endorse=self._mark_endorsed).pack(
                    fill="x", pady=CARD_GAP, padx=(0, 4))

        if up_to_date:
            self._add_section_header("Up to Date", ACCENT_NEW, len(up_to_date))
            for r in up_to_date:
                name = r["local"].get("name") or r["forge"]["title"]
                self._add_plain_row(f"{name}   ·   v{r['current_version']}",
                                    link=r["forge"]["link"])

        if unmatched:
            self._add_section_header("Not Found on Forge", TEXT_DIM, len(unmatched))
            for r in unmatched:
                name = r["local"].get("name") or "(unknown)"
                self._add_plain_row(f"{name}   ·   v{r['local'].get('version') or '?'}")

        if unchecked:
            self._add_section_header("Couldn't Check", ACCENT_DANGER, len(unchecked))
            self._add_plain_row(
                "The Forge rate-limited these lookups — they may well be fine. "
                "Scan again in a minute.", wrap=True)
            for r in unchecked:
                name = r["local"].get("name") or "(unknown)"
                self._add_plain_row(f"{name}   ·   v{r['local'].get('version') or '?'}")

        self.resurface()


def _relative_time(ts_str):
    """Convert an ISO or RFC 2822 timestamp to a relative string."""
    dt = parse_dt(ts_str)
    if dt is None:
        return ""
    s = (datetime.now(timezone.utc) - dt).total_seconds()
    if s < 60:
        return "just now"
    elif s < 3600:
        return f"{int(s/60)}m ago"
    elif s < 86400:
        return f"{int(s/3600)}h ago"
    elif s < 86400 * 2:
        return "yesterday"
    else:
        return f"{int(s/86400)}d ago"


def _is_new_author(author_since):
    dt = parse_dt(author_since)
    if dt is None:
        return False
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    return age_days <= NEW_AUTHOR_DAYS


class _ClippedText:
    """One line of card text on its own canvas, so it is clipped by the canvas
    rather than spilling across the card.

    Shows an ellipsized version at rest and the full string, marqueeing, while
    the card is hovered: a static line that ends mid-word reads as a rendering
    bug, and a line that scrolls whether or not you are looking at it is
    restless in a list of fourteen.
    """

    def __init__(self, parent, text, fnt, fill, bg):
        self.full = text or ""
        self.font = fnt
        self.canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0,
                                height=fnt.metrics("linespace") + 2, cursor="hand2")
        self.item = self.canvas.create_text(0, 0, text=self.full, anchor="nw",
                                            font=fnt, fill=fill)
        self.text_w = fnt.measure(self.full)
        self._hover = False
        self.canvas.bind("<Configure>", lambda _e: self._render())

    def _width(self):
        w = self.canvas.winfo_width()
        return w if w > 1 else 0

    def _render(self):
        self.canvas.coords(self.item, 0, 0)
        shown = self.full if self._hover else ellipsize(self.font, self.full, self._width())
        self.canvas.itemconfigure(self.item, text=shown)

    def set_hover(self, on):
        self._hover = on
        self._render()

    def overflow(self):
        w = self._width()
        return max(0, self.text_w - w + 8) if w else 0

    def offset(self, dx):
        self.canvas.coords(self.item, -dx, 0)


CARD_THUMB = 52
CARD_GAP = 3        # vertical space above and below each card in a column
_CARD_PAD_Y = 7     # card edge to content box, top and bottom
# Vertical padding baked into the meta row: pady=(4, 3) around the chips.
_META_PAD_Y = 7

_card_metrics = None
_measuring = False


def _estimated_body_height():
    """Rough content height, used only to build the probe card below."""
    # +2 on each text line matches the slack _ClippedText adds to its canvas so
    # descenders aren't shaved off.
    title = font(9, "bold").metrics("linespace") + 2
    meta = font(7, "bold").metrics("linespace") + 4 + _META_PAD_Y
    desc = font(8).metrics("linespace") + 2
    return max(CARD_THUMB, title + meta + desc)


def card_metrics(master=None):
    """(content box height, total card height) for every card in the app.

    Taken from a real card built off-screen and asked for its own requested
    height, rather than predicted by adding up font metrics. The arithmetic
    version was a pixel short -- widget borders, image padding and the grid's
    own rounding all contribute, and being one pixel out clips the descenders
    off the description row on every card in the window.

    It has to be a measurement rather than a constant because Tk font sizes are
    in points: every line in a card grows with the Windows display scaling
    setting, so a height pinned to the 100% numbers pushes the description row
    out through the bottom of the card at 125% or 150%.

    Measured once per process -- fonts are cached and cannot change at runtime.
    """
    global _card_metrics, _measuring
    if _card_metrics is not None:
        return _card_metrics
    if _measuring:
        # The probe card is being constructed right now: hand it the estimate,
        # which only decides the canvas height it will be measured independently of.
        body = _estimated_body_height()
        return body, body + _CARD_PAD_Y * 2

    root = master or tk._default_root
    _measuring = True
    try:
        holder = tk.Frame(root, bg=BG)
        probe = ModCard(holder, {
            # The tallest arrangement a card can take: every chip present, the
            # change-notes glyph shown, and text with ascenders and descenders.
            "title": "Ag", "author": "Ag", "description": "Ag",
            "version": "1.0.0", "prev_version": "0.9.0", "category": "Tools",
            "link": "probe", "changelog": "x", "is_fresh": True,
        }, CATEGORY_COLOR_DEFAULT, _probe_photo())
        probe.update_idletasks()
        body = max(CARD_THUMB, probe._body.winfo_reqheight())
        holder.destroy()
    finally:
        _measuring = False
    _card_metrics = (body, body + _CARD_PAD_Y * 2)
    return _card_metrics


_probe_photo_img = None


def _probe_photo():
    global _probe_photo_img
    if _probe_photo_img is None:
        _probe_photo_img = ImageTk.PhotoImage(
            Image.new("RGBA", (CARD_THUMB, CARD_THUMB), (0, 0, 0, 0)))
    return _probe_photo_img


def card_pitch(master=None):
    """Height one card occupies in a column, including the gap around it."""
    return card_metrics(master)[1] + CARD_GAP * 2


class ModCard(tk.Canvas):
    """One mod in a column: thumbnail, title, metadata and description on a
    rounded surface outlined in its category color."""

    THUMB = CARD_THUMB
    PAD_X = 10       # card edge to content (clears the 4px category rail)
    PAD_R = 11
    GAP = 10         # thumbnail to text

    def __init__(self, parent, mod, accent, photo, on_endorse=None):
        # Content box is taller than the thumbnail so the three text rows get
        # their full line height -- sized to the thumbnail instead, the
        # description row loses its descenders to the bottom of the card.
        self.BODY_H, self.HEIGHT = card_metrics(parent)
        super().__init__(parent, bg=parent.cget("bg"), height=self.HEIGHT,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._mod = mod
        self._photo = photo
        self._accent = accent
        self._on_endorse = on_endorse
        self._endorsed = bool(mod.get("endorsed"))
        self._fill = CARD_BG
        self._lines = []
        self._chips = []
        self._scroll_id = None
        self._scroll_offset = 0
        self._leave_id = None
        self._caps = None

        body = self._body = tk.Frame(self, bg=CARD_BG)
        self._body_id = self.create_window(self.PAD_X, 0, window=body, anchor="nw")

        img_lbl = tk.Label(body, image=photo, bg=CARD_BG, cursor="hand2", bd=0)
        img_lbl.grid(row=0, column=0, rowspan=3, padx=(0, self.GAP), pady=(2, 0))
        self._img_lbl = img_lbl

        body.columnconfigure(1, weight=1)

        # Row 0 -- title, with the age pinned to the far right so a long title
        # can never push it out of the card.
        title_row = tk.Frame(body, bg=CARD_BG)
        title_row.grid(row=0, column=1, sticky="ew")
        ts = _relative_time(mod.get("updated", ""))
        if ts:
            tk.Label(title_row, text=ts, font=font(8), fg=TEXT_FAINT, bg=CARD_BG,
                     cursor="hand2").pack(side="right", padx=(8, 0))
        title = _ClippedText(title_row, mod["title"], font(9, "bold"), TEXT_BRIGHT, CARD_BG)
        title.canvas.pack(side="left", fill="x", expand=True)
        self._lines.append(title)

        # Row 1 -- version, badges, attribution, change-notes affordance.
        meta = tk.Frame(body, bg=CARD_BG)
        meta.grid(row=1, column=1, sticky="ew", pady=(4, 3))
        self._meta = meta

        version = mod.get("version", "")
        if mod.get("prev_version") and version:
            self._add_chip(meta, f"{mod['prev_version']} → {version}", ACCENT_UPD)
        elif version:
            self._add_chip(meta, f"v{version}", TEXT_BRIGHT, tint=0.10)
        if mod.get("is_fresh"):
            self._add_chip(meta, "NEW", ACCENT_NEW)
        if mod.get("local_update_available"):
            self._add_chip(meta, "UPDATE", ACCENT_UPD)
        if _is_new_author(mod.get("author_since", "")):
            self._add_chip(meta, "NEW AUTHOR", ACCENT_NEW_AUTHOR)

        attribution = mod.get("author", "")
        if mod.get("category"):
            attribution += f"   ·   {mod['category']}"
        attr = _ClippedText(meta, attribution, font(8), TEXT_DIM, CARD_BG)
        attr.canvas.pack(side="left", fill="x", expand=True)
        self._lines.append(attr)

        # Row 2 -- description, with the two action icons in the corner beside
        # it. They sit on this row rather than up on the meta row because they
        # have to take their width from something: next to the attribution they
        # ate into the author's name, and a truncated author is a worse trade
        # than a slightly shorter description teaser.
        desc_row = tk.Frame(body, bg=CARD_BG)
        desc_row.grid(row=2, column=1, sticky="ew")
        self._desc_row = desc_row

        self._notes_lbl = None
        if mod.get("changelog"):
            self._notes_dim = notes_glyph(13, 14, TEXT_FAINT)
            self._notes_lit = notes_glyph(13, 14, ACCENT_UPD)
            notes = tk.Label(desc_row, image=self._notes_dim, bg=CARD_BG,
                             cursor="hand2", bd=0)
            notes.pack(side="right", padx=(7, 2))
            notes.bind("<Enter>", lambda e: (self._enter(e),
                                             notes.configure(image=self._notes_lit)))
            notes.bind("<Leave>", lambda e: (self._leave(e),
                                             notes.configure(image=self._notes_dim)))
            notes.bind("<Button-1>", self._toggle_change_notes)
            self._notes_lbl = notes

        self._endorse_lbl = None
        if ENDORSE_ENABLED and mod.get("link"):
            self._sync_endorse_glyph(build=True)

        desc = _ClippedText(desc_row, mod.get("description", ""), font(8),
                            TEXT_DIM, CARD_BG)
        desc.canvas.pack(side="left", fill="x", expand=True)
        self._lines.append(desc)

        for w in self._interactive():
            w.bind("<Button-1>", self._click)
            w.bind("<Button-3>", self._right_click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

        self.bind("<Configure>", self._layout)

    # ── Construction helpers ───────────────────────────────────────────

    def _add_chip(self, parent, text, color, tint=0.18):
        lbl = chip(parent, text, color, surface=CARD_BG, font_size=7, tint=tint)
        lbl.pack(side="left", padx=(0, 5))
        self._chips.append(lbl)

    def _sync_endorse_glyph(self, build=False):
        """Draw the thumbs-up in the state matching self._endorsed."""
        dim = ACCENT_NEW if self._endorsed else TEXT_FAINT
        self._endorse_dim = endorse_glyph(14, dim, filled=self._endorsed)
        self._endorse_lit = endorse_glyph(14, ACCENT_NEW, filled=True)
        if build:
            lbl = tk.Label(self._desc_row, image=self._endorse_dim, bg=CARD_BG,
                           cursor="hand2", bd=0)
            lbl.pack(side="right", padx=(7, 0))
            lbl.bind("<Enter>", lambda e: (self._enter(e),
                                           lbl.configure(image=self._endorse_lit)))
            lbl.bind("<Leave>", lambda e: (self._leave(e),
                                           lbl.configure(image=self._endorse_dim)))
            lbl.bind("<Button-1>", self._endorse)
            self._endorse_lbl = lbl
        elif self._endorse_lbl is not None:
            self._endorse_lbl.configure(image=self._endorse_dim)

    def _endorse(self, _e=None):
        """Open the mod's Forge page, where the endorse button lives.

        Unreachable while ENDORSE_ENABLED is False -- see the note beside it in
        config for why the button is out of the UI and what has to change for it
        to come back. Kept working so that turning it on is a one-line change.

        The app cannot endorse on the user's behalf: the Forge API is
        documented as read-only and unauthenticated, so there is no endpoint to
        call and no way to prove who is asking. So this takes them to the right
        page in one click and marks the card locally as one they went to
        endorse -- the Forge remains the record of what actually counted.
        """
        webbrowser.open(self._mod["link"])
        if not self._endorsed:
            self._endorsed = True
            self._mod["endorsed"] = True
            self._sync_endorse_glyph()
            if self._on_endorse:
                self._on_endorse(self._mod["link"])
        return "break"

    def _interactive(self):
        skip = {self._notes_lbl, self._endorse_lbl}
        widgets = [self, self._body, self._img_lbl, self._meta, self._desc_row]
        widgets += [line.canvas for line in self._lines]
        widgets += self._chips
        widgets += [w for w in self._meta.winfo_children() if w not in skip]
        widgets += [w for w in self._desc_row.winfo_children() if w not in skip]
        return widgets

    # ── Painting ───────────────────────────────────────────────────────

    def _layout(self, _e=None):
        w = self.winfo_width()
        h = self.HEIGHT
        if w < 2:
            return
        self.delete("cardbg")
        left, right = card_caps(h, self._fill, self._accent)
        self._caps = (left, right)
        self.create_image(0, 0, anchor="nw", image=left, tags="cardbg")
        self.create_image(w - CAP_W, 0, anchor="nw", image=right, tags="cardbg")
        x0, x1 = CAP_W, w - CAP_W
        if x1 > x0:
            b = CARD_BORDER_W
            self.create_rectangle(x0, b, x1, h - b, fill=self._fill,
                                  width=0, tags="cardbg")
            self.create_rectangle(x0, 0, x1, b, fill=self._accent, width=0, tags="cardbg")
            self.create_rectangle(x0, h - b, x1, h, fill=self._accent, width=0, tags="cardbg")
        self.tag_lower("cardbg")

        self.coords(self._body_id, self.PAD_X, (h - self.BODY_H) // 2)
        self.itemconfigure(self._body_id, width=max(1, w - self.PAD_X - self.PAD_R),
                           height=self.BODY_H)

    def _set_surface(self, color):
        self._fill = color
        self._layout()
        for widget in (self._body, self._img_lbl, self._meta, self._desc_row,
                       *(line.canvas for line in self._lines), *self._chips,
                       *self._meta.winfo_children(),
                       *self._desc_row.winfo_children()):
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass
        for child in self._body.winfo_children():
            try:
                child.configure(bg=color)
            except tk.TclError:
                pass

    # ── Interaction ────────────────────────────────────────────────────

    def _click(self, _e):
        webbrowser.open(self._mod["link"])

    def _right_click(self, e):
        items = [
            ("Open on Forge", lambda: webbrowser.open(self._mod["link"])),
            ("-", None),
            ("Copy Link", self._copy_link),
        ]
        if ENDORSE_ENABLED:
            items.insert(1, ("Endorse on Forge", self._endorse))
        if self._mod.get("changelog"):
            items.insert(0, ("View Change Notes", self._show_change_notes))
            items.insert(1, ("-", None))
        ContextMenu(self, items).show(e.x_root, e.y_root)

    def _toggle_change_notes(self, _e=None):
        """Clicking the notes icon a second time closes what it opened."""
        # Anchored to the card, so the icon that opened the popup is never
        # underneath it and stays available to close it again.
        ChangeNotesWindow.toggle(self.winfo_toplevel(), self._mod, anchor=self)
        return "break"

    def _show_change_notes(self):
        ChangeNotesWindow.show(self.winfo_toplevel(), self._mod, anchor=self)

    def _copy_link(self):
        self.clipboard_clear()
        self.clipboard_append(self._mod["link"])

    # ── Hover with debounce ────────────────────────────────────────────

    def _enter(self, _e):
        if self._leave_id:
            self.after_cancel(self._leave_id)
            self._leave_id = None
        self._set_surface(CARD_HOVER)
        for line in self._lines:
            line.set_hover(True)
        if not self._scroll_id:
            self._start_scroll()

    def _leave(self, _e):
        if self._leave_id:
            self.after_cancel(self._leave_id)
        self._leave_id = self.after(50, self._do_leave)

    def _do_leave(self):
        self._leave_id = None
        self._set_surface(CARD_BG)
        self._stop_scroll()
        for line in self._lines:
            line.set_hover(False)

    # ── Scroll logic ───────────────────────────────────────────────────

    def _max_overflow(self):
        return max((line.overflow() for line in self._lines), default=0)

    def _start_scroll(self):
        self._scroll_offset = 0
        self._scroll_id = self.after(PAUSE_START_MS, self._scroll_tick)

    def _stop_scroll(self):
        if self._scroll_id:
            self.after_cancel(self._scroll_id)
            self._scroll_id = None
        self._scroll_offset = 0
        for line in self._lines:
            line.offset(0)

    def _scroll_tick(self):
        overflow = self._max_overflow()
        if overflow <= 0:
            self._scroll_id = None
            return

        self._scroll_offset += SCROLL_PX
        for line in self._lines:
            limit = line.overflow()
            if limit > 0:
                line.offset(min(self._scroll_offset, limit))

        if self._scroll_offset >= overflow:
            self._scroll_id = self.after(PAUSE_END_MS, self._reset_scroll)
        else:
            self._scroll_id = self.after(SCROLL_INTERVAL_MS, self._scroll_tick)

    def _reset_scroll(self):
        self._scroll_offset = 0
        for line in self._lines:
            line.offset(0)
        self._scroll_id = self.after(PAUSE_RESET_MS, self._scroll_tick)


class MiniScrollbar(tk.Canvas):
    """Minimal dark-themed vertical scrollbar for pairing with a Text widget."""

    def __init__(self, parent, **kw):
        kw.setdefault("bg", BG)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("width", 10)
        super().__init__(parent, **kw)
        self._first = 0.0
        self._last = 1.0
        self._command = None
        self._drag_start_y = None
        self._drag_start_first = None
        self._hover = False
        self._thumb_img = None

        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_command(self, command):
        self._command = command

    def set(self, first, last):
        self._first = float(first)
        self._last = float(last)
        self._draw()

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _thumb_coords(self):
        h = self.winfo_height()
        if h < 2:
            return None
        y0 = self._first * h
        y1 = self._last * h
        min_h = 24
        if y1 - y0 < min_h:
            mid = (y0 + y1) / 2
            y0 = max(0, mid - min_h / 2)
            y1 = min(h, y0 + min_h)
        return y0, y1

    def _draw(self):
        self.delete("all")
        if self._last - self._first >= 0.999:
            return
        coords = self._thumb_coords()
        if not coords:
            return
        y0, y1 = coords
        w = self.winfo_width()
        pad = 3
        thumb_w = max(1, w - pad * 2)
        thumb_h = max(1, int(y1 - y0) - 2)
        self._thumb_img = pill(thumb_w, thumb_h,
                               lighten(BORDER, 0.18) if self._hover else BORDER)
        self.create_image(pad, int(y0) + 1, anchor="nw", image=self._thumb_img)

    def _on_press(self, e):
        coords = self._thumb_coords()
        if not (coords and coords[0] <= e.y <= coords[1]) and self._command:
            h = self.winfo_height()
            frac = max(0.0, min(1.0, e.y / max(1, h)))
            self._command("moveto", frac)
        self._drag_start_y = e.y
        self._drag_start_first = self._first

    def _on_drag(self, e):
        if self._drag_start_y is None or not self._command:
            return
        h = self.winfo_height()
        if h < 2:
            return
        delta = (e.y - self._drag_start_y) / h
        frac = max(0.0, min(1.0, self._drag_start_first + delta))
        self._command("moveto", frac)

    def _on_release(self, _e):
        self._drag_start_y = None
        self._drag_start_first = None
