# Ultimate DJ — project notes for Claude Code

This file is loaded into every Claude Code session as project context.
Keep it short, factual, and update when conventions change.

## What this project is

DJ library manager + analyser + downloader + transition-recommender for
Windows. Python + CustomTkinter UI, librosa/sounddevice audio,
Playwright scraping, PyTorch Siamese model for transition scoring.

Single-user solo project, public GitHub repo
`AYmANTwill/ultimate-dj`.

## Common commands

```bash
python run.py                      # launch the app
python -m pytest tests/ -q         # run smoke + engine tests
python _check_boot.py              # diagnose boot times
python _check_parse.py             # diagnose 1001tracklists fetch+parse
ruff format app/ tests/            # auto-format Python sources
ruff check app/ tests/             # lint
```

## Architecture (one-paragraph version)

`run.py` → boot deps check → `App` (CTk root) → sidebar + content frame
+ docked activity tray. `app/engine/` is pure-Python business logic
(no Tk imports): `library.py` (SQLite WAL), `analyzer.py` (BPM/key/
energy), `embeddings.py` (audio fingerprints), `segmentation.py`
(intro/outro), `tracklists.py` (Playwright-based 1001tracklists
scraping with stealth + cookies), `transition_model.py` (Siamese L4),
`cooccurrence.py` (L2), `feedback.py` (L5), `training_pipeline.py`
(end-to-end corpus enricher), `repair.py` (audio file repair),
`downloader.py` (yt-dlp wrapper). `app/ui/` is the CTk page-per-file
UI. `app/__init__.py` enables per-monitor DPI awareness before any Tk
import.

## Conventions (durable rules)

- **No `Co-Authored-By: Claude` trailer in commits.** Solo authorship.
  This is enforced; do not add it back.
- **Sync README before every push.** Update the AI roadmap table and
  "Done in vX / Next up" lists in the same commit as the code change
  if the roadmap status moves.
- **Default to writing no comments.** Code should be self-explanatory.
  Add a comment only when the WHY is non-obvious (workaround for a
  specific bug, hidden invariant, surprising behaviour).
- **Never touch user audio files without explicit opt-in.** The
  `write_tags_to_files` setting defaults False; do not enable it
  automatically. Anything that mutates `.wav`/`.flac`/`.m4a` must
  go through `engine.repair` or `engine.analyzer.write_tags()` (which
  itself respects the opt-in gate).
- **Pin scraping config to the user's logged-in session.** Cookies
  live in `data/tracklists_auth_state.json` and are loaded by every
  thread via `tracklists._get_thread_browser()`. Do not bypass that
  helper.

## Memory pointers

`C:/Users/knade.MSI_TWILL/.claude/projects/D--UltimateDJ---Copie/memory/`
holds session-persistent feedback. The two durable entries today:

- `feedback_readme_on_push.md` — keep README in sync at every push
- `feedback_no_claude_coauthor.md` — no Claude co-author trailer

## Subagents worth invoking

- `Explore` — code search beyond Grep/Glob for cross-file context
- `Plan` — design step before non-trivial implementation
- `claude-code-guide` — questions about Claude Code itself (hooks,
  MCPs, agent SDK)

## Skills worth invoking (on demand)

- `security-review` — before touching `tracklists.py` login flow,
  `secrets_store.py`, `_browser_launcher.py`
- `simplify` — clean up redundant code after a feature lands
- `consolidate-memory` — periodic cleanup of the memory folder
- `review` — before opening a PR
- `loop` / `schedule` — for the overnight corpus enrichment pipeline

## What NOT to do

- Don't run destructive git ops (`reset --hard`, `push --force`)
  without explicit user confirmation. Force-push to main is allowed
  ONLY when rewriting history the user asked for.
- Don't add new dependencies casually. Each one is documented in
  `requirements.txt` + auto-installed by `app/deps.py`. Heavy installs
  (torch, playwright Chromium) are opt-in by setting/section.
- Don't commit the diag scripts at the repo root (`_check_boot.py`,
  `_check_parse.py`, `_check_scrape.py`, `_scrape_dump.html`,
  `_tracklist_dump.html`) — they're local workspace tools and stay
  out of git (see `.gitignore`).
