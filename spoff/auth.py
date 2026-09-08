import os
import json
import time
import secrets
import hashlib
import base64
import logging
import threading
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
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
    "user-library-read"
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

class OAuthCallbackServer:
    """Lightweight local loopback server to capture Spotify's redirect code."""
    def __init__(self, port: int = SPOTIFY_PORT):
        self.port = port
        self.code: Optional[str] = None
        self.error: Optional[str] = None
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._stopped = False

    def start(self, on_complete: Callable[[Optional[str], Optional[str]], None]):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/login":
                    qs = urllib.parse.parse_qs(parsed.query)
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
                
                # Ignore browser prefetch requests (favicon, etc.) and keep listening
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", self.port), Handler)
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
    if AUTH_FILE.exists():
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {AUTH_FILE}: {e}")
    return None

def save_spotify_auth(data: Dict[str, Any]):
    """Persists Spotify auth session to disk."""
    try:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving {AUTH_FILE}: {e}")

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

def spotify_api_get(endpoint: str, token: str) -> Optional[Dict[str, Any]]:
    """Performs an authorized GET request to Spotify Web API."""
    url = f"{SPOTIFY_API_BASE}{endpoint}" if endpoint.startswith("/") else endpoint
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Spoff/0.1.0"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Spotify API GET failed for {endpoint}: {e}")
        return None

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

def fetch_playlist_tracks(token: str, playlist_id: str) -> List[Dict[str, Any]]:
    """Fetches all tracks for a specific playlist with pagination."""
    tracks = []
    url = f"/playlists/{playlist_id}/tracks?limit=100"
    while url:
        res = spotify_api_get(url, token)
        if not res:
            break
        for entry in res.get("items", []):
            if not entry or not entry.get("track"):
                continue
            t = entry["track"]
            t_id = t.get("id") or str(hash(t.get("name", "") + str(t.get("artists", []))))
            artists = ", ".join(a.get("name", "Unknown") for a in t.get("artists", []))
            tracks.append({
                "id": t_id,
                "title": t.get("name", "Unknown"),
                "artist": artists if artists else "Unknown",
                "duration_ms": t.get("duration_ms", 0),
                "uri": t.get("uri", "")
            })
        url = res.get("next")
    return tracks

def fetch_liked_songs(token: str, max_tracks: int = 200) -> List[Dict[str, Any]]:
    """Fetches the user's saved Liked Songs."""
    tracks = []
    url = "/me/tracks?limit=50"
    while url and len(tracks) < max_tracks:
        res = spotify_api_get(url, token)
        if not res:
            break
        for entry in res.get("items", []):
            if not entry or not entry.get("track"):
                continue
            t = entry["track"]
            t_id = t.get("id") or str(hash(t.get("name", "") + str(t.get("artists", []))))
            artists = ", ".join(a.get("name", "Unknown") for a in t.get("artists", []))
            tracks.append({
                "id": t_id,
                "title": t.get("name", "Unknown"),
                "artist": artists if artists else "Unknown",
                "duration_ms": t.get("duration_ms", 0),
                "uri": t.get("uri", "")
            })
        url = res.get("next")
    return tracks

def sync_spotify_library(token: str, progress_callback: Optional[Callable[[str], None]] = None) -> int:
    """
    Synchronizes user's Liked Songs and Spotify playlists into Spoff's local storage.
    Returns the number of playlists synchronized.
    """
    if progress_callback:
        progress_callback("Syncing Liked Songs...")
    
    liked = fetch_liked_songs(token)
    existing_playlists = load_saved_playlists()
    
    synced_count = 0
    
    # Sync Liked Songs if present
    if liked:
        liked_pl = {
            "id": "spotify_liked_songs",
            "name": "Liked Songs",
            "url": "",
            "tracks": liked
        }
        # Update or add
        found = False
        for p in existing_playlists:
            if p.get("id") == "spotify_liked_songs" or p.get("name") == "Liked Songs":
                p["tracks"] = liked
                found = True
                break
        if not found:
            existing_playlists.insert(0, liked_pl)
        synced_count += 1

    # Sync User Playlists
    if progress_callback:
        progress_callback("Fetching user playlists from Spotify...")
    
    user_pls = fetch_user_playlists(token)
    for idx, pl in enumerate(user_pls):
        p_id = pl["id"]
        p_name = pl["name"]
        if progress_callback:
            progress_callback(f"Syncing playlist ({idx+1}/{len(user_pls)}): '{p_name}'...")
        
        tracks = fetch_playlist_tracks(token, p_id)
        found = False
        for p in existing_playlists:
            if p.get("id") == p_id:
                p["name"] = p_name
                p["tracks"] = tracks
                p["url"] = pl.get("url", "")
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
