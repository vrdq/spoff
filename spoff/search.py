import json
import re
import logging
import urllib.request
import urllib.parse
from typing import List, Dict, Any, cast
import yt_dlp

try:
    from .ytmusic import search_ytmusic_tracks, get_ytmusic_search_suggestions
except ImportError:
    try:
        from ytmusic import search_ytmusic_tracks, get_ytmusic_search_suggestions
    except ImportError:
        search_ytmusic_tracks = None
        get_ytmusic_search_suggestions = None

logger = logging.getLogger("search")

def get_search_suggestions(query: str) -> List[str]:
    """Gets real-time search query suggestions as the user types."""
    if not query.strip():
        return []
    if get_ytmusic_search_suggestions:
        suggestions = get_ytmusic_search_suggestions(query)
        if suggestions:
            return suggestions
    try:
        encoded = urllib.parse.quote(query.strip())
        url = f"https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&q={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            content = resp.read().decode("utf-8")
        match = re.search(r'\((\[.*\])\)', content)
        if match:
            data = json.loads(match.group(1))
            return [item[0] for item in data[1][:6]]
    except Exception as e:
        logger.debug(f"Suggestions query failed: {e}")
    return []

def live_search_tracks(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Live searches YouTube/YT Music for playable tracks with metadata."""
    if not query.strip():
        return []

    # Fast path: use ytmusicapi
    if search_ytmusic_tracks:
        res = search_ytmusic_tracks(query, limit=limit)
        if res:
            return res

    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    
    tracks: List[Dict[str, Any]] = []
    try:
        with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{query.strip()}", download=False)
            if not res:
                return tracks
            entries: Any = res.get("entries")
            entry_list = list(entries) if entries else []
            for e in entry_list:
                if not e or not isinstance(e, dict):
                    continue
                t_id = e.get("id")
                title = e.get("title") or "Unknown Track"
                uploader = e.get("uploader") or e.get("channel") or "Unknown Artist"
                uploader = re.sub(r'(?i)\s*-\s*topic$', '', uploader).strip()
                # Strip common redundant suffixes
                cleaned_title = re.sub(r'(?i)\s*[\(\[](official\s*(video|audio|lyric|music\s*video)|lyrics|audio|hd|4k)[\)\]]', '', title).strip()
                if " - " in cleaned_title:
                    parts = cleaned_title.split(" - ", 1)
                    artist_part = parts[0].strip()
                    title_part = parts[1].strip()
                    if uploader.lower() in ("unknown artist", "unknown", "") or artist_part.lower() in uploader.lower() or uploader.lower() in artist_part.lower():
                        cleaned_title = title_part
                        if uploader.lower() in ("unknown artist", "unknown", ""):
                            uploader = artist_part
                
                tracks.append({
                    "id": t_id,
                    "title": cleaned_title if cleaned_title else title,
                    "artist": uploader,
                    "duration_ms": int((e.get("duration") or 0) * 1000),
                    "url": e.get("url") or f"https://www.youtube.com/watch?v={t_id}",
                    "source": "ytmusic"
                })
    except Exception as e:
        logger.error(f"Live search failed for '{query}': {e}")
        
    return tracks
