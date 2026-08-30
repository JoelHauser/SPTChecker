# SPTChecker — working notes for Claude

Windows tray app (Python 3.13 + Tkinter + Pillow) that watches the SPT Forge at
sp-mod.com for new and updated mods, and optionally scans the user's installed
mods against it. Ships as a single PyInstaller .exe.

This file is loaded automatically at the start of every session. Keep it to
things a fresh session would otherwise have to rediscover the hard way — not a
chronological diary, which would grow without bound and cost context on every
run. **Update the "Current state" section at the end when you finish a piece of
work.**

---

## Layout

| Module | Owns |
| --- | --- |
| `main.py` | Argument parsing (`--background`) and launch |
| `sptchecker/app.py` | The main window, all state, all threading, the check cycle |
| `sptchecker/widgets.py` | Cards, popups, scroll areas — presentation only |
| `sptchecker/theme.py` | Colour math, cached fonts, PIL-rendered primitives |
| `sptchecker/config.py` | Every tunable: colours, metrics, URLs, limits, feature flags |
| `sptchecker/feed.py` | All Forge HTTP: rate limiting, retries, RSS + API parsing |
| `sptchecker/state.py` | State file load/save/migrate, thumbnails, `compute_stats` |
| `sptchecker/localmods.py` | Scanning an SPT install for installed mods |
| `sptchecker/matcher.py` | Matching installed mods to Forge listings |
| `sptchecker/platform.py` | Windows specifics: DPI, dark title bar, registry, toasts |
| `modreader/` | Standalone .NET helper, built separately via `dotnet publish` |

**Widgets stay dumb.** They own no network or state logic — only widgets and
callbacks. `app.py` already owns state and threading, so it drives the work and
calls back into the popup. Follow this when adding features; the local-scan
window is the reference example.

## Things that will bite you

Each of these cost real debugging time. They are not hypothetical.

- **Tk's canvas has no anti-aliasing on Windows.** Every rounded corner, pill
  and glyph is drawn by Pillow at `SS` (4x) and LANCZOS-downsampled. Never draw
  a shape with `create_oval`/`create_arc` and expect it to look acceptable.
- **Never name an attribute `self._w` on a widget subclass.** `tkinter.Misc`
  stores the widget's Tcl path there. Shadowing it makes every later `tk.call`
  address a widget named `"89"`.
- **Never hardcode a pixel height for anything containing text.** Tk font sizes
  are in points, so they grow with Windows display scaling while a pixel
  constant does not. `widgets.card_metrics()` measures a real card built
  off-screen; `app._size_to_fit()` and `app._min_width()` measure the real
  chrome. Arithmetic guesses were 2px short and clipped every description.
- **A `RoundedPanel` with an externally managed height must push that height
  onto its embedded frame.** Otherwise the frame keeps its own request — for a
  `tk.Text` that is 24 lines — and the overflow is silently clipped, so the
  Text believes it fits and never becomes scrollable.
- **`tk.Canvas` requests a default 378px width.** In a narrower container it
  consumes the whole cavity, and anything packed after it (a scrollbar) is
  allocated nothing. Pass `width=1` and pack the scrollbar with `before=`.
- **Buttons must require a press before acting on a release.** Destroying a
  window on mouse-down drops the pointer grab and the OS delivers the release
  to whatever was underneath. See `theme.PillButton._on_press`.
- **Wheel scrolling is dispatched globally** (`widgets._on_global_wheel`).
  Binding `<Enter>`/`<Leave>` on a scroll canvas breaks the moment it holds
  embedded windows, because Tk reports a Leave when the pointer enters a child.

## The Forge

- **The v0 API is read-only and unauthenticated.** Their docs say so
  explicitly, and every route is a GET. There is no way to act as the user —
  which is why endorsing is switched off behind `config.ENDORSE_ENABLED`
  rather than implemented. Do not attempt to drive the website with a user's
  login to work around this.
- **Respect the rate limits.** `feed.py` meters API and media traffic on
  separate sliding windows because the server counts them separately. The
  maintainer has turned on bot countermeasures before. Never bypass
  `_forge_request`.
- **15 minutes is the poll floor**, matching the shortest cache window
  sp-mod.com serves. Checking faster spends requests on unchanged bytes.
- `published_at` (API) is the true publish time; RSS `pubDate` is when the
  listing was *created*, often a day earlier. The stats chart counts new
  publications only, never updates.
- **A newly published mod appears in *both* feeds at once** -- it is
  simultaneously the newest-created and the newest-updated thing on the site,
  so `fetch_feeds()` returns it in both lists. `_bg_check` holds anything in
  the new column out of the updated one; without that it filled a slot in
  both and fired two toasts for one event. Roughly 12 of each 50-mod window
  overlap, so this is the common case, not an edge one.

## Conventions

- **Comments explain why, not what** — ideally naming the failure the code
  prevents. Match the surrounding density; the codebase is deliberately heavy
  on rationale because most of it encodes a bug that already happened once.
- Prose in comments uses `--`, not em dashes.
- `config.py` holds the tunables. Layout constants shared between modules live
  next to the code that reads them (see `app.BODY_PAD_TOP`).

## Verifying UI changes

There is no automated test suite. UI work is verified with a throwaway
**offline harness** — rebuild it in a scratch directory rather than committing
it. The pattern:

1. Point `config.DATA_DIR` / `STATE_FILE` / `CACHE_DIR` at a `tempfile.mkdtemp()`
   **before** importing `sptchecker.app`, so the user's real state is untouched.
2. Stub `app.fetch_feeds`, `unpublished_links`, `download_thumb`,
   `check_for_update`, `send_toast`, and `SPTCheckerApp._setup_tray`.
3. Build the app, `root.after(...)` a callback, then screenshot the window via
   `DwmGetWindowAttribute(hwnd, 9, ...)` + `ImageGrab.grab(bbox=...)`.
4. **Look at the screenshot.** Most of the bugs in this app's history were
   visible and would not have been caught by an assertion.

Simulate display scaling with `root.tk.call("tk", "scaling", 1.5)` before
building — several bugs only appear at 125%/150%.

## Releasing

The version lives in **four** places and they must agree:

1. `config.APP_VERSION` — the runtime source of truth
2. `version_info.py` — `filevers`, `prodvers`, `FileVersion`, `ProductVersion`,
   `OriginalFilename`
3. `SPTModChecker_v<VER>.spec` — the filename *and* the `name=` inside it
4. `README.md` — the "Update to X" line in the migration notice

Then `python -m PyInstaller --noconfirm SPTModChecker_v<VER>.spec`.

- **Avoid `--clean`.** The repo sits in a OneDrive-synced folder; sync holds a
  lock on `build/` and `--clean` dies with `PermissionError`. Delete the build
  folder manually if you need a cold build.
- **Verify by artifact timestamp, not exit code.** A failed PyInstaller run can
  leave a stale exe in `dist/` that looks like a fresh build.
- **Smoke-test the exe before shipping.** Launch it with `--background`, confirm
  it survives ~15s and wrote a fresh `last_check` to the state file. A frozen
  build can fail at runtime on a missing import even when the build succeeded.
  Kill the whole process tree — the PyInstaller bootloader spawns a child that
  outlives a plain kill of the parent.
- `dist/` and `build/` are gitignored; releases go to the Forge and GitHub.

---

## Current state

**Update this section as work completes.**

- Version **3.4.1**, working branch **`checkertest`** (not `main`).
- 3.4.0 was a full visual overhaul: `theme.py` is new, and cards, header,
  stats, popups and window sizing were all rebuilt. 3.4.1 fixed two bugs it
  introduced (popup close button activating the control underneath; change
  notes clipping their last lines).
- The built `dist/SPTModChecker_v3.4.1.exe` predates the most recent commits
  (dead-code removal, chart relabel, and the duplicate new/updated fix). The
  first two change nothing users see; the duplicate fix does, so a rebuild is
  needed before it reaches them.
- Update notifications fire on a mod's **version actually changing**, not on
  it entering the updated column. The old rule missed a genuine update to a
  mod already sitting in the column, and announced unchanged mods that
  drifted back into it.

### Open items

- **Changelogs are capped at 5000 chars** (`feed.CHANGELOG_MAX_CHARS`), so a
  long one shows less in-app than on the Forge. Raising it grows the state file.
- **The Stats popup centres over the header**, putting its ✕ on top of the
  Local Mods button. Harmless since buttons now require a matching press, but
  offsetting popups below the header bar would be tidier.
- **`feed.get_session()` is dead code** — predates this work, left in case it
  is a deliberate accessor.
- **`theme._BUTTON_KINDS["ghost"]`** is never selected; removing it would
  collapse the whole `kind` parameter.
- **~230 MB of stale `build/` folders** for versions 2.1.1 – 3.0.0. Gitignored
  and regenerable, but they sync to OneDrive for no reason.
