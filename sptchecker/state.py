import hashlib
import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image, ImageDraw, ImageOps

from .config import (
    CACHE_DIR, CARD_BG, DATA_DIR, HOST_MIGRATIONS, MIGRATED_URL_FIELDS,
    SEPARATOR, STATE_FILE, TEXT_DIM, THUMB_MAX_AGE_DAYS, THUMB_SIZE,
    TOP_STATS_WINDOW_DAYS, TREND_WINDOW_DAYS,
)
from .feed import media_request
from .utils import parse_dt


def _migrated_url(url):
    """Swap a dead old-Forge host for its sp-mod.com replacement, leaving the
    rest of the URL (and any non-Forge URL) untouched."""
    if not isinstance(url, str):
        return url
    for old, new in HOST_MIGRATIONS.items():
        prefix = f"//{old}/"
        if prefix in url:
            return url.replace(prefix, f"//{new}/", 1)
    return url


def _migrate_urls(obj):
    """Rewrite every known URL-bearing field in a nested state structure,
    in place. Returns True if anything changed. Deliberately keyed on field
    name rather than a blind whole-file string replace: free-text fields
    like changelogs carry author-written links, and silently rewriting
    someone's prose is not this function's job."""
    changed = False
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in MIGRATED_URL_FIELDS:
                migrated = _migrated_url(value)
                if migrated != value:
                    obj[key] = migrated
                    changed = True
            else:
                changed |= _migrate_urls(value)
    elif isinstance(obj, list):
        for item in obj:
            changed |= _migrate_urls(item)
    return changed


def _migrate_state_hosts(state):
    """Move a saved state off the retired forge.sp-tarkov.com hosts. The mods
    dict is keyed by mod link, so its keys get rewritten too -- otherwise the
    next check would treat every known mod as newly discovered. Returns True
    if the state changed and should be persisted."""
    changed = _migrate_urls(state)

    mods = state.get("mods")
    if isinstance(mods, dict):
        remapped = {}
        for link, mod in mods.items():
            # A pre-migration duplicate of an already-migrated entry would
            # collide here; keep whichever was stored first rather than
            # letting a stale copy overwrite a current one.
            remapped.setdefault(_migrated_url(link), mod)
        if remapped != mods:
            state["mods"] = remapped
            changed = True

    return changed


def _salvage_state(raw):
    """Recover what's usable from a state file that won't parse, or None.

    The corruption this exists for is two overlapping writes, which leave a
    complete JSON document with the tail of a longer one stuck on the end
    ("Extra data: line 1 column N"). That leading document is a real
    snapshot, so decoding just the prefix hands the user their whole tracked
    history back rather than resetting them to nothing.
    """
    try:
        salvaged, _end = json.JSONDecoder().raw_decode(raw)
    except ValueError:
        return None
    return salvaged if isinstance(salvaged, dict) else None


def _quarantine_state_file():
    """Set an unparseable state file aside instead of deleting it -- it's the
    only copy of that history, and keeping one makes the failure diagnosable
    if it ever recurs. A fixed name, so repeated failures can't pile up."""
    try:
        os.replace(STATE_FILE, STATE_FILE.with_name(STATE_FILE.stem + ".corrupt.json"))
    except OSError:
        pass


def load_state():
    if not STATE_FILE.exists():
        return {"mods": {}}
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return {"mods": {}}

    try:
        state = json.loads(raw)
    except ValueError:
        # A malformed state file used to raise straight out of here, which
        # happens during __init__ before any window exists -- so the app died
        # on launch with a raw traceback, and since state lives under
        # LOCALAPPDATA rather than next to the exe, reinstalling didn't help.
        # Recover instead. Starting clean is safe when nothing is salvageable:
        # an empty history reads as a first run, which notifies about nothing.
        state = _salvage_state(raw)
        _quarantine_state_file()
        if state is None:
            return {"mods": {}}
        save_state(state)

    if not isinstance(state, dict):
        return {"mods": {}}
    if _migrate_state_hosts(state):
        # Persist right away so the rewrite is a one-time cost that
        # survives even if the app is closed before its first check.
        save_state(state)
    return state


# The periodic check, the local scan and the main thread all persist state,
# from three different threads. Path.write_text truncates before it writes and
# holds no lock, so two overlapping writers left one complete document with the
# tail of a longer one after it -- a file that then refused to parse and took
# the whole app down on next launch.
_save_lock = threading.Lock()


def save_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    payload = json.dumps(state, separators=(",", ":"))
    with _save_lock:
        # Write alongside the target, then rename: os.replace is atomic, so an
        # interrupted write (crash, power cut, or the app being killed) leaves
        # the previous state whole instead of a half-written file. The temp
        # file sits in the same directory deliberately -- os.replace is only
        # atomic within a single filesystem.
        tmp = STATE_FILE.with_name(STATE_FILE.stem + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, STATE_FILE)


# Bumped when the rendering below changes, so caches written by an older
# version aren't reused -- entries are keyed by URL alone, which stays the
# same even though the image we derive from it does not. Orphaned entries
# from a previous generation age out via purge_old_thumbs.
_THUMB_CACHE_GEN = 3


def _thumb_path(url):
    key = hashlib.md5(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{key}-{_THUMB_CACHE_GEN}.jpg"


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
        r = media_request("get", url, timeout=15)
        r.raise_for_status()
        # RGBA, not RGB: a straight convert("RGB") discards the alpha channel
        # and exposes whatever RGB sits underneath the transparent pixels,
        # which is arbitrary leftover data rather than anything the author
        # meant to be seen -- one Forge thumbnail hides an entire unrelated
        # landscape back there. Browsers composite against the page instead,
        # which is why those mods look right on the Forge and wrong here.
        # Passing the image as its own paste mask composites it onto the card
        # colour the same way; fully opaque images have an all-255 mask and
        # are unaffected.
        img = Image.open(BytesIO(r.content)).convert("RGBA")
        flat = Image.new("RGB", img.size, CARD_BG)
        flat.paste(img, (0, 0), img)
        # Scale-and-crop to fill the square rather than fitting the whole image
        # inside it. Forge thumbnails are wide banners, so fitting them left a
        # thin strip stranded in the middle of a mostly empty tile; filling
        # gives every card the same solid block of art to lead with. The corner
        # rounding is applied at display time, not baked in here, so the cache
        # stays a plain tile and the card can round it against whatever surface
        # it is currently painted.
        canvas = ImageOps.fit(flat, THUMB_SIZE, Image.LANCZOS, centering=(0.5, 0.5))
        canvas.save(cached, "JPEG", quality=88)
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
_placeholder_img = None


def placeholder_thumb():
    """Render the same wireframe-cube "no thumbnail" placeholder the Forge
    website shows, via PIL supersample + LANCZOS downscale for anti-aliasing
    (Tk/raw-bitmap primitives look jagged at this size otherwise). The
    output is deterministic and this is called per mod-without-thumbnail on
    every check cycle, so render once and reuse."""
    global _placeholder_img
    if _placeholder_img is not None:
        return _placeholder_img
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

    _placeholder_img = img.resize((w, h), Image.LANCZOS)
    return _placeholder_img


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
