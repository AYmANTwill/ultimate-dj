# 🎛 Ultimate DJ

> **A music-aware DJ library manager for Windows** — analyses your tracks
> with librosa, finds harmonic transitions through real audio similarity
> (not just BPM matching), and previews your mixes with frame-precise
> dual-deck playback.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)]()
[![Status: Active](https://img.shields.io/badge/status-active%20development-brightgreen)]()

---

## Why this exists

Every DJ library tool on the market — Rekordbox, Serato, Mixed In Key,
Lexicon — picks transitions by **BPM + Camelot wheel**. That's pattern
matching on two numbers. It can't tell that a dark techno track and a
melodic house track in the same key are completely wrong for the same
moment of a set.

**Ultimate DJ** scores transitions on **four signals**, with the last
one being the real differentiator:

| Axis | Weight | What it captures |
|---|---|---|
| Camelot key (graduated) | 40% | Harmonic compatibility, energy-boost rule, modal switch |
| BPM (tight + ½×/2× aware) | 30% | Beatmatching feasibility |
| Energy flow | 10% | Build-up vs drop progression |
| **Audio embedding similarity** | **20%** | **Actual sonic fingerprint — captures timbre, production density, mood, instrument family** |

Plus genre family bonus, A/B rating ratchet, same-artist penalty.
The result: the Mixer's "Best Transitions" panel actually surfaces
tracks that **sound like they belong together**, not just tracks that
share a number on the Camelot wheel.

---

## ✨ Headline features

- 🎵 **Audio analysis** — BPM, musical key, Camelot code, energy,
  beat-grid (per-beat onsets), via `librosa` + a hand-rolled spectral
  fingerprint
- 🤖 **AI audio embeddings** — 256-d L2-normalised vectors per track,
  three pluggable backends: pure-numpy lite (always works) →
  AudioSet-trained PANNs (optional) → LAION CLAP (state of the art,
  optional)
- 🎚 **Frame-precise dual-deck preview** — `sounddevice` based, real
  seek (not `pygame.mixer`'s fake one), per-deck volume, equal-power
  crossfader, hot-cue keyboard shortcuts (`1`-`8`, `Space`, `Home`)
- ⬇️ **YouTube / SoundCloud / Spotify download** — `yt-dlp` with
  Cloudflare bypass, Spotify playlist resolution via the API, smart
  re-sync (diff added/removed instead of full re-download), manual
  track selection before any download fires, folder bootstrap (a
  playlist downloaded before the sync system existed is recognised
  from its files — only new songs download), and a `.m3u8` written in
  the exact Spotify order on every sync
- 🔗 **Embedded WebView2 browser** — log in to Spotify / YouTube
  inside the app, persistent sessions via Windows Credential Manager
- 🛡 **Production-grade integrity** — daily `VACUUM INTO` DB snapshots,
  trash + 30-day undo for bulk deletes, log rotation, force-snapshot
  before every destructive operation
- 🩹 **WAV/FLAC/M4A repair tool v2** — undoes BOTH legacy corruptions:
  ID3 bytes prepended before the container magic (v1) and the `id3 `
  chunk appended after `data` that makes Rekordbox 7 / Engine DJ refuse
  WAV files (v2 — RIFF chunk walker, reversible: the cut tail is kept
  in `data/repair_tails/`), audit history in `data/repair_history.json`.
  Tag writes are now **verified-or-reverted** with per-format opt-in.
- 📤 **Export to Rekordbox XML, Serato `.crate`, M3U8** — your work
  travels with you
- ⌨️ **Pro-DJ UX** — resizable panels, virtualised `ttk.Treeview`
  lists (5000 tracks render in <500 ms), non-blocking toasts, keyboard
  shortcuts, opt-in tag writing (so Rekordbox does its own analysis
  without interference)

---

## 📷 Screenshots


> app has 8 pages: Home / Download / Library / Analyze / Mixer /
> Setlist / Discover / Settings._

<img width="1918" height="1045" alt="image" src="https://github.com/user-attachments/assets/77f822f2-976b-445d-90b0-f4ab76f9e56f" />

<img width="1915" height="1046" alt="image" src="https://github.com/user-attachments/assets/fb492efb-d143-4f0e-abcc-1bd71b35d9fb" />

<img width="1919" height="1049" alt="image" src="https://github.com/user-attachments/assets/d8b4d3e8-717d-4938-95a8-64b84c78fea2" />

<img width="1919" height="1054" alt="image" src="https://github.com/user-attachments/assets/62d1495b-923a-4b80-9189-a5d492bc4d69" />

<img width="1917" height="1046" alt="image" src="https://github.com/user-attachments/assets/a26aee94-ab77-4063-8e1e-828da756864b" />



---

## 🚀 Quick start

### Requirements

- **Windows 10 / 11** (Win32 APIs used for WebView2 reparenting)
- **Python 3.10 or 3.11**
- **FFmpeg** + **Node.js** (auto-installed via `winget` on first launch)

### Install

```bash
git clone https://github.com/AYmANTwill/ultimate-dj.git
cd ultimate-dj
python -m pip install -r requirements.txt
```

### Launch

```bash
python run.py
```

…or double-click `UltimateDJ.bat`.

First launch shows a setup splash and auto-installs any missing
dependencies via `pip` + `winget`. After that the app starts in
< 1 second.

### Configure

1. **Settings → Paths** → set your music folder
2. **Settings → Spotify API** → paste Client ID + Secret (free at
   [developer.spotify.com](https://developer.spotify.com/))
   — credentials are stored encrypted via Windows Credential Manager,
   never in plaintext
3. **Library → Sync Library** → walks the folder, analyses new files
4. **(optional) Settings → AI · Embeddings audio → Encoder les nouveaux**
   — computes per-track audio embeddings in background (~1 s per
   track on the lite backend) so the Mixer can do AI-aware transitions

See [`GUIDE.md`](GUIDE.md) for the full user manual (French).

---

## 🏗 Architecture

```
ultimate-dj/
├── run.py                    ← entry point (deps check → mainloop)
├── app/
│   ├── config.py             ← paths, themes, opt-ins
│   ├── deps.py               ← first-launch auto-installer
│   ├── logger.py             ← rotating file logger (5 × 2 MB)
│   ├── secrets_store.py      ← keyring wrapper (Win Cred Manager)
│   │
│   ├── engine/               ← business logic, **no Tk**
│   │   ├── library.py        ← SQLite WAL, schema, search, scoring
│   │   ├── analyzer.py       ← BPM/key/energy/beats (librosa)
│   │   ├── embeddings.py     ← AI audio fingerprints (3 backends)
│   │   ├── player.py         ← sounddevice dual-deck + time-stretch
│   │   ├── downloader.py     ← yt-dlp wrapper
│   │   ├── spotify.py        ← Spotify API client
│   │   ├── tracklists.py     ← 1001tracklists scraper
│   │   ├── export.py         ← Rekordbox XML, Serato, M3U8 writers
│   │   ├── repair.py         ← WAV/FLAC/M4A magic-prefix stripper
│   │   └── backup.py         ← VACUUM INTO snapshots
│   │
│   └── ui/                   ← CustomTkinter, page-per-file
│       ├── app.py            ← sidebar + page switcher + startup
│       ├── home.py           ← dashboard
│       ├── library.py        ← table + filters + bulk actions
│       ├── mixer.py          ← transitions + dual-deck preview
│       ├── deck.py           ← waveform + transport + cues
│       ├── fastlist.py       ← ttk.Treeview wrapper (virtualised)
│       ├── toast.py          ← non-blocking notifications
│       ├── track_editor.py   ← BPM override, rating, genre, cues
│       ├── browser.py        ← WebView2 reparenting (Win32 SetParent)
│       └── …
│
├── tests/                    ← 43 tests (engine, repair v2, smoke)
├── data/                     ← runtime state (gitignored)
│   ├── dj_library.db         ← SQLite WAL
│   ├── db_backups/           ← daily VACUUM INTO snapshots
│   ├── waveforms/            ← .npy peak caches
│   ├── tracklists/           ← scraped 1001tracklists JSON
│   ├── browser_profile/      ← WebView2 cookies
│   └── repair_history.json   ← audit trail
└── GUIDE.md                  ← user manual
```

### Design principles

- **`engine/` knows nothing about Tk** — pure Python, fully testable
- **Threading via `UiThrottle`** — workers post UI updates at most every
  80 ms via a coalescing queue, so the main loop is never flooded
- **DB safety in depth** — WAL mode + thread-local connections +
  trash-not-delete + daily snapshots + force-snapshot before destructive ops
- **Graceful degradation** — every optional dependency (CLAP,
  rubberband.exe, keyring, panns_inference) falls back to a pure-Python
  alternative so the app works on a vanilla install
- **No file mutation by default** — `write_tags_to_files` is opt-in
  via Settings. Your audio files stay byte-for-byte identical unless
  you ask otherwise — Rekordbox / Serato / Engine DJ do their own
  analysis on import without our values interfering

---

## 🤖 The AI layer (roadmap)

The transition-scoring AI is shipped end-to-end across all 5 levels.
Every layer is wired into the production transition score in
`library.transition_score`; the Mixer's per-transition breakdown popup
shows the contribution from each.

| Level | What | Status |
|---|---|---|
| **L1** Pretrained audio embeddings (CLAP / PANNs / lite) | 256-d vector per track captures sonic identity. Cosine similarity feeds into `transition_score`. | ✅ shipped |
| **L2** Co-occurrence from 1001tracklists | Position-decay weights mined from cached tracklists (`engine/cooccurrence.py`); plugged into `transition_score` as a 5th axis — tracks that pro DJs actually mix together get a bonus over tracks that just share a key. | ✅ shipped |
| **L3** Structure segmentation | RMS-envelope heuristic (`engine/segmentation.py`) auto-detects `intro_end` / `outro_start` / drops on every `analyze_track`. Mixer scores outro-of-A vs intro-of-B instead of comparing whole tracks. | ✅ shipped |
| **L4** Custom Siamese transition model | Trainable Siamese net (`engine/transition_model.py`) — shared encoder, contrastive loss on (outro_A, intro_B) pairs from 1001tracklists ordering + folded-in user feedback, ~200 k params, CPU-trainable in ~30 s on a 1 k-track library. `score()` is wired into `library.transition_score` (±10 pt swing) AND surfaced in the Mixer's per-transition score breakdown. **Settings → AI · Modèle de transition** has a one-click train + reset. Day-1 cold start handled by `bootstrap_pairs()` — synthesizes training data by distilling the heuristic + cooc + feedback scorer when no real DJ data exists yet; the next retrain (after the user scrapes a set or votes) supersedes it with real signal. | ✅ shipped end-to-end |
| **L5** Active learning | User feedback (👍 / 👎 on suggested transitions) — instant score modifier (+12 / –25) AND folded into the L4 training set as oversampled high-confidence examples. After every vote, `transition_model.maybe_auto_retrain()` checks the delta since the last train and (if the **Settings → AI · Modèle de transition** auto-retrain toggle is on, ≥ 10 new votes, torch available, and no train already running) fires a background retrain visible in the activity tray. | ✅ shipped end-to-end |

---

## 🧰 Tech stack

- **Python 3.10+** · **Windows 10/11**
- **UI** — CustomTkinter + ttk.Treeview + custom Win32 reparenting
  for the embedded WebView2
- **DB** — SQLite WAL, thread-local connections, idempotent migrations
- **Audio analysis** — librosa, hand-rolled spectral features (numpy
  STFT + mel filterbank + MFCC) to work around `numba` / NumPy 2.2
  incompatibility
- **Audio playback** — `sounddevice` (PortAudio) for frame-precise
  dual-deck seek
- **Time-stretch** — `pyrubberband` (best) → `librosa.effects` →
  `ffmpeg atempo` (always available) fallback chain
- **Downloads** — `yt-dlp` with Cloudflare bypass via `cloudscraper`
- **External APIs** — Spotify (creds via `keyring` / DPAPI), 1001tracklists
- **Tags** — `mutagen` per-extension dispatch (`MP3` / `WAVE` / `FLAC` /
  `MP4` / `OggVorbis`) so WAV/FLAC/M4A files are never corrupted

---

## 🛣 Roadmap

### Done in v1.3 → v1.4
- AI Level 1 — pretrained audio embeddings
- AI Level 2 — 1001tracklists co-occurrence wired into the transition score
- AI Level 3 — RMS-envelope intro/outro segmentation
- AI Level 4 — Siamese transition model end-to-end: trainable, `score()` wired into the scoreur AND shown in the Mixer breakdown popup, one-click training in **Settings → AI · Modèle de transition** with per-epoch progress in the activity tray, day-1 cold-start handled by `bootstrap_pairs()` distillation of the heuristic scorer (so the model is useful from the very first train, even without any scraped tracklists)
- L4 training pipeline (`engine/training_pipeline.py`) — multi-stage corpus enricher: scan top artists in your lib → discover their sets on 1001tracklists → batch scrape (with Cloudflare circuit breaker) → download missing tracks via yt-dlp into `data/training_corpus/` → analyse + embed → optionally purge MP3 (embeddings-only mode keeps DB at ~50 MB for 5 k corpus tracks) → cooccurrence rebuild → L4 retrain. One-click in **Settings → AI · Pipeline d'entraînement**.
- Playwright-backed 1001tracklists scraping — modern 1001tracklists serves a JS-rendered shell that cloudscraper can't reach. Pipeline now escalates to a stealthed headless Chromium (`playwright` + `playwright-stealth`) for both DJ-index discovery and individual set fetches. IP rate-limits are detected and surfaced as a distinct `IPLimitedError` instead of a silent 0-result run.
- FMA Small (`engine/fma.py`) — Free Music Archive integration: downloads + extracts 8 000 cross-genre 30s clips (~7 GB, resumable), analyses each into the DB with `source='fma'` (hidden from UI), purges MP3 in embeddings-only mode. Anchors the L4 embedding space with diversity the user library alone can't provide.
- AI Level 5 — 👍/👎 feedback loop fully closed: Mixer buttons → instant score modifier (+12 / –25) → oversampled into L4's training set → optional auto-retrain in the background when ≥ 10 new votes accumulate since the last train (toggle in Settings)
- Discover page — 1-click batch scrape from a DJ slug
- Activity tray — every background job visible top-left (lazy-built on first task)
- `pyproject.toml` + 12 engine unit tests
- PyInstaller spec + one-shot build script → standalone `.exe`
- Per-monitor DPI awareness — embedded Spotify / YouTube / SoundCloud
  / 1001Tracklists windows now report the correct viewport (fixes
  Spotify's `position: fixed` playback bar disappearing)
- Boot time slashed by deferring heavy module imports + lazy ActivityTray
  + auto-scan librosa import moved to worker thread (no UI freeze)

### Done in v1.4 → v1.5
- **WAV repair v2** — RIFF chunk walker (`inspect_chunks`) catches the
  trailing-`id3 ` corruption that v1's prefix detector calls "ok";
  `repair_trailing` cuts the tail + fixes the RIFF size atomically, the
  removed bytes are saved under `data/repair_tails/` so `undo_trailing`
  restores byte-identical files. Dry-run on the real library: 363/451
  flagged, 0 false positives. Legit trailing chunks (`LIST`, `cue `…)
  are flagged `review` and never touched. After a 10-file pilot verified
  in Rekordbox, all 363 were repaired and machine-verified (0 failures,
  full undo retained).
- **Write guard rails** — per-format tag-write opt-in
  (`write_tags_wav/flac/m4a`, all default OFF, survive `force=True`),
  magic-byte pre-flight on every non-MP3, and WAV writes are
  verified-or-reverted (snapshot → save → chunk re-walk → byte-identical
  restore if the layout is no longer clean). The active corruption
  vector (mutagen's WAVE wrapper appends `id3 ` after `data`) is
  neutralised.
- **Mixer: L4-vs-heuristic verdict** — the breakdown popup now shows
  whether the Siamese model agrees, disputes (≥ 6 pts against the
  heuristic) or is neutral on each transition; disputed suggestions get
  a ▲/▼ marker in the list and a "L4 doute — tranche" panel turns them
  into one-click vote targets (active learning).
- **Feedback UX** — keyboard votes (`F` = 👍, `D` = 👎, `X` = clear)
  while the Mixer is focused; Settings shows votes-since-last-train vs
  the auto-retrain threshold.
- **Auto-retrain after rebuild** — "Reconstruire la matrice" now fires
  a background L4 retrain when pairs actually changed (opt-in gated).
- **Spotify playlist order preserved** — the download queue follows the
  playlist order (a set-difference used to scramble it) and every sync
  writes `<playlist>.m3u8` next to the files in the exact source order
  (Rekordbox/Engine/VLC importable, no audio file renamed).
- **Manual track selection** — before any playlist download fires, a
  dialog lists every track with checkboxes (filter, live counter,
  all/none) so a 150-song playlist can be trimmed in one pass.
- **Folder bootstrap re-sync** — a folder downloaded before the sync
  system existed is recognised by fuzzy-matching its files against the
  playlist; matched tracks are skipped, only new songs download.
- **Downloader unblocked** — the 2025-era `player_client: ["web"]` pin
  and browser-cookie attachment now hit YouTube's PO-token wall and
  killed 100 % of downloads ("Requested format is not available");
  both removed, verified end-to-end.
- **setlist.fm fallback skeleton** (`engine/setlist_fm.py`) — REST
  client (stdlib only) mapping setlists into the cooccurrence cache
  format, ready as Plan B for 1001tracklists rate-limits (one fired
  2026-07-05); needs a free API key in `setlistfm_api_key`.
- **Tests 12 → 43** — repair v2 (16), parser fixture, matcher
  precision, L4-None, playlist diff order, m3u, bootstrap, resolve
  regression, write guards. Plus a completed silent-swallow audit:
  13 `log_warning` added on data paths (corrupt JSON, scrape cache,
  embedding blobs, taste profile), every remaining swallow classified
  as legitimate teardown/cleanup/migration best-effort — new data-path
  swallows must log, as policy.
- **CI** — GitHub Actions (`windows-latest`): ruff (non-blocking for
  now) + pytest on every push/PR.
- **Living project map** — `docs/PROJECT_MAP.md` (subsystems, DB, AI
  layers, deep relations, progress board + journal) with an offline 3D
  interactive twin (`docs/PROJECT_MAP.html`) including a quest tree
  with XP, dependencies and blockers.
- **L1 audio axis recalibrated** — measured mean pairwise cosine of
  0.971 between RANDOM tracks on the lite backend: the absolute
  (cos+1)×50 mapping returned 95-100 for every pair, a dead axis.
  `similarity_score` now maps the library's own p5..p95 range onto
  0..100 (auto-recalibrated when embeddings change): spread went from
  std 1.9 to 26.4 on real pairs.
- **L4 evaluated honestly** — held-out ranking AUC **0.899** vs 0.601
  for raw features without the model: the Siamese genuinely learned.
- **kbps column + spectral truth detector** — container bitrate stored
  and shown per track, plus a numpy-STFT spectral-ceiling probe that
  catches transcodes hiding in 320/lossless containers (a "WAV 1411k"
  ceiling at 16 kHz is a ~128k rip). Real-library scan: 530/1104
  suspected transcodes, only 2 genuinely full-band files.
- **Corpus dedup made name-aware** — an audio-cosine hit alone paired
  Janet Jackson with Skrillex at 0.9999 and deleted a whole downloaded
  batch as "duplicates"; a dup verdict now also requires a name match.
- **Environment hardening** — numba>=0.66 pinned (older numba rejects
  NumPy 2.2 and killed librosa.load entirely), polyphase resampling on
  all fixed-rate loads, repair scans now persist the ⚠ corrupt flags,
  and a setlist.fm API-key field in Settings activates the fallback.
- **Corpus ×12.8** — 604 corpus tracks ingested end-to-end by the
  pipeline itself (embeddings kept, audio purged by design, rows now
  survive library syncs): L2 pairs 584 → 7 468, L4 retrained on
  44 776 examples.
- **`settings.py` split** — the 2 129-line monolith became a 6-file
  package (all < 800 LOC) with verbatim bodies and a headless-build
  check.
- **Shareable Windows build** — `python build_share.py` produces a
  verified folder app: environment preflight (Python 3.10/3.11,
  64-bit, core imports), string-imported pages collected into the
  bundle, torch/transformers excluded (−1.5 GB), ffmpeg/ffprobe/node
  copied into `bin/` and smoke-run at copy time, frozen-safe embedded
  browser (the exe relaunches itself with `--browser-launcher`), app
  icon. Friends unzip and double-click — no Python, no pip, no winget.
- **Download hardening** — a playlist folder that already exists
  resyncs with the disk as authority (rename-tolerant matching), Stop
  cancels mid-download instead of hanging, SSL failures self-diagnose
  Windows clock skew (a 7-day-late clock rejected Spotify's freshly
  rotated cert while every older cert still passed), and emoji-only
  playlist names get a safe `.m3u8` filename.

### Next up
- Finish the CLAP embedding migration (re-encode → rebuild → retrain),
  then re-run the per-track evaluation (lite baseline: AUC 0.546 on
  unseen tracks — the model memorises identities, CLAP is the
  generalisation lever)
- Reach 50 L5 votes in the Mixer (2/50) so the feedback layer becomes
  measurable
- setlist.fm fallback activation — paste the free API key (the
  test/fetch button now ships in Settings; engine + tests done)
- Code signing (SmartScreen) on the shareable build
- Continuous-learning auto-trigger — fire `enrich_corpus()` when ≥ N
  new tracks land in the library

See [`CHANGELOG.md`](CHANGELOG.md) for the full history.

---

## 🤝 Contributing

This is a personal R&D project, but PRs and issues are welcome. Useful
contributions:

- New audio embedding backends (any pretrained music encoder)
- Beat-grid alignment for the BPM-sync feature
- Tests! 43 today for ~18 000 LOC — the UI layer is still untested.
- Cross-platform support (macOS audio, Linux WebView fallback)
- Translations (currently French-leaning UI strings)

---

## 📜 License

[MIT](LICENSE) — go nuts, just keep the notice.

---

## Acknowledgments

Built on the shoulders of:
[librosa](https://librosa.org/),
[yt-dlp](https://github.com/yt-dlp/yt-dlp),
[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter),
[sounddevice](https://python-sounddevice.readthedocs.io/),
[mutagen](https://mutagen.readthedocs.io/),
[pywebview](https://pywebview.flowrl.com/),
[LAION CLAP](https://github.com/LAION-AI/CLAP),
[PANNs](https://github.com/qiuqiangkong/audioset_tagging_cnn),
and [breakfastquay's rubberband](https://breakfastquay.com/rubberband/).

The Camelot wheel transition rules come from Mixed In Key's
publicly-documented harmonic-mixing system.
