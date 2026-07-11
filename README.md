## SPT Mod Checker

*Inspired by Refringe's Check Mods CLI <3*

A lightweight Windows desktop app that monitors the [SPT Forge](https://forge.sp-tarkov.com/mods) for new and updated mods in real time.

> **Note on antivirus flags:** A small number of vendors may flag this exe as malicious. Most of them (ALYac, Arcabit, Emsisoft, eScan, GData, VIPRE) all run BitDefender's engine under the hood and are triggering off the same single false positive detection. These are not real threats. Source code is open on GitHub.

---

### What it does

SPT Mod Checker runs quietly in your system tray and checks the Forge on a configurable interval (5–60 minutes, default 20) for changes. When it finds something, it sends a Windows toast notification and updates the UI — no need to manually browse the Forge to stay up to date.

---

### How it works

1. On first launch, the app populates both columns — **New Mods** from the Forge RSS feed and **Recently Updated** mirroring the website's "Recently Updated" tab.
2. Both columns use the Forge RSS feeds and API — **no HTML scraping**.
3. At the configured interval, it re-fetches and compares against stored state.
4. New mod URLs are flagged as **New**. The Recently Updated column mirrors the Forge website in real time.
5. If a mod author unpublishes their mod, it is automatically removed from the display.
6. Results persist across checks and restarts — new findings push older entries down in a rolling history (up to 7 per column).

---

### UI

- **Two-panel layout** — New mods on the left, recently updated mods on the right (up to 7 per column)
- **Mod cards** — Each card shows the thumbnail, title, author, version, category, and description
- **Version diff** — Updated mods show the version change inline (e.g. `1.2.3 → 1.3.0`)
- **View Change Notes** — A small icon on updated mod cards opens a popup with that version's full changelog
- **NEW badge** — Freshly detected mods are marked with a green NEW badge so you can spot changes at a glance
- **Timestamps** — Each card shows how long ago the mod was published or updated (e.g. "2h ago", "yesterday")
- **Hover-scrolling** — Hover over any card to smoothly scroll truncated text and read the full details
- **Click to open** — Click any card to open the mod page directly on the Forge
- **Right-click menu** — Right-click any card for quick options to open on Forge or copy the link
- **Adjustable interval** — Slider in the status bar to control check frequency, saved across restarts
- **Window size memory** — The app remembers and restores its window size between sessions
- **Dark theme** — Styled to match the SPT aesthetic

---

### System tray

- Closing the window minimizes to tray instead of quitting
- Double-click the tray icon to restore the window
- Right-click for **Show**, **Check Now**, or **Quit**
- **Tray badge** — A red dot appears on the tray icon when there are unread changes while the app is minimized, with an unread count in the tooltip. Clears automatically when you open the window
- Toast notifications work even while minimized

---

### Additional features

- **Run on Startup** — One-click checkbox to launch silently in the background when Windows starts
- **Forge status indicator** — A dot in the status bar shows whether the Forge is reachable (hover for details)
- **Thumbnail caching** — Mod thumbnails are cached to disk and auto-purged after 3 days
- **Unpublished mod detection** — Mods removed from the Forge are automatically cleared from the display
- **Low resource usage** — Connection pooling, shared fonts, smart timer that sleeps when the window is hidden, and state writes are skipped when nothing changed
- **Standalone .exe** — No Python installation required, just download and run
