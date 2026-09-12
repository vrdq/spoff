import json
import re
import urllib.request
import urllib.parse
import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

try:
    from .storage import DATA_DIR
except ImportError:
    try:
        from storage import DATA_DIR
    except ImportError:
        DATA_DIR = Path.home() / ".local" / "share" / "spoff"

logger = logging.getLogger("art")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
ART_CACHE_FILE = DATA_DIR / "cache" / "art_cache.json"

_art_cache_lock = threading.Lock()
_memory_art_cache: Dict[str, Dict[str, Optional[str]]] = {}
_cache_loaded = False


def _load_disk_cache() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    try:
        if ART_CACHE_FILE.exists():
            with open(ART_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _memory_art_cache.update(data)
    except Exception as e:
        logger.debug(f"Failed to load art cache from disk: {e}")
    finally:
        _cache_loaded = True


def _save_disk_cache() -> None:
    try:
        ART_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = ART_CACHE_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(_memory_art_cache, f, indent=2)
        temp_file.replace(ART_CACHE_FILE)
    except Exception as e:
        logger.debug(f"Failed to write art cache to disk: {e}")


def _normalize_key(title: str, artist: str) -> str:
    t = re.sub(r'[\W_]+', '', title.lower().strip())
    a = re.sub(r'[\W_]+', '', artist.lower().strip())
    return f"{a}::{t}"


def get_cached_artwork(track: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Synchronously and immediately looks up cached artwork from memory or disk.
    Returns a dict with 'art_url', 'artist_art_url', 'album_art_url' or empty dict.
    """
    with _art_cache_lock:
        _load_disk_cache()

        # 1. Check ID key
        raw_id = str(track.get("id") or "").strip()
        if raw_id and raw_id in _memory_art_cache:
            return dict(_memory_art_cache[raw_id])

        # 2. Check Spotify URI / ID
        raw_uri = str(track.get("uri") or "").strip()
        if raw_uri.startswith("spotify:track:"):
            sp_id = raw_uri.split(":")[-1]
            if sp_id in _memory_art_cache:
                return dict(_memory_art_cache[sp_id])

        # 3. Check normalized artist::title
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        if title:
            norm_key = _normalize_key(title, artist)
            if norm_key in _memory_art_cache:
                return dict(_memory_art_cache[norm_key])

    return {}


def _save_to_cache(track: Dict[str, Any], art_data: Dict[str, Optional[str]]) -> None:
    with _art_cache_lock:
        _load_disk_cache()
        raw_id = str(track.get("id") or "").strip()
        if raw_id:
            _memory_art_cache[raw_id] = art_data

        raw_uri = str(track.get("uri") or "").strip()
        if raw_uri.startswith("spotify:track:"):
            sp_id = raw_uri.split(":")[-1]
            _memory_art_cache[sp_id] = art_data

        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        if title:
            norm_key = _normalize_key(title, artist)
            _memory_art_cache[norm_key] = art_data

        _save_disk_cache()


def fetch_spotify_embed_art(spotify_id: str, timeout: float = 3.5) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetches album artwork and artist image for a Spotify track via embed endpoints.
    Returns (album_cover_url, artist_picture_url).
    """
    cover_url: Optional[str] = None
    artist_url: Optional[str] = None
    artist_id: Optional[str] = None

    try:
        embed_url = f"https://open.spotify.com/embed/track/{spotify_id}"
        req = urllib.request.Request(embed_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
        if match:
            data = json.loads(match.group(1))
            entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
            imgs = entity.get("visualIdentity", {}).get("image", [])
            if imgs and isinstance(imgs, list):
                # Pick the highest resolution image
                sorted_imgs = sorted(imgs, key=lambda x: int(x.get("maxWidth", 0) or x.get("maxHeight", 0)))
                cover_url = sorted_imgs[-1].get("url")

            artists = entity.get("artists", [])
            if artists and isinstance(artists, list) and len(artists) > 0:
                first_artist = artists[0]
                if isinstance(first_artist, dict) and "uri" in first_artist:
                    artist_id = first_artist["uri"].split(":")[-1]
    except Exception as e:
        logger.debug(f"Spotify track embed fetch failed for {spotify_id}: {e}")

    # Fetch artist photo if artist_id resolved
    if artist_id:
        try:
            art_embed_url = f"https://open.spotify.com/embed/artist/{artist_id}"
            req_a = urllib.request.Request(art_embed_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_a, timeout=timeout) as resp_a:
                html_a = resp_a.read().decode("utf-8", errors="ignore")

            match_a = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_a)
            if match_a:
                data_a = json.loads(match_a.group(1))
                entity_a = data_a.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                aimgs = entity_a.get("visualIdentity", {}).get("image", [])
                if aimgs and isinstance(aimgs, list):
                    sorted_aimgs = sorted(aimgs, key=lambda x: int(x.get("maxWidth", 0) or x.get("maxHeight", 0)))
                    artist_url = sorted_aimgs[-1].get("url")
        except Exception as e2:
            logger.debug(f"Spotify artist embed fetch failed for {artist_id}: {e2}")

    return cover_url, artist_url


def fetch_deezer_art(title: str, artist: str, timeout: float = 3.5) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetches high-res album cover and artist picture from Deezer's public API.
    Returns (album_cover_url, artist_picture_url).
    """
    clean_artist = "" if artist.lower() in ("unknown artist", "unknown", "none", "") else artist.strip()
    clean_title = re.sub(r'(?i)\s*[\(\[](official\s*(video|audio|lyric|music\s*video)|lyrics|audio|hd|4k)[\)\]]', '', title).strip()
    query = f"{clean_artist} {clean_title}".strip() if clean_artist else clean_title

    cover_url: Optional[str] = None
    artist_url: Optional[str] = None

    if query:
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://api.deezer.com/search?q={encoded}&limit=3"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            items = data.get("data", [])
            if items and isinstance(items, list):
                item = items[0]
                alb = item.get("album", {})
                if alb:
                    cover_url = alb.get("cover_xl") or alb.get("cover_big") or alb.get("cover_medium")
                art = item.get("artist", {})
                if art:
                    artist_url = art.get("picture_xl") or art.get("picture_big") or art.get("picture_medium")
        except Exception as e:
            logger.debug(f"Deezer track search failed for '{query}': {e}")

    # Fallback to artist-specific search if artist_url missing
    if not artist_url and clean_artist:
        try:
            encoded_art = urllib.parse.quote(clean_artist)
            url = f"https://api.deezer.com/search/artist?q={encoded_art}&limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            items = data.get("data", [])
            if items and isinstance(items, list):
                art_item = items[0]
                artist_url = art_item.get("picture_xl") or art_item.get("picture_big") or art_item.get("picture_medium")
        except Exception as e:
            logger.debug(f"Deezer artist search failed for '{clean_artist}': {e}")

    return cover_url, artist_url


def fetch_itunes_art(title: str, artist: str, timeout: float = 3.5) -> Optional[str]:
    """
    Fetches high-res (600x600) album artwork from Apple iTunes API.
    """
    clean_artist = "" if artist.lower() in ("unknown artist", "unknown", "none", "") else artist.strip()
    query = f"{clean_artist} {title}".strip() if clean_artist else title
    if not query:
        return None

    try:
        encoded = urllib.parse.quote(query)
        url = f"https://itunes.apple.com/search?term={encoded}&entity=song&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        results = data.get("results", [])
        if results and isinstance(results, list):
            raw_art = results[0].get("artworkUrl100", "")
            if raw_art:
                return raw_art.replace("100x100bb", "600x600bb")
    except Exception as e:
        logger.debug(f"iTunes art search failed for '{query}': {e}")
    return None


def resolve_track_artwork(track: Dict[str, Any], timeout: float = 3.5) -> Dict[str, Optional[str]]:
    """
    Resolves comprehensive artwork (cover art and artist picture) for any track.
    Returns:
    {
        "art_url": str,           # Main artwork for MPRIS / display (cover or artist)
        "artist_art_url": str,    # Dedicated artist picture
        "album_art_url": str,     # Dedicated album cover
        "source": str             # Provider
    }
    """
    # 1. Fast check if track already has both
    existing_art = track.get("art_url") or track.get("thumbnail") or track.get("cover_url")
    existing_artist = track.get("artist_art_url")
    if existing_art and existing_artist:
        return {
            "art_url": str(existing_art),
            "artist_art_url": str(existing_artist),
            "album_art_url": str(existing_art),
            "source": "existing"
        }

    # 2. Check cache
    cached = get_cached_artwork(track)
    if cached.get("art_url") and cached.get("artist_art_url"):
        return cached

    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    t_id = str(track.get("id") or "").strip()
    uri = str(track.get("uri") or "").strip()

    cover_url = cached.get("album_art_url") or cached.get("art_url") or existing_art
    artist_url = cached.get("artist_art_url") or existing_artist
    provider = "cache" if (cover_url or artist_url) else "none"

    # 3. Spotify track resolution
    sp_id = None
    if uri.startswith("spotify:track:"):
        sp_id = uri.split(":")[-1]
    elif len(t_id) == 22 and t_id.isalnum() and track.get("source") == "spotify":
        sp_id = t_id

    if sp_id and (not cover_url or not artist_url):
        sp_cov, sp_art = fetch_spotify_embed_art(sp_id, timeout=timeout)
        if sp_cov and not cover_url:
            cover_url = sp_cov
            provider = "spotify"
        if sp_art and not artist_url:
            artist_url = sp_art
            provider = "spotify"

    # 4. Deezer resolution (provides both cover and artist picture)
    if (not cover_url or not artist_url) and (title or artist):
        dz_cov, dz_art = fetch_deezer_art(title, artist, timeout=timeout)
        if dz_cov and not cover_url:
            cover_url = dz_cov
            if provider == "none":
                provider = "deezer"
        if dz_art and not artist_url:
            artist_url = dz_art
            if provider == "none":
                provider = "deezer"

    # 5. iTunes resolution for cover
    if not cover_url and (title or artist):
        it_cov = fetch_itunes_art(title, artist, timeout=timeout)
        if it_cov:
            cover_url = it_cov
            if provider == "none":
                provider = "itunes"

    # 6. YouTube thumbnail fallback
    if not cover_url and len(t_id) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', t_id):
        cover_url = f"https://img.youtube.com/vi/{t_id}/hqdefault.jpg"
        if provider == "none":
            provider = "youtube"

    # If artist picture is found but no cover art, cover = artist picture
    if not cover_url and artist_url:
        cover_url = artist_url
    # If cover art is found but no artist picture, artist picture = cover art
    if not artist_url and cover_url:
        artist_url = cover_url

    main_art = cover_url or artist_url

    result: Dict[str, Optional[str]] = {
        "art_url": main_art,
        "artist_art_url": artist_url,
        "album_art_url": cover_url,
        "source": provider
    }

    if main_art or artist_url:
        _save_to_cache(track, result)

    return result
