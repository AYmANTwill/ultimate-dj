"""Which edition is running — full or the trimmed 'Lite' build.

Lite is the same codebase with a smaller surface for a non-technical
user: only Library + Download. It's selected at build time by the
lite PyInstaller spec, which ships a runtime hook that sets
ULTIMATEDJ_LITE=1 before anything imports. In dev you can force it with
``set ULTIMATEDJ_LITE=1`` (Windows) / ``ULTIMATEDJ_LITE=1 python run.py``.
"""
from __future__ import annotations

import os

IS_LITE = os.environ.get("ULTIMATEDJ_LITE", "").strip().lower() in (
    "1", "true", "yes", "on")

APP_NAME = "Ultimate DJ Lite" if IS_LITE else "Ultimate DJ"

# Sidebar sections a Lite build is allowed to show. app.ui.app filters
# SIDEBAR_GROUPS against this when IS_LITE. Full build ignores it.
LITE_PAGES = {"Download", "Library", "Settings"}
