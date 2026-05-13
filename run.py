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

# Ensure Node.js is on PATH before any yt-dlp import
for candidate in [r"C:\Program Files\nodejs", os.path.expandvars(r"%LOCALAPPDATA%\fnm_multishells")]:
    if os.path.isdir(candidate) and candidate not in os.environ.get("PATH", ""):
        os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")


def main():
    # Step 1: Auto-install missing dependencies (shows splash if needed)
    from app.deps import ensure_deps
    ensure_deps()

    # Step 2: Launch GUI
    from app.ui.app import App
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
