"""
Standalone pywebview window launcher.
Run as a subprocess so the Tk main loop and the WebView2 message pump
don't fight each other.

Usage:
    python -m app.ui._browser_launcher <url> [<title>] [<storage_path>]

When <storage_path> is provided, WebView2 uses it as a persistent user
data folder — cookies, localStorage, and IndexedDB survive between
launches, so Spotify/YouTube/SoundCloud logins stick.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: browser_launcher <url> [title] [storage_path]")
        return 2

    url = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) >= 3 else "Ultimate DJ — Browser"
    storage_path = sys.argv[3] if len(sys.argv) >= 4 else ""

    # Set the WebView2 user data folder BEFORE importing webview, so the
    # underlying Edge runtime picks it up. This is what makes logins
    # persist across launches.
    if storage_path:
        Path(storage_path).mkdir(parents=True, exist_ok=True)
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = storage_path

    import webview  # noqa: E402  (must come AFTER env var is set)

    webview.create_window(title, url, width=1280, height=820, resizable=True)

    # private_mode=False  → session cookies/cache survive the process
    # storage_path        → pywebview's own settings dir (icons, etc.)
    if storage_path:
        webview.start(private_mode=False, storage_path=storage_path)
    else:
        webview.start(private_mode=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
