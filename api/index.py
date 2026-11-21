"""Vercel entry point — wraps the FastAPI app with Mangum (ASGI → serverless)."""
from __future__ import annotations
import os
import sys

# Ensure the repo root is on sys.path so `from app.main import app` resolves.
# On Vercel, __file__ is  /var/task/api/index.py  so parent of parent is /var/task.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from mangum import Mangum  # noqa: E402
from app.main import app   # noqa: E402

# lifespan="off" avoids startup/shutdown lifecycle issues in serverless where
# there is no persistent process to run lifespan events.
# Tables are created lazily on first DB access instead (see app/main.py).
handler = Mangum(app, lifespan="off")
