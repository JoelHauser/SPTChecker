import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from .config import (
    CACHE_DIR, CARD_BG, DATA_DIR, STATE_FILE, THUMB_MAX_AGE_DAYS, THUMB_SIZE,
    TOP_STATS_WINDOW_DAYS,
)
from .feed import get_session
from .utils import parse_dt


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"mods": {}}


def save_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")


def _thumb_path(url):
    key = hashlib.md5(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{key}.jpg"


def download_thumb(url):
    if not url:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _thumb_path(url)

    if cached.exists():
        try:
            return Image.open(cached).copy()
        except Exception:
            cached.unlink(missing_ok=True)

    try:
        r = get_session().get(url, timeout=15)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
        canvas = Image.new("RGB", THUMB_SIZE, CARD_BG)
        x = (THUMB_SIZE[0] - img.width) // 2
        y = (THUMB_SIZE[1] - img.height) // 2
        canvas.paste(img, (x, y))
        canvas.save(cached, "JPEG", quality=85)
        return canvas
    except Exception:
        return None


def placeholder_thumb():
    return Image.new("RGB", THUMB_SIZE, "#333348")


def purge_old_thumbs():
    if not CACHE_DIR.exists():
        return
    max_age = THUMB_MAX_AGE_DAYS * 86400
    now = time.time()
    for f in CACHE_DIR.iterdir():
        try:
            if now - f.stat().st_mtime > max_age:
                f.unlink()
        except OSError:
            pass


def compute_stats(mods):
    """Summarize the full mod-tracking history (self.state["mods"]) for the stats view.

    "Top authors" and "top categories" are both rolling windows (TOP_STATS_WINDOW_DAYS),
    not all-time -- recomputed fresh from each mod's published date every call, so
    activity ages out day by day rather than needing a stored counter that gets reset
    on a schedule.
    """
    author_counts = Counter()
    author_ids = {}
    author_links = {}
    category_counts = Counter()
    added_this_week = 0
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    window_start = now - timedelta(days=TOP_STATS_WINDOW_DAYS)

    for mod in mods.values():
        author = mod.get("author") or "Unknown"
        published = parse_dt(mod.get("published", ""))
        in_window = published and published >= window_start
        if in_window:
            author_counts[author] += 1
        if mod.get("author_id"):
            author_ids[author] = mod["author_id"]
        elif mod.get("link"):
            # Fallback so the UI can look up the id on demand (e.g. on click) for
            # authors whose id wasn't captured during a regular feed check.
            author_links.setdefault(author, mod["link"])
        category = mod.get("category")
        if category and in_window:
            category_counts[category] += 1
        if published and published >= week_ago:
            added_this_week += 1

    return {
        "total": len(mods),
        "added_this_week": added_this_week,
        "top_authors": author_counts.most_common(5),
        "author_ids": author_ids,
        "author_links": author_links,
        "top_categories": category_counts.most_common(5),
    }
