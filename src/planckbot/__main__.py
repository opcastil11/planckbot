"""Entry point: python -m planckbot"""

import sys


def main():
    if "--version" in sys.argv:
        from planckbot import __version__
        print(f"planckbot {__version__}")
        return

    from planckbot.ui.app import start_app
    start_app()


if __name__ == "__main__":
    main()
