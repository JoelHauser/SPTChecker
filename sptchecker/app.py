import threading
import time
import tkinter as tk
from datetime import datetime

import pystray
from PIL import Image, ImageTk

from .config import (
    ACCENT_NEW, ACCENT_UPD, BG, CARD_BG, CARD_HOVER,
    CHECK_INTERVAL_SEC, DISPLAY_FIELDS, FORGE_URL, MAX_PER_CATEGORY,
    SEPARATOR, STATE_FIELDS, STATUS_BG, TEXT, TEXT_BRIGHT, TEXT_DIM,
)
from .feed import fetch_mods
from .platform import (
    is_startup_enabled, load_app_icon, send_toast,
    set_dark_title_bar, set_startup_enabled,
)
from .state import download_thumb, load_state, placeholder_thumb, purge_old_thumbs, save_state
from .widgets import ModCard


class SPTCheckerApp:
    def __init__(self, start_hidden=False):
        self._start_hidden = start_hidden
        self.root = tk.Tk()
        self.root.title("SPT Mod Checker")
        self.root.configure(bg=BG)
        self.root.geometry("780x600")
        self.root.minsize(700, 500)

        set_dark_title_bar(self.root)

        self._app_icon = load_app_icon()
        self._icon_photo = ImageTk.PhotoImage(self._app_icon.resize((32, 32), Image.LANCZOS))
        self.root.iconphoto(True, self._icon_photo)

        self.state = load_state()
        self._photos = []
        self._checking = False
        self._next_check_ts = None
        self._tray = None
        self._visible = not start_hidden
        self._startup_var = tk.BooleanVar(value=is_startup_enabled())

        self._build_ui()
        self._setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

        if start_hidden:
            self.root.withdraw()

        self.root.after(400, self._check_now)

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=BG, pady=4)
        hdr.pack(fill="x", padx=12)

        self._btn = tk.Button(
            hdr, text="Check Now", font=("Segoe UI", 8),
            bg=CARD_BG, fg=TEXT, activebackground=CARD_HOVER,
            activeforeground=TEXT_BRIGHT, relief="flat", padx=8, pady=2,
            cursor="hand2", command=self._check_now,
        )
        self._btn.pack(side="right")
        self._tooltip_id = None
        self._tooltip_win = None
        self._btn.bind("<Enter>", self._btn_hover_start)
        self._btn.bind("<Leave>", self._btn_hover_end)

        chk = tk.Checkbutton(
            hdr, text="Run on Startup", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG, selectcolor=CARD_BG,
            activebackground=BG, activeforeground=TEXT,
            variable=self._startup_var, command=self._toggle_startup,
        )
        chk.pack(side="right", padx=(0, 10))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(2, weight=1, uniform="col")

        new_lbl = tk.Label(body, text="● NEW MODS", font=("Segoe UI", 10, "bold"),
                           fg=ACCENT_NEW, bg=BG, anchor="w")
        new_lbl.grid(row=0, column=0, sticky="w", pady=(0, 3))
        new_lbl.bind("<Button-1>", lambda _: self._test_notification())
        self._new_frame = tk.Frame(body, bg=BG)
        self._new_frame.grid(row=1, column=0, sticky="nsew")

        tk.Frame(body, bg=SEPARATOR, width=1).grid(
            row=0, column=1, rowspan=2, sticky="ns", padx=8)

        tk.Label(body, text="● UPDATED MODS", font=("Segoe UI", 10, "bold"),
                 fg=ACCENT_UPD, bg=BG, anchor="w").grid(row=0, column=2, sticky="w", pady=(0, 3))
        self._upd_frame = tk.Frame(body, bg=BG)
        self._upd_frame.grid(row=1, column=2, sticky="nsew")
        body.rowconfigure(1, weight=1)

        self._set_placeholder(self._new_frame, "Checking…")
        self._set_placeholder(self._upd_frame, "Checking…")

        bar = tk.Frame(self.root, bg=STATUS_BG, pady=3)
        bar.pack(fill="x", side="bottom")
        self._lbl_status = tk.Label(bar, text="Starting…", font=("Segoe UI", 8),
                                    fg=TEXT_DIM, bg=STATUS_BG)
        self._lbl_status.pack(side="left", padx=10)
        self._lbl_timer = tk.Label(bar, text="", font=("Segoe UI", 8),
                                   fg=TEXT_DIM, bg=STATUS_BG)
        self._lbl_timer.pack(side="right", padx=10)

    @staticmethod
    def _set_placeholder(frame, text):
        for w in frame.winfo_children():
            w.destroy()
        tk.Label(frame, text=text, font=("Segoe UI", 9), fg=TEXT_DIM,
                 bg=BG, justify="center").pack(pady=20)

    # ── Tooltip ─────────────────────────────────────────────────────────

    def _btn_hover_start(self, event):
        self._tooltip_id = self.root.after(1000, self._show_tooltip)

    def _btn_hover_end(self, _e):
        if self._tooltip_id:
            self.root.after_cancel(self._tooltip_id)
            self._tooltip_id = None
        if self._tooltip_win:
            self._tooltip_win.destroy()
            self._tooltip_win = None

    def _show_tooltip(self):
        self._tooltip_id = None
        x = self._btn.winfo_rootx()
        y = self._btn.winfo_rooty() + self._btn.winfo_height() + 4
        tw = tk.Toplevel(self.root)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=CARD_BG)
        tk.Label(tw, text="Check the Forge for new or updated mods",
                 font=("Segoe UI", 8), fg=TEXT, bg=CARD_BG,
                 padx=8, pady=4).pack()
        self._tooltip_win = tw

    # ── Hidden test notification ──────────────────────────────────────

    def _test_notification(self):
        send_toast(
            "2 New SPT Mods",
            "TestMod v1.0.0 by SampleAuthor\nAnotherMod v2.3.1 by AnotherDev",
            launch_url=FORGE_URL,
        )

    # ── Startup toggle ─────────────────────────────────────────────────

    def _toggle_startup(self):
        try:
            set_startup_enabled(self._startup_var.get())
        except OSError:
            self._startup_var.set(not self._startup_var.get())

    # ── System tray ────────────────────────────────────────────────────

    def _setup_tray(self):
        image = self._app_icon.resize((64, 64), Image.LANCZOS)
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Check Now", self._tray_check),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray = pystray.Icon("SPTModChecker", image, "SPT Mod Checker", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _hide_to_tray(self):
        self._visible = False
        self.root.withdraw()

    def _tray_show(self, _icon=None, _item=None):
        self.root.after(0, self._do_show)

    def _do_show(self):
        self._visible = True
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        if self._next_check_ts:
            self._tick_timer()

    def _tray_check(self, _icon=None, _item=None):
        self.root.after(0, self._check_now)

    def _tray_quit(self, _icon=None, _item=None):
        if self._tray:
            self._tray.stop()
        self.root.after(0, self.root.destroy)

    # ── Check logic ────────────────────────────────────────────────────

    def _check_now(self):
        if self._checking:
            return
        self._checking = True
        self._btn.configure(state="disabled", text="Checking…")
        self._lbl_status.configure(text="Fetching mods…")
        threading.Thread(target=self._bg_check, daemon=True).start()

    @staticmethod
    def _merge_display(fresh, prev, max_count):
        seen = set()
        merged = []
        for mod in fresh + prev:
            link = mod.get("link", "")
            if link not in seen:
                seen.add(link)
                merged.append(mod)
            if len(merged) >= max_count:
                break
        return merged

    @staticmethod
    def _strip_for_state(mods, extra_fields=()):
        fields = (*DISPLAY_FIELDS, *extra_fields)
        return [{k: m[k] for k in fields if k in m} for m in mods]

    def _bg_check(self):
        try:
            feed = fetch_mods()
            known = self.state.get("mods", {})
            first_run = len(known) == 0

            fresh_new, fresh_upd = [], []
            for mod in feed:
                mid = mod["link"]
                if mid not in known:
                    fresh_new.append(mod)
                else:
                    old = known[mid]
                    if old.get("version") != mod["version"] or old.get("updated") != mod["updated"]:
                        fresh_upd.append({**mod, "old_version": old.get("version", "?")})

            for mod in feed:
                known[mod["link"]] = {k: mod[k] for k in STATE_FIELDS if k in mod}
            self.state["mods"] = known
            self.state["last_check"] = datetime.now().isoformat()

            prev_new = self.state.get("display_new", [])
            prev_upd = self.state.get("display_updated", [])

            display_new = self._merge_display(fresh_new, prev_new, MAX_PER_CATEGORY)
            display_upd = self._merge_display(fresh_upd, prev_upd, MAX_PER_CATEGORY)

            if fresh_new or fresh_upd or first_run:
                self.state["display_new"] = self._strip_for_state(display_new)
                self.state["display_updated"] = self._strip_for_state(display_upd, ("old_version",))
                save_state(self.state)

            purge_old_thumbs()

            for mod in display_new + display_upd:
                pil = download_thumb(mod.get("thumb_url"))
                mod["_pil"] = pil if pil else placeholder_thumb()

            notify_new = fresh_new[:MAX_PER_CATEGORY]
            notify_upd = fresh_upd[:MAX_PER_CATEGORY]
            if not first_run:
                self._send_notifications(notify_new, notify_upd)

            self.root.after(0, self._apply, display_new, display_upd, first_run,
                            len(notify_new), len(notify_upd))
        except Exception as exc:
            self.root.after(0, self._on_error, str(exc))

    def _send_notifications(self, new_mods, updated_mods):
        if new_mods:
            lines = []
            for m in new_mods[:3]:
                lines.append(f"{m['title']} {m['version']} by {m['author']}")
            if len(new_mods) > 3:
                lines.append(f"and {len(new_mods) - 3} more…")
            url = new_mods[0]["link"] if len(new_mods) == 1 else FORGE_URL
            send_toast(
                f"{len(new_mods)} New SPT Mod{'s' if len(new_mods) != 1 else ''}",
                "\n".join(lines),
                launch_url=url,
            )

        if updated_mods:
            lines = []
            for m in updated_mods[:3]:
                old_v = m.get("old_version", "?")
                lines.append(f"{m['title']} {old_v} → {m['version']}")
            if len(updated_mods) > 3:
                lines.append(f"and {len(updated_mods) - 3} more…")
            url = updated_mods[0]["link"] if len(updated_mods) == 1 else FORGE_URL
            send_toast(
                f"{len(updated_mods)} SPT Mod{'s' if len(updated_mods) != 1 else ''} Updated",
                "\n".join(lines),
                launch_url=url,
            )

    def _apply(self, display_new, display_upd, first_run, n_fresh_new=0, n_fresh_upd=0):
        self._checking = False
        self._btn.configure(state="normal", text="Check Now")
        self._photos.clear()
        now = datetime.now().strftime("%H:%M:%S")
        total = len(self.state.get("mods", {}))

        if display_new:
            self._fill_column(self._new_frame, display_new, ACCENT_NEW)
        elif first_run:
            self._set_placeholder(self._new_frame, "Baseline set — monitoring for new mods…")
        else:
            self._set_placeholder(self._new_frame, "No new mods detected yet.")

        if display_upd:
            self._fill_column(self._upd_frame, display_upd, ACCENT_UPD)
        elif first_run:
            self._set_placeholder(self._upd_frame, "Baseline set — monitoring for updates…")
        else:
            self._set_placeholder(self._upd_frame, "No updates detected yet.")

        if first_run:
            self._lbl_status.configure(text=f"Baseline: {total} mods cataloged at {now}")
        elif n_fresh_new or n_fresh_upd:
            self._lbl_status.configure(
                text=f"{n_fresh_new} new, {n_fresh_upd} updated at {now}  •  Tracking {total}"
            )
        else:
            self._lbl_status.configure(text=f"No changes at {now}  •  Tracking {total} mods")

        if self._tray:
            n = n_fresh_new + n_fresh_upd
            tip = f"SPT Mod Checker — {n} changes" if n else "SPT Mod Checker — no changes"
            self._tray.title = tip

        self._schedule_next()

    def _on_error(self, msg):
        self._checking = False
        self._btn.configure(state="normal", text="Check Now")
        self._lbl_status.configure(text=f"Error: {msg}")
        self._next_check_ts = time.time() + 300
        self._tick_timer()

    def _fill_column(self, frame, mods, accent):
        for w in frame.winfo_children():
            w.destroy()
        for mod in mods:
            photo = ImageTk.PhotoImage(mod.pop("_pil"))
            self._photos.append(photo)
            card = ModCard(frame, mod, accent, photo)
            card.pack(fill="both", expand=True, pady=2)

    # ── Timer ──────────────────────────────────────────────────────────

    def _schedule_next(self):
        self._next_check_ts = time.time() + CHECK_INTERVAL_SEC
        self._tick_timer()

    def _tick_timer(self):
        if self._next_check_ts is None:
            return
        left = max(0, int(self._next_check_ts - time.time()))
        if left <= 0:
            self._check_now()
            return
        if self._visible:
            m, s = divmod(left, 60)
            self._lbl_timer.configure(text=f"Next check in {m:02d}:{s:02d}")
            self.root.after(1000, self._tick_timer)
        else:
            self.root.after(left * 1000, self._tick_timer)

    # ── Run ────────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()
