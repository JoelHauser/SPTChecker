import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image, ImageDraw

from .config import (
    CACHE_DIR, CARD_BG, DATA_DIR, SEPARATOR, STATE_FILE, TEXT_DIM,
    THUMB_MAX_AGE_DAYS, THUMB_SIZE, TOP_STATS_WINDOW_DAYS, TREND_WINDOW_DAYS,
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


# Traced from the Forge website's own "no thumbnail uploaded" placeholder --
# a Heroicons cube-transparent outline (24x24 viewBox) shown on a plain panel
# when a mod's author didn't upload a thumbnail -- so ours matches theirs
# instead of showing an empty block.
_PLACEHOLDER_ICON_VIEWBOX = 24
_PLACEHOLDER_ICON_SEGMENTS = [
    ((21, 7.5), (18.75, 6.187)),
    ((21, 7.5), (21, 9.75)),
    ((21, 7.5), (18.75, 8.813)),
    ((3, 7.5), (5.25, 6.187)),
    ((3, 7.5), (5.25, 8.813)),
    ((3, 7.5), (3, 9.75)),
    ((12, 12.75), (14.25, 11.437)),
    ((12, 12.75), (9.75, 11.437)),
    ((12, 12.75), (12, 15)),
    ((12, 21.75), (14.25, 20.437)),
    ((12, 21.75), (12, 19.5)),
    ((12, 21.75), (9.75, 20.437)),
    ((9.75, 3.562), (12, 2.25)),
    ((12, 2.25), (14.25, 3.563)),
    ((21, 14.25), (21, 16.5)),
    ((21, 16.5), (18.75, 17.813)),
    ((5.25, 17.813), (3, 16.5)),
    ((3, 16.5), (3, 14.25)),
]
_PLACEHOLDER_SUPERSAMPLE = 4


def placeholder_thumb():
    """Render the same wireframe-cube "no thumbnail" placeholder the Forge
    website shows, via PIL supersample + LANCZOS downscale for anti-aliasing
    (Tk/raw-bitmap primitives look jagged at this size otherwise)."""
    w, h = THUMB_SIZE
    big_w, big_h = w * _PLACEHOLDER_SUPERSAMPLE, h * _PLACEHOLDER_SUPERSAMPLE
    img = Image.new("RGB", (big_w, big_h), SEPARATOR)
    draw = ImageDraw.Draw(img)

    icon_px = min(big_w, big_h) * 0.55
    scale = icon_px / _PLACEHOLDER_ICON_VIEWBOX
    off_x = (big_w - icon_px) / 2
    off_y = (big_h - icon_px) / 2
    line_width = max(1, round(_PLACEHOLDER_SUPERSAMPLE * 1.4))
    r = line_width / 2

    def to_px(pt):
        x, y = pt
        return (off_x + x * scale, off_y + y * scale)

    for start, end in _PLACEHOLDER_ICON_SEGMENTS:
        p0, p1 = to_px(start), to_px(end)
        draw.line([p0, p1], fill=TEXT_DIM, width=line_width)
        for px, py in (p0, p1):
            draw.ellipse([px - r, py - r, px + r, py + r], fill=TEXT_DIM)

    return img.resize((w, h), Image.LANCZOS)


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
    today = now.date()
    week_ago = now - timedelta(days=7)
    window_start = now - timedelta(days=TOP_STATS_WINDOW_DAYS)
    # Oldest day first, today last -- daily_counts[-1] is always today's count.
    daily_counts = [0] * TREND_WINDOW_DAYS
    daily_dates = [
        (today - timedelta(days=TREND_WINDOW_DAYS - 1 - i)).isoformat()
        for i in range(TREND_WINDOW_DAYS)
    ]

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
        if published:
            age_days = (today - published.date()).days
            if 0 <= age_days < TREND_WINDOW_DAYS:
                daily_counts[TREND_WINDOW_DAYS - 1 - age_days] += 1

    return {
        "total": len(mods),
        "added_this_week": added_this_week,
        "top_authors": author_counts.most_common(5),
        "author_ids": author_ids,
        "author_links": author_links,
        "top_categories": category_counts.most_common(5),
        "daily_counts": daily_counts,
        "daily_dates": daily_dates,
    }
