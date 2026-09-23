import json
import re
import logging
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional, cast
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
        "socket_timeout": 10,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 1,
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
                
                thumb_url = e.get("thumbnail") or (f"https://img.youtube.com/vi/{t_id}/hqdefault.jpg" if t_id else None)
                tracks.append({
                    "id": t_id,
                    "title": cleaned_title if cleaned_title else title,
                    "artist": uploader,
                    "duration_ms": int((e.get("duration") or 0) * 1000),
                    "url": e.get("url") or f"https://www.youtube.com/watch?v={t_id}",
                    "source": "ytmusic",
                    "art_url": thumb_url,
                    "thumbnail": thumb_url,
                })
    except Exception as e:
        logger.error(f"Live search failed for '{query}': {e}")
        
    return tracks


def resolve_direct_track_url(query: str) -> Optional[Dict[str, Any]]:
    """
    Detects and resolves direct track URLs or URIs (Spotify, YouTube, YouTube Music, or raw media).
    Returns a unified track dict if matched and resolved, otherwise None.
    """
    raw_query = (query or "").strip()
    if not raw_query:
        return None

    # 1. Spotify track URL / URI
    sp_match = re.search(r'(?:spotify:track:|open\.spotify\.com/(?:[a-zA-Z-]+/)?track/)([a-zA-Z0-9]{22})', raw_query)
    if sp_match:
        sp_id = sp_match.group(1)
        # Try fetching full metadata via authenticated Spotify API
        try:
            from .auth import spotify_api_request, get_valid_token
        except ImportError:
            try:
                from auth import spotify_api_request, get_valid_token
            except ImportError:
                spotify_api_request = None
                get_valid_token = None

        if spotify_api_request and get_valid_token:
            token = get_valid_token()
            if token:
                try:
                    ok, data, _ = spotify_api_request(f"/tracks/{sp_id}", method="GET", token=token)
                    if ok and data and isinstance(data, dict):
                        images = data.get("album", {}).get("images", [])
                        art_url = images[0].get("url") if images and isinstance(images[0], dict) else None
                        artists = ", ".join(a.get("name", "Unknown") for a in data.get("artists", []))
                        return {
                            "id": sp_id,
                            "title": data.get("name") or "Unknown Track",
                            "artist": artists or "Unknown Artist",
                            "duration_ms": data.get("duration_ms", 0),
                            "uri": data.get("uri") or f"spotify:track:{sp_id}",
                            "album": data.get("album", {}).get("name", ""),
                            "art_url": art_url,
                            "thumbnail": art_url,
                            "url": f"https://open.spotify.com/track/{sp_id}",
                            "source": "spotify"
                        }
                except Exception as e:
                    logger.debug(f"Failed to fetch Spotify track {sp_id} from API: {e}")

        # The embed page includes artist/duration; oEmbed may contain only a title.
        try:
            try:
                from .spotify import fetch_spotify_track
            except ImportError:
                from spotify import fetch_spotify_track
            embedded = fetch_spotify_track(sp_id)
            if embedded and embedded.get("artist") not in (None, "", "Unknown Artist"):
                return embedded
        except Exception:
            logger.debug("Spotify embed metadata lookup failed for %s", sp_id, exc_info=True)

        # Fallback to Spotify oEmbed
        try:
            oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{sp_id}"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                oembed_data = json.loads(resp.read().decode("utf-8"))
                title = oembed_data.get("title") or "Spotify Track"
                art_url = oembed_data.get("thumbnail_url")
                artist = "Unknown Artist"
                # oEmbed titles are track names, not an artist/title protocol.
                return {
                    "id": sp_id,
                    "title": title,
                    "artist": artist,
                    "duration_ms": 0,
                    "uri": f"spotify:track:{sp_id}",
                    "album": "",
                    "art_url": art_url,
                    "thumbnail": art_url,
                    "url": f"https://open.spotify.com/track/{sp_id}",
                    "source": "spotify"
                }
        except Exception as e:
            logger.debug(f"Spotify oEmbed fallback failed for {sp_id}: {e}")

    # 2. YouTube / YouTube Music URL
    try:
        parsed = urllib.parse.urlsplit(raw_query)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    v_id = None
    if host in ("youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            v_id = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        else:
            path_match = re.fullmatch(r"/(?:shorts|embed|live)/([a-zA-Z0-9_-]{11})/?", parsed.path)
            if path_match:
                v_id = path_match.group(1)
    elif host == "youtu.be":
        v_id = parsed.path.strip("/")
    if v_id and re.fullmatch(r"[a-zA-Z0-9_-]{11}", v_id):
        # Try ytmusicapi first
        try:
            from .ytmusic import get_ytmusic_client
        except ImportError:
            try:
                from ytmusic import get_ytmusic_client
            except ImportError:
                get_ytmusic_client = None

        if get_ytmusic_client:
            try:
                ytm = get_ytmusic_client()
                if ytm:
                    song_dict = ytm.get_song(v_id)
                    vd = song_dict.get("videoDetails", {}) if song_dict else {}
                    if vd:
                        title = vd.get("title") or "YouTube Track"
                        author = vd.get("author") or "Unknown Artist"
                        dur_s = int(vd.get("lengthSeconds") or 0)
                        thumbs = vd.get("thumbnail", {}).get("thumbnails", [])
                        art_url = thumbs[-1].get("url") if thumbs else f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg"
                        return {
                            "id": v_id,
                            "title": title,
                            "artist": author,
                            "duration_ms": dur_s * 1000,
                            "album": "",
                            "art_url": art_url,
                            "thumbnail": art_url,
                            "url": f"https://www.youtube.com/watch?v={v_id}",
                            "source": "ytmusic"
                        }
            except Exception as e:
                logger.debug(f"ytmusic get_song failed for {v_id}: {e}")

        # Fallback to yt_dlp
        try:
            ydl_opts = {
                "extract_flat": True,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "socket_timeout": 8,
            }
            with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={v_id}", download=False)
                if info:
                    title = info.get("title") or "YouTube Track"
                    uploader = info.get("uploader") or info.get("channel") or "Unknown Artist"
                    dur_s = int(info.get("duration") or 0)
                    art_url = info.get("thumbnail") or f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg"
                    return {
                        "id": v_id,
                        "title": title,
                        "artist": uploader,
                        "duration_ms": dur_s * 1000,
                        "album": "",
                        "art_url": art_url,
                        "thumbnail": art_url,
                        "url": f"https://www.youtube.com/watch?v={v_id}",
                        "source": "ytmusic"
                    }
        except Exception as e:
            logger.debug(f"yt_dlp fallback failed for {v_id}: {e}")

    # 3. Generic web audio/video URL (e.g. SoundCloud, direct media link, etc.)
    if raw_query.startswith("http://") or raw_query.startswith("https://"):
        try:
            ydl_opts = {
                "extract_flat": True,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "socket_timeout": 8,
            }
            with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
                info = ydl.extract_info(raw_query, download=False)
                if info:
                    t_id = info.get("id") or raw_query
                    title = info.get("title") or "Web Track"
                    uploader = info.get("uploader") or info.get("creator") or info.get("artist") or "Unknown Artist"
                    dur_s = int(info.get("duration") or 0)
                    art_url = info.get("thumbnail")
                    return {
                        "id": t_id,
                        "title": title,
                        "artist": uploader,
                        "duration_ms": dur_s * 1000,
                        "album": "",
                        "art_url": art_url,
                        "thumbnail": art_url,
                        "url": raw_query,
                        "source": "ytmusic"
                    }
        except Exception as e:
            logger.debug(f"Generic URL extract failed for {raw_query}: {e}")

    return None
