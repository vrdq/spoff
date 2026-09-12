import re
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Callable, cast
from concurrent.futures import Future
import yt_dlp
try:
    from .storage import CACHE_DIR, register_cached_track, get_cached_track_path, validate_track_id, CACHE_EXTENSIONS
except ImportError:
    from storage import CACHE_DIR, register_cached_track, get_cached_track_path, validate_track_id, CACHE_EXTENSIONS

logger = logging.getLogger("streamer")
_active_downloads = set()
_active_download_futures: Dict[str, Future] = {}
_download_lock = threading.Lock()
_stream_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
STREAM_CACHE_TTL = 7200.0  # 2 hours

def invalidate_stream_cache(track_title: str, artist: str, direct_url: Optional[str] = None):
    cache_key = f"{track_title.lower()}::{artist.lower()}"
    if direct_url:
        cache_key = f"{direct_url}::{cache_key}"
    _stream_cache.pop(cache_key, None)

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
    cached_entry = _stream_cache.get(cache_key)
    if cached_entry is not None:
        cached_data, cached_ts = cached_entry
        if time.time() - cached_ts < STREAM_CACHE_TTL:
            return cached_data

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
            if len(_stream_cache) > 500:
                _stream_cache.clear()
            _stream_cache[cache_key] = (stream_data, time.time())
            return stream_data
    except Exception as e:
        logger.error(f"Error resolving stream for {track_title} {artist}: {e}")
        return None

def download_track_to_cache(
    track_id: str,
    title: str,
    artist: str,
    on_complete: Optional[Callable[[Path], None]] = None,
    direct_url: Optional[str] = None,
    track_meta: Optional[Dict[str, Any]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    blocking: bool = False
) -> Optional[threading.Thread]:
    """
    Downloads track to local disk cache. If blocking is True, runs synchronously;
    otherwise spawns a daemon background thread.
    """
    try:
        val_id = validate_track_id(track_id)
    except ValueError as e:
        if on_error:
            on_error(e)
        return None

    cached_path = get_cached_track_path(val_id)
    if cached_path and cached_path.is_file() and cached_path.stat().st_size > 10000:
        if on_complete:
            on_complete(cached_path)
        return None

    with _download_lock:
        existing_future = _active_download_futures.get(val_id)
        if existing_future is not None or val_id in _active_downloads:
            if existing_future is None:
                existing_future = Future()
                _active_download_futures[val_id] = existing_future
                # If someone added to _active_downloads externally without a worker, complete it with an error
                existing_future.set_exception(RuntimeError(f"Track {val_id} is already being downloaded"))
            future = existing_future
            is_new = False
        else:
            future = Future()
            _active_download_futures[val_id] = future
            _active_downloads.add(val_id)
            is_new = True

    def _deliver(fut: Future):
        try:
            res_p = fut.result()
            if on_complete:
                on_complete(res_p)
        except Exception as ex:
            if on_error:
                on_error(ex)

    if not is_new:
        if blocking:
            try:
                res_p = future.result(timeout=300)
                if on_complete:
                    on_complete(res_p)
            except Exception as ex:
                if on_error:
                    on_error(ex)
            return None
        else:
            future.add_done_callback(_deliver)
            return None

    future.add_done_callback(_deliver)

    def _worker():
        try:
            temp_path = CACHE_DIR / f"{val_id}_dl"
            if direct_url and (direct_url.startswith("http://") or direct_url.startswith("https://")):
                query = direct_url
            elif title.startswith("http://") or title.startswith("https://"):
                query = title
            elif len(val_id) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', val_id):
                query = f"https://www.youtube.com/watch?v={val_id}"
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

            valid_exts = CACHE_EXTENSIONS
            downloaded_path: Optional[Path] = None
            for f in CACHE_DIR.glob(f"{val_id}_dl.*"):
                if not f.is_file():
                    continue
                if f.name.endswith((".part", ".ytdl", ".temp", ".aria2")):
                    continue
                ext = f.suffix.lower()
                if ext in valid_exts and f.stat().st_size > 10000:
                    final_path = CACHE_DIR / f"{val_id}{ext}"
                    if f != final_path:
                        f.replace(final_path)
                    meta_to_save = dict(track_meta) if track_meta else {"title": title, "artist": artist}
                    register_cached_track(val_id, meta_to_save, final_path)
                    downloaded_path = final_path
                    if not future.done():
                        future.set_result(final_path)
                    break

            if not downloaded_path:
                cached_existing = get_cached_track_path(val_id)
                if cached_existing and cached_existing.is_file() and cached_existing.stat().st_size > 10000:
                    if not future.done():
                        future.set_result(cached_existing)
                else:
                    err = RuntimeError(f"No audio file produced for track '{title}' ({val_id})")
                    if not future.done():
                        future.set_exception(err)
        except Exception as e:
            logger.error(f"Failed to cache track {val_id}: {e}")
            if not future.done():
                future.set_exception(e)
        finally:
            with _download_lock:
                _active_download_futures.pop(val_id, None)
                _active_downloads.discard(val_id)

    if blocking:
        _worker()
        return None

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t

