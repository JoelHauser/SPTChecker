import html
import re
import xml.etree.ElementTree as ET

import requests

from .config import API_MOD_URL, API_URL, DC_NS, FEED_URL, FEED_UPDATED_URL

_MOD_ID_RE = re.compile(r"/mod/(\d+)/")

_session = requests.Session()
_session.headers["User-Agent"] = "SPTModChecker/2.0"

_API_HEADERS = {"Accept": "application/json"}


def get_session():
    return _session


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


def _fetch_api_mods(sort="-updated_at"):
    """Fetch mods from the API with the given sort order."""
    try:
        resp = _session.get(API_URL, headers=_API_HEADERS, params={
            "include": "versions,category",
            "sort": sort,
            "per_page": 50,
        }, timeout=30)
        resp.raise_for_status()

        mods = []
        for item in resp.json().get("data", []):
            versions = item.get("versions", [])
            latest = versions[0] if versions else {}
            owner = item.get("owner") or {}
            category = item.get("category") or {}

            mods.append({
                "title": item.get("name", ""),
                "link": item.get("detail_url", ""),
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
            })
        return mods
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
