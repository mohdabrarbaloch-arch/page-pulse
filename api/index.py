"""Vercel entry point — wraps the FastAPI app with Mangum (ASGI → Lambda/Vercel)."""

from __future__ import annotations

import os
import sys

# Ensure the repo root is on the path so `from app.main import app` works.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mangum import Mangum  # noqa: E402

from app.main import app  # noqa: E402

handler = Mangum(app, lifespan="auto")
