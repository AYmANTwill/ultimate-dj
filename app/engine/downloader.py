"""
Download engine — YouTube, SoundCloud, Spotify via yt-dlp.
Supports MP3 and WAV output with format priority/fallback.
All methods are designed to run in a background thread.

Performance: yt_dlp is ~6 s to import on a cold cache (it eagerly
loads thousands of extractor modules). Importing it at module load
made the Download page block app startup for several seconds. We
defer the import to first use via _yt_dlp() so opening the app
without ever clicking Download stays cheap.
"""
from __future__ import annotations

import os
import time
import threading
from pathlib import Path
from typing import Callable, Optional

from app.config import get_ffmpeg, get_node
from app.logger import log_error, log_info, log_warning


_YDL_CACHE = None


def _yt_dlp():
    """Lazy import of yt_dlp.YoutubeDL — called from download workers
    only, never at module load. Cached after first call."""
    global _YDL_CACHE
    if _YDL_CACHE is None:
        from yt_dlp import YoutubeDL as _YDL
        _YDL_CACHE = _YDL
    return _YDL_CACHE


# Compatibility shim — anything that used `YoutubeDL(opts)` at module
# scope now goes through _yt_dlp() automatically.
class YoutubeDL:
    def __new__(cls, *args, **kwargs):
        return _yt_dlp()(*args, **kwargs)

_BASE_CACHE: dict | None = None


class _SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class _ProgressLogger:
    def __init__(self, cb: Callable | None):
        self.cb = cb
    def debug(self, msg): pass
    def warning(self, msg):
        if self.cb:
            self.cb("warning", msg)
    def error(self, msg):
        if self.cb:
            self.cb("error", msg)
        log_warning(f"yt-dlp: {msg}")


def _yt_base_opts() -> dict:
    """Base options that fix YouTube signature / 403 errors. Cached."""
    global _BASE_CACHE
    if _BASE_CACHE is not None:
        return dict(_BASE_CACHE)

    opts: dict = {
        "extractor_args": {"youtube": {"player_client": ["web"]}},
    }
    node = get_node()
    if node:
        node_dir = os.path.dirname(node)
        if node_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")
        opts["js_runtimes"] = {"node": {"path": node}}

    for browser in ("brave", "edge", "chrome", "firefox"):
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
            extract_cookies_from_browser(browser)
            opts["cookiesfrombrowser"] = (browser,)
            break
        except Exception:
            continue

    _BASE_CACHE = opts
    return dict(opts)


def _safe_filename(name: str, max_len: int = 120) -> str:
    for c in '<>:"/\\|?*':
        name = name.replace(c, "")
    return name[:max_len].strip()


def _postprocessors(codec: str, quality: str) -> list[dict]:
    if codec == "wav":
        return [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]
    return [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality}]


def _wav_postprocessor_args() -> dict:
    """
    Force 16-bit PCM at 44.1 kHz — the only WAV format Rekordbox accepts reliably.
    ffmpeg default produces 32-bit float PCM which many DJ apps reject.
    """
    return {"ffmpegextractaudio": ["-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2"]}


def _max_existing_number(folder: Path) -> int:
    """
    Scan folder for files whose names begin with digits (e.g. '23 - Artist - Title.mp3')
    and return the highest number found.  Returns 0 if none found.
    """
    import re
    max_n = 0
    try:
        for f in folder.iterdir():
            m = re.match(r'^(\d+)\s*[-_.\s]', f.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    except OSError:
        pass
    return max_n


def _file_exists(folder: Path, stem: str, codec: str, fallback: str | None = None) -> bool:
    if (folder / f"{stem}.{codec}").exists():
        return True
    if fallback and (folder / f"{stem}.{fallback}").exists():
        return True
    for ext in ("m4a", "mp4", "webm", "opus"):
        if (folder / f"{stem}.{ext}").exists():
            return True
    return False


def _collect_files(folder: Path, codec: str, fallback: str | None) -> list[str]:
    primary = list(folder.glob(f"*.{codec}"))
    if primary:
        return [str(p) for p in primary]
    if fallback:
        return [str(p) for p in folder.glob(f"*.{fallback}")]
    return [str(p) for p in folder.glob("*.mp3")] + [str(p) for p in folder.glob("*.wav")]


# ── Public API ───────────────────────────────────────────────────

def download_url(
    url: str,
    output_folder: str,
    quality: str = "320",
    codec: str = "mp3",
    fallback_codec: str | None = None,
    numbered: bool = True,
    on_progress: Callable | None = None,
    stop_event: threading.Event | None = None,
) -> list[str]:
    """
    Download from YouTube / SoundCloud URL.
    Returns list of downloaded file paths.
    """
    out = Path(output_folder)
    out.mkdir(parents=True, exist_ok=True)

    ffmpeg    = get_ffmpeg()
    start_num = _max_existing_number(out) + 1 if numbered else 1

    if numbered:
        tmpl = "%(autonumber)02d - %(title)s.%(ext)s"
    else:
        tmpl = "%(title)s.%(ext)s"

    opts = {
        **_yt_base_opts(),
        "format": "bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio/best",
        "outtmpl": str(out / tmpl),
        "autonumber_start": start_num,
        "postprocessors": _postprocessors(codec, quality) + [
            {"key": "EmbedThumbnail"},
            {"key": "FFmpegMetadata"},
        ],
        "writethumbnail": True,
        "ignoreerrors": True,
        "retries": 8,
        "logger": _ProgressLogger(on_progress),
    }
    if codec == "wav":
        opts["postprocessor_args"] = _wav_postprocessor_args()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg

    def hook(d):
        if stop_event and stop_event.is_set():
            raise Exception("Download stopped by user")
        if on_progress and d.get("status") == "downloading":
            pct = d.get("_percent_str", "?").strip()
            speed = d.get("_speed_str", "").strip()
            on_progress("downloading", f"{pct}  {speed}")
    opts["progress_hooks"] = [hook]

    try:
        log_info(f"Download started: {url}")
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
        if on_progress:
            on_progress("done", "Download complete")
        log_info(f"Download finished: {url}")
    except Exception as e:
        if "stopped by user" not in str(e):
            log_error(f"download_url failed for {url}", e)
        if on_progress:
            on_progress("error", str(e))

    files = _collect_files(out, codec, fallback_codec)

    if not files and fallback_codec and not (stop_event and stop_event.is_set()):
        if on_progress:
            on_progress("fallback", f"Retrying as {fallback_codec.upper()}...")
        opts["postprocessors"] = _postprocessors(fallback_codec, quality) + [
            {"key": "EmbedThumbnail"},
            {"key": "FFmpegMetadata"},
        ]
        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:
            log_error(f"Fallback download failed for {url}", e)
        files = _collect_files(out, fallback_codec, None)

    return files


def download_tracks_by_search(
    tracks: list[dict],
    output_folder: str,
    quality: str = "320",
    codec: str = "mp3",
    fallback_codec: str | None = None,
    on_track: Callable | None = None,
    stop_event: threading.Event | None = None,
    pause_event: threading.Event | None = None,
) -> tuple[int, int, list[str], list[dict]]:
    """
    Download tracks by YouTube search (for Spotify playlists).

    on_track(i, total, display_title, status, error_msg)
      status: "downloading" | "ok" | "fail" | "stopped" | "paused"

    Returns (ok_count, fail_count, list_of_paths, list_of_failed_tracks).
    """
    out_dir = Path(output_folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg    = get_ffmpeg()
    base      = _yt_base_opts()
    ok, fail  = 0, 0
    failed_tracks: list[dict] = []

    # Continue numbering from whatever already exists in the folder
    num_offset = _max_existing_number(out_dir)
    log_info(f"Spotify batch download started: {len(tracks)} tracks → {output_folder}"
             f" (starting from #{num_offset + 1})")

    for rel_i, track in enumerate(tracks, 1):
        i = rel_i  # used for progress display (relative to this batch)
        # Check stop before starting each track
        if stop_event and stop_event.is_set():
            if on_track:
                on_track(i, len(tracks), "—", "stopped", "")
            log_info("Download stopped by user")
            break

        # Check pause — block until resumed
        if pause_event and pause_event.is_set():
            if on_track:
                on_track(i, len(tracks), "—", "paused", "")
            while pause_event and pause_event.is_set():
                if stop_event and stop_event.is_set():
                    break
                time.sleep(0.2)
            if stop_event and stop_event.is_set():
                break

        title   = track.get("title", "Unknown")
        artist  = track.get("artist", "Unknown")
        display = f"{artist} — {title}"

        queries = [
            f"{artist} - {title}",
            f"{title} {artist} official audio",
            f"{title} official audio",
            title,
        ]
        abs_num = num_offset + rel_i          # file number in the folder
        stem    = _safe_filename(f"{abs_num:02d} - {artist} - {title}")

        if on_track:
            on_track(i, len(tracks), display, "downloading", "")

        downloaded = False
        last_err = "No result found"

        for query in queries:
            if stop_event and stop_event.is_set():
                break

            captured_errors: list[str] = []

            class _CaptureLogger:
                def debug(self, msg): pass
                def warning(self, msg): pass
                def error(self, msg, *a):
                    captured_errors.append(str(msg))

            opts = {
                **base,
                "format": "bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio/best",
                "outtmpl": str(out_dir / (stem + ".%(ext)s")),
                "postprocessors": _postprocessors(codec, quality),
                "default_search": "ytsearch1",
                "noplaylist": True,
                "ignoreerrors": True,
                "quiet": True,
                "no_warnings": True,
                "logger": _CaptureLogger(),
            }
            if codec == "wav":
                opts["postprocessor_args"] = _wav_postprocessor_args()
            if ffmpeg:
                opts["ffmpeg_location"] = ffmpeg

            try:
                with YoutubeDL(opts) as ydl:
                    ydl.download([f"ytsearch1:{query}"])
            except Exception as e:
                last_err = str(e)
                log_error(f"yt-dlp search failed: {query}", e)
                continue

            if _file_exists(out_dir, stem, codec, fallback_codec):
                downloaded = True
                break

            if captured_errors:
                last_err = captured_errors[-1][:120]

        if downloaded:
            ok += 1
            if on_track:
                on_track(i, len(tracks), display, "ok", "")
        elif not (stop_event and stop_event.is_set()):
            fail += 1
            failed_tracks.append(track)
            log_error(f"Failed to download: {display} — {last_err}")
            if on_track:
                on_track(i, len(tracks), display, "fail", last_err)

    log_info(f"Spotify batch done: {ok} ok / {fail} failed")
    paths = _collect_files(out_dir, codec, fallback_codec)
    return ok, fail, paths, failed_tracks
