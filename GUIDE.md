# Ultimate DJ — Guide utilisateur

App Python/CustomTkinter pour préparer ses sets DJ : analyse BPM/key/energy,
téléchargement YouTube/SoundCloud/Spotify, library manager, harmonic mixer,
setlist auto-build, export Rekordbox/Serato/m3u8.

---

## Sommaire

- [Premier lancement](#premier-lancement)
- [Page **Home**](#page-home) — tableau de bord
- [Page **Download**](#page-download) — télécharger des tracks
- [Page **Library**](#page-library) — gérer la bibliothèque
- [Page **Analyze**](#page-analyze) — analyser BPM/key/énergie
- [Page **Mixer**](#page-mixer) — transitions + crossfade dual-deck
- [Page **Setlist**](#page-setlist) — auto-build + éditeur
- [Page **Discover**](#page-discover) — playlists Spotify recommandées
- [Page **Settings**](#page-settings) — config + outil de réparation
- [Modal **Track Editor**](#modal-track-editor) — métadonnées DJ
- [Workflows complets](#workflows-complets)
- [Raccourcis](#raccourcis)
- [Dépannage](#dépannage)

---

## Premier lancement

À la première ouverture, l'app vérifie ses dépendances Python (customtkinter,
yt-dlp, librosa, pygame, pywebview, mutagen…) et les installe via `pip` si
besoin. Idem pour FFmpeg et Node.js via `winget`. Splash screen pendant ~30s
le premier coup, instantané ensuite.

**Avant tout** : va dans **Settings** et configure :
1. **Music library folder** — où sont tes morceaux
2. **Download folder** — où sortir les téléchargements
3. **Spotify Client ID / Secret** — gratuit sur `developer.spotify.com`
   (nécessaire pour Discover et le download de playlists Spotify)

---

## Page **Home**

**Tableau de bord** affichant :
- 4 cartes stats : nb tracks total · non notés · doublons · dossier musique
- Boutons **Actions rapides** (raccourcis vers Library/Analyze/Discover/Setlist)
- Liste des **derniers imports** (10 plus récents avec rating + BPM + Camelot)

Les chiffres se rafraîchissent à chaque retour sur Home.

---

## Page **Download**

**Téléchargement YouTube / SoundCloud / Spotify / 1001Tracklists**

### URL field
- Colle un lien
- **MP3 320 kbps** (verrouillé — standard DJ)
- Choix de format : MP3, WAV, MP3 fallback WAV, WAV fallback MP3

### Browser intégré (sous le champ URL)
- 4 boutons rapides : **SoundCloud · Spotify · YouTube · 1001Tracklists**
- Embed Edge WebView2 — sessions persistantes (logins gardés)
- Tape une URL ou navigue, puis **« ↑ Coller dans URL »** envoie le lien
  dans le champ URL principal
- **Fermer** vide la fenêtre
- **Effacer session** (Settings) supprime les cookies (te déconnecte de tous
  les services)

### Dossier de destination
- Path label cliquable — affiche le dossier courant
- **Parcourir** : ouvre le sélecteur Windows
- **Nouveau** : crée un sous-dossier inline

### Pendant un download
- Barre de progression
- Status temps réel (throttlé à 80ms pour pas saturer l'UI)
- **Pause / Stop** disponibles pendant que ça tourne
- Pour Spotify : affiche la track-list avec icônes de status par track
  (⬇ en cours · ✓ ok · ✗ échec)
- **« ← Browser »** revient au navigateur intégré (la session reste vivante)

---

## Page **Library**

**Browser de tous les morceaux analysés.**

### Header
- Compteur de tracks
- **Doublons** (switch) — filtre les tracks ayant un doublon (même titre
  normalisé + BPM + Camelot)
- **Non notés** (switch) — filtre les tracks sans rating
- **Sync Library** — scan le dossier musique, retire les orphelins,
  analyse les nouveaux fichiers
- **Export…** — exporte la sélection (ou toute la lib visible si rien
  n'est sélectionné) au format M3U8 / Rekordbox XML / Serato `.crate`
- **Refresh** — recharge depuis la DB

### Search bar
- **Search title** — recherche par titre (substring)
- **Key** — filtre Camelot ou nom (ex: `8A`, `C minor`)
- **BPM** min — max
- **Genre** — substring sur le champ genre
- **Min ★** — rating minimum (any / 1+ / 2+ / 3+ / 4+ / 5)

### Tableau (8 colonnes)
- Title · BPM · Key · Camelot · Energy · Rating (★) · Genre · Duration
- 🔒 sur le BPM = override manuel verrouillé
- **Clic en-tête** = trier par cette colonne
- **Double-clic** = ouvre le **Track Editor** (rating, BPM override, etc.)
- **Multi-sélection** : Ctrl+clic ou Shift+clic
- **Clic-droit** = menu contextuel (voir ci-dessous)

### Menu clic-droit (bulk actions)
Avec 1 ou plusieurs lignes sélectionnées :
- **Modifier** (1 row) — ouvre le Track Editor
- **Rating ★ / ★★ / … / clear** — applique le rating à tout le batch
- **Définir le genre…** — prompt qui pré-remplit si le genre est
  identique sur toutes les tracks
- **Exporter la sélection…** — ouvre le dialog d'export
- **Retirer de la bibliothèque (DB seule)** — supprime de la DB, garde
  les fichiers (Sync Library les ré-ajoutera)
- **Supprimer fichier(s) ET DB** — IRRÉVERSIBLE, double confirmation

---

## Page **Analyze**

**Analyse BPM / key / énergie** via librosa.

### Boutons
- **Analyze File** — un fichier
- **Scan Folder** — scan récursif d'un dossier (mp3/wav/flac/ogg/m4a)
- **Pause / Resume** — disponible pendant le scan
- **Stop** — arrête après le fichier en cours

### Pendant le scan
- Status throttlé (80ms) — n'inonde pas le mainloop
- Barre de progression
- Tableau des résultats (FastList Treeview) — green tag = OK,
  red tag = échec

### Que se passe-t-il pour chaque fichier
1. librosa charge l'audio (~22 kHz mono, 90s premiers)
2. Détection BPM (beat tracker)
3. Détection key (chroma + Krumhansl-Kessler) + score de confiance
4. Détection énergie (RMS normalisée 0-10)
5. Camelot calculé via la map standard
6. **Tags ID3/RIFF/FLAC/MP4 écrits** dans le fichier (via le bon wrapper
   mutagen selon l'extension — **plus jamais de corruption**)
7. UPSERT en DB (préserve `bpm_locked`, `rating`, `genre`, `tags`,
   `cue_points`)

### ⚠️ BPM verrouillé
Si tu as fait un override manuel via Track Editor avec « Verrouiller le BPM »,
l'analyse ne peut PAS écraser cette valeur. C'est exprès — librosa se trompe
souvent sur la jungle / DnB (87 au lieu de 174).

---

## Page **Mixer**

**Trouve les transitions harmoniques + preview crossfade en temps réel.**

### Layout 2 colonnes
- **Gauche** — ta library avec filtre rapide. Sélection → charge sur Deck A
  + calcule les transitions
- **Droite** — top 20 transitions triées par score (50% key + 40% BPM +
  10% energy). Sélection → charge sur Deck B

### Dual-deck en bas
- **Deck A · current** + **Deck B · next** (DeckWidget chacun, voir
  ci-dessous)
- **Crossfader A↔B** — slider equal-power (cosinus/sinus) pour garder
  le loudness perçu constant pendant la transition
- Quitter la page coupe les 2 decks (pas d'audio orphelin)

### DeckWidget (chaque deck)
- **Waveform** — peaks downsamplés via librosa, cache `.npy` pour
  réouverture instantanée
- Click sur la waveform = seek
- **Time** — courant / durée totale
- **▶/⏸ ⏹** — play/pause/stop
- **Vol** — slider de volume
- **+ cue** — ajoute un cue point au position actuelle (label auto :
  INTRO/BUILD/DROP/BREAK/OUTRO selon ratio)
- **Cue chips** — clic = jump, clic ✗ = supprime
- Cues auto-sauvés en DB

---

## Page **Setlist**

**Auto-build + éditeur manuel.**

### Génération
- **Start track** — dropdown des tracks de la lib (capé à 1000)
- **Length** — 2 à 50
- **Generate Setlist** — algorithme greedy : à chaque slot, choisit la
  track non-utilisée qui maximise le score de transition

### Édition
- **↑ / ↓** — déplace la track sélectionnée
- **Lock / Unlock** — fige une track sur sa position
- **Double-clic ligne** — toggle lock
- **🔒** colonne — affiche le statut de lock
- **Score** colonne — score de transition vers cette track depuis la
  précédente (recalculé après chaque mouvement)

### Regenerate (keep locked)
Régénère uniquement les slots **non-verrouillés**, en gardant les locks
en place. Utile pour fixer des « peak moments » et laisser l'algo
compléter le reste.

### Save / Load
- **Save…** — nomme et sauvegarde le setlist (DB)
- **Load…** — popup avec tous les setlists sauvegardés :
  - **Charger** — recharge dans la page (refresh les méta des tracks
    depuis la DB en passant)
  - **✗** — supprime ce setlist
- Les tracks supprimées de la lib sont silencieusement écartées au load

### Export
- M3U8 (universel) · Rekordbox XML · Serato `.crate`

---

## Page **Discover**

**Playlist Akinator-style basée sur Spotify recos.**

1. Décris ton style en quelques mots
2. Tape pour chercher des seed songs (auto-complete Spotify, max 5)
3. Choisis le nombre de tracks (5-50)
4. **Generate Playlist** — appelle Spotify recommendations API
5. **Garder mes likes** (checkbox) — au prochain Generate, les tracks
   que tu as aimées sont gardées et seules les autres sont remplacées

### Pendant la review
- ✓ vert = j'aime (apprend ton goût pour les futurs Generates)
- ✗ rouge = je n'aime pas (apprend les anti-patterns)
- Le **profil de goût** apparaît en haut : top artistes / BPM range / keys

### Après une playlist générée
Tu peux la copier vers Download via ↓ Add to Download (envoie une
recherche YouTube par track).

---

## Page **Settings**

### Paths
- **Music library folder** — racine de ta bibliothèque
- **Download folder** — où mettre les nouveaux téléchargements
- **FFmpeg path** — auto-détecté, override possible

### Spotify API
- **Client ID / Client Secret** — credentials gratuits depuis
  developer.spotify.com

### Audio
- **MP3 quality** — verrouillé à **320 kbps** (standard DJ — pas de 128/192/256)

### Theme
- **Cyan Night** (par défaut, dark)
- **Mono** (dark gris pur, pour les écrans à fort contraste)
- Les autres themes ont été retirés (pas adaptés au booth DJ)

### 🔧 Réparation des fichiers audio (CRITIQUE)
Une ancienne version de `write_tags()` corrompait les WAV/FLAC/M4A en
préfixant les octets ID3 avant le magic du conteneur. Rekordbox 7,
Engine DJ et autres parsers stricts refusent ces fichiers.

- **Diagnostiquer** — scan dry-run, compte les fichiers cassés sans rien modifier
- **Réparer** — strip les octets parasites, écrit dans `data/repair_history.json`
- **Voir l'historique** — popup avec date/octets stripés/chemin de toutes
  les réparations passées
- **Nettoyer .bak** — supprime tous les `*.bak` legacy (économie disque)

⚠️ Pas de backup `.bak` créé pendant la réparation (trop d'espace disque).
La sécurité vient du fait que la fonction `inspect()` REFUSE de toucher
un fichier dont le magic RIFF/fLaC/ftyp n'est pas trouvable (il n'est
donc pas vraiment un WAV/FLAC/M4A).

### System
Affiche les chemins détectés de FFmpeg et Node.js (verts si OK, rouges
sinon).

### About
Numéro de version + résumé des features.

---

## Modal **Track Editor**

Ouvert via double-clic ou clic-droit → Modifier dans Library.

### Layout
- **Header** : titre + nom de fichier
- **DeckWidget** intégré : waveform + transport + cues (édite directement
  les cue points)

### Colonne gauche
- **Rating** — 5 étoiles cliquables, **clear** pour 0
- **Genre** — Entry libre (substring filter dans Library)
- **Tags** — Entry comma-separated

### Colonne droite
- **BPM**
  - Entry directe (édite la valeur)
  - **÷2** / **×2** — boutons rapides pour fix half/double-time
  - **Verrouiller le BPM** (checkbox) — empêche les futures analyses
    d'écraser la valeur
- **Tap-tempo**
  - Bouton **TAP** — appuie au rythme (4+ taps)
  - Calcule la moyenne des intervalles, clamp dans [60, 200] BPM,
    auto-verrouille le BPM
  - **reset** efface les taps
- **Key** — affiche la key détectée + Camelot + **% de confiance**
  (vert ≥75% · jaune 50-74% · rouge <50%)

### Save
- Écrit en DB
- Re-écrit les tags ID3/WAVE/FLAC/MP4 dans le fichier (via le bon
  wrapper mutagen — pas de corruption)
- Ferme la fenêtre + déclenche un refresh de Library

---

## Workflows complets

### A. Premier rip d'un dossier
```
Settings  → set Music folder + Download folder
Library   → Sync Library  (scan + analyse les nouveaux)
Analyze   → optionnel pour ré-analyser des problèmes
Library   → trier par rating ou genre, taguer
```

### B. Préparer un set techno 130 BPM
```
Library   → Search + Min★=3+ + BPM 128-134 + Genre=techno
            → multi-sélect des candidats → Export… → Rekordbox XML
Setlist   → Generate avec une track de départ + length=15
            → ↑↓ pour réordonner → Lock le peak → Regenerate
            → Save…  ("Friday techno 130")
            → Export → m3u8 pour préserver
```

### C. Télécharger une playlist Spotify
```
Settings  → vérifier Spotify creds
Download  → coller l'URL Spotify  ou  Browser intégré → Spotify →
            naviguer → ↑ Coller dans URL
          → Format MP3, Télécharger
          → la track-list apparaît + status par track
          → ← Browser pour revenir naviguer pendant que ça download
Library   → Refresh pour voir les nouveaux dans la lib
```

### D. Réparer des WAV cassés par un ancien scan
```
Settings  → Réparation des fichiers audio
          → Diagnostiquer  (compte les fichiers à réparer)
          → Réparer        (strip les octets ID3 parasites)
          → ouvrir Rekordbox 7 pour vérifier qu'un fichier réparé
            s'ouvre maintenant
Settings  → Voir l'historique  pour audit trail
Settings  → Nettoyer .bak     pour libérer l'espace des anciens backups
```

### E. Mixer & preview une transition
```
Mixer     → clic sur une track de la lib (gauche) → load Deck A
          → clic sur une transition (droite) → load Deck B
          → ▶ Deck A pour entendre la track courante
          → bouger le crossfader vers B pendant que A joue
          → ▶ Deck B au moment du switch
```

### F. BPM mal détecté
```
Library   → double-clic la track  →  Track Editor s'ouvre
          → écouter dans le Deck du popup pour vérifier
          → si librosa donne 87 et c'est de la jungle :
            cliquer ×2 (passe à 174)
          → cocher « Verrouiller le BPM »
          → Save  (re-écrit le tag ID3 + bloque les futures analyses)
```

---

## Raccourcis

- **Enter** dans la barre de recherche Library = lance la recherche
- **Double-clic** sur une track = ouvre Track Editor
- **Clic-droit** sur une (ou plusieurs) track(s) = menu bulk
- **Clic** sur un en-tête de colonne = sort
- **Ctrl+clic / Shift+clic** = multi-sélection
- **Clic** sur la waveform du Deck = seek

---

## Dépannage

### « Spotify error » dans Discover ou Download
→ Settings · vérifie Client ID / Client Secret. Pour les playlists
éditoriales (`37i9dQZF1…`), fais d'abord « Save to your library »
dans Spotify et utilise le nouveau lien.

### « FFmpeg not found »
→ Settings · clic sur le path FFmpeg, browse vers `ffmpeg.exe` ; ou
relance l'app, le splash le ré-installe via winget.

### Rekordbox refuse mes WAV
→ Settings · Réparation · Diagnostiquer · Réparer (voir workflow D).

### L'app freeze pendant l'analyse
→ Vérifie que l'analyse tourne bien en thread (pas dans le mainloop).
Si ça gèle 5+ secondes pendant un scan, c'est probablement librosa qui
charge un fichier énorme. Pause + Stop sont disponibles.

### Plus de son après quitter Mixer
→ Normal. `on_hide` coupe les 2 decks pour pas laisser d'audio orphelin.

### Le browser intégré ne charge rien
→ Vérifie que pywebview est installé (Settings · System ne le montre
pas, mais le splash de démarrage l'installe). Si vraiment KO, lance
manuellement `pip install pywebview` dans le venv.

### Les cookies du browser disparaissent
→ Settings → Effacer session (ou tu as fait Réparer/Effacer manuellement
dans `data/browser_profile/`).

### Corruption détectée par Diagnostiquer mais 0 fichiers réparés
→ Le magic RIFF/fLaC/ftyp est introuvable dans les premiers 256 KB.
C'est sécuritaire — l'app refuse de toucher un fichier dont la signature
n'est pas reconnue. Le fichier est probablement irrécupérable, ouvre-le
dans un éditeur hex pour confirmer.

---

## Architecture en bref

- **UI** : CustomTkinter (Tk + thème custom) + `ttk.Treeview` pour les
  listes tabulaires (FastList wrapper)
- **DB** : SQLite WAL, connexion thread-locale, autoriser
  readers + writers concurrents
- **Audio** : pygame.mixer 2 canaux pour playback dual-deck +
  librosa pour analyse + waveform
- **Browser** : pywebview (Edge WebView2) reparenté via Win32 SetParent
  dans une CTkFrame, sessions persistantes via `WEBVIEW2_USER_DATA_FOLDER`
- **Threading** : tâches lourdes en threads daemon, UI updates throttlées
  à 80ms par `UiThrottle` pour pas saturer le mainloop

---

*Ultimate DJ — DJ library manager + analyzer + downloader. Édition v1.2.*
