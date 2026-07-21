import difflib
import re

from .config import FUZZY_MATCH_THRESHOLD
from .feed import lookup_by_guid, lookup_by_name


def _normalize_name(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower()) if name else ""


def _rank_candidates(local_mod, candidates):
    """Cascade: exact normalized name -> author+name -> fuzzy similarity above
    threshold. Returns (forge_mod, match_method), or (None, None) if nothing
    is confident enough -- an unmatched mod is reported as unmatched, never
    guessed."""
    if not candidates:
        return None, None
    target_name = _normalize_name(local_mod.get("name"))
    if not target_name:
        return None, None
    target_author = _normalize_name(local_mod.get("author"))

    for c in candidates:
        if _normalize_name(c.get("title")) == target_name:
            return c, "name_exact"

    if target_author:
        for c in candidates:
            if (_normalize_name(c.get("author")) == target_author
                    and target_name in _normalize_name(c.get("title"))):
                return c, "author_name"

    best, best_ratio = None, 0.0
    for c in candidates:
        ratio = difflib.SequenceMatcher(None, target_name, _normalize_name(c.get("title"))).ratio()
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
        candidates = lookup_by_name(local_mod.get("name") or "")
        forge, match_method = _rank_candidates(local_mod, candidates)

    current_version = local_mod.get("version")
    available_version = forge.get("version") if forge else None
    update_available = bool(
        forge and available_version and current_version and available_version != current_version
    )

    return {
        "local": local_mod,
        "forge": forge,
        "match_method": match_method,
        "current_version": current_version,
        "available_version": available_version,
        "update_available": update_available,
    }


def match_local_mods(local_mods):
    return [match_one(m) for m in local_mods]
