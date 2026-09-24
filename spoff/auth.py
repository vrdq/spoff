import re
import json
import time
import base64
import hashlib
import secrets
import logging
import threading
import math
import urllib.request
import urllib.parse
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List, Callable, Tuple

try:
    from .storage import (
        DATA_DIR, load_saved_playlists, save_saved_playlists, storage_transaction, mutate_playlist,
        get_deleted_spotify_playlist_ids, record_deleted_spotify_playlist_id,
        load_liked_songs, save_liked_songs, stable_track_id, liked_index
    )
except ImportError:
    from storage import (
        DATA_DIR, load_saved_playlists, save_saved_playlists, storage_transaction, mutate_playlist,
        get_deleted_spotify_playlist_ids, record_deleted_spotify_playlist_id,
        load_liked_songs, save_liked_songs, stable_track_id, liked_index
    )

try:
    from .matching import _normalized_name, _matches_recording, _tracks_match, _clean_artist_name, _split_artists, _seconds
except ImportError:
    from matching import _normalized_name, _matches_recording, _tracks_match, _clean_artist_name, _split_artists, _seconds

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
    allow_reuse_address = True

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
    with storage_transaction():
        _atomic_json_dump(AUTH_FILE, data, mode=0o600)

def logout_spotify() -> bool:
    """Removes saved Spotify auth session."""
    with storage_transaction():
        if AUTH_FILE.exists():
            try:
                AUTH_FILE.unlink()
                return True
            except Exception as e:
                logger.error(f"Error removing {AUTH_FILE}: {e}")
        return False

_token_refresh_lock = threading.Lock()

def get_valid_token() -> Optional[str]:
    """Returns a valid, unexpired access token, auto-refreshing if necessary."""
    auth = load_spotify_auth()
    if not auth:
        return None

    access_token = auth.get("access_token")
    try:
        expires_at = float(auth.get("expires_at", 0))
        if not math.isfinite(expires_at):
            expires_at = 0.0
    except (ValueError, TypeError):
        expires_at = 0.0
    refresh_token = auth.get("refresh_token")

    # Refresh 60s before expiration
    if time.time() >= expires_at - 60:
        if refresh_token:
            with _token_refresh_lock:
                current = load_spotify_auth()
                if current:
                    try:
                        cur_exp = float(current.get("expires_at", 0))
                    except (ValueError, TypeError):
                        cur_exp = 0.0
                    if time.time() < cur_exp - 60 and current.get("access_token"):
                        return current.get("access_token")

                new_tokens = refresh_spotify_token(refresh_token)
                if not new_tokens:
                    return None
                with storage_transaction():
                    current = load_spotify_auth()
                    if not current or current.get("refresh_token") != refresh_token:
                        return None
                    # Do not overwrite a refresh another worker already committed
                    if current.get("access_token") != auth.get("access_token"):
                        return current.get("access_token")
                    current.update(new_tokens)
                    save_spotify_auth(current)
                    return current.get("access_token")
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
        req_headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Spoff/0.1.0",
        }
        if payload is not None:
            req_headers["Content-Type"] = "application/json"
        elif method.upper() in ("PUT", "POST", "PATCH"):
            payload = b""
            req_headers["Content-Length"] = "0"

        req = urllib.request.Request(
            url,
            data=payload,
            headers=req_headers,
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
                    retry_sec = min(max(1, int(retry_header)), 30)
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
            t_id = t.get("id") or stable_track_id(t)
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
            t_id = t.get("id") or stable_track_id(t)
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

def extract_spotify_playlist_id(pl_data: Any) -> Optional[str]:
    """
    Extracts a 22-character Spotify playlist ID from a playlist dict, ID string,
    Spotify web URL, Spotify URI, or API href.
    """
    if not pl_data:
        return None

    if isinstance(pl_data, str):
        s = pl_data.strip()
        if not s or s == "spotify_liked_songs":
            return None
        if re.fullmatch(r"[A-Za-z0-9]{22}", s) and not s.startswith("local_") and not s.startswith("pl_"):
            return s
        m = re.search(r'playlist/([a-zA-Z0-9]{22})', s)
        if m:
            return m.group(1)
        if s.startswith("spotify:playlist:"):
            parts = s.split(":")
            if len(parts) >= 3 and re.fullmatch(r"[A-Za-z0-9]{22}", parts[2]):
                return parts[2]
        m = re.search(r'/playlists/([a-zA-Z0-9]{22})', s)
        if m:
            return m.group(1)
        return None

    if isinstance(pl_data, dict):
        sp_id = pl_data.get("spotify_id")
        if sp_id and isinstance(sp_id, str):
            extracted = extract_spotify_playlist_id(sp_id)
            if extracted:
                return extracted

        pid = pl_data.get("id")
        if pid and isinstance(pid, str):
            extracted = extract_spotify_playlist_id(pid)
            if extracted:
                return extracted

        url = pl_data.get("url")
        if url and isinstance(url, str):
            extracted = extract_spotify_playlist_id(url)
            if extracted:
                return extracted

        uri = pl_data.get("uri")
        if uri and isinstance(uri, str):
            extracted = extract_spotify_playlist_id(uri)
            if extracted:
                return extracted

        href = pl_data.get("href")
        if href and isinstance(href, str):
            extracted = extract_spotify_playlist_id(href)
            if extracted:
                return extracted

    return None

# _tracks_match is imported from .matching


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

    matched_ct_indices = set()
    for st in spotify_tracks:
        for idx, ct in enumerate(existing_tracks):
            if idx not in matched_ct_indices:
                if _tracks_match(ct, st):
                    matched_ct_indices.add(idx)
                    if ct.get("url") and (not st.get("url") or str(st.get("url", "")).startswith("https://open.spotify.com/")):
                        st["url"] = ct["url"]
                    for k in ("art_url", "artist_art_url", "album_art_url", "thumbnail"):
                        if ct.get(k) and not st.get(k):
                            st[k] = ct[k]
                    if not st.get("spotify_id") and (st.get("id") or ct.get("spotify_id")):
                        st["spotify_id"] = st.get("id") or ct.get("spotify_id")
                    if not st.get("spotify_uri") and (st.get("uri") or ct.get("spotify_uri")):
                        st["spotify_uri"] = st.get("uri") or ct.get("spotify_uri")
                    break

    client_buckets: Dict[Optional[str], List[Dict[str, Any]]] = {None: []}
    current_anchor: Optional[str] = None
    has_client_tracks = False

    for idx, t in enumerate(existing_tracks):
        # A like that has not reached Spotify yet is kept like a client track,
        # otherwise a sync fetched before the push would delete it.
        if is_client_side_track(t) or t.get("spotify_sync_pending"):
            if idx in matched_ct_indices:
                continue
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

    # Top client tracks preceding any Spotify track
    for ct in client_buckets.pop(None, []):
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
                merged.append(ct)

    # Any remaining client tracks whose Spotify anchors were removed on Spotify
    for remaining_list in client_buckets.values():
        for ct in remaining_list:
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

    initial_local = load_saved_playlists()
    initial_ids = {p.get("id") for p in initial_local if p.get("id")}
    initial_sp_ids = {p.get("spotify_id") for p in initial_local if p.get("spotify_id")}
    initial_liked = load_liked_songs()
    initial_by_id = {p["id"]: p for p in initial_local if p.get("id")}

    liked = fetch_liked_songs(token, max_tracks=None)

    if progress_callback:
        progress_callback("Fetching user playlists from Spotify...")

    deleted_ids = get_deleted_spotify_playlist_ids()
    fetched_remote: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    user_pls = fetch_user_playlists(token)
    for idx, pl in enumerate(user_pls):
        p_id = pl.get("id")
        p_name = pl.get("name", "Playlist")
        if not p_id or p_id in deleted_ids:
            continue
        if progress_callback:
            progress_callback(f"Syncing playlist ({idx+1}/{len(user_pls)}): '{p_name}'...")

        tracks = fetch_playlist_tracks(token, p_id)
        if tracks is None:
            logger.warning(f"Skipping sync for playlist '{p_name}' due to fetch error")
            continue
        fetched_remote.append((pl, tracks))

    synced_count = 0
    with storage_transaction():
        deleted_ids = get_deleted_spotify_playlist_ids()
        current_playlists = load_saved_playlists()
        current_ids = {p.get("id") for p in current_playlists if p.get("id")}
        current_sp_ids = {p.get("spotify_id") for p in current_playlists if p.get("spotify_id")}

        # Sync Liked Songs if successfully fetched
        if liked is not None:
            current_liked = load_liked_songs()
            initial_keys = {
                str(x.get("id") or x.get("spotify_id") or x.get("uri") or "")
                for x in initial_liked if isinstance(x, dict)
            }
            current_keys = {
                str(x.get("id") or x.get("spotify_id") or x.get("uri") or "")
                for x in current_liked if isinstance(x, dict)
            }
            removed_during_fetch = {k for k in (initial_keys - current_keys) if k}

            filtered_liked = [
                rt for rt in liked
                if not (
                    (str(rt.get("id") or "") in removed_during_fetch)
                    or (str(rt.get("spotify_id") or "") in removed_during_fetch)
                    or (str(rt.get("uri") or "") in removed_during_fetch)
                )
            ]
            merged_liked = merge_spotify_and_client_tracks(filtered_liked, current_liked)
            save_liked_songs(merged_liked)
            synced_count += 1

        for pl, tracks in fetched_remote:
            p_id = pl.get("id")
            p_name = pl.get("name", "Playlist")
            if p_id in deleted_ids:
                continue
            found = False
            for p in current_playlists:
                matches = (
                    p.get("id") == p_id
                    or (p.get("spotify_id") and p.get("spotify_id") == p_id)
                )
                if matches:
                    original = initial_by_id.get(p.get("id"))
                    existing_tracks = p.get("tracks", [])
                    original_tracks = original.get("tracks", []) if original else []

                    orig_keys = {
                        str(x.get("id") or x.get("spotify_id") or x.get("uri") or "")
                        for x in original_tracks if isinstance(x, dict)
                    }
                    curr_keys = {
                        str(x.get("id") or x.get("spotify_id") or x.get("uri") or "")
                        for x in existing_tracks if isinstance(x, dict)
                    }
                    removed_during_fetch = {k for k in (orig_keys - curr_keys) if k}

                    filtered_tracks = [
                        rt for rt in tracks
                        if not (
                            (str(rt.get("id") or "") in removed_during_fetch)
                            or (str(rt.get("spotify_id") or "") in removed_during_fetch)
                            or (str(rt.get("uri") or "") in removed_during_fetch)
                        )
                    ]
                    if not (original and original.get("name") != p.get("name")):
                        p["name"] = p_name
                    p["tracks"] = merge_spotify_and_client_tracks(filtered_tracks, existing_tracks)
                    if not p.get("url"):
                        p["url"] = pl.get("url", "")
                    if p.get("id") != p_id and not p.get("spotify_id"):
                        p["spotify_id"] = p_id
                    found = True
                    break
            if not found:
                was_known = (p_id in initial_ids or p_id in initial_sp_ids)
                is_currently_known = (p_id in current_ids or p_id in current_sp_ids)
                if not was_known or is_currently_known:
                    current_playlists.append({
                        "id": p_id,
                        "name": p_name,
                        "url": pl.get("url", ""),
                        "tracks": tracks
                    })
            synced_count += 1

        save_saved_playlists(current_playlists)

    # Push likes made while offline or before login.
    if liked is not None and has_modify_scopes():
        for t in load_liked_songs():
            if t.get("spotify_sync_pending") and not is_client_side_track(t):
                try:
                    add_track_to_spotify_account("liked", "Liked Songs", t, token=token)
                except Exception as e:
                    logger.debug(f"Failed to push pending like '{t.get('title')}': {e}")

    # Auto-sync any local playlists to Spotify if they are not yet linked to Spotify
    if has_modify_scopes():
        deleted_ids = get_deleted_spotify_playlist_ids()
        for p in current_playlists:
            p_id = p.get("id")
            # Only push playlists that were never linked. A linked playlist
            # missing from fetched_remote may just have failed to fetch, and a
            # full PUT from stale local data would delete remote-only tracks.
            # YouTube Music tracks are left out and stay in Spoff only.
            if not p_id or p_id in deleted_ids or extract_spotify_playlist_id(p):
                continue
            try:
                ok_sync, msg = sync_playlist_tracks_to_spotify(p_id, tracks=p.get("tracks", []), token=token)
                if ok_sync:
                    synced_count += 1
                    logger.info(f"Auto-synced local playlist '{p.get('name')}' to Spotify: {msg}")
            except Exception as e:
                logger.debug(f"Failed to auto-sync local playlist '{p.get('name')}' to Spotify: {e}")

    return synced_count

def has_modify_scopes() -> bool:
    """Checks if the saved Spotify session has write permissions for playlists and library."""
    auth = load_spotify_auth()
    if not auth:
        return False
    granted_scopes = set(auth.get("scope", "").split())
    required = {"playlist-modify-public", "playlist-modify-private", "user-library-modify"}
    return required.issubset(granted_scopes)

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

def search_spotify_track(
    title: str,
    artist: str = "",
    token: Optional[str] = None,
    duration: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Searches Spotify for a track by title and artist.
    Returns the track info dict with 'id' and 'uri', or None.
    """
    if not token:
        token = get_valid_token()
    if not token:
        return None

    clean_title = str(title or "").strip()
    clean_artist = str(artist or "").strip()
    cleaned_artist = _clean_artist_name(clean_artist)
    primary_artist = _split_artists(cleaned_artist)[0] if cleaned_artist else ""

    queries: List[str] = []
    if clean_title and primary_artist and primary_artist.lower() != "unknown":
        queries.append(f'track:"{clean_title}" artist:"{primary_artist}"')
    if clean_title and cleaned_artist and cleaned_artist != primary_artist and cleaned_artist.lower() != "unknown":
        queries.append(f'track:"{clean_title}" artist:"{cleaned_artist}"')
    if clean_title and cleaned_artist and cleaned_artist.lower() != "unknown":
        queries.append(f"{clean_title} {cleaned_artist}")
    elif clean_title:
        queries.append(clean_title)

    norm_title = _normalized_name(clean_title)
    if norm_title and norm_title != clean_title.lower() and primary_artist:
        queries.append(f"{norm_title} {primary_artist}")
    if clean_title and len(clean_title) >= 3:
        queries.append(f'track:"{clean_title}"')

    seen_queries = set()

    for q in queries:
        q_strip = q.strip()
        if not q_strip or q_strip in seen_queries:
            continue
        seen_queries.add(q_strip)
        url = f"/search?q={urllib.parse.quote(q_strip)}&type=track&limit=10"
        ok, data, _ = spotify_api_request(url, method="GET", token=token)
        if ok and data and "tracks" in data:
            items = data["tracks"].get("items", [])
            for item in items:
                dur_ms = item.get("duration_ms", 0)
                candidate = {
                    "id": item.get("id"),
                    "uri": item.get("uri"),
                    "title": item.get("name"),
                    "artists": item.get("artists") or [],
                    "artist": ", ".join(a.get("name", "Unknown") for a in item.get("artists", []) if isinstance(a, dict)),
                    "duration_ms": dur_ms,
                    "duration_seconds": (dur_ms / 1000.0) if dur_ms else 0.0,
                }
                if _matches_recording(candidate, title, artist, duration) or _tracks_match(candidate, {"title": title, "artist": artist}):
                    return candidate
    return None

def resolve_spotify_track_info(track: Dict[str, Any], token: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    Resolves a track to its (spotify_track_id, spotify_track_uri).
    If the track is already from Spotify, returns directly.
    Otherwise, queries Spotify search to find the matching Spotify track.
    """
    if not isinstance(track, dict):
        return None
    # YouTube Music / local tracks stay in Spoff only. They are never matched
    # to a Spotify recording, so liking, adding, or removing them never
    # touches the Spotify account.
    if is_client_side_track(track):
        return None

    sp_id = str(track.get("spotify_id") or "").strip()
    if sp_id and len(sp_id) == 22 and sp_id.isalnum():
        sp_uri = str(track.get("spotify_uri") or track.get("uri") or "").strip()
        if not sp_uri.startswith("spotify:track:"):
            sp_uri = f"spotify:track:{sp_id}"
        return sp_id, sp_uri

    sp_uri = str(track.get("spotify_uri") or "").strip()
    if sp_uri.startswith("spotify:track:"):
        spotify_id = sp_uri.split(":")[-1]
        return spotify_id, sp_uri

    uri = str(track.get("uri") or "").strip()
    if uri.startswith("spotify:track:"):
        spotify_id = uri.split(":")[-1]
        return spotify_id, uri

    t_id = str(track.get("id") or "").strip()
    if len(t_id) == 22 and t_id.isalnum() and not t_id.startswith("local_"):
        return t_id, f"spotify:track:{t_id}"

    dur = 0.0
    if "duration_seconds" in track and track["duration_seconds"]:
        try:
            dur = float(track["duration_seconds"])
        except (ValueError, TypeError):
            dur = 0.0
    elif "duration_ms" in track and track["duration_ms"]:
        try:
            dur = float(track["duration_ms"]) / 1000.0
        except (ValueError, TypeError):
            dur = 0.0
    elif "duration" in track and track["duration"]:
        dur = _seconds(track["duration"])

    search_kwargs: Dict[str, Any] = {"token": token}
    if dur > 0:
        search_kwargs["duration"] = dur

    found = search_spotify_track(track.get("title", ""), track.get("artist", ""), **search_kwargs)
    if found and found.get("id") and found.get("uri"):
        track["spotify_id"] = found["id"]
        track["spotify_uri"] = found["uri"]
        if not track.get("uri") or str(track.get("uri")).startswith("spotify:"):
            track["uri"] = found["uri"]
        return found["id"], found["uri"]

    return None


def create_spotify_playlist(
    playlist_name: str,
    token: Optional[str] = None,
    description: str = "Synced from Spoff"
) -> Tuple[bool, Optional[str], str]:
    """Creates a new playlist on the user's Spotify account."""
    if not token:
        token = get_valid_token()
    if not token:
        return False, None, "Not logged in to Spotify"
    if not has_modify_scopes():
        return False, None, "Spotify permission required: please re-link account (press 'L') for playlist sync"

    clean_name = playlist_name.strip() if playlist_name else "Playlist"
    create_body = {
        "name": clean_name,
        "description": description,
        "public": False
    }
    ok_create, pl_data, err = spotify_api_request("/me/playlists", method="POST", body=create_body, token=token)
    if not ok_create or not pl_data or "id" not in pl_data:
        user_prof = fetch_current_user_profile(token)
        if user_prof and user_prof.get("id"):
            ok_create, pl_data, err = spotify_api_request(
                f"/users/{user_prof['id']}/playlists",
                method="POST",
                body=create_body,
                token=token
            )

    if ok_create and pl_data and "id" in pl_data:
        return True, pl_data["id"], ""
    return False, None, err or "Failed to create playlist on Spotify"


def _merge_resolved_spotify_ids(
    current_tracks: List[Dict[str, Any]],
    resolved_tracks: List[Dict[str, Any]],
) -> None:
    """Copies resolved spotify_id/spotify_uri onto the stored tracks in place.

    Writing back the resolved snapshot wholesale would drop any edit made to the
    playlist while the Spotify requests were in flight.
    """
    for rt in resolved_tracks:
        if not isinstance(rt, dict) or not (rt.get("spotify_id") or rt.get("spotify_uri")):
            continue
        for ct in current_tracks:
            if not isinstance(ct, dict) or ct.get("spotify_id"):
                continue
            if ct is rt or (rt.get("id") and ct.get("id") == rt.get("id")):
                ct["spotify_id"] = rt.get("spotify_id")
                if rt.get("spotify_uri"):
                    ct["spotify_uri"] = rt["spotify_uri"]


def _collect_playlist_spotify_uris(
    tracks: List[Dict[str, Any]],
    token: Optional[str] = None,
) -> List[str]:
    """
    Extracts or resolves Spotify track URIs for all tracks in a playlist.
    YouTube Music and local tracks are excluded; they stay in Spoff only.
    """
    uris: List[str] = []
    seen: set = set()
    for t in tracks:
        if not isinstance(t, dict) or is_client_side_track(t):
            continue
        sp_uri = str(t.get("spotify_uri") or "").strip()
        if sp_uri.startswith("spotify:track:"):
            if sp_uri not in seen:
                seen.add(sp_uri)
                uris.append(sp_uri)
            continue
        sp_id = str(t.get("spotify_id") or "").strip()
        if len(sp_id) == 22 and sp_id.isalnum() and not sp_id.startswith("local_"):
            uri = f"spotify:track:{sp_id}"
            if uri not in seen:
                seen.add(uri)
                uris.append(uri)
            continue
        uri = str(t.get("uri") or "").strip()
        if uri.startswith("spotify:track:"):
            if uri not in seen:
                seen.add(uri)
                uris.append(uri)
            continue
        tid = str(t.get("id") or "").strip()
        if len(tid) == 22 and tid.isalnum() and not tid.startswith("local_") and not is_client_side_track(t):
            uri = f"spotify:track:{tid}"
            if uri not in seen:
                seen.add(uri)
                uris.append(uri)
            continue

    return uris


def add_track_to_spotify_account(
    playlist_id: str,
    playlist_name: str,
    track: Dict[str, Any],
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Syncs the addition of a track to the user's Spotify account.
    Handles Liked Songs, existing Spotify playlists, and local playlists (matching or creating them on Spotify).
    Auto-heals 404s by recreating playlists on Spotify and syncing tracks.
    Returns (success, status_message).
    """
    if not playlist_id:
        return False, "Invalid playlist ID"

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
    if playlist_id in ("spotify_liked_songs", "liked", "liked_songs"):
        ok, _, err = spotify_api_request(f"/me/tracks?ids={spotify_track_id}", method="PUT", token=token)
        if ok:
            try:
                with storage_transaction():
                    current = load_liked_songs()
                    idx = liked_index(current, track)
                    if idx is not None:
                        if not current[idx].get("spotify_id"):
                            current[idx]["spotify_id"] = spotify_track_id
                        if not current[idx].get("spotify_uri"):
                            current[idx]["spotify_uri"] = spotify_track_uri
                        current[idx].pop("spotify_sync_pending", None)
                        save_liked_songs(current)
            except Exception as e:
                logger.debug(f"Failed to persist resolved spotify track info to liked songs: {e}")
            return True, "Synced to Spotify Liked Songs"
        return False, err

    # 2. Existing Spotify playlist ID check
    target_spotify_pl_id = extract_spotify_playlist_id(playlist_id)
    if not target_spotify_pl_id:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id:
                target_spotify_pl_id = extract_spotify_playlist_id(pl)
                if target_spotify_pl_id:
                    break

    def update_sp_meta(p: Dict[str, Any]) -> None:
        for t in p.get("tracks", []):
            if _tracks_match(t, track):
                if not t.get("spotify_id"):
                    t["spotify_id"] = spotify_track_id
                if not t.get("spotify_uri"):
                    t["spotify_uri"] = spotify_track_uri

    # 3. If not found, create the playlist on Spotify and sync all tracks
    if not target_spotify_pl_id:
        ok_create, new_sp_id, err = create_spotify_playlist(playlist_name, token=token)
        if not ok_create or not new_sp_id:
            return False, f"Failed to create playlist on Spotify: {err}"
        target_spotify_pl_id = new_sp_id

        local_playlists = load_saved_playlists()
        cur_pl = next((p for p in local_playlists if p.get("id") == playlist_id), None)
        all_tracks = list(cur_pl.get("tracks", [])) if cur_pl else [track]
        uris = _collect_playlist_spotify_uris(all_tracks, token=token)
        if spotify_track_uri not in uris:
            uris.append(spotify_track_uri)

        for i in range(0, len(uris), 100):
            spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="POST", body={"uris": uris[i:i+100]}, token=token)

        def _save_new_sp_pl(p: Dict[str, Any]) -> None:
            p["spotify_id"] = target_spotify_pl_id
            if not p.get("url") or not str(p.get("url")).startswith("http"):
                p["url"] = f"https://open.spotify.com/playlist/{target_spotify_pl_id}"
            _merge_resolved_spotify_ids(p.get("tracks", []), all_tracks)
            update_sp_meta(p)

        try:
            mutate_playlist(playlist_id, _save_new_sp_pl)
        except Exception as e:
            logger.debug(f"Failed to persist resolved spotify track info to playlist: {e}")
        return True, f"Created Spotify playlist '{playlist_name}' and synced tracks"

    # 4. Add track to Spotify playlist with 404 auto-healing
    add_body = {
        "uris": [spotify_track_uri]
    }
    ok_add, _, err = spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="POST", body=add_body, token=token)
    if ok_add:
        try:
            mutate_playlist(playlist_id, update_sp_meta)
        except Exception as e:
            logger.debug(f"Failed to persist resolved spotify track info to playlist: {e}")
        return True, f"Synced to Spotify playlist '{playlist_name}'"

    # Auto-heal on 404
    if "404" in str(err) or "not found" in str(err).lower():
        logger.info(f"Target Spotify playlist {target_spotify_pl_id} returned 404; auto-healing by recreating on Spotify...")
        ok_create, new_sp_id, cr_err = create_spotify_playlist(playlist_name, token=token)
        if ok_create and new_sp_id:
            target_spotify_pl_id = new_sp_id
            local_playlists = load_saved_playlists()
            cur_pl = next((p for p in local_playlists if p.get("id") == playlist_id), None)
            all_tracks = list(cur_pl.get("tracks", [])) if cur_pl else [track]
            uris = _collect_playlist_spotify_uris(all_tracks, token=token)
            if spotify_track_uri not in uris:
                uris.append(spotify_track_uri)

            for i in range(0, len(uris), 100):
                spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="POST", body={"uris": uris[i:i+100]}, token=token)

            def _heal_save(p: Dict[str, Any]) -> None:
                p["spotify_id"] = target_spotify_pl_id
                if not p.get("url") or not str(p.get("url")).startswith("http"):
                    p["url"] = f"https://open.spotify.com/playlist/{target_spotify_pl_id}"
                _merge_resolved_spotify_ids(p.get("tracks", []), all_tracks)
                update_sp_meta(p)

            try:
                mutate_playlist(playlist_id, _heal_save)
            except Exception as e:
                logger.debug(f"Failed to persist resolved spotify track info to playlist: {e}")
            return True, f"Recreated and synced to Spotify playlist '{playlist_name}'"

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

    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"

    if not has_modify_scopes():
        return False, "Spotify permission required: please re-link account (press 'L')"

    res = resolve_spotify_track_info(track, token=token)
    if not res:
        # YouTube Music / local track: it never reached Spotify, so there is
        # nothing to delete. Report False so the UI shows no Spotify notice.
        return False, "Track is local-only; nothing to remove on Spotify"
    spotify_track_id, spotify_track_uri = res

    if playlist_id in ("spotify_liked_songs", "liked", "liked_songs"):
        ok, _, err = spotify_api_request(f"/me/tracks?ids={spotify_track_id}", method="DELETE", token=token)
        if ok:
            return True, "Removed from Spotify Liked Songs"
        return False, err

    target_spotify_pl_id = extract_spotify_playlist_id(playlist_id)
    if not target_spotify_pl_id:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id:
                target_spotify_pl_id = extract_spotify_playlist_id(pl)
                if target_spotify_pl_id:
                    break

    if not target_spotify_pl_id:
        return True, "Playlist is not linked to Spotify"

    del_body = {
        "tracks": [{"uri": spotify_track_uri}]
    }
    ok_del, _, err = spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="DELETE", body=del_body, token=token)
    if ok_del:
        return True, f"Removed from Spotify playlist '{playlist_name}'"
    if "404" in str(err) or "not found" in str(err).lower():
        return True, "Remote playlist not found (already removed from Spotify)"
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

    if playlist_id in ("spotify_liked_songs", "liked"):
        return False, "Liked Songs is chronological on Spotify"

    if not has_modify_scopes():
        return False, "Spotify permission required: please re-link account (press 'L') for playlist sync"

    target_spotify_pl_id = extract_spotify_playlist_id(playlist_id)
    if not target_spotify_pl_id:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id:
                target_spotify_pl_id = extract_spotify_playlist_id(pl)
                if target_spotify_pl_id:
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
    logger.warning(f"Failed to reorder track on Spotify playlist {target_spotify_pl_id}: {err}")
    return False, err

def sync_playlist_tracks_to_spotify(
    playlist_id: str,
    tracks: Optional[List[Dict[str, Any]]] = None,
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Replaces or synchronizes the remote Spotify playlist track order with the exact order
    of Spotify tracks in the local playlist. Auto-creates or auto-heals if remote playlist is missing or 404.
    """
    if not playlist_id or playlist_id in ("spotify_liked_songs", "liked"):
        return False, "Cannot set track order for Liked Songs"

    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"

    if not has_modify_scopes():
        return False, "Spotify permission required: please re-link account (press 'L') for playlist sync"

    pl_name = "Playlist"
    target_spotify_pl_id = extract_spotify_playlist_id(playlist_id)
    local_playlists = load_saved_playlists()
    # Match by local ID first. Comparing extracted Spotify IDs alone would let
    # any unlinked playlist (None == None) stand in for this one.
    local_pl = next((pl for pl in local_playlists if pl.get("id") == playlist_id), None)
    if local_pl is None and target_spotify_pl_id:
        local_pl = next(
            (pl for pl in local_playlists if extract_spotify_playlist_id(pl) == target_spotify_pl_id),
            None,
        )
    if local_pl is not None:
        pl_name = local_pl.get("name") or pl_name
        if not target_spotify_pl_id:
            target_spotify_pl_id = extract_spotify_playlist_id(local_pl)
        if tracks is None:
            tracks = local_pl.get("tracks", [])

    if tracks is None:
        return False, "No tracks provided"

    was_unlinked = not bool(target_spotify_pl_id)
    if was_unlinked:
        ok_cr, new_id, cr_err = create_spotify_playlist(pl_name, token=token)
        if not ok_cr or not new_id:
            return False, f"Failed to create playlist on Spotify: {cr_err}"
        target_spotify_pl_id = new_id
        def _set_created_pl(p: Dict[str, Any]) -> None:
            p["spotify_id"] = new_id
            if not p.get("url") or not str(p.get("url")).startswith("http"):
                p["url"] = f"https://open.spotify.com/playlist/{new_id}"
        mutate_playlist(playlist_id, _set_created_pl)

    uris = _collect_playlist_spotify_uris(tracks, token=token)

    # Persist any tracks that were resolved with spotify_id / spotify_uri
    try:
        def _update_resolved(p: Dict[str, Any]) -> None:
            _merge_resolved_spotify_ids(p.get("tracks", []), tracks)
            if target_spotify_pl_id:
                p["spotify_id"] = target_spotify_pl_id
                if not p.get("url") or not str(p.get("url")).startswith("http"):
                    p["url"] = f"https://open.spotify.com/playlist/{target_spotify_pl_id}"
        mutate_playlist(playlist_id, _update_resolved)
    except Exception as e:
        logger.debug(f"Failed to persist resolved tracks to playlist: {e}")

    if not uris:
        if was_unlinked:
            return True, f"Created Spotify playlist '{pl_name}'"
        return True, "No Spotify tracks to sync"

    if was_unlinked:
        for i in range(0, len(uris), 100):
            spotify_api_request(
                f"/playlists/{target_spotify_pl_id}/tracks",
                method="POST",
                body={"uris": uris[i:i+100]},
                token=token
            )
        return True, f"Synced {len(uris)} tracks to new Spotify playlist '{pl_name}'"

    first_batch = uris[:100]
    ok, _, err = spotify_api_request(
        f"/playlists/{target_spotify_pl_id}/tracks",
        method="PUT",
        body={"uris": first_batch},
        token=token
    )
    if not ok:
        if "404" in str(err) or "not found" in str(err).lower():
            logger.info(f"Target Spotify playlist {target_spotify_pl_id} returned 404; auto-healing by recreating on Spotify...")
            ok_cr, new_id, cr_err = create_spotify_playlist(pl_name, token=token)
            if not ok_cr or not new_id:
                return False, f"Failed to recreate playlist on Spotify: {cr_err}"
            target_spotify_pl_id = new_id
            def _heal_pl(p: Dict[str, Any]) -> None:
                p["spotify_id"] = new_id
                if not p.get("url") or not str(p.get("url")).startswith("http"):
                    p["url"] = f"https://open.spotify.com/playlist/{new_id}"
                _merge_resolved_spotify_ids(p.get("tracks", []), tracks)
            mutate_playlist(playlist_id, _heal_pl)
            for i in range(0, len(uris), 100):
                spotify_api_request(f"/playlists/{target_spotify_pl_id}/tracks", method="POST", body={"uris": uris[i:i+100]}, token=token)
            return True, f"Recreated and synced {len(uris)} tracks to Spotify playlist '{pl_name}'"

        logger.warning(f"Failed to sync playlist tracks to Spotify: {err}")
        return False, err or "Failed to update playlist tracks on Spotify"

    for i in range(100, len(uris), 100):
        batch = uris[i:i+100]
        ok_chunk, _, err_chunk = spotify_api_request(
            f"/playlists/{target_spotify_pl_id}/tracks",
            method="POST",
            body={"uris": batch},
            token=token
        )
        if not ok_chunk:
            logger.warning(f"Failed to append batch to Spotify playlist: {err_chunk}")
            return False, err_chunk or f"Failed to append tracks chunk starting at {i}"

    return True, f"Playlist tracks synced to Spotify ({len(uris)} tracks)"

def delete_spotify_playlist(
    playlist_id: str,
    playlist_name: str,
    token: Optional[str] = None,
    remote_id: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Syncs the deletion / unfollowing of a playlist from the user's Spotify account.
    """
    if not playlist_id and not remote_id:
        return False, "Invalid playlist ID"

    if playlist_id == "spotify_liked_songs" or remote_id == "spotify_liked_songs":
        return False, "Cannot delete Liked Songs collection"

    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"

    if not has_modify_scopes():
        return False, "Spotify write permission required"

    target_spotify_pl_id = remote_id or extract_spotify_playlist_id(playlist_id)
    if not target_spotify_pl_id:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id:
                extracted = extract_spotify_playlist_id(pl)
                if extracted:
                    target_spotify_pl_id = extracted
                    break

    if not target_spotify_pl_id:
        return False, "Playlist not found on Spotify"

    ok, _, err = spotify_api_request(f"/playlists/{target_spotify_pl_id}/followers", method="DELETE", token=token)
    if ok:
        record_deleted_spotify_playlist_id(target_spotify_pl_id)
        logger.info(f"Deleted playlist '{playlist_name}' ({target_spotify_pl_id}) from Spotify")
        return True, f"Deleted playlist '{playlist_name}' from Spotify"
    return False, err or "Spotify API deletion failed"


def rename_spotify_playlist(
    playlist_id: str,
    new_name: str,
    token: Optional[str] = None,
    remote_id: Optional[str] = None
) -> Tuple[bool, str]:
    """Syncs renaming of a playlist to the user's Spotify account."""
    if (not playlist_id and not remote_id) or not new_name:
        return False, "Invalid parameters"
    if playlist_id == "spotify_liked_songs" or remote_id == "spotify_liked_songs":
        return False, "Cannot rename Liked Songs"
    if not token:
        token = get_valid_token()
    if not token:
        return False, "Not logged in to Spotify"
    if not has_modify_scopes():
        return False, "Spotify permission required"

    target_spotify_pl_id = remote_id or extract_spotify_playlist_id(playlist_id)
    if not target_spotify_pl_id:
        local_playlists = load_saved_playlists()
        for pl in local_playlists:
            if pl.get("id") == playlist_id:
                extracted = extract_spotify_playlist_id(pl)
                if extracted:
                    target_spotify_pl_id = extracted
                break

    if not target_spotify_pl_id:
        ok_cr, new_id, err_cr = create_spotify_playlist(new_name, token=token)
        if ok_cr and new_id:
            mutate_playlist(playlist_id, lambda p: p.update(spotify_id=new_id, name=new_name))
            sync_playlist_tracks_to_spotify(playlist_id, token=token)
            return True, f"Created Spotify playlist '{new_name}'"
        return False, "Playlist not linked to Spotify"

    ok, _, err = spotify_api_request(
        f"/playlists/{target_spotify_pl_id}",
        method="PUT",
        body={"name": new_name},
        token=token
    )
    if ok:
        return True, f"Renamed playlist on Spotify to '{new_name}'"

    if "404" in str(err) or "not found" in str(err).lower():
        ok_cr, new_id, err_cr = create_spotify_playlist(new_name, token=token)
        if ok_cr and new_id:
            mutate_playlist(playlist_id, lambda p: p.update(spotify_id=new_id, name=new_name))
            sync_playlist_tracks_to_spotify(playlist_id, token=token)
            return True, f"Recreated playlist on Spotify as '{new_name}'"

    return False, err


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
                return (
                    False,
                    new_sp_id,
                    f"Created Spotify playlist, but copied only {added_count}/{len(uris)} "
                    f"tracks: {add_err}. Remote playlist: {new_sp_id}"
                )

    # 4. Link the local playlist with spotify_id and Spotify URL
    if local_playlist_id:
        def link_sp(pl: Dict[str, Any]) -> None:
            pl["spotify_id"] = new_sp_id
            if not pl.get("url") or not str(pl.get("url", "")).startswith("http"):
                pl["url"] = f"https://open.spotify.com/playlist/{new_sp_id}"
        mutate_playlist(local_playlist_id, link_sp)

    return True, new_sp_id, f"Synced to Spotify: '{clean_name}' with {added_count} tracks"
