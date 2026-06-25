import html
import re
import xml.etree.ElementTree as ET

import requests

from .config import API_URL, DC_NS, FEED_URL

_session = requests.Session()
_session.headers["User-Agent"] = "SPTModChecker/1.3"


def get_session():
    return _session


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_spt_versions():
    try:
        resp = _session.get(API_URL, params={
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


def fetch_newest():
    """Fetch mods from RSS feed — matches website 'Newest' tab order."""
    resp = _session.get(FEED_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    version_map = _fetch_spt_versions()

    mods = []
    for item in root.findall(".//item"):
        link = item.findtext("link", "")
        if not link:
            continue
        pub = item.findtext("pubDate", "")
        enc = item.find("enclosure")
        thumb = enc.get("url", "") if enc is not None else ""
        desc_raw = item.findtext("description", "")
        desc_text = strip_html(desc_raw)

        spt_ver = version_map.get(link, "")

        mods.append({
            "title": item.findtext("title", ""),
            "link": link,
            "author": item.findtext(f"{{{DC_NS}}}creator", "Unknown"),
            "version": item.findtext(f"{{{DC_NS}}}identifier", ""),
            "category": item.findtext("category", ""),
            "published": pub,
            "updated": item.findtext(f"{{{DC_NS}}}date", pub),
            "thumb_url": thumb,
            "description": desc_text[:300],
            "spt_version": spt_ver,
        })
    return mods


def fetch_recently_updated():
    """Fetch mods from API — matches website 'Recently Updated' tab order."""
    resp = _session.get(API_URL, params={
        "include": "versions,category",
        "sort": "-updated_at",
        "per_page": 12,
    }, timeout=30)
    resp.raise_for_status()

    mods = []
    for item in resp.json().get("data", []):
        versions = item.get("versions", [])
        latest = versions[0] if versions else {}
        owner = item.get("owner") or {}
        category = item.get("category") or {}

        spt_ver = latest.get("spt_version_constraint", "").strip()
        if spt_ver.startswith("~"):
            spt_ver = spt_ver[1:]

        mods.append({
            "title": item.get("name", ""),
            "link": item.get("detail_url", ""),
            "author": owner.get("name", "Unknown"),
            "version": latest.get("version", ""),
            "category": category.get("title", ""),
            "published": item.get("published_at", ""),
            "updated": item.get("updated_at", ""),
            "thumb_url": item.get("thumbnail", ""),
            "description": (item.get("teaser", "") or "")[:300],
            "spt_version": spt_ver,
        })
    return mods
