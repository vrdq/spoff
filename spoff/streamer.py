import os
import re
import sys
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any
import yt_dlp
try:
    from .storage import CACHE_DIR, get_cached_track_path, register_cached_track
except ImportError:
    from storage import CACHE_DIR, get_cached_track_path, register_cached_track

logger = logging.getLogger("streamer")
_active_downloads = set()
_download_lock = threading.Lock()
_stream_cache = {}

def get_base_ydl_opts(extra_opts=None):
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch1:",
        "extract_flat": False,
    }
    if extra_opts:
        opts.update(extra_opts)
    return opts

def search_and_resolve_stream(track_title: str, artist: str) -> Optional[Dict[str, Any]]:
    """
    Rapidly resolves a playable direct audio stream URL.
    """
    cache_key = f"{track_title.lower()}::{artist.lower()}"
    if cache_key in _stream_cache:
        return _stream_cache[cache_key]

    queries = [
        f"{track_title} {artist} audio",
        f"{track_title} {artist}",
    ]
    ydl_opts = get_base_ydl_opts()
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            item = None
            for query in queries:
                try:
                    res = ydl.extract_info(query, download=False)
                    if res:
                        if "entries" in res and res["entries"]:
                            item = res["entries"][0]
                            break
                        elif not res.get("entries"):
                            item = res
                            break
                except Exception as ex:
                    logger.debug(f"Search query '{query}' failed: {ex}")
                    continue

            if not item:
                return None

            stream_url = item.get("url")
            # If item is a manifest or has multiple formats, pick bestaudio
            if not stream_url and "formats" in item:
                audio_formats = [f for f in item["formats"] if f.get("acodec") != "none" and f.get("url")]
                if audio_formats:
                    stream_url = audio_formats[-1].get("url")

            if not stream_url and item.get("webpage_url"):
                stream_url = item.get("webpage_url")

            if not stream_url:
                logger.error(f"No stream URL found in result for: {track_title} {artist}")
                return None

            stream_data = {
                "stream_url": stream_url,
                "duration": item.get("duration", 0),
                "webpage_url": item.get("webpage_url"),
                "ext": item.get("ext", "m4a")
            }
            _stream_cache[cache_key] = stream_data
            return stream_data
    except Exception as e:
        logger.error(f"Error resolving stream for {track_title} {artist}: {e}")
        return None

def download_track_to_cache(track_id: str, title: str, artist: str, on_complete=None):
    """
    Asynchronously downloads track to local disk.
    """
    with _download_lock:
        if track_id in _active_downloads:
            return None
        _active_downloads.add(track_id)

    def _worker():
        try:
            target_path = CACHE_DIR / f"{track_id}.m4a"
            if target_path.exists() and target_path.stat().st_size > 10000:
                if on_complete:
                    on_complete(target_path)
                return

            temp_path = CACHE_DIR / f"{track_id}_dl"
            query = f"{title} {artist} audio"
            ydl_opts = get_base_ydl_opts({
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": f"{str(temp_path)}.%(ext)s",
                "overwrites": True
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([query])

            for f in CACHE_DIR.glob(f"{track_id}_dl.*"):
                if f.is_file() and f.stat().st_size > 10000:
                    ext = f.suffix
                    final_path = CACHE_DIR / f"{track_id}{ext}"
                    if f != final_path:
                        f.replace(final_path)
                    register_cached_track(track_id, {"title": title, "artist": artist}, final_path)
                    if on_complete:
                        on_complete(final_path)
                    break
        except Exception as e:
            logger.error(f"Failed to background cache track {track_id}: {e}")
        finally:
            with _download_lock:
                _active_downloads.discard(track_id)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
