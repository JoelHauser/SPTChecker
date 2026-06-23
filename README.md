# SPT Mod Checker

A lightweight Windows desktop app that monitors the [SPT Forge](https://forge.sp-tarkov.com/mods) for new and updated mods. It checks the forge every 30 minutes, displays results in a dark-themed UI, and sends Windows toast notifications when changes are detected — even while running silently in the system tray.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Live monitoring** — Polls the SPT Forge RSS feed every 30 minutes for new and updated mods.
- **Two-panel UI** — New mods and updated mods displayed side-by-side with thumbnails, author, category, and description.
- **Scrolling text** — Hover over any mod card to smoothly scroll truncated text and read the full details.
- **Windows notifications** — Toast notifications with mod info fire automatically when changes are found, even while minimized.
- **System tray** — Closing the window minimizes to tray. Right-click the tray icon for Show, Check Now, or Quit.
- **Run on startup** — One-click checkbox registers the app to launch silently in the background when Windows starts.
- **Thumbnail caching** — Mod thumbnails are cached to disk so they only download once.
- **Low resource usage** — Connection pooling, disk-cached images, and a smart timer that sleeps when the window is hidden.

## Setup

```bash
pip install -r requirements.txt
```

### Dependencies

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
├── sptchecker/
│   ├── config.py        # Constants, paths, colors
│   ├── feed.py          # RSS fetching and parsing
│   ├── state.py         # JSON persistence and thumbnail cache
│   ├── platform.py      # Windows integrations (dark title bar, startup registry, toasts, tray)
│   ├── widgets.py       # ModCard widget with hover-scroll
│   └── app.py           # Main application class
└── data/                # Runtime data (gitignored)
    ├── spt_mods_state.json
    └── thumb_cache/
```

## How It Works

1. On launch, the app fetches the latest ~50 mods from the SPT Forge RSS feed.
2. First run establishes a baseline — all mods are cataloged without triggering notifications.
3. Every 30 minutes, it re-fetches the feed and compares against the stored state.
4. Mods with a new URL are flagged as **new**. Mods with a changed version or update timestamp are flagged as **updated**.
5. Up to 5 of each are displayed in the UI, and Windows toast notifications are sent if the app is running (even in the tray).
6. The mod state accumulates over time, so the app can detect changes across restarts.

## License

This project is provided as-is for personal use.
