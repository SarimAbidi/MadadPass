import argparse
import logging
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from madadpass.app import create_app


def load_environment():
    path = Path(__file__).with_name(".env")
    if path.exists():
        for key, separator, value in (line.strip().partition("=") for line in path.read_text(encoding="utf-8-sig").splitlines()):
            if separator and key in {"GEMINI_API_KEY", "GEMINI_MODEL", "GROQ_API_KEY", "GROQ_MODEL", "MADADPASS_DB", "MADADPASS_SECRET"}:
                os.environ.setdefault(key, value.strip().strip("\"'"))


def main():
    parser = argparse.ArgumentParser(description="Run the local MadadPass synthetic prototype.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", help="Path to an alternate SQLite data file")
    parser.add_argument("--no-browser", action="store_true")
    options = parser.parse_args()
    load_environment()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    url = f"http://127.0.0.1:{options.port}"
    print(f"MadadPass is available at {url} after startup. Press Ctrl+C to stop.", flush=True)
    if not options.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(db_path=options.db), host="127.0.0.1", port=options.port, access_log=False, ws="none")


if __name__ == "__main__":
    main()
