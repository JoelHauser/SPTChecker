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
