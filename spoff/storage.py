import os
import json
import logging
import uuid
import shutil
import tempfile
import fcntl
import threading
import re
from pathlib import Path
from contextlib import contextmanager
from functools import wraps
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

_storage_lock = threading.RLock()
_transaction_state = threading.local()

@contextmanager
def storage_transaction():
    """Process-wide and inter-process transactional lock using fcntl.flock."""
    with _storage_lock:
        if getattr(_transaction_state, "active", False):
            yield
            return
        lock_path = DATA_DIR / ".storage.lock"
        with open(lock_path, "a+b") as lockfile:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
            _transaction_state.active = True
            try:
                yield
            finally:
                _transaction_state.active = False
                try:
                    fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

def transactional(fn):
    """Decorator ensuring a storage transaction wraps the operation."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with storage_transaction():
            return fn(*args, **kwargs)
    return wrapped

def _atomic_json_dump(filepath: Path, data: Any, mode: int = 0o644) -> None:
    """Safely writes JSON data via an fsynced unique temporary file replaced atomically."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=filepath.parent,
            prefix=f".{filepath.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = Path(f.name)
            try:
                os.chmod(tmp_path, mode)
            except OSError:
                pass
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
        try:
            directory_fd = os.open(filepath.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception as e:
        logger.error(f"Atomic write failed for {filepath}: {e}")
        raise
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

def _init_storage_once():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not PLAYLISTS_FILE.exists():
        try:
            _atomic_json_dump(PLAYLISTS_FILE, [])
        except Exception as e:
            logger.error(f"Failed to create empty playlists file: {e}")

    if not INDEX_FILE.exists():
        try:
            _atomic_json_dump(INDEX_FILE, {})
        except Exception as e:
            logger.error(f"Failed to create offline index: {e}")

_init_storage_once()

# Cache extensions supported by downloader and local playback
CACHE_EXTENSIONS = (".m4a", ".opus", ".mp3", ".webm", ".ogg", ".flac")

def validate_track_id(track_id: str) -> str:
    """Validates track_id against path traversal and special characters."""
    if not isinstance(track_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", track_id):
        raise ValueError(f"Invalid track ID: {track_id!r}")
    return track_id

def cache_path(track_id: str, suffix: str) -> Path:
    """Constructs a validated cache file path strictly inside CACHE_DIR."""
    val_id = validate_track_id(track_id)
    path = (CACHE_DIR / f"{val_id}{suffix}").resolve()
    cache_root = CACHE_DIR.resolve()
    if not str(path).startswith(str(cache_root)):
        raise ValueError("Cache path escapes cache directory")
    if path.is_symlink():
        raise ValueError("Cache files must not be symbolic links")
    return path

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

def save_config(config: Dict[str, Any]) -> None:
    _atomic_json_dump(CONFIG_FILE, config)

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

@transactional
def mark_first_launch_done():
    """Records that first-launch onboarding has been completed."""
    try:
        cfg = load_config()
        cfg["first_launch_prompted"] = True
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error marking first launch done: {e}")
        raise

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

@transactional
def save_volume(volume: int):
    """Persists volume level (0-100) to config."""
    try:
        cfg = load_config()
        cfg["volume"] = max(0, min(100, int(volume)))
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving volume: {e}")
        raise

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

@transactional
def save_sidebar_width(width: int):
    """Persists sidebar width to config."""
    try:
        cfg = load_config()
        cfg["sidebar_width"] = max(20, min(120, int(width)))
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving sidebar width: {e}")
        raise

def get_saved_advanced_mode() -> bool:
    """Retrieves whether advanced mode (no keybind hints) is enabled, defaulting to False."""
    try:
        cfg = load_config()
        return bool(cfg.get("advanced_mode", False))
    except Exception as e:
        logger.error(f"Error reading advanced mode: {e}")
        return False

@transactional
def save_advanced_mode(enabled: bool):
    """Persists advanced mode setting to config."""
    try:
        cfg = load_config()
        cfg["advanced_mode"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving advanced mode: {e}")
        raise

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

@transactional
def save_search_engine(engine: str):
    """Persists search engine choice to config."""
    try:
        cfg = load_config()
        cfg["search_engine"] = "spotify" if engine == "spotify" else "ytmusic"
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving search engine: {e}")
        raise

def get_saved_transparency() -> bool:
    """Retrieves whether terminal window transparency is enabled, defaulting to True."""
    try:
        cfg = load_config()
        return bool(cfg.get("transparency", True))
    except Exception as e:
        logger.error(f"Error reading transparency setting: {e}")
        return True

@transactional
def save_transparency(enabled: bool):
    """Persists transparency setting to config."""
    try:
        cfg = load_config()
        cfg["transparency"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving transparency setting: {e}")
        raise

def get_saved_instant_search() -> bool:
    """Retrieves whether instant search is enabled, defaulting to True."""
    try:
        cfg = load_config()
        return bool(cfg.get("instant_search", True))
    except Exception as e:
        logger.error(f"Error reading instant search setting: {e}")
        return True

@transactional
def save_instant_search(enabled: bool):
    """Persists instant search setting to config."""
    try:
        cfg = load_config()
        cfg["instant_search"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving instant search setting: {e}")
        raise

def get_saved_auto_update() -> bool:
    """Retrieves whether auto-update is enabled, defaulting to True."""
    try:
        cfg = load_config()
        return bool(cfg.get("auto_update", True))
    except Exception as e:
        logger.error(f"Error reading auto update setting: {e}")
        return True

@transactional
def save_auto_update(enabled: bool):
    """Persists auto-update setting to config."""
    try:
        cfg = load_config()
        cfg["auto_update"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving auto update setting: {e}")
        raise

def get_saved_visualizer_style() -> str:
    """Retrieves active visualizer style ('bars', 'braille', 'stereo', 'wave', 'dots'), defaulting to 'bars'."""
    try:
        cfg = load_config()
        style = cfg.get("visualizer_style", "bars")
        if style in ("bars", "braille", "stereo", "wave", "dots"):
            return style
    except Exception as e:
        logger.error(f"Error reading visualizer style: {e}")
    return "bars"

@transactional
def save_visualizer_style(style: str):
    """Persists visualizer style choice to config."""
    try:
        cfg = load_config()
        if style in ("bars", "braille", "stereo", "wave", "dots"):
            cfg["visualizer_style"] = style
            save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving visualizer style: {e}")
        raise

def get_saved_visualizer_color() -> str:
    """Retrieves active visualizer color theme ('green', 'cyan', 'amber', 'mono'), defaulting to 'green'."""
    try:
        cfg = load_config()
        color = cfg.get("visualizer_color", "green")
        if color in ("green", "cyan", "amber", "mono"):
            return color
    except Exception as e:
        logger.error(f"Error reading visualizer color: {e}")
    return "green"

@transactional
def save_visualizer_color(color: str):
    """Persists visualizer color theme to config."""
    try:
        cfg = load_config()
        if color in ("green", "cyan", "amber", "mono"):
            cfg["visualizer_color"] = color
            save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving visualizer color: {e}")
        raise

def get_custom_keybindings() -> Dict[str, str]:
    """Retrieves custom keybindings mapping action_name -> key_string."""
    try:
        cfg = load_config()
        kb = cfg.get("keybindings")
        if isinstance(kb, dict):
            return {str(k): str(v) for k, v in kb.items() if v is not None}
    except Exception as e:
        logger.error(f"Error reading custom keybindings: {e}")
    return {}

@transactional
def save_custom_keybindings(keybindings: Dict[str, str]):
    """Persists custom keybindings mapping to config."""
    try:
        cfg = load_config()
        cfg["keybindings"] = keybindings
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving custom keybindings: {e}")
        raise

@transactional
def reset_custom_keybindings():
    """Removes custom keybindings from config, reverting to defaults."""
    try:
        cfg = load_config()
        cfg.pop("keybindings", None)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error resetting custom keybindings: {e}")
        raise

def load_eq_settings() -> Dict[str, Any]:
    """Loads saved EQ engine parameters and active preset state."""
    try:
        cfg = load_config()
        eq_data = cfg.get("eq")
        if isinstance(eq_data, dict):
            return eq_data
    except Exception as e:
        logger.error(f"Error reading EQ configuration: {e}")
    return {}

@transactional
def save_eq_settings(eq_data: Dict[str, Any]) -> None:
    """Persists EQ engine state atomically to config."""
    try:
        cfg = load_config()
        cfg["eq"] = eq_data
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving EQ configuration: {e}")
        raise


def load_saved_playlists() -> List[Dict[str, Any]]:
    try:
        if PLAYLISTS_FILE.exists() and PLAYLISTS_FILE.stat().st_size > 0:
            with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    valid_playlists = []
                    for p in data:
                        if isinstance(p, dict):
                            if "id" not in p:
                                p["id"] = f"pl_{uuid.uuid4().hex[:8]}"
                            if "tracks" not in p or not isinstance(p["tracks"], list):
                                p["tracks"] = []
                            else:
                                p["tracks"] = [t for t in p["tracks"] if isinstance(t, dict)]
                            valid_playlists.append(p)
                    return valid_playlists
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

def save_saved_playlists(playlists: List[Dict[str, Any]]) -> None:
    _atomic_json_dump(PLAYLISTS_FILE, playlists)

@transactional
def add_saved_playlist(playlist: Dict[str, Any]):
    if not isinstance(playlist, dict):
        return
    existing = load_saved_playlists()
    target_id = playlist.get("id")
    for p in existing:
        if target_id and p.get("id") == target_id:
            p.update(playlist)
            save_saved_playlists(existing)
            return
    existing.insert(0, playlist)
    save_saved_playlists(existing)

@transactional
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

@transactional
def add_track_to_playlist(playlist_id: str, track: Dict[str, Any]) -> bool:
    if not playlist_id or not isinstance(track, dict):
        return False
    existing = load_saved_playlists()
    for p in existing:
        if p.get("id") == playlist_id:
            if "tracks" not in p or not isinstance(p["tracks"], list):
                p["tracks"] = []
            for t in p["tracks"]:
                if t.get("id") and track.get("id") and t.get("id") == track.get("id"):
                    return False
                if t.get("title") and track.get("title") and t.get("title") == track.get("title") and t.get("artist") == track.get("artist"):
                    return False
            p["tracks"].append(track)
            save_saved_playlists(existing)
            return True
    return False

@transactional
def remove_track_from_playlist(playlist_id: str, track_id: str) -> bool:
    if not playlist_id or not track_id:
        return False
    existing = load_saved_playlists()
    for p in existing:
        if p.get("id") == playlist_id:
            if "tracks" in p and isinstance(p["tracks"], list):
                original_len = len(p["tracks"])
                p["tracks"] = [t for t in p["tracks"] if t.get("id") != track_id]
                if len(p["tracks"]) != original_len:
                    save_saved_playlists(existing)
                    return True
    return False

@transactional
def update_playlist_tracks(playlist_id: str, tracks: List[Dict[str, Any]]):
    if not playlist_id:
        return
    existing = load_saved_playlists()
    for p in existing:
        if p.get("id") == playlist_id:
            p["tracks"] = [t for t in tracks if isinstance(t, dict)]
            save_saved_playlists(existing)
            return

@transactional
def remove_saved_playlist(playlist_id: str) -> bool:
    if not playlist_id:
        return False
    existing = load_saved_playlists()
    filtered = [p for p in existing if p.get("id") != playlist_id]
    if len(filtered) != len(existing):
        save_saved_playlists(filtered)
        logger.info(f"Deleted playlist: {playlist_id}")
        return True
    return False

@transactional
def rename_saved_playlist(playlist_id: str, new_name: str) -> bool:
    """Atomically renames a saved playlist."""
    clean_name = new_name.strip()
    if not playlist_id or not clean_name:
        return False
    existing = load_saved_playlists()
    for p in existing:
        if p.get("id") == playlist_id:
            p["name"] = clean_name
            save_saved_playlists(existing)
            logger.info(f"Renamed playlist {playlist_id} to '{clean_name}'")
            return True
    return False

@transactional
def clone_saved_playlist(playlist_id: str, new_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Atomically duplicates a saved playlist with all its tracks into a new local playlist.
    """
    if not playlist_id:
        return None
    existing = load_saved_playlists()
    target_idx = None
    target_p = None
    for idx, p in enumerate(existing):
        if p.get("id") == playlist_id:
            target_idx = idx
            target_p = p
            break

    if target_p is None:
        return None

    orig_name = target_p.get("name", "Playlist")
    c_name = new_name.strip() if new_name and new_name.strip() else f"{orig_name} (Copy)"
    new_pid = f"local_{uuid.uuid4().hex[:8]}"

    # Deep copy tracks list
    raw_tracks = target_p.get("tracks", [])
    cloned_tracks = []
    if isinstance(raw_tracks, list):
        for t in raw_tracks:
            if isinstance(t, dict):
                cloned_tracks.append(dict(t))

    orig_url = target_p.get("url", "")
    cloned_url = "" if (orig_url.startswith("http") or "spotify" in orig_url or "youtube" in orig_url) else orig_url

    cloned_playlist = {
        "id": new_pid,
        "name": c_name,
        "url": cloned_url,
        "tracks": cloned_tracks,
    }

    insert_at = (target_idx + 1) if target_idx is not None else 0
    existing.insert(insert_at, cloned_playlist)
    save_saved_playlists(existing)
    logger.info(f"Cloned playlist {playlist_id} to '{c_name}' (id: {new_pid}, {len(cloned_tracks)} tracks)")
    return cloned_playlist


@transactional
def move_saved_playlist(playlist_id: str, delta: int) -> bool:
    """Atomically moves a saved playlist up (-1) or down (+1)."""
    playlists = load_saved_playlists()
    index = next((i for i, p in enumerate(playlists) if p.get("id") == playlist_id), None)
    if index is None or not 0 <= index + delta < len(playlists):
        return False
    playlists.insert(index + delta, playlists.pop(index))
    save_saved_playlists(playlists)
    return True

def load_offline_index() -> Dict[str, Dict[str, Any]]:
    try:
        if INDEX_FILE.exists() and INDEX_FILE.stat().st_size > 0:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    valid_index = {}
                    for k, v in data.items():
                        if isinstance(k, str) and isinstance(v, dict):
                            valid_index[k] = v
                    return valid_index
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

def save_offline_index(index: Dict[str, Dict[str, Any]]) -> None:
    _atomic_json_dump(INDEX_FILE, index)

def get_cached_track_path(track_id: str) -> Optional[Path]:
    if not track_id:
        return None
    try:
        val_id = validate_track_id(track_id)
    except ValueError:
        return None
    for ext in CACHE_EXTENSIONS:
        try:
            track_path = cache_path(val_id, ext)
            if track_path.is_file() and track_path.stat().st_size > 10000:
                return track_path
        except (FileNotFoundError, ValueError, OSError):
            continue
    return None

@transactional
def register_cached_track(track_id: str, meta: Dict[str, Any], filepath: Path):
    val_id = validate_track_id(track_id)
    filepath = Path(filepath)
    if not filepath.is_file() or filepath.stat().st_size <= 10000:
        raise ValueError(f"Incomplete cached audio file: {filepath}")
    index = load_offline_index()
    try:
        dur_ms = int(float(meta.get("duration_ms") or 0))
    except (ValueError, TypeError):
        dur_ms = 0
    index[val_id] = {
        "id": val_id,
        "title": meta.get("title", "Unknown"),
        "artist": meta.get("artist", "Unknown"),
        "duration_ms": dur_ms,
        "filepath": str(filepath.resolve()),
        "size_bytes": filepath.stat().st_size
    }
    save_offline_index(index)
    logger.info(f"Registered cached track: {val_id} -> {filepath}")

@transactional
def delete_cached_track(track_id: str) -> bool:
    """Deletes the cached audio file from disk and removes it from offline registry."""
    if not track_id:
        return False
    try:
        val_id = validate_track_id(track_id)
    except ValueError:
        return False
    index = load_offline_index()
    removed = False
    if val_id in index:
        del index[val_id]
        save_offline_index(index)
        removed = True

    for ext in (*CACHE_EXTENSIONS, ".part"):
        try:
            p = cache_path(val_id, ext)
            if p.exists() and p.is_file():
                p.unlink()
                removed = True
                logger.info(f"Deleted cache file: {p}")
        except Exception as e:
            logger.error(f"Failed to delete cache file {p}: {e}")
    return removed
