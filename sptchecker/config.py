import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────

import os

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
FORGE_URL = "https://forge.sp-tarkov.com/mods"
DC_NS = "http://purl.org/dc/elements/1.1/"

# ── Behaviour ──────────────────────────────────────────────────────────

CHECK_INTERVAL_SEC = 30 * 60
MAX_PER_CATEGORY = 6
THUMB_SIZE = (52, 52)
STATE_FIELDS = ("title", "link", "author", "version", "category", "published", "updated")
DISPLAY_FIELDS = ("title", "link", "author", "version", "category", "thumb_url", "description", "spt_version")
THUMB_MAX_AGE_DAYS = 3

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
STATUS_BG = "#14141c"
SEPARATOR = "#333348"
BADGE_BG = "#1a3a4a"
BADGE_FG = "#7bbfcc"
