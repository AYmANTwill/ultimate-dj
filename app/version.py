"""Single source of truth for the app version.

Bump this on every release; the auto-updater compares it against the
latest GitHub release tag to decide whether an update is available.
Keep it in sync with the git tag (tag `vX.Y.Z` ↔ `__version__` `X.Y.Z`).
"""
__version__ = "1.6.1"
