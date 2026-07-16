# 🚀 LAUNCH PLAN — Ultimate DJ version finale

> **Rôle de ce document.** Runbook de lancement de la version finale
> partageable. Il vit À PART : on ne l'exécute PAS tant que l'app n'est
> pas déclarée complète, mais on le MET À JOUR dans le même commit que
> tout changement qui touche build, packaging, dépendances, premier
> lancement ou distribution. Le jour du GO, on le déroule du §2 au §8
> sans réfléchir.
>
> Règle jumelle des règles README/PROJECT_MAP (voir CLAUDE.md).

---

## 1. Critères « l'app est complète » (GO / NO-GO)

Le lancement s'exécute quand TOUTES les cases sont cochées. Statut au
2026-07-15 :

| # | Critère | État |
|---|---------|------|
| G1 | Sprint A (réparation fichiers) 100 % | ✅ 363/363 WAV réparés, undo conservé |
| G2 | Tests verts en CI (badge GitHub Actions) | ✅ 56/56 local — vérifier le badge au GO |
| G3 | Build partageable vérifié (B5+B6) | ✅ 928 Mo, exe vivant, bin/ smoke-testé |
| G4 | Migration CLAP terminée + éval per-track ≥ baseline | 🟡 en cours (analyse ~100/607) — AUC unseen à battre : 0.546 |
| G5 | B2 audit silent-swallows terminé | ✅ clos 2026-07-15 — 13 log_warning data-paths, reste classé légitime, politique « nouveau swallow data = log » |
| G6 | C2 setlist.fm actif (clé collée + bouton 🧪 vert) | 🟡 bouton livré, clé user manquante |
| G7 | 50 votes L5 → feedback mesurable au breakdown | 🟡 2/50 (action user, pas du code) |
| G8 | Décision signing : certificat code OU SmartScreen assumé dans LISEZ-MOI | 🟡 par défaut : assumé (documenté) |
| G9 | Version marquée : `CHANGELOG.md` à jour + tag git `vX.Y` | ⬜ au GO |

**NO-GO automatique** si : un test rouge, l'exe ne survit pas 15 s, un
fichier > 800 LOC introduit, ou une écriture audio hors `engine.repair`.

---

## 2. Pré-vol machine de build

```bash
python --version        # DOIT être 3.10.x ou 3.11.x, 64-bit
git status --short      # arbre PROPRE (tout commité)
python -m pytest tests/ -q          # 100 % vert
ffmpeg -version && node --version   # présents (seront bundlés)
```

- Espace disque : ≥ 4 Go libres (build 928 Mo + work/ temporaire).
- `build_share.py` refuse de builder si l'environnement ment — le
  laisser refuser, ne jamais forcer.

## 3. Build

```bash
python build_share.py
```

Fait tout seul : pré-vol imports → PyInstaller (`ultimate_dj.spec`) →
copie + smoke-run de `ffmpeg/ffprobe/node` dans `dist/UltimateDJ/bin/`
→ écrit `LISEZ-MOI.txt` (variante « DEJA INCLUS » si bundle complet).

## 4. Vérifications post-build (toutes obligatoires)

| ✓ | Vérification | Commande / attendu |
|---|--------------|--------------------|
| ⬜ | Exe vivant 15 s | lancer `dist/UltimateDJ/UltimateDJ.exe`, attendre 15 s, processus présent, GUI affichée, kill |
| ⬜ | bin/ complet | `ffmpeg.exe ffprobe.exe node.exe` présents ; `bin/ffmpeg.exe -version` répond |
| ⬜ | LISEZ-MOI correct | contient « DEJA INCLUS » (sinon le bundle a échoué) |
| ⬜ | Rien de privé dans dist/ | AUCUN `.claude/`, `docs/`, script `_check_*`, `data/` personnelle (DB, cookies 1001TL, tails) |
| ⬜ | Icône visible | l'exe montre le vinyle dans l'Explorateur |
| ⬜ | Taille plausible | ~930 Mo ±10 % (dérive = dépendance embarquée par accident — investiguer) |
| ⬜ | Navigateur intégré | ouvrir la page Download → panneau browser s'ouvre (route `--browser-launcher`) |

## 5. Test d'acceptation « machine ami » (avant TOUT envoi)

Sur un AUTRE PC Windows (ou VM propre, sans Python ni ffmpeg) :

1. Dézipper, double-clic `UltimateDJ.exe` → SmartScreen : « Exécuter
   quand même » → l'app s'ouvre.
2. Réglages → coller Client ID/Secret Spotify → sauvegarder.
3. Télécharger UNE track (prouve yt-dlp + ffmpeg + node bundlés).
4. Scanner un petit dossier de musique (prouve librosa/numba figés).
5. Ouvrir le panneau browser intégré (prouve WebView2 + route argv).
6. Fermer/rouvrir l'app (prouve la persistance config/DB côté ami).

Un seul échec = retour au §3 après fix. Ne JAMAIS envoyer un zip non
passé par ce §5.

## 6. Distribution

- Zipper le dossier `UltimateDJ` ENTIER → `UltimateDJ-vX.Y.zip`.
- Canal : lien direct (Drive/OneDrive/WeTransfer — ~1 Go). Pas de
  GitHub Release public tant que le repo contient l'historique des
  playlists perso dans les exemples — à re-vérifier au GO.
- Joindre le message : « dézippe TOUT le dossier, lis LISEZ-MOI.txt,
  double-clique UltimateDJ.exe ».

## 7. Post-lancement

```bash
git tag vX.Y && git push origin vX.Y
```

- `CHANGELOG.md` : section vX.Y gelée le jour du GO.
- Garder le zip N-1 sur disque (rollback trivial : renvoyer l'ancien).
- Support : demander à l'ami `data/app.log` (+ `data/app.log.1`) — le
  journal in-app (Réglages → Journal) affiche la même chose.

## 8. Retours amis → hotfix

1. Repro local en mode dev (`python run.py`).
2. Fix + test + commit (règles habituelles).
3. Re-dérouler §2→§6. Pas de patch à la main dans un dist existant.

---

## 9. Journal de ce plan

| Date | Changement |
|------|-----------|
| 2026-07-15 | G5 (B2 silent-swallows) → ✅. GO désormais bloqué par : CLAP (G4), clé setlist.fm (G6), votes L5 (G7). |
| 2026-07-15 | Création. B5+B6 verts (build 928 Mo vérifié, bin/ bundlé, WebView2 frozen-safe, icône). GO bloqué par : CLAP (G4), B2 (G5), clé setlist.fm (G6), votes L5 (G7). |
