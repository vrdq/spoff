import re
import logging
import urllib.parse
import urllib.request
import json
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

try:
    from ytmusicapi import YTMusic
except ImportError:
    YTMusic = None

try:
    from .storage import DATA_DIR
except ImportError:
    try:
        from storage import DATA_DIR
    except ImportError:
        DATA_DIR = Path.home() / ".local" / "share" / "spoff"

logger = logging.getLogger("ytmusic")

_ytmusic_client: Optional[Any] = None

def get_ytmusic_client() -> Optional[Any]:
    """Returns a cached YTMusic instance, authenticated if ytmusic_auth.json exists."""
    global _ytmusic_client
    if _ytmusic_client is not None:
        return _ytmusic_client

    if YTMusic is None:
        logger.warning("ytmusicapi is not installed.")
        return None

    auth_file = DATA_DIR / "ytmusic_auth.json"
    try:
        if auth_file.exists():
            _ytmusic_client = YTMusic(auth=str(auth_file))
            logger.info(f"Initialized authenticated YTMusic client from {auth_file}")
        else:
            _ytmusic_client = YTMusic()
            logger.info("Initialized unauthenticated YTMusic client")
    except Exception as e:
        logger.warning(f"Failed to initialize YTMusic with auth ({e}), falling back to unauthenticated.")
        try:
            _ytmusic_client = YTMusic()
        except Exception as e2:
            logger.error(f"Failed to initialize YTMusic client: {e2}")
            _ytmusic_client = None

    return _ytmusic_client


def parse_ytmusic_url(url_or_id: str) -> Optional[Tuple[str, str]]:
    """
    Extracts type ('playlist', 'album', 'track') and ID from a YouTube / YouTube Music URL.
    Returns (item_type, item_id) or None.
    """
    u = url_or_id.strip()
    if not u:
        return None

    # Plain playlist ID
    if u.startswith("PL") and len(u) >= 16 and re.match(r'^[a-zA-Z0-9_-]+$', u):
        return ("playlist", u)

    # Plain album browse ID
    if u.startswith("MPREb_") and re.match(r'^[a-zA-Z0-9_-]+$', u):
        return ("album", u)

    # Plain video ID
    if len(u) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', u):
        return ("track", u)

    if not (u.startswith("http://") or u.startswith("https://")):
        if any(d in u.lower() for d in ("youtube.com", "youtu.be")):
            u = f"https://{u}"

    try:
        parsed = urllib.parse.urlparse(u)
    except Exception:
        return None

    netloc = parsed.netloc.lower()
    if not any(d in netloc for d in ("youtube.com", "youtu.be")):
        return None

    qs = urllib.parse.parse_qs(parsed.query)
    list_id = qs.get("list", [None])[0]

    if list_id:
        if list_id.startswith("OLAK5uy_"):
            return ("album", list_id)
        return ("playlist", list_id)

    m_browse = re.search(r'browse/(MPREb_[a-zA-Z0-9_-]+)', parsed.path)
    if m_browse:
        return ("album", m_browse.group(1))

    v_id = qs.get("v", [None])[0]
    if v_id:
        return ("track", v_id)

    if "youtu.be" in netloc:
        path = parsed.path.strip("/")
        if path and len(path) == 11:
            return ("track", path)

    m_watch = re.search(r'(?:embed|v|shorts)/([a-zA-Z0-9_-]{11})', parsed.path)
    if m_watch:
        return ("track", m_watch.group(1))

    return None


def parse_duration_str(dur_str: str) -> int:
    """Converts 'MM:SS' or 'HH:MM:SS' to milliseconds."""
    if not dur_str:
        return 0
    try:
        parts = [int(p) for p in str(dur_str).strip().split(":")]
        if len(parts) == 1:
            return parts[0] * 1000
        elif len(parts) == 2:
            return (parts[0] * 60 + parts[1]) * 1000
        elif len(parts) == 3:
            return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    except Exception:
        pass
    return 0


def fetch_ytmusic_playlist(playlist_id_or_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches full playlist metadata and tracks from YouTube Music / YouTube.
    """
    parsed = parse_ytmusic_url(playlist_id_or_url)
    if parsed and parsed[0] == "playlist":
        p_id = parsed[1]
    else:
        p_id = playlist_id_or_url.strip()

    ytm = get_ytmusic_client()
    if ytm:
        try:
            data = ytm.get_playlist(p_id, limit=None)
            if data:
                tracks: List[Dict[str, Any]] = []
                for item in data.get("tracks", []):
                    if not item or not item.get("videoId"):
                        continue
                    v_id = item["videoId"]
                    artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
                    if not artists:
                        author_info = item.get("author") or {}
                        if isinstance(author_info, dict):
                            artists = author_info.get("name", "")
                        elif isinstance(author_info, str):
                            artists = author_info
                    if not artists:
                        artists = "Unknown Artist"

                    dur_ms = parse_duration_str(item.get("duration", ""))
                    if not dur_ms and item.get("duration_seconds"):
                        dur_ms = int(item["duration_seconds"]) * 1000

                    tracks.append({
                        "id": v_id,
                        "title": item.get("title") or "Unknown Title",
                        "artist": artists,
                        "duration_ms": dur_ms,
                        "url": f"https://www.youtube.com/watch?v={v_id}",
                        "source": "ytmusic",
                    })

                name = data.get("title") or "YouTube Music Playlist"
                author_data = data.get("author")
                author_name = author_data.get("name") if isinstance(author_data, dict) else (author_data or "")

                return {
                    "id": p_id,
                    "name": name,
                    "author": author_name,
                    "description": data.get("description") or "",
                    "tracks": tracks
                }
        except Exception as e:
            logger.warning(f"ytmusicapi failed to fetch playlist {p_id}: {e}. Trying yt-dlp fallback.")

    # Fallback to yt-dlp flat extraction
    try:
        import yt_dlp
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            full_url = f"https://www.youtube.com/playlist?list={p_id}"
            res = ydl.extract_info(full_url, download=False)
            if res:
                tracks = []
                for e in res.get("entries", []):
                    if not e or not e.get("id"):
                        continue
                    v_id = e["id"]
                    uploader = e.get("uploader") or e.get("channel") or "Unknown Artist"
                    uploader = re.sub(r'(?i)\s*-\s*topic$', '', uploader).strip()
                    raw_title = e.get("title") or "Unknown Track"
                    cleaned_title = re.sub(r'(?i)\s*[\(\[](official\s*(video|audio|lyric|music\s*video)|lyrics|audio|hd|4k)[\)\]]', '', raw_title).strip()
                    tracks.append({
                        "id": v_id,
                        "title": cleaned_title if cleaned_title else raw_title,
                        "artist": uploader,
                        "duration_ms": int((e.get("duration") or 0) * 1000),
                        "url": f"https://www.youtube.com/watch?v={v_id}",
                        "source": "ytmusic",
                    })
                return {
                    "id": p_id,
                    "name": res.get("title") or "YouTube Playlist",
                    "description": res.get("description") or "",
                    "tracks": tracks
                }
    except Exception as e2:
        logger.error(f"yt-dlp playlist fallback failed for {p_id}: {e2}")

    return None


def fetch_ytmusic_album(album_id_or_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches full album metadata and tracks from YouTube Music.
    Supports both browseId ('MPREb_...') and album playlistId ('OLAK5uy_...').
    """
    parsed = parse_ytmusic_url(album_id_or_url)
    if parsed and parsed[0] == "album":
        a_id = parsed[1]
    else:
        a_id = album_id_or_url.strip()

    ytm = get_ytmusic_client()
    if not ytm:
        return None

    browse_id = a_id
    if a_id.startswith("OLAK5uy_"):
        try:
            resolved = ytm.get_album_browse_id(a_id)
            if resolved:
                browse_id = resolved
        except Exception as e:
            logger.debug(f"Failed to resolve OLAK5uy browseId: {e}")

    if not browse_id.startswith("MPRE"):
        return fetch_ytmusic_playlist(a_id)

    try:
        data = ytm.get_album(browse_id)
        if data:
            tracks: List[Dict[str, Any]] = []
            album_artist = ", ".join(a.get("name", "") for a in data.get("artists", []) if a.get("name")) or "Unknown Artist"

            for item in data.get("tracks", []):
                if not item or not item.get("videoId"):
                    continue
                v_id = item["videoId"]
                track_artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name")) or album_artist
                dur_ms = parse_duration_str(item.get("duration", ""))
                if not dur_ms and item.get("duration_seconds"):
                    dur_ms = int(item["duration_seconds"]) * 1000

                tracks.append({
                    "id": v_id,
                    "title": item.get("title") or "Unknown Title",
                    "artist": track_artists,
                    "duration_ms": dur_ms,
                    "url": f"https://www.youtube.com/watch?v={v_id}",
                    "source": "ytmusic",
                })

            return {
                "id": browse_id,
                "name": data.get("title") or "YouTube Music Album",
                "artist": album_artist,
                "year": data.get("year"),
                "tracks": tracks
            }
    except Exception as e:
        logger.error(f"Failed to fetch YouTube Music album {browse_id}: {e}")

    return None


def fetch_ytmusic_track(video_id_or_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches details for a single YouTube / YouTube Music track.
    """
    parsed = parse_ytmusic_url(video_id_or_url)
    if parsed and parsed[0] == "track":
        v_id = parsed[1]
    else:
        v_id = video_id_or_url.strip()

    ytm = get_ytmusic_client()
    if ytm:
        try:
            data = ytm.get_song(v_id)
            if data and data.get("videoDetails"):
                vd = data["videoDetails"]
                dur_s = int(vd.get("lengthSeconds") or 0)
                return {
                    "id": v_id,
                    "title": vd.get("title") or "Unknown Title",
                    "artist": vd.get("author") or "Unknown Artist",
                    "duration_ms": dur_s * 1000,
                    "url": f"https://www.youtube.com/watch?v={v_id}",
                    "source": "ytmusic",
                }
        except Exception as e:
            logger.debug(f"ytmusicapi get_song failed for {v_id}: {e}")

    # Fallback to yt-dlp
    try:
        import yt_dlp
        ydl_opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"https://www.youtube.com/watch?v={v_id}", download=False)
            if res:
                return {
                    "id": v_id,
                    "title": res.get("title") or "Unknown Title",
                    "artist": res.get("uploader") or res.get("channel") or "Unknown Artist",
                    "duration_ms": int((res.get("duration") or 0) * 1000),
                    "url": f"https://www.youtube.com/watch?v={v_id}",
                    "source": "ytmusic",
                }
    except Exception as e2:
        logger.error(f"yt-dlp track fallback failed for {v_id}: {e2}")

    return None


def search_ytmusic_tracks(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    """
    Searches YouTube Music for tracks with clean, studio-accurate metadata.
    Falls back gracefully to yt-dlp if needed.
    """
    q = query.strip()
    if not q:
        return []

    tracks: List[Dict[str, Any]] = []
    ytm = get_ytmusic_client()

    if ytm:
        try:
            results = ytm.search(q, filter="songs", limit=limit)
            for r in results:
                if not r or not r.get("videoId"):
                    continue
                v_id = r["videoId"]
                artists = ", ".join(a.get("name", "") for a in r.get("artists", []) if a.get("name"))
                if not artists:
                    artists = "Unknown Artist"

                dur_str = r.get("duration") or ""
                dur_ms = parse_duration_str(dur_str)
                if not dur_ms and r.get("duration_seconds"):
                    dur_ms = int(r["duration_seconds"]) * 1000

                tracks.append({
                    "id": v_id,
                    "title": r.get("title") or "Unknown Title",
                    "artist": artists,
                    "duration_ms": dur_ms,
                    "url": f"https://www.youtube.com/watch?v={v_id}",
                    "source": "ytmusic",
                    "album": r.get("album", {}).get("name") if r.get("album") else None
                })

            if tracks:
                return tracks[:limit]
        except Exception as e:
            logger.warning(f"ytmusicapi song search failed for '{q}': {e}. Using fallback.")

    # Fallback search
    try:
        import yt_dlp
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
            entries = res.get("entries", [])
            for e in entries:
                if not e or not e.get("id"):
                    continue
                t_id = e["id"]
                title = e.get("title") or "Unknown Track"
                uploader = e.get("uploader") or e.get("channel") or "Unknown Artist"
                uploader = re.sub(r'(?i)\s*-\s*topic$', '', uploader).strip()
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
                    "url": f"https://www.youtube.com/watch?v={t_id}",
                    "source": "ytmusic"
                })
    except Exception as e2:
        logger.error(f"Fallback search failed for '{q}': {e2}")

    return tracks[:limit]


def get_ytmusic_search_suggestions(query: str) -> List[str]:
    """Real-time autocomplete query suggestions for YouTube Music."""
    q = query.strip()
    if not q:
        return []
    try:
        encoded = urllib.parse.quote(q)
        url = f"https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&q={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            content = resp.read().decode("utf-8")
        match = re.search(r'\((\[.*\])\)', content)
        if match:
            data = json.loads(match.group(1))
            return [item[0] for item in data[1][:6]]
    except Exception as e:
        logger.debug(f"YTM suggestions failed: {e}")
    return []
