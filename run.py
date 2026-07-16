"""
Ultimate DJ — entry point.
Checks dependencies, then launches the GUI.
"""
import sys
import os

# Ensure the project root is on the path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Mark the process DPI-aware BEFORE any GUI window is created.
# Without this, the Tk window lives in Windows' virtualised 96-DPI
# coordinate space while the WebView2 child window (Edge) is per-monitor
# DPI-aware → mixed-mode SetParent gives Spotify the wrong innerHeight
# and its `position: fixed; bottom: 0` playback bar renders below the
# visible viewport. Per-monitor-v2 is the right level on Win10+; we
# fall back to system-aware then legacy on older Windows.
if sys.platform == "win32":
    try:
        import ctypes
        try:
            # Windows 10 1703+: per-monitor v2 (best, handles monitor moves)
            ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4))     # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            try:
                # Windows 8.1+: per-monitor v1
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                # Vista+: system-wide DPI aware
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Ensure Node.js is on PATH before any yt-dlp import
for candidate in [r"C:\Program Files\nodejs", os.path.expandvars(r"%LOCALAPPDATA%\fnm_multishells")]:
    if os.path.isdir(candidate) and candidate not in os.environ.get("PATH", ""):
        os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")


def main():
    # Packaged-exe helper mode: the embedded browser can't spawn
    # `python -m app.ui._browser_launcher` (sys.executable IS the app,
    # not python), so browser.py re-launches this same exe with a
    # sentinel argv — route straight to the webview launcher instead of
    # booting the full GUI a second time.
    if len(sys.argv) >= 2 and sys.argv[1] == "--browser-launcher":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from app.ui._browser_launcher import main as launcher_main
        sys.exit(launcher_main())

    # Step 1: Auto-install missing dependencies (shows splash if needed)
    from app.deps import ensure_deps
    ensure_deps()

    # Step 2: Launch GUI
    from app.ui.app import App
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
