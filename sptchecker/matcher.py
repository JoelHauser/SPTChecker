import difflib
import re
import time

from .config import FUZZY_MATCH_THRESHOLD
from .feed import lookup_by_guid, lookup_by_name, lookup_by_query

# Forge's API allows 300 requests per window; a full scan can easily fire a
# couple hundred lookups (guid + name-fallback per unmatched mod) back to
# back with no natural pacing, which blows straight through that limit and
# makes real matches silently look like "not found" (feed.py's 429 backoff
# is a safety net, not something to rely on for routine pacing).
_REQUEST_PACING_SECONDS = 0.25


def _normalize_name(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower()) if name else ""


_VERSION_PART_RE = re.compile(r"\d+")


def _parse_version(v):
    """Dotted version string -> tuple of ints, for numeric comparison.
    Returns None if it doesn't contain anything version-shaped."""
    if not v:
        return None
    parts = _VERSION_PART_RE.findall(v)
    return tuple(int(p) for p in parts) if parts else None


def _is_newer(available, current):
    """True only if the Forge version is actually numerically greater than
    the installed one -- a matched mod's Forge listing can be an older
    re-upload/fork than what's actually installed (a different, more
    current upload exists elsewhere), so a plain string inequality check
    would flag a downgrade as an "update" just because the strings differ.
    Unparseable versions are treated as no-update rather than guessed at --
    better to miss a rare oddly-formatted version than wrongly tell someone
    to "update" to something older.
    """
    a, c = _parse_version(available), _parse_version(current)
    if a is None or c is None:
        return False
    return a > c


_CLIENT_SERVER_SUFFIX_RE = re.compile(r"[\s._-]*(client|server)$", re.IGNORECASE)
# Two word-boundary shapes: lowercase/digit -> uppercase ("TaskAutomation" ->
# "Task Automation") and the tail of an acronym run -> the word that follows
# it ("UIFixes" -> "UI Fixes", "HTTPServer" -> "HTTP Server"). A run of
# capitals directly followed by an all-lowercase continuation with no further
# capital ("SICCcase") has no casing signal marking the boundary at all --
# genuinely undecidable without knowing the acronym, so that case is left as
# a known gap rather than guessed at.
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_DOTTED_PREFIX_RE = re.compile(r"^[A-Za-z0-9]+[._-]")


def _guid_derived_name(guid):
    """The last reverse-domain segment of a GUID is often the mod's actual
    name with the author's own prefix already stripped off for free (e.g.
    'com.kipperworks.gunsmithbarters' -> 'gunsmithbarters') -- worth trying
    as its own search term, same as Refringe's Check Mods does."""
    if not guid or "." not in guid:
        return None
    return guid.rsplit(".", 1)[-1]


def _search_terms(name, guid=None):
    """Query variants to try against Forge's name search, in order, stopping
    at the first that returns any hits.

    Forge's filter[name] only matches when the search term is a literal
    substring of the stored title -- but the name embedded in a mod's own
    files often isn't written the way its Forge listing is: a local
    'TaskAutomation' won't match a Forge title of 'Task Automation', and a
    local 'Modern Weapon Mods (MWM) Client' won't match a Forge title of
    'Modern Weapon Mods (MWM)' (the search term must fit *inside* the title,
    not the other way around). Confirmed against the live API. Refringe's
    Check Mods CLI uses the same transforms (suffix-strip, camelCase split,
    GUID-derived name) for the same reasons; the author-prefix strip below
    is this app's own addition, for names like 'Kipperworks.GunsmithBarters'
    where the author's own name is prepended ahead of the actual mod name.
    """
    terms = []
    if name:
        terms.append(name)
        stripped = _CLIENT_SERVER_SUFFIX_RE.sub("", name).strip()
        if stripped and stripped not in terms:
            terms.append(stripped)
        unprefixed = _DOTTED_PREFIX_RE.sub("", stripped or name).strip()
        if unprefixed and unprefixed not in terms:
            terms.append(unprefixed)
        for base in (stripped or name, unprefixed):
            spaced = _CAMEL_BOUNDARY_RE.sub(" ", base).strip()
            if spaced and spaced not in terms:
                terms.append(spaced)

    guid_name = _guid_derived_name(guid)
    if guid_name and guid_name not in terms:
        terms.append(guid_name)
        spaced_guid = _CAMEL_BOUNDARY_RE.sub(" ", guid_name).strip()
        if spaced_guid and spaced_guid not in terms:
            terms.append(spaced_guid)

    return terms


def _search_by_name(name, guid=None):
    """Returns (candidates, term) -- term is whichever search-term variant
    actually produced hits, since that's what ranking should compare
    against next, not necessarily the raw original name (e.g. a search for
    'Tyfon.UIFixes' only finds anything once split down to 'UI Fixes', so
    that's the form worth ranking candidates against too)."""
    seen_links = set()
    for i, term in enumerate(_search_terms(name, guid)):
        if i > 0:
            time.sleep(_REQUEST_PACING_SECONDS)
        candidates = []
        for c in lookup_by_name(term):
            if c.get("link") not in seen_links:
                seen_links.add(c.get("link"))
                candidates.append(c)
        if candidates:
            return candidates, term
    return [], None


def _best_score(target_name, candidate):
    """Forge's display title and its URL slug are both fair game to compare
    against -- the slug is already normalized (lowercase, hyphenated, no
    punctuation), so it often lines up with a camelCase/dotted local name
    far better than the display title does. Same approach Refringe's Check
    Mods takes (max of a name-score and a slug-score)."""
    name_ratio = difflib.SequenceMatcher(None, target_name, _normalize_name(candidate.get("title"))).ratio()
    slug_ratio = difflib.SequenceMatcher(None, target_name, _normalize_name(candidate.get("slug"))).ratio()
    return max(name_ratio, slug_ratio)


def _rank_candidates(candidates, name, author=None):
    """Cascade: exact normalized name/slug -> author+name -> fuzzy similarity
    above threshold. Returns (forge_mod, match_method), or (None, None) if
    nothing is confident enough -- an unmatched mod is reported as
    unmatched, never guessed."""
    if not candidates:
        return None, None
    target_name = _normalize_name(name)
    if not target_name:
        return None, None
    target_author = _normalize_name(author)

    for c in candidates:
        if target_name in (_normalize_name(c.get("title")), _normalize_name(c.get("slug"))):
            return c, "name_exact"

    if target_author:
        for c in candidates:
            title_n = _normalize_name(c.get("title"))
            slug_n = _normalize_name(c.get("slug"))
            if (_normalize_name(c.get("author")) == target_author
                    and (target_name in title_n or target_name in slug_n)):
                return c, "author_name"

    best, best_ratio = None, 0.0
    for c in candidates:
        ratio = _best_score(target_name, c)
        if ratio > best_ratio:
            best, best_ratio = c, ratio
    if best and best_ratio >= FUZZY_MATCH_THRESHOLD:
        return best, "fuzzy"
    return None, None


def match_one(local_mod):
    """Match a single locally-scanned mod against the Forge. GUID lookup is
    tried first (exact, cheap); falls back to a name search + ranking cascade
    only when there's no clean GUID or the GUID lookup missed."""
    guid = local_mod.get("guid")
    forge = lookup_by_guid(guid) if guid else None
    match_method = "guid" if forge else None

    if not forge:
        candidates, matched_term = _search_by_name(local_mod.get("name"), guid)
        forge, match_method = _rank_candidates(
            candidates, matched_term or local_mod.get("name"), local_mod.get("author"))

    if not forge:
        # filter[name] only matches a literal substring of the stored title,
        # which structurally can't bridge every gap between a mod's internal
        # name and its Forge listing. query= is a separate, undocumented
        # parameter (the same one Refringe's Check Mods CLI uses) that does
        # real fuzzy/full-text search -- last resort since it returns looser
        # candidates, still filtered through the same ranking cascade so a
        # weak match still can't slip through as a false positive.
        time.sleep(_REQUEST_PACING_SECONDS)
        candidates = lookup_by_query(local_mod.get("name") or "")
        forge, match_method = _rank_candidates(
            candidates, local_mod.get("name"), local_mod.get("author"))
        if forge:
            match_method = f"query_{match_method}"

    current_version = local_mod.get("version")
    available_version = forge.get("version") if forge else None
    update_available = bool(
        forge and available_version and current_version
        and _is_newer(available_version, current_version)
    )

    return {
        "local": local_mod,
        "forge": forge,
        "match_method": match_method,
        "current_version": current_version,
        "available_version": available_version,
        "update_available": update_available,
    }


def match_local_mods(local_mods, on_progress=None):
    """Match every locally-scanned mod against the Forge. on_progress(done,
    total), if given, is called after each mod -- this is the slow part of a
    scan (paced, per-mod network lookups), so it's the meaningful thing to
    report progress against; the file-scan phase before this is fast."""
    results = []
    total = len(local_mods)
    for i, mod in enumerate(local_mods):
        if i > 0:
            time.sleep(_REQUEST_PACING_SECONDS)
        results.append(match_one(mod))
        if on_progress:
            on_progress(i + 1, total)
    return results
