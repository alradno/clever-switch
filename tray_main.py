import pathlib
import sys
import traceback

if __name__ == "__main__":
    try:
        from cleverswitch.tray.app import main

        main()
    except Exception:
        # Windowed apps have no stderr. Keep one local error report instead of
        # showing an unhandled-exception dialog; never log input events.
        directory = pathlib.Path.home() / ".config/cleverswitch-tray"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "last-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        sys.exit(1)
