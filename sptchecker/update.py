"""Self-update check against the project's own GitHub releases.

The app has no auto-updater and is distributed as a standalone exe, so until
now a fix could only reach someone who happened to re-check where they
downloaded it. That's how a startup crash stayed live for a user who had
already tried reinstalling. This doesn't install anything -- it only notices
that a newer release exists and lets the UI say so.

Deliberately points at GitHub rather than the Forge: releases are published
there, it costs sp-mod.com nothing, and it stays clear of the rate limits the
Forge meters us against.
"""
import requests

from .config import APP_VERSION, GITHUB_RELEASES_API
from .utils import is_newer

_session = requests.Session()
_session.headers["User-Agent"] = f"SPTModChecker/{APP_VERSION}"
_HEADERS = {"Accept": "application/vnd.github+json"}


def latest_release():
    """The newest published release tag, or None.

    None covers every failure -- offline, GitHub down, rate limited, an
    unexpected payload shape -- because this is a background nicety. Nothing
    here is worth surfacing an error over: the app's actual job is unaffected
    by not knowing whether it's current.
    """
    try:
        resp = _session.get(GITHUB_RELEASES_API, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    return tag if isinstance(tag, str) and tag.strip() else None


def check_for_update(current=APP_VERSION):
    """Return the newer release's tag if one exists, else None.

    Compared numerically rather than by string, so a tag that merely differs
    in decoration ("V3.3.1" against "3.3.1") or in segment count ("3.3.1.0")
    doesn't nag someone who is already up to date -- and a republished older
    release can never advertise itself as an upgrade.
    """
    tag = latest_release()
    if tag and is_newer(tag, current):
        return tag
    return None
