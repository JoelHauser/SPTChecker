# SPT Mod Checker

A lightweight Windows desktop app that monitors the [SPT Forge](https://forge.sp-tarkov.com/mods) for new and updated mods. Uses the Forge RSS feeds and API to display results in a dark-themed UI with thumbnails, SPT version badges, and Windows toast notifications — even while running silently in the system tray.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Live monitoring** — Checks the Forge on a configurable interval (5–60 min, default 20).
- **Adjustable interval** — Slider in the status bar, saved across restarts.
- **Two-panel UI** — New mods and recently updated mods displayed side-by-side with thumbnails, author, category, and description. Both columns mirror the Forge website's ordering.
- **SPT version badge** — Pulled from the Forge API, no HTML scraping.
- **Unpublished mod detection** — Mods removed from the Forge are automatically cleared from the display.
- **Scrolling text** — Hover over any card to read the full details.
- **Persistent history** — Mod cards persist across checks and restarts.
- **Windows notifications** — Toast notifications when changes are found, even while minimized.
- **System tray** — Closing minimizes to tray. Right-click for Show, Check Now, or Quit.
- **Run on startup** — One-click checkbox for silent background launch.
- **Thumbnail caching** — Cached to disk, auto-purged after 3 days.

## Setup

### Standalone exe

Download `SPTModChecker_v2.0.2.exe` from [Releases](https://github.com/JoelHauser/SPTChecker/releases). No Python needed.

### From source

```bash
pip install -r requirements.txt
python main.py
```

## Usage

| Action              | How                                          |
|---------------------|----------------------------------------------|
| Check immediately   | Click **Check Now** in the header             |
| Change interval     | Drag the **Interval** slider in the status bar |
| Open a mod page     | Click any mod card                            |
| Read full text      | Hover over a card                             |
| Minimize to tray    | Close the window (X button)                   |
| Restore from tray   | Double-click tray icon, or right-click → Show |
| Quit                | Right-click tray icon → Quit                  |
| Auto-start          | Check **Run on Startup** in the header        |

## How It Works

1. On launch, the app populates both columns from the Forge RSS feeds — New Mods and Recently Updated — matching the website's ordering exactly.
2. SPT version compatibility is fetched from the Forge API — no HTML scraping.
3. At the configured interval, it re-fetches and compares against stored state.
4. New mod URLs are flagged as **new**. The Recently Updated column mirrors the Forge website in real time.
5. Mods that get unpublished are automatically removed from the display.
6. Up to 7 cards per column. Windows toast notifications fire automatically, even while minimized to the tray.

## License

This project is provided as-is for personal use.
