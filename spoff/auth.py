import re
import json
import time
import base64
import hashlib
import secrets
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List, Callable, Tuple

try:
    from .storage import DATA_DIR, load_saved_playlists, save_saved_playlists
except ImportError:
    from storage import DATA_DIR, load_saved_playlists, save_saved_playlists

logger = logging.getLogger("auth")

# Pre-authorized Spotify client ID with extended quota mode
SPOTIFY_CLIENT_ID = "d420a117a32841c2b3474932e49fb54b"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8989/login"
SPOTIFY_PORT = 8989

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

SCOPES = [
    "user-read-private",
    "user-read-email",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
    "user-library-read",
    "user-library-modify",
]

AUTH_FILE = DATA_DIR / "spotify_auth.json"

SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Spoff - Spotify Login</title>
  <style>
    body {
      background-color: #131313;
      color: #e2e2e2;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }
    .card {
      background: #181818;
      border: 1px solid #2a2a2a;
      padding: 36px 44px;
      border-radius: 8px;
      text-align: center;
      max-width: 440px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    }
    h2 {
      color: #ffffff;
      margin-top: 0;
      font-size: 22px;
      letter-spacing: 0.5px;
    }
    p {
      color: #888888;
      font-size: 14px;
      line-height: 1.6;
    }
    .badge {
      display: inline-block;
      padding: 6px 14px;
      background: #1e3a24;
      color: #569f68;
      font-weight: bold;
      border-radius: 4px;
      font-size: 12px;
      margin-bottom: 16px;
      letter-spacing: 1px;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">SPOFF CONNECTED</div>
    <h2>Logged in to Spotify</h2>
    <p>Your account is now linked. You can close this browser tab and return to Spoff in your terminal.</p>
  </div>
</body>
</html>
"""

def generate_pkce_pair() -> Tuple[str, str]:
    """Generates a high-entropy URL-safe verifier and SHA256 challenge."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge

def build_auth_url(verifier: str, client_id: str = SPOTIFY_CLIENT_ID, redirect_uri: str = SPOTIFY_REDIRECT_URI) -> Tuple[str, str]:
    """Builds the authorization URL and returns (auth_url, state)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(16)
    
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state
    }
    return f"{SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}", state

class CallbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

class OAuthCallbackServer:
    """Lightweight local loopback server to capture Spotify's redirect code."""
    def __init__(self, port: int = SPOTIFY_PORT):
        self.port = port
        self.code: Optional[str] = None
        self.error: Optional[str] = None
        self.expected_state: Optional[str] = None
        self._server: Optional[CallbackHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._stopped = False
        self._lock = threading.Lock()

    def start(self, on_complete: Callable[[Optional[str], Optional[str]], None], expected_state: Optional[str] = None):
        outer = self
        self.expected_state = expected_state

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/login":
                    qs = urllib.parse.parse_qs(parsed.query)
                    supplied_state = qs.get("state", [""])[0]
                    if not outer.expected_state or not secrets.compare_digest(supplied_state, outer.expected_state):
                        self.send_response(400)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"Invalid OAuth state")
                        return

                    with outer._lock:
                        if outer.code is not None or outer.error is not None:
                            self.send_response(200)
                            self.end_headers()
                            self.wfile.write(b"OK")
                            return

                        if "code" in qs:
                            outer.code = qs["code"][0]
                            self.send_response(200)
                            self.send_header("Content-Type", "text/html; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(SUCCESS_HTML.encode("utf-8"))
                            threading.Thread(target=outer.stop, daemon=True).start()
                            on_complete(outer.code, None)
                            return
                        elif "error" in qs:
                            outer.error = qs["error"][0]
                            self.send_response(400)
                            self.send_header("Content-Type", "text/html; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(b"<html><body style='background:#131313;color:#fff;font-family:sans-serif;padding:40px;'><h2>Spotify Login Denied</h2></body></html>")
                            threading.Thread(target=outer.stop, daemon=True).start()
                            on_complete(None, outer.error)
                            return
                
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        self._server = CallbackHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"OAuth loopback server listening on 127.0.0.1:{self.port}")

    def stop(self):
        if self._server and not self._stopped:
            self._stopped = True
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:
                logger.debug(f"Error closing OAuth server: {e}")

def exchange_code_for_tokens(code: str, verifier: str, client_id: str = SPOTIFY_CLIENT_ID, redirect_uri: str = SPOTIFY_REDIRECT_URI) -> Optional[Dict[str, Any]]:
    """Exchanges an authorization code for Spotify access & refresh tokens."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier
    }).encode("utf-8")

    req = urllib.request.Request(
        SPOTIFY_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8")
            res = json.loads(body)
            expires_in = res.get("expires_in", 3600)
            res["expires_at"] = time.time() + expires_in
            return res
    except Exception as e:
        logger.error(f"Failed to exchange code for token: {e}")
        return None

def refresh_spotify_token(refresh_token: str, client_id: str = SPOTIFY_CLIENT_ID) -> Optional[Dict[str, Any]]:
    """Refreshes an expired Spotify access token using the refresh token."""
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id
    }).encode("utf-8")

    req = urllib.request.Request(
        SPOTIFY_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8")
            res = json.loads(body)
            expires_in = res.get("expires_in", 3600)
            res["expires_at"] = time.time() + expires_in
            if "refresh_token" not in res:
                res["refresh_token"] = refresh_token
            return res
    except Exception as e:
        logger.error(f"Failed to refresh Spotify token: {e}")
        return None

def load_spotify_auth() -> Optional[Dict[str, Any]]:
    """Loads saved Spotify auth session from disk."""
    if AUTH_FILE.exists() and AUTH_FILE.stat().st_size > 0:
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.error(f"Error reading {AUTH_FILE}: {e}")
    return None

def save_spotify_auth(data: Dict[str, Any]):
    """Persists Spotify auth session to disk with mode 0600."""
    try:
        from .storage import _atomic_json_dump
    except ImportError:
        from storage import _atomic_json_dump
    _atomic_json_dump(AUTH_FILE, data, mode=0o600)

def logout_spotify() -> bool:
    """Removes saved Spotify auth session."""
    if AUTH_FILE.exists():
        try:
            AUTH_FILE.unlink()
            return True
        except Exception as e:
            logger.error(f"Error removing {AUTH_FILE}: {e}")
    return False

def get_valid_token() -> Optional[str]:
    """Returns a valid, unexpired access token, auto-refreshing if necessary."""
    auth = load_spotify_auth()
    if not auth:
        return None

    access_token = auth.get("access_token")
    expires_at = auth.get("expires_at", 0)
    refresh_token = auth.get("refresh_token")

    # Refresh 60s before expiration
    if time.time() >= expires_at - 60:
        if refresh_token:
            new_tokens = refresh_spotify_token(refresh_token)
            if new_tokens:
                auth.update(new_tokens)
                save_spotify_auth(auth)
                return auth.get("access_token")
        return None

    return access_token

def spotify_api_request(
    endpoint: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    max_retries: int = 2
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Performs an authorized HTTP request to Spotify Web API with auto-retry on 429 rate limit.
    Returns (success, response_dict_or_none, error_message).
    """
    if not token:
        token = get_valid_token()
    if not token:
        return False, None, "Not authenticated with Spotify"

    url = f"{SPOTIFY_API_BASE}{endpoint}" if endpoint.startswith("/") else endpoint
    payload = json.dumps(body).encode("utf-8") if body is not None else None

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "Spoff/0.1.0",
                "Content-Type": "application/json"
            },
            method=method.upper()
        )
        try:
            with urllib.request.urlopen(req, timeout=14) as resp:
                content = resp.read().decode("utf-8")
                data = json.loads(content) if content.strip() else {}
                return True, data, ""
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                err_val = err_json.get("error") if isinstance(err_json, dict) else None
                if isinstance(err_val, dict):
                    msg = str(err_val.get("message") or err_val.get("description") or e)
                elif isinstance(err_val, str):
                    msg = err_val
                else:
                    msg = f"HTTP {e.code}: {e.reason}"
            except Exception:
                msg = f"HTTP {e.code}: {e.reason}"

            if e.code == 429 and attempt < max_retries:
                retry_header = e.headers.get("retry-after") or e.headers.get("Retry-After") or "2"
                try:
                    retry_sec = min(int(retry_header), 6)
                except ValueError:
                    retry_sec = 2
                logger.warning(f"Spotify 429 rate limit on {endpoint}, waiting {retry_sec}s (attempt {attempt+1}/{max_retries})")
                time.sleep(retry_sec)
                continue

            if e.code == 403 and "scope" in msg.lower():
                return False, None, "Spotify permission required: please re-link account (press 'L') for playlist sync"

            logger.error(f"Spotify API {method} {endpoint} failed ({e.code}): {msg}")
            return False, None, msg
        except Exception as e:
            logger.error(f"Spotify API {method} {endpoint} network error: {e}")
            return False, None, str(e)

    return False, None, "Spotify request timed out after retries"

def spotify_api_get(endpoint: str, token: str) -> Optional[Dict[str, Any]]:
    """Performs an authorized GET request to Spotify Web API with auto-retry."""
    ok, data, _ = spotify_api_request(endpoint, method="GET", token=token)
    return data if ok else None

def fetch_current_user_profile(token: str) -> Optional[Dict[str, Any]]:
    """Fetches user profile information."""
    data = spotify_api_get("/me", token)
    if data:
        return {
            "id": data.get("id"),
            "display_name": data.get("display_name") or data.get("id"),
            "email": data.get("email"),
            "product": data.get("product", "free"),
            "images": data.get("images", [])
        }
    return None

def fetch_user_playlists(token: str) -> List[Dict[str, Any]]:
    """Fetches all playlists owned or followed by the authenticated user."""
    playlists = []
    url = "/me/playlists?limit=50"
    while url:
        res = spotify_api_get(url, token)
        if not res:
            break
        items = res.get("items", [])
        for item in items:
            if not item:
                continue
            playlists.append({
                "id": item.get("id"),
                "name": item.get("name", "Untitled"),
                "description": item.get("description", ""),
                "url": item.get("external_urls", {}).get("spotify", ""),
                "tracks_count": item.get("tracks", {}).get("total", 0)
            })
        url = res.get("next")
    return playlists

def fetch_playlist_tracks(token: str, playlist_id: str) -> Optional[List[Dict[str, Any]]]:
    """Fetches all tracks for a specific playlist with pagination. Returns None on network/API failure."""
    tracks = []
    url = f"/playlists/{playlist_id}/tracks?limit=100"
    while url:
        res = spotify_api_get(url, token)
        if res is None:
            logger.error(f"Failed to fetch tracks page for playlist {playlist_id}")
            return None
        for entry in res.get("items", []):
            if not entry or not entry.get("track"):
                continue
            t = entry["track"]
            t_id = t.get("id") or str(hash(t.get("name", "") + str(t.get("artists", []))))
            artists = ", ".join(a.get("name", "Unknown") for a in t.get("artists", []))
            album_info = t.get("album") or {}
            album_name = album_info.get("name")
            images = album_info.get("images") or []
            art_url = images[0].get("url") if images and isinstance(images[0], dict) else None
            tracks.append({
                "id": t_id,
                "title": t.get("name", "Unknown"),
                "artist": artists if artists else "Unknown",
                "duration_ms": t.get("duration_ms", 0),
                "uri": t.get("uri", ""),
                "album": album_name,
                "art_url": art_url,
                "source": "spotify"
            })
        url = res.get("next")
    return tracks

def fetch_liked_songs(token: str, max_tracks: Optional[int] = 200) -> Optional[List[Dict[str, Any]]]:
    """Fetches the user's saved Liked Songs. Returns None on network/API failure."""
    tracks = []
    url = "/me/tracks?limit=50"
    while url:
        if max_tracks and len(tracks) >= max_tracks:
            break
        res = spotify_api_get(url, token)
        if res is None:
            logger.error("Failed to fetch liked songs from Spotify")
            return None
        for entry in res.get("items", []):
            if not entry or not entry.get("track"):
                continue
            t = entry["track"]
            t_id = t.get("id") or str(hash(t.get("name", "") + str(t.get("artists", []))))
            album_info = t.get("album") or {}
            album_name = album_info.get("name")
            images = album_info.get("images") or []
            art_url = images[0].get("url") if images and isinstance(images[0], dict) else None
            tracks.append({
                "id": t_id,
                "title": t.get("name", "Unknown"),
                "artist": artists if artists else "Unknown",
                "duration_ms": t.get("duration_ms", 0),
                "uri": t.get("uri", ""),
                "album": album_name,
                "art_url": art_url,
                "source": "spotify"
            })
        url = res.get("next")
    return tracks

def is_client_side_track(track: Dict[str, Any]) -> bool:
    """
    Returns True if track was added client-side (e.g. from YouTube Music search,
    URL import, or local offline storage) rather than official Spotify catalogue.
    """
    if not isinstance(track, dict):
        return False
    src = str(track.get("source", "")).lower()
    if src in ("ytmusic", "youtube", "local", "offline"):
        return True
    url = str(track.get("url", "")).lower()
    if "youtube.com" in url or "youtu.be" in url:
        return True
    if src == "spotify":
        return False
    uri = str(track.get("uri", ""))
    if uri.startswith("spotify:track:"):
        return False
    tid = str(track.get("id", ""))
    if len(tid) == 22 and tid.isalnum() and not tid.startswith("local_"):
        return False
    return True

def merge_spotify_and_client_tracks(
    spotify_tracks: List[Dict[str, Any]],
    existing_tracks: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Merges newly synchronized tracks from Spotify with client-side tracks (YouTube Music,
    local tracks) that the user previously added into the playlist.
    Preserves all client-side tracks and their relative positions in the playlist.
    """
    if not existing_tracks:
        return list(spotify_tracks)

    client_buckets: Dict[Optional[str], List[Dict[str, Any]]] = {None: []}
    current_anchor: Optional[str] = None
    has_client_tracks = False

    for t in existing_tracks:
        if is_client_side_track(t):
            has_client_tracks = True
            client_buckets[current_anchor].append(t)
        else:
            anchor_key = str(t.get("id") or t.get("uri") or "")
            current_anchor = anchor_key
            if current_anchor not in client_buckets:
                client_buckets[current_anchor] = []

    if not has_client_tracks:
        return list(spotify_tracks)

    merged: List[Dict[str, Any]] = []
    seen_client_keys = set()

    # Top client tracks preceding any Spotify track
    for ct in client_buckets.pop(None, []):
        ckey = ct.get("id") or (str(ct.get("title", "")).strip().lower(), str(ct.get("artist", "")).strip().lower())
        if ckey not in seen_client_keys:
            seen_client_keys.add(ckey)
            merged.append(ct)

    # Fresh Spotify tracks with their anchored client tracks
    for st in spotify_tracks:
        merged.append(st)
        st_id = str(st.get("id") or "")
        st_uri = str(st.get("uri") or "")

        matched_anchor = None
        if st_id in client_buckets:
            matched_anchor = st_id
        elif st_uri in client_buckets:
            matched_anchor = st_uri

        if matched_anchor is not None:
            for ct in client_buckets.pop(matched_anchor, []):
                ckey = ct.get("id") or (str(ct.get("title", "")).strip().lower(), str(ct.get("artist", "")).strip().lower())
                if ckey not in seen_client_keys:
                    seen_client_keys.add(ckey)
                    merged.append(ct)

    # Any remaining client tracks whose Spotify anchors were removed on Spotify
    for remaining_list in client_buckets.values():
        for ct in remaining_list:
            ckey = ct.get("id") or (str(ct.get("title", "")).strip().lower(), str(ct.get("artist", "")).strip().lower())
            if ckey not in seen_client_keys:
                seen_client_keys.add(ckey)
                merged.append(ct)

    return merged

def sync_spotify_library(token: str, progress_callback: Optional[Callable[[str], None]] = None) -> int:
    """
    Synchronizes user's Liked Songs and Spotify playlists into Spoff's local storage.
    Preserves all client-side / YouTube Music tracks added by the user so they stay
    client-side across syncs without being removed or overwritten.
    Returns the number of playlists synchronized.
    """
    if progress_callback:
        progress_callback("Syncing Liked Songs...")
    
    liked = fetch_liked_songs(token)
    existing_playlists = load_saved_playlists()
    
    synced_count = 0
    
    # Sync Liked Songs if successfully fetched
    if liked is not None:
        found = False
        for p in existing_playlists:
            if p.get("id") == "spotify_liked_songs":
                existing_tracks = p.get("tracks", [])
                p["tracks"] = merge_spotify_and_client_tracks(liked, existing_tracks)
                found = True
                break
        if not found and liked:
            existing_playlists.insert(0, {
                "id": "spotify_liked_songs",
                "name": "Liked Songs",
                "url": "",
                "tracks": liked
            })
        synced_count += 1

    # Sync User Playlists
    if progress_callback:
        progress_callback("Fetching user playlists from Spotify...")
    
    user_pls = fetch_user_playlists(token)
    for idx, pl in enumerate(user_pls):
        p_id = pl.get("id")
        p_name = pl.get("name", "Playlist")
        if not p_id:
            continue
        if progress_callback:
            progress_callback(f"Syncing playlist ({idx+1}/{len(user_pls)}): '{p_name}'...")
        
        tracks = fetch_playlist_tracks(token, p_id)
        if tracks is None:
            logger.warning(f"Skipping sync for playlist '{p_name}' due to fetch error")
            continue

        found = False
        for p in existing_playlists:
            matches = (
                p.get("id") == p_id
                or (p.get("spotify_id") and p.get("spotify_id") == p_id)
            )
            if matches:
                p["name"] = p_name
                existing_tracks = p.get("tracks", [])
                p["tracks"] = merge_spotify_and_client_tracks(tracks, existing_tracks)
                if not p.get("url"):
                    p["url"] = pl.get("url", "")
                if p.get("id") != p_id and not p.get("spotify_id"):
                    p["spotify_id"] = p_id
                found = True
                break
        if not found:
            existing_playlists.append({
                "id": p_id,
                "name": p_name,
                "url": pl.get("url", ""),
                "tracks": tracks
            })
        synced_count += 1

    save_saved_playlists(existing_playlists)
    return synced_count

def has_modify_scopes() -> bool:
    """Checks if the saved Spotify session has write permissions for playlists and library."""
    auth = load_spotify_auth()
    if not auth:
        return False
    granted_scopes = set(auth.get("scope", "").split())
    required = {"playlist-modify-public", "playlist-modify-private", "user-library-modify"}
    return bool(required.intersection(granted_scopes))

def search_spotify_tracks(query: str, limit: int = 25, token: Optional[str] = None) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Searches Spotify for tracks matching a search query.
    Returns (success, list_of_tracks, status_message).
    """
    if not query.strip():
        return True, [], ""

    if not token:
        token = get_valid_token()
    if not token:
        return False, [], "Not logged in to Spotify. Press Shift+L or click Spotify to log in."

    url = f"/search?q={urllib.parse.quote(query.strip())}&type=track&limit={limit}"
    ok, data, err = spotify_api_request(url, method="GET", token=token)
    if not ok or not data:
        return False, [], err or "Failed to search Spotify"

    items = data.get("tracks", {}).get("items", [])
    tracks: List[Dict[str, Any]] = []
    for item in items:
        if not item:
            continue
        t_id = item.get("id")
        title = item.get("name") or "Unknown Track"
        artists = ", ".join(a.get("name", "Unknown") for a in item.get("artists", []))
        duration_ms = item.get("duration_ms", 0)
        uri = item.get("uri") or (f"spotify:track:{t_id}" if t_id else "")
        album_info = item.get("album") or {}
        album_name = album_info.get("name")
        images = album_info.get("images") or []
        art_url = images[0].get("url") if images and isinstance(images[0], dict) else None
        tracks.append({
            "id": t_id,
            "title": title,
            "artist": artists,
            "duration_ms": duration_ms,
            "uri": uri,
            "album": album_name,
            "art_url": art_url,
            "url": item.get("external_urls", {}).get("spotify", f"https://open.spotify.com/track/{t_id}" if t_id else ""),
            "source": "spotify"
        })

    return True, tracks, ""

def search_spotify_track(title: str, artist: str = "", token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Searches Spotify for a track by title and artist.
    Returns the track info dict with 'id' and 'uri', or None.
    """
    if not token:
        token = get_valid_token()
    if not token:
        return None

    clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
    clean_artist = re.sub(r"\(.*?\)|\[.*?\]", "", artist).strip()

    query_parts = []
    if clean_title:
        query_parts.append(f'track:"{clean_title}"')
    if clean_artist and clean_artist.lower() != "unknown":
        query_parts.append(f'artist:"{clean_artist}"')

    q_str = " ".join(query_parts) if query_parts else title
    url = f"/search?q={urllib.parse.quote(q_str)}&type=track&limit=1"
    ok, data, _ = spotify_api_request(url, method="GET", token=token)

    items = []
    if ok and data and "tracks" in data:
        items = data["tracks"].get("items", [])

    if not items:
        plain_q = f"{clean_title} {clean_artist}".strip()
        url = f"/search?q={urllib.parse.quote(plain_q)}&type=track&limit=1"
        ok, data, _ = spotify_api_request(url, method="GET", token=token)
        if ok and data and "tracks" in data:
            items = data["tracks"].get("items", [])

    if items:
        item = items[0]
        return {
            "id": item.get("id"),
            "uri": item.get("uri"),
            "title": item.get("name"),
            "artist": ", ".join(a.get("name", "Unknown") for a in item.get("artists", [])),
            "duration_ms": item.get("duration_ms", 0)
        }
    return None

def resolve_spotify_track_info(track: Dict[str, Any], token: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    Resolves a track to its (spotify_track_id, spotify_track_uri).
    If the track is already from Spotify, returns directly.
    Otherwise, queries Spotify search to find the matching Spotify track.
    """
    uri = track.get("uri", "")
    t_id = track.get("id", "")

    if uri and uri.startswith("spotify:track:"):
        spotify_id = uri.split(":")[-1]
        return spotify_id, uri

    if t_id and len(t_id) == 22 and t_id.isalnum():
        return t_id, f"spotify:track:{t_id}"

    found = search_spotify_track(track.get("title", ""), track.get("artist", ""), token=token)
    if found and found.get("id") and found.get("uri"):
        track["uri"] = found["uri"]
        track["spotify_id"] = found["id"]
        return found["id"], found["uri"]

    return None

def add_track_to_spotify_account(
    playlist_id: str,
    playlist_name: str,
    track: Dict[str, Any],
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Syncs the addition of a track to the user's Spotify account.
    Handles Liked Songs, existing Spotify playlists, and local playlists (matching or creating them on Spotify).
    Returns (success, status_message).
    """
    if not playlist_id:
        return False, "Invalid playlist ID"

    if is_client_side_track(track):
        return False, "Client-side track only (not synced to Spotify)"

    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"

    if not has_modify_scopes():
        return False, "Spotify permission required: please re-link account (press 'L') to grant playlist sync"

    res = resolve_spotify_track_info(track, token=token)
    if not res:
        return False, f"Could not find '{track.get('title')}' on Spotify"
    spotify_track_id, spotify_track_uri = res

    # 1. Liked Songs
    if playlist_id == "spotify_liked_songs" or playlist_name.strip().lower() == "liked songs":
        ok, _, err = spotify_api_request(f"/me/tracks?ids={spotify_track_id}", method="PUT", token=token)
        if ok:
            return True, "Synced to Spotify Liked Songs"
        return False, err

    # 2. Existing Spotify playlist ID check
    target_spotify_pl_id = None
    if len(playlist_id) == 22 and playlist_id.isalnum() and not playlist_id.startswith("local_"):
        target_spotify_pl_id = playlist_id
    else:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id and pl.get("spotify_id"):
                target_spotify_pl_id = pl["spotify_id"]
                break

    # 3. If not found by ID, search user's playlists by name
    if not target_spotify_pl_id:
        user_pls = fetch_user_playlists(token)
        for pl in user_pls:
            if pl.get("name", "").strip().lower() == playlist_name.strip().lower():
                target_spotify_pl_id = pl["id"]
                break

    # 4. If still not found, create the playlist on Spotify
    if not target_spotify_pl_id:
        create_body = {
            "name": playlist_name,
            "description": "Synced from Spoff",
            "public": False
        }
        ok_create, pl_data, err = spotify_api_request("/me/playlists", method="POST", body=create_body, token=token)
        if not ok_create or not pl_data or "id" not in pl_data:
            user_prof = fetch_current_user_profile(token)
            if user_prof and user_prof.get("id"):
                ok_create, pl_data, err = spotify_api_request(f"/users/{user_prof['id']}/playlists", method="POST", body=create_body, token=token)

        if ok_create and pl_data and "id" in pl_data:
            target_spotify_pl_id = pl_data["id"]
            local_playlists = load_saved_playlists()
            for pl in local_playlists:
                if pl.get("id") == playlist_id:
                    pl["spotify_id"] = target_spotify_pl_id
                    save_saved_playlists(local_playlists)
                    break
        else:
            return False, f"Failed to create playlist on Spotify: {err}"

    # 5. Add track to Spotify playlist
    add_body = {
        "uris": [spotify_track_uri]
    }
    ok_add, _, err = spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="POST", body=add_body, token=token)
    if ok_add:
        return True, f"Synced to Spotify playlist '{playlist_name}'"
    return False, f"Failed to add track to Spotify: {err}"

def remove_track_from_spotify_account(
    playlist_id: str,
    playlist_name: str,
    track: Dict[str, Any],
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Syncs the removal of a track from the user's Spotify account.
    """
    if not playlist_id:
        return False, "Invalid playlist ID"

    if is_client_side_track(track):
        return False, "Client-side track only (not present on Spotify)"

    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"

    if not has_modify_scopes():
        return False, "Spotify permission required: please re-link account (press 'L')"

    res = resolve_spotify_track_info(track, token=token)
    if not res:
        return False, "Track not found on Spotify"
    spotify_track_id, spotify_track_uri = res

    if playlist_id == "spotify_liked_songs" or playlist_name.strip().lower() == "liked songs":
        ok, _, err = spotify_api_request(f"/me/tracks?ids={spotify_track_id}", method="DELETE", token=token)
        if ok:
            return True, "Removed from Spotify Liked Songs"
        return False, err

    target_spotify_pl_id = None
    if len(playlist_id) == 22 and playlist_id.isalnum() and not playlist_id.startswith("local_"):
        target_spotify_pl_id = playlist_id
    else:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id and pl.get("spotify_id"):
                target_spotify_pl_id = pl["spotify_id"]
                break

    if not target_spotify_pl_id:
        user_pls = fetch_user_playlists(token)
        for pl in user_pls:
            if pl.get("name", "").strip().lower() == playlist_name.strip().lower():
                target_spotify_pl_id = pl["id"]
                break

    if not target_spotify_pl_id:
        return False, "Playlist not found on Spotify"

    del_body = {
        "tracks": [{"uri": spotify_track_uri}]
    }
    ok_del, _, err = spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="DELETE", body=del_body, token=token)
    if ok_del:
        return True, f"Removed from Spotify playlist '{playlist_name}'"
    return False, err

def reorder_spotify_playlist_track(
    playlist_id: str,
    old_index: int,
    new_index: int,
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Syncs track reordering to the user's Spotify playlist.
    """
    if not playlist_id:
        return False, "Invalid playlist ID"

    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"

    if playlist_id == "spotify_liked_songs":
        return False, "Liked Songs is chronological on Spotify"

    target_spotify_pl_id = None
    if len(playlist_id) == 22 and playlist_id.isalnum() and not playlist_id.startswith("local_"):
        target_spotify_pl_id = playlist_id
    else:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id and pl.get("spotify_id"):
                target_spotify_pl_id = pl["spotify_id"]
                break

    if not target_spotify_pl_id:
        return False, "Not a Spotify playlist"

    insert_before = new_index if new_index < old_index else new_index + 1
    body = {
        "range_start": old_index,
        "insert_before": insert_before,
        "range_length": 1
    }
    ok, _, err = spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="PUT", body=body, token=token)
    if ok:
        return True, "Reordered on Spotify"
    return False, err

def delete_spotify_playlist(
    playlist_id: str,
    playlist_name: str,
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Syncs the deletion / unfollowing of a playlist from the user's Spotify account.
    """
    if not playlist_id:
        return False, "Invalid playlist ID"

    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"

    if not has_modify_scopes():
        return False, "Spotify permission required"

    if playlist_id == "spotify_liked_songs" or playlist_name.strip().lower() == "liked songs":
        return False, "Cannot delete Liked Songs collection"

    target_spotify_pl_id = None
    if len(playlist_id) == 22 and playlist_id.isalnum() and not playlist_id.startswith("local_"):
        target_spotify_pl_id = playlist_id
    else:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id:
                if pl.get("spotify_id"):
                    target_spotify_pl_id = pl["spotify_id"]
                elif pl.get("url") and "spotify.com/playlist/" in pl["url"]:
                    m = re.search(r'playlist/([a-zA-Z0-9]{22})', pl["url"])
                    if m:
                        target_spotify_pl_id = m.group(1)
                break

    if not target_spotify_pl_id:
        user_pls = fetch_user_playlists(token)
        for pl in user_pls:
            if pl.get("name", "").strip().lower() == playlist_name.strip().lower():
                target_spotify_pl_id = pl["id"]
                break

    if not target_spotify_pl_id:
        return False, "Playlist not found on Spotify"

    ok, _, err = spotify_api_request(f"/playlists/{target_spotify_pl_id}/followers", method="DELETE", token=token)
    if ok:
        return True, f"Deleted playlist '{playlist_name}' from Spotify"
    return False, err


def rename_spotify_playlist(
    playlist_id: str,
    new_name: str,
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """Syncs renaming of a playlist to the user's Spotify account."""
    if not playlist_id or not new_name:
        return False, "Invalid parameters"
    if playlist_id == "spotify_liked_songs":
        return False, "Cannot rename Liked Songs"
    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"
    if not has_modify_scopes():
        return False, "Spotify permission required"

    target_spotify_pl_id = None
    if len(playlist_id) == 22 and playlist_id.isalnum() and not playlist_id.startswith("local_"):
        target_spotify_pl_id = playlist_id
    else:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id:
                if pl.get("spotify_id"):
                    target_spotify_pl_id = pl["spotify_id"]
                elif pl.get("url") and "spotify.com/playlist/" in pl["url"]:
                    m = re.search(r'playlist/([a-zA-Z0-9]{22})', pl["url"])
                    if m:
                        target_spotify_pl_id = m.group(1)
                break

    if not target_spotify_pl_id:
        return False, "Not a linked Spotify playlist"

    ok, _, err = spotify_api_request(
        f"/playlists/{target_spotify_pl_id}",
        method="PUT",
        body={"name": new_name.strip()},
        token=token
    )
    if ok:
        return True, f"Renamed playlist to '{new_name}' on Spotify"
    return False, err or "Failed to rename on Spotify"


def clone_spotify_playlist(
    local_playlist_id: str,
    cloned_name: str,
    tracks: List[Dict[str, Any]],
    token: Optional[str] = None
) -> Tuple[bool, Optional[str], str]:
    """
    Creates a new playlist on the user's Spotify account and copies all tracks to it.
    Returns (success, spotify_playlist_id, message).
    """
    clean_name = cloned_name.strip() if cloned_name else ""
    if not clean_name:
        return False, None, "Invalid playlist name"
    if not token:
        token = get_valid_token()
    if not token:
        return False, None, "Not logged in to Spotify"
    if not has_modify_scopes():
        return False, None, "Spotify permission required"

    # 1. Create the new playlist on Spotify
    create_body = {
        "name": clean_name,
        "description": "Cloned with Spoff",
        "public": False
    }
    ok_create, pl_data, err = spotify_api_request("/me/playlists", method="POST", body=create_body, token=token)
    if not ok_create or not pl_data or "id" not in pl_data:
        user_prof = fetch_current_user_profile(token)
        if user_prof and user_prof.get("id"):
            ok_create, pl_data, err = spotify_api_request(f"/users/{user_prof['id']}/playlists", method="POST", body=create_body, token=token)

    if not ok_create or not pl_data or "id" not in pl_data:
        return False, None, f"Failed to create playlist on Spotify: {err or 'Unknown error'}"

    new_sp_id = pl_data["id"]

    # 2. Extract track URIs
    uris = []
    if tracks:
        for t in tracks:
            if not isinstance(t, dict):
                continue
            res = resolve_spotify_track_info(t, token=token)
            if res and res[1]:
                uris.append(res[1])

    # 3. Add tracks in batches of 100
    added_count = 0
    if uris:
        for i in range(0, len(uris), 100):
            chunk = uris[i:i + 100]
            ok_add, _, add_err = spotify_api_request(
                f"/playlists/{new_sp_id}/tracks",
                method="POST",
                body={"uris": chunk},
                token=token
            )
            if ok_add:
                added_count += len(chunk)
            else:
                logger.warning(f"Failed to add chunk of tracks to Spotify playlist {new_sp_id}: {add_err}")

    # 4. Link the local playlist with spotify_id and Spotify URL
    if local_playlist_id:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == local_playlist_id:
                pl["spotify_id"] = new_sp_id
                if not pl.get("url") or not str(pl.get("url", "")).startswith("http"):
                    pl["url"] = f"https://open.spotify.com/playlist/{new_sp_id}"
                save_saved_playlists(local_playlists)
                break

    return True, new_sp_id, f"Synced to Spotify: '{clean_name}' with {added_count} tracks"





