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


def _fetch_api_mods():
    """Fetch mods from API for the updated column."""
    try:
        resp = _session.get(API_URL, headers=_API_HEADERS, params={
            "include": "versions,category",
            "sort": "-updated_at",
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

        mods.append({
            "title": item.findtext("title", ""),
            "link": link,
            "author": item.findtext(f"{{{DC_NS}}}creator", "Unknown"),
            "version": item.findtext(f"{{{DC_NS}}}identifier", ""),
            "category": item.findtext("category", ""),
            "published": pub,
            "updated": item.findtext(f"{{{DC_NS}}}date", pub),
            "thumb_url": thumb,
            "description": strip_html(desc_raw)[:300],
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
    api_mods = _fetch_api_mods()

    newest = _extract_mods(_parse_rss(FEED_URL))

    rss_updated = _extract_mods(_parse_rss(FEED_UPDATED_URL))

    # Combine RSS + API for updated column, deduplicate, sort by version created_at
    seen = set()
    combined = []
    for mod in rss_updated + api_mods:
        if mod["link"] not in seen:
            seen.add(mod["link"])
            combined.append(mod)
    combined.sort(key=lambda m: m.get("updated", ""), reverse=True)

    return newest, combined
