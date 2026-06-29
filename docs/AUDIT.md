# Ultimate DJ — 360° Project Audit

Baseline snapshot of the codebase health, captured at v1.4 (post-L4-shipped,
post-1001tracklists-scraping, post-FMA-integration). Re-run the audit and
update this document whenever a major area lands.

---

## TL;DR — global score 🟡

The app does a LOT and the AI roadmap is shipped end-to-end (L1-L5 all
live, real DJ pairs feeding L4), but there's accumulated scar tissue
in three areas — error handling, file mutation safety, and a few
oversized modules — that the next sprint should clean up before
piling on new features.

| Dimension | Score | One-liner |
|---|---|---|
| 1. Architecture | 🟢 | `engine/` ↔ `ui/` separation is enforced (0 reverse imports). |
| 2. Code volume | 🟡 | 18 k LOC, 7 files over 600 LOC, `settings.py` is 1959 lines. |
| 3. Tests | 🔴 | 555 LOC of tests for 18 k LOC of source (~3 %). 2 files only. |
| 4. Error handling | 🟡 | 122 silent `except Exception: pass` patterns swallow bugs. |
| 5. File mutation safety | 🔴 | The WAV/FLAC/M4A corruption regression proves the audio-write path needs hardening (see Risk #1). |
| 6. Threading | 🟡 | 68 thread/lock sites, mostly clean, a few cross-thread Playwright traps already fixed. |
| 7. Secrets | 🟢 | All creds in Windows Credential Manager via `keyring`, no plaintext. |
| 8. AI pipeline | 🟢 | All 5 levels live, real signal flowing, L4 trained on Gorillaz sets (loss 0.29). |
| 9. Performance | 🟢 | Boot < 1s warm-cache after the lazy-import + DPI awareness work. |
| 10. Build/distribute | 🟡 | PyInstaller spec works; no installer, no code signing. |
| 11. Documentation | 🟢 | README + GUIDE + CHANGELOG kept in sync per durable rule. |
| 12. UX/Reliability | 🟡 | Activity tray pattern just stabilised; embedded browser DPI fix landed; legacy ID3-corruption damage on user lib remains to be reversed. |

---

## 1. Architecture

**Status: 🟢 healthy**

```
app/
├── __init__.py           per-monitor DPI awareness (1st thing imported)
├── config.py             paths, themes, opt-ins
├── deps.py               first-launch dependency installer
├── logger.py             rotating file logger
├── secrets_store.py      keyring wrapper for Spotify + 1001tracklists creds
├── engine/   (20 files, pure Python, NO Tk)
└── ui/       (21 files, CTk, page-per-file)
```

- **engine ↔ ui boundary respected**: 0 imports from `app.engine` to `app.ui`
  (verified by grep). All cross-talk goes UI → engine, never the reverse.
- **Top UI → engine consumers**: `library` (10 importers) and `analyzer`
  (2) — expected hotspots, not a smell.
- **Dependency injection**: engine modules take `conn` as parameter
  rather than importing a global — makes them testable.

**Watch-outs**:
- `app/ui/settings.py` is 1959 LOC — see Dimension 2.
- Some UI workers do non-trivial business logic inline (the FMA + corpus
  enrichment pipelines could be moved fully into `engine/training_pipeline.py`).

---

## 2. Code volume

**Status: 🟡 some files need to be split**

- **18 142 LOC** of Python in `app/`
- **20 engine modules + 21 UI modules**
- **Files ≥ 600 LOC (7)**:
  | File | LOC | Notes |
  |---|---|---|
  | `app/ui/settings.py` | 1959 | Holds ~15 settings sections; ripe for splitting into a `settings/` package |
  | `app/engine/library.py` | 1377 | DB schema + transition_score + breakdown + utility; could split out scoring |
  | `app/engine/tracklists.py` | 1234 | Login flow + scrape + parse + matching all in one file |
  | `app/ui/download.py` | 903 | Folder browser + Spotify resolution + UI live in one page |
  | `app/ui/library.py` | 792 | Treeview + filters + bulk ops + transitions panel |
  | `app/ui/discover.py` | 695 | Akinator + scraper batch + URL importer |
  | `app/ui/mixer.py` | 673 | Decks + transitions + feedback row |

- **Zero `# TODO` / `# FIXME` markers** — either everything is done or
  technical debt is invisible. The latter is more likely; recommend
  routing future "to fix later" notes through explicit markers.

---

## 3. Tests

**Status: 🔴 dangerously thin**

- **555 LOC of tests, 18 142 LOC of source → ~3 % ratio**
- Only **2 test files**: `tests/test_smoke.py` (276 LOC) and
  `tests/test_engine.py` (279 LOC).
- The README claims "12 engine unit tests" — pytest run shows the
  baseline is roughly that. Modules like `tracklists.py` (1234 LOC,
  critical surface) have minimal coverage.

**Immediate priorities**:
1. Test the WAV-tag-write path so the corruption regression can't repeat
   (Risk #1) — test should FAIL if any byte is written outside the
   declared `data` chunk.
2. Test the cooccurrence matcher with realistic edge cases (ID
   placeholders, "Artist & B - Title" variants, case + accent).
3. Test L4 inference output range (0-100) and that None propagates
   correctly when the model is missing.
4. Test the login flow at the unit level via a mocked Playwright context
   (the integration test costs real captcha solving — skip in CI).

---

## 4. Error handling

**Status: 🟡 too many silent swallows**

- **122 `except Exception: pass` patterns** across the codebase.
- Most are defensive (e.g., progress callbacks, optional UI updates)
  but a significant minority sit on data paths and can hide bugs.

**Recommendation**: audit the top-10 silent-swallow sites with the
`Read` tool, decide per case: (a) keep with a `log_warning()` added,
(b) catch a narrower exception type, (c) let it propagate.

---

## 5. File mutation safety

**Status: 🔴 active regression on user audio**

The legacy `analyzer.write_tags()` corrupted ~363 WAV files in the
user's library by appending a non-standard `id3 ` chunk after the
`data` chunk — Rekordbox 7 / Engine DJ refuse to open them. Audio is
intact; structural fix is straightforward (truncate after `data` chunk
end, update RIFF size). Full root cause + repair plan is in the
upcoming `repair_v2` work.

**Hard guard rails to add**:
1. Mark `write_tags()` for `.wav` / `.flac` / `.m4a` as **explicit
   opt-in PER format** (not a single global opt-in) — most users want
   tags in MP3 but never in WAV.
2. Pre-flight: read 64 KB of any non-MP3 audio file BEFORE mutating
   it; refuse if the magic isn't where we expect it (RIFF at 0, fLaC
   at 0, ftyp at 4).
3. Round-trip assertion: after `audio.save()`, re-open the file via
   the standard decoder and verify the data chunk is still last.

The new `repair_v2` will reverse the existing damage; the guard rails
above prevent recurrence.

---

## 6. Threading

**Status: 🟡 clean architecture, recently stabilised**

- **68 thread/lock sites** across engine + UI.
- The `_get_thread_browser()` / `threading.local` pattern for Playwright
  was added after a real crash (`greenlet.error: Cannot switch to a
  different thread`) — pattern is now correct.
- Activity tray now lives on the root window as a status bar — pattern
  is correct (page switches can't unpack it).
- DB connections are thread-local in `library._local` — pattern is
  correct (SQLite handles per-thread cleanly under WAL).

**One remaining smell**: `transition_model._RETRAIN_LOCK` is module-
level; if the auto-retrain is triggered from a UI thread holding the
Tk main loop, the lock acquisition could starve other callers.
Low-prob in practice but worth a defensive timeout.

---

## 7. Secrets

**Status: 🟢 clean**

- All creds (Spotify Client ID/Secret, 1001tracklists email/password)
  go through `app.secrets_store` → Windows Credential Manager via
  `keyring`.
- `ensure_migrated()` runs at boot, pulls any legacy plaintext out of
  `config.json` and into the keyring, then blanks the JSON fields.
- Cookies in `data/tracklists_auth_state.json` are session tokens, not
  passwords — kept under `data/` which is gitignored.

---

## 8. AI pipeline (L1-L5)

**Status: 🟢 end-to-end functional, real signal flowing**

| Layer | State | Numbers |
|---|---|---|
| L1 Embeddings | ✅ | 1033/1066 user tracks encoded |
| L2 Co-occurrence | ✅ | 582 pairs from 55 scraped tracklists |
| L3 Segmentation | ✅ | 1066/1066 segmented |
| L4 Siamese model | ✅ | trained on 3492 examples (585 real + 2907 random-neg), loss 0.29 |
| L5 Feedback | ✅ | 2 votes captured; engine + UI + auto-retrain wired |

**Concrete gains since v1.3**: cooccurrence matrix went from 8 → 582 pairs
after the precision-first hybrid matcher landed (token-sort + audio
dedup). L4 loss improved from 0.60 (bootstrap distillation only) to 0.29
(real DJ pairs).

**Next moves**:
- Scrape Pegassi / Maceo Plex / Daft Punk catalogues (high-match-rate
  artists in the user's library) to grow the positive-pair pool.
- Expose the L4-vs-heuristic delta in the Mixer breakdown so the user
  can SEE which transitions L4 disagrees with.
- L5 auto-retrain currently triggers at delta = 10 votes; should also
  trigger after every "Reconstruire la matrice" rebuild.

---

## 9. Performance

**Status: 🟢 boot < 1 s warm-cache**

- App boot path: deps check → CTk root → home page → activity tray →
  background jobs.
- Major perf wins already shipped: lazy `app.ui.app` imports, deferred
  `librosa` import into worker thread, lazy ActivityTray rebuild (since
  reverted to eager — the trade-off was solidity over 130 ms).
- DPI awareness set in `app/__init__.py` BEFORE any Tk import so CTk
  picks the right scaling factor at startup.

**Watch-outs**:
- `enrich_corpus()` is single-threaded over discovery → scrape → match
  → train. Could parallelise scrape vs match. Not urgent.
- The cooccurrence rebuild ran ~336 s on 55 tracklists × ~22 tracks
  each (= ~1200 × library match operations, O(N²) on title strings).
  Could memoise normalisation + add an index on tokenised title.

---

## 10. Build / distribution

**Status: 🟡 functional, not polished**

- `ultimate_dj.spec` + `build.bat` produce a working PyInstaller exe.
- No installer (.msi / .nsis) — user double-clicks the exe.
- No code signing — Windows SmartScreen will flag unknown publisher.
- Heavy installs (torch ~700 MB, Playwright Chromium ~150 MB) are
  triggered by setting toggles, not bundled — the install is light by
  default.
- No CI: tests + build are run manually.

---

## 11. Documentation

**Status: 🟢 maintained**

- `README.md` updated on every push per durable rule.
- `GUIDE.md` exists (user manual, French).
- `CHANGELOG.md` follows the README's "Done in vX / Next up" structure.
- This `docs/AUDIT.md` is the new entry — re-run the audit before each
  major release.
- `CLAUDE.md` at repo root captures conventions for AI assistants.

---

## 12. UX / Reliability

**Status: 🟡 several recent stabilisations, residual risks**

Recently stabilised (commits visible in `git log`):
- Activity tray: float-via-place → root-status-bar (impossible to bury).
- Embedded WebView2: per-monitor DPI awareness, force-scale-factor=1,
  burst of re-fits to catch late-rendering SPAs (Spotify, SoundCloud).
- Settings buttons: pre-mounted progress rows (no layout reflow), all
  long-running buttons give immediate visual feedback.
- 1001tracklists scraping: cookies cross-thread propagation via mtime
  check, strict logged-in detection via real-scrape verification.

Residual risks:
- **WAV corruption from legacy `write_tags()`** — see Dimension 5.
  Highest priority before any further library writes.
- IP-rate-limit recovery: the auth cookies bypass guest limit, but if
  1001tracklists tightens further (per-account limits), we have no
  fallback. Setlist.fm is a documented Plan B (REST API, no scraping).

---

## Top 10 risks (by impact × likelihood)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | More WAV corruption when `write_tags_to_files` is flipped on | High | Critical (data loss) | Per-format opt-in + pre-flight check + round-trip assertion |
| 2 | 1001tracklists tightens auth (per-account quota) | Medium | High (corpus stops growing) | Setlist.fm fallback path |
| 3 | Silent `except Exception: pass` hides a real bug | High | Medium | Audit top-10 sites, narrow exception types or add logging |
| 4 | `settings.py` 1959 LOC becomes unmaintainable | Medium | Low | Split into `app/ui/settings/` package |
| 5 | Tests too thin → regression in matcher / parser | High | Medium | Add tests for tracklist parser + matcher + L4 inference |
| 6 | Playwright cookie file deleted by user → silent re-login loop | Low | Low | Detect missing file and prompt clearly |
| 7 | DB schema migration on first run fails silently | Low | High | Already wrapped in try/except; add explicit logging |
| 8 | Torch missing → user can't train but sees no clear message | Low | Low | `transition_model.train()` returns False with log message; consider Settings status row |
| 9 | yt-dlp extractor break (YouTube rolls out new player) | Medium | Medium | Already bumps via `yt-dlp[default]` weekly — keep auto-update |
| 10 | FMA download (~7 GB) interrupted, partial extraction | Medium | Low | Resume logic exists; verify on a real test |

---

## Top 10 quick wins (< 1 hour each)

1. Per-format opt-in toggle in Settings → Interop (1 boolean per fmt).
2. Pre-flight magic-byte check before any `audio.save()` for WAV / FLAC / M4A.
3. Narrow the top-10 silent-swallow patterns; add `log_warning()` to
   anything on a data path.
4. Test for the WAV-tag-write path that fails if any byte is written
   outside the declared `data` chunk.
5. Add `# pragma: no audit` markers to acceptable-silent patterns so
   the count of "real" risky swallows drops.
6. Split `settings.py` into `app/ui/settings/__init__.py` +
   `_credentials.py`, `_paths.py`, `_ai.py`, `_repair.py`.
7. Expose L4-vs-heuristic delta in the Mixer breakdown popup.
8. Trigger auto-retrain after `cooccurrence.rebuild()` (not just after
   feedback-delta threshold).
9. Build `setlist.fm` fallback skeleton in `engine/setlist_fm.py` for
   when 1001tracklists is unreachable.
10. Add a `pytest` GitHub Actions workflow so tests run on every push.

---

## Roadmap recommendation (3 sprints)

### Sprint A — "Reverse the damage" (1 week)
- Build + ship `repair_v2()` for trailing-id3 corruption.
- Per-format opt-in + pre-flight + round-trip assertion in `analyzer`.
- Bulk-repair UI in Settings → "Réparer la lib" with progress bar.
- Tests for the write path (must fail on bad mutation).

### Sprint B — "Lock down quality" (1 week)
- Tests: tracklist parser, matcher, L4 inference range.
- Audit top-20 silent-swallows; narrow or log them.
- Split `settings.py` into a package.
- GitHub Actions CI: pytest + ruff check on every PR.

### Sprint C — "Grow the AI signal" (1-2 weeks)
- Scrape Pegassi / Maceo Plex / 3-5 high-match-rate artist catalogues.
- Setlist.fm fallback skeleton.
- Mixer breakdown: surface L4-vs-heuristic delta.
- Trigger retrain after every cooccurrence rebuild.

---

## How to re-run this audit

```bash
python _audit_stats.py            # raw numbers (LOC, tests, AI state)
grep -rn "except Exception: pass" app/ --include="*.py" | wc -l
grep -rn "TODO\|FIXME\|XXX\|HACK" app/ --include="*.py"
git log --oneline -20             # recent change velocity
```

Or invoke the `dj-architect` subagent (see `.claude/agents/dj-architect.md`)
with prompt `"Re-run the 360 audit and update docs/AUDIT.md inline"`.
