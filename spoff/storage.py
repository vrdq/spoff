import os
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

def _atomic_json_dump(filepath: Path, data: Any) -> None:
    """Safely writes JSON data via an fsynced temporary file replaced atomically."""
    tmp_path = filepath.with_suffix(f".tmp.{os.getpid()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception as e:
        logger.error(f"Atomic write failed for {filepath}: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

def _init_storage_once():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not PLAYLISTS_FILE.exists() or PLAYLISTS_FILE.stat().st_size == 0:
        try:
            _atomic_json_dump(PLAYLISTS_FILE, [])
        except Exception as e:
            logger.error(f"Failed to create empty playlists file: {e}")

    if not INDEX_FILE.exists() or INDEX_FILE.stat().st_size == 0:
        try:
            _atomic_json_dump(INDEX_FILE, {})
        except Exception as e:
            logger.error(f"Failed to create offline index: {e}")

_init_storage_once()

def load_config() -> Dict[str, Any]:
    try:
        if CONFIG_FILE.exists() and CONFIG_FILE.stat().st_size > 0:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        logger.error(f"Error reading config: {e}")
    return {}

def save_config(config: Dict[str, Any]):
    try:
        _atomic_json_dump(CONFIG_FILE, config)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

def is_first_launch() -> bool:
    """Returns True if Spoff is running for the first time without configured onboarding."""
    try:
        cfg = load_config()
        if "first_launch_prompted" in cfg:
            return not bool(cfg.get("first_launch_prompted"))
        auth_file = DATA_DIR / "spotify_auth.json"
        if auth_file.exists():
            mark_first_launch_done()
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking first launch: {e}")
        return False

def mark_first_launch_done():
    """Records that first-launch onboarding has been completed."""
    try:
        cfg = load_config()
        cfg["first_launch_prompted"] = True
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error marking first launch done: {e}")

def get_saved_volume() -> int:
    """Retrieves saved volume level (0-100), defaulting to 80."""
    try:
        cfg = load_config()
        val = cfg.get("volume")
        if val is not None:
            return max(0, min(100, int(val)))
    except Exception as e:
        logger.error(f"Error reading saved volume: {e}")
    return 80

def save_volume(volume: int):
    """Persists volume level (0-100) to config."""
    try:
        cfg = load_config()
        cfg["volume"] = max(0, min(100, int(volume)))
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving volume: {e}")

def get_saved_sidebar_width() -> int:
    """Retrieves saved sidebar width, defaulting to 44."""
    try:
        cfg = load_config()
        val = cfg.get("sidebar_width")
        if val is not None:
            return max(20, min(120, int(val)))
    except Exception as e:
        logger.error(f"Error reading saved sidebar width: {e}")
    return 44

def save_sidebar_width(width: int):
    """Persists sidebar width to config."""
    try:
        cfg = load_config()
        cfg["sidebar_width"] = max(20, min(120, int(width)))
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving sidebar width: {e}")

def get_saved_advanced_mode() -> bool:
    """Retrieves whether advanced mode (no keybind hints) is enabled, defaulting to False."""
    try:
        cfg = load_config()
        return bool(cfg.get("advanced_mode", False))
    except Exception as e:
        logger.error(f"Error reading advanced mode: {e}")
        return False

def save_advanced_mode(enabled: bool):
    """Persists advanced mode setting to config."""
    try:
        cfg = load_config()
        cfg["advanced_mode"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving advanced mode: {e}")

def get_saved_search_engine() -> str:
    """Retrieves active search engine ('ytmusic' or 'spotify'), defaulting to 'ytmusic'."""
    try:
        cfg = load_config()
        eng = cfg.get("search_engine", "ytmusic")
        if eng in ("ytmusic", "spotify"):
            return eng
    except Exception as e:
        logger.error(f"Error reading search engine: {e}")
    return "ytmusic"

def save_search_engine(engine: str):
    """Persists search engine choice to config."""
    try:
        cfg = load_config()
        cfg["search_engine"] = "spotify" if engine == "spotify" else "ytmusic"
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving search engine: {e}")

def get_saved_transparency() -> bool:
    """Retrieves whether terminal window transparency is enabled, defaulting to True."""
    try:
        cfg = load_config()
        return bool(cfg.get("transparency", True))
    except Exception as e:
        logger.error(f"Error reading transparency setting: {e}")
        return True

def save_transparency(enabled: bool):
    """Persists transparency setting to config."""
    try:
        cfg = load_config()
        cfg["transparency"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving transparency setting: {e}")

def get_custom_keybindings() -> Dict[str, str]:
    """Retrieves custom keybindings mapping action_name -> key_string."""
    try:
        cfg = load_config()
        kb = cfg.get("keybindings")
        if isinstance(kb, dict):
            return {str(k): str(v) for k, v in kb.items() if v}
    except Exception as e:
        logger.error(f"Error reading custom keybindings: {e}")
    return {}

def save_custom_keybindings(keybindings: Dict[str, str]):
    """Persists custom keybindings mapping to config."""
    try:
        cfg = load_config()
        cfg["keybindings"] = keybindings
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving custom keybindings: {e}")

def reset_custom_keybindings():
    """Removes custom keybindings from config, reverting to defaults."""
    try:
        cfg = load_config()
        cfg.pop("keybindings", None)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error resetting custom keybindings: {e}")

def load_saved_playlists() -> List[Dict[str, Any]]:
    try:
        if PLAYLISTS_FILE.exists() and PLAYLISTS_FILE.stat().st_size > 0:
            with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception as e:
        logger.error(f"Error reading playlists: {e}")
        try:
            if PLAYLISTS_FILE.exists() and PLAYLISTS_FILE.stat().st_size > 0:
                corrupted = PLAYLISTS_FILE.with_suffix(".json.corrupted")
                shutil.copy2(PLAYLISTS_FILE, corrupted)
                logger.warning(f"Corrupted playlists backed up to {corrupted}")
        except Exception:
            pass
    return []

def save_saved_playlists(playlists: List[Dict[str, Any]]):
    try:
        _atomic_json_dump(PLAYLISTS_FILE, playlists)
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
        if INDEX_FILE.exists() and INDEX_FILE.stat().st_size > 0:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        logger.error(f"Error reading offline index: {e}")
        try:
            if INDEX_FILE.exists() and INDEX_FILE.stat().st_size > 0:
                corrupted = INDEX_FILE.with_suffix(".json.corrupted")
                shutil.copy2(INDEX_FILE, corrupted)
                logger.warning(f"Corrupted offline index backed up to {corrupted}")
        except Exception:
            pass
    return {}

def save_offline_index(index: Dict[str, Dict[str, Any]]):
    try:
        _atomic_json_dump(INDEX_FILE, index)
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
