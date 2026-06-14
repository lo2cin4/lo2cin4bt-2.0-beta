"""lo2cin4bt browser-first app launcher."""

from __future__ import annotations

import importlib
import argparse
import logging
import os
import sys
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Timer

import uvicorn

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

_utils = importlib.import_module("utils")
get_console = _utils.get_console
show_error = _utils.show_error
show_info = _utils.show_info
show_welcome = _utils.show_welcome

APP_HOST = "127.0.0.1"
APP_PORT = 2424
AUTO_OPEN_BROWSER = True

console = get_console()


def setup_logging() -> logging.Logger:
    log_dir = CURRENT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "main.log"

    logger = logging.getLogger("lo2cin4bt")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    return logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the lo2cin4bt local app.")
    parser.add_argument(
        "--host",
        default=os.environ.get("LO2CIN4BT_HOST", APP_HOST),
        help="Host to bind. Defaults to LO2CIN4BT_HOST or 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LO2CIN4BT_PORT", str(APP_PORT))),
        help="Port to bind. Defaults to LO2CIN4BT_PORT or 2424.",
    )
    parser.add_argument(
        "--no-browser",
        "--no-open-browser",
        action="store_true",
        help="Start the server without opening a browser tab.",
    )
    return parser.parse_args(argv)


def _app_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _auto_open_browser_enabled(args: argparse.Namespace) -> bool:
    if args.no_browser:
        return False
    value = str(os.environ.get("LO2CIN4BT_AUTO_OPEN_BROWSER", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    app_url = _app_url(args.host, args.port)
    logger = setup_logging()
    logger.info("Starting lo2cin4bt unified app v2")

    show_welcome(
        "lo2cin4bt",
        "[bold #dbac30]Lo2cin4BT App[/bold #dbac30]\n[white]Launching the browser-first React + FastAPI workspace.[/white]",
    )
    show_info("MAIN", f"App URL: {app_url}")
    show_info("MAIN", "main.py now starts the unified web app directly.")

    try:
        create_app = importlib.import_module("app.api").create_app
        app = create_app(CURRENT_DIR)
        if AUTO_OPEN_BROWSER and _auto_open_browser_enabled(args):
            Timer(1.2, _open_browser, args=(app_url,)).start()
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
            access_log=False,
        )
    except ImportError as exc:
        show_error("MAIN", f"Import failed: {exc}")
        logger.error("Import failed: %s", exc)
    except Exception as exc:  # pragma: no cover
        show_error("MAIN", f"Launcher failed: {exc}")
        logger.exception("Launcher error")


if __name__ == "__main__":
    main()
