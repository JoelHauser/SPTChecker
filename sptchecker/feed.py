import html
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

from .config import (
    API_MOD_URL, API_MODS_UPDATES_URL, API_URL, DC_NS, FEED_URL, FEED_UPDATED_URL,
    MODS_UPDATES_CHUNK_SIZE, PUBLISHED_CHUNK_SIZE as _PUBLISHED_CHUNK_SIZE,
)

_MOD_ID_RE = re.compile(r"/mod/(\d+)/")

_session = requests.Session()
_session.headers["User-Agent"] = "SPTModChecker/3.3.1"

_API_HEADERS = {"Accept": "application/json"}
# The RSS routes previously sent no Accept at all, leaving content negotiation
# to the server's default -- which is the HTML page, not the feed.
_RSS_HEADERS = {"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"}

# The Forge allows ~300 requests per window, and that budget is shared by
# every caller in the app -- the periodic feed check and the local-mod scan
# run on separate threads and can overlap at startup. Pacing and 429 retry
# live here, in the one place every Forge request passes through, so no
# call site can forget them.
_REQUEST_MIN_INTERVAL = 0.25
_throttle_lock = threading.Lock()
_last_request_ts = 0.0


class ForgeBlocked(Exception):
    """The host answered, but refused to serve us at the edge rather than at
    the application -- currently a Cloudflare interactive challenge.

    Distinct from an ordinary HTTP error because the remedy is different and
    nothing the app can retry its way out of: no header, endpoint or backoff
    changes the outcome, since passing the challenge requires executing its
    JavaScript in a real browser to earn a `cf_clearance` cookie. Raised as
    its own type so the check flow can report the real situation instead of
    surfacing a bare "403 Client Error" that reads like a bug in the app.
    """


def get_session():
    return _session


def _is_challenge(resp):
    """True when a response is a Cloudflare bot challenge rather than content.

    `Cf-Mitigated: challenge` is the explicit signal and is checked first; the
    status/content-type pair is a fallback for edge configs that omit it. Both
    matter because the challenge is served as 403 with an HTML body, which is
    otherwise indistinguishable from a genuine application-level 403.
    """
    if resp.headers.get("Cf-Mitigated", "").lower() == "challenge":
        return True
    return (resp.status_code in (403, 503)
            and resp.headers.get("Server", "").lower() == "cloudflare"
            and "text/html" in resp.headers.get("Content-Type", ""))


def _forge_request(method, url, retries=3, **kw):
    """Single chokepoint for all Forge requests: enforces a minimum interval
    between requests app-wide and retries on 429 -- a silently-swallowed 429
    looks identical to a real "not found" to callers otherwise. Returns the
    response without raising; callers check status.

    The one exception is an edge challenge, which raises ForgeBlocked: it is
    not a per-request condition callers can meaningfully handle one at a time,
    and retrying only burns the rate-limit budget.
    """
    global _last_request_ts
    for attempt in range(retries + 1):
        with _throttle_lock:
            wait = _REQUEST_MIN_INTERVAL - (time.monotonic() - _last_request_ts)
            if wait > 0:
                time.sleep(wait)
            _last_request_ts = time.monotonic()
        resp = _session.request(method, url, **kw)
        if _is_challenge(resp):
            raise ForgeBlocked(
                "sp-mod.com is blocking automated requests (Cloudflare challenge)"
            )
        if resp.status_code != 429 or attempt == retries:
            return resp
        time.sleep(float(resp.headers.get("Retry-After", 2)))
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


def _fetch_mods(params, timeout=15):
    """Fetch and parse one page of mods from the API; [] on any failure,
    matching this module's fail-soft convention.

    ForgeBlocked is deliberately not swallowed: fail-soft exists to keep one
    flaky request from taking down a check, but an edge block affects every
    request equally, and degrading it to [] would present a site-wide outage
    as "no mods found".
    """
    try:
        resp = _forge_request("get", API_URL, params={"include": "versions,category", **params},
                              headers=_API_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return [_parse_api_mod(item) for item in resp.json().get("data", [])]
    except ForgeBlocked:
        raise
    except Exception:
        return []


def _fetch_api_mods(sort="-updated_at"):
    """Fetch mods from the API with the given sort order."""
    return _fetch_mods({"sort": sort, "per_page": 50}, timeout=30)


def lookup_by_guid(guid):
    """Exact-match a locally-scanned mod's GUID against the Forge catalog.

    Forge exposes `guid` as a filterable field (confirmed live against
    filter[guid]=<value>), so this is a single indexed lookup rather than a
    search -- the primary local-mod matching strategy. Returns None on no
    match, ambiguous results, or any request failure.
    """
    if not guid:
        return None
    mods = _fetch_mods({"filter[guid]": guid})
    if len(mods) != 1 or mods[0].get("guid") != guid:
        return None
    return mods[0]


def lookup_by_name(name):
    """Fallback search by name for local mods that yielded no clean GUID match.

    filter[name] does a partial/contains-style match server-side, so callers
    should rank the results themselves rather than assume the first is right.
    """
    return _fetch_mods({"filter[name]": name, "per_page": 20}) if name else []


def lookup_by_query(term):
    """Fuzzy/full-text search, for local mods filter[name] can't find.

    filter[name] only matches a term that's a literal substring of the
    stored title -- it can't bridge a local mod's internal name (often
    camelCase/dotted developer shorthand) to a differently-worded Forge
    listing. `query=` is a separate, undocumented parameter (confirmed live
    against the API) that does real fuzzy matching instead, at the cost of
    returning looser candidates -- callers still need to rank results
    themselves, same as lookup_by_name.
    """
    return _fetch_mods({"query": term, "per_page": 20}) if term else []


def lookup_updates(pairs, spt_version):
    """Batch-check locally-installed mods against Forge's own authoritative
    update logic: given (guid, installed_version) pairs and the user's
    actual installed SPT version, Forge judges whether a newer version
    exists, whether it's actually compatible with that SPT version, and
    whether installing it would violate another mod's dependency
    constraint -- none of which a local version-string comparison can know.
    Confirmed live: this catches false "update available" flags that a
    local numeric-newer check alone lets through -- Forge treats a matched
    mod's version history as the source of truth, not just "is the number
    bigger".

    Chunked since the API's mods= list has no documented length cap, and
    merged into one dict of the four Forge result buckets: updates,
    blocked_updates, up_to_date, incompatible_with_spt. A chunk that fails
    (network hiccup, one bad identifier) is skipped rather than failing the
    whole batch -- callers should treat a pair absent from every bucket as
    "no authoritative answer" and keep their own fallback, not as "up to
    date".
    """
    merged = {"updates": [], "blocked_updates": [], "up_to_date": [], "incompatible_with_spt": []}
    if not pairs or not spt_version:
        return merged
    for i in range(0, len(pairs), MODS_UPDATES_CHUNK_SIZE):
        chunk = pairs[i:i + MODS_UPDATES_CHUNK_SIZE]
        mods_param = ",".join(f"{guid}:{version}" for guid, version in chunk)
        try:
            resp = _forge_request("get", API_MODS_UPDATES_URL,
                                  params={"mods": mods_param, "spt_version": spt_version},
                                  headers=_API_HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data", {})
        except Exception:
            continue
        for key in merged:
            merged[key].extend(data.get(key, []))
    return merged


def _parse_rss(url):
    """Fetch and parse an RSS feed into mod dicts; [] if the feed is
    unavailable or malformed.

    Fail-soft because RSS is the app's secondary source -- everything it
    carries is also available from the API, which fetch_feeds() has already
    queried by the time this runs. Previously this was the one unguarded
    request in the whole startup path, so an RSS-only problem (a feed route
    that moved, a transient 5xx, a truncated body) aborted the entire check
    and surfaced a raw HTTP error, despite usable API results sitting right
    there. A site-wide block still propagates -- see _fetch_mods.
    """
    try:
        resp = _forge_request("get", url, headers=_RSS_HEADERS, timeout=30)
        resp.raise_for_status()
        return _extract_mods(ET.fromstring(resp.content))
    except ForgeBlocked:
        raise
    except Exception:
        return []


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


def unpublished_links(links):
    """Of these mod links, which are no longer published?

    Replaces what used to be one HEAD request per mod against its rendered
    HTML page. sp-mod.com serves API responses roughly 30x faster than mod
    pages (measured: ~0.02s vs ~0.58s each), and a display refresh checks 14
    mods, so the old approach cost ~8s per check and dominated the whole
    cycle. One indexed filter[id] lookup answers for all of them at once.

    Returns the set of links that are definitively gone -- everything else
    stays displayed. Every uncertain path deliberately reports "nothing is
    unpublished" rather than guessing: these mods came out of the live feed
    moments earlier, so a mod vanishing is the rare case and a request
    failure is the likely one. Wrongly hiding a real mod is far worse than
    briefly showing one that just got pulled.
    """
    by_id = {}
    for link in links:
        m = _MOD_ID_RE.search(link)
        # No parseable id -- can't ask about it, so leave it displayed.
        if m:
            by_id.setdefault(m.group(1), []).append(link)
    if not by_id:
        return set()

    found = set()
    ids = list(by_id)
    for i in range(0, len(ids), _PUBLISHED_CHUNK_SIZE):
        chunk = ids[i:i + _PUBLISHED_CHUNK_SIZE]
        try:
            resp = _forge_request("get", API_URL, headers=_API_HEADERS, timeout=20,
                                  params={"filter[id]": ",".join(chunk),
                                          "fields": "id",
                                          "per_page": _PUBLISHED_CHUNK_SIZE})
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except Exception:
            return set()
        # Asking about several live-feed mods and being told none of them
        # exist is far more likely an API contract change than a simultaneous
        # mass unpublish -- treat it as inconclusive rather than blanking the
        # display. A single-id chunk is still trusted, so real one-off
        # unpublishes are still caught.
        if not data and len(chunk) > 1:
            return set()
        found.update(str(item.get("id")) for item in data)

    return {link for mod_id, group in by_id.items() if mod_id not in found
            for link in group}


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
        resp = _forge_request("get", f"{API_MOD_URL}/{m.group(1)}",
                              headers=_API_HEADERS, timeout=15)
        resp.raise_for_status()
        owner = resp.json().get("data", {}).get("owner") or {}
        return owner.get("id")
    except Exception:
        return None


def fetch_feeds():
    """Fetch newest and recently updated mods from RSS feeds + API."""
    api_updated = _fetch_api_mods(sort="-updated_at")
    api_newest = _fetch_api_mods(sort="-created_at")

    # The API is authoritative for both columns; RSS is additive, contributing
    # entries that fall outside the API's fetched window. When a feed is
    # unavailable its column falls back to the API set rather than rendering
    # empty -- "new mods" in particular used to come from RSS alone, so a feed
    # failure blanked that column even with API results already in hand.
    newest = _parse_rss(FEED_URL) or api_newest
    rss_updated = _parse_rss(FEED_UPDATED_URL)

    # RSS entries lack several API-only fields -- build per-link lookups from the
    # API sets and enrich both columns, whichever source each entry came from.
    all_api = {m["link"]: m for m in api_updated + api_newest}
    enrich_fields = ("changelog", "full_description", "author_since", "author_id")
    lookups = {
        field: {link: m[field] for link, m in all_api.items() if m.get(field)}
        for field in enrich_fields
    }
    # RSS's pubDate reflects when the listing was *created* (often drafted well
    # before it actually goes live), not when it was published -- confirmed live
    # against the API's published_at, which is the true publish timestamp. That
    # skew is what makes the daily-activity graph undercount "today" and only
    # catch up once the date rolls over. The API value always wins when
    # available; RSS's pubDate is only a fallback for mods outside the API's
    # fetched window.
    published_lookup = {link: m["published"] for link, m in all_api.items() if m.get("published")}

    def _enrich(mod):
        api_published = published_lookup.get(mod["link"])
        if api_published:
            mod["published"] = api_published
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
