import os
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    _BUNDLE_DIR = Path(sys._MEIPASS)
else:
    _BUNDLE_DIR = Path(__file__).parent.parent

ASSETS_DIR = _BUNDLE_DIR / "assets"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SPTModChecker"
STATE_FILE = DATA_DIR / "spt_mods_state.json"
CACHE_DIR = DATA_DIR / "thumb_cache"

# ── Version ────────────────────────────────────────────────────────────

# The runtime source of truth: the User-Agent sent to the Forge and the
# baseline the self-update check compares releases against. version_info.py
# and the .spec still carry their own copy, since PyInstaller reads those at
# build time and can't import this -- keep all three in step when bumping.
APP_VERSION = "3.4.1"

# This app's own listing on the Forge -- where users actually download it, so
# it's the version that matters for "am I out of date". Checked through the
# same metered request path as everything else, one request per interval.
FORGE_MOD_ID = 2921
FORGE_MOD_PAGE = "https://sp-mod.com/mod/2921/sptchecker"
# Six-hourly, not per check cycle. A release lands every few weeks at most, so
# riding the 15-minute mod poll would spend hundreds of requests a day to learn
# nothing -- against a host that meters us and asked us to ease off.
UPDATE_CHECK_INTERVAL_HOURS = 6

# ── Feed ───────────────────────────────────────────────────────────────

FEED_URL = "https://sp-mod.com/mods/rss"
FEED_UPDATED_URL = "https://sp-mod.com/mods/rss?sort=updated"
API_URL = "https://sp-mod.com/api/v0/mods"
API_MOD_URL = "https://sp-mod.com/api/v0/mod"
API_MODS_UPDATES_URL = "https://sp-mod.com/api/v0/mods/updates"
FORGE_URL = "https://sp-mod.com/mods"
FORGE_USER_URL = "https://sp-mod.com/user"
DC_NS = "http://purl.org/dc/elements/1.1/"

# The Forge moved to sp-mod.com under new management; the old hosts are fully
# down (HTTP 521), not redirecting, so nothing resolves without this. Mod
# ids and slugs carried over unchanged -- verified live, old
# /mod/<id>/<slug> paths resolve 1:1 on the new domain -- so rewriting just
# the host preserves each mod's identity. That matters because a mod's link
# is its dedupe key in saved state: without this rewrite every already-known
# mod would look brand new on the first check after updating and fire a
# notification storm.
HOST_MIGRATIONS = {
    "forge.sp-tarkov.com": "sp-mod.com",
    "forge-static.sp-tarkov.com": "files.sp-mod.com",
}
# State fields holding a URL that needs the rewrite above.
MIGRATED_URL_FIELDS = ("link", "thumb_url")

# ── Behaviour ──────────────────────────────────────────────────────────

# Matched to the shortest cache window sp-mod.com serves: its maintainer
# confirmed every endpoint carries at least 15 minutes of cached data, so
# checking faster than this spends requests on bytes that cannot have changed.
# Polling every 5 minutes made two of every three checks pure waste, against a
# host that had already turned on bot countermeasures once.
CHECK_INTERVAL_MINUTES = 15
MAX_PER_CATEGORY = 7
THUMB_SIZE = (52, 52)
STATE_FIELDS = (
    "title", "link", "author", "author_id", "version", "category", "published", "updated",
)
DISPLAY_FIELDS = (
    "title", "link", "author", "author_id", "version", "category", "thumb_url", "description",
    "full_description", "changelog", "author_since",
)
THUMB_MAX_AGE_DAYS = 3
NEW_AUTHOR_DAYS = 60
TOP_STATS_WINDOW_DAYS = 30
TREND_WINDOW_DAYS = 30

# ── Endorsing (dormant) ────────────────────────────────────────────────

# Off, and deliberately still here rather than deleted.
#
# Endorsing is a per-user action, so it needs the Forge to know who is asking.
# The v0 API cannot: it is documented as "publicly accessible and requires no
# authentication or API key", every route is a GET, and /api/v0/mod/{id}/endorse
# answers 404 rather than the 405 a POST-only route would give. The only way to
# do it today would be driving the website with the user's own login, which is
# not something to build into a third-party download.
#
# So the button was taken out of the UI, not the code. Everything behind it
# still works -- the glyph, the per-mod state, the plumbing through to
# save_state -- and it currently opens the mod's Forge page rather than
# endorsing directly. If the Forge ever ships user API tokens, flip this to
# True and replace ModCard._endorse's webbrowser.open with the real call.
ENDORSE_ENABLED = False

# ── Local mod scan (opt-in) ──────────────────────────────────────────────

# Client plugins sit at the game root in every version -- deliberately *not*
# inside the server folder. SPT 4.1's own mod manager had to fix exactly that
# mistake: plugins placed under SPT_Runtime/BepInEx/plugins are somewhere the
# game never looks.
BEPINEX_PLUGINS_SUBPATH = "BepInEx/plugins"
# SPT 4.1 renamed the server folder from "SPT" to "SPT_Runtime", so a 4.1
# install has its server mods somewhere 4.0's layout doesn't describe. Both are
# checked, newest naming first, since a machine can hold either -- and one
# upgraded in place can still have the old folder sitting alongside the new.
SERVER_DIR_NAMES = ("SPT_Runtime", "SPT")
SERVER_MODS_RELATIVE = "user/mods"
SERVER_MODS_SUBPATHS = tuple(f"{d}/{SERVER_MODS_RELATIVE}" for d in SERVER_DIR_NAMES)
# Pre-v4 installs kept server mods at the game root. Note a stray root-level
# user/mods is *also* the fingerprint of the 4.1 mod-manager bug, where mods
# were written to a folder the game never reads -- so this is matched only on
# the old package.json manifest shape, never on v4-style DLLs, to avoid
# reporting mods that are installed but dead.
LEGACY_SERVER_MODS_SUBPATH = "user/mods"
FUZZY_MATCH_THRESHOLD = 0.82
# Every SPT install ships with its own core components (SPT.Common,
# SPT.Reflection, etc.) sitting in the same folder shapes as real mods --
# these were never published to the Forge, so every user would otherwise see
# them show up as permanent "Not Found on Forge" clutter.
CORE_SPT_NAME_PREFIX = "spt."
# Standalone .NET helper (see modreader/) that reads real mod metadata via
# actual CLR reflection -- built separately via `dotnet publish` and copied
# into assets/, not built by PyInstaller itself.
MODREADER_EXE = ASSETS_DIR / "ModReader.exe"
MODREADER_TIMEOUT_SECONDS = 120
# Used to read the installed SPT server version straight from the exe's own
# Windows version resource -- the only reliable source, since it's not
# recorded in any plain-text config file on disk.
SPT_SERVER_EXE_NAME = "SPT.Server.exe"
SPT_SERVER_EXE_SUBPATHS = tuple(f"{d}/{SPT_SERVER_EXE_NAME}" for d in SERVER_DIR_NAMES)
# GET /api/v0/mods/updates takes a comma-separated `mods` list with no
# documented cap -- chunk defensively so a large install never risks the
# request line getting rejected/truncated by a proxy or server limit.
MODS_UPDATES_CHUNK_SIZE = 40
# Batched published-status lookups are bounded by the API's documented
# per_page maximum of 50.
PUBLISHED_CHUNK_SIZE = 50

# ── Window ─────────────────────────────────────────────────────────────

# The window opens at this width and is then sized to fit vertically (see
# _size_to_fit). Chosen as the tightest width at which a mod title and its
# author/category line still survive without ellipsis on most cards -- below
# roughly this, titles start losing words and the category collapses to a
# lone ellipsis. Raised at runtime if the header needs more room than this,
# which it does at higher display scaling.
WINDOW_DEFAULT_WIDTH = 720
# Only the height here is provisional: it is on screen for the moment between
# the window appearing and _size_to_fit measuring the real chrome.
WINDOW_DEFAULT_GEOMETRY = f"{WINDOW_DEFAULT_WIDTH}x680"

# Floor for manual resizing, not a recommendation -- the real minimum is
# measured from the header at runtime, since that is the one part of the
# window with a hard horizontal requirement. Both are deliberately below the
# default: the columns scroll and the text ellipsizes, so someone who wants a
# small window on a small screen can have one.
WINDOW_MIN_WIDTH = 600
WINDOW_MIN_HEIGHT = 420

# Bumped whenever the card layout changes enough that a window size saved by an
# older build no longer shows a full column. A geometry stored under a
# different value is discarded once and re-fitted, so an existing user is not
# left scrolling after an update that made the cards taller.
LAYOUT_VERSION = 2

# ── Windows registry ──────────────────────────────────────────────────

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "SPTModChecker"

# ── Colors ─────────────────────────────────────────────────────────────

# Surfaces run darkest (window chrome) to lightest (a hovered card), and every
# boundary in the UI is drawn with that value step rather than an outline -- a
# dense list then reads as a stack of panels instead of a grid of boxes.
BG = "#101219"            # window background, behind the cards
STATUS_BG = "#0b0d13"     # recessed chrome: header bar, status bar, popup bars
CARD_BG = "#1a1d27"       # raised surface: cards, inputs, popup panels
CARD_HOVER = "#262b3a"    # the same surface under the cursor
SEPARATOR = "#262b38"     # hairline rules
BORDER = "#2f3545"        # outline on interactive chrome (buttons, inputs)

TEXT_BRIGHT = "#eef0f6"   # titles and primary numbers
TEXT = "#bcc2d2"          # body copy and button labels
TEXT_DIM = "#828ba1"      # metadata, descriptions
TEXT_FAINT = "#5b6377"    # rank numbers, disabled labels

# Semantic accents. Green/amber are load-bearing (new vs updated) and are kept
# clear of every category hue below so a card's rail can never be mistaken for
# one of them.
ACCENT_NEW = "#4ad07f"
ACCENT_UPD = "#f2a53c"
ACCENT_NEW_AUTHOR = "#ef5a92"
ACCENT_DANGER = "#ef5350"

# Card accent color by mod category -- matches Forge's own category list.
# "Other" and any unrecognized category fall back to CATEGORY_COLOR_DEFAULT.
# Hues are deliberately spread >=15 degrees apart (computed, not eyeballed) and
# kept >=20 degrees clear of the reserved green/orange/pink accents above, so
# adjacent categories stay visually distinguishable rather than blurring together.
# The card border still carries this color, but as a 1px rounded stroke with a
# thicker rail down the left edge rather than the flat 2px rectangle it used to
# be -- same signal, without a full column reading as a wall of boxes.
CATEGORY_COLORS = {
    "Weapons": "#cf4b3f",
    "Overhauls": "#d9d568",
    "Hideout": "#3fcf85",
    "Tools": "#68d9bb",
    "Audio": "#3fcfcf",
    "Locations": "#68bdd9",
    "Scripting": "#3f85cf",
    "Locales": "#6882d9",
    "Quests": "#443fcf",
    "Items": "#8868d9",
    "Clothing": "#8e3fcf",
    "Retextures": "#c368d9",
    "Models": "#cf3fc5",
    # Deliberately desaturated/neutral, distinct from the vibrant hues above --
    # also frees up more spacing between the remaining 13 colorful categories.
    "Bots": "#8d6e63",
    "Traders": "#78909c",
    "Equipment": "#9e9e9e",
}
CATEGORY_COLOR_DEFAULT = "#4a5266"

# ── Metrics ────────────────────────────────────────────────────────────

# One spacing scale for the whole UI. Every pad/gap is a multiple of it, which
# is what stops a hand-tuned layout from drifting into a dozen near-identical
# 3/4/5px values that read as sloppy rather than deliberate.
SPACE = 4
CARD_RADIUS = 8
PILL_RADIUS = 999   # clamped to half the height -- i.e. a full stadium
