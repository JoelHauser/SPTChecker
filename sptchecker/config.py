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

# ── Feed ───────────────────────────────────────────────────────────────

FEED_URL = "https://forge.sp-tarkov.com/mods/rss"
FEED_UPDATED_URL = "https://forge.sp-tarkov.com/mods/rss?sort=updated"
API_URL = "https://forge.sp-tarkov.com/api/v0/mods"
API_MOD_URL = "https://forge.sp-tarkov.com/api/v0/mod"
FORGE_URL = "https://forge.sp-tarkov.com/mods"
FORGE_USER_URL = "https://forge.sp-tarkov.com/user"
DC_NS = "http://purl.org/dc/elements/1.1/"

# ── Behaviour ──────────────────────────────────────────────────────────

CHECK_INTERVAL_MINUTES = 5
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

# ── Local mod scan (opt-in) ──────────────────────────────────────────────

BEPINEX_PLUGINS_SUBPATH = "BepInEx/plugins"
SERVER_MODS_SUBPATH = "SPT/user/mods"
LEGACY_SERVER_MODS_SUBPATH = "user/mods"
FUZZY_MATCH_THRESHOLD = 0.82
# Standalone .NET helper (see modreader/) that reads real mod metadata via
# actual CLR reflection -- built separately via `dotnet publish` and copied
# into assets/, not built by PyInstaller itself.
MODREADER_EXE = ASSETS_DIR / "ModReader.exe"
MODREADER_TIMEOUT_SECONDS = 120

# ── Window ─────────────────────────────────────────────────────────────

WINDOW_DEFAULT_GEOMETRY = "780x600"
WINDOW_MIN_WIDTH = 700
WINDOW_MIN_HEIGHT = 500

# ── Windows registry ──────────────────────────────────────────────────

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "SPTModChecker"

# ── Colors ─────────────────────────────────────────────────────────────

BG = "#1a1a24"
CARD_BG = "#252535"
CARD_HOVER = "#30304a"
TEXT = "#ccccdd"
TEXT_DIM = "#777799"
TEXT_BRIGHT = "#eeeef4"
ACCENT_NEW = "#4caf50"
ACCENT_UPD = "#ffa726"
ACCENT_NEW_AUTHOR = "#e91e63"
STATUS_BG = "#14141c"
SEPARATOR = "#333348"

# Card border color by mod category -- matches Forge's own category list.
# "Other" and any unrecognized category fall back to CATEGORY_COLOR_DEFAULT.
# Hues are deliberately spread >=15 degrees apart (computed, not eyeballed) and
# kept >=20 degrees clear of the reserved green/orange/pink accents above, so
# adjacent categories stay visually distinguishable rather than blurring together.
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
CATEGORY_COLOR_DEFAULT = SEPARATOR
