"""Ultimate DJ — top-level package.

Sets per-monitor DPI awareness before anything pulls in Tk / CTk /
WebView2 so the Spotify (and other) embedded browsers report the right
viewport size to their host pages and don't render `position: fixed`
elements (like Spotify's playback bar) below the visible area.

Safe to import multiple times: SetProcessDpi* fails with ACCESS_DENIED
on the second call, which we silently swallow.
"""
import sys as _sys

if _sys.platform == "win32":
    try:
        import ctypes as _ctypes
        try:
            # Windows 10 1703+: per-monitor v2 (best — handles monitor moves)
            _ctypes.windll.user32.SetProcessDpiAwarenessContext(
                _ctypes.c_void_p(-4))   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            try:
                # Windows 8.1+: per-monitor v1
                _ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                # Vista+: system-wide DPI aware
                _ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
