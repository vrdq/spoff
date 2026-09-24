import re
import time
import logging
import threading
import json
from urllib.parse import urlsplit
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Callable, cast
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import yt_dlp
try:
    from . import storage
    from .matching import _seconds, _matches_recording, _normalized_name
    from .storage import CACHE_DIR as CACHE_DIR, register_cached_track, get_cached_track_path, validate_track_id, CACHE_EXTENSIONS
except ImportError:
    import storage  # type: ignore
    from matching import _seconds, _matches_recording, _normalized_name
    from storage import CACHE_DIR as CACHE_DIR, register_cached_track, get_cached_track_path, validate_track_id, CACHE_EXTENSIONS  # type: ignore

import tempfile
import subprocess

logger = logging.getLogger("streamer")
_active_downloads = set()
_active_download_futures: Dict[str, Future] = {}
_download_lock = threading.Lock()
_stream_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_stream_cache_lock = threading.Lock()
STREAM_CACHE_TTL = 7200.0  # 2 hours

def invalidate_stream_cache(track_title: str, artist: str, direct_url: Optional[str] = None):
    t_clean = str(track_title or "").strip().lower()
    a_clean = str(artist or "").strip().lower()
    cache_key = f"{t_clean}::{a_clean}"
    if direct_url:
        cache_key = f"{direct_url}::{cache_key}"
    with _stream_cache_lock:
        _stream_cache.pop(cache_key, None)


_download_slots = threading.BoundedSemaphore(4)

def get_base_ydl_opts(extra_opts=None):
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch1:",
        "extract_flat": False,
        "socket_timeout": 10,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 1,
    }
    if extra_opts:
        opts.update(extra_opts)
    return opts

def cached_audio_matches_duration(path: Path, duration_ms: Any) -> bool:
    """Probe old cache entries; unknown duration is not evidence of a mismatch."""
    expected = _seconds(duration_ms) / 1000
    if not expected:
        return True
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=5,
        )
        actual = _seconds(json.loads(result.stdout).get("format", {}).get("duration"))
        return not actual or abs(actual - expected) <= max(8.0, expected * 0.04)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        logger.warning("Could not verify cached audio duration: %s", path)
        return True


def _same_recording_other_uploader(ytm: Any, track_title: str, duration: float) -> list:
    """Same title and near-identical length under any uploader.

    Spotify-only artist names are often distributor re-uploads of audio that
    exists on YouTube under another name. A length within 2 s together with an
    identical title is strong evidence it is the same recording.
    """
    wanted = _normalized_name(track_title)
    found = []
    for flt in ("songs", "videos"):
        for match in ytm.search(track_title, filter=flt, limit=15) or []:
            length = _seconds(match.get("duration_seconds") or match.get("duration"))
            if (match.get("videoId") and length and abs(length - duration) <= 2.0
                    and _normalized_name(match.get("title")) == wanted):
                query = (f"https://www.youtube.com/watch?v={match['videoId']}", True)
                if query not in found:
                    found.append(query)
        if found:
            break
    return found


def _ytmusic_match_queries(track_title: str, clean_artist: str, duration: float) -> list:
    """Verified YouTube URLs for a recording, found through YouTube Music search.

    Shared by playback and the search-result check so both agree on what is
    playable. Returns (url, True) tuples, best first.
    """
    queries: list = []
    try:
        try:
            from .ytmusic import get_ytmusic_client
        except ImportError:
            from ytmusic import get_ytmusic_client
        ytm = get_ytmusic_client()
        if not ytm:
            return queries
        performers = dict.fromkeys((clean_artist, clean_artist.split(",")[0].strip()))
        for flt in ("songs", "videos"):
            for performer in performers:
                for match in ytm.search(f"{performer} {track_title}", filter=flt, limit=10) or []:
                    if match.get("videoId") and _matches_recording(match, track_title, clean_artist, duration):
                        query = (f"https://www.youtube.com/watch?v={match['videoId']}", True)
                        if query not in queries:
                            queries.append(query)
                if queries:
                    return queries
        if duration:
            queries.extend(_same_recording_other_uploader(ytm, track_title, duration))
    except Exception:
        logger.debug("YTMusic song match lookup failed", exc_info=True)
    return queries


_playable_cache: Dict[str, bool] = {}
_playable_cache_lock = threading.Lock()


def is_on_youtube(track_title: str, artist: str, expected_duration_ms: Any = None) -> bool:
    """Cheap check (no audio download) that a recording can be matched on YouTube."""
    title = str(track_title or "").strip()
    clean_artist = str(artist or "").strip()
    if clean_artist.lower() in ("unknown artist", "unknown", "none"):
        clean_artist = ""
    if not title or not clean_artist:
        return False
    duration = _seconds(expected_duration_ms) / 1000
    key = f"{title.lower()}::{clean_artist.lower()}::{int(duration)}"
    with _playable_cache_lock:
        if key in _playable_cache:
            return _playable_cache[key]
    found = bool(_ytmusic_match_queries(title, clean_artist, duration))
    if not found:
        # Same last resort as playback: a plain YouTube search, checked strictly.
        primary = clean_artist.split(",")[0].strip()
        opts = get_base_ydl_opts({"extract_flat": True})
        try:
            with yt_dlp.YoutubeDL(cast(Any, opts)) as ydl:
                res = ydl.extract_info(f"ytsearch5:{primary} - {title} official audio", download=False) or {}
            found = any(isinstance(e, dict) and _matches_recording(e, title, clean_artist, duration)
                        for e in res.get("entries") or [])
        except Exception:
            logger.debug("YouTube search check failed for %s", title, exc_info=True)
            return True  # a network error is not proof it's missing; don't hide it
    with _playable_cache_lock:
        _playable_cache[key] = found
    return found


def search_and_resolve_stream(track_title: str, artist: str, direct_url: Optional[str] = None,
                              expected_duration_ms: Any = None) -> Optional[Dict[str, Any]]:
    track_title = str(track_title or "").strip()
    artist = str(artist or "").strip()
    if direct_url:
        if not isinstance(direct_url, str):
            return None
        try:
            urlsplit(direct_url)
        except ValueError:
            return None
    duration = _seconds(expected_duration_ms) / 1000
    cache_key = f"{track_title.lower()}::{artist.lower()}"
    if direct_url:
        cache_key = f"{direct_url}::{cache_key}"
    with _stream_cache_lock:
        cached_entry = _stream_cache.get(cache_key)
        if cached_entry is not None:
            cached_data, cached_ts = cached_entry
            if time.time() - cached_ts < STREAM_CACHE_TTL and cached_data.get("expected_duration", 0) == duration:
                return cached_data

    def is_spotify(url):
        host = (urlsplit(url).hostname or "").lower()
        return url.startswith("spotify:") or host == "spotify.com" or host.endswith(".spotify.com")

    exact_url = None
    if direct_url and re.fullmatch(r"[a-zA-Z0-9_-]{11}", direct_url):
        exact_url = f"https://www.youtube.com/watch?v={direct_url}"
    elif direct_url and direct_url.startswith(("http://", "https://")) and not is_spotify(direct_url):
        exact_url = direct_url
    elif not direct_url and track_title.startswith(("http://", "https://")) and not is_spotify(track_title):
        exact_url = track_title

    clean_artist = "" if artist.lower() in ("unknown artist", "unknown", "none", "") else artist
    queries = []
    if exact_url:
        # A chosen recording is authoritative. Failure must not select another song.
        queries.append((exact_url, True))
    else:
        if not track_title or not clean_artist:
            logger.warning("Insufficient metadata to match recording: %s / %s", track_title, artist)
            return None
        queries.extend(_ytmusic_match_queries(track_title, clean_artist, duration))
        primary_artist = clean_artist.split(",")[0].strip()
        queries.append((f"ytsearch5:{primary_artist} - {track_title} official audio", False))

    try:
        with yt_dlp.YoutubeDL(cast(Any, get_base_ydl_opts())) as ydl:
            item = None
            for query, identity_verified in queries:
                try:
                    res = ydl.extract_info(query, download=False)
                    if not res:
                        continue
                    entries = res.get("entries")
                    candidates = entries if entries is not None else [res]
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        if not identity_verified and not _matches_recording(candidate, track_title, clean_artist, duration):
                            continue
                        actual_duration = _seconds(candidate.get("duration"))
                        if not exact_url and duration and actual_duration and abs(actual_duration - duration) > max(8.0, duration * 0.04):
                            continue
                        if not (candidate.get("url") or candidate.get("formats") or candidate.get("webpage_url")):
                            continue
                        item = candidate
                        break
                    if item:
                        break
                except Exception:
                    logger.debug("Stream extraction failed for %s", query, exc_info=True)
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
                "expected_duration": duration,
                "resolved_title": item.get("title"),
                "duration": item.get("duration", 0),
                "webpage_url": item.get("webpage_url"),
                "thumbnail": item.get("thumbnail"),
                "ext": item.get("ext", "m4a")
            }
            with _stream_cache_lock:
                if len(_stream_cache) > 500:
                    _stream_cache.clear()
                _stream_cache[cache_key] = (stream_data, time.time())
            return stream_data
    except Exception as e:
        logger.error(f"Error resolving stream for {track_title} {artist}: {e}")
        return None

def _run_download_process(
    val_id: str,
    title: str,
    artist: str,
    direct_url: Optional[str] = None,
    track_meta: Optional[Dict[str, Any]] = None,
) -> Path:
    cached = get_cached_track_path(val_id)
    if cached and cached_audio_matches_duration(cached, (track_meta or {}).get("duration_ms")):
        register_cached_track(val_id, track_meta or {"title": title, "artist": artist}, cached)
        return cached
    resolved = search_and_resolve_stream(
        title, artist, direct_url=direct_url,
        expected_duration_ms=(track_meta or {}).get("duration_ms"),
    )
    if not resolved or not resolved.get("stream_url"):
        raise RuntimeError(f"Could not resolve audio for {title}")
    query = resolved.get("webpage_url") or resolved["stream_url"]

    cache_dir = getattr(storage, "_get_cache_dir", lambda: storage.CACHE_DIR)()
    with tempfile.TemporaryDirectory(prefix=f".{val_id}-", dir=cache_dir) as stage:
        opts = get_base_ydl_opts({
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(Path(stage) / "audio.%(ext)s"),
            "overwrites": True,
        })
        try:
            with yt_dlp.YoutubeDL(cast(Any, opts)) as ydl:
                if ydl.download([query]) != 0:
                    invalidate_stream_cache(title, artist, direct_url=direct_url)
                    raise RuntimeError("Audio download failed")
        except Exception:
            invalidate_stream_cache(title, artist, direct_url=direct_url)
            raise
        candidates = [
            p for p in Path(stage).iterdir()
            if p.suffix.lower() in CACHE_EXTENSIONS
            and p.is_file() and p.stat().st_size > 0
        ]
        if len(candidates) != 1:
            raise RuntimeError("Expected one completed audio file")
        downloaded = candidates[0]
        try:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-xerror", "-i", str(downloaded), "-f", "null", "-"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=120
            )
        except FileNotFoundError:
            logger.warning("ffmpeg not found in PATH; skipping audio integrity validation")
        if not cached_audio_matches_duration(downloaded, (track_meta or {}).get("duration_ms")):
            invalidate_stream_cache(title, artist, direct_url=direct_url)
            raise RuntimeError("Downloaded recording does not match the requested duration")
        final_path = cache_dir / f"{val_id}{downloaded.suffix.lower()}"
        downloaded.replace(final_path)
        # An older file in a preferred extension must not shadow the replacement.
        superseded = [cache_dir / f"{val_id}{ext}" for ext in CACHE_EXTENSIONS
                      if ext != downloaded.suffix.lower() and (cache_dir / f"{val_id}{ext}").is_file()]
        for old in superseded:
            # The new file replaces it; archiving into .replaced-* dirs leaked disk forever.
            old.unlink(missing_ok=True)
        meta_to_save = dict(track_meta) if track_meta else {"title": title, "artist": artist}
        meta_to_save["resolved_url"] = resolved.get("webpage_url")
        meta_to_save["resolved_title"] = resolved.get("resolved_title")
        register_cached_track(val_id, meta_to_save, final_path)
        return final_path


def download_track_to_cache(
    track_id: str,
    title: str,
    artist: str,
    on_complete: Optional[Callable[[Path], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    blocking: bool = False,
    direct_url: Optional[str] = None,
    track_meta: Optional[Dict[str, Any]] = None,
) -> Optional[threading.Thread]:
    """
    Downloads track to local disk cache. If blocking is True, runs synchronously;
    otherwise spawns a daemon background thread.
    """
    def report_error(exc):
        if on_error:
            try:
                on_error(exc)
            except Exception:
                logger.exception("Download error callback failed for %s", track_id)
        else:
            logger.error("Download failed for %s: %s", track_id, exc)

    try:
        val_id = validate_track_id(track_id)
    except ValueError as exc:
        report_error(exc)
        return None

    cached_path = get_cached_track_path(val_id)
    if cached_path and not _seconds((track_meta or {}).get("duration_ms")):
        meta_to_save = dict(track_meta) if track_meta else {"title": title, "artist": artist}
        try:
            register_cached_track(val_id, meta_to_save, cached_path)
            if on_complete:
                try:
                    on_complete(cached_path)
                except Exception:
                    logger.exception("Download completion callback failed for %s", val_id)
        except Exception as exc:
            report_error(exc)
        return None

    # Duration validation runs in the worker because probing audio may block.
    acquired_slot = False
    busy_error = None
    # Blocking callers (bulk playlist download) wait for a free slot instead of
    # failing the track just because four single downloads are running. The
    # wait happens outside _download_lock, which workers need to release slots.
    pre_acquired = _download_slots.acquire(timeout=600) if blocking else False
    with _download_lock:
        existing_future = _active_download_futures.get(val_id)
        if existing_future is not None or val_id in _active_downloads:
            if existing_future is None:
                existing_future = Future()
                _active_download_futures[val_id] = existing_future
                existing_future.set_exception(RuntimeError(f"Track {val_id} is already being downloaded"))
            future = existing_future
            is_new = False
            if pre_acquired:
                _download_slots.release()
        else:
            if not (pre_acquired or _download_slots.acquire(blocking=False)):
                busy_error = RuntimeError("Four downloads are already active")
            else:
                future = Future()
                _active_download_futures[val_id] = future
                _active_downloads.add(val_id)
                is_new = True
                acquired_slot = True

    if busy_error is not None:
        report_error(busy_error)
        return None

    def _deliver(fut: Future):
        try:
            res_p = fut.result()
        except Exception as exc:
            report_error(exc)
            return
        if on_complete:
            try:
                on_complete(res_p)
            except Exception:
                # A UI notification failure does not make downloaded audio fail.
                logger.exception("Download completion callback failed for %s", val_id)

    if not is_new:
        if blocking:
            try:
                future.result(timeout=300)
            except FutureTimeoutError as exc:
                if not future.done():
                    report_error(exc)
                    return None
            except Exception:
                pass  # _deliver handles the completed failure exactly once.
            _deliver(future)
            return None
        else:
            future.add_done_callback(_deliver)
            return None

    future.add_done_callback(_deliver)

    def _worker():
        result = None
        error = None
        try:
            result = _run_download_process(
                val_id, title, artist, direct_url=direct_url, track_meta=track_meta
            )
        except Exception as exc:
            logger.error("Failed to cache track %s: %s", val_id, exc)
            error = exc
        finally:
            # Future callbacks run synchronously. Retire this job before invoking
            # them so a completion/error callback can immediately start another.
            with _download_lock:
                _active_download_futures.pop(val_id, None)
                _active_downloads.discard(val_id)
            if acquired_slot:
                _download_slots.release()
        if not future.done():
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

    if blocking:
        _worker()
        return None

    try:
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t
    except Exception as e:
        with _download_lock:
            _active_download_futures.pop(val_id, None)
            _active_downloads.discard(val_id)
        if acquired_slot:
            _download_slots.release()
        if not future.done():
            future.set_exception(e)
        logger.exception("Could not start download worker for %s", val_id)
        return None
