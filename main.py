import argparse
import sys

from sptchecker.app import SPTCheckerApp
from sptchecker.config import SHOW_PROTOCOL
from sptchecker.platform import signal_running_instance


def _is_show_request():
    """Whether Windows launched us by activating our own URI scheme.

    Checked ahead of argparse, which would reject the URI as an unrecognised
    positional and exit -- so a toast click would have killed the new process
    with a usage error instead of showing anything.
    """
    return any(a.lower().startswith(f"{SHOW_PROTOCOL}://") for a in sys.argv[1:])


def main():
    if _is_show_request():
        # A copy is already running: hand the request over and get out of the
        # way, rather than putting a second window and a second tray icon on
        # screen for what the user experienced as one click.
        if signal_running_instance():
            return
        # Nothing was listening, so the app isn't running -- the click should
        # still start it, and visibly: asking to be shown is the whole point.
        SPTCheckerApp(start_hidden=False).run()
        return

    parser = argparse.ArgumentParser(description="SPTChecker")
    parser.add_argument("--background", action="store_true",
                        help="Start minimized to the system tray")
    args = parser.parse_args()
    SPTCheckerApp(start_hidden=args.background).run()


if __name__ == "__main__":
    main()
