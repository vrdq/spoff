import json
import re
import urllib.request
import urllib.parse
from typing import List, Dict, Optional, Any

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def parse_spotify_url(url_or_id: str) -> Optional[tuple[str, str]]:
    """Extracts type ('playlist', 'album', 'track') and ID from Spotify URL or URI."""
    url_or_id = url_or_id.strip()
    if url_or_id.startswith("spotify:"):
        parts = url_or_id.split(":")
        if len(parts) >= 3:
            return parts[1], parts[2]
    
    match = re.search(r'open\.spotify\.com/(?:[a-zA-Z0-9_-]+/)*(playlist|album|track)/([a-zA-Z0-9]+)', url_or_id)
    if match:
        return match.group(1), match.group(2)
        
    return None

def fetch_spotify_playlist(playlist_id_or_url: str) -> Optional[Dict[str, Any]]:
    """Fetches playlist details and tracks without Spotify credentials."""
    parsed = parse_spotify_url(playlist_id_or_url)
    if parsed:
        _, p_id = parsed
    else:
        p_id = playlist_id_or_url.strip()

    embed_url = f"https://open.spotify.com/embed/playlist/{p_id}"
    req = urllib.request.Request(embed_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8")
    except Exception:
        return None

    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except Exception:
        return None

    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
    if not entity:
        return None

    cover_sources = entity.get("coverArt", {}).get("sources", []) if entity.get("coverArt") else []
    cover_url = cover_sources[0].get("url") if cover_sources and len(cover_sources) > 0 and isinstance(cover_sources[0], dict) else None

    tracks: List[Dict[str, Any]] = []
    for item in entity.get("trackList", []):
        tracks.append({
            "id": item.get("uri", "").replace("spotify:track:", ""),
            "title": item.get("title") or "Unknown Title",
            "artist": item.get("subtitle") or "Unknown Artist",
            "duration_ms": item.get("duration") or 0,
            "uri": item.get("uri") or "",
            "art_url": cover_url,
        })

    return {
        "id": p_id,
        "name": entity.get("name") or "Spotify Playlist",
        "description": entity.get("subtitle") or "",
        "cover_url": cover_url,
        "tracks": tracks
    }

def fetch_spotify_album(album_id_or_url: str) -> Optional[Dict[str, Any]]:
    """Fetches album details and tracks."""
    parsed = parse_spotify_url(album_id_or_url)
    if parsed:
        _, a_id = parsed
    else:
        a_id = album_id_or_url.strip()

    embed_url = f"https://open.spotify.com/embed/album/{a_id}"
    req = urllib.request.Request(embed_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8")
    except Exception:
        return None

    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except Exception:
        return None

    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
    if not entity:
        return None

    cover_sources = entity.get("coverArt", {}).get("sources", []) if entity.get("coverArt") else []
    cover_url = cover_sources[0].get("url") if cover_sources and len(cover_sources) > 0 and isinstance(cover_sources[0], dict) else None
    album_name = entity.get("name") or "Spotify Album"

    tracks: List[Dict[str, Any]] = []
    for item in entity.get("trackList", []):
        tracks.append({
            "id": item.get("uri", "").replace("spotify:track:", ""),
            "title": item.get("title") or "Unknown Title",
            "artist": item.get("subtitle") or "Unknown Artist",
            "duration_ms": item.get("duration") or 0,
            "uri": item.get("uri") or "",
            "album": album_name,
            "art_url": cover_url,
        })

    return {
        "id": a_id,
        "name": album_name,
        "description": entity.get("subtitle") or "",
        "cover_url": cover_url,
        "tracks": tracks
    }

def fetch_spotify_track(track_id_or_url: str) -> Optional[Dict[str, Any]]:
    """Fetches single track details, album cover, and artist picture."""
    parsed = parse_spotify_url(track_id_or_url)
    if parsed:
        _, t_id = parsed
    else:
        t_id = track_id_or_url.strip()

    embed_url = f"https://open.spotify.com/embed/track/{t_id}"
    req = urllib.request.Request(embed_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8")
    except Exception:
        return None

    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except Exception:
        return None

    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
    if not entity:
        return None

    imgs = entity.get("visualIdentity", {}).get("image", [])
    cover_url = None
    if imgs and isinstance(imgs, list):
        sorted_imgs = sorted(imgs, key=lambda x: int(x.get("maxWidth", 0) or x.get("maxHeight", 0)))
        cover_url = sorted_imgs[-1].get("url")

    artists = entity.get("artists", [])
    artist_name = "Unknown Artist"
    artist_pic = None
    if artists and isinstance(artists, list) and len(artists) > 0:
        artist_name = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")) or "Unknown Artist"
        first_artist = artists[0]
        if isinstance(first_artist, dict) and "uri" in first_artist:
            art_id = first_artist["uri"].split(":")[-1]
            try:
                art_req = urllib.request.Request(f"https://open.spotify.com/embed/artist/{art_id}", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(art_req, timeout=3.5) as a_resp:
                    a_html = a_resp.read().decode("utf-8")
                a_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', a_html)
                if a_match:
                    a_data = json.loads(a_match.group(1))
                    a_entity = a_data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                    a_imgs = a_entity.get("visualIdentity", {}).get("image", [])
                    if a_imgs and isinstance(a_imgs, list):
                        sorted_aimgs = sorted(a_imgs, key=lambda x: int(x.get("maxWidth", 0) or x.get("maxHeight", 0)))
                        artist_pic = sorted_aimgs[-1].get("url")
            except Exception:
                pass

    return {
        "id": t_id,
        "title": entity.get("title") or entity.get("name") or "Unknown Title",
        "artist": artist_name,
        "duration_ms": entity.get("duration") or 0,
        "uri": entity.get("uri") or f"spotify:track:{t_id}",
        "url": f"https://open.spotify.com/track/{t_id}",
        "art_url": cover_url or artist_pic,
        "artist_art_url": artist_pic or cover_url,
        "album_art_url": cover_url,
        "source": "spotify"
    }

