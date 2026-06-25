import argparse

from sptchecker.app import SPTCheckerApp


def main():
    parser = argparse.ArgumentParser(description="SPT Mod Checker")
    parser.add_argument("--background", action="store_true",
                        help="Start minimized to the system tray")
    args = parser.parse_args()
    SPTCheckerApp(start_hidden=args.background).run()


if __name__ == "__main__":
    main()