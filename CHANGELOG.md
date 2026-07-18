# Changelog

All notable changes to **Ultimate DJ** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/) and the project
uses [Semantic Versioning](https://semver.org/).

---

## [1.6.0] — 2026-07-17 · **First shareable release (GO)**

The owner declared the app complete; the launch runbook
(`docs/LAUNCH_PLAN.md`) was executed end-to-end. Highlights since 1.3
(full details in the README "Done in" sections):

- **WAV repair v2** — 363/363 corrupted files repaired, byte-identical
  undo retained.
- **CLAP embeddings everywhere** — per-track generalisation AUC
  0.546 → 0.712; similarity axis percentile-calibrated.
- **First-party training** — the owner's own Rekordbox history
  (33 sets) feeds the co-occurrence layer (10 362 pairs) and 67
  validated transitions feed the feedback layer; model retrained.
- **Live mode v1** — real-time now-playing detection from Rekordbox
  (read-only) + set-aware next-track suggestions.
- **Zero-dependency Windows build** — 937 MB folder app with
  ffmpeg/ffprobe/node bundled, frozen-safe embedded browser, icon;
  unzip and double-click. SmartScreen warning assumed (unsigned),
  documented in LISEZ-MOI.txt.
- Hardening: disk-full resilience lessons, silent-swallow audit
  closed, clock-skew self-diagnosis on SSL failures, 60 tests green
  in CI.

## [Unreleased] — v1.3 (AI layer & integrity sprint)

### Added — AI level 1

- **Audio embeddings engine** (`app/engine/embeddings.py`)
  - 256-d L2-normalised vector per track captures sonic identity
  - Three-tier backend: **CLAP** (best, optional) → **PANNs** (optional) →
    **lite** (pure-numpy STFT + mel filterbank + MFCC + spectral
    statistics — always available, no extra deps)
  - Sidesteps the broken `librosa.feature.*` → numba → numpy 2.2
    import chain by computing everything via numpy + soundfile/ffmpeg
  - BLOB column `tracks.embedding` + per-backend metadata
  - Background bulk-encoder with ETA, progress toast, idempotent
- **AI-aware transition scoring** — `transition_score` now weights
  `key 40% · BPM 30% · audio similarity 20% · energy 10%` when both
  tracks have embeddings, with graceful fallback to the heuristic
  `key 50% · BPM 35% · energy 15%` otherwise
- **Cooccurrence groundwork** — `engine/tracklists.py` Phase 1 scraper
  for 1001tracklists with fuzzy-match against the local library
  (per-set JSON cache, Cloudflare bypass via `cloudscraper`, 5s
  rate-limit). Phase 2 (batch) + Phase 3 (training) on the roadmap.

### Added — integrity & safety

- **DB auto-backup** (`app/engine/backup.py`) — daily `VACUUM INTO`
  snapshots in `data/db_backups/`, kept × 10 rotating. Forced snapshot
  before every destructive operation (bulk delete, sync orphans,
  deduplication). One-click restore from any snapshot in Settings.
- **Trash + Undo** — bulk delete is now soft. Tracks live in the
  `trash` table for 30 days before being permanently dropped. Undo via
  toast button immediately after deletion, or from the Library trash
  filter later.
- **Spotify credentials → Windows Credential Manager** (`keyring`
  + DPAPI encryption). Legacy plaintext copies in `config.json` are
  auto-migrated on first launch.
- **Log rotation** — 5 × 2 MB rolling, no more unbounded growth.

### Added — UX

- **Toast notification system** (`app/ui/toast.py`) — non-blocking,
  auto-dismissing, optional action button (Undo, Open log…).
- **Hot-cue keyboard shortcuts** on each DeckWidget — `1-8` jump to
  cue points, `Space` toggle play/pause, `Home/0` seek to start.
  Focused per deck so the two Mixer decks never fight.
- **Auto-scan on startup** — silent sync after first paint, logs new
  imports + orphans to `errors.log`.
- **Sync ETA** — rolling-average `Analysing 12/348 · ETA 4min32s`.
- **Corrupt WAV badge** in Library — auto-detected during sync, ⚠
  prefix in title column, filter switch.
- **Stretch backend indicator** — Mixer Sync button shows whether
  rubberband or ffmpeg will run.
- **Track Editor confirmation** — explicit ON/OFF banner for tag
  writing, toast on save.
- **Smart playlist re-sync** — pasting a previously-downloaded
  playlist URL shows a diff (added/kept/removed) and the user opts in
  to removing tracks no longer in the source list.
- **Resizable panels** — `tk.PanedWindow` sashes in Download
  (FolderBrowser ↔ embedded browser) and Mixer (Library ↔ Transitions
  horizontal, Lists ↔ Decks vertical).
- **FastList column stretching** — title/genre columns absorb extra
  horizontal space, narrow indicators stay tight.

### Changed

- **Audio playback engine rewrite** — `pygame.mixer` → `sounddevice`.
  Frame-precise seek is now real (the old pygame implementation
  silently restarted from frame 0 on every `seek()`).
- **WaveformDeck repaint** — three-layer tag system (`static` /
  `cue` / `playhead`). Tick now redraws **1 line** instead of the
  whole waveform → 50× cheaper, no chunkiness on heavy tracks.
- **PanedWindow** uses `opaqueresize=False` for smooth drag on CTk-heavy
  pages.

### Fixed

- 🚨 **WAV / FLAC / M4A corruption** by the legacy `write_tags()` —
  was prepending raw ID3v2 bytes before the container magic, breaking
  Rekordbox 7 / Engine DJ imports. New dispatch-by-extension uses the
  correct mutagen wrapper for each format (`WAVE`, `FLAC`, `MP4`,
  `OggVorbis`) so the file's container is never violated.
- **WAV repair tool** (`engine/repair.py`) recovers files corrupted
  by the legacy bug — locates the real magic bytes (RIFF/fLaC/ftyp)
  and rewrites the file from there. Audit history kept in
  `data/repair_history.json` (no .bak clutter on disk).
- **`write_tags` is now opt-in** — `Settings → Interop` toggle.
  Default OFF so Rekordbox / Engine / Serato can do their own
  analysis on import without being seeded by Ultimate DJ's values.
- **SQLite thread safety** — `WAL` mode + thread-local connections.
  Concurrent readers + writer no longer trigger `ProgrammingError`.
- **Duplicates modal** — was creating thousands of CTk widgets on
  the UI thread (1 minute freeze + crash on libraries with many
  duplicates). Now uses `FastList`, off-thread scan, click-to-keep
  selection model.
- **BPM lock checkbox** in Track Editor — `BooleanVar.set(True)`
  didn't always repaint the `CTkCheckBox`. Forced via `.select()`.

### Removed

- `pygame` (replaced by `sounddevice` for frame-precise audio)
- 3 unused themes (`Sunset`, `Forest`, `Light`) — kept only `Cyan
  Night` (default dark) and `Mono` (high-contrast booth)
- Standalone Camelot wheel page — merged into Mixer's transition list

---

## [v1.2] — Library-manager baseline

### Added

- Setlist save/load (DB-persisted)
- Library context menu (rating ★, genre, tags, export, delete)
- Filters: title / key / BPM / genre / rating
- Export Rekordbox XML / Serato `.crate` / M3U8
- Track Editor with rating, genre, tags, BPM override, tap-tempo,
  key confidence
- Cue points UI on Deck widgets

### Performance

- DB SQLite WAL + thread-locale (concurrent reads + writes)
- FastList (`ttk.Treeview` wrapper) — 5000 rows in <500 ms
- UI throttling (80 ms) for high-frequency status / progress updates
- `find_spec` startup-time optimisation (4.25 s → 0.26 s)

---

## [v1.1] — Initial release

- BPM / key / Camelot / energy detection (librosa)
- YouTube / SoundCloud / Spotify downloads (yt-dlp + Spotify API)
- Discover page (Spotify recommendations)
- Embedded WebView2 browser
- Camelot transition scoring (key 50 / BPM 40 / energy 10)
