import html
import re
import xml.etree.ElementTree as ET

import requests

from .config import DC_NS, FEED_URL, FORGE_URL

_session = requests.Session()
_session.headers["User-Agent"] = "SPTModChecker/1.0"


def get_session():
    return _session


_SPT_VERSION_RE = re.compile(r"SPT\s+(\d+\.\d+\.\d+)")
_PAGE_VERSION_RE = re.compile(
    r'href="(https://forge\.sp-tarkov\.com/mod/\d+/[^"]+)".*?SPT\s+(\d+\.\d+\.\d+)',
    re.DOTALL,
)


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_spt_versions():
    try:
        resp = _session.get(FORGE_URL, timeout=30)
        resp.raise_for_status()
        return {url: ver for url, ver in _PAGE_VERSION_RE.findall(resp.text)}
    except Exception:
        return {}


def fetch_mods():
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

        spt_ver = version_map.get(link, "") or _SPT_VERSION_RE.search(desc_raw)
        if spt_ver and not isinstance(spt_ver, str):
            spt_ver = spt_ver.group(1)

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
                "description": desc_text[:300],
                "spt_version": spt_ver or "",
            }
        )
    return mods
