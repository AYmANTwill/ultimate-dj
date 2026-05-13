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
  re-sync (diff added/removed instead of full re-download)
- 🔗 **Embedded WebView2 browser** — log in to Spotify / YouTube
  inside the app, persistent sessions via Windows Credential Manager
- 🛡 **Production-grade integrity** — daily `VACUUM INTO` DB snapshots,
  trash + 30-day undo for bulk deletes, log rotation, force-snapshot
  before every destructive operation
- 🩹 **WAV/FLAC/M4A repair tool** — undoes legacy ID3-prefix corruption
  (where mutagen `ID3.save()` silently corrupted non-MP3 containers),
  audit history in `data/repair_history.json`
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
├── tests/test_smoke.py       ← 11 unit + integration tests
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

The transition-scoring AI is being built in five levels. **Level 1
is live**; Levels 2-5 are in flight.

| Level | What | Status |
|---|---|---|
| **L1** Pretrained audio embeddings (CLAP / PANNs / lite) | 256-d vector per track captures sonic identity. Cosine similarity feeds into `transition_score`. | ✅ shipped |
| **L2** Co-occurrence from 1001tracklists | Word2vec-style embeddings learned from real DJ sets. Tracks often mixed by pros end up near each other in the latent space. | 🟡 scraper Phase 1 done; batch + training pending |
| **L3** Structure segmentation | Auto-detect intro/build/drop/break/outro. The Mixer can then score outro-of-A vs intro-of-B instead of comparing whole tracks. | ⚪ design |
| **L4** Custom Siamese transition model | Train on (outro features, intro features) pairs labelled by 1001tracklists ordering. Negative pairs are random tracks not from the same set. | ⚪ design |
| **L5** Active learning | User feedback (👍 / 👎 on suggested transitions) fine-tunes the model on personal taste. | ⚪ design |

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

### Done in v1.3
- AI audio embeddings (L1)
- Production-grade safety net (snapshots + trash + undo)
- Spotify creds → Windows Credential Manager
- WAV/FLAC/M4A repair tool
- Frame-precise sounddevice playback
- Hot-cue keyboard shortcuts
- Resizable panels

### Next up
- AI Level 2 — co-occurrence learning from 1001tracklists batch scrape
- AI Level 3 — structure segmentation (intro/outro detection)
- `pyproject.toml` + `pip-compile` lockfile + `ruff` + pre-commit
- 20+ engine-level tests
- PyInstaller spec → standalone `.exe` distribution

See [`CHANGELOG.md`](CHANGELOG.md) for the full history.

---

## 🤝 Contributing

This is a personal R&D project, but PRs and issues are welcome. Useful
contributions:

- New audio embedding backends (any pretrained music encoder)
- Beat-grid alignment for the BPM-sync feature
- Tests! There are only 11 smoke tests for ~7 500 LOC.
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
