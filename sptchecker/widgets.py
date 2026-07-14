import re
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from .config import (
    ACCENT_NEW, ACCENT_NEW_AUTHOR, ACCENT_UPD, BG, CARD_BG, CARD_HOVER,
    FORGE_USER_URL, NEW_AUTHOR_DAYS, SEPARATOR, STATUS_BG, TEXT_BRIGHT, TEXT_DIM,
)
from .feed import fetch_author_id

class ContextMenu(tk.Toplevel):
    """Styled frameless context menu matching the dark theme."""

    def __init__(self, parent, items):
        super().__init__(parent)
        self.wm_overrideredirect(True)
        self.configure(bg=SEPARATOR)

        inner = tk.Frame(self, bg=CARD_BG, padx=1, pady=1)
        inner.pack(fill="both", expand=True)

        font = ("Segoe UI", 9)
        for label, command in items:
            if label == "-":
                tk.Frame(inner, bg=SEPARATOR, height=1).pack(fill="x", padx=6, pady=2)
                continue
            row = tk.Label(inner, text=label, font=font, fg=TEXT_BRIGHT,
                           bg=CARD_BG, anchor="w", padx=14, pady=5, cursor="hand2")
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.configure(bg=CARD_HOVER))
            row.bind("<Leave>", lambda e, r=row: r.configure(bg=CARD_BG))
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

_title_font = None
_desc_font = None
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


def _get_fonts():
    global _title_font, _desc_font
    if _title_font is None:
        _title_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        _desc_font = tkfont.Font(family="Segoe UI", size=8)
    return _title_font, _desc_font


def _get_markdown_fonts():
    global _bold_font, _italic_font, _header_font, _code_font, _emoji_font
    if _bold_font is None:
        _bold_font = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        _italic_font = tkfont.Font(family="Segoe UI", size=8, slant="italic")
        _header_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        _code_font = tkfont.Font(family="Consolas", size=8)
        _emoji_font = tkfont.Font(family="Segoe UI Emoji", size=9)
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

    text_widget.tag_configure("md_bold", font=bold_f)
    text_widget.tag_configure("md_italic", font=italic_f)
    text_widget.tag_configure("md_code", font=code_f, background=SEPARATOR)
    text_widget.tag_configure("md_header", font=header_f, foreground=TEXT_BRIGHT,
                              spacing1=4, spacing3=2)
    text_widget.tag_configure("md_quote", foreground=TEXT_DIM, lmargin1=10, lmargin2=10)
    text_widget.tag_configure("md_bullet", lmargin1=14, lmargin2=26)

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


class FramelessPopup(tk.Toplevel):
    """Base for frameless, dark-themed popups with a hand-drawn draggable title bar.

    Relying on Windows to theme a native title bar turned out to be unreliable for
    owned Toplevels, so drawing it ourselves guarantees the color always matches.
    """

    WIDTH, HEIGHT = 480, 420

    def __init__(self, parent, title_text):
        super().__init__(parent)
        self.overrideredirect(True)
        self.configure(bg=BG)
        self._position_over(parent)
        self.transient(parent)

        self._drag_x = 0
        self._drag_y = 0

        title_bar = tk.Frame(self, bg=STATUS_BG, height=34)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        title_lbl = tk.Label(title_bar, text=title_text, font=("Segoe UI", 9),
                             fg=TEXT_DIM, bg=STATUS_BG, anchor="w")
        title_lbl.pack(side="left", fill="x", expand=True, padx=(12, 4))

        close_btn = tk.Label(title_bar, text="✕", font=("Segoe UI", 10),
                             fg=TEXT_DIM, bg=STATUS_BG, cursor="hand2", padx=12)
        close_btn.pack(side="right", fill="y")
        close_btn.bind("<Enter>", lambda _e: close_btn.configure(bg="#e53935", fg=TEXT_BRIGHT))
        close_btn.bind("<Leave>", lambda _e: close_btn.configure(bg=STATUS_BG, fg=TEXT_DIM))
        close_btn.bind("<Button-1>", lambda _e: self.destroy())

        for w in (title_bar, title_lbl):
            w.bind("<Button-1>", self._start_move)
            w.bind("<B1-Motion>", self._on_move)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Alt-F4>", lambda _e: self.destroy())

    def _position_over(self, parent):
        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = max(0, px + (pw - self.WIDTH) // 2)
        y = max(0, py + (ph - self.HEIGHT) // 2)
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

    def _finish_show(self):
        self.lift()
        self.focus_force()


class ChangeNotesWindow(FramelessPopup):
    """Popup window showing a mod's change notes / description as rendered markdown."""

    WIDTH, HEIGHT = 480, 420

    def __init__(self, parent, mod):
        super().__init__(parent, f"{mod.get('title', 'Mod')} — Change Notes")

        tk.Label(self, text=mod.get("title", ""), font=("Segoe UI", 11, "bold"),
                 fg=TEXT_BRIGHT, bg=BG, anchor="w", wraplength=440,
                 justify="left").pack(fill="x", padx=14, pady=(14, 0))

        version = mod.get("version", "")
        if mod.get("prev_version") and version:
            version_text = f"{mod['prev_version']} → {version}"
        else:
            version_text = version
        meta = "  —  ".join(p for p in (version_text, mod.get("author", "")) if p)
        tk.Label(self, text=meta, font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG,
                 anchor="w").pack(fill="x", padx=14, pady=(2, 10))

        body = tk.Frame(self, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        text = tk.Text(body, bg=CARD_BG, fg=TEXT_DIM, font=("Segoe UI", 9),
                       wrap="word", bd=0, highlightthickness=0, cursor="arrow",
                       padx=10, pady=10, insertwidth=0, state="disabled")
        scrollbar = MiniScrollbar(body)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.set_command(text.yview)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.bind("<MouseWheel>", lambda e: text.yview_scroll(int(-e.delta / 40), "units"))

        changelog = mod.get("changelog", "").strip()
        if changelog:
            header = f"**What changed in {version}:**" if version else "**What changed:**"
            content = f"{header}\n\n{changelog}"
        else:
            content = mod.get("full_description", "").strip() or "No update notes available for this version."
        render_markdown(text, content)

        btn_bar = tk.Frame(self, bg=BG)
        btn_bar.pack(fill="x", padx=14, pady=(0, 14))
        tk.Button(
            btn_bar, text="Open on Forge", font=("Segoe UI", 9),
            bg=CARD_BG, fg=TEXT_BRIGHT, activebackground=CARD_HOVER,
            activeforeground=TEXT_BRIGHT, relief="flat", padx=10, pady=4,
            cursor="hand2", command=lambda: webbrowser.open(mod.get("link", "")),
        ).pack(side="right")

        self._finish_show()


class StatsWindow(FramelessPopup):
    """Popup showing a summary of the full mod-tracking history."""

    WIDTH, HEIGHT = 360, 480

    def __init__(self, parent, stats):
        super().__init__(parent, "Mod Tracker Stats")

        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        scrollbar = MiniScrollbar(container)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.set_command(canvas.yview)
        canvas.pack(side="left", fill="both", expand=True)
        # Scrollbar is only packed once content actually overflows (see
        # _update_scrollbar) -- an empty, non-functional strip when everything
        # already fits looks like a broken control, not a nice one.

        inner = tk.Frame(canvas, bg=BG)
        inner_id = canvas.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: self._update_scrollbar(canvas, scrollbar))
        canvas.bind("<Configure>", lambda e: (
            canvas.itemconfigure(inner_id, width=e.width),
            self._update_scrollbar(canvas, scrollbar),
        ))

        def on_wheel(e):
            canvas.yview_scroll(int(-e.delta / 40), "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        tk.Label(inner, text=f"{stats['total']:,} mods tracked", font=("Segoe UI", 13, "bold"),
                 fg=TEXT_BRIGHT, bg=BG, anchor="w").pack(fill="x", padx=16, pady=(16, 0))
        tk.Label(inner, text=f"{stats['added_this_week']} added in the last 7 days",
                 font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG, anchor="w").pack(
            fill="x", padx=16, pady=(2, 14))

        self._build_section(inner, "Top Authors (Last 30 Days)", stats["top_authors"], "mod",
                            link_ids=stats.get("author_ids"), link_fallback=stats.get("author_links"))
        self._build_section(inner, "Top Categories (Last 30 Days)", stats["top_categories"], "mod")

        self._finish_show()

    @staticmethod
    def _update_scrollbar(canvas, scrollbar):
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.update_idletasks()
        bbox = canvas.bbox("all")
        content_h = (bbox[3] - bbox[1]) if bbox else 0
        if content_h > canvas.winfo_height():
            if not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y")
                scrollbar.update_idletasks()
        elif scrollbar.winfo_ismapped():
            scrollbar.pack_forget()

    def _build_section(self, parent, heading, entries, unit, link_ids=None, link_fallback=None):
        tk.Label(parent, text=heading.upper(), font=("Segoe UI", 8, "bold"),
                 fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", padx=16, pady=(6, 2))

        section = tk.Frame(parent, bg=CARD_BG)
        section.pack(fill="x", padx=16, pady=(0, 4))

        if not entries:
            tk.Label(section, text="Not enough data yet", font=("Segoe UI", 9),
                     fg=TEXT_DIM, bg=CARD_BG, anchor="w").pack(fill="x", padx=10, pady=8)
            return

        for i, (name, count) in enumerate(entries):
            row = tk.Frame(section, bg=CARD_BG)
            row.pack(fill="x", padx=10, pady=4)

            author_id = link_ids.get(name) if link_ids else None
            fallback_link = link_fallback.get(name) if link_fallback else None
            name_lbl = tk.Label(row, text=f"{i + 1}. {name}", font=("Segoe UI", 9),
                                fg=TEXT_BRIGHT, bg=CARD_BG, anchor="w")
            name_lbl.pack(side="left")
            if author_id or fallback_link:
                name_lbl.configure(cursor="hand2")
                name_lbl.bind("<Button-1>",
                              lambda _e, n=name, aid=author_id, link=fallback_link:
                              self._open_author(n, aid, link))
                name_lbl.bind("<Enter>", lambda _e, w=name_lbl: w.configure(fg=ACCENT_UPD))
                name_lbl.bind("<Leave>", lambda _e, w=name_lbl: w.configure(fg=TEXT_BRIGHT))

            plural = "" if count == 1 else "s"
            tk.Label(row, text=f"{count} {unit}{plural}", font=("Segoe UI", 9),
                     fg=TEXT_DIM, bg=CARD_BG, anchor="e").pack(side="right")

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


def _parse_ts(ts_str):
    """Parse an ISO or RFC 2822 timestamp (RSS vs API formats) into an aware datetime."""
    if not ts_str:
        return None
    try:
        try:
            dt = parsedate_to_datetime(ts_str)
        except Exception:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _relative_time(ts_str):
    """Convert an ISO or RFC 2822 timestamp to a relative string."""
    dt = _parse_ts(ts_str)
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
    dt = _parse_ts(author_since)
    if dt is None:
        return False
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    return age_days <= NEW_AUTHOR_DAYS


def _draw_notes_icon(canvas, color):
    canvas.delete("all")
    canvas.create_rectangle(2, 1, 11, 13, outline=color, width=1)
    canvas.create_line(4, 4, 9, 4, fill=color)
    canvas.create_line(4, 7, 9, 7, fill=color)
    canvas.create_line(4, 10, 7, 10, fill=color)


class ModCard(tk.Frame):
    def __init__(self, parent, mod, accent, photo):
        super().__init__(parent, bg=CARD_BG, padx=6, pady=4,
                         highlightbackground=accent, highlightthickness=2)
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
        if mod.get("prev_version") and mod.get("version"):
            full_title += f"  {mod['prev_version']} → {mod['version']}"
        elif mod.get("version"):
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

        # NEW badge + timestamp row
        meta_frame = tk.Frame(self, bg=CARD_BG)
        meta_frame.grid(row=2, column=1, sticky="w", pady=(1, 0))
        self._widgets.append(meta_frame)

        if mod.get("is_fresh"):
            new_badge = tk.Label(meta_frame, text=" NEW ", font=("Segoe UI", 7, "bold"),
                                 fg=CARD_BG, bg=ACCENT_NEW, cursor="hand2")
            new_badge.pack(side="left", padx=(0, 5))
            self._widgets.append(new_badge)

        if _is_new_author(mod.get("author_since", "")):
            author_badge = tk.Label(meta_frame, text=" NEW AUTHOR ", font=("Segoe UI", 7, "bold"),
                                    fg=TEXT_BRIGHT, bg=ACCENT_NEW_AUTHOR, cursor="hand2")
            author_badge.pack(side="left", padx=(0, 5))
            self._widgets.append(author_badge)

        ts = _relative_time(mod.get("updated", ""))
        if ts:
            ts_lbl = tk.Label(meta_frame, text=ts, font=("Segoe UI", 7),
                              fg=TEXT_DIM, bg=CARD_BG, cursor="hand2")
            ts_lbl.pack(side="left")
            self._widgets.append(ts_lbl)

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

        for w in [self, img_lbl, c_title, c_desc] + list(meta_frame.winfo_children()):
            w.bind("<Button-1>", self._click)
            w.bind("<Button-3>", self._right_click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
        meta_frame.bind("<Button-1>", self._click)
        meta_frame.bind("<Button-3>", self._right_click)
        meta_frame.bind("<Enter>", self._enter)
        meta_frame.bind("<Leave>", self._leave)

        if mod.get("changelog"):
            notes_icon = tk.Canvas(meta_frame, width=14, height=14, bg=CARD_BG,
                                   highlightthickness=0, cursor="hand2")
            _draw_notes_icon(notes_icon, TEXT_DIM)
            notes_icon.pack(side="left", padx=(6, 0))
            self._widgets.append(notes_icon)
            notes_icon.bind("<Enter>", lambda e: (self._enter(e), _draw_notes_icon(notes_icon, TEXT_BRIGHT)))
            notes_icon.bind("<Leave>", lambda e: (self._leave(e), _draw_notes_icon(notes_icon, TEXT_DIM)))
            notes_icon.bind("<Button-1>", lambda _e: self._show_change_notes())

    def _click(self, _e):
        webbrowser.open(self._mod["link"])

    def _right_click(self, e):
        items = [
            ("Open on Forge", lambda: webbrowser.open(self._mod["link"])),
            ("-", None),
            ("Copy Link", self._copy_link),
        ]
        if self._mod.get("changelog"):
            items.insert(0, ("View Change Notes", self._show_change_notes))
            items.insert(1, ("-", None))
        menu = ContextMenu(self, items)
        menu.show(e.x_root, e.y_root)

    def _show_change_notes(self):
        ChangeNotesWindow(self.winfo_toplevel(), self._mod)

    def _copy_link(self):
        self.clipboard_clear()
        self.clipboard_append(self._mod["link"])

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


def _round_rect_points(x1, y1, x2, y2, r):
    if x2 - x1 < r * 2:
        r = max(0, (x2 - x1) // 2)
    return [
        x1 + r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]


class MiniScrollbar(tk.Canvas):
    """Minimal dark-themed vertical scrollbar for pairing with a Text widget."""

    def __init__(self, parent, **kw):
        kw.setdefault("bg", CARD_BG)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("width", 8)
        super().__init__(parent, **kw)
        self._first = 0.0
        self._last = 1.0
        self._command = None
        self._drag_start_y = None
        self._drag_start_first = None

        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_command(self, command):
        self._command = command

    def set(self, first, last):
        self._first = float(first)
        self._last = float(last)
        self._draw()

    def _thumb_coords(self):
        h = self.winfo_height()
        if h < 2:
            return None
        y0 = self._first * h
        y1 = self._last * h
        min_h = 20
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
        pad = 2
        r = max(1, (w - pad * 2) // 2)
        self.create_polygon(
            _round_rect_points(pad, y0 + 1, w - pad, y1 - 1, r),
            smooth=True, fill=SEPARATOR, outline="",
        )

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
