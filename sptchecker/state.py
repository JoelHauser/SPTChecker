import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO

from PIL import Image

from .config import CACHE_DIR, CARD_BG, DATA_DIR, STATE_FILE, THUMB_MAX_AGE_DAYS, THUMB_SIZE
from .feed import get_session


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


def _parse_dt(ts_str):
    """Parse an ISO or RFC 2822 timestamp (RSS vs API formats) into an aware datetime."""
    if not ts_str:
        return None
    try:
        try:
            dt = parsedate_to_datetime(ts_str)
        except Exception:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def compute_stats(mods):
    """Summarize the full mod-tracking history (self.state["mods"]) for the stats view."""
    author_counts = Counter()
    category_counts = Counter()
    added_this_week = 0
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    for mod in mods.values():
        author_counts[mod.get("author") or "Unknown"] += 1
        category = mod.get("category")
        if category:
            category_counts[category] += 1
        published = _parse_dt(mod.get("published", ""))
        if published and published >= week_ago:
            added_this_week += 1

    return {
        "total": len(mods),
        "added_this_week": added_this_week,
        "top_authors": author_counts.most_common(5),
        "top_categories": category_counts.most_common(5),
    }
