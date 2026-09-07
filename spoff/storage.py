import json
import logging
import uuid
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Any

DATA_DIR = Path.home() / ".local" / "share" / "spoff"
OLD_DATA_DIR = Path.home() / ".local" / "share" / "spotato-tui"

# Smooth migration from previous version if exists
if OLD_DATA_DIR.exists() and not DATA_DIR.exists():
    try:
        shutil.copytree(OLD_DATA_DIR, DATA_DIR)
    except Exception:
        pass

CACHE_DIR = DATA_DIR / "cache"
CONFIG_FILE = DATA_DIR / "config.json"
PLAYLISTS_FILE = DATA_DIR / "playlists.json"
INDEX_FILE = DATA_DIR / "offline_index.json"
LOG_FILE = DATA_DIR / "spoff.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("spoff")

def _init_storage_once():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not PLAYLISTS_FILE.exists():
        default_playlists = [
            {
                "id": "37i9dQZF1DXcBWIGoYBM5M",
                "name": "Today's Top Hits",
                "description": "The hottest tracks right now.",
                "url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
            },
            {
                "id": "37i9dQZF1DX0XUsuxWHRQd",
                "name": "RapCaviar",
                "description": "Hip-hop heavyweights.",
                "url": "https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd"
            },
            {
                "id": "37i9dQZF1DX4WYpdgoIcn6",
                "name": "Chill Hits",
                "description": "Kick back to the best chill tunes.",
                "url": "https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6"
            }
        ]
        try:
            with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
                json.dump(default_playlists, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to create default playlists: {e}")

    if not INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to create offline index: {e}")

_init_storage_once()

def load_saved_playlists() -> List[Dict[str, Any]]:
    try:
        if PLAYLISTS_FILE.exists():
            with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error reading playlists: {e}")
    return []

def save_saved_playlists(playlists: List[Dict[str, Any]]):
    try:
        with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(playlists, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving playlists: {e}")

def add_saved_playlist(playlist: Dict[str, Any]):
    existing = load_saved_playlists()
    for p in existing:
        if p["id"] == playlist["id"]:
            p.update(playlist)
            save_saved_playlists(existing)
            return
    existing.insert(0, playlist)
    save_saved_playlists(existing)

def create_local_playlist(name: str) -> Dict[str, Any]:
    pid = f"local_{uuid.uuid4().hex[:8]}"
    playlist = {
        "id": pid,
        "name": name,
        "url": "",
        "tracks": []
    }
    existing = load_saved_playlists()
    existing.insert(0, playlist)
    save_saved_playlists(existing)
    return playlist

def add_track_to_playlist(playlist_id: str, track: Dict[str, Any]) -> bool:
    existing = load_saved_playlists()
    for p in existing:
        if p["id"] == playlist_id:
            if "tracks" not in p or not isinstance(p["tracks"], list):
                p["tracks"] = []
            for t in p["tracks"]:
                if t.get("id") and t.get("id") == track.get("id"):
                    return False
                if t.get("title") == track.get("title") and t.get("artist") == track.get("artist"):
                    return False
            p["tracks"].append(track)
            save_saved_playlists(existing)
            return True
    return False

def remove_track_from_playlist(playlist_id: str, track_id: str) -> bool:
    existing = load_saved_playlists()
    for p in existing:
        if p["id"] == playlist_id:
            if "tracks" in p and isinstance(p["tracks"], list):
                original_len = len(p["tracks"])
                p["tracks"] = [t for t in p["tracks"] if t.get("id") != track_id]
                if len(p["tracks"]) != original_len:
                    save_saved_playlists(existing)
                    return True
    return False

def update_playlist_tracks(playlist_id: str, tracks: List[Dict[str, Any]]):
    existing = load_saved_playlists()
    for p in existing:
        if p["id"] == playlist_id:
            p["tracks"] = list(tracks)
            save_saved_playlists(existing)
            return

def remove_saved_playlist(playlist_id: str) -> bool:
    existing = load_saved_playlists()
    filtered = [p for p in existing if p["id"] != playlist_id]
    if len(filtered) != len(existing):
        save_saved_playlists(filtered)
        logger.info(f"Deleted playlist: {playlist_id}")
        return True
    return False

def load_offline_index() -> Dict[str, Dict[str, Any]]:
    try:
        if INDEX_FILE.exists():
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error reading offline index: {e}")
    return {}

def save_offline_index(index: Dict[str, Dict[str, Any]]):
    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving offline index: {e}")

def get_cached_track_path(track_id: str) -> Optional[Path]:
    for ext in (".m4a", ".opus", ".mp3", ".webm"):
        track_path = CACHE_DIR / f"{track_id}{ext}"
        if track_path.exists() and track_path.stat().st_size > 10000:
            return track_path
    return None

def register_cached_track(track_id: str, meta: Dict[str, Any], filepath: Path):
    index = load_offline_index()
    index[track_id] = {
        "id": track_id,
        "title": meta.get("title", "Unknown"),
        "artist": meta.get("artist", "Unknown"),
        "duration_ms": meta.get("duration_ms", 0),
        "filepath": str(filepath.resolve()),
        "size_bytes": filepath.stat().st_size if filepath.exists() else 0
    }
    save_offline_index(index)
    logger.info(f"Registered cached track: {track_id} -> {filepath}")

def delete_cached_track(track_id: str) -> bool:
    """Deletes the cached audio file from disk and removes it from offline registry."""
    index = load_offline_index()
    removed = False
    if track_id in index:
        del index[track_id]
        save_offline_index(index)
        removed = True

    for ext in (".m4a", ".opus", ".mp3", ".webm", ".part"):
        p = CACHE_DIR / f"{track_id}{ext}"
        if p.exists():
            try:
                p.unlink()
                removed = True
                logger.info(f"Deleted cache file: {p}")
            except Exception as e:
                logger.error(f"Failed to delete cache file {p}: {e}")
    return removed
