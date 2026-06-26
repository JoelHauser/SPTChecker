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


def _fetch_spt_versions():
    try:
        resp = _session.get(API_URL, headers=_API_HEADERS, params={
            "include": "versions",
            "sort": "-published_at",
            "per_page": 50,
        }, timeout=30)
        resp.raise_for_status()
        version_map = {}
        for item in resp.json().get("data", []):
            url = item.get("detail_url", "")
            versions = item.get("versions", [])
            if url and versions:
                spt = versions[0].get("spt_version_constraint", "").strip()
                if spt.startswith("~"):
                    spt = spt[1:]
                if spt:
                    version_map[url] = spt
        return version_map
    except Exception:
        return {}


def _parse_rss(url, version_map):
    """Parse an RSS feed and return ET root."""
    resp = _session.get(url, timeout=30)
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _extract_mods(root, version_map):
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
            "spt_version": version_map.get(link, ""),
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
    """Fetch newest and recently updated mods from RSS feeds."""
    version_map = _fetch_spt_versions()

    newest_root = _parse_rss(FEED_URL, version_map)
    newest = _extract_mods(newest_root, version_map)

    updated_root = _parse_rss(FEED_UPDATED_URL, version_map)
    updated = _extract_mods(updated_root, version_map)
    updated.sort(key=lambda m: m.get("updated", ""), reverse=True)

    return newest, updated
