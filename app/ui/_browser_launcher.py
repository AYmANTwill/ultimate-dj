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

    # Force the embedded Edge to render at device scale factor 1.0
    # AND disable its own DPI scaling. Without this, on a 125 % / 150 %
    # Windows scaling setup the WebView2 child reports a CSS viewport
    # height to Spotify that's bigger than what the host Tk window can
    # actually show — Spotify then puts its `position: fixed; bottom: 0`
    # playback bar below the visible area and it looks like it's
    # missing. Forcing a flat 1.0 scale makes the WebView2's idea of
    # the viewport match exactly what we display in the parent HWND.
    extra_args = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    forced_args = (
        "--high-dpi-support=1 "
        "--force-device-scale-factor=1 "
        "--disable-features=msEdgeRedirectsXdg"
    )
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"{extra_args} {forced_args}".strip())

    # Also make this launcher process per-monitor DPI aware so SetParent
    # in the host doesn't fall into mixed-DPI scaling rules.
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4))     # PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    import webview  # noqa: E402  (must come AFTER env vars are set)

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
