"""Expose the MadadPass ASGI application to the hosting runtime."""

from madadpass.app import create_app

app = create_app()
