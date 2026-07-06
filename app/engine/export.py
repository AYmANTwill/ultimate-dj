"""
Export tracks / setlists to formats real DJ software can import.

Supported formats:
- M3U8: universal playlist, works with Serato, Engine DJ, VirtualDJ, VLC
- Rekordbox XML: official Pioneer Rekordbox import format
- Serato Crate (.crate): drop into Serato `_Serato_/Subcrates/`

The functions take a list of track dicts (from engine.library.all_tracks)
plus a target path, and write the file. Errors are raised so the UI can
show them instead of silently failing.
"""
from __future__ import annotations

import struct
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom


# ── M3U8 (universal) ─────────────────────────────────────────────

def export_m3u8(tracks: list[dict], out_path: str | Path,
                playlist_name: str = "Ultimate DJ") -> Path:
    """Write a UTF-8 .m3u8 playlist. The format every DJ tool reads."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist_name}"]
    for t in tracks:
        dur = int(t.get("duration") or 0)
        title = t.get("title") or Path(t["path"]).stem
        # #EXTINF:<duration>,<artist - title>
        lines.append(f"#EXTINF:{dur},{title}")
        lines.append(str(Path(t["path"]).resolve()))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# ── Rekordbox XML ────────────────────────────────────────────────

def _rb_url(path: str) -> str:
    """Rekordbox uses file:// URLs with percent-encoded segments."""
    p = Path(path).resolve().as_posix()
    # On Windows that's "C:/Users/..."; we want "file://localhost/C:/Users/..."
    if len(p) >= 2 and p[1] == ":":
        return "file://localhost/" + urllib.parse.quote(p, safe="/:")
    return "file://" + urllib.parse.quote(p, safe="/")


def export_rekordbox_xml(tracks: list[dict], out_path: str | Path,
                          playlist_name: str = "Ultimate DJ") -> Path:
    """Write a Rekordbox-compatible XML library + playlist.

    Rekordbox imports this through Preferences → Advanced → rekordbox xml.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="UltimateDJ", Version="1.2",
                   Company="UltimateDJ")

    collection = ET.SubElement(root, "COLLECTION", Entries=str(len(tracks)))
    for i, t in enumerate(tracks, 1):
        dur = int(t.get("duration") or 0)
        bpm = float(t.get("bpm") or 0)
        attrs = {
            "TrackID":     str(i),
            "Name":        t.get("title") or Path(t["path"]).stem,
            "Kind":        "MP3 File",
            "Location":    _rb_url(t["path"]),
            "TotalTime":   str(dur),
            "AverageBpm":  f"{bpm:.2f}",
        }
        if t.get("key"):
            attrs["Tonality"] = t["key"]
        if t.get("genre"):
            attrs["Genre"] = t["genre"]
        if t.get("rating"):
            # Rekordbox uses 0/51/102/153/204/255 for 0–5 stars
            attrs["Rating"] = str(int(t["rating"]) * 51)

        track_el = ET.SubElement(collection, "TRACK", attrs)
        # Cue points (HOT_CUE 0..7 in Rekordbox)
        for j, cue in enumerate(_decode_cues(t)[:8]):
            ET.SubElement(track_el, "POSITION_MARK",
                           Name=str(cue.get("label", "")),
                           Type="0",
                           Start=f"{float(cue.get('position', 0)):.3f}",
                           Num=str(j))

    # Playlists root → ONE folder containing one playlist
    pls_root = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(pls_root, "NODE", Type="0", Name="ROOT", Count="1")
    pl = ET.SubElement(folder, "NODE", Type="1",
                        Name=playlist_name, KeyType="0",
                        Entries=str(len(tracks)))
    for i in range(1, len(tracks) + 1):
        ET.SubElement(pl, "TRACK", Key=str(i))

    # Pretty-print
    pretty = minidom.parseString(ET.tostring(root, encoding="utf-8"))
    out.write_text(pretty.toprettyxml(indent="  ", encoding="utf-8")
                       .decode("utf-8"),
                   encoding="utf-8")
    return out


# ── Serato .crate ────────────────────────────────────────────────

def _serato_field(tag: bytes, body: bytes) -> bytes:
    """Build one length-prefixed Serato field."""
    return tag + struct.pack(">I", len(body)) + body


def _serato_str(s: str) -> bytes:
    """Serato strings are UTF-16-BE."""
    return s.encode("utf-16-be")


def export_serato_crate(tracks: list[dict], out_path: str | Path) -> Path:
    """Write a Serato `.crate` file. Drop it into `_Serato_/Subcrates/`.

    Each track must live on the same drive as the Serato library, and
    paths in the crate are stored RELATIVE to that drive root (Serato's
    quirk). On Windows that means the path becomes "Users/me/Music/x.mp3"
    instead of "C:/Users/...".
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    body = b""
    # Crate header — version field
    body += _serato_field(b"vrsn", _serato_str("1.0/Serato ScratchLive Crate"))

    for t in tracks:
        p = Path(t["path"]).resolve()
        # Drop the drive letter — Serato wants it relative to the volume
        as_posix = p.as_posix()
        if len(as_posix) >= 2 and as_posix[1] == ":":
            rel = as_posix[3:]  # strip "C:/"
        else:
            rel = as_posix.lstrip("/")
        track_body = _serato_field(b"ptrk", _serato_str(rel))
        body += _serato_field(b"otrk", track_body)

    out.write_bytes(body)
    return out


# ── helpers ──────────────────────────────────────────────────────

def _decode_cues(track: dict) -> list[dict]:
    raw = track.get("cue_points")
    if not raw:
        return []
    import json
    try:
        return list(json.loads(raw))
    except Exception as e:
        from app.logger import log_warning
        log_warning(f"export: corrupt cue_points for "
                    f"{track.get('path', '?')}: {e}")
        return []
