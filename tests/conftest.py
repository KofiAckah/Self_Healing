"""Shared pytest configuration.

Each component lives in its own top-level directory (app/, chaos/, ...), so we
prepend those directories to sys.path to import them by module name without
packaging the whole repo.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for sub in ("app", "chaos", "remediation", "ai_analysis"):
    path = os.path.join(REPO_ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)
