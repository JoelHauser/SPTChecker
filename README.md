## SPT Mod Checker

A lightweight Windows desktop app that monitors the [SPT Forge](https://forge.sp-tarkov.com/mods) for new and updated mods in real time.

> **Note on antivirus flags:** A small number of vendors may flag this exe as malicious. Most of them (ALYac, Arcabit, Emsisoft, eScan, GData, VIPRE) all run BitDefender's engine under the hood and are triggering off the same single false positive detection. These are not real threats. Source code is open on GitHub.

---

### What it does

SPT Mod Checker runs quietly in your system tray and checks the Forge every 5 minutes for changes. When it finds something, it sends a Windows toast notification and updates the UI — no need to manually browse the Forge to stay up to date.

It can also do the reverse: point it at your SPT install folder and it'll scan your actually-installed mods and tell you which ones have updates waiting on the Forge (see **Local mod scanning** below) — entirely opt-in, off by default.

---

### How it works

1. On first launch, the app populates both columns — **New Mods** from the Forge RSS feed and **Recently Updated** mirroring the website's "Recently Updated" tab.
2. Both columns use the Forge RSS feeds and API — **no HTML scraping**.
3. Every 5 minutes, it re-fetches and compares against stored state.
4. New mod URLs are flagged as **New**. The Recently Updated column mirrors the Forge website in real time.
5. If a mod author unpublishes their mod, it is automatically removed from the display.
6. Results persist across checks and restarts — new findings push older entries down in a rolling history (up to 7 per column).
7. If a check fails (Forge unreachable, network hiccup, etc.), the app doesn't crash — it shows the error in the status bar and quietly retries on the next cycle.

---

### UI

- **Two-panel layout** — New mods on the left, recently updated mods on the right (up to 7 per column)
- **Mod cards** — Each card shows the thumbnail, title, author, version, category, and description
- **Version diff** — Updated mods show the version change inline (e.g. `1.2.3 → 1.3.0`)
- **View Change Notes** — A small icon on updated mod cards opens a popup with that version's changelog, rendered as actual markdown (bold, italic, inline code, headers, bullet lists, and clickable links that open in your browser) rather than a plain text dump, plus a direct "Open on Forge" button
- **NEW badge** — Freshly detected mods are marked with a green NEW badge so you can spot changes at a glance
- **NEW AUTHOR badge** — Mods from accounts created in the last 60 days are flagged so you can spot new community members
- **Timestamps** — Each card shows how long ago the mod was published or updated (e.g. "2h ago", "yesterday")
- **Hover-scrolling** — Hover over any card to smoothly scroll truncated text and read the full details
- **Click to open** — Click any card to open the mod page directly on the Forge
- **Right-click menu** — Right-click any card for quick options to view change notes, open on Forge, or copy the link
- **Category legend** — Hover the info icon in the header for a color key to every mod category
- **Stats window** — Click **Stats** for total mods tracked, mods added this week, a 30-day daily-activity chart (hover any point for the exact date/count), and the top 5 authors/categories by activity in the last 30 days (rolls forward automatically, no manual reset). Author names are clickable and open their Forge profile
- **Window size memory** — The app remembers and restores its window size between sessions
- **Dark theme** — Styled to match the SPT aesthetic

---

### Local mod scanning (opt-in)

Click **Local Mods** to check your own installed mods against the Forge — entirely optional and off by default:

1. Point it at your SPT install folder (validated automatically — it checks for `BepInEx/plugins` or `SPT/user/mods`).
2. Click **Scan Now**. The app reads each installed mod's real identity directly from its compiled files — BepInEx client plugins' `BepInPlugin` metadata and SPT v4 server mods' declared metadata — rather than guessing from filenames.
3. Each mod is matched against the Forge (exact ID lookup first, falling back to a name search + fuzzy ranking) and grouped into:
   - **Updates Available** — shown as full mod cards (thumbnail, version diff, changelog, right-click menu — same as the main feed)
   - **Up to Date** — a simple clickable list linking to each mod's Forge page
   - **Not Found on Forge** — mods that couldn't be matched
4. A toast notification fires if any installed mods have updates available.
5. Your folder path and last scan results are remembered across restarts. If enabled, it automatically rescans once when the app launches — it does **not** rescan on the regular 5-minute timer, only on startup or when you manually click Scan Now.

---

### System tray

- Closing the window minimizes to tray instead of quitting
- Double-click the tray icon to restore the window
- Right-click for **Show**, **Check Now**, or **Quit**
- **Tray badge** — A red dot appears on the tray icon when there are unread changes while the app is minimized, with an unread count in the tooltip. Clears automatically when you open the window
- Toast notifications work even while minimized

---

### Status bar

- **Forge status indicator** — A dot shows whether the last check reached the Forge OK (green) or failed (red); hover for details
- **Live countdown** — Shows time remaining until the next automatic check
- **Running summary** — Shows the baseline mod count on first run, or how many new/updated mods were found (and total tracked) on each subsequent check

---

### Additional features

- **Run on Startup** — One-click checkbox to launch silently in the background when Windows starts
- **Thumbnail caching** — Mod thumbnails are cached to disk and auto-purged after 3 days; mods with no uploaded thumbnail get the same wireframe placeholder icon shown on the Forge itself, instead of a blank box
- **Unpublished mod detection** — Mods removed from the Forge are automatically cleared from the display
- **Low resource usage** — Connection pooling, shared fonts, smart timer that sleeps when the window is hidden, and state writes are skipped when nothing changed
- **Standalone .exe** — No Python installation required, just download and run
