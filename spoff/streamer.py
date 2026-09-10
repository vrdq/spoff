import re
import logging
import threading
from typing import Optional, Dict, Any, cast
import yt_dlp
try:
    from .storage import CACHE_DIR, register_cached_track
except ImportError:
    from storage import CACHE_DIR, register_cached_track

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

def search_and_resolve_stream(track_title: str, artist: str, direct_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Rapidly resolves a playable direct audio stream URL.
    """
    cache_key = f"{track_title.lower()}::{artist.lower()}"
    if direct_url:
        cache_key = f"{direct_url}::{cache_key}"
    if cache_key in _stream_cache:
        return _stream_cache[cache_key]

    queries = []
    if direct_url and (direct_url.startswith("http://") or direct_url.startswith("https://")):
        queries.append(direct_url)
    elif track_title.startswith("http://") or track_title.startswith("https://"):
        queries.append(track_title)
    elif len(track_title) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', track_title):
        queries.append(f"https://www.youtube.com/watch?v={track_title}")

    clean_artist = "" if artist.lower() in ("unknown artist", "unknown", "none", "") else artist.strip()
    if clean_artist:
        queries.extend([
            f"{track_title} {clean_artist} audio",
            f"{track_title} {clean_artist}",
        ])
    else:
        queries.extend([
            f"{track_title} audio",
            track_title,
        ])
    ydl_opts = get_base_ydl_opts()
    try:
        with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
            item = None
            for query in queries:
                try:
                    res = ydl.extract_info(query, download=False)
                    if res:
                        entries: Any = res.get("entries")
                        if entries:
                            entry_list = list(entries) if not isinstance(entries, list) else entries
                            if entry_list:
                                item = entry_list[0]
                                break
                        else:
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
                formats = item.get("formats")
                if formats:
                    audio_formats = [f for f in formats if isinstance(f, dict) and f.get("acodec") != "none" and f.get("url")]
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

def download_track_to_cache(track_id: str, title: str, artist: str, on_complete=None, direct_url: Optional[str] = None):
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
            if direct_url and (direct_url.startswith("http://") or direct_url.startswith("https://")):
                query = direct_url
            elif title.startswith("http://") or title.startswith("https://"):
                query = title
            elif len(track_id) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', track_id):
                query = f"https://www.youtube.com/watch?v={track_id}"
            elif len(title) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', title):
                query = f"https://www.youtube.com/watch?v={title}"
            else:
                clean_artist = "" if artist.lower() in ("unknown artist", "unknown", "none", "") else artist.strip()
                query = f"{title} {clean_artist} audio" if clean_artist else f"{title} audio"
            ydl_opts = get_base_ydl_opts({
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": f"{str(temp_path)}.%(ext)s",
                "overwrites": True
            })
            with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
                ydl.download([query])

            valid_exts = (".m4a", ".opus", ".mp3", ".webm", ".ogg", ".flac")
            for f in CACHE_DIR.glob(f"{track_id}_dl.*"):
                if not f.is_file():
                    continue
                if f.name.endswith((".part", ".ytdl", ".temp", ".aria2")):
                    continue
                ext = f.suffix.lower()
                if ext in valid_exts and f.stat().st_size > 10000:
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
