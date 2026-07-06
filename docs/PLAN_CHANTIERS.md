# Plan de remédiation — les 4 chantiers actifs

> Créé le 2026-07-02, ancré dans le code réel (fichiers + lignes vérifiés).
> Compagnon de [PROJECT_MAP.md](PROJECT_MAP.md) §6 — quand une étape est
> livrée, cocher ici ET mettre à jour l'état dans la map + son journal.
>
> Chantiers couverts : **A1** (repair v2 WAV), **C3** (delta L4 dans le
> Mixer), **C1** (croissance corpus), **L5** (signal feedback). Les items
> satellites A2/A3/A4 et C4 sont intégrés là où ils appartiennent.

---

## Le flywheel — pourquoi ces 4 chantiers se renforcent

```
C3 expose les désaccords L4-vs-heuristique dans le Mixer
        │
        ▼
L5 collecte des votes 👍/👎 CIBLÉS sur ces désaccords
   (labels à très haute valeur d'entraînement — active learning)
        │
        ▼
C4 (hook) + feedback.py:133 déclenchent l'auto-retrain
        │
        ▼
L4 s'améliore → moins de désaccords → le cycle se resserre
        │
   C1 en parallèle élargit la couverture (plus de paires réelles)
```

A1 est orthogonal (sécurité fichiers) mais bloque le déverrouillage de
`write_tags` et la réouverture des 363 WAV dans Rekordbox.

## Ordre d'exécution recommandé

| Phase | Contenu | Risque fichier | Effort |
|---|---|---|---|
| **1 — immédiat** | C3 → L5-UX → C4-hook | Zéro (engine + UI seulement) | ~1,5 j |
| **2 — continu** | C1 batches de scraping | Zéro | fond de session |
| **3 — au déblocage user** | A4 tests → A1 v2 → A2 guards → A3 UI | Mutation audio (TDD strict) | ~2,5 j |

La phase 3 reste ⏸️ tant que tu n'as pas dit GO (ta pause explicite sur A1).

---

## Chantier C3 — Delta L4-vs-heuristique dans le Mixer

**Objectif** : voir dans le breakdown OÙ le modèle Siamese est en
désaccord avec l'heuristique — et le signaler dans la liste de
suggestions.

**État** : design validé en session précédente ; les points d'accroche
existent tous.

### Étapes

1. **Engine** — `app/engine/library.py` :
   `transition_score_breakdown()` (ligne 1196) retourne déjà les
   composantes. Ajouter 3 clés calculées :
   - `heuristic_total` : sous-total AVANT la contribution L4
   - `l4_delta` : les points L4 signés (déjà bornés ±10)
   - `l4_verdict` : `'agree'` (delta et heuristique de même signe relatif),
     `'dispute'` (|delta| ≥ 6 et sens opposé au classement heuristique),
     `'neutral'` (|delta| < 6), `'absent'` (modèle non chargé → None)
   Fonction pure `l4_verdict(heuristic_total, l4_delta)` séparée pour
   être testable sans DB.
2. **Popup** — `app/ui/mixer.py` `_show_breakdown()` (ligne 389, consomme
   le breakdown ligne 401-402) : ajouter une ligne « Modèle L4 » avec
   badge coloré — vert `renforce (+7)`, orange `conteste (−9)`, gris
   `neutre`, pointillé `modèle absent`.
3. **Liste de suggestions** — même fichier, autour du builder de lignes
   (binding double-clic ligne 130) : glyphe ▲/▼ discret quand
   `l4_verdict == 'dispute'`.
4. **Tests** — `tests/test_engine.py` : `l4_verdict()` sur les 4 cas +
   clamp ±10 + propagation None (modèle manquant).

### Done quand
- Le popup montre la ligne L4 sur une vraie transition.
- Un ▼ apparaît sur au moins une suggestion contestée (il y en a —
  loss 0.29 ≠ accord parfait).
- `python -m pytest tests/ -q` vert.

**Risques** : aucun invariant touché ; ne PAS déplacer la logique dans
`ui/` (le verdict se calcule côté engine). Effort : **0,5-1 j**.

---

## Chantier L5 — Faire couler le signal feedback (2 votes → 50)

**Objectif** : le circuit 👍/👎 est branché de bout en bout
(`feedback.record()` ligne 90 → `_LIKE_BONUS`/+12, `_DISLIKE_PENALTY`/−25
lignes 54-55 → auto-retrain ligne 133). Le chantier n'est PAS du câblage,
c'est de la **réduction de friction** pour que les votes arrivent.

### Étapes

1. **Raccourcis clavier Mixer** — `app/ui/mixer.py` : binder deux touches
   (proposition : `F` = 👍, `D` = 👎) sur la transition sélectionnée,
   toast de confirmation. Une transition jugée sans quitter le clavier.
2. **File « le modèle doute »** (la synergie C3) : dans la page Mixer,
   petit encart listant les 3-5 transitions au verdict `dispute` pour le
   track chargé — un clic 👍/👎 tranche. Chaque vote ici vaut de l'or à
   l'entraînement : c'est exactement là où le modèle apprend le plus.
3. **Visibilité** — `app/ui/settings.py` (section IA) : ligne de statut
   `feedback.count()` + date du dernier retrain + « prochain retrain
   dans N votes » (seuil `AUTO_RETRAIN_THRESHOLD`, feedback.py:129).
4. **Tests** : `record()` puis `state()`/`score_modifier()` round-trip ;
   le seuil déclenche `maybe_auto_retrain` (monkeypatch, pas de vrai
   train).

### Done quand
- Voter ne prend qu'une touche.
- L'encart « le modèle doute » affiche des transitions réelles.
- Le compteur Settings monte ; **cible : 50 votes** puis vérifier que
  le breakdown montre la composante L5 sur les paires votées.

**Risques** : ne pas binder les touches globalement (seulement quand la
page Mixer a le focus), sinon collision avec la recherche. Effort : **0,5 j**.

---

## Chantier C4 (enabler de C1) — Auto-retrain après rebuild

**Objectif** : aujourd'hui `maybe_auto_retrain()` (transition_model.py:507)
n'est déclenché que par le delta de votes (feedback.py:133). Après un
« Reconstruire la matrice », les nouvelles paires n'entraînent rien.

### Étapes

1. `app/ui/settings.py` ligne ~716 : après
   `summary = cooccurrence.rebuild(conn, on_progress=progress)`, appeler
   `transition_model.maybe_auto_retrain()` dans le MÊME worker thread
   (le lock `_RETRAIN_LOCK` protège la concurrence), toast du résultat.
2. Vérifier que `training_pipeline.py:448` (l'autre appelant de
   `rebuild()`) enchaîne déjà sur un train — si oui ne rien doubler,
   sinon même hook.
3. Test : monkeypatch `maybe_auto_retrain` → assert appelé après rebuild.

**Done quand** : rebuild manuel → retrain se lance si les données ont
bougé. Effort : **~30 min**. Risque : zéro (le lock existe déjà).

---

## Chantier C1 — Croissance du corpus DJ (582 → 1500 paires)

**Objectif** : plus de paires positives réelles pour L2 et L4. Le
pipeline est opérationnel — c'est un chantier d'**opérations répétées**,
pas de code (sauf l'étape 1, optionnelle).

### Étapes

1. *(optionnel, 1 h)* `app/engine/discovery.py` : helper
   `suggest_artists(conn)` — extraire l'artiste du pattern
   « Artist - Title » dans `tracks.title` (source='user'), ranker par
   nombre de tracks. Donne la liste des catalogues à fort taux de match
   au lieu de deviner.
2. **Batches** (répéter par session, via la page Discover ou
   `enrich_corpus`) :
   - 2-3 artistes par batch, cap ~15 tracklists chacun ;
   - délai poli entre fetch (8-15 s) — les cookies utilisateur bypassent
     la limite invité mais un ban par compte n'a AUCUN fallback
     aujourd'hui (setlist.fm = item C2, pas commencé) ;
   - après chaque batch : rebuild → (C4) retrain automatique.
3. **Suivi des métriques** dans PROJECT_MAP §3 à chaque batch :
   paires L2, exemples L4, loss. Stop-condition d'un batch : le taux de
   match d'un artiste < 5 % → catalogue pas dans la lib, passer au
   suivant.

### Done quand
- ≥ **1500 paires** dans `track_pairs` ;
- loss L4 ≤ 0.29 maintenue (si elle remonte : sur-représentation d'un
  artiste → équilibrer les négatifs dans `extract_pairs`) ;
- zéro bannissement du compte 1001TL.

**Risques** : rate-limit par compte (mitigation : cadence + C2 en Plan B) ;
biais de catalogue (mitigation : varier les genres scrapés). Effort :
**continu, ~20 min de lancement par session**.

---

## Chantier A1 (+A2+A3+A4) — Repair v2 des 363 WAV ⏸️

**Objectif** : les 363 WAV ont un chunk `id3 ` APRÈS le chunk `data`
(+ taille RIFF incohérente). Rekordbox 7 / Engine DJ les refusent.
L'audio est intact.

**⚠ Découverte de recon** : la v1 (`repair.inspect()`, repair.py:88) rend
`'ok'` pour ces fichiers — elle ne détecte que le garbage AVANT le magic
(`RIFF` est bien à l'offset 0 ici). La v2 exige un **walker de chunks
RIFF**, ce n'est pas un patch de la v1.

**Séquence TDD stricte** (mutation d'audio utilisateur = la règle la plus
dure du projet) — **ne démarre qu'à ton GO explicite** :

### Étape 1 — A4 : les tests d'abord (0,5 j)

`tests/test_repair_v2.py`, fixtures synthétiques en `tmp_path` (aucun
vrai fichier de ta lib) :
- générer un WAV valide minimal (`struct.pack` : RIFF/WAVE + fmt + data) ;
- variante corrompue : chunk `id3 ` appendé après `data` + taille RIFF
  du header laissée fausse (reproduit le bug du legacy `write_tags`) ;
- asserts : (1) le walker flag la variante et PAS le WAV sain ;
  (2) après réparation `taille_RIFF == filesize − 8` ; (3) `wave.open()`
  stdlib relit le fichier ; (4) hash des octets audio du chunk `data`
  inchangé ; (5) idempotence (2ᵉ run = no-op) ; (6) refus propre si
  chunk `data` introuvable ; (7) le tail retiré est restaurable (undo).

### Étape 2 — A1 : le moteur (1 j)

`app/engine/repair.py`, en EXTENSION de l'API v1 (`inspect` / `repair` /
`scan_folder` restent compatibles) :
- `inspect_chunks(path) -> dict` : walk RIFF (`[4B id][4B size]`,
  word-aligned) ; détecte chunk(s) après `data`, taille RIFF fausse,
  taille `data` incohérente. Statuts : `ok | trailing_garbage |
  riff_size_mismatch | no_data_chunk | error`.
- `repair_trailing(path) -> dict` : temp-file + `os.replace` (même
  pattern atomique que v1 ligne 203-208) — copie jusqu'à la fin de
  `data`, patch la taille RIFF (octets 4-8, u32 LE).
- **Backup intelligent** : le tail retiré (quelques Ko) est sauvé dans
  `data/repair_tails/<sha1>.bin` + entrée `repair_history.json` (réutilise
  `_append_history`, ligne 138) avec `tail_file` + `data_end_offset` →
  **undo intégral sans coût disque** (pas de copie des 363 fichiers).
- `scan_folder()` : ajouter la passe chunks pour `.wav` ; le résumé
  distingue `prefix_corrupt` (v1) / `trailing_corrupt` (v2).
- Après réparation réussie : `tracks.corrupt = 0` via un helper library.

### Étape 3 — A2 : les guard rails (0,5 j)

Pour que ça ne se REPRODUISE jamais :
- `app/config.py` : opt-in PAR format (`write_tags_wav/flac/m4a`,
  tous False ; le global `write_tags_to_files` reste le master switch).
- `app/engine/analyzer.py` `write_tags()` : pre-flight magic-byte check
  (réutilise `repair._expected_magic` / `_find_magic_offset`) → refus
  loggé si le container n'est pas sain AVANT écriture ;
- post-save round-trip : re-walk des chunks (WAV) / magic check → si le
  fichier n'est plus conforme, restauration immédiate du snapshot
  pré-écriture + `log_warning` + `corrupt = 1`.
- Test : simuler une écriture corruptrice (monkeypatch) → assert
  restauration + flag.

### Étape 4 — A3 : l'UI (0,5 j)

- `app/ui/settings.py` section Réparer : bouton « Scanner (dry-run) »
  → compte par type de dégât ; bouton « Réparer N fichiers » avec la
  progress row pré-montée (pattern existant) ; lien vers l'historique
  (`repair.history()` existe déjà, ligne 164).
- Page Library : filtre `corrupt = 1` + action contextuelle « Réparer ».

### Done quand
- pytest vert (test_repair_v2 complet) ;
- dry-run sur ta lib : compte cohérent (~363) ;
- réparation batch : échantillon de 10 fichiers ouverts dans
  Rekordbox 7 avec succès AVANT de lancer les 353 restants ;
- undo vérifié sur 1 fichier (tail réappliqué → octets identiques).

### Risques
- **Fichier verrouillé** pendant la lecture (player sounddevice ou
  Rekordbox ouvert) → skip + report, ne jamais forcer.
- Chunks exotiques légitimes APRÈS data (`LIST INFO`, `cue `) : le
  walker les liste ; on ne tronque QUE si le chunk est `id3 `/inconnu
  ET que l'historique du bug le corrobore — sinon statut `review` et on
  n'y touche pas.
- Invariant projet : tout passe par `engine.repair` ; **aucune** écriture
  audio hors de ce module.

---

## Récap une ligne par chantier

| Chantier | Première action concrète | Effort | Gate |
|---|---|---|---|
| C3 | ✅ livré 2026-07-02 — `l4_verdict` + clés breakdown + popup + colonne L4 | fait | — |
| L5 | ✅ livré 2026-07-02 — touches F/D/X + encart doute + statut Settings (les VOTES restent à accumuler) | fait (code) | — |
| C4 | ✅ livré 2026-07-02 — retrain post-rebuild (opt-in + delta paires) | fait | — |
| C1 | 🟡 batch 1 terminé 2026-07-05 : retrain loss 0.2761, 584 paires ; a débusqué 3 bugs corrigés (`resolve_missing`↔matcher, pin `player_client:web`, `cookiesfrombrowser`) ; 608 manquants identifiés, 0 téléchargé (downloader réparé + vérifié depuis). **Relancer quand l'IP 1001TL est débloquée (quelques heures / VPN)** | continu | IP 1001TL rate-limitée |
| A1-A4 | ✅ code livré 2026-07-02 en TDD (16 tests, walker+undo+guards+UI) · dry-run réel : **363/451, 0 faux positif** · ⏳ réparation effective = clic user (10 d'abord → Rekordbox → le reste) | fait (code) | clic « Réparer » |
