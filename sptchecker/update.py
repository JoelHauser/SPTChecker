"""Notice when a newer release of this app has been published.

The app ships as a standalone exe with no auto-updater, so until now a fix
could only reach someone who happened to re-check where they downloaded it.
That is how a startup crash stayed live for a user who had already reinstalled
trying to clear it. This installs nothing -- it only notices that a newer
version exists so the UI can say so.

Checked against the app's own Forge listing rather than anywhere else,
because the Forge is where people actually download it: that is the version a
user would be comparing themselves to.
"""
from .config import APP_VERSION, FORGE_MOD_ID, FORGE_MOD_PAGE
from .feed import lookup_mod_by_id
from .utils import is_newer


def check_for_update(current=APP_VERSION, mod_id=FORGE_MOD_ID):
    """Return {"version", "url"} for a newer published release, else None.

    None covers every failure -- offline, the Forge down or rate limiting us,
    an unexpected payload -- because this is a background nicety. Not knowing
    whether a newer build exists is never worth interrupting anyone over, and
    the app's actual job is unaffected by it.

    Compared numerically rather than by string, so a version differing only in
    segment count ("3.3.1.0" against "3.3.1") can't nag someone already
    current, and a re-uploaded older version can't advertise itself as an
    upgrade.
    """
    mod = lookup_mod_by_id(mod_id)
    if not mod:
        return None
    latest = mod.get("version")
    if not latest or not is_newer(latest, current):
        return None
    return {"version": latest, "url": mod.get("link") or FORGE_MOD_PAGE}
