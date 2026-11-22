"""Expose the MadadPass ASGI application to the hosting runtime."""

import os
from pathlib import Path
from tempfile import gettempdir

from madadpass.app import create_app

database = os.environ.get("MADADPASS_DB")
if not database and os.environ.get("VERCEL") == "1":
    database = Path(gettempdir()) / "madadpass-demo" / "madadpass.sqlite3"

app = create_app(db_path=database)
