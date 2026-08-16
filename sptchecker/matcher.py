import difflib
import re

from .config import FUZZY_MATCH_THRESHOLD
from .feed import (
    ForgeRateLimited, lookup_by_guid, lookup_by_name, lookup_by_query, lookup_updates,
)
from .utils import is_newer, pad_versions, parse_version


def _normalize_name(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower()) if name else ""




# Version comparison lives in utils -- the app's own update check needs the
# same "is this actually newer" rule this does. Why it matters here: a matched
# mod's Forge listing can be an older re-upload or fork than what's installed,
# so a plain string inequality would flag a downgrade as an update.
_parse_version = parse_version
_pad_versions = pad_versions
_is_newer = is_newer


def _versions_equal(v1, v2):
    """Numeric equality, so '1.0.2' and '1.0.2.0' are recognized as the same
    version instead of looking like a client/server version drift."""
    a, c = _parse_version(v1), _parse_version(v2)
    if a is None or c is None:
        return v1 == v2
    a, c = _pad_versions(a, c)
    return a == c


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
    as its own search term."""
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
    not the other way around). Confirmed against the live API -- suffix
    stripping, camelCase splitting, and a GUID-derived name variant all help
    bridge that gap; the author-prefix strip below handles names like
    'Kipperworks.GunsmithBarters' where the author's own name is prepended
    ahead of the actual mod name.
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
    for term in _search_terms(name, guid):
        candidates = lookup_by_name(term)
        if candidates:
            return candidates, term
    return [], None


def _rank_candidates(candidates, name, author=None, original_name=None):
    """Cascade: exact normalized name/slug -> author+name -> fuzzy similarity
    above threshold. Returns (forge_mod, match_method), or (None, None) if
    nothing is confident enough -- an unmatched mod is reported as
    unmatched, never guessed.

    Forge's display title and its URL slug are both fair game to compare
    against -- the slug is already normalized (lowercase, hyphenated, no
    punctuation), so it often lines up with a camelCase/dotted local name
    far better than the display title does, so the ranking takes the max
    of a name-score and a slug-score.

    original_name, if given, is the mod's real unstripped name -- name may
    already be a suffix-stripped/author-prefix-stripped/camelCase-split
    search-term variant (see _search_terms), and the fuzzy tier specifically
    re-checks against the original too. Confirmed live: a local
    'Realism-CommonLib' had 'Realism-' wrongly stripped as if it were an
    author prefix, leaving the dangerously generic term 'CommonLib', which
    then fuzzy-matched an unrelated 'WTT - CommonLib' listing at a
    comfortable 0.857 -- comparing against the *original* name instead
    scores only 0.643, correctly well below threshold. name_exact and
    author_name are unaffected since they aren't fooled by an over-stripped
    term the same way -- this check only guards the last-resort tier."""
    if not candidates:
        return None, None
    target_name = _normalize_name(name)
    if not target_name:
        return None, None
    target_author = _normalize_name(author)
    original_name_n = _normalize_name(original_name) if original_name else target_name

    normed = [(c, _normalize_name(c.get("title")), _normalize_name(c.get("slug")))
              for c in candidates]

    for c, title_n, slug_n in normed:
        if target_name in (title_n, slug_n):
            return c, "name_exact"

    if target_author:
        for c, title_n, slug_n in normed:
            if (_normalize_name(c.get("author")) == target_author
                    and (target_name in title_n or target_name in slug_n)):
                return c, "author_name"

    # difflib caches preprocessing for the second sequence, so keep the
    # shared target there and swap only the candidate side per comparison.
    matcher = difflib.SequenceMatcher(None, "", target_name)
    best, best_ratio, best_title_n, best_slug_n = None, 0.0, "", ""
    for c, title_n, slug_n in normed:
        matcher.set_seq1(title_n)
        ratio = matcher.ratio()
        matcher.set_seq1(slug_n)
        ratio = max(ratio, matcher.ratio())
        if ratio > best_ratio:
            best, best_ratio, best_title_n, best_slug_n = c, ratio, title_n, slug_n
    if best and best_ratio >= FUZZY_MATCH_THRESHOLD:
        orig_matcher = difflib.SequenceMatcher(None, best_title_n, original_name_n)
        orig_ratio = orig_matcher.ratio()
        orig_matcher.set_seq1(best_slug_n)
        orig_ratio = max(orig_ratio, orig_matcher.ratio())
        if orig_ratio >= FUZZY_MATCH_THRESHOLD:
            return best, "fuzzy"
    return None, None


def match_one(local_mod):
    """Match a single locally-scanned mod against the Forge. GUID lookup is
    tried first (exact, cheap); falls back to a name search + ranking cascade
    only when there's no clean GUID or the GUID lookup missed.

    Sets lookup_failed when the Forge rate-limited us rather than answering.
    That case has to stay distinct from an honest miss: every lookup here
    returns "no match" on failure, so without it a throttled scan would
    quietly report a wall of perfectly ordinary installed mods as missing
    from the Forge, blaming the mods for the app's own request rate.
    """
    guid = local_mod.get("guid")
    try:
        forge = lookup_by_guid(guid) if guid else None
        match_method = "guid" if forge else None

        if not forge:
            candidates, matched_term = _search_by_name(local_mod.get("name"), guid)
            forge, match_method = _rank_candidates(
                candidates, matched_term, local_mod.get("author"),
                original_name=local_mod.get("name"))

        if not forge:
            # filter[name] only matches a literal substring of the stored
            # title, which structurally can't bridge every gap between a
            # mod's internal name and its Forge listing. query= is a
            # separate, undocumented parameter that does real fuzzy/full-text
            # search -- last resort since it returns looser candidates, still
            # filtered through the same ranking cascade so a weak match still
            # can't slip through as a false positive.
            candidates = lookup_by_query(local_mod.get("name"))
            forge, match_method = _rank_candidates(
                candidates, local_mod.get("name"), local_mod.get("author"))
            if forge:
                match_method = f"query_{match_method}"
    except ForgeRateLimited:
        return {
            "local": local_mod,
            "forge": None,
            "match_method": None,
            "current_version": local_mod.get("version"),
            "available_version": None,
            "update_available": False,
            "lookup_failed": True,
        }

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
        "lookup_failed": False,
    }


def _merge_group(group):
    """Combine several scan results that all resolved to the same Forge mod
    into one. forge is identical across the group by construction (that's
    the grouping key); only the local side differs.

    The common case is that every component is at the same installed
    version -- show that one version, same as an unmerged entry would. It's
    also possible for a mod's client and server halves to drift out of sync
    with each other (one updated, one not), in which case an unlabeled
    "1.4.0 / 1.5.0" is ambiguous about which is which -- label each by its
    source instead. Versions are compared numerically (via _versions_equal),
    not as raw strings -- otherwise '1.0.2' and '1.0.2.0' (the same version,
    just with a trailing zero segment) would look like a drift that isn't
    real.
    """
    versions = [r["current_version"] for r in group if r["current_version"]]
    first = versions[0] if versions else None
    if versions and any(not _versions_equal(v, first) for v in versions):
        current_version = ", ".join(
            f"{r['local'].get('source', '?')} {r['current_version']}"
            for r in group if r["current_version"]
        )
    else:
        current_version = first
    return {
        **group[0],
        "current_version": current_version,
        "update_available": any(r["update_available"] for r in group),
    }


def _apply_authoritative_updates(results, spt_version):
    """Override each match's update_available/available_version with
    Forge's own mods/updates verdict where one exists. Confirmed live
    against a real install: a local numeric version comparison alone
    produces false "update available" flags that Forge's own version
    history doesn't back up, and can't know whether a newer version is
    actually compatible with the user's installed SPT build or blocked by
    another mod's dependency constraint. Looked up by the *Forge-matched*
    guid, not the mod's own locally-declared one -- the two can legitimately
    differ (e.g. a mod re-registered under a new author/guid on Forge while
    keeping its old local plugin GUID), and Forge's own guid is what its API
    actually recognizes.

    A pair Forge doesn't recognize at all (identifier unknown, or this exact
    installed version was never one it tracked) is left untouched -- no
    verdict isn't the same as "up to date", so those results keep whatever
    match_one already computed locally as a fallback rather than being
    silently cleared. Keyed by (guid, installed version) together, not guid
    alone -- the same guid can appear twice with different local versions
    (e.g. a stale duplicate DLL left behind after an update), and keying on
    guid alone would let one component's verdict overwrite the other's.
    """
    if not spt_version:
        return
    by_key = {}
    pairs = []
    for r in results:
        forge = r.get("forge")
        guid = forge.get("guid") if forge else None
        version = r.get("current_version")
        if guid and version and _parse_version(version):
            pairs.append((guid, version))
            by_key.setdefault((guid.lower(), version), []).append(r)
    if not pairs:
        return

    data = lookup_updates(pairs, spt_version)

    for entry in data["updates"]:
        key = (entry["current_version"]["guid"].lower(), entry["current_version"]["version"])
        for r in by_key.get(key, []):
            r["update_available"] = True
            r["available_version"] = entry["recommended_version"]["version"]

    for entry in data["blocked_updates"]:
        key = (entry["current_version"]["guid"].lower(), entry["current_version"]["version"])
        for r in by_key.get(key, []):
            r["update_available"] = False

    for entry in data["up_to_date"] + data["incompatible_with_spt"]:
        key = (entry["guid"].lower(), entry["version"])
        for r in by_key.get(key, []):
            r["update_available"] = False


def merge_duplicate_matches(results):
    """A single logical mod is sometimes split across multiple installed
    files that each declare their own GUID -- a BepInEx client plugin and a
    separate SPT server-side DLL are scanned and matched independently, but
    if they both resolve to the same Forge listing they're one mod to the
    user, not two separate "update available" entries."""
    groups, order = {}, []
    for r in results:
        # Unmatched entries have no forge.link to group by -- key each on its
        # own identity so they never merge with one another.
        key = r["forge"]["link"] if r["forge"] else id(r)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    return [group[0] if len(group) == 1 else _merge_group(group)
            for group in (groups[key] for key in order)]


def match_local_mods(local_mods, spt_version=None, on_progress=None):
    """Match every locally-scanned mod against the Forge. Request pacing and
    rate-limit retry live in feed.py's request layer, not here. on_progress
    (done, total), if given, is called after each mod -- this is the slow
    part of a scan (per-mod network lookups), so it's the meaningful thing
    to report progress against; the file-scan phase before it is fast.
    spt_version, if known, is passed straight through to Forge's own
    mods/updates endpoint to double-check each match's update status --
    skipped entirely (falling back to the plain local version comparison
    from match_one) when it couldn't be detected, e.g. a client-only
    install with no SPT.Server.exe."""
    results = []
    total = len(local_mods)
    for i, mod in enumerate(local_mods):
        results.append(match_one(mod))
        if on_progress:
            on_progress(i + 1, total)
    _apply_authoritative_updates(results, spt_version)
    return merge_duplicate_matches(results)
