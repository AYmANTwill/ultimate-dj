"""
Build the shareable **Lite** Windows app for a non-technical friend —
only Library + Download, with Deezer alongside the other sources and
a one-click updater pointed at the owner's GitHub Releases.

    python build_share_lite.py

Output: dist/UltimateDJ-Lite/  (zip the whole folder and send it)

Reuses build_share's preflight and binary-bundling so the two builds
never drift.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from build_share import ROOT, _fail, bundle_binaries, preflight

_OUT = ROOT / "dist" / "UltimateDJ-Lite"

_README_FR = """ULTIMATE DJ LITE — a lire avant de lancer
==========================================

1. Decompresse ce dossier ENTIER quelque part (Bureau, Documents...).
   Garde tous les fichiers ensemble.

2. Double-clique UltimateDJ-Lite.exe.
   Windows SmartScreen peut afficher un avertissement (editeur inconnu) :
   clique "Informations complementaires" puis "Executer quand meme".

{tools}

4. L'app a deux pages : Bibliotheque (gere tes dossiers de musique) et
   Telechargement (colle un lien YouTube, SoundCloud, Spotify ou
   Deezer). Pour Spotify : Reglages -> colle un Client ID + Secret
   (gratuits sur developer.spotify.com). Deezer et YouTube ne
   demandent rien.

5. Mises a jour : Reglages -> "Verifier les mises a jour". Quand une
   nouvelle version est publiee, l'app la telecharge et se relance
   toute seule.
"""

_TOOLS_INCLUDED = """3. FFmpeg et Node.js sont DEJA INCLUS (dossier bin) —
   rien d'autre a installer."""

_TOOLS_WINGET = """3. Au premier lancement, l'app installe FFmpeg et Node.js
   via winget (accepte si Windows demande)."""


def build() -> Path:
    print("== Build PyInstaller LITE (quelques minutes) ==")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "ultimate_dj_lite.spec"],
        cwd=ROOT, check=True)
    exe = _OUT / "UltimateDJ-Lite.exe"
    if not exe.is_file():
        _fail("Build termine mais UltimateDJ-Lite.exe est introuvable.")
    return _OUT


def main() -> None:
    preflight()
    out = build()
    bundled, missing = bundle_binaries(out)
    tools = _TOOLS_INCLUDED if not missing else _TOOLS_WINGET
    (out / "LISEZ-MOI.txt").write_text(
        _README_FR.format(tools=tools), encoding="utf-8")
    size_mb = sum(f.stat().st_size for f in out.rglob("*")
                  if f.is_file()) / 1e6
    print("\n== Termine (LITE) ==")
    print(f"  Dossier : {out}")
    print(f"  Taille  : {size_mb:.0f} MB")
    print(f"  Bundles : {', '.join(bundled) or 'aucun'}"
          + (f"  (manquants : {', '.join(missing)})" if missing else ""))
    print("\n  -> Zippe le dossier 'UltimateDJ-Lite' et envoie-le.")
    print("     Pour publier une mise a jour : bump app/version.py,")
    print("     rebuild, et attache le zip a une Release GitHub taggee.")


if __name__ == "__main__":
    main()
