import html
import re
import xml.etree.ElementTree as ET

import requests

from .config import API_URL, DC_NS, FEED_URL, FEED_UPDATED_URL

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


def fetch_feeds():
    """Fetch newest and recently updated mods from RSS feeds + API."""
    api_updated = _fetch_api_mods(sort="-updated_at")
    api_newest = _fetch_api_mods(sort="-created_at")

    newest = _extract_mods(_parse_rss(FEED_URL))
    rss_updated = _extract_mods(_parse_rss(FEED_UPDATED_URL))

    # Build changelog + full_description lookup from both API sets
    all_api = {m["link"]: m for m in api_updated + api_newest}
    changelogs = {link: m["changelog"] for link, m in all_api.items() if m.get("changelog")}
    full_descs = {link: m["full_description"] for link, m in all_api.items() if m.get("full_description")}

    # Enrich new mods with API changelogs
    for mod in newest:
        mod["changelog"] = mod.get("changelog") or changelogs.get(mod["link"], "")
        mod["full_description"] = mod.get("full_description") or full_descs.get(mod["link"], "")

    # Combine RSS + API for updated column, deduplicate, sort by version created_at
    seen = set()
    combined = []
    for mod in rss_updated + api_updated:
        if mod["link"] not in seen:
            seen.add(mod["link"])
            combined.append(mod)
    for mod in combined:
        mod["changelog"] = mod.get("changelog") or changelogs.get(mod["link"], "")
        mod["full_description"] = mod.get("full_description") or full_descs.get(mod["link"], "")
    combined.sort(key=lambda m: m.get("updated", ""), reverse=True)

    return newest, combined
