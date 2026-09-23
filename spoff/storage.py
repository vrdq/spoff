import os
import json
import logging
import uuid
import hashlib
import shutil
import tempfile
import fcntl
import threading
import re
import math
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import contextmanager
from functools import wraps
from typing import List, Dict, Optional, Any, Callable, Tuple

try:
    from .matching import _tracks_match
except ImportError:
    from matching import _tracks_match

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
LIKED_SONGS_FILE = DATA_DIR / "liked_songs.json"
DELETED_PLAYLISTS_FILE = DATA_DIR / "deleted_spotify_playlists.json"
INDEX_FILE = DATA_DIR / "offline_index.json"
LOG_FILE = DATA_DIR / "spoff.log"

def _get_cache_dir() -> Path:
    if CACHE_DIR.parent != DATA_DIR:
        return DATA_DIR / CACHE_DIR.name
    return CACHE_DIR

def _get_config_file() -> Path:
    if CONFIG_FILE.parent != DATA_DIR:
        return DATA_DIR / CONFIG_FILE.name
    return CONFIG_FILE

def _get_playlists_file() -> Path:
    if PLAYLISTS_FILE.parent != DATA_DIR:
        return DATA_DIR / PLAYLISTS_FILE.name
    return PLAYLISTS_FILE

def _get_liked_songs_file() -> Path:
    if LIKED_SONGS_FILE.parent != DATA_DIR:
        return DATA_DIR / LIKED_SONGS_FILE.name
    return LIKED_SONGS_FILE

def _get_deleted_playlists_file() -> Path:
    if DELETED_PLAYLISTS_FILE.parent != DATA_DIR:
        return DATA_DIR / DELETED_PLAYLISTS_FILE.name
    return DELETED_PLAYLISTS_FILE

def _get_index_file() -> Path:
    if INDEX_FILE.parent != DATA_DIR:
        return DATA_DIR / INDEX_FILE.name
    return INDEX_FILE

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
        lock_path.parent.mkdir(parents=True, exist_ok=True)
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

def normalize_track(value: Any) -> Optional[Dict[str, Any]]:
    """Normalizes and validates a track dictionary to conform to the Spoff schema."""
    if not isinstance(value, dict):
        return None
    result = dict(value)
    raw_id = result.get("id")
    if raw_id is not None:
        result["id"] = str(raw_id)
    result["title"] = str(result.get("title") or "Unknown Track")
    result["artist"] = str(result.get("artist") or "Unknown Artist")
    try:
        dur_val = result.get("duration_ms")
        duration = float(dur_val if dur_val is not None else 0)
        if not math.isfinite(duration) or duration < 0:
            duration = 0
    except (TypeError, ValueError, OverflowError):
        duration = 0
    result["duration_ms"] = int(duration)
    return result

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
    playlists_f = _get_playlists_file()
    if not playlists_f.exists():
        try:
            _atomic_json_dump(playlists_f, [])
        except Exception as e:
            logger.error(f"Failed to create empty playlists file: {e}")

    liked_f = _get_liked_songs_file()
    if not liked_f.exists():
        try:
            _atomic_json_dump(liked_f, [])
        except Exception as e:
            logger.error(f"Failed to create empty liked songs file: {e}")

    index_f = _get_index_file()
    if not index_f.exists():
        try:
            _atomic_json_dump(index_f, {})
        except Exception as e:
            logger.error(f"Failed to create offline index: {e}")

_init_storage_once()

# Cache extensions supported by downloader and local playback
CACHE_EXTENSIONS = (".m4a", ".opus", ".mp3", ".webm", ".ogg", ".flac")

def stable_track_id(track: Dict[str, Any]) -> str:
    """Generates a deterministic persistent identifier for tracks lacking an upstream ID."""
    if isinstance(track, dict) and track.get("id"):
        return str(track["id"])
    if not isinstance(track, dict):
        return "local_" + hashlib.sha256(str(track).encode("utf-8")).hexdigest()[:24]
    title = track.get("title") or track.get("name") or ""
    artist = track.get("artist") or track.get("artists") or ""
    identity = [
        str(track.get("source") or ""),
        str(track.get("url") or ""),
        str(track.get("uri") or ""),
        str(title),
        str(artist),
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return "local_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

def validate_track_id(track_id: str) -> str:
    """Validates track_id against path traversal and special characters."""
    if not isinstance(track_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", track_id):
        raise ValueError(f"Invalid track ID: {track_id!r}")
    return track_id

def cache_path(track_id: str, suffix: str) -> Path:
    """Constructs a validated cache file path strictly inside CACHE_DIR."""
    val_id = validate_track_id(track_id)
    if suffix not in (*CACHE_EXTENSIONS, ".part"):
        raise ValueError("Unsupported cache suffix")
    root = _get_cache_dir().resolve()
    candidate = root / f"{val_id}{suffix}"
    if candidate.is_symlink():
        raise ValueError("Cache files must not be symbolic links")
    resolved = candidate.resolve()
    if resolved.parent != root:
        raise ValueError("Cache path escapes cache directory")
    return candidate

def load_config() -> Dict[str, Any]:
    cfg_file = _get_config_file()
    if not cfg_file.exists():
        return {}
    if cfg_file.stat().st_size == 0:
        corrupted = cfg_file.with_suffix(".json.corrupted")
        try:
            shutil.copy2(cfg_file, corrupted)
        except Exception:
            pass
        raise ValueError("Config file is empty/corrupt")
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            else:
                corrupted = cfg_file.with_suffix(".json.corrupted")
                try:
                    shutil.copy2(cfg_file, corrupted)
                except Exception:
                    pass
                raise ValueError("Config file is not a dictionary")
    except (OSError, ValueError) as e:
        logger.error(f"Error reading config: {e}")
        try:
            corrupted = cfg_file.with_suffix(".json.corrupted")
            if not corrupted.exists():
                shutil.copy2(cfg_file, corrupted)
        except Exception:
            pass
        raise

def save_config(config: Dict[str, Any]) -> None:
    _atomic_json_dump(_get_config_file(), config)

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

def get_saved_transparency_opacity() -> float:
    """Retrieves transparency opacity level (0.1 to 1.0), defaulting to 0.85."""
    try:
        cfg = load_config()
        val = float(cfg.get("transparency_opacity", 0.85))
        return max(0.1, min(1.0, val))
    except Exception:
        return 0.85

@transactional
def save_transparency_opacity(opacity: float):
    """Persists transparency opacity level to config."""
    try:
        cfg = load_config()
        cfg["transparency_opacity"] = max(0.1, min(1.0, float(opacity)))
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving transparency opacity: {e}")
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

def get_saved_notifications_enabled() -> bool:
    """Retrieves whether in-app notifications are enabled, defaulting to True."""
    try:
        cfg = load_config()
        return bool(cfg.get("notifications_enabled", True))
    except Exception as e:
        logger.error(f"Error reading notifications_enabled setting: {e}")
        return True

@transactional
def save_notifications_enabled(enabled: bool):
    """Persists notifications enabled setting to config."""
    try:
        cfg = load_config()
        cfg["notifications_enabled"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving notifications_enabled setting: {e}")
        raise

def get_saved_visualizer_enabled() -> bool:
    """Retrieves whether the CAVA visualizer is enabled, defaulting to True."""
    try:
        cfg = load_config()
        if "visualizer_enabled" in cfg:
            return bool(cfg["visualizer_enabled"])
        if cfg.get("visualizer_style") == "off":
            return False
    except Exception as e:
        logger.error(f"Error reading visualizer_enabled: {e}")
    return True

@transactional
def save_visualizer_enabled(enabled: bool) -> None:
    """Persists visualizer enabled state to config."""
    try:
        cfg = load_config()
        cfg["visualizer_enabled"] = bool(enabled)
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving visualizer_enabled: {e}")
        raise

def get_saved_visualizer_style() -> str:
    """Retrieves active visualizer style ('bars', 'braille', 'stereo', 'wave', 'dots', 'off'), defaulting to 'bars'."""
    try:
        cfg = load_config()
        style = cfg.get("visualizer_style", "bars")
        if style in ("bars", "braille", "stereo", "wave", "dots", "off"):
            return style
    except Exception as e:
        logger.error(f"Error reading visualizer style: {e}")
    return "bars"

@transactional
def save_visualizer_style(style: str):
    """Persists visualizer style choice to config."""
    try:
        cfg = load_config()
        if style in ("bars", "braille", "stereo", "wave", "dots", "off"):
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

def get_saved_last_tab() -> Optional[str]:
    """Retrieves the last opened tab name from config ('search', 'playlist', 'liked', 'offline', 'lyrics')."""
    try:
        cfg = load_config()
        val = cfg.get("last_tab")
        if isinstance(val, str) and val in ("search", "playlist", "liked", "offline", "lyrics"):
            return val
    except Exception as e:
        logger.error(f"Error reading last tab: {e}")
    return None

def get_saved_last_playlist_id() -> Optional[str]:
    """Retrieves the last active playlist ID from config."""
    try:
        cfg = load_config()
        val = cfg.get("last_playlist_id")
        if isinstance(val, str) and val.strip():
            return val.strip()
    except Exception as e:
        logger.error(f"Error reading last playlist ID: {e}")
    return None

@transactional
def save_last_tab(tab: str, playlist_id: Optional[str] = None) -> None:
    """Persists last active tab and optional playlist ID to config."""
    try:
        cfg = load_config()
        if tab in ("search", "playlist", "liked", "offline", "lyrics"):
            cfg["last_tab"] = tab
        if playlist_id is not None:
            clean_pid = str(playlist_id).strip()
            if clean_pid:
                cfg["last_playlist_id"] = clean_pid
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving last tab: {e}")

def get_saved_last_played() -> Dict[str, Any]:
    """Retrieves saved last played state from config."""
    try:
        cfg = load_config()
        val = cfg.get("last_played")
        if isinstance(val, dict):
            return val
    except Exception as e:
        logger.error(f"Error reading last played state: {e}")
    return {}

@transactional
def save_last_played(state: Dict[str, Any]) -> None:
    """Persists last played state to config."""
    try:
        cfg = load_config()
        clean_state: Dict[str, Any] = {}
        for k in ("playlist_id", "tab", "track_id", "track_title", "track_artist", "track_index"):
            if k in state:
                val = state[k]
                if isinstance(val, (str, int, float, bool)):
                    clean_state[k] = val
        cfg["last_played"] = clean_state
        save_config(cfg)
    except Exception as e:
        logger.error(f"Error saving last played state: {e}")
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


def load_liked_songs() -> List[Dict[str, Any]]:
    """Loads all tracks from dedicated liked_songs.json storage."""
    liked_f = _get_liked_songs_file()
    if not liked_f.exists():
        return []
    if liked_f.stat().st_size == 0:
        corrupted = liked_f.with_suffix(".json.corrupted")
        try:
            shutil.copy2(liked_f, corrupted)
        except Exception:
            pass
        raise ValueError("Liked songs file is empty/corrupt")
    try:
        with open(liked_f, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.error(f"Error reading liked songs: {e}")
        try:
            corrupted = liked_f.with_suffix(".json.corrupted")
            shutil.copy2(liked_f, corrupted)
            logger.warning(f"Corrupted liked songs backed up to {corrupted}")
        except Exception:
            pass
        raise
    if not isinstance(data, list) or not all(isinstance(t, dict) for t in data):
        try:
            corrupted = liked_f.with_suffix(".json.corrupted")
            shutil.copy2(liked_f, corrupted)
        except Exception:
            pass
        raise ValueError("Invalid Liked Songs document; preserving the file")
    normalized = []
    for t in data:
        norm = normalize_track(t)
        if norm is not None:
            normalized.append(norm)
    return normalized

def save_liked_songs(tracks: List[Dict[str, Any]]) -> None:
    """Atomically persists liked tracks to liked_songs.json."""
    clean_tracks = [normalize_track(t) for t in tracks if isinstance(t, dict) and normalize_track(t) is not None]
    _atomic_json_dump(_get_liked_songs_file(), clean_tracks)

def liked_index(tracks: List[Dict[str, Any]], target: Dict[str, Any]) -> Optional[int]:
    """Finds index of target in tracks by exact ID first, then title and artist, and _tracks_match."""
    if not isinstance(target, dict):
        return None
    track_id = target.get("id")
    if track_id:
        for i, item in enumerate(tracks):
            if isinstance(item, dict) and item.get("id") == track_id:
                return i
    title = str(target.get("title") or "").strip().casefold()
    artist = str(target.get("artist") or "").strip().casefold()
    if title and artist:
        for i, item in enumerate(tracks):
            if isinstance(item, dict) and (str(item.get("title") or "").strip().casefold() == title
                    and str(item.get("artist") or "").strip().casefold() == artist):
                return i
    for i, item in enumerate(tracks):
        if isinstance(item, dict) and _tracks_match(item, target):
            return i
    return None

@transactional
def add_track_to_liked_songs(track: Dict[str, Any]) -> bool:
    """Adds a track to Liked Songs if not already present. Returns True if added."""
    norm = normalize_track(track)
    if not norm:
        return False
    existing = load_liked_songs()
    if liked_index(existing, norm) is not None:
        return False
    existing.insert(0, norm)
    save_liked_songs(existing)
    logger.info(f"Added track to Liked Songs: {norm.get('title')}")
    return True

def is_track_liked(track: Dict[str, Any]) -> bool:
    """Checks whether a track is present in Liked Songs."""
    if not isinstance(track, dict):
        return False
    try:
        existing = load_liked_songs()
    except Exception:
        return False
    return liked_index(existing, track) is not None

@transactional
def remove_liked_track(track: Dict[str, Any]) -> bool:
    """Removes a track from Liked Songs using consistent ID/metadata matching. Returns True if removed."""
    if not isinstance(track, dict):
        return False
    tracks = load_liked_songs()
    index = liked_index(tracks, track)
    if index is None:
        return False
    tracks.pop(index)
    save_liked_songs(tracks)
    return True

@transactional
def remove_track_from_liked_songs(track_id_or_title: str, artist: Optional[str] = None) -> bool:
    """Legacy helper. Removes a track from Liked Songs. Returns True if removed."""
    if not track_id_or_title:
        return False
    tracks = load_liked_songs()
    target_id = str(track_id_or_title).strip()
    # First pass: exact ID match
    for i, t in enumerate(tracks):
        if t.get("id") == target_id:
            tracks.pop(i)
            save_liked_songs(tracks)
            return True
    # Second pass: if no track matched by ID, treat as title
    target_title = target_id.casefold()
    target_artist = str(artist).strip().casefold() if artist else None
    for i, t in enumerate(tracks):
        t_title = str(t.get("title") or "").strip().casefold()
        if t_title == target_title:
            if target_artist is None or str(t.get("artist") or "").strip().casefold() == target_artist:
                tracks.pop(i)
                save_liked_songs(tracks)
                return True
    return False

@transactional
def move_liked_track(track: Dict[str, Any], delta: int, index: Optional[int] = None) -> bool:
    """Moves a track up (delta=-1) or down (delta=1) in Liked Songs atomically."""
    tracks = load_liked_songs()
    target_idx = None
    if index is not None and 0 <= index < len(tracks):
        cand = tracks[index]
        if liked_index([cand], track) is not None:
            target_idx = index
    if target_idx is None:
        target_idx = liked_index(tracks, track)
    if target_idx is None or not (0 <= target_idx + delta < len(tracks)):
        return False
    tracks.insert(target_idx + delta, tracks.pop(target_idx))
    save_liked_songs(tracks)
    return True

@transactional
def load_saved_playlists() -> List[Dict[str, Any]]:
    pl_file = _get_playlists_file()
    if not pl_file.exists():
        return []
    if pl_file.stat().st_size == 0:
        corrupted = pl_file.with_suffix(".json.corrupted")
        try:
            shutil.copy2(pl_file, corrupted)
        except Exception:
            pass
        raise ValueError("Playlists file is empty/corrupt")
    try:
        with open(pl_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.error(f"Error reading playlists: {e}")
        try:
            corrupted = pl_file.with_suffix(".json.corrupted")
            shutil.copy2(pl_file, corrupted)
            logger.warning(f"Corrupted playlists backed up to {corrupted}")
        except Exception:
            pass
        raise
    if not isinstance(data, list):
        try:
            corrupted = pl_file.with_suffix(".json.corrupted")
            shutil.copy2(pl_file, corrupted)
        except Exception:
            pass
        raise ValueError("Playlists document is not a list")

    if any(not isinstance(p, dict) for p in data):
        try:
            corrupted = pl_file.with_suffix(".json.corrupted")
            if not corrupted.exists():
                shutil.copy2(pl_file, corrupted)
        except Exception:
            pass

    valid_playlists = []
    migrated_liked = []
    legacy_containers = []
    had_liked = False
    abort_migration = False
    for p in data:
        if not isinstance(p, dict):
            continue
        if p.get("id") == "spotify_liked_songs":
            had_liked = True
            legacy_containers.append(p)
            legacy = p.get("tracks", [])
            if not isinstance(legacy, list) or not all(isinstance(t, dict) for t in legacy):
                abort_migration = True
                continue
            migrated_liked.extend([t for t in legacy if isinstance(t, dict)])
            continue
        if "id" not in p:
            p["id"] = f"pl_{uuid.uuid4().hex[:8]}"
        if "tracks" not in p or not isinstance(p["tracks"], list):
            p["tracks"] = []
        else:
            p["tracks"] = [normalize_track(t) for t in p["tracks"] if isinstance(t, dict) and normalize_track(t) is not None]
        valid_playlists.append(p)

    if abort_migration:
        valid_playlists.extend(legacy_containers)
        try:
            corrupted = pl_file.with_suffix(".json.corrupted")
            if not corrupted.exists():
                shutil.copy2(pl_file, corrupted)
        except Exception:
            pass
        return valid_playlists

    if had_liked:
        merged = load_liked_songs()
        for track in migrated_liked:
            norm = normalize_track(track)
            if norm and liked_index(merged, norm) is None:
                merged.append(norm)
        save_liked_songs(merged)
        save_saved_playlists(valid_playlists)
    return valid_playlists

def save_saved_playlists(playlists: List[Dict[str, Any]]) -> None:
    _atomic_json_dump(_get_playlists_file(), playlists)

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

def get_track_index_in_playlist(playlist: Dict[str, Any], track: Dict[str, Any]) -> Optional[int]:
    """Returns 0-based index of track in playlist if present, else None."""
    if not isinstance(playlist, dict) or not isinstance(track, dict):
        return None
    tracks = playlist.get("tracks", [])
    if not isinstance(tracks, list):
        return None
    idx = liked_index(tracks, track)
    if idx is not None:
        return idx
    t_id = track.get("id")
    t_title = str(track.get("title") or "").strip().casefold()
    t_artist = str(track.get("artist") or "").strip().casefold()
    for i, t in enumerate(tracks):
        if not isinstance(t, dict):
            continue
        if t_id and t.get("id") and t.get("id") == t_id:
            return i
        if t_title and str(t.get("title") or "").strip().casefold() == t_title:
            if not t_artist or not str(t.get("artist") or "").strip().casefold() or str(t.get("artist") or "").strip().casefold() == t_artist:
                return i
    return None

def is_track_in_playlist(playlist: Dict[str, Any], track: Dict[str, Any]) -> bool:
    """Checks whether a track is present in a playlist by ID or case-insensitive title and artist."""
    return get_track_index_in_playlist(playlist, track) is not None

@transactional
def add_track_to_playlist(playlist_id: str, track: Dict[str, Any], allow_duplicate: bool = False) -> bool:
    if not playlist_id or not isinstance(track, dict):
        return False
    existing = load_saved_playlists()
    for p in existing:
        if p.get("id") == playlist_id:
            if "tracks" not in p or not isinstance(p["tracks"], list):
                p["tracks"] = []
            if not allow_duplicate and is_track_in_playlist(p, track):
                return False
            for t in p["tracks"]:
                if not allow_duplicate:
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
def move_playlist_track(playlist_id: str, track: Dict[str, Any], delta: int, index: Optional[int] = None) -> bool:
    """Moves a track up (delta=-1) or down (delta=1) in a playlist atomically."""
    existing = load_saved_playlists()
    for p in existing:
        if p.get("id") == playlist_id:
            tracks = p.get("tracks", [])
            target_idx = None
            if index is not None and 0 <= index < len(tracks):
                cand = tracks[index]
                if liked_index([cand], track) is not None:
                    target_idx = index
            if target_idx is None:
                target_idx = liked_index(tracks, track)
            if target_idx is None or not (0 <= target_idx + delta < len(tracks)):
                return False
            tracks.insert(target_idx + delta, tracks.pop(target_idx))
            p["tracks"] = tracks
            save_saved_playlists(existing)
            return True
    return False

@transactional
def remove_track_from_playlist_by_index_or_track(playlist_id: str, track: Dict[str, Any], index: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Removes a track from a playlist atomically by index or identity."""
    existing = load_saved_playlists()
    for p in existing:
        if p.get("id") == playlist_id:
            tracks = p.get("tracks", [])
            target_idx = None
            if index is not None and 0 <= index < len(tracks):
                cand = tracks[index]
                if liked_index([cand], track) is not None:
                    target_idx = index
            if target_idx is None:
                target_idx = liked_index(tracks, track)
            if target_idx is not None and 0 <= target_idx < len(tracks):
                removed = tracks.pop(target_idx)
                p["tracks"] = tracks
                save_saved_playlists(existing)
                return removed
    return None

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

def get_deleted_spotify_playlist_ids() -> set:
    """Returns set of Spotify playlist IDs that were deleted by the user."""
    try:
        del_f = _get_deleted_playlists_file()
        if del_f.exists() and del_f.stat().st_size > 0:
            with open(del_f, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {str(x).strip() for x in data if x}
    except Exception as e:
        logger.error(f"Error loading deleted Spotify playlists: {e}")
    return set()

@transactional
def record_deleted_spotify_playlist_id(spotify_id: str) -> None:
    """Records a Spotify playlist ID as deleted to prevent resurrection on sync."""
    if not spotify_id or not isinstance(spotify_id, str):
        return
    clean_id = spotify_id.strip()
    if not clean_id:
        return
    existing = get_deleted_spotify_playlist_ids()
    existing.add(clean_id)
    _atomic_json_dump(_get_deleted_playlists_file(), sorted(list(existing)))
    logger.info(f"Recorded deleted Spotify playlist tombstone: {clean_id}")

@transactional
def remove_deleted_spotify_playlist_id(spotify_id: str) -> None:
    """Removes a Spotify playlist ID from the tombstone set (e.g. if user re-imports it)."""
    if not spotify_id or not isinstance(spotify_id, str):
        return
    clean_id = spotify_id.strip()
    if not clean_id:
        return
    existing = get_deleted_spotify_playlist_ids()
    if clean_id in existing:
        existing.remove(clean_id)
        _atomic_json_dump(_get_deleted_playlists_file(), sorted(list(existing)))
        logger.info(f"Removed Spotify playlist tombstone: {clean_id}")

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
    Supports regular saved playlists as well as Liked Songs.
    """
    if not playlist_id:
        return None

    existing = load_saved_playlists()
    new_pid = f"local_{uuid.uuid4().hex[:8]}"

    # Handle cloning Liked Songs
    if playlist_id in ("liked_songs", "spotify_liked_songs") or playlist_id.lower() == "liked songs":
        orig_name = "Liked Songs"
        c_name = new_name.strip() if new_name and new_name.strip() else f"{orig_name} (Copy)"
        raw_tracks = load_liked_songs()
        cloned_tracks = [dict(t) for t in raw_tracks if isinstance(t, dict)]
        cloned_playlist = {
            "id": new_pid,
            "name": c_name,
            "url": "",
            "tracks": cloned_tracks,
        }
        existing.insert(0, cloned_playlist)
        save_saved_playlists(existing)
        logger.info(f"Cloned Liked Songs to '{c_name}' (id: {new_pid}, {len(cloned_tracks)} tracks)")
        return cloned_playlist

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

@transactional
def mutate_playlist(playlist_id: str, mutate: Callable[[Dict[str, Any]], None]) -> bool:
    """Atomically loads, mutates, and saves a playlist by ID."""
    playlists = load_saved_playlists()
    for playlist in playlists:
        if playlist.get("id") == playlist_id:
            mutate(playlist)
            save_saved_playlists(playlists)
            return True
    return False

def merge_track_artwork(playlist_id: str, track_id: str, artwork: Dict[str, Any]) -> bool:
    """Safely merges resolved artwork URLs into a track in a saved playlist or liked songs."""
    if playlist_id in ("liked", "spotify_liked_songs", "liked_songs"):
        with storage_transaction():
            liked = load_liked_songs()
            modified = False
            for track in liked:
                if isinstance(track, dict) and track.get("id") == track_id:
                    for key in ("art_url", "artist_art_url", "album_art_url"):
                        if artwork.get(key) and not track.get(key):
                            track[key] = artwork[key]
                            modified = True
            if modified:
                save_liked_songs(liked)
            return modified

    def merge(playlist: Dict[str, Any]) -> None:
        for track in playlist.get("tracks", []):
            if isinstance(track, dict) and track.get("id") == track_id:
                for key in ("art_url", "artist_art_url", "album_art_url"):
                    if artwork.get(key) and not track.get(key):
                        track[key] = artwork[key]
    return mutate_playlist(playlist_id, merge)


def _reconcile_offline_cache(index: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """Scans CACHE_DIR to ensure all physically cached audio files are represented in the offline index."""
    changed = False
    try:
        cache_dir = _get_cache_dir()
        if not cache_dir.is_dir():
            cache_dir.mkdir(parents=True, exist_ok=True)

        available_ids = set()
        for p in cache_dir.iterdir():
            try:
                if not p.is_file() or p.is_symlink():
                    continue
                if p.suffix.lower() not in CACHE_EXTENSIONS or p.stat().st_size == 0:
                    continue
                val_id = p.stem
                validate_track_id(val_id)
                available_ids.add(val_id)
            except (ValueError, OSError):
                continue

        # Prune index entries whose audio file was removed from disk
        for track_id, entry in list(index.items()):
            fp = entry.get("filepath")
            if fp:
                try:
                    p_file = Path(fp)
                    if p_file.stat().st_size == 0:
                        del index[track_id]
                        changed = True
                except FileNotFoundError:
                    del index[track_id]
                    changed = True
                except OSError:
                    # Permission/transient I/O failures do not prove deletion.
                    continue
            elif track_id not in available_ids and (entry.get("is_offline") or "path" in entry):
                del index[track_id]
                changed = True

        known_meta: Optional[Dict[str, Dict[str, Any]]] = None

        for p in cache_dir.iterdir():
            try:
                if not p.is_file() or p.is_symlink():
                    continue
                if p.suffix.lower() not in CACHE_EXTENSIONS or p.stat().st_size == 0:
                    continue
                val_id = p.stem
                validate_track_id(val_id)
            except (ValueError, OSError):
                continue

            if val_id not in index:
                if known_meta is None:
                    known_meta = {}
                    try:
                        for pl in load_saved_playlists():
                            for t in pl.get("tracks", []):
                                if t.get("id"):
                                    known_meta[t["id"]] = t
                        for t in load_liked_songs():
                            if t.get("id") and t["id"] not in known_meta:
                                known_meta[t["id"]] = t
                    except Exception:
                        pass

                m = known_meta.get(val_id, {})
                title = m.get("title") or f"Offline Track ({val_id[:11]})"
                artist = m.get("artist") or "Offline Library"
                dur = m.get("duration_ms") or 0
                entry = dict(m)
                entry.update({
                    "id": val_id,
                    "title": str(title),
                    "artist": str(artist),
                    "duration_ms": int(dur),
                    "filepath": str(p.resolve()),
                    "size_bytes": p.stat().st_size,
                    "is_offline": True,
                })
                index[val_id] = entry
                changed = True
            else:
                entry = index[val_id]
                resolved_p = str(p.resolve())
                if entry.get("filepath") != resolved_p:
                    entry["filepath"] = resolved_p
                    changed = True
                if not entry.get("size_bytes"):
                    entry["size_bytes"] = p.stat().st_size
                    changed = True
    except Exception as e:
        logger.debug(f"Cache reconciliation error: {e}")
    return index, changed

@transactional
def load_offline_index() -> Dict[str, Dict[str, Any]]:
    valid_index: Dict[str, Dict[str, Any]] = {}
    try:
        idx_file = _get_index_file()
        if idx_file.exists() and idx_file.stat().st_size > 0:
            with open(idx_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    try:
                        corrupted = idx_file.with_suffix(".json.corrupted")
                        shutil.copy2(idx_file, corrupted)
                        logger.warning(f"Non-dict offline index backed up to {corrupted}")
                    except Exception:
                        pass
                    data = {}
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, dict):
                        normalized = normalize_track(v)
                        if normalized is not None:
                            normalized["id"] = k
                            valid_index[k] = normalized
    except Exception as e:
        logger.error(f"Error reading offline index: {e}")
        try:
            idx_file = _get_index_file()
            if idx_file.exists() and idx_file.stat().st_size > 0:
                corrupted = idx_file.with_suffix(".json.corrupted")
                shutil.copy2(idx_file, corrupted)
                logger.warning(f"Corrupted offline index backed up to {corrupted}")
        except Exception:
            pass

    valid_index, changed = _reconcile_offline_cache(valid_index)
    if changed:
        try:
            save_offline_index(valid_index)
        except Exception:
            pass

    return valid_index

def save_offline_index(index: Dict[str, Dict[str, Any]]) -> None:
    _atomic_json_dump(_get_index_file(), index)

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
            if track_path.is_file() and track_path.stat().st_size > 0:
                return track_path
        except (FileNotFoundError, ValueError, OSError):
            continue
    return None

@transactional
def register_cached_track(track_id: str, meta: Dict[str, Any], filepath: Path):
    val_id = validate_track_id(track_id)
    filepath = Path(filepath)
    if not filepath.is_file() or filepath.stat().st_size == 0:
        raise ValueError(f"Incomplete cached audio file: {filepath}")
    index = load_offline_index()
    try:
        dur_ms = int(float(meta.get("duration_ms") or 0))
    except (ValueError, TypeError):
        dur_ms = 0
    previous = index.get(val_id, {})
    index[val_id] = {
        "id": val_id,
        "title": meta.get("title", "Unknown"),
        "artist": meta.get("artist", "Unknown"),
        "duration_ms": dur_ms,
        "filepath": str(filepath.resolve()),
        "size_bytes": filepath.stat().st_size
    }
    # Keep source identity when an offline track is played or downloaded again.
    for key in ("url", "uri", "source", "album", "art_url", "thumbnail", "resolved_url", "resolved_title"):
        value = meta.get(key) or previous.get(key)
        if isinstance(value, str) and value:
            index[val_id][key] = value
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

    root = _get_cache_dir().resolve()
    for ext in (*CACHE_EXTENSIONS, ".part"):
        candidate = root / f"{val_id}{ext}"
        try:
            if candidate.is_symlink():
                candidate.unlink(missing_ok=True)
                removed = True
                continue
            if candidate.exists() and candidate.is_file():
                candidate.unlink()
                removed = True
                logger.info(f"Deleted cache file: {candidate}")
        except Exception as e:
            logger.error(f"Failed to delete cache file {candidate}: {e}")
    return removed

@transactional
def quarantine_cached_track(track_id: str, source: Optional[Any] = None) -> bool:
    """Quarantines corrupt audio file and removes it from offline registry if source is in cache."""
    if not track_id:
        return False
    if source is not None and urlsplit(str(source)).scheme:
        return False
    try:
        val_id = validate_track_id(track_id)
    except ValueError:
        return False

    if source is None:
        idx = load_offline_index()
        if val_id in idx:
            entry = idx[val_id]
            source = entry.get("filepath") or entry.get("file")
        if not source:
            source = get_cached_track_path(val_id)

    if not source:
        return False

    try:
        source_path = Path(source).resolve()
        root = _get_cache_dir().resolve()
        source_path.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return False

    removed = False
    try:
        if source_path.is_file() or source_path.is_symlink():
            source_path.unlink(missing_ok=True)
            removed = True
    except Exception as e:
        logger.warning(f"Could not delete corrupt cache file {source_path}: {e}")

    index = load_offline_index()
    if val_id in index:
        del index[val_id]
        save_offline_index(index)
        removed = True

    return removed

