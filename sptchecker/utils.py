import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_VERSION_PART_RE = re.compile(r"\d+")


def parse_version(v):
    """Dotted version string -> tuple of ints, for numeric comparison.
    Returns None if it doesn't contain anything version-shaped. Leading
    decoration is ignored, so a release tag ("V3.3.1") and a bare version
    ("3.3.1") parse identically."""
    if not v:
        return None
    parts = _VERSION_PART_RE.findall(v)
    return tuple(int(p) for p in parts) if parts else None


def pad_versions(a, b):
    """Zero-pad the shorter tuple so (1, 0, 2) and (1, 0, 2, 0) -- the same
    version, just written with a different number of segments -- compare as
    equal instead of the longer one looking "newer" by tuple length alone."""
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def is_newer(available, current):
    """True only if `available` is numerically greater than `current`.

    Shared by mod matching and the app's own update check, which have the
    same requirement: a plain string inequality would call a *downgrade* an
    update purely because the strings differ. Unparseable versions are
    treated as no-update rather than guessed at -- better to miss a rare
    oddly-formatted version than to tell someone to "update" to something
    older.
    """
    a, c = parse_version(available), parse_version(current)
    if a is None or c is None:
        return False
    a, c = pad_versions(a, c)
    return a > c


# Pieces of a Forge spt_version_constraint. The Forge takes Composer-style
# constraints and is lenient about how they're typed, and mod authors use that
# leniency: "~4.", "~4.1. > 4.1.1" and ">=4.0  <4.1" all appear on live
# listings and all resolve on the Forge's side.
_CONSTRAINT_OP_GAP_RE = re.compile(r"(>=|<=|!=|==|>|<|=|~|\^)\s+")
_CONSTRAINT_RANGE_RE = re.compile(r"^(\S+)\s+-\s+(\S+)$")
_CONSTRAINT_TERM_RE = re.compile(
    r"^(>=|<=|!=|==|>|<|=|~|\^)?v?(\d+)(?:\.(\d+|[x*]))?(?:\.(\d+|[x*]))?\.?$",
    re.IGNORECASE)
_SPT_RELEASE_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _pad3(nums):
    return tuple(nums) + (0,) * (3 - len(nums))


def _bump(nums, index):
    """The first version past everything that shares nums[:index + 1]."""
    out = list(nums[:index + 1])
    out[index] += 1
    return _pad3(out)


def _constraint_term(term):
    """(operator, components) for one term -- each component an int, or "*"
    for a trailing wildcard -- or None if it isn't a term we can read."""
    m = _CONSTRAINT_TERM_RE.match(term)
    if not m:
        return None
    parts = [int(p) if p.isdigit() else "*" for p in m.group(2, 3, 4) if p is not None]
    if "*" in parts[:-1]:
        return None
    return m.group(1) or "", parts


def _term_allows(release, term):
    """Whether one term allows the release; None if the term is unreadable."""
    if term in ("*", "x", "X"):
        return True
    parsed = _constraint_term(term)
    if parsed is None:
        return None
    op, parts = parsed
    if parts[-1] == "*":
        if op:
            return None
        nums = parts[:-1]
        return _pad3(nums) <= release < _bump(nums, len(nums) - 1)
    low = _pad3(parts)
    if op == "~":
        # Composer's tilde, not npm's: "~4.0" allows everything below 5.0.0,
        # and only a full "~4.0.2" stops at the next minor. Read the npm way,
        # the many "~4.0" listings would wrongly exclude every 4.1 release.
        return low <= release < _bump(parts, max(0, len(parts) - 2))
    if op == "^":
        index = next((i for i, p in enumerate(parts) if p), len(parts) - 1)
        return low <= release < _bump(parts, index)
    return {
        ">=": release >= low, "<=": release <= low,
        ">": release > low, "<": release < low, "!=": release != low,
    }.get(op, release == low)


def _alternative_allows(release, alternative):
    """One ||-separated alternative: every term in it has to hold."""
    alternative = _CONSTRAINT_OP_GAP_RE.sub(r"\1", alternative.strip())
    m = _CONSTRAINT_RANGE_RE.match(alternative)
    if m:
        low, high = _constraint_term(m.group(1)), _constraint_term(m.group(2))
        if not low or not high or low[0] or high[0] or "*" in low[1] + high[1]:
            return None
        # A partial upper bound is completed with a wildcard, as Composer does:
        # "1.0 - 2.0" runs up to 2.1.0, while "1.0.0 - 2.1.0" includes 2.1.0.
        top = high[1]
        below_top = release < _bump(top, len(top) - 1) if len(top) < 3 else release <= _pad3(top)
        return _pad3(low[1]) <= release and below_top
    terms = [t for t in re.split(r"[\s,]+", alternative) if t]
    results = [_term_allows(release, t) for t in terms]
    if not results or None in results:
        return None
    return all(results)


def spt_version_satisfies(spt_version, constraint):
    """Whether an SPT release (e.g. "4.0.13") satisfies a mod version's
    spt_version_constraint as published by the Forge.

    The Forge filters its mod lists by SPT version itself, but only whole mods,
    never their versions -- so something still has to pick which of a mod's
    versions to show. Checked against the Forge's own resolution of every
    distinct constraint on several pages of live listings (GET
    /api/v0/spt/versions?filter[spt_version]=<constraint>, across all 50 SPT
    releases), with no disagreements.

    Anything it can't read -- an empty constraint included -- is False rather
    than a guess: showing a version as compatible when it isn't is the one
    failure a compatibility filter must not have.
    """
    m = _SPT_RELEASE_RE.match((spt_version or "").strip())
    if not m or not (constraint or "").strip():
        return False
    release = tuple(int(x) for x in m.groups())
    results = [_alternative_allows(release, alt) for alt in re.split(r"\|\|?", constraint)]
    if None in results:
        return False
    return any(results)


def parse_dt(ts_str):
    """Parse an ISO or RFC 2822 timestamp (RSS vs API formats) into an aware datetime."""
    if not ts_str:
        return None
    try:
        try:
            dt = parsedate_to_datetime(ts_str)
        except Exception:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
