# Ultimate DJ — Carte vivante du projet

> **Document évolutif.** C'est LA vue d'ensemble : chaque sous-système, chaque
> item, la base de données, le pipeline IA, et leurs relations profondes.
>
> **Protocole de mise à jour** (chaque session qui fait bouger un item) :
> 1. Modifier la colonne **État** de l'item concerné (sections 1 et 6).
> 2. Ajouter une ligne datée au **Journal** (section 7). Ne jamais réécrire
>    l'historique.
> 3. Si les chiffres IA changent (pairs, loss, votes), mettre à jour la
>    section 3.
>
> Légende : ✅ livré et stable · 🟡 partiel / en cours · 🔴 à faire · ⏸️ reporté
>
> Documents frères : [AUDIT.md](AUDIT.md) (santé 12 dimensions, point-in-time),
> [ECC_USAGE.md](ECC_USAGE.md) (outillage IA de dev).
> **Version 3D interactive : [PROJECT_MAP.html](PROJECT_MAP.html)** — double-clic,
> s'ouvre dans le navigateur, 100 % offline (aucun CDN). Même snapshot de données.
> Inclut l'**arbre de quêtes** (bouton ⚔ dans le header) : chaque chantier en
> quête ✅/🟡/🔓/🔒 avec XP, dépendances, bloqueurs et prochaine quête recommandée.
> Snapshot initial : v1.4, 2026-07-02 — 18 142 LOC, 46 fichiers Python
> (5 racine + 20 engine + 21 ui).

---

## 0. Vue macro

```mermaid
flowchart TD
    subgraph EXT["Sources externes"]
        TL["1001tracklists<br/>(Playwright + cookies)"]
        YT["YouTube / SoundCloud<br/>(yt-dlp)"]
        SP["API Spotify"]
        FMA["Free Music Archive"]
    end

    subgraph UI["app/ui — 21 fichiers CTk"]
        PAGES["Pages : Home · Library · Mixer · Setlist<br/>Analyze · Download · Discover · Settings"]
        TRAY["ActivityTray<br/>(status-bar racine)"]
    end

    subgraph ENGINE["app/engine — 20 fichiers, Python pur, AUCUN import Tk"]
        LIB["library.py<br/>DB + transition_score"]
        ANA["analyzer / embeddings /<br/>segmentation"]
        SCRAPE["tracklists / discovery /<br/>cooccurrence / training_pipeline"]
        MODEL["transition_model (L4)<br/>feedback (L5)"]
        ACQ["downloader / spotify /<br/>playlist_sync / fma"]
        MAINT["repair / backup / export"]
        PLAY["player / tasks"]
    end

    subgraph DATA["Données sur disque"]
        DB[("data/library.db<br/>SQLite WAL")]
        PT["data/models/transition.pt"]
        CACHE["data/tracklists/*.json"]
        AUTH["data/tracklists_auth_state.json"]
    end

    UI -->|"appels directs<br/>(jamais l'inverse)"| ENGINE
    TL --> SCRAPE
    YT --> ACQ
    SP --> ACQ
    FMA --> ACQ
    ENGINE <--> DB
    MODEL <--> PT
    SCRAPE <--> CACHE
    SCRAPE <--> AUTH
```

Règle architecturale n°1 : `engine/` n'importe **jamais** `ui/`
(0 import inverse, vérifié). Les modules engine reçoivent `conn` en
paramètre — testables sans UI.

---

## 1. Arbre des sous-systèmes

### 1.1 Boot & fondations — ✅ stable

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `run.py` | — | Point d'entrée : check deps → `App().mainloop()` | ✅ |
| `app/__init__.py` | 28 | DPI awareness per-monitor, **AVANT tout import Tk** — ne pas déplacer | ✅ |
| `app/config.py` | 198 | Chemins, thème, opt-ins (`write_tags_to_files`=False par défaut) | ✅ |
| `app/deps.py` | 242 | Installeur premier-lancement ; torch + Chromium opt-in | ✅ |
| `app/logger.py` | 58 | Logger fichier rotatif (`log_info` / `log_warning`) | ✅ |
| `app/secrets_store.py` | 149 | Wrapper `keyring` → Windows Credential Manager (Spotify + 1001TL) ; migration auto du plaintext legacy | ✅ |

Boot < 1 s à cache chaud (imports lazy, librosa différé en worker).

### 1.2 Bibliothèque & données — ✅ cœur stable, 🟡 volume

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `engine/library.py` | 1377 | Schéma DB + migrations inline, `transition_score()` (scorer central L1-L5), trash/undo 30 j, `find_audio_duplicate()` (moitié audio du matching hybride) | ✅ (🟡 1377 LOC — extraire le scoring un jour) |
| `ui/library.py` | 792 | Treeview + filtres + ops bulk + panneau transitions | ✅ |
| `ui/fastlist.py` | 291 | Liste virtualisée performante | ✅ |
| `ui/track_editor.py` | 424 | Édition métadonnées d'un track | ✅ |
| `ui/duplicates.py` | 333 | Détection/fusion de doublons | ✅ |
| `ui/camelot.py` | 200 | Roue Camelot interactive | ✅ |

### 1.3 Analyse audio — ✅ livré, 🔴 write_tags gelé

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `engine/analyzer.py` | 249 | BPM / key / energy via librosa ; `write_tags()` **gated opt-in** | ✅ analyse · 🔴 write_tags gelé tant que repair v2 pas livré |
| `engine/embeddings.py` | 366 | **L1** — fingerprint 256-d (lite/CLAP/PANNs), L2-normalisé, stocké en BLOB dans `tracks.embedding` | ✅ |
| `engine/segmentation.py` | 235 | **L3 v2** — intro/outro/drops par **richesse spectrale** (RMS + ratio HF ≥ 4 kHz, persistance 8 s, breakdown→drop) → `tracks.intro_end/outro_start/drops` | 🟡 moteur livré, backfill après spot-check user |
| `ui/analyze.py` | 301 | Page analyse batch avec progression | ✅ |

### 1.4 Acquisition de musique — ✅ livré

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `engine/downloader.py` | 364 | Wrapper yt-dlp (auto-update hebdo) | ✅ |
| `engine/spotify.py` | 151 | Résolution playlists/métadonnées Spotify | ✅ |
| `engine/playlist_sync.py` | 238 | Sync playlist Spotify → téléchargements | ✅ |
| `engine/fma.py` | 365 | Import dataset FMA Small (corpus d'entraînement, source='fma') | ✅ |
| `ui/download.py` | 903 | Page download : browser de dossiers + résolution Spotify | ✅ (🟡 903 LOC) |
| `ui/browser.py` + `ui/_browser_launcher.py` | 507+82 | WebView2 embarqué (Spotify/SoundCloud) ; force-scale-factor=1 + DPI avant `import webview` | ✅ |

### 1.5 Scraping & corpus DJ — ✅ pipeline complet

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `engine/tracklists.py` | 1234 | Scraping 1001tracklists : login Chromium visible, cookies `_AUTH_STATE_PATH` rechargés cross-thread par mtime, Playwright **thread-local** (`_PW_TLS`), parse schema.org, matcher hybride precision-first (token-sort noms + dedup audio cosine 0.92) | ✅ (🟡 1234 LOC, 3 responsabilités à séparer) |
| `engine/discovery.py` | 371 | Découverte de tracklists par artiste | ✅ |
| `engine/cooccurrence.py` | 333 | **L2** — mine les sets scrapés → `track_pairs` (decay positionnel) | ✅ 582 paires |
| `engine/training_pipeline.py` | 474 | Enrichisseur bout-en-bout : discover → scrape → match → download → analyse → train ; mode `embeddings_only` purge l'audio après features | ✅ |
| `ui/discover.py` | 695 | Akinator artistes + batch scraper + import URL | ✅ |

### 1.6 Modèle IA & feedback — ✅ entraîné sur données réelles

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `engine/transition_model.py` | 591 | **L4** — Siamese 134-d→64-d, contrastive loss, `data/models/transition.pt` ; `bootstrap_pairs()` cold-start ; `maybe_auto_retrain()` (lock anti-concurrence) | ✅ loss 0.29 |
| `engine/feedback.py` | 228 | **L5** — 👍/👎 → `transition_feedback` → modifier +12/−25 + corpus L4 | ✅ |

### 1.7 Mix, lecture & setlists — ✅ livré

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `engine/player.py` | 561 | Lecture audio sounddevice, waveform | ✅ |
| `engine/tasks.py` | 190 | File de jobs d'arrière-plan (alimente l'ActivityTray) | ✅ |
| `ui/mixer.py` | 673 | Double deck + suggestions scorées + breakdown popup + boutons feedback | ✅ |
| `ui/deck.py` | 533 | Widget deck (waveform, cues, transport) | ✅ |
| `ui/setlist.py` | 557 | Constructeur de setlists (slots JSON en DB) | ✅ |
| `engine/export.py` + `ui/export_dialog.py` | 162+124 | Export setlists/lib (M3U, Rekordbox…) | ✅ |

### 1.8 Maintenance & sécurité fichiers — 🔴 chantier prioritaire

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `engine/repair.py` | ~520 | Réparation v1 (préfixe ID3 avant RIFF) + **v2** (walker de chunks RIFF : trailing `id3` tronqué, taille RIFF corrigée, tail sauvegardé dans `data/repair_tails/` → undo intégral) | ✅ v1+v2 livrés · ⏳ exécution sur les 363 WAV au clic user |
| `engine/backup.py` | 159 | Sauvegarde DB | ✅ |
| flag `tracks.corrupt` | — | Badge ⚠ en Library quand container corrompu | ✅ détection |

### 1.9 Chrome UI — ✅ stabilisé

| Fichier | LOC | Rôle | État |
|---|---|---|---|
| `ui/app.py` | 349 | Racine CTk : sidebar + frame contenu + **tray docké en bas de la racine** (pack `side="bottom"` — jamais `place()`) | ✅ |
| `ui/activity_tray.py` | 263 | Barre de statut des jobs — indestructible aux changements de page | ✅ |
| `ui/home.py` | 178 | Dashboard d'accueil | ✅ |
| `ui/toast.py` | 185 | Notifications éphémères | ✅ |
| `ui/helpers.py` | 221 | Utilitaires CTk partagés | ✅ |
| `ui/settings.py` | 1959 | ~15 sections de réglages | 🟡 fonctionne mais **doit être splitté en package** (item B3) |

---

## 2. Base de données — `data/library.db` (SQLite WAL, connexions thread-local `library._local`)

### `tracks` — la table centrale (1 ligne = 1 fichier audio)

| Colonne | Type | Écrit par | Lu par |
|---|---|---|---|
| `path` PK | TEXT | scan Library / download | tout le monde |
| `title, bpm, key, camelot, energy, duration` | — | `analyzer` | Mixer, Library, transition_score |
| `rating, genre, tags, cue_points, bpm_locked` | — | `track_editor`, Library | Library, export |
| `key_confidence, beat_grid, added_at` | — | `analyzer` | Mixer, deck |
| `corrupt` | INT | scan repair | badge ⚠ Library |
| `embedding` (BLOB 256-d) + `embedding_backend` | — | **L1** `embeddings.bulk_encode()` | transition_score, dedup audio, features L4 |
| `intro_end, outro_start, drops` | — | **L3** `segmentation.detect_structure()` | Mixer (points de mix), scoring outro_A vs intro_B |
| `source` | 'user' / 'training' / 'fma' | training_pipeline | filtre des pages user (training/fma cachés) |
| `audio_purged` | INT | mode embeddings_only | Library refuse de charger le fichier ; le modèle score quand même |

### Tables satellites

| Table | Colonnes | Rempli par | Consommé par |
|---|---|---|---|
| `track_pairs` (**L2**) | path_a, path_b, weight, sets | `cooccurrence.rebuild()` depuis le cache scrapé | `transition_score` (bonus co-occurrence), `extract_pairs` L4 |
| `transition_feedback` (**L5**) | path_a, path_b, like_score, created_at, source | `feedback.record()` (Mixer 👍/👎) | `transition_score` (+12/−25), corpus L4, seuil auto-retrain |
| `setlists` | id, name, created_at, updated_at, slots_json | page Setlist | page Setlist, export |
| `trash` | path, deleted_at, track_json, file_deleted | suppression bulk Library | Undo 30 j, purge auto |

### Fichiers de données hors DB

| Fichier | Contenu | Écrit par | Lu par |
|---|---|---|---|
| `data/tracklists/<slug>.json` | Tracklists parsées brutes (cache) | `tracklists.fetch_tracklist` | `cooccurrence.rebuild`, ré-analyse sans re-scrape |
| `data/tracklists_auth_state.json` | Cookies session 1001TL (gitignoré) | flow login | `_get_thread_browser()` de chaque thread (reload par mtime) |
| `data/models/transition.pt` | Poids Siamese L4 | `transition_model.train()` | `transition_model.score()` (lazy load) |
| `config.json` | Réglages (secrets migrés → keyring) | Settings | boot |

---

## 3. Pipeline IA — 5 couches, toutes branchées dans `library.transition_score()`

| Couche | Module | Entrée | Sortie / stockage | État chiffré (2026-07-02) |
|---|---|---|---|---|
| **L1 Embeddings** | `embeddings.py` | audio décodé | vecteur 256-d → `tracks.embedding` | ✅ 1033/1066 tracks user encodés |
| **L2 Co-occurrence** | `cooccurrence.py` | cache `data/tracklists/*.json` + matcher hybride | `track_pairs` (weight à decay positionnel) | ✅ **7 468 paires · 657 tracks reconnues** (2026-07-07, corpus 604 tracks training ingéré ; était 584, et 8 avant le matcher hybride) |
| **L3 Segmentation** | `segmentation.py` | enveloppe RMS | `intro_end / outro_start / drops` | ✅ 1066/1066 segmentés |
| **L4 Siamese** | `transition_model.py` | 134-d = 128-d embedding + 6 scalaires contextuels → 64-d, contrastive | `data/models/transition.pt` ; ±10 pts dans le score | ✅ loss **0.2761** (retrain auto du pipeline, 2026-07-05, 20 epochs) — était 0.29, et 0.60 en bootstrap-distillation |
| **L5 Feedback** | `feedback.py` | 👍/👎 Mixer | `transition_feedback` ; +12/−25 immédiat + auto-retrain à Δ10 votes | ✅ branché (2 votes capturés — signal encore mince) |

**Formule du score** (dans `library.transition_score(a, b)`) :
heuristique de base (compat BPM + roue Camelot + delta energy)
→ + similarité cosine L1 → + bonus co-occurrence L2 → scoring
outro(A)/intro(B) via L3 → ± 10 pts L4 → modificateur L5.
Le popup breakdown du Mixer expose chaque composante.

---

## 4. Relations profondes — les 5 flux end-to-end

### Flux 1 — Import & analyse d'un track
```
Download/Scan ─→ analyzer.analyze() (BPM/key/energy, librosa en worker)
             ─→ embeddings.encode()             [L1]
             ─→ segmentation.detect_structure() [L3]
             ─→ library.upsert_track()  → ligne `tracks` complète
```
Ordre important : `training_pipeline.analyze_into_db` embed **avant**
l'upsert pour que le dedup audio puisse court-circuiter les doublons.

### Flux 2 — Corpus & entraînement (le "moteur de croissance" IA)
```
discovery (recherche artiste) ─→ tracklists.fetch_tracklist (Playwright,
   cookies thread-local) ─→ cache data/tracklists/*.json
─→ tracklists.match_with_library (token-sort ≥ seuil strict
   + library.find_audio_duplicate cosine 0.92)
─→ cooccurrence.rebuild() → track_pairs                        [L2]
─→ transition_model.extract_pairs (pairs + feedback) → train() [L4]
─→ data/models/transition.pt
```
`training_pipeline.enrich_corpus()` orchestre tout ça en un bouton.

### Flux 3 — Scoring d'une transition (Mixer)
```
ui/mixer ─→ library.transition_score(A, B)
   ├─ heuristique BPM/Camelot/energy   (tracks)
   ├─ cosine(embedding_A, embedding_B) [L1]
   ├─ lookup track_pairs               [L2]
   ├─ outro_A vs intro_B               [L3]
   ├─ transition_model.score() ± 10    [L4]
   └─ feedback.score_modifier +12/−25  [L5]
─→ popup breakdown affiche la décomposition
```

### Flux 4 — Boucle de feedback (l'app apprend du user)
```
Mixer 👍/👎 ─→ feedback.record() → transition_feedback
   ├─ effet immédiat : modificateur dans transition_score
   └─ effet différé : maybe_auto_retrain() à Δ10 votes
      → les votes deviennent des exemples d'entraînement L4
```

### Flux 5 — Sécurité fichiers (l'unique chemin d'écriture audio)
```
write_tags_to_files=False (défaut) ─→ AUCUNE écriture audio, jamais
Si opt-in : analyzer.write_tags() / engine.repair UNIQUEMENT
   🔴 repair v2 pending : tronquer le chunk id3 trailing des 363 WAV
   🔴 guard rails pending : opt-in PAR format + magic-byte pre-flight
      + assertion round-trip post-save
```

### Couplages UI → engine (nombre d'imports mesuré)

`settings.py` → 31 · `library.py` → 10 · `mixer.py` / `discover.py` → 5 ·
`app.py` → 4 · `download.py` / `track_editor.py` → 3 · autres → 1-2.
Les deux hubs engine les plus consommés : `library` et `analyzer` —
attendu, pas un smell.

---

## 5. Invariants à ne jamais casser (résumé exécutable)

1. `engine/` sans import Tk ; UI → engine, jamais l'inverse.
2. DPI awareness dans `app/__init__.py` avant tout import Tk.
3. Connexions DB thread-local (`library._local`) ; pas de connexion globale.
4. Playwright thread-local (`tracklists._PW_TLS`) via `_get_thread_browser()`.
5. ActivityTray = status-bar packée sur la racine, pas de `place()`.
6. Aucune mutation `.wav/.flac/.m4a` hors `engine.repair` / `write_tags()` opt-in.
7. Pas de trailer `Co-Authored-By: Claude` ; README synchronisé à chaque push.

---

## 6. Carte d'avancement — les 13 items actifs

Reprend les 3 sprints de [AUDIT.md](AUDIT.md) ; c'est CETTE table qui bouge
au fil des sessions. **Plan d'exécution détaillé des 4 chantiers actifs
(A1, C1, C3, L5 + satellites A2-A4, C4) : [PLAN_CHANTIERS.md](PLAN_CHANTIERS.md).**

### Sprint A — « Réparer les dégâts » (sécurité fichiers)

| Item | Description | État |
|---|---|---|
| **A1** | `repair_v2()` : tronquer le chunk `id3` trailing des 363 WAV + corriger la taille RIFF | ✅ code livré 2026-07-02 (`inspect_chunks`/`repair_trailing`/`undo_trailing`, backup tails, dry-run réel = **363/451 détectés, 0 faux positif**) · ⏳ exécution réelle = clic user « Réparer » (échantillon 10 → Rekordbox → le reste) |
| **A2** | Guard rails écriture : opt-in par format + pre-flight magic-byte + assertion round-trip | ✅ livré 2026-07-02 (`should_write_tags_for`, pre-flight non-MP3, WAV verified-or-reverted byte-identique) |
| **A3** | UI bulk-repair dans Settings (« Réparer la lib ») avec progression | ✅ livré 2026-07-02 (boutons existants v1 étendus : compte v2/review, cases par format dans Interop) |
| **A4** | Tests du chemin d'écriture (doit échouer si un octet sort du chunk `data`) | ✅ livré 2026-07-02 (16 tests `test_repair_v2.py` : walker, truncate, idempotence, review-refus, undo byte-identique, guards write) |

### Sprint B — « Verrouiller la qualité »

| Item | Description | État |
|---|---|---|
| **B1** | Tests : parser tracklists + matcher + plage d'inférence L4 | ✅ livré 2026-07-05 — **41 tests** (12 au départ) : fixture schema.org, précision matcher, L4-None, repair v2, ordre playlists, setlist.fm |
| **B2** | Audit des `except Exception: pass` → narrow / log / propager | ✅ clos 2026-07-15 — passe 1 (07-05) : 11 `log_warning` data-paths ; passe finale : 2 derniers data-paths corrigés (taste profile + cache setlists discovery), le reste audité et classé légitime par catégorie (teardown audio/Playwright, cleanup temp, migrations sqlite, callbacks UI, fallbacks documentés). Politique : tout nouveau swallow sur chemin de données DOIT logger |
| **B3** | Splitter `ui/settings.py` en package `settings/` | ✅ livré 2026-07-07 — split mécanique par plages verbatim : `page.py` 289 + `_general` 373 + `_ai_sections` 612 + `_ai_workers` 598 + `_maintenance` 362 (tous < 800) ; surface publique inchangée, construction headless complète vérifiée, 51/51 |
| **B4** | CI GitHub Actions : pytest + ruff sur chaque push | ✅ livré 2026-07-05 (`.github/workflows/ci.yml`, windows-latest, ruff non-bloquant + pytest bloquant) |
| **B5** | Distribution : exe autonome partageable (installeur/signing = plus tard) | ✅ build vérifié 2026-07-08 — `build_share.py` (pré-vol Python 3.10/3.11 + 64-bit + imports cœur), spec réparé (`collect_submodules("app")` + torch/transformers exclus), `deps.py` frozen-aware. **dist/UltimateDJ 540 Mo, exe lancé et vivant 15 s (GUI OK, aucun crash figé)**. B6 2026-07-15 : ffmpeg/ffprobe/node bundlés dans `dist/bin` (smoke-run à la copie) + résolution bin-d'abord en frozen (config/deps) + WebView2 frozen-safe (`--browser-launcher` routé par run.py) + icône. Reste optionnel : code signing anti-SmartScreen |

### Sprint C — « Faire grandir le signal IA »

| Item | Description | État |
|---|---|---|
| **C1** | Scraper d'autres catalogues à fort taux de match (Pegassi, Maceo Plex…) | ✅ **objectif 1500 dépassé ×5** le 2026-07-07 : 604 tracks corpus ingérés (embeddings-only), **7 468 paires**, L4 réentraîné sur 44 776 exemples |
| **C2** | Fallback setlist.fm (`engine/setlist_fm.py`) si 1001TL verrouille | ✅ 100 % (2026-07-16 : clé user enregistrée + test API réel — setlist Solomun cachée). Historique : squelette 2026-07-05 (REST stdlib, mapping cover→artiste, format cache cooccurrence, 3 tests mockés) + bouton « 🧪 Tester la clé » livré 2026-07-15 (Settings : sauve la clé, fetch 3 setlists en thread, statut inline ; vérifié en build headless). **Reste uniquement ta clé gratuite (setlist.fm/settings/api)** |
| **C3** | Exposer le delta L4-vs-heuristique dans le breakdown du Mixer | ✅ livré 2026-07-02 (`l4_verdict` engine + bannière popup + colonne L4 + encart doute) — à valider visuellement dans l'app |
| **C4** | Auto-retrain aussi après chaque `cooccurrence.rebuild()` (pas seulement à Δ10 votes) | ✅ livré 2026-07-02 (hook gardé par toggle `ai_auto_retrain` + changement réel de paires) |

**Prochain focus suggéré** : C3 (petit, visible, fait briller le modèle) ou
A1 (dette la plus dangereuse) selon l'humeur de la session.

---

## 7. Journal des mises à jour

| Date | Session | Changement |
|---|---|---|
| 2026-07-02 | création | Snapshot initial v1.4. Tous sous-systèmes livrés sauf sprints A/B/C ci-dessus. L4 à loss 0.29 (données réelles), 582 paires L2, 1033/1066 embeddings, tray stabilisé, secrets keyring, matcher hybride livré. |
| 2026-07-02 | plan | [PLAN_CHANTIERS.md](PLAN_CHANTIERS.md) créé pour les 4 chantiers actifs. Recon clé : `repair.inspect()` v1 rend « ok » sur les 363 WAV du bug v2 (corruption APRÈS le chunk data) → la v2 exige un walker de chunks RIFF. Ordre retenu : Phase 1 C3→L5→C4 (zéro risque fichier), Phase 2 C1 en continu, Phase 3 A4→A1→A2→A3 au GO user. |
| 2026-07-02 | phase 1 | **C3 + L5-friction + C4 livrés** (+251/−22 sur 5 fichiers). C3 : `l4_verdict()` + 3 clés breakdown (library.py), bannière verdict popup + colonne L4 (mixer.py). L5 : touches F/D/X, encart « L4 doute » (active learning), delta votes/seuil dans le statut Settings. C4 : retrain post-rebuild gardé par opt-in + delta paires. Bonus : fix schéma `init_schema` (colonnes `source`/`audio_purged` manquantes → test cooccurrence cassé). pytest 14/14 ✅. Non commité. |
| 2026-07-17 | L3 v2 recalibré sur vérité terrain user — 3 bugs en cascade | L'user donne un repère : *« Anyma Sentient : le premier intro est à 15 s, le 90 s c'est le second intro »*. Ce seul point a démasqué 3 défauts : ① seuil relatif à la médiane du corps → sur un morceau qui monte, seule la section la plus dense qualifiait (l'algo rendait 92 s = le SECOND intro, structurellement vrai mais pas ce qu'un DJ veut) → **seuil relatif à l'amplitude dynamique propre du morceau** ; ② volume × aigus multipliés → le kick à 15 s était ignoré jusqu'aux hats à 30 s → **deux signaux indépendants pondérés par leur contraste réel** (le signal plat est écarté, l'informatif décide) ; ③ le *ratio* d'aigus s'affole dans le silence (souffle = 100 % d'aigus sur presque rien → intro de 1,5 s sur Fred again - Kyle) → **énergie absolue des aigus** (`rms × ratio`). Résultat : Anyma **17,5 s** (vérité 15 s, écart = lissage 5 s), Kyle 23 s, et le drop d'Anyma tombe à **94 s = le second intro annoncé**. A/B 30 tracks : intros ratées 50→**17 %**, 0-drop 77→**30 %**, outro 50→37 %. 58/58 tests. Backfill toujours en attente : manque un repère sur track compressée (hard techno) + un repère d'outro. |
| 2026-07-17 | L3 v2 — découpage sans Rekordbox (moteur livré, backfill en attente) | Le user veut que le découpage intro/outro/drops appartienne à l'app (ses amis n'ont pas ses cues Rekordbox). Diagnostic v1 confirmé sur 30 tracks réelles : 50 % d'intros ratées, 47 % d'outros, 70 % sans drop (RMS seul = le kick d'un master compressé passe le seuil dès la 1re mesure). **v2 = richesse spectrale** : par fenêtre, RMS + ratio d'énergie ≥ 4 kHz (hats/leads/voix) ; « corps » = fort ET riche ; frontières avec persistance 8 s (anti-sweep) ; drops = creux ≥ 6 s → saut net. A/B v1-vs-v2 : intro ratée 50→27 %, outro 47→37 %, 0-drop 70→10 %, intro médiane 2,8 s→20,5 s. API `detect_structure` inchangée, fonction pure (ne touche NI audio NI DB). Test unitaire spectral ajouté (piège l'intro forte-mais-pauvre-en-aigus que v1 rate). 58/58. **Backfill des 1599 tracks en attente d'un spot-check user** (quelques surestimations à calibrer : intros de 90-106 s à confirmer comme vraies ou trop strictes). |
| 2026-07-17 | ⚠ Correction du « GO » | La ligne ci-dessous reposait sur une mauvaise lecture : « l'app est maintenant complète » était une **question** de l'user, pas une déclaration. Le travail technique (pré-vol, build, vérifs, zip 940 Mo, tag v1.6.0) reste valide et rien n'a été distribué — mais G8 (signing) est **rouverte** dans le LAUNCH_PLAN et le vrai GO attend la déclaration explicite. Leçon : une déclaration de lancement se confirme avant d'exécuter. |
| 2026-07-17 | 🚀 GO — v1.6.0, première release partageable | **L'user a déclaré l'app complète → LAUNCH_PLAN exécuté §2→§7** (la règle d'or du plan a fonctionné : zéro réflexion, que du déroulé). Pré-vol vert (pytest 100 %, git propre, 22 Go), build du jour revalidé, résidu du test §4 purgé du dist (DB vide auto-créée par l'exe — aucune donnée perso, le build était propre), G8 défaut SmartScreen assumé, CHANGELOG [1.6.0] gelé, tag `v1.6.0`, `dist/UltimateDJ-v1.6.zip` produit. Reste §5 : acceptation sur machine vierge (premier ami cobaye) avant distribution large. Les 9 gates G1-G9 : toutes ✅. |
| 2026-07-16 | LV1 livré — le mode Live v1 est dans l'app | Nouvelle page **Live** (groupe MIX, sidebar) : `engine/live.py` = `LiveSession` (thread daemon qui sonde l'historique Rekordbox du jour toutes les 5 s en lecture seule, état thread-safe consommé par `snapshot()`), détection du morceau joué (~1 min de latence Rekordbox), **top 10 « à jouer ensuite »** classé par `transition_score` (bonus co-occurrence = ses propres sets) en excluant le déjà-joué, timeline du set. UI 100 % passive côté threads (tick 2 s). Vérifié : py_compile ×3, build headless de la page, tests du suggesteur (exclusion + tri). v1.1 : votes en un geste, capture auto de fin de set, delta L4/L5 au classement. Retrain avec les 67 votes ✓ (fini pendant la construction). |
| 2026-07-16 | ⚔ G7 TOMBÉE : 67 votes L5 en une session | L'user a exporté ses sets du 6 et 16 juillet et validé **tous les enchaînements en bloc** — avec une leçon de design gravée en mémoire : *« les écarts de key sont volontaires, la key n'est pas une métrique absolue »* (le bloc hitek croise le Camelot en permanence et fonctionne — l'harmonique est un indice, jamais un veto ; ⚠key retirés du vocabulaire). 67/67 transitions résolues par chemin exact → `feedback.record(+1, source="history")` (134 rows symétriques), doublons dédupliqués, 0 échec. **Quête « 50 votes » : 2/50 → 67/67 ✅.** Retrain relancé avec les votes (×3). Il ne reste plus que G8 (décision signing) avant le GO. |
| 2026-07-16 | MODE LIVE lancé — Live-0 : tes 109 historiques Rekordbox deviennent du signal d'entraînement | Nouvelle quête majeure (demande user) : mode Live = détection du morceau joué sur Rekordbox + suggestions temps réel + capture des transitions. Setup user : Rekordbox 7.2.11 seul sur PC → source = master.db (SQLCipher, lu par **pyrekordbox** — clé publique, LECTURE SEULE). Probe validé : 1 692 tracks rekordbox, 109 sessions d'historique lisibles. **Live-0 livré : `engine/rekordbox_bridge.py`** — chaque session devient un JSON au format cache cooccurrence (`rekordbox-history-<id>.json`, dj="Mes sets (Rekordbox)"), matching par chemin de fichier exact (495/684 lignes = 72 %) avec repli nom nettoyé. Import réel : 33 vrais sets (76 sessions < 4 tracks ignorées). Phases suivantes : Live-1 = poller du même historique pendant le set + page Live + suggesteur set-aware ; Live-2 = écoute WASAPI (positions/parties utilisées → vérité terrain L3). Dépendance pyrekordbox ajoutée partout (requirements/deps/spec/LAUNCH_PLAN §9). |
| 2026-07-15 | ⚔ MIGRATION CLAP VALIDÉE : AUC per-track 0.546 → **0.712** | **Le L4 généralise enfin.** Nuit mouvementée : le job avait fini download 607 + analyze 604 CLAP mais rendu 0 paires — cause réelle : **D: plein à 100 %** (`database or disk is full` ; 30 Go = `D:\Music - Copie`, un doublon intégral à trancher par l'user). 84 Mo libérés (build/ + pycache) → rebuild : **7 468 paires à l'identique** (57 sets, 657 matchées / 28 orphelines) → retrain L4 sur features CLAP. Calibration L1 vérifiée sur CLAP : p5=0.907/p95=0.999, axe 0→100 vivant. **Éval per-track (harnais réécrit, 65 tracks held-out, 1 474 pos / 7 370 neg, prod restaurée octet-à-octet) : AUC 0.712 vs 0.546 lite** — un nouveau morceau obtient des suggestions L4 sensées sans figurer dans aucune setlist. Écosystème complet : 1 104 user + 604 corpus en CLAP. Bonus : `maybe_auto_enrich()` livré (apprentissage continu opt-in `ai_auto_enrich`, seuil 25, baseline anti-surprise au premier enable, hook fin de Sync, case Réglages, 4 gardes testées). |
| 2026-07-15 | B2 ✅ clos | Passe finale de l'audit silent-swallows : inventaire complet (365 matches regex → ~120 vrais `pass`/`continue` muets), 2 derniers data-paths corrigés dans `discovery.py` (profil de goûts + cache setlists : un JSON corrompu se resettait EN SILENCE → loggé), toutes les autres catégories auditées et déclarées légitimes (teardown player/tracklists, cleanup OSError, migrations sqlite `ALTER`-if-exists, protections de callbacks de progression, fallbacks commentés feedback/analyzer). Sprint B : 6/6 ✅. |
| 2026-07-15 | LAUNCH_PLAN créé | **[docs/LAUNCH_PLAN.md](LAUNCH_PLAN.md)** : runbook vivant du lancement de la version finale, à part (demande user). GO/NO-GO (G1-G9, snapshot du jour : G1-G3 ✅, G4-G8 🟡), pré-vol, build, 7 vérifs post-build, test d'acceptation « machine ami », distribution, post-launch, hotfix. Règle jumelle README/carte ancrée dans CLAUDE.md + mémoire : MAJ dans le même commit que tout changement build/packaging/deps ; exécution SEULEMENT quand l'app est déclarée complète. |
| 2026-07-15 | C2 bouton UI | Bouton « 🧪 Tester la clé (3 setlists) » dans Settings > setlist.fm : sauve la clé dans config.json, `fetch_and_cache(artiste, limit=3)` en thread daemon, résultat inline (✓ n setlists en cache / clé vide / erreur API). Construction headless vérifiée. C2 ne dépend plus que de la clé gratuite de l'user. |
| 2026-07-15 | S6 SSL horloge + B6 zéro-dépendance | **Panne user élucidée : Spotify KO des deux côtés (API `SSLCertVerificationError` + WebView2 HSTS) parce que l'horloge Windows retardait de 7 jours** — le cert Spotify renouvelé le 09-07 était « pas encore valide » pour le PC (Google, cert du 22-06, passait → illusion d'une panne Spotify-only). Diag posé (DigiCert authentique, DNS/hosts sains, skew mesuré via header `Date` HTTP), horloge resynchronisée par l'user → TLS re-testé OK. L'app sait désormais l'expliquer seule : `spotify._clock_skew_hint` mesure le décalage et l'ajoute au message d'erreur. NB : les entrées « 07-07/07-08 » ci-dessous ont été écrites sous l'horloge retardée (vraies dates : 14/15-07). Bonus : nom m3u8 émoji-safe (Tk affichait `¿Þ.m3u8`) + test. **B6 packaging** : ffmpeg/ffprobe/node copiés dans `dist/bin` avec smoke-run, `config.get_ffmpeg/get_node` + `deps._find_exe` regardent bin/ d'abord en frozen, WebView2 frozen-safe (l'exe se relance en `--browser-launcher`, routé en tête de `run.py` — le `python -m` était impossible dans l'exe), icône vinyle `assets/icon.ico` branchée au spec. Arbre : B5 ✅ + B6/S5/S6 ajoutées. |
| 2026-07-07 | 2 bugs download + B5 build partageable | **Bugs user corrigés** : ① playlist Spotify re-téléchargée en double → le **disque devient l'autorité** (`split_present_absent` filtre `added` avant la fenêtre de sélection) + matching tolérant aux renames (strip du n° de piste, multi-artistes) — l'utilisateur avait vu juste sur les deux causes. ② bouton **Stop figé** → hook yt-dlp qui annule en plein téléchargement (`_DownloadCancelled` + socket_timeout) + Stop libère les modales bloquées. 55/55 tests. **B5 lancé** : le partage de dossier échouait car dépendant du Python de l'ami → réponse = exe PyInstaller autonome. Spec réparé (bug majeur : les pages chargées par `importlib` string étaient absentes du bundle → crash à l'ouverture de Settings ; `collect_submodules("app")` corrige) + torch/transformers exclus (−1,5 Go), `deps.py` rendu frozen-aware, `build_share.py` vérifie Python 3.10/3.11 + 64-bit + imports cœur avant de builder. **Build vérifié : dist/UltimateDJ 540 Mo (3201 fichiers, exe 23 Mo), l'exe se lance et reste vivant 15 s — GUI démarrée, aucun crash d'import figé.** Prêt à zipper/partager. |
| 2026-07-07 | B3 ✅ — settings.py splitté | Pendant la migration CLAP : **le monolithe de 2 129 lignes devient un package 6 fichiers (tous < 800)** via un splitter par plages de lignes (corps byte-identiques — rien retapé). `SettingsPage(GeneralMixin, AISectionsMixin, AIWorkersMixin, MaintenanceMixin, CTkFrame)` ; `_build_ui` découpé en 4 builders. Vérifié : py_compile ×6, import, **construction headless complète de la page**, pytest 51/51. Sprint B : B1 ✅ B2 🟡 B3 ✅ B4 ✅ (reste B5 installeur). |
| 2026-07-07 | GO CLAP — migration lancée | User a dit GO. `transformers` installé, smoke-test réel OK (`laion/clap-htsat-unfused`, 512-d→256-d, norme 1.0, `best_backend()`='clap'). **Migration nocturne en cours** (job unique) : ① ré-encodage CLAP des 1 104 tracks user (la calibration L1 s'invalide à chaque écriture) ② purge des rows corpus lite (audio purgé = non ré-encodables) + `track_pairs` ③ `enrich_corpus` complet — re-download 608, analyse avec embeddings CLAP, rebuild, retrain. À la notification : recalibration vérifiée + **ré-éval per-track** pour mesurer le gain de généralisation (baseline lite : AUC 0.546). Journal in-app livré au passage (Settings → Journal, 51/51 tests). |
| 2026-07-07 | ⚔ 363/363 WAV réparés | **User a validé le pilote dans Rekordbox (« bombacide is working perfectly ») → GO → les 353 restants réparés + vérifiés machine en une passe : 353/353, 0 échec, 0 badge corrupt restant en DB.** Les 363 WAV corrompus par le legacy write_tags sont tous rendus à Rekordbox. Undo intégral toujours possible (tails dans data/repair_tails + repair_history.json). Sprint A : 100 %. |
| 2026-07-07 | batch 4 : corpus PERSISTANT + double vérité AUC | **La chaîne C1 est bouclée et durable** : batch 4 = 607 téléchargés, **604 analysés/stockés par le pipeline lui-même**, 7 468 paires (chemins vivants), retrain sur 44 776 ex (loss 0.41 — corpus ×12 = tâche plus dure, normal). **Preuve de persistance : sync_library réelle → orphans_removed = 0, les 604 rows training intactes.** Éval finale double lecture : **AUC 0.899 sur paires nouvelles de tracks connus** (= le régime réel du Mixer) mais **0.546 sur tracks jamais vus** (322 pos/1 610 neg) → le L4 mémorise les identités, il ne généralise pas au-delà avec les embeddings lite (cosine ~0.97 partout). Conclusions : l'auto-retrain post-ajout est vital (existe ✓) ; le levier de généralisation = backend CLAP, pas plus de données. |
| 2026-07-07 | 4ᵉ verrou : la sync mangeait le corpus | **Correctif au triomphe du matin : les 604 rows training ont été EFFACÉES après le job** — `sync_library` traitait toutes les rows sans fichier comme orphelines, or l'audio corpus est purgé PAR DESIGN (embeddings_only) → chaque Sync exterminait le corpus. Fix : le balayage d'orphelins exclut `source != 'user'` et `audio_purged = 1` + test de régression. **Éval per-track exécutée avant l'effacement : AUC 0.856** (8 pos/40 neg, zéro track partagé — petite mais honnête). Les 7 468 paires référencent des chemins morts → le rebuild du batch 4 les régénérera. **Batch 4 relancé pour la nuit** (download+analyze+rebuild+retrain, rows désormais persistantes). pytest 50/50 ✅. |
| 2026-07-07 | corpus ×12.8 | **Job analyze-only terminé : 604/604 stockés** (name-gate : 3 vrais dups seulement), audio purgé après embed (embeddings-only). **Paires L2 : 584 → 7 468** (objectif C1 1500 dépassé ×5), 657 tracks reconnues. **L4 réentraîné sur 44 776 exemples** (était 3 492). C1 → ✅. Éval per-track à suivre sur ce corpus élargi. |
| 2026-07-07 | env numba réparé + backfill spectral | **Batch 3 : 607 téléchargés, 0 analysé — cause : numba < 0.66 rejette NumPy 2.2 et `librosa/core/audio.py` l'importe au niveau module → `librosa.load` mort sur tout l'env** (l'analyse user datait d'avant le bump numpy ; le backend lite a son propre loader soundfile, d'où les tests verts). Fix : numba 0.66 installé + pin `numba>=0.66` dans requirements + `res_type="polyphase"` sur les 4 `librosa.load(sr=fixe)` (évite le fallback resampy). Vérifié : analyze_track OK sur MP3 corpus réel. **Job analyze-only relancé sur les 607 fichiers en place** (pas de re-download). **Backfill spectral terminé : 530/1104 transcodes suspects, 506 tracks ≤128 kbps réels, 2 seuls en pleine bande** — la colonne kbps dit maintenant la vérité. pytest 49/49 ✅. |
| 2026-07-06 | qualité spectrale | **Détection des transcodes déguisés** : `analyzer.estimate_spectral_ceiling` (STFT numpy hand-rolled — piège débusqué : `librosa.load(offset=…)` importe numba, cassé avec NumPy 2.2 ; seek via soundfile) + `estimate_true_kbps` (plafond→famille : 16 kHz≈128k … 20,5 kHz≈320k, 999=pleine bande) + colonne `est_kbps` (2 schémas) + cellule Library `⚠~128` quand le container ment. **Sanity réelle : les 3 premiers WAV « ♾1411k » testés plafonnent à ~15-16 kHz → ~128 kbps réels** (rips déguisés). Backfill 1103 fichiers lancé en fond. pytest 49/49 ✅. |
| 2026-07-06 | badge bitrate | **Colonne kbps livrée** : `tracks.bitrate` (2 schémas + upsert COALESCE + test), `analyzer.get_bitrate` (mutagen read-only) alimente les analyses futures, colonne « kbps » dans la Library (♾ pour lossless ≥900, ligne orange si ≤192). Backfill réel : 1103/1104 sondés — distribution : **503 tracks user à 161-192 kbps (46 %)**, 148 à 193-330, 450 lossless. Limite honnête documentée : un transcode garde le bitrate du container — c'est un plancher de qualité, pas une preuve de source ; la détection spectrale du vrai plafond = quête future. |
| 2026-07-06 | éval L4 | **Harnais d'évaluation L4 exécuté** (quête README « Next up ») : split 80/20 des paires (587 pos / 2912 neg), entraînement sur copie scratchpad (prod intouchée), ranking sur held-out : **AUC modèle 0.899 vs baseline features-brutes 0.601** (sim encodée pos −0.23 vs neg −0.85). Le Siamese a réellement appris — le ±10 pts du Mixer est justifié. Caveat honnête : les tracks (pas les paires) peuvent apparaître des deux côtés du split → AUC légèrement optimiste ; un split par-track est l'étape suivante du harnais. |
| 2026-07-06 | L1 calibré | **Axe audio ressuscité** : `similarity_score` calibré sur la distribution de la lib (p5→0, p95→100, cache invalidé à chaque `set_embedding`, fallback legacy < 50 embeddings). Mesuré sur 500 paires réelles : std **1.9 → 26.4**, p5-p95 94→100 devient **0→100**. Breakdown affiche « cosine 0.94 → 62 calibré ». pytest 46/46 ✅. Reste en quêtes : badge bitrate, éval L4 (AUC), split settings.py. |
| 2026-07-06 | bug dédup destructeur + L1 saturé | **Batch 2 terminé : 1106 téléchargés MAIS 0 stocké** — `analyze_into_db` a supprimé (`os.remove`) les 1106 fichiers comme « audio-dups » : le backend lite produit des cosines ~0.97 entre tracks ALÉATOIRES (Janet Jackson ≈ Skrillex à 0.9999). Fix : le verdict dup exige désormais une confirmation par nom (`_confirms_duplicate` ≥ 0.75) avant skip/remove + test de régression sur les paires réelles du log. pytest 44/44 ✅. Batch 3 relancé. **Découverte majeure mesurée : l'axe L1 du score est saturé** (cosine moyen 0.971, p5 0.886 → l'axe audio vaut 95-100 pour toutes les paires = quasi-constant, pas un discriminateur) → quête prioritaire : rescaling percentile de L1 (sans nouveau backend). |
| 2026-07-05 | v1.5 pushed + pilote réparation + FMA purge | **Push GitHub** : 3 commits (`5da0a3f` v1.5, `369b3aa` CI, `bf62784` docs), auteur solo. **Évaluation DJ** livrée (chat) — verdicts clés : app 3,3 Mo vs 17 Go de caches ; FMA 12 Go / 0 ligne DB ; 87/1104 beat grids ; 0 setlist utilisée. **Pilote réparation : 10/10 WAV réparés + vérifiés machine** (auto-undo armé, 0 échec), 353 flagués `corrupt=1` (le scan persiste désormais les flags → badges Library réels) — attente vérif Rekordbox user avant les 353. **FMA purgé : −12,07 Go** via les fonctions de l'app. Contrainte user respectée : PAS de ré-analyse beat-grid librosa (pas meilleur que Rekordbox) — vérifié : l'export XML n'écrit aucune grille. Badge bitrate → quête dispo. |
| 2026-07-05 | download UX | **2 demandes user livrées** : ① modal de **sélection des tracks** avant tout téléchargement de playlist (> 1 track) — cases pré-cochées, filtre, compteur live, Tout/Rien, bouton « Télécharger N » ; ② **bootstrap du cache de sync depuis le dossier** (`playlist_sync.bootstrap_cache_from_folder`) : un dossier téléchargé avant l'existence du sync est reconnu par match flou des fichiers → seuls les nouveaux morceaux téléchargent (texte du modal resync adapté). Bonus : fix des doubles espaces dans `_norm` partagé (améliore aussi le matching de `merge_after_download`). +3 tests. pytest **43/43** ✅. |
| 2026-07-05 | complétion max | **5 quêtes avancées en une passe** : B1 ✅ (41 tests — fixture parser schema.org, précision matcher, L4-None), B4 ✅ (CI windows-latest), B2 🟡 70 % (11 log_warning data-path, contrôle de flux intact), C2 🟡 80 % (squelette `setlist_fm.py` REST + 3 tests mockés — reste clé API), CR ✅ (IP sondée LIBRE → batch 2 relancé, **les téléchargements passent : ~180/608 [ok]**, rebuild+retrain suivront). pytest **41/41** ✅. Arbre de quêtes ~68 %. Non commité. |
| 2026-07-05 | arbre de quêtes | PROJECT_MAP.html : ajout du système de quêtes (bouton ⚔) — 20 quêtes sur 5 lanes (Fondations/A/B/C/Découvertes), arbre SVG de dépendances, XP 261/555 (47 %), statuts ✅🟡🔓🔒 avec bloqueurs, panneau détail par quête, « prochaine recommandée » = ⚔ Réparer les 363 WAV. Chiffres du HTML resynchronisés (584 paires, loss 0.276). |
| 2026-07-05 | batch C1 + fix downloader | **Batch C1 terminé** : 10 artistes, 57 sets (47 cache), retrain L4 → loss **0.2761** ; MAIS 608/608 téléchargements échoués + 1001TL a rate-limité l'IP en découverte. Root cause downloads (prouvée par A/B probe) : `_yt_base_opts` épinglait `player_client:["web"]` ET attachait `cookiesfrombrowser` — les deux forcent le client web de YouTube, désormais muré par PO-token → zéro format. Les deux retirés (workarounds 2025 devenus poisons), vérifié par un vrai téléchargement end-to-end (ok=1). Paires 582→584 seulement (aucun nouveau track) — **relancer le batch quand l'IP 1001TL est débloquée** : les 608 manquants téléchargeront cette fois. pytest 33/33 ✅. |
| 2026-07-02 | bug ordre playlists | **Ordre Spotify préservé au téléchargement.** Cause 1 : `playlist_sync.compute_diff` construisait `added` via une différence d'ensembles (ordre arbitraire) → réécrit en marche ordonnée sur la source (added/kept/missing en ordre playlist). Cause 2 : aucune matérialisation de l'ordre sur disque (yt-dlp nomme `Artist - Title` → tri alphabétique) → `write_m3u()` écrit `<playlist>.m3u8` (UTF-8, chemins relatifs) dans le dossier à chaque sync, importable Rekordbox/Engine/VLC, zéro renommage audio. +2 tests régression. pytest 33/33 ✅. |
| 2026-07-02 | phases 2+3 | **Chaîne A livrée en TDD** : A4 (16 tests fixtures synthétiques) → A1 (walker RIFF `inspect_chunks`, `repair_trailing` avec tail backup + `undo_trailing` byte-identique, `scan_folder` v1+v2) → A2 (opt-in par format `should_write_tags_for`, pre-flight magic, WAV verified-or-reverted — le vecteur actif était le wrapper WAVE de mutagen) → A3 (Settings : compte v2/review, cases par format). **Dry-run réel : 363/451 trailing détectés, 0 faux positif** — réparation effective au clic user. Phase 2 : batch C1 lancé, a débusqué un bug de contrat `resolve_missing` ↔ matcher hybride (AttributeError) → corrigé + test régression + garde anti-placeholder ID, batch relancé. pytest **31/31** ✅. Smoke Mixer headless OK. Non commité. |
