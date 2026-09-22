"""The local web app — `scieflow serve`. Loopback only, one user, no CORS."""

from scieflow.web.app import create_app

__all__ = ["create_app"]
