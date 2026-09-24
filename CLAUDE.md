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
- **Tk centres canvas text by its line box, not its capitals.** The line box
  keeps room for descenders, so centred labels sat 0.5px to 3px low depending
  on display scaling and on which way Tk rounded. `theme.cap_centred_top`
  places the baseline from `theme.CAP_HEIGHT_EM` instead; `PillButton` and
  `ToggleSwitch` use it (measured within half a pixel, 100% to 300%). Other
  text drawn centred on a canvas still centres the old way.
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
  sp-mod.com serves. Checking faster spends requests on unchanged bytes. The
  status bar's schedule menu (`config.CHECK_INTERVAL_CHOICES`) only offers
  intervals at or above it.
- `published_at` (API) is the true publish time; RSS `pubDate` is when the
  listing was *created*, often a day earlier. The stats chart counts new
  publications only, never updates.
- **A newly published mod appears in *both* feeds at once** -- it is
  simultaneously the newest-created and the newest-updated thing on the site,
  so `fetch_feeds()` returns it in both lists. `_bg_check` holds anything in
  the new column out of the updated one; without that it filled a slot in
  both and fired two toasts for one event. Roughly 12 of each 50-mod window
  overlap, so this is the common case, not an edge one.
- **`filter[spt_version]` filters mods, never their versions.** The page holds
  only mods with *a* compatible version, but `include=versions` still lists
  all of them (newest first by version number, not date; capped near 10 per
  mod, though the docs say 6). `_parse_api_mod` picks the one to show with
  `utils.spt_version_satisfies`, and a mod with its own line per SPT release is
  the common case -- 79 of 100 compatible mods sampled showed an older version
  than their newest.
- **Constraints are Composer semver, typed loosely.** `~4.0` means `<5.0.0`
  (not npm's `<4.1.0`), and live listings carry `~4.`, `~4.1. > 4.1.1`,
  `4.0.x` and `3.7.1 - 4.1.3`, all of which the Forge accepts. The ground truth
  is the server itself: `GET /api/v0/spt/versions?filter[spt_version]=<c>`
  returns the releases `<c>` allows. The evaluator matched it on every
  constraint across 183 live listings -- re-run that comparison before changing
  it. RSS carries no constraints at all, so `fetch_feeds` skips RSS while
  filtering.

## Toast activation

Clicking a toast raises the window, via a chain worth knowing before touching
any part of it:

1. `send_toast` sets winotify's `launch=SHOW_URI`, which becomes
   `activationType="protocol"` on the toast XML.
2. Windows shell-executes `sptchecker://show`, resolved through a scheme the
   app registers for itself in HKCU on every launch (`register_show_protocol`).
3. That starts a **second copy of the app**. `main.py` spots the URI before
   argparse sees it, signals a named event, and exits.
4. The running copy's waiter thread wakes and calls `_do_show` on the UI
   thread. If nothing was listening the app wasn't running, so the second copy
   stays up and shows itself instead.

- **Declare argtypes/restype on every event call.** A HANDLE is 64-bit; ctypes
  defaults returns to C int and silently truncates it, so the wait then blocks
  on a handle that is not the event -- and a truncated handle still looks like
  a plausible number, so it fails invisibly.
- **The event is auto-reset, so exactly one waiter wakes per signal.** If a
  stale instance is left running during testing it eats the wake and the app
  under test looks broken.
- **`Process.MainWindowHandle` does not reliably report a Tk window** -- it
  read 0 for a window that was plainly visible. Verify visibility by
  enumerating windows and calling `IsWindowVisible`, not through .NET.

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
  A launch within the check interval of the last check doesn't check at all,
  so smoke-test against a fresh data dir (point `LOCALAPPDATA` at an empty
  folder) or a `last_check` older than the interval.
  Kill the whole process tree — the PyInstaller bootloader spawns a child that
  outlives a plain kill of the parent.
- `dist/` and `build/` are gitignored; releases go to the Forge and GitHub.

---

## Current state

**Update this section as work completes.**

- SPT 4.1 server scan fix on `spt41-server-mods`: ModReader now reads
  `IModMetadata` (including explicit implementations) as well as inherited
  `AbstractModMetadata`. Rebuilt `assets/ModReader.exe`. Helper failures raise
  scan errors instead of successful empty scans; individual DLL errors are
  logged while readable mods remain available. Sixteen regression tests pass,
  including real SPT 4.1.6 and 4.0.13 installs and a scan without client plugins.
  Windows 10 itself has not been tested. Fix version is 3.4.6.
  Executable: `dist/SPTModChecker_v3.4.6.exe` (and matching zip).
  Frozen smoke test passed: alive after 18 seconds, fresh `last_check`, embedded
  helper matches `assets/ModReader.exe`; original registry entries restored.

- Version **3.4.6**, working branch **`spt41-server-mods`**, based on `checkertest` (not `main`).
- 3.4.0 was a full visual overhaul: `theme.py` is new, and cards, header,
  stats, popups and window sizing were all rebuilt. 3.4.1 fixed two bugs it
  introduced (popup close button activating the control underneath; change
  notes clipping their last lines). 3.4.2 stopped a newly published mod
  filling a slot in both columns and firing two toasts.
- **3.4.5 is built and smoke-tested**: `dist/SPTModChecker_v3.4.5.exe` plus a
  matching `.zip` (the exe alone at the zip root, same shape as previous
  releases), tagged `V3.4.5`. Smoke test ran against a fresh data dir: alive
  after 15s, `last_check` written, filter resolved to the newest release. The
  binary still has to be attached to a GitHub release and uploaded to the
  Forge by hand -- `gh` isn't signed in on either machine.
- **Smoke-testing the exe rewrites the real registry**: `refresh_startup_if_stale`
  points the HKCU Run entry at `dist/`, and `register_show_protocol` does the
  same for `sptchecker://`. Back both up first and restore them after, or the
  installed copy stops launching at startup once `dist/` is cleaned.
- 3.4.4 carried the R2-traffic response described in Open Items: the 30-day
  thumbnail cache and category-tinted placeholders.
- No 3.4.1 exe was ever built -- `dist/` went straight from 3.3.3 to 3.4.2,
  so 3.4.1's two fixes first reached users in 3.4.2.
- 3.4.3 made a toast click raise the window (see **Toast activation**).
- **3.4.5 is scrmjt's PR #27** (SPT version filter, check schedule,
  cap-centred labels), the first outside contribution. It was merged with
  their commits intact rather than squashed, so they keep authorship on
  GitHub, and credited in the README's Credits section. Do the same for
  future contributors.
- Update notifications fire on a mod's **version actually changing**, not on
  it entering the updated column. The old rule missed a genuine update to a
  mod already sitting in the column, and announced unchanged mods that
  drifted back into it.
- **SPT version filter** (header picker, since 3.4.5). State keys:
  `spt_version_filter` -- `"latest"`, `"4.0.x"` (newest of that line), a
  release, `"auto"` (the Local Mods install) or `"all"`; absent means
  `config.DEFAULT_SPT_VERSION_FILTER`, which is `"latest"`. `spt_versions`
  is the release list those resolve against, refreshed by the first check
  each day on the check thread -- not a timer, because the default can't
  resolve without it. `last_check_spt_version` is the resolved release the
  last check used, so a new release moving "latest" re-baselines like any
  other filter change. Mod records taken under a filter carry `spt_version`.
  `_bg_check` stays quiet on the check after the filter changes, and never
  compares a version against one recorded under a different filter -- the
  same mod is legitimately 1.3.0 for one SPT and 0.9.3 for another, and
  comparing them was a toast and a downgrade arrow for every such mod.
- **Check schedule** (status bar countdown, since 3.4.5). State key
  `check_interval_minutes`: one of `config.CHECK_INTERVAL_CHOICES`, or 0 for
  off; anything else falls back to 15. A launch no longer always checks:
  `_start_schedule` checks only if `last_check` is older than the interval,
  otherwise it shows the saved columns (thumbnails loaded on a thread, since
  a cache miss would be fetched on the UI thread) and resumes the countdown.
  A failed check retries after `max(5, interval // 12)` minutes, and never
  when checks are off.

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
- **Check Now has lost its hover highlight**: `_bind_tooltip` replaces
  `PillButton`'s own `<Enter>` binding instead of adding to it (flagged by
  scrmjt in PR #27, predates it).
- **~230 MB of stale `build/` folders** for versions 2.1.1 – 3.0.0. Gitignored
  and regenerable, but they sync to OneDrive for no reason.
- **The Forge's R2 image storage is under cost pressure from external
  consumers** (clodan, 2026-09-07 in Discord) and this app is caught in
  whatever rule they use to curb it -- thumbnail fetches may start erroring
  outright, with no alternative source planned. `THUMB_MAX_AGE_DAYS` was
  raised 3 → 30 to match the 1 month edge cache clodan added on their end,
  cutting repeat R2 hits per install by roughly 10x. `placeholder_thumb()`
  (`state.py`) now tints its panel with the mod's `CATEGORY_COLORS` accent
  instead of a flat `SEPARATOR` gray and caches one rendering per category,
  so a run of fetch failures still reads as distinct cards instead of a wall
  of identical gray tiles -- `download_thumb` already failed soft to this on
  any fetch error, that part needed no change. If clodan mentions a pre-sized
  thumbnail variant (today `download_thumb` pulls the full-resolution image
  from R2 and downsizes to 52x52 locally, which is more bytes than
  necessary), switching to it would cut per-request bytes too.
