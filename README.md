# SPT Mod Checker

A lightweight Windows desktop app that monitors the [SPT Forge](https://forge.sp-tarkov.com/mods) for new and updated mods. Checks on a user-configurable interval (5–60 minutes, default 20), displays results in a dark-themed UI with thumbnails and SPT version badges, and sends Windows toast notifications — even while running silently in the system tray.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Live monitoring** — Polls the SPT Forge RSS feed on a configurable interval (5–60 minutes, default 20) for new and updated mods.
- **Adjustable check interval** — Slider in the status bar lets you set how often the app checks the Forge. Your choice is saved and persists across restarts.
- **Two-panel UI** — New mods and updated mods displayed side-by-side (6 per column) with thumbnails, author, category, and description.
- **SPT version badge** — Each card shows the compatible SPT version scraped from the Forge listing page.
- **Scrolling text** — Hover over any mod card to smoothly scroll truncated text and read the full details.
- **Persistent history** — Mod cards persist across checks and restarts. New findings push older entries down, keeping a rolling list.
- **Windows notifications** — Toast notifications fire automatically when changes are found, even while minimized to the tray.
- **System tray** — Closing the window minimizes to tray. Right-click for Show, Check Now, or Quit.
- **Run on startup** — One-click checkbox to launch silently in the background when Windows starts.
- **Thumbnail caching** — Mod thumbnails cached to disk, auto-purged after 3 days.
- **Low resource usage** — Connection pooling, shared fonts, smart timer that sleeps when hidden, compact state file with skipped writes on no-change checks.

## Setup

### From source

```bash
pip install -r requirements.txt
python main.py
```

### Standalone exe

Download `SPTModChecker_v1.3.exe` from [Releases](https://github.com/JoelHauser/SPTChecker/releases). No Python install needed — just run it.

### Dependencies (source only)

| Package    | Purpose                        |
|------------|--------------------------------|
| `requests` | HTTP requests                  |
| `Pillow`   | Image handling and thumbnails  |
| `pystray`  | System tray icon               |
| `winotify` | Windows toast notifications    |

## Usage

```bash
# Launch normally
python main.py

# Launch minimized to the system tray
python main.py --background
```

### Controls

| Action              | How                                          |
|---------------------|----------------------------------------------|
| Check immediately   | Click **Check Now** in the header             |
| Change check interval | Drag the **Interval** slider in the status bar |
| Open a mod page     | Click any mod card                            |
| Read full text      | Hover over a card — text scrolls automatically|
| Minimize to tray    | Close the window (X button)                   |
| Restore from tray   | Double-click the tray icon, or right-click → Show |
| Quit                | Right-click tray icon → Quit                  |
| Auto-start          | Check **Run on Startup** in the header        |

## Project Structure

```
SPTChecker/
├── main.py              # Entry point
├── requirements.txt     # pip dependencies
├── .gitignore
├── assets/              # App icons
│   ├── icon.png
│   ├── icon_256.png
│   └── icon.ico
└── sptchecker/
    ├── config.py        # Constants, paths, colors
    ├── feed.py          # RSS fetching, SPT version scraping
    ├── state.py         # JSON persistence, thumbnail cache, purge
    ├── platform.py      # Dark title bar, startup registry, toasts, tray icon
    ├── widgets.py       # ModCard widget with hover-scroll, IntervalSlider
    └── app.py           # Main application class
```

App data (state file and thumbnail cache) is stored in `%LOCALAPPDATA%\SPTModChecker\`.

## How It Works

1. On launch, the app fetches the latest ~50 mods from the SPT Forge RSS feed and scrapes SPT version compatibility from the listing page.
2. First run establishes a baseline — all mods are cataloged without triggering notifications.
3. At the configured interval (default 20 minutes), it re-fetches and compares against the stored state.
4. Mods with a new URL are flagged as **new**. Mods with a changed version or update timestamp are flagged as **updated**.
5. Up to 6 of each are displayed in the UI. New findings push older entries down in the rolling history.
6. Windows toast notifications are sent if the app is running (even in the tray).
7. Thumbnails are cached to disk and auto-purged after 3 days.

## License

This project is provided as-is for personal use.
