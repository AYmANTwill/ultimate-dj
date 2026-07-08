"""
Build the shareable Windows app — one self-contained folder your friends
just unzip and double-click. No Python, no pip, no dependency install on
their side.

    python build_share.py

Output: dist/UltimateDJ/  (zip this whole folder and send it)

What it verifies BEFORE building — the things that broke folder-sharing:
  * Python 3.10 or 3.11 (librosa/numba/numpy stack is pinned to these)
  * 64-bit interpreter
  * PyInstaller present
  * every core runtime import actually importable in THIS environment
It refuses to build on a mismatched environment rather than shipping a
broken exe.
"""
from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE_IMPORTS = [
    "customtkinter", "yt_dlp", "spotipy", "librosa", "numpy", "soundfile",
    "mutagen", "PIL", "sounddevice", "cloudscraper", "bs4", "lxml",
    "keyring", "numba",
]


def _fail(msg: str) -> None:
    print(f"\n[X] {msg}")
    sys.exit(1)


def preflight() -> None:
    print("== Pre-vol ==")
    v = sys.version_info
    print(f"  Python {v.major}.{v.minor}.{v.micro}  "
          f"({platform.architecture()[0]})")
    if (v.major, v.minor) not in ((3, 10), (3, 11)):
        _fail(f"Python {v.major}.{v.minor} non supporte — utilise 3.10 ou "
              f"3.11 (la stack librosa/numba/numpy y est epinglee).")
    if sys.maxsize <= 2**32:
        _fail("Interpreteur 32-bit — un build 64-bit est requis.")
    if importlib.util.find_spec("PyInstaller") is None:
        _fail("PyInstaller manquant — installe-le : pip install pyinstaller")
    missing = [m for m in CORE_IMPORTS if importlib.util.find_spec(m) is None]
    if missing:
        _fail("Dependances runtime manquantes dans cet environnement : "
              + ", ".join(missing)
              + "\n    Lance d'abord : pip install -r requirements.txt")
    print("  Toutes les dependances coeur sont importables. OK.\n")


def build() -> Path:
    print("== Build PyInstaller (quelques minutes) ==")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "ultimate_dj.spec"],
        cwd=ROOT, check=True)
    out = ROOT / "dist" / "UltimateDJ"
    if not (out / "UltimateDJ.exe").is_file():
        _fail("Le build s'est termine mais UltimateDJ.exe est introuvable.")
    return out


_README_FR = """ULTIMATE DJ — a lire avant de lancer
=====================================

1. Decompresse ce dossier ENTIER quelque part (Bureau, Documents...).
   Garde tous les fichiers ensemble — ne sors pas UltimateDJ.exe seul.

2. Double-clique UltimateDJ.exe.
   Windows SmartScreen peut afficher un avertissement (editeur inconnu) :
   clique "Informations complementaires" puis "Executer quand meme".

3. Au premier lancement, l'app installe ce qui lui manque tout seul :
   - FFmpeg et Node.js via winget (accepte si Windows demande).
   Si winget n'est pas dispo, installe-les a la main :
     FFmpeg  : https://www.gyan.dev/ffmpeg/builds/  (ajoute /bin au PATH)
     Node.js : https://nodejs.org/  (version LTS)

4. Pour telecharger depuis Spotify : Reglages -> Spotify API -> colle un
   Client ID + Secret (gratuits sur developer.spotify.com).

Les fonctions IA lourdes (modele de transition, scraping) sont
optionnelles et s'installent a la demande depuis les Reglages.

Aucune connexion n'est requise pour gerer ta bibliotheque locale.
"""


def main() -> None:
    preflight()
    out = build()
    (out / "LISEZ-MOI.txt").write_text(_README_FR, encoding="utf-8")
    size_mb = sum(f.stat().st_size for f in out.rglob("*")
                  if f.is_file()) / 1e6
    print("\n== Termine ==")
    print(f"  Dossier : {out}")
    print(f"  Taille  : {size_mb:.0f} MB")
    print(f"  Fichiers: {sum(1 for _ in out.rglob('*'))}")
    print("\n  -> Zippe le dossier 'UltimateDJ' entier et envoie-le.")
    print("     Ton ami decompresse et double-clique UltimateDJ.exe.")


if __name__ == "__main__":
    main()
