import bisect
import hashlib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .storage import DATA_DIR, _atomic_json_dump

logger = logging.getLogger("spoff.lyrics")

LYRICS_DIR = DATA_DIR / "lyrics"


def _init_lyrics_storage():
    try:
        LYRICS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create lyrics directory {LYRICS_DIR}: {e}")


def clean_track_query(title: str, artist: str = "") -> Tuple[str, str]:
    """Cleans fluff like '(Official Video)', '[Remastered]', '- Topic', feat tags."""
    t = title or ""
    a = artist or ""

    if " - " in t and not a:
        parts = t.split(" - ", 1)
        a = parts[0].strip()
        t = parts[1].strip()
    elif " - " in t and a and t.lower().startswith(a.lower()):
        t = t[len(a):].lstrip(" -").strip()

    # Strip bracketed fluff
    t = re.sub(
        r"(?i)\s*[\(\[][^)\]]*(?:official|audio|video|remaster|live|lyrics|hd|4k|ft\.?|feat\.?)[^)\]]*[\)\]]",
        "",
        t,
    )
    # Strip trailing unbracketed feat tags
    t = re.sub(r"(?i)\s+(?:feat\.?|ft\.?)\s+.*$", "", t)

    if a:
        a = re.sub(r"(?i)\s+(?:feat\.?|ft\.?)\s+.*$", "", a)
        a = a.replace(" - Topic", "").strip()

    return t.strip(), a.strip()


def parse_lrc(lrc_text: str) -> List[Dict[str, Any]]:
    """
    Parses standard LRC lyrics into a sorted list of timestamped lines.
    Each element: {'time': float, 'text': str}
    """
    lines: List[Dict[str, Any]] = []
    if not lrc_text:
        return lines

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Check for metadata tags like [ti:...], [ar:...], [length:...]
        if re.match(r"^\[[a-zA-Z]+:", line):
            continue

        # Extract all timestamps on this line
        time_matches = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        if not time_matches:
            continue

        # Remove timestamp tags to get pure lyric text
        content = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", line).strip()

        for m_str, s_str in time_matches:
            try:
                seconds = float(m_str) * 60.0 + float(s_str)
                lines.append({"time": round(seconds, 2), "text": content})
            except ValueError:
                continue

    lines.sort(key=lambda item: item["time"])
    return lines


def _get_cache_path(title: str, artist: str) -> Path:
    _init_lyrics_storage()
    safe_key = hashlib.sha256(f"{artist.lower().strip()}_{title.lower().strip()}".encode("utf-8")).hexdigest()
    return LYRICS_DIR / f"{safe_key}.json"


def fetch_lyrics(title: str, artist: str = "", duration_ms: Optional[int] = None) -> Dict[str, Any]:
    """
    Fetches lyrics from local cache or LRCLIB.
    Returns:
    {
        "title": str,
        "artist": str,
        "synced": bool,
        "instrumental": bool,
        "lines": List[Dict[str, Any]],
        "plain": str
    }
    """
    clean_t, clean_a = clean_track_query(title, artist)
    cache_file = _get_cache_path(clean_t, clean_a)

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning(f"Failed to read cached lyrics for '{clean_t}': {e}")

    result_data = {
        "title": clean_t or title,
        "artist": clean_a or artist,
        "synced": False,
        "instrumental": False,
        "lines": [],
        "plain": "",
    }

    # 1. Try exact match via LRCLIB /api/get
    base_url = "https://lrclib.net/api/get"
    params = {
        "track_name": clean_t,
        "artist_name": clean_a,
    }
    if duration_ms and duration_ms > 0:
        params["duration"] = str(int(duration_ms / 1000))

    headers = {"User-Agent": "SpoffTUI/0.1.0"}
    raw_json = None

    try:
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                raw_json = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.debug(f"LRCLIB get error {e.code} for '{clean_t}': {e}")
    except Exception as e:
        logger.debug(f"LRCLIB request failed for '{clean_t}': {e}")

    # 2. Fallback to /api/search if exact match missed
    if not raw_json:
        try:
            search_query = f"{clean_a} {clean_t}".strip()
            search_url = f"https://lrclib.net/api/search?{urllib.parse.urlencode({'q': search_query})}"
            s_req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(s_req, timeout=5) as s_resp:
                if s_resp.status == 200:
                    s_data = json.loads(s_resp.read().decode("utf-8"))
                    if isinstance(s_data, list) and len(s_data) > 0:
                        raw_json = s_data[0]
        except Exception as e:
            logger.debug(f"LRCLIB search fallback failed for '{clean_t}': {e}")

    if raw_json and isinstance(raw_json, dict):
        if raw_json.get("instrumental"):
            result_data["instrumental"] = True
        else:
            synced_lrc = raw_json.get("syncedLyrics")
            plain_lrc = raw_json.get("plainLyrics")

            if synced_lrc:
                parsed_lines = parse_lrc(synced_lrc)
                if parsed_lines:
                    result_data["lines"] = parsed_lines
                    result_data["synced"] = True

            if not result_data["synced"] and plain_lrc:
                result_data["plain"] = plain_lrc
                result_data["lines"] = [
                    {"time": None, "text": pl_line.strip()}
                    for pl_line in plain_lrc.splitlines()
                    if pl_line.strip()
                ]

    # Cache result to disk only if we obtained lyrics or confirmed instrumental (avoid caching network dropouts)
    if result_data.get("lines") or result_data.get("instrumental"):
        try:
            _atomic_json_dump(cache_file, result_data)
        except Exception as e:
            logger.warning(f"Could not cache lyrics to {cache_file}: {e}")

    return result_data


def get_active_lyric_index(lines: List[Dict[str, Any]], current_seconds: float) -> int:
    """
    Returns the index of the currently active lyric line based on current playback seconds.
    Returns -1 if before the first line or if lines are empty/unsynced.
    """
    if not lines:
        return -1

    # If first line has no timestamp (plain text lyrics)
    if lines[0].get("time") is None:
        return -1

    times = [item["time"] for item in lines if item.get("time") is not None]
    if not times:
        return -1

    idx = bisect.bisect_right(times, current_seconds) - 1
    return idx
