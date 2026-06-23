import html
import re
import xml.etree.ElementTree as ET

import requests

from .config import DC_NS, FEED_URL

_session = requests.Session()
_session.headers["User-Agent"] = "SPTModChecker/1.0"


def get_session():
    return _session


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_mods():
    resp = _session.get(FEED_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    mods = []
    for item in root.findall(".//item"):
        link = item.findtext("link", "")
        if not link:
            continue
        pub = item.findtext("pubDate", "")
        enc = item.find("enclosure")
        thumb = enc.get("url", "") if enc is not None else ""
        mods.append(
            {
                "title": item.findtext("title", ""),
                "link": link,
                "author": item.findtext(f"{{{DC_NS}}}creator", "Unknown"),
                "version": item.findtext(f"{{{DC_NS}}}identifier", ""),
                "category": item.findtext("category", ""),
                "published": pub,
                "updated": item.findtext(f"{{{DC_NS}}}date", pub),
                "thumb_url": thumb,
                "description": strip_html(item.findtext("description", ""))[:300],
            }
        )
    return mods
