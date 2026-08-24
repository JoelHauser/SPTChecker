import re
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime

import pystray
from PIL import Image, ImageTk

from .config import (
    ACCENT_DANGER, ACCENT_NEW, ACCENT_UPD, APP_VERSION, BG, BORDER, CARD_BG,
    CATEGORY_COLOR_DEFAULT, CATEGORY_COLORS,
    CHECK_INTERVAL_MINUTES,
    DISPLAY_FIELDS, FORGE_MOD_PAGE, FORGE_URL, LAYOUT_VERSION, MAX_PER_CATEGORY,
    SEPARATOR, STATE_FIELDS, STATUS_BG, TEXT, TEXT_BRIGHT, TEXT_DIM, TEXT_FAINT,
    UPDATE_CHECK_INTERVAL_HOURS,
    WINDOW_DEFAULT_GEOMETRY, WINDOW_DEFAULT_WIDTH, WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from .feed import ForgeBlocked, fetch_feeds, unpublished_links
from .localmods import detect_spt_version, scan_installed_mods
from .matcher import match_local_mods
from .platform import (
    badge_icon, disable_show_animation, is_startup_enabled, load_app_icon,
    refresh_startup_if_stale, send_toast, set_dark_title_bar, set_dpi_aware,
    set_startup_enabled,
)
from .state import (
    compute_stats, download_thumb, load_state, placeholder_thumb, purge_old_thumbs, save_state,
)
from .theme import (
    ToggleSwitch, chip, dot, flat_button, font, info_glyph, ring, rounded_photo,
)
from .update import check_for_update
from .widgets import (
    CARD_GAP, LocalScanSettingsWindow, ModCard, StatsWindow, build_scroll_area,
    card_pitch,
)

# Layout constants shared between the widgets that use them and _size_to_fit,
# which has to reproduce the same spacing to work out how tall the window needs
# to be for a full column.
BODY_PAD_X = 14
BODY_PAD_TOP = 12
COL_HEADER_GAP = 8


class SPTCheckerApp:
    def __init__(self, start_hidden=False):
        self._start_hidden = start_hidden
        self.state = load_state()

        set_dpi_aware()
        self.root = tk.Tk()
        self.root.title("SPTChecker")
        self.root.configure(bg=BG)
        saved_geometry = self._load_geometry()
        self.root.geometry(saved_geometry or WINDOW_DEFAULT_GEOMETRY)
        self.root.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        set_dark_title_bar(self.root, show=not start_hidden)
        disable_show_animation(self.root)

        self._app_icon = load_app_icon()
        self._icon_photo = ImageTk.PhotoImage(self._app_icon.resize((32, 32), Image.LANCZOS))
        self.root.iconphoto(True, self._icon_photo)

        self._photos = {}  # frame -> PhotoImage refs for its current cards
        self._checking = False
        self._scanning = False
        self._local_scan_window = None
        self._next_check_ts = None
        self._timer_after_id = None
        self._new_sig = None
        self._upd_sig = None
        self._tray = None
        self._unread_count = 0
        self._visible = not start_hidden
        startup_on = is_startup_enabled()
        self._startup_var = tk.BooleanVar(value=startup_on)
        try:
            refresh_startup_if_stale()
        except OSError:
            pass

        self._build_ui()
        # Applied on every launch, not just the first: the header's real
        # requirement depends on the display scaling of whichever machine this
        # is running on now, which a size saved elsewhere knows nothing about.
        self.root.minsize(self._min_width(), WINDOW_MIN_HEIGHT)
        if not saved_geometry:
            # Only on a first launch (or the first after a layout change):
            # a size the user chose themselves is never overridden.
            self._size_to_fit()
        self._setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

        self.root.after(400, self._check_now)
        # Local mod scanning never runs on its own, even with the feature
        # enabled and a folder already set -- only an explicit click on
        # Scan Now (in _show_local_scan) starts one.

    # ── Window geometry ─────────────────────────────────────────────────

    def _load_geometry(self):
        """The saved window size, or None when there isn't a usable one.

        A geometry saved under a different LAYOUT_VERSION is discarded: it was
        chosen to fit cards of a different height, and restoring it is exactly
        what would leave a returning user looking at a window that scrolls on
        the first launch after an update. They get one re-fit, then their own
        sizing is respected again.
        """
        if self.state.get("layout_version") != LAYOUT_VERSION:
            return None
        m = re.fullmatch(r"(\d+)x(\d+)", self.state.get("window_geometry", ""))
        if not m:
            return None
        w = max(WINDOW_MIN_WIDTH, int(m.group(1)))
        h = max(WINDOW_MIN_HEIGHT, int(m.group(2)))
        return f"{w}x{h}"

    def _min_width(self):
        """Narrowest the window can be before the header controls collide.

        Measured rather than fixed: the header is laid out by its fonts, so it
        needs 596px at 100% display scaling and 746px at 200% -- a constant
        that looks generous on one machine clips the buttons on another.
        """
        self.root.update_idletasks()
        return max(WINDOW_MIN_WIDTH, self._header_bar.winfo_reqwidth() + 8)

    def _size_to_fit(self):
        """Size the window so a full column of cards fits without scrolling.

        Height is measured from the real widgets rather than assumed from
        constants: the header, the column headings and the status bar are all
        sized by their fonts, so their heights change with the Windows display
        scaling setting. A hardcoded default that fits seven cards at 100%
        clips them at 125%, which is the more common setting on laptops.

        Width stays deliberately tight -- the two columns are the content, and
        extra width past the point where titles stop being ellipsized just
        stretches the cards.
        """
        chrome = (self._header_bar.winfo_reqheight()
                  + self._status_bar.winfo_reqheight()
                  + self._col_header.winfo_reqheight() + COL_HEADER_GAP
                  + BODY_PAD_TOP)
        wanted_h = chrome + MAX_PER_CATEGORY * card_pitch(self.root)
        # Not winfo_width(): with --background the window is never deiconified,
        # and an unmapped window reports a width of 1.
        wanted_w = max(WINDOW_DEFAULT_WIDTH, self._min_width())

        # Never open larger than the display. If the screen genuinely cannot
        # show every card the columns scroll, which is what that fallback is
        # there for -- but nothing is gained by opening off the bottom edge.
        max_h = int(self.root.winfo_screenheight() * 0.90)
        max_w = int(self.root.winfo_screenwidth() * 0.95)
        self.root.geometry(f"{min(wanted_w, max_w)}x{min(wanted_h, max_h)}")

    def _save_geometry(self):
        if not self._visible:
            return
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        if w > 1 and h > 1:
            self.state["window_geometry"] = f"{w}x{h}"
            self.state["layout_version"] = LAYOUT_VERSION
            save_state(self.state)

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_body()
        self._build_status_bar()

    # -- Header ---------------------------------------------------------

    def _build_header(self):
        """A full-width app bar in the chrome color rather than a strip of
        loose controls floating on the window background: it gives the window
        a top edge, and it is what separates the app's own controls from the
        mod list they act on."""
        bar = self._header_bar = tk.Frame(self.root, bg=STATUS_BG)
        bar.pack(fill="x", side="top")
        hdr = tk.Frame(bar, bg=STATUS_BG, pady=9)
        hdr.pack(fill="x", padx=14)
        tk.Frame(bar, bg=SEPARATOR, height=1).pack(fill="x")

        brand_icon = ImageTk.PhotoImage(
            rounded_photo(self._app_icon.resize((22, 22), Image.LANCZOS), radius=5))
        self._brand_icon = brand_icon
        tk.Label(hdr, image=brand_icon, bg=STATUS_BG).pack(side="left")
        tk.Label(hdr, text="SPTChecker", font=font(11, "bold"), fg=TEXT_BRIGHT,
                 bg=STATUS_BG).pack(side="left", padx=(9, 0))
        tk.Label(hdr, text=f"v{APP_VERSION}", font=font(8), fg=TEXT_FAINT,
                 bg=STATUS_BG).pack(side="left", padx=(7, 0), pady=(3, 0))

        # Packed right to left, so the primary action anchors the far corner.
        self._btn = flat_button(hdr, "Check Now", self._check_now, accent=ACCENT_NEW,
                                bg=STATUS_BG, padx=14, pady=5)
        self._btn.pack(side="right")
        self._tooltip_id = None
        self._tooltip_win = None
        self._bind_tooltip(self._btn, "Check the Forge for new or updated mods")

        flat_button(hdr, "Local Mods", self._show_local_scan,
                    bg=STATUS_BG).pack(side="right", padx=(0, 8))
        flat_button(hdr, "Stats", self._show_stats,
                    bg=STATUS_BG).pack(side="right", padx=(0, 8))

        ToggleSwitch(hdr, "Run on startup", self._startup_var,
                     command=self._toggle_startup, font_size=8,
                     bg=STATUS_BG).pack(side="right", padx=(0, 16))

        self._legend_icon_dim = info_glyph(15, TEXT_FAINT)
        self._legend_icon_bright = info_glyph(15, TEXT_BRIGHT)
        self._legend_icon = tk.Label(hdr, image=self._legend_icon_dim, bg=STATUS_BG,
                                     cursor="hand2")
        self._legend_icon.pack(side="right", padx=(0, 16))
        self._legend_icon.bind("<Enter>", self._legend_enter)
        self._legend_icon.bind("<Leave>", self._legend_leave)

    # -- Columns --------------------------------------------------------

    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=BODY_PAD_X, pady=(BODY_PAD_TOP, 0))
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(2, weight=1, uniform="col")
        body.rowconfigure(1, weight=1)

        self._new_count, self._col_header = self._column_header(
            body, 0, "New Mods", ACCENT_NEW)
        self._upd_count, _ = self._column_header(body, 2, "Updated Mods", ACCENT_UPD)

        # A plain gutter instead of the divider rule that used to sit here: two
        # columns of outlined cards already read as two columns.
        tk.Frame(body, bg=BG, width=BODY_PAD_X).grid(
            row=0, column=1, rowspan=2, sticky="ns")

        new_col = tk.Frame(body, bg=BG)
        new_col.grid(row=1, column=0, sticky="nsew")
        self._new_frame = build_scroll_area(new_col)

        upd_col = tk.Frame(body, bg=BG)
        upd_col.grid(row=1, column=2, sticky="nsew")
        self._upd_frame = build_scroll_area(upd_col)

        self._set_placeholder(self._new_frame, "Checking the Forge…")
        self._set_placeholder(self._upd_frame, "Checking the Forge…")

    def _column_header(self, parent, column, text, color):
        """Returns (count label, row). _apply keeps the count in step with the
        column; the row is measured by _size_to_fit."""
        row = tk.Frame(parent, bg=BG)
        row.grid(row=0, column=column, sticky="ew", pady=(0, COL_HEADER_GAP))
        swatch = dot(8, color)
        lbl = tk.Label(row, image=swatch, bg=BG)
        lbl._swatch = swatch
        lbl.pack(side="left", padx=(0, 8))
        tk.Label(row, text=text.upper(), font=font(9, "bold"), fg=color,
                 bg=BG).pack(side="left")
        count = tk.Label(row, text="", font=font(9, "bold"), fg=TEXT_FAINT, bg=BG)
        count.pack(side="left", padx=(8, 0))
        tk.Frame(row, bg=SEPARATOR, height=1).pack(
            side="left", fill="x", expand=True, padx=(12, 0), pady=(1, 0))
        return count, row

    # -- Status bar -----------------------------------------------------

    def _build_status_bar(self):
        bar = self._status_bar = tk.Frame(self.root, bg=STATUS_BG)
        bar.pack(fill="x", side="bottom")
        tk.Frame(bar, bg=SEPARATOR, height=1).pack(fill="x", side="top")
        inner = tk.Frame(bar, bg=STATUS_BG, pady=7)
        inner.pack(fill="x", padx=14)

        self._forge_dot_ok = dot(7, ACCENT_NEW)
        self._forge_dot_bad = dot(7, ACCENT_DANGER)
        self._forge_dot_idle = dot(7, TEXT_FAINT)
        self._forge_dot = tk.Label(inner, image=self._forge_dot_idle, bg=STATUS_BG)
        self._forge_dot.pack(side="left", padx=(0, 8))
        self._bind_tooltip(
            self._forge_dot,
            "Green: last check reached the Forge OK\nRed: last check failed (retrying)",
        )

        self._lbl_status = tk.Label(inner, text="Starting…", font=font(8),
                                    fg=TEXT_DIM, bg=STATUS_BG)
        self._lbl_status.pack(side="left")

        self._lbl_timer = tk.Label(inner, text="", font=font(8),
                                   fg=TEXT_FAINT, bg=STATUS_BG)
        self._lbl_timer.pack(side="right")

        # Left unbuilt -- the update chip only appears once a newer release is
        # actually found, so the bar stays quiet for anyone already current.
        self._status_inner = inner
        self._update_url = FORGE_MOD_PAGE
        self._update_lbl = None

    @staticmethod
    def _set_placeholder(frame, text):
        """The empty/waiting state for a column. A ring glyph above the line of
        text, because a lone sentence of dim gray in an otherwise blank column
        reads as a label that failed to load rather than as "nothing here"."""
        for w in frame.winfo_children():
            w.destroy()
        holder = tk.Frame(frame, bg=BG)
        holder.pack(fill="x", pady=(54, 0))
        glyph = ring(26, SEPARATOR, width=2)
        lbl = tk.Label(holder, image=glyph, bg=BG)
        lbl._glyph = glyph
        lbl.pack()
        tk.Label(holder, text=text, font=font(9), fg=TEXT_FAINT,
                 bg=BG, justify="center", wraplength=250).pack(pady=(12, 0))

    # ── Tooltip ─────────────────────────────────────────────────────────

    def _popup_shell(self, widget, pad_x=10, pad_y=7):
        """A bordered dark panel anchored under `widget`, used for both the
        hover tooltips and the category legend. The 1px outer frame is doing
        real work: an unbordered dark popup over a dark window has no edge, so
        it reads as text spilling onto the page rather than as a panel."""
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 6
        tw = tk.Toplevel(self.root)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=BORDER)
        inner = tk.Frame(tw, bg=CARD_BG, padx=pad_x, pady=pad_y)
        inner.pack(padx=1, pady=1)
        return tw, inner

    def _bind_tooltip(self, widget, text):
        widget.bind("<Enter>", lambda _e: self._tooltip_hover_start(widget, text))
        widget.bind("<Leave>", self._tooltip_hover_end)

    def _tooltip_hover_start(self, widget, text):
        self._tooltip_id = self.root.after(700, lambda: self._show_tooltip(widget, text))

    def _tooltip_hover_end(self, _e=None):
        if self._tooltip_id:
            self.root.after_cancel(self._tooltip_id)
            self._tooltip_id = None
        if self._tooltip_win:
            self._tooltip_win.destroy()
            self._tooltip_win = None

    def _show_tooltip(self, widget, text):
        self._tooltip_id = None
        tw, inner = self._popup_shell(widget)
        tk.Label(inner, text=text, font=font(8), fg=TEXT, bg=CARD_BG,
                 justify="left").pack()
        self._tooltip_win = tw

    def _legend_enter(self, _e):
        self._legend_icon.configure(image=self._legend_icon_bright)
        self._tooltip_id = self.root.after(400, self._show_legend)

    def _legend_leave(self, _e=None):
        self._legend_icon.configure(image=self._legend_icon_dim)
        self._tooltip_hover_end()

    def _show_legend(self):
        self._tooltip_id = None
        tw, inner = self._popup_shell(self._legend_icon, pad_x=14, pad_y=12)
        tk.Label(inner, text="CARD BORDER = CATEGORY", font=font(8, "bold"),
                 fg=TEXT_FAINT, bg=CARD_BG, anchor="w").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 9))

        tw._dot_photos = []  # keep PhotoImage refs alive for this popup's lifetime
        cols = 2
        entries = list(CATEGORY_COLORS.items()) + [("Other / uncategorized",
                                                    CATEGORY_COLOR_DEFAULT)]
        for i, (category, color) in enumerate(entries):
            row, col = i // cols + 1, (i % cols) * 2
            swatch = dot(9, color)
            tw._dot_photos.append(swatch)
            tk.Label(inner, image=swatch, bg=CARD_BG).grid(
                row=row, column=col, sticky="w", padx=(0, 8), pady=3)
            tk.Label(inner, text=category, font=font(8), fg=TEXT, bg=CARD_BG,
                     anchor="w").grid(row=row, column=col + 1, sticky="w",
                                      padx=(0, 18), pady=3)

        self._tooltip_win = tw

    # ── Settings ───────────────────────────────────────────────────────

    def _toggle_startup(self):
        try:
            set_startup_enabled(self._startup_var.get())
        except OSError:
            self._startup_var.set(not self._startup_var.get())

    def _show_stats(self):
        stats = compute_stats(self.state.get("mods", {}))
        StatsWindow(self.root, stats)

    # ── Local mod scan (opt-in) ───────────────────────────────────────

    def _show_local_scan(self):
        if self._local_scan_window and self._local_scan_window.winfo_exists():
            self._local_scan_window.resurface()
            return
        self._local_scan_window = LocalScanSettingsWindow(
            self.root,
            enabled=self.state.get("local_scan_enabled", False),
            spt_path=self.state.get("spt_install_path", ""),
            on_toggle=self._toggle_local_scan,
            on_path_change=self._set_local_scan_path,
            on_scan_now=self._scan_local_now,
            on_endorse=self._mark_endorsed,
            endorsed=self.state.get("endorsed", []),
        )
        if self._scanning:
            # A scan is already running (e.g. the startup auto-scan) --
            # reflect that instead of showing an idle Scan Now state.
            self._local_scan_window.set_scanning()
        else:
            cached = self.state.get("local_scan_results")
            if cached:
                self._local_scan_window.set_results(cached)

    def _mark_endorsed(self, link):
        """Remember that this mod was opened to be endorsed.

        A local note only: the Forge API is read-only, so the app has no way to
        read back whether the endorsement actually happened. It exists so a mod
        already dealt with looks different from one still waiting, not as a
        claim about the Forge's own records.
        """
        endorsed = self.state.setdefault("endorsed", [])
        if link not in endorsed:
            endorsed.append(link)
            save_state(self.state)

    def _toggle_local_scan(self, enabled):
        self.state["local_scan_enabled"] = enabled
        save_state(self.state)

    def _set_local_scan_path(self, path):
        self.state["spt_install_path"] = path
        save_state(self.state)

    def _scan_local_now(self):
        if self._scanning:
            return
        self._scanning = True
        threading.Thread(target=self._bg_scan_local, daemon=True).start()

    def _bg_scan_local(self):
        try:
            spt_path = self.state.get("spt_install_path", "")
            local_mods = scan_installed_mods(spt_path)
            results = match_local_mods(
                local_mods,
                spt_version=detect_spt_version(spt_path),
                on_progress=lambda done, total: self.root.after(
                    0, self._update_scan_progress, done, total),
            )
            for r in results:
                if r["update_available"]:
                    # May be None -- the widget falls back to its shared
                    # placeholder, no need to render one per mod here.
                    r["_pil"] = download_thumb(r["forge"].get("thumb_url"))
            # _pil (a PIL Image) isn't JSON-serializable -- persist a stripped
            # copy, and do the JSON write here on the worker thread so the UI
            # callback only touches widgets.
            self.state["local_scan_results"] = [
                {k: v for k, v in r.items() if k != "_pil"} for r in results
            ]
            save_state(self.state)
            self.root.after(0, self._apply_local_scan, results)
        except Exception as exc:
            self.root.after(0, self._on_local_scan_error, str(exc))

    def _apply_local_scan(self, results):
        self._scanning = False
        if self._local_scan_window and self._local_scan_window.winfo_exists():
            self._local_scan_window.set_results(results)

        updates = [r for r in results if r["update_available"]]
        if updates:
            self._send_list_toast(
                f"{len(updates)} Installed Mod{'s' if len(updates) != 1 else ''} Can Be Updated",
                [f"{r['forge']['title']} {r['current_version']} → {r['available_version']}"
                 for r in updates],
                [r["forge"]["link"] for r in updates],
            )

    def _on_local_scan_error(self, msg):
        self._scanning = False
        if self._local_scan_window and self._local_scan_window.winfo_exists():
            self._local_scan_window.set_error(msg)

    def _update_scan_progress(self, done, total):
        if self._local_scan_window and self._local_scan_window.winfo_exists():
            self._local_scan_window.set_progress(done, total)

    # ── System tray ────────────────────────────────────────────────────

    def _setup_tray(self):
        self._tray_icon_normal = self._app_icon.resize((64, 64), Image.LANCZOS)
        self._tray_icon_unread = badge_icon(self._tray_icon_normal)
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Check Now", self._tray_check),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray = pystray.Icon(
            "SPTModChecker", self._tray_icon_normal, "SPTChecker", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _hide_to_tray(self):
        self._save_geometry()
        self._visible = False
        self.root.withdraw()

    def _tray_show(self, _icon=None, _item=None):
        self.root.after(0, self._do_show)

    def _do_show(self):
        self._visible = True
        # Windows reveals the window the instant deiconify() runs, before Tk
        # has actually painted every widget -- update_idletasks() alone isn't
        # enough to beat that, since the reveal doesn't wait on paint
        # completion. Staying fully transparent until a full update() forces
        # every widget to finish painting means there's nothing left to
        # progressively fill in once we make it visible.
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.root.state("normal")
        self.root.update()
        self.root.attributes("-alpha", 1.0)
        self.root.lift()
        self.root.focus_force()
        self._clear_unread()
        if self._next_check_ts:
            self._tick_timer()

    def _tray_check(self, _icon=None, _item=None):
        self.root.after(0, self._check_now)

    def _tray_quit(self, _icon=None, _item=None):
        self._save_geometry()
        if self._tray:
            self._tray.stop()
        self.root.after(0, self.root.destroy)

    def _clear_unread(self):
        self._unread_count = 0
        if self._tray:
            self._tray.icon = self._tray_icon_normal
            self._tray.title = "SPTChecker"

    # ── Self-update check ──────────────────────────────────────────────

    def _schedule_update_check(self, delay_ms=3000):
        """Look for a newer release of the app itself, then re-arm.

        Deliberately off the mod-check cycle and on its own long timer:
        releases are weeks apart, so tying this to the 15-minute poll would
        be hundreds of requests a day to learn nothing. The first run is
        delayed a few seconds so it never competes with the startup check
        for the window's attention.
        """
        self.root.after(delay_ms, lambda: threading.Thread(
            target=self._bg_update_check, daemon=True).start())

    def _bg_update_check(self):
        # Never raises: check_for_update swallows every failure and returns
        # None, since not knowing whether an update exists is not worth
        # bothering anyone about.
        found = check_for_update()
        if found:
            self.root.after(0, self._show_update_available,
                            found["version"], found["url"])
        self.root.after(0, self._schedule_update_check,
                        UPDATE_CHECK_INTERVAL_HOURS * 3600 * 1000)

    def _show_update_available(self, version, url=FORGE_MOD_PAGE):
        """Surface the newer release in the status bar as a chip -- the one
        thing in that bar worth clicking, so it should not read as another line
        of status text. Rebuilt rather than reconfigured because the chip's
        pill is rendered to fit its label, and the version only changes on the
        rare occasion a newer release actually appears."""
        self._update_url = url
        if self._update_lbl is not None:
            self._update_lbl.destroy()
        self._update_lbl = chip(self._status_inner, f"↑  v{version} available",
                                ACCENT_NEW, surface=STATUS_BG, font_size=8)
        self._update_lbl.configure(cursor="hand2")
        self._update_lbl.pack(side="right", padx=(0, 16))
        self._update_lbl.bind("<Button-1>", lambda _e: webbrowser.open(self._update_url))
        self._bind_tooltip(
            self._update_lbl,
            f"SPTChecker v{version} is on the Forge.\nClick to open its page.")

    # ── Check logic ────────────────────────────────────────────────────

    def _check_now(self):
        if self._checking:
            return
        self._checking = True
        self._btn.configure(state="disabled", text="Checking…")
        self._lbl_status.configure(text="Fetching mods…")
        threading.Thread(target=self._bg_check, daemon=True).start()

    @staticmethod
    def _strip_for_state(mods):
        return [{k: m[k] for k in DISPLAY_FIELDS if k in m} for m in mods]

    def _bg_check(self):
        try:
            newest, updated = fetch_feeds()
            known = self.state.get("mods", {})
            first_run = len(known) == 0
            prev_versions = {link: m.get("version", "") for link, m in known.items()}

            for mod in newest + updated:
                known[mod["link"]] = {k: mod[k] for k in STATE_FIELDS if k in mod}
            self.state["mods"] = known
            self.state["last_check"] = datetime.now().isoformat()

            prev_new = self.state.get("display_new", [])
            prev_upd = self.state.get("display_updated", [])

            display_new = newest[:MAX_PER_CATEGORY]
            display_upd = updated[:MAX_PER_CATEGORY]

            # One batched lookup covering both columns, rather than a request
            # per mod -- see unpublished_links().
            gone = unpublished_links([m["link"] for m in display_new + display_upd])
            display_new = [m for m in display_new if m["link"] not in gone]
            display_upd = [m for m in display_upd if m["link"] not in gone]

            for mod in display_upd:
                old_version = prev_versions.get(mod["link"], "")
                if old_version and old_version != mod.get("version", ""):
                    mod["prev_version"] = old_version

            self.state["display_new"] = self._strip_for_state(display_new)
            self.state["display_updated"] = self._strip_for_state(display_upd)
            save_state(self.state)

            purge_old_thumbs()

            for mod in display_new + display_upd:
                pil = download_thumb(mod.get("thumb_url"))
                mod["_pil"] = pil if pil else placeholder_thumb()

            prev_new_links = {m["link"] for m in prev_new}
            prev_upd_links = {m["link"] for m in prev_upd}
            notify_new = [m for m in display_new if m["link"] not in prev_new_links] if not first_run else []
            notify_upd = [m for m in display_upd if m["link"] not in prev_upd_links] if not first_run else []

            # Mark fresh mods for NEW badge
            fresh_new_links = {m["link"] for m in notify_new}
            fresh_upd_links = {m["link"] for m in notify_upd}
            for m in display_new:
                m["is_fresh"] = m["link"] in fresh_new_links
            for m in display_upd:
                m["is_fresh"] = m["link"] in fresh_upd_links

            if not first_run:
                self._send_notifications(notify_new, notify_upd)

            self.root.after(0, self._apply, display_new, display_upd, first_run,
                            len(notify_new), len(notify_upd))
        except ForgeBlocked as exc:
            self.root.after(0, self._on_error, str(exc), "")
        except Exception as exc:
            self.root.after(0, self._on_error, str(exc))

    @staticmethod
    def _send_list_toast(title, lines, links):
        """Common toast shape for a list of mods: up to 3 detail lines plus
        an 'and N more…' tail, clicking through to the mod's own page when
        there's exactly one, or the Forge listing otherwise."""
        shown = lines[:3]
        if len(lines) > 3:
            shown.append(f"and {len(lines) - 3} more…")
        url = links[0] if len(links) == 1 else FORGE_URL
        send_toast(title, "\n".join(shown), launch_url=url)

    def _send_notifications(self, new_mods, updated_mods):
        if new_mods:
            self._send_list_toast(
                f"{len(new_mods)} New SPT Mod{'s' if len(new_mods) != 1 else ''}",
                [f"{m['title']} {m['version']} by {m['author']}" for m in new_mods],
                [m["link"] for m in new_mods],
            )

        if updated_mods:
            def line(m):
                version_str = (f"{m['prev_version']} → {m.get('version', '')}"
                               if m.get("prev_version") else m.get("version", ""))
                return f"{m['title']} {version_str} by {m.get('author', '')}"
            self._send_list_toast(
                f"{len(updated_mods)} SPT Mod{'s' if len(updated_mods) != 1 else ''} Updated",
                [line(m) for m in updated_mods],
                [m["link"] for m in updated_mods],
            )

    def _apply(self, display_new, display_upd, first_run, n_fresh_new=0, n_fresh_upd=0):
        self._checking = False
        self._btn.configure(state="normal", text="Check Now")
        self._forge_dot.configure(image=self._forge_dot_ok)
        self._lbl_status.configure(fg=TEXT_DIM)
        now = datetime.now().strftime("%H:%M")
        total = len(self.state.get("mods", {}))

        self._new_sig = self._render_column(
            self._new_frame, display_new, self._new_sig, first_run,
            "Baseline set.\nWatching for new mods…", "Nothing new since the last check.")
        self._upd_sig = self._render_column(
            self._upd_frame, display_upd, self._upd_sig, first_run,
            "Baseline set.\nWatching for updates…", "No updates since the last check.")
        self._new_count.configure(text=str(len(display_new)) if display_new else "")
        self._upd_count.configure(text=str(len(display_upd)) if display_upd else "")

        if first_run:
            self._lbl_status.configure(text=f"Baseline set — {total:,} mods cataloged at {now}")
        elif n_fresh_new or n_fresh_upd:
            self._lbl_status.configure(
                text=f"{n_fresh_new} new, {n_fresh_upd} updated at {now}"
                     f"   ·   tracking {total:,} mods"
            )
        else:
            self._lbl_status.configure(
                text=f"No changes at {now}   ·   tracking {total:,} mods")

        if not self._visible and not first_run:
            self._unread_count += n_fresh_new + n_fresh_upd

        if self._tray:
            if self._unread_count:
                self._tray.icon = self._tray_icon_unread
                self._tray.title = f"SPTChecker — {self._unread_count} unread"
            else:
                self._tray.icon = self._tray_icon_normal
                self._tray.title = "SPTChecker — no changes"

        self._schedule_next()

    def _on_error(self, msg, prefix="Error: "):
        self._checking = False
        self._btn.configure(state="normal", text="Check Now")
        self._forge_dot.configure(image=self._forge_dot_bad)
        # Colored to match the status dot: a failure reported in the same dim
        # gray as a successful check is a failure nobody notices.
        self._lbl_status.configure(text=f"{prefix}{msg}", fg=ACCENT_DANGER)
        # A failed check leaves both columns empty on a cold start, since
        # nothing has rendered yet -- fall back to the last results saved to
        # state so the window still shows the most recent known mods (stale,
        # but far better than blank) alongside the reason it couldn't refresh.
        self._show_cached_results()
        self._next_check_ts = time.time() + 300
        self._tick_timer()

    def _show_cached_results(self):
        """Render the last successfully-fetched mods from saved state.

        Only fills genuinely empty columns: once a check has rendered live
        results this session, those are newer than anything on disk and must
        not be replaced by them.
        """
        if self._new_sig is not None or self._upd_sig is not None:
            return
        cached_new = self.state.get("display_new", [])
        cached_upd = self.state.get("display_updated", [])
        if not cached_new and not cached_upd:
            return
        for mod in cached_new + cached_upd:
            # Thumbnails come from the on-disk cache; a miss can't be fetched
            # while the site is unreachable, so it falls back to a placeholder.
            pil = download_thumb(mod.get("thumb_url"))
            mod["_pil"] = pil if pil else placeholder_thumb()
            mod["is_fresh"] = False
        self._new_sig = self._render_column(
            self._new_frame, cached_new, self._new_sig, False,
            "", "No new mods detected yet.")
        self._upd_sig = self._render_column(
            self._upd_frame, cached_upd, self._upd_sig, False,
            "", "No updates detected yet.")

    def _render_column(self, frame, mods, prev_sig, first_run, baseline_text, empty_text):
        """Render one column, returning its new content signature. When the
        signature hasn't changed since last render, the destroy/rebuild of
        every card is skipped -- rebuilding identical cards on every check
        cycle caused a visible flash (including the check that fires as soon
        as you reopen the window after it's been hidden past an interval)."""
        if not mods:
            self._set_placeholder(frame, baseline_text if first_run else empty_text)
            return None
        sig = self._column_signature(mods)
        if sig != prev_sig:
            self._fill_column(frame, mods)
        else:
            # _pil is only consumed by a rebuild -- drop it on the skip path
            # so stale PIL images don't linger on the mod dicts.
            for m in mods:
                m.pop("_pil", None)
        return sig

    def _fill_column(self, frame, mods):
        # PhotoImage refs are kept per-frame: Tk widgets don't hold a Python
        # reference to their images, so clearing another column's refs while
        # skipping its rebuild would blank its thumbnails.
        photos = self._photos[frame] = []
        for w in frame.winfo_children():
            w.destroy()
        endorsed = set(self.state.get("endorsed", []))
        for mod in mods:
            photo = ImageTk.PhotoImage(rounded_photo(mod.pop("_pil")))
            photos.append(photo)
            accent = CATEGORY_COLORS.get(mod.get("category"), CATEGORY_COLOR_DEFAULT)
            mod["endorsed"] = mod.get("link") in endorsed
            card = ModCard(frame, mod, accent, photo, on_endorse=self._mark_endorsed)
            card.pack(fill="x", pady=CARD_GAP, padx=(0, 2))

    @staticmethod
    def _column_signature(mods):
        """Identifies what's actually rendered in a column."""
        return tuple((m["link"], m.get("version"), m.get("prev_version"), m.get("is_fresh"))
                     for m in mods)

    # ── Timer ──────────────────────────────────────────────────────────

    def _schedule_next(self):
        self._next_check_ts = time.time() + CHECK_INTERVAL_MINUTES * 60
        self._tick_timer()

    def _tick_timer(self):
        # _do_show calls this directly (in addition to whatever chain is
        # already pending from before the window was hidden), so cancel any
        # previously scheduled tick first -- otherwise repeated hide/show
        # cycles stack up duplicate timer chains that never get cleaned up.
        if self._timer_after_id is not None:
            self.root.after_cancel(self._timer_after_id)
            self._timer_after_id = None

        if self._next_check_ts is None:
            return
        left = max(0, int(self._next_check_ts - time.time()))
        if left <= 0:
            self._check_now()
            return
        if self._visible:
            m, s = divmod(left, 60)
            self._lbl_timer.configure(text=f"Next check {m:02d}:{s:02d}")
            self._timer_after_id = self.root.after(1000, self._tick_timer)
        else:
            self._timer_after_id = self.root.after(left * 1000, self._tick_timer)

    # ── Run ────────────────────────────────────────────────────────────

    def run(self):
        self._schedule_update_check()
        self.root.mainloop()
