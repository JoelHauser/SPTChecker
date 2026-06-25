import requests

from .config import API_URL

_session = requests.Session()
_session.headers["User-Agent"] = "SPTModChecker/1.3"


def get_session():
    return _session


def fetch_mods():
    resp = _session.get(API_URL, params={
        "include": "versions,category",
        "sort": "-updated_at",
        "per_page": 50,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("data", [])

    mods = []
    for item in data:
        versions = item.get("versions", [])
        latest = versions[0] if versions else {}

        owner = item.get("owner") or {}
        category = item.get("category") or {}

        spt_ver = latest.get("spt_version_constraint", "")
        if spt_ver.startswith("~"):
            spt_ver = spt_ver[1:]

        link = item.get("detail_url", "")

        mods.append({
            "title": item.get("name", ""),
            "link": link,
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
