"""Backward-compatible spelling for the /api/genrate route."""

from api.generate import handler as GenerateHandler


class handler(GenerateHandler):
    """Own route handler class so Vercel discovers this endpoint."""
    pass
