"""PyInstaller runtime hook — runs before any app import in the Lite
build and flips the edition flag. app.edition reads this env var to
show the trimmed sidebar (Library + Download only)."""
import os

os.environ.setdefault("ULTIMATEDJ_LITE", "1")
