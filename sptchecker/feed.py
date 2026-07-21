import html
import re
import time
import xml.etree.ElementTree as ET

import requests

from .config import API_MOD_URL, API_URL, DC_NS, FEED_URL, FEED_UPDATED_URL

_MOD_ID_RE = re.compile(r"/mod/(\d+)/")

_session = requests.Session()
_session.headers["User-Agent"] = "SPTModChecker/2.3.0"

_API_HEADERS = {"Accept": "application/json"}


def get_session():
    return _session


def _get_with_backoff(url, params, timeout, retries=3):
    """GET with 429 (rate limit) backoff -- the local-mod scan can fire a
    couple hundred lookups in a burst, comfortably over the Forge API's
    per-window limit, and a silently-swallowed 429 looks identical to a
    real "not found" to the caller (matcher.py) without this."""
    resp = _session.get(url, headers=_API_HEADERS, params=params, timeout=timeout)
    for _ in range(retries):
        if resp.status_code != 429:
            break
        wait = float(resp.headers.get("Retry-After", 2))
        time.sleep(wait)
        resp = _session.get(url, headers=_API_HEADERS, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


CHANGELOG_MAX_CHARS = 5000


def _truncate(text, limit):
    """Truncate at a word boundary near `limit` to avoid cutting mid-markdown-token."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit - 50:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _parse_api_mod(item):
    """Map a raw /api/v0/mods item (as returned with include=versions,category) to
    this app's internal mod dict shape."""
    versions = item.get("versions", [])
    latest = versions[0] if versions else {}
    owner = item.get("owner") or {}
    category = item.get("category") or {}

    return {
        "title": item.get("name", ""),
        "slug": item.get("slug", ""),
        "link": item.get("detail_url", ""),
        "guid": item.get("guid", ""),
        "author": owner.get("name", "Unknown"),
        "author_id": owner.get("id", ""),
        "author_since": owner.get("created_at", ""),
        "version": latest.get("version", ""),
        "category": category.get("title", ""),
        "published": item.get("published_at", ""),
        "updated": latest.get("created_at", item.get("updated_at", "")),
        "thumb_url": item.get("thumbnail", ""),
        "description": (item.get("teaser", "") or "")[:300],
        "full_description": item.get("teaser", "") or "",
        "changelog": _truncate(latest.get("description", "") or "", CHANGELOG_MAX_CHARS),
    }


def _fetch_api_mods(sort="-updated_at"):
    """Fetch mods from the API with the given sort order."""
    try:
        resp = _session.get(API_URL, headers=_API_HEADERS, params={
            "include": "versions,category",
            "sort": sort,
            "per_page": 50,
        }, timeout=30)
        resp.raise_for_status()
        return [_parse_api_mod(item) for item in resp.json().get("data", [])]
    except Exception:
        return []


def lookup_by_guid(guid):
    """Exact-match a locally-scanned mod's GUID against the Forge catalog.

    Forge exposes `guid` as a filterable field (confirmed live against
    filter[guid]=<value>), so this is a single indexed lookup rather than a
    search -- the primary local-mod matching strategy. Returns None on no
    match, ambiguous results, or any request failure.
    """
    if not guid:
        return None
    try:
        resp = _get_with_backoff(API_URL, {
            "include": "versions,category",
            "filter[guid]": guid,
        }, timeout=15)
        data = resp.json().get("data", [])
        if len(data) != 1 or data[0].get("guid") != guid:
            return None
        return _parse_api_mod(data[0])
    except Exception:
        return None


def lookup_by_name(name):
    """Fallback search by name for local mods that yielded no clean GUID match.

    filter[name] does a partial/contains-style match server-side, so callers
    should rank the results themselves rather than assume the first is right.
    """
    if not name:
        return []
    try:
        resp = _get_with_backoff(API_URL, {
            "include": "versions,category",
            "filter[name]": name,
            "per_page": 20,
        }, timeout=15)
        return [_parse_api_mod(item) for item in resp.json().get("data", [])]
    except Exception:
        return []


def lookup_by_query(term):
    """Fuzzy/full-text search, for local mods filter[name] can't find.

    filter[name] only matches a term that's a literal substring of the
    stored title -- it can't bridge a local mod's internal name (often
    camelCase/dotted developer shorthand) to a differently-worded Forge
    listing. `query=` is a separate, undocumented parameter (found by
    reading Refringe's Check Mods CLI source, confirmed live) that does
    real fuzzy matching instead, at the cost of returning looser candidates
    -- callers still need to rank results themselves, same as lookup_by_name.
    """
    if not term:
        return []
    try:
        resp = _get_with_backoff(API_URL, {
            "include": "versions,category",
            "query": term,
            "per_page": 20,
        }, timeout=15)
        return [_parse_api_mod(item) for item in resp.json().get("data", [])]
    except Exception:
        return []


def _parse_rss(url):
    """Parse an RSS feed and return ET root."""
    resp = _session.get(url, timeout=30)
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _extract_mods(root):
    """Extract mod dicts from parsed RSS XML."""
    mods = []
    for item in root.findall(".//item"):
        link = item.findtext("link", "")
        if not link:
            continue
        pub = item.findtext("pubDate", "")
        enc = item.find("enclosure")
        thumb = enc.get("url", "") if enc is not None else ""
        desc_raw = item.findtext("description", "")
        full_desc = strip_html(desc_raw)

        mods.append({
            "title": item.findtext("title", ""),
            "link": link,
            "author": item.findtext(f"{{{DC_NS}}}creator", "Unknown"),
            "version": item.findtext(f"{{{DC_NS}}}identifier", ""),
            "category": item.findtext("category", ""),
            "published": pub,
            "updated": item.findtext(f"{{{DC_NS}}}date", pub),
            "thumb_url": thumb,
            "description": full_desc[:300],
            "full_description": full_desc,
        })
    return mods


def check_mod_published(url):
    """Return True if the mod page is still reachable (not unpublished)."""
    try:
        resp = _session.head(url, timeout=10, allow_redirects=True)
        return resp.status_code == 200
    except Exception:
        return True


def fetch_author_id(mod_link):
    """On-demand lookup of a mod's owner id from its page link.

    Used to backfill an author's Forge profile id when it wasn't captured during
    a regular check -- e.g. an older mod that hasn't cycled back through the
    "most recent" API window since author_id started being tracked. Returns None
    on any failure (bad link, network error, missing owner).
    """
    m = _MOD_ID_RE.search(mod_link)
    if not m:
        return None
    try:
        resp = _session.get(f"{API_MOD_URL}/{m.group(1)}", headers=_API_HEADERS, timeout=15)
        resp.raise_for_status()
        owner = resp.json().get("data", {}).get("owner") or {}
        return owner.get("id")
    except Exception:
        return None


def fetch_feeds():
    """Fetch newest and recently updated mods from RSS feeds + API."""
    api_updated = _fetch_api_mods(sort="-updated_at")
    api_newest = _fetch_api_mods(sort="-created_at")

    newest = _extract_mods(_parse_rss(FEED_URL))
    rss_updated = _extract_mods(_parse_rss(FEED_UPDATED_URL))

    # RSS entries lack several API-only fields -- build per-link lookups from the
    # API sets and enrich both columns, whichever source each entry came from.
    all_api = {m["link"]: m for m in api_updated + api_newest}
    enrich_fields = ("changelog", "full_description", "author_since", "author_id")
    lookups = {
        field: {link: m[field] for link, m in all_api.items() if m.get(field)}
        for field in enrich_fields
    }

    def _enrich(mod):
        for field in enrich_fields:
            mod[field] = mod.get(field) or lookups[field].get(mod["link"], "")

    for mod in newest:
        _enrich(mod)

    # Combine RSS + API for updated column, deduplicate, sort by version created_at
    seen = set()
    combined = []
    for mod in rss_updated + api_updated:
        if mod["link"] not in seen:
            seen.add(mod["link"])
            combined.append(mod)
    for mod in combined:
        _enrich(mod)
    combined.sort(key=lambda m: m.get("updated", ""), reverse=True)

    return newest, combined
