import sys
import os
import re
import signal
import subprocess 
import shutil
import base64
import urllib.parse

if "TEXTUAL_FPS" not in os.environ:
    os.environ["TEXTUAL_FPS"] = "60"

import time
import logging
import threading
import atexit
import secrets
import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Tuple, TypeVar
import random

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual import events, work
from textual.css.query import NoMatches
from textual.app import App, ComposeResult, ScreenStackError
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static, Input, DataTable, ProgressBar, Button
from textual.coordinate import Coordinate
from textual.geometry import Offset
from textual.binding import Binding

_ScreenResultType = TypeVar("_ScreenResultType")

class SafeModalScreen(ModalScreen[_ScreenResultType]):
    """Modal screen with idempotent dismiss to prevent screen stack corruption."""
    def dismiss(self, result: Optional[_ScreenResultType] = None) -> None:
        if getattr(self, "_dismissed", False):
            return
        self._dismissed = True
        try:
            super().dismiss(result)
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:
        if event.key in ("return", "ctrl+m"):
            event.prevent_default()
            event.stop()
            self.post_message(events.Key(key="enter", character="\r"))

try:
    from .spotify import fetch_spotify_playlist, fetch_spotify_album, fetch_spotify_track, parse_spotify_url
    from .ytmusic import (
        fetch_ytmusic_playlist, fetch_ytmusic_album, fetch_ytmusic_track,
        parse_ytmusic_url
    )
    from .storage import (
        load_saved_playlists, add_saved_playlist, remove_saved_playlist,
        rename_saved_playlist, clone_saved_playlist, move_saved_playlist, merge_track_artwork,
        create_local_playlist, add_track_to_playlist,
        get_cached_track_path, load_offline_index, save_offline_index, register_cached_track, stable_track_id,
        delete_cached_track, is_first_launch, mark_first_launch_done,
        get_saved_volume, save_volume, get_saved_sidebar_width, save_sidebar_width,
        get_saved_advanced_mode, save_advanced_mode, get_saved_search_engine, save_search_engine,
        get_saved_transparency, save_transparency, get_saved_transparency_opacity, save_transparency_opacity,
        get_saved_instant_search, save_instant_search,
        get_saved_auto_update, save_auto_update,
        get_saved_notifications_enabled, save_notifications_enabled,
        get_saved_visualizer_style, save_visualizer_style, get_saved_visualizer_color, save_visualizer_color,
        get_saved_visualizer_enabled, save_visualizer_enabled,
        get_custom_keybindings, save_custom_keybindings, reset_custom_keybindings,
        load_eq_settings, save_eq_settings, remove_deleted_spotify_playlist_id,
        load_liked_songs, save_liked_songs, add_track_to_liked_songs, remove_track_from_liked_songs,
        is_track_liked, get_track_index_in_playlist, get_saved_last_played, save_last_played,
        get_saved_last_tab, get_saved_last_playlist_id, save_last_tab,
        remove_liked_track, move_liked_track, move_playlist_track,
        remove_track_from_playlist_by_index_or_track, quarantine_cached_track,
        record_deleted_spotify_playlist_id, liked_index, storage_transaction,
        update_playlist_details
    )
    from .streamer import search_and_resolve_stream, download_track_to_cache, invalidate_stream_cache, cached_audio_matches_duration
    from .search import live_search_tracks, resolve_direct_track_url
    from .player import MPVController
    from .eq import (
        FilterType, BUILTIN_PRESETS,
        ParametricEQEngine, SAMSUNG_AKG_REFERENCE_PRESET,
        format_gain_bar, render_curve,
        parse_equalizer_apo
    )
    from .auth import (
        load_spotify_auth, save_spotify_auth, logout_spotify, get_valid_token,
        generate_pkce_pair, build_auth_url, exchange_code_for_tokens,
        fetch_current_user_profile, sync_spotify_library, OAuthCallbackServer,
        SPOTIFY_PORT, add_track_to_spotify_account, remove_track_from_spotify_account,
        reorder_spotify_playlist_track, delete_spotify_playlist, rename_spotify_playlist, clone_spotify_playlist, has_modify_scopes,
        search_spotify_tracks, is_client_side_track, extract_spotify_playlist_id,
        fetch_liked_songs, merge_spotify_and_client_tracks, sync_playlist_tracks_to_spotify,
        apply_pending_unlikes, update_spotify_playlist_details
    )
    from .lyrics import fetch_lyrics, get_active_lyric_index
    from .mpris import MPRISService
    from .visualizer import VisualizerWidget, CavaVisualizer
    from .updater import check_for_updates, perform_update, run_cli_update, is_git_checkout
    from .art import resolve_track_artwork, get_cached_artwork
except ImportError:
    from spotify import fetch_spotify_playlist, fetch_spotify_album, fetch_spotify_track, parse_spotify_url
    from ytmusic import (
        fetch_ytmusic_playlist, fetch_ytmusic_album, fetch_ytmusic_track,
        parse_ytmusic_url
    )
    from storage import (
        load_saved_playlists, add_saved_playlist, remove_saved_playlist,
        rename_saved_playlist, clone_saved_playlist, move_saved_playlist, merge_track_artwork,
        create_local_playlist, add_track_to_playlist,
        get_cached_track_path, load_offline_index, save_offline_index, register_cached_track, stable_track_id,
        delete_cached_track, is_first_launch, mark_first_launch_done,
        get_saved_volume, save_volume, get_saved_sidebar_width, save_sidebar_width,
        get_saved_advanced_mode, save_advanced_mode, get_saved_search_engine, save_search_engine,
        get_saved_transparency, save_transparency, get_saved_transparency_opacity, save_transparency_opacity,
        get_saved_instant_search, save_instant_search,
        get_saved_auto_update, save_auto_update,
        get_saved_notifications_enabled, save_notifications_enabled,
        get_saved_visualizer_style, save_visualizer_style, get_saved_visualizer_color, save_visualizer_color,
        get_saved_visualizer_enabled, save_visualizer_enabled,
        get_custom_keybindings, save_custom_keybindings, reset_custom_keybindings,
        load_eq_settings, save_eq_settings, remove_deleted_spotify_playlist_id,
        load_liked_songs, save_liked_songs, add_track_to_liked_songs, remove_track_from_liked_songs,
        is_track_liked, get_track_index_in_playlist, get_saved_last_played, save_last_played,
        get_saved_last_tab, get_saved_last_playlist_id, save_last_tab,
        remove_liked_track, move_liked_track, move_playlist_track,
        remove_track_from_playlist_by_index_or_track, quarantine_cached_track,
        record_deleted_spotify_playlist_id, liked_index, storage_transaction,
        update_playlist_details
    )
    from streamer import search_and_resolve_stream, download_track_to_cache, invalidate_stream_cache, cached_audio_matches_duration
    from search import live_search_tracks, resolve_direct_track_url
    from player import MPVController
    from eq import (
        FilterType, BUILTIN_PRESETS,
        ParametricEQEngine, SAMSUNG_AKG_REFERENCE_PRESET,
        format_gain_bar, render_curve,
        parse_equalizer_apo
    )
    from auth import (
        load_spotify_auth, save_spotify_auth, logout_spotify, get_valid_token,
        generate_pkce_pair, build_auth_url, exchange_code_for_tokens,
        fetch_current_user_profile, sync_spotify_library, OAuthCallbackServer,
        SPOTIFY_PORT, add_track_to_spotify_account, remove_track_from_spotify_account,
        reorder_spotify_playlist_track, delete_spotify_playlist, rename_spotify_playlist, clone_spotify_playlist, has_modify_scopes,
        search_spotify_tracks, is_client_side_track, extract_spotify_playlist_id,
        fetch_liked_songs, merge_spotify_and_client_tracks, sync_playlist_tracks_to_spotify,
        apply_pending_unlikes, update_spotify_playlist_details
    )
    from lyrics import fetch_lyrics, get_active_lyric_index
    from mpris import MPRISService
    from visualizer import VisualizerWidget, CavaVisualizer
    from updater import check_for_updates, perform_update, run_cli_update, is_git_checkout
    from art import resolve_track_artwork, get_cached_artwork

logger = logging.getLogger("spoff")

__all__ = ["SpoffTUI", "remove_track_from_liked_songs"]

def format_frequency(hz: float) -> str:
    return f"{hz / 1000:.1f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"

def format_time(seconds: Any) -> str:
    if seconds is None:
        return "00:00"
    try:
        sec = int(float(seconds))
        if sec <= 0:
            return "00:00"
        m = sec // 60
        s = sec % 60
        return f"{m:02d}:{s:02d}"
    except (ValueError, TypeError, OverflowError):
        return "00:00"

def resolve_track_url(track: Dict[str, Any]) -> Tuple[str, str]:
    """
    Resolves the shareable URL and source service name for a track.
    Returns (share_url, service_name).
    """
    raw_url = str(track.get("url") or "").strip()
    raw_id = str(track.get("id") or "").strip()
    raw_uri = str(track.get("uri") or "").strip()
    raw_src = str(track.get("source") or "").lower()

    # 1. Spotify track
    if raw_uri.startswith("spotify:track:"):
        sp_id = raw_uri.split(":")[-1]
        return f"https://open.spotify.com/track/{sp_id}", "Spotify"
    if "open.spotify.com/track/" in raw_url:
        return raw_url, "Spotify"
    if raw_src == "spotify" and raw_id:
        return f"https://open.spotify.com/track/{raw_id}", "Spotify"
    sp_tr_id = str(track.get("spotify_id") or "").strip()
    if sp_tr_id and len(sp_tr_id) == 22 and sp_tr_id.isalnum():
        return f"https://open.spotify.com/track/{sp_tr_id}", "Spotify"
    if len(raw_id) == 22 and raw_id.isalnum() and not raw_url.startswith("http"):
        return f"https://open.spotify.com/track/{raw_id}", "Spotify"

    # 2. YouTube / YouTube Music track
    if "music.youtube.com" in raw_url or "youtube.com" in raw_url or "youtu.be" in raw_url:
        label = "YouTube Music" if "music.youtube" in raw_url else "YouTube"
        return raw_url, label
    if len(raw_id) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', raw_id):
        return f"https://music.youtube.com/watch?v={raw_id}", "YouTube Music"
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url, "Web"

    # 3. Fallback search query
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    clean_artist = "" if artist.lower() in ("unknown artist", "unknown", "none", "") else artist
    query = f"{title} {clean_artist}".strip() if clean_artist else title
    if query:
        encoded = urllib.parse.quote(query)
        if raw_src == "spotify":
            return f"https://open.spotify.com/search/{encoded}", "Spotify"
        return f"https://music.youtube.com/search?q={encoded}", "YouTube Music"

    return "", ""


def playback_direct_url(track: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(track, dict):
        return None
    if track.get("url"):
        return str(track["url"])
    identifier = str(track.get("id") or "")
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", identifier):
        return f"https://www.youtube.com/watch?v={identifier}"
    return None


def resolve_playlist_url(playlist: Dict[str, Any], default_engine: str = "ytmusic") -> Tuple[str, str]:
    """
    Resolves the shareable URL and source service name for a playlist.
    Returns (share_url, service_name).
    """
    if not playlist or not isinstance(playlist, dict):
        return "", ""

    raw_url = str(playlist.get("url") or "").strip()
    raw_id = str(playlist.get("id") or "").strip()
    raw_uri = str(playlist.get("uri") or "").strip()
    raw_src = str(playlist.get("source") or "").lower()
    name = str(playlist.get("name") or "").strip()
    sp_pl_id = str(playlist.get("spotify_id") or "").strip()

    # 1. Spotify playlist / album / liked songs
    if raw_id == "spotify_liked_songs" or (raw_src == "spotify" and name.lower() == "liked songs"):
        return "https://open.spotify.com/collection/tracks", "Spotify"
    if sp_pl_id and len(sp_pl_id) == 22 and sp_pl_id.isalnum():
        return f"https://open.spotify.com/playlist/{sp_pl_id}", "Spotify"
    if raw_uri.startswith("spotify:playlist:"):
        sp_id = raw_uri.split(":")[-1]
        return f"https://open.spotify.com/playlist/{sp_id}", "Spotify"
    if raw_uri.startswith("spotify:album:"):
        sp_id = raw_uri.split(":")[-1]
        return f"https://open.spotify.com/album/{sp_id}", "Spotify"
    if "open.spotify.com/playlist/" in raw_url:
        return raw_url, "Spotify"
    if "open.spotify.com/album/" in raw_url:
        return raw_url, "Spotify"
    if raw_src == "spotify" and raw_id and not raw_id.startswith("local_") and not raw_id.startswith("pl_"):
        return f"https://open.spotify.com/playlist/{raw_id}", "Spotify"
    if len(raw_id) == 22 and raw_id.isalnum() and not raw_id.startswith("local_") and not raw_id.startswith("pl_") and not raw_url.startswith("http"):
        return f"https://open.spotify.com/playlist/{raw_id}", "Spotify"


    # 2. YouTube Music / YouTube playlist
    if "music.youtube.com/playlist" in raw_url:
        return raw_url, "YouTube Music"
    if "youtube.com/playlist" in raw_url or "youtu.be" in raw_url:
        label = "YouTube Music" if "music.youtube" in raw_url else "YouTube"
        return raw_url, label
    if any(raw_id.startswith(p) for p in ("PL", "VL", "RD", "OLAK", "MPREb_")):
        return f"https://music.youtube.com/playlist?list={raw_id}", "YouTube Music"

    # 3. Any standard web URL attached to the playlist
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        label = "Spotify" if "spotify.com" in raw_url else ("YouTube Music" if "music.youtube" in raw_url else ("YouTube" if "youtube.com" in raw_url else "Web"))
        return raw_url, label

    # 4. Fallback search query for local playlists
    if name:
        encoded = urllib.parse.quote(name)
        if raw_src == "spotify" or default_engine == "spotify":
            return f"https://open.spotify.com/search/{encoded}", "Spotify"
        return f"https://music.youtube.com/search?q={encoded}", "YouTube Music"

    return "", ""


def copy_to_clipboard(text: str, app: Optional[Any] = None) -> bool:
    """
    Copies text to the system clipboard across Wayland (wl-copy), X11 (xclip/xsel),
    Textual's clipboard driver, and OSC 52 terminal escape sequence.
    """
    if not text:
        return False
    copied = False

    # 1. Textual App API
    if app and hasattr(app, "copy_to_clipboard"):
        try:
            app.copy_to_clipboard(text)
            copied = True
        except Exception:
            pass

    # 2. Wayland wl-copy (Fast & native in Hyprland / Wayland desktops)
    if shutil.which("wl-copy"):
        try:
            res = subprocess.run(
                ["wl-copy"],
                input=text,
                text=True,
                capture_output=True,
                timeout=1.0
            )
            if res.returncode == 0:
                copied = True
        except Exception:
            pass

    # 3. X11 xclip fallback
    if not copied and shutil.which("xclip"):
        try:
            res = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                text=True,
                capture_output=True,
                timeout=1.0
            )
            if res.returncode == 0:
                copied = True
        except Exception:
            pass

    # 4. X11 xsel fallback
    if not copied and shutil.which("xsel"):
        try:
            res = subprocess.run(
                ["xsel", "--clipboard", "--input"],
                input=text,
                text=True,
                capture_output=True,
                timeout=1.0
            )
            if res.returncode == 0:
                copied = True
        except Exception:
            pass

    # 5. OSC 52 escape sequence (Terminal emulator clipboard sync). Textual's
    # copy_to_clipboard already sends it through the driver; writing raw bytes
    # to stdout while the app owns the terminal can corrupt the display.
    if app is not None and hasattr(app, "copy_to_clipboard"):
        return copied
    try:
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        osc52 = f"\033]52;c;{b64}\a"
        sys.stdout.write(osc52)
        sys.stdout.flush()
        copied = True
    except Exception:
        pass

    return copied


def read_from_clipboard(app: Optional[Any] = None) -> Optional[str]:
    """
    Reads text from the system clipboard across Wayland (wl-paste) and X11 (xclip/xsel).
    """
    # 1. Wayland wl-paste
    if shutil.which("wl-paste"):
        try:
            res = subprocess.run(
                ["wl-paste", "-n"],
                capture_output=True,
                text=True,
                timeout=1.0
            )
            if res.returncode == 0 and res.stdout:
                stripped = res.stdout.strip()
                if stripped:
                    return stripped
        except Exception:
            pass

    # 2. X11 xclip
    if shutil.which("xclip"):
        try:
            res = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                timeout=1.0
            )
            if res.returncode == 0 and res.stdout:
                stripped = res.stdout.strip()
                if stripped:
                    return stripped
        except Exception:
            pass

    # 3. X11 xsel
    if shutil.which("xsel"):
        try:
            res = subprocess.run(
                ["xsel", "--clipboard", "--output"],
                capture_output=True,
                text=True,
                timeout=1.0
            )
            if res.returncode == 0 and res.stdout:
                stripped = res.stdout.strip()
                if stripped:
                    return stripped
        except Exception:
            pass

    return None


def escape_markup(text: str) -> str:
    """Escapes text safely for Textual markup rendering."""
    return str(text).replace("\\", "\\\\").replace("[", "\\[")


DEFAULT_KEYBINDINGS: Dict[str, str] = {
    "toggle_play": "space",
    "next_track": "n",
    "prev_track": "p",
    "share_track": "c",
    "seek_fwd": "right",
    "seek_bwd": "left",
    "cursor_up": "k",
    "cursor_down": "j",
    "vol_up": "f3",
    "vol_down": "f2",
    "vol_mute": "f1",
    "toggle_shuffle": "s",
    "toggle_repeat": "r",
    "focus_search": "slash",
    "download_offline": "b",
    "bulk_download_playlist": "B",
    "focus_bar": "",
    "focus_import": "i",
    "add_to_playlist": "a",
    "share_playlist": "y",
    "delete_item": "d",
    "delete_playlist": "D",
    "rename_playlist": "R",
    "clone_playlist": "Y",
    "playlist_settings": "S",
    "open_spotify_auth": "L",
    "nav_search": "1",
    "nav_playlist": "2",
    "nav_offline": "3",
    "nav_lyrics": "4",
    "nav_liked": "5",
    "find_in_view": "f",
    "open_settings": "comma",
    "show_help": "colon",
    "check_update": "u",
    "quit_app": "q",
    "focus_sidebar": "h",
    "focus_tracks": "right",
    "like_track": "l",
    "toggle_focus": "tab",
    "move_item_up": "K",
    "move_item_down": "J",
    "switch_engine": "ctrl+e",
    "toggle_visualizer": "v",
    "toggle_vis_on_off": "V",
    "cycle_vis_color": "C",
    "open_equalizer": "e",
    "toggle_eq_bypass": "E",
    "open_eq_settings": "alt+e",
    "toggle_notifications": "",
}

ACTION_INFO: Dict[str, Tuple[str, str]] = {
    "toggle_play": ("Playback", "Play / Pause (Space / F8)"),
    "next_track": ("Playback", "Next Track (n / F9)"),
    "prev_track": ("Playback", "Previous Track (p / F7)"),
    "share_track": ("Playback", "Copy Track Link / Share"),
    "seek_fwd": ("Playback", "Seek Forward (+5s)"),
    "seek_bwd": ("Playback", "Seek Backward (-5s)"),
    "cursor_up": ("Navigation", "Move Cursor Up (k)"),
    "cursor_down": ("Navigation", "Move Cursor Down (j)"),
    "vol_up": ("Volume", "Volume Up (F3)"),
    "vol_down": ("Volume", "Volume Down (F2)"),
    "vol_mute": ("Volume", "Mute / Unmute (F1)"),
    "toggle_shuffle": ("Playback", "Toggle Shuffle"),
    "toggle_repeat": ("Playback", "Cycle Repeat Mode"),
    "focus_search": ("Navigation", "Focus Search Bar"),
    "download_offline": ("Library", "Download Song / Playlist Offline (b)"),
    "bulk_download_playlist": ("Library", "Bulk Download Playlist Offline (B)"),
    "focus_bar": ("Playback", "Focus Seek Bar"),
    "focus_import": ("Playlists", "New Playlist / Import"),
    "add_to_playlist": ("Playlists", "Add Song to Playlist"),
    "like_track": ("Library", "Like / Unlike Song (l)"),
    "share_playlist": ("Playlists", "Copy Playlist Link / Share"),
    "delete_item": ("Playlists", "Delete Selected Item"),
    "delete_playlist": ("Playlists", "Delete Entire Playlist"),
    "rename_playlist": ("Playlists", "Rename Playlist (R)"),
    "clone_playlist": ("Playlists", "Clone / Copy Playlist (Y)"),
    "playlist_settings": ("Playlists", "Playlist Settings (S)"),
    "open_spotify_auth": ("Integrations", "Spotify Menu & Login"),
    "nav_search": ("Navigation", "Switch to Search"),
    "nav_playlist": ("Navigation", "Switch to Playlists"),
    "nav_offline": ("Navigation", "Switch to Offline"),
    "nav_lyrics": ("Navigation", "Synchronized Lyrics"),
    "nav_liked": ("Navigation", "Switch to Liked Songs"),
    "find_in_view": ("Navigation", "Find Track in Playlist / View (f)"),
    "switch_engine": ("Navigation", "Switch Search Engine (YTM/Spotify)"),
    "toggle_visualizer": ("Visualizer", "Cycle Visualizer Style (v)"),
    "toggle_vis_on_off": ("Visualizer", "Toggle Visualizer On / Off (Shift+V)"),
    "cycle_vis_color": ("Visualizer", "Cycle Visualizer Color (C)"),
    "open_equalizer": ("Audio", "Parametric Equalizer (e)"),
    "toggle_eq_bypass": ("Audio", "Toggle EQ Bypass / A-B (E)"),
    "open_eq_settings": ("Audio", "EQ & DSP Settings (Alt+e)"),
    "open_settings": ("General", "Settings & Keybinds"),
    "show_help": ("General", "Help & Reference"),
    "check_update": ("General", "Check for Updates"),
    "quit_app": ("General", "Quit Spoff"),
    "focus_sidebar": ("Navigation", "Focus Sidebar (h)"),
    "focus_tracks": ("Navigation", "Focus Main Table (Right)"),
    "toggle_focus": ("Navigation", "Cycle Sidebar / Main"),
    "move_item_up": ("Playlists", "Reorder Song Up (K)"),
    "move_item_down": ("Playlists", "Reorder Song Down (J)"),
    "toggle_notifications": ("General", "Toggle Notifications (On/Off)"),
}

def canonicalize_key(k: str) -> str:
    """Normalizes any user-typed or string-represented key into a canonical form."""
    if not k:
        return ""
    s = k.strip()
    if len(s) == 1:
        return s
    s_lower = s.lower()
    for sep in ("+", "-"):
        if sep in s_lower and len(s_lower) > 1:
            parts = [p.strip() for p in s_lower.split(sep) if p.strip()]
            norm_parts = []
            for p in parts:
                if p == "fn":
                    continue
                elif p in ("cntrl", "control"): norm_parts.append("ctrl")
                elif p in ("opt", "option"): norm_parts.append("alt")
                elif p in ("cmd", "command"): norm_parts.append("ctrl")
                elif p == "esc": norm_parts.append("escape")
                elif p == "return": norm_parts.append("enter")
                elif p in ("del", "delete"): norm_parts.append("delete")
                elif p == "one": norm_parts.append("1")
                elif p == "two": norm_parts.append("2")
                elif p == "three": norm_parts.append("3")
                elif p == "four": norm_parts.append("4")
                elif p == "five": norm_parts.append("5")
                elif p == "six": norm_parts.append("6")
                elif p == "seven": norm_parts.append("7")
                elif p == "eight": norm_parts.append("8")
                elif p == "nine": norm_parts.append("9")
                elif p == "zero": norm_parts.append("0")
                else: norm_parts.append(p)
            return "+".join(norm_parts)
    if s_lower in ("cntrl", "control"): return "ctrl"
    if s_lower == "esc": return "escape"
    if s_lower == "return": return "enter"
    if s_lower in ("del", "delete"): return "delete"
    return s_lower

def format_key_display(k: str) -> str:
    if not k:
        return "[dim]Unbound[/dim]"
    special_labels = {
        "space": "Space",
        "slash": "/",
        "comma": ",",
        "colon": ":",
        "semicolon": ";",
        "question_mark": "?",
        "plus": "+",
        "minus": "-",
        "escape": "Esc",
        "delete": "Del",
        "del": "Del",
        "backspace": "Backspace",
        "enter": "Enter",
        "tab": "Tab",
        "up": "↑ Up",
        "down": "↓ Down",
        "left": "← Left",
        "right": "→ Right",
    }
    parts = k.split("+")
    res = []
    for p in parts:
        lower = p.lower()
        if lower in special_labels:
            res.append(special_labels[lower])
        elif len(p) == 1:
            res.append(p.upper() if len(parts) > 1 else p)
        elif lower.startswith("f") and lower[1:].isdigit():
            res.append(lower.upper())
        else:
            res.append(p.capitalize())
    return "+".join(res)

def normalize_captured_key(event_key: str, event_char: Optional[str]) -> str:
    ek = str(event_key or "").strip()
    ec = str(event_char or "")
    ek_lower = ek.lower()

    # Ignore lone modifier keys waiting for combinations
    if ek_lower in ("ctrl", "control", "shift", "alt", "meta", "super", "hyper", "fn"):
        return ""

    # Audio / Hardware media keys
    if ek_lower in ("audio_play", "audio_pause", "mediaplaypause"):
        return "f8"
    if ek_lower in ("audio_next", "medianexttrack"):
        return "f9"
    if ek_lower in ("audio_prev", "mediaprevioustrack"):
        return "f7"
    if ek_lower == "audio_mute":
        return "f1"
    if ek_lower == "audio_lower_volume":
        return "f2"
    if ek_lower == "audio_raise_volume":
        return "f3"

    # 1. Modifiers combinations (ctrl+1, ctrl+shift+a, alt+up, shift+tab)
    if "+" in ek_lower or ek_lower.startswith("ctrl+") or ek_lower.startswith("alt+"):
        parts = [p.strip() for p in ek_lower.split("+") if p.strip()]
        parts = [p for p in parts if p != "fn"]
        if not parts:
            return "fn"
        # Shift + single letter -> uppercase letter
        if len(parts) == 2 and parts[0] == "shift" and len(parts[1]) == 1 and parts[1].isalpha():
            return parts[1].upper()
        if ek_lower == "shift+semicolon": return ":"
        if ek_lower == "shift+slash": return "?"
        if ek_lower == "shift+equal": return "+"
        if ek_lower == "ctrl+@": return "ctrl+space"
        return "+".join(parts)

    # 2. Named special keys
    named_keys = {
        "space", "enter", "tab", "escape", "backspace", "delete", "del",
        "up", "down", "left", "right",
        "home", "end", "pageup", "pagedown",
    }
    if ek_lower in named_keys:
        if ek_lower in ("del", "delete"):
            return "delete"
        return ek_lower

    # 3. Function keys
    if ek_lower.startswith("f") and ek_lower[1:].isdigit():
        return ek_lower

    # 4. Punctuation symbols
    if ec in (",", "/", ":", ";", "?", "+", "-"):
        return ec
    if ek_lower == "slash": return "/"
    if ek_lower == "comma": return ","
    if ek_lower == "colon": return ":"
    if ek_lower == "semicolon": return ";"
    if ek_lower == "question_mark": return "?"

    # 5. Printable single characters
    if ec and len(ec) == 1 and ec.isprintable() and not ec.isspace():
        return ec

    if len(ek) == 1:
        return ek

    return ek_lower

def key_matches(event_key: str, event_char: Optional[str], bound_key: str) -> bool:
    if not bound_key:
        return False
    c_bound = canonicalize_key(bound_key)
    if not c_bound:
        return False
    ek = str(event_key or "").strip()
    ec = str(event_char or "")
    c_ek = canonicalize_key(ek)

    # 1. Exact canonical match
    if c_ek == c_bound:
        return True

    # 2. Single uppercase letter bound (e.g. 'D', 'K', 'J')
    if len(bound_key) == 1 and bound_key.isupper():
        if ec == bound_key or ek == bound_key or ek == f"shift+{bound_key.lower()}" or c_ek == f"shift+{bound_key.lower()}":
            return True
        return False

    # 3. Single lowercase letter bound (e.g. 'd', 'k', 'j')
    if len(bound_key) == 1 and bound_key.islower():
        if (ec == bound_key or ek == bound_key) and (not ec or not ec.isupper()):
            return True
        return False

    # 4. Punctuation aliases (checked before generic + combos so shift+semicolon / shift+slash resolve properly)
    punct_map = {
        "comma": ",", ",": "comma",
        "slash": "/", "/": "slash",
        "colon": ":", ":": "colon",
        "semicolon": ";", ";": "semicolon",
        "question_mark": "?", "?": "question_mark",
        "plus": "+", "+": "plus",
        "minus": "-", "-": "minus",
    }
    if c_bound in punct_map:
        target = punct_map[c_bound]
        if ek == target or ec == target or c_ek == target:
            return True
    if c_bound in (":", "colon") and (ek in ("colon", ":", "shift+semicolon") or ec == ":"):
        return True
    if c_bound in ("?", "question_mark") and (ek in ("question_mark", "?", "shift+slash") or ec == "?"):
        return True
    if c_bound in ("+", "plus") and (ek in ("plus", "+", "shift+equal") or ec == "+"):
        return True
    if c_bound in ("space", " ") and (ek == "space" or ec == " "):
        return True

    # 5. Multi-key / modifier combos
    if "+" in c_bound or "+" in c_ek:
        if c_ek.lower() == c_bound.lower():
            return True
        if c_bound in ("ctrl+space", "ctrl+@") and c_ek in ("ctrl+space", "ctrl+@"):
            return True
        return False

    return ek.lower() == bound_key.lower() or ec == bound_key

class AdvModeToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_advanced_mode()

class NotificationsToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_notifications()

class TransparencyToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_transparency()

class InstantSearchToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_instant_search()

class AutoUpdateToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_auto_update()

class SearchEngineToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_search_engine()

class VisualizerToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_visualizer()

class VisualizerStyleToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.cycle_visualizer_style()

class VisualizerColorToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.cycle_visualizer_color()

class EQSettingsNavToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.open_eq_settings()

class KeyCaptureBox(Static):
    can_focus = True


class RebindKeyModal(SafeModalScreen[Optional[str]]):
    def __init__(
        self,
        action_id: str,
        action_category: str,
        action_title: str,
        current_key: str,
        default_key: str,
        existing_bindings: Dict[str, str],
    ):
        super().__init__()
        self.action_id = action_id
        self.action_category = action_category
        self.action_title = action_title
        self.current_key = current_key
        self.default_key = default_key
        self.existing_bindings = existing_bindings
        self.selected_key = current_key

    def compose(self) -> ComposeResult:
        cur_disp = format_key_display(self.current_key) if self.current_key else "[dim]Unbound[/dim]"
        def_disp = format_key_display(self.default_key) if self.default_key else "[dim]None[/dim]"
        with Vertical(id="rebind-dialog"):
            with Horizontal(id="rebind-header"):
                yield Static("REBIND SHORTCUT", id="rebind-title")
                yield Static("[dim]Esc to unbind  |  Ctrl+C to cancel[/dim]", id="rebind-close-hint")

            yield Static(f"Action: [#ffffff]{escape(str(self.action_title))}[/]  [dim]({escape(str(self.action_category))})[/dim]", id="rebind-action-info")
            yield Static(f"Current: [#ffffff]{cur_disp}[/]   [#444444]•[/]   Default: [dim]{def_disp}[/dim]", id="rebind-curr-info")

            with Vertical(id="rebind-capture-container"):
                yield KeyCaptureBox(
                    "[#ffffff]Listening for keypress...[/]\n[#767676]Press any key, function key, or combo[/]",
                    id="rebind-capture-box"
                )
                yield Static(f"[dim]Current:[/] [#ffffff]{cur_disp}[/]", id="rebind-key-display")
                yield Static("[#767676]Press Enter to save, Esc to unbind, or press a new key[/]", id="rebind-conflict-warning")

            with Horizontal(id="rebind-buttons"):
                yield Button("Save [Enter]", id="rebind-btn-save")
                yield Button("Unbind [Esc]", id="rebind-btn-unbind")
                yield Button("Reset Default", id="rebind-btn-default")
                yield Button("Cancel", id="rebind-btn-cancel")

    def on_mount(self) -> None:
        try:
            box = self.query_one("#rebind-capture-box", KeyCaptureBox)
            box.focus()
        except Exception:
            pass

    def _update_preview(self, val: str) -> None:
        c_key = canonicalize_key(val) if val else ""
        disp = format_key_display(c_key) if c_key else "[dim]Unbound[/dim]"

        try:
            self.query_one("#rebind-capture-box", KeyCaptureBox).update(
                "[#ffffff]Key detected[/]\n[#767676]Press Enter to save, Esc to unbind, or press another key[/]"
            )
            self.query_one("#rebind-key-display", Static).update(
                f"[dim]Captured:[/] [#ffffff]{disp}[/]"
            )

            conflicting_act = None
            if c_key:
                for other_id, bound in self.existing_bindings.items():
                    if other_id != self.action_id and canonicalize_key(bound) == c_key:
                        conflicting_act = other_id
                        break

            warning_lbl = self.query_one("#rebind-conflict-warning", Static)
            if conflicting_act:
                _, conf_title = ACTION_INFO.get(conflicting_act, ("General", conflicting_act))
                warning_lbl.update(f"[#c4a768]Replaces existing shortcut for '{escape(str(conf_title))}'[/]")
            else:
                warning_lbl.update("[#888888]Valid shortcut. Press Enter to confirm.[/]")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rebind-btn-save":
            self.dismiss(canonicalize_key(self.selected_key) if self.selected_key else "")
        elif event.button.id == "rebind-btn-unbind":
            self.dismiss("")
        elif event.button.id == "rebind-btn-default":
            self.dismiss(self.default_key)
        elif event.button.id == "rebind-btn-cancel":
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        ek_lower = (event.key or "").lower()

        # 1. Escape -> Unbind! (User explicitly requested: going to bind area and pressing esc unbinds)
        if ek_lower == "escape":
            self.dismiss("")
            event.prevent_default()
            event.stop()
            return

        # 2. Enter / Return -> Confirm captured key
        if ek_lower in ("enter", "return", "ctrl+m"):
            self.dismiss(canonicalize_key(self.selected_key) if self.selected_key else "")
            event.prevent_default()
            event.stop()
            return

        # 3. Ctrl+C -> Cancel without changes
        if ek_lower == "ctrl+c":
            self.dismiss(None)
            event.prevent_default()
            event.stop()
            return

        # 4. Any other keypress -> capture immediately!
        captured = normalize_captured_key(event.key, getattr(event, "character", None))
        if captured:
            self.selected_key = captured
            self._update_preview(captured)
            event.prevent_default()
            event.stop()
            return

class SettingsModal(SafeModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_or_cancel", "Close", priority=True),
        Binding("q", "dismiss_or_cancel", "Close", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "select_or_toggle", "Select", show=False),
        Binding("space", "select_or_toggle", "Toggle", show=False),
        Binding("u", "unbind_selected_key", "Unbind Key", show=False),
        Binding("delete", "unbind_selected_key", "Unbind Key", show=False),
        Binding("backspace", "reset_selected_key", "Reset Key", show=False),
        Binding("r", "reset_selected_key", "Reset Key", show=False),
        Binding("R", "reset_all_keys", "Reset All", show=False),
        Binding("tab", "switch_focus", "Switch Focus", show=False),
    ]

    def __init__(self):
        super().__init__()

    @property
    def spoff_app(self) -> Any:
        return self.app

    SETTINGS_HINT = "enter change · u unbind · r reset · R reset all · esc close"

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            with Horizontal(id="settings-header"):
                yield Static("SETTINGS", id="settings-title")
                yield Static("[dim]esc[/dim]", id="settings-close-hint")

            with Vertical(id="settings-options-container"):
                yield Static("INTERFACE", classes="settings-section")
                yield AdvModeToggle(id="adv-mode-toggle", classes="setting-toggle-item")
                yield NotificationsToggle(id="notifications-toggle", classes="setting-toggle-item")
                yield TransparencyToggle(id="transparency-toggle", classes="setting-toggle-item")
                yield Static("SEARCH & UPDATES", classes="settings-section")
                yield SearchEngineToggle(id="engine-toggle", classes="setting-toggle-item")
                yield InstantSearchToggle(id="instant-search-toggle", classes="setting-toggle-item")
                yield AutoUpdateToggle(id="auto-update-toggle", classes="setting-toggle-item")
                yield Static("VISUALIZER", classes="settings-section")
                yield VisualizerToggle(id="vis-toggle", classes="setting-toggle-item")
                yield VisualizerStyleToggle(id="vis-style-toggle", classes="setting-toggle-item")
                yield VisualizerColorToggle(id="vis-color-toggle", classes="setting-toggle-item")
                yield Static("AUDIO", classes="settings-section")
                yield EQSettingsNavToggle(id="eq-settings-nav-toggle", classes="setting-toggle-item")

            yield Static("KEYS", id="settings-table-title")
            yield DataTable(id="settings-table", cursor_type="row", show_header=False)
            yield Static("", id="settings-status-line")
            yield Static(f"[dim]{self.SETTINGS_HINT}[/dim]", id="settings-footer")

    @staticmethod
    def _action_label(title: str) -> str:
        """ACTION_INFO titles carry their default key in parentheses; the key column already shows it."""
        return re.sub(r"\s*\([^)]*\)\s*$", "", title)

    def _key_cells(self, act_id: str) -> Tuple[str, str]:
        cur_key = self.spoff_app.keybindings.get(act_id, "")
        if not cur_key:
            return "[#555555]none[/]", ""
        marker = "" if cur_key == DEFAULT_KEYBINDINGS.get(act_id) else "[#6cc483]•[/]"
        # Drawn as a keycap so keys read as keys, not as more text.
        # U+2800 pads the cap: DataTable strips a leading plain space.
        return f"[#e2e2e2 on #2a2a2a]\u2800{escape(format_key_display(cur_key))}\u2800[/]", marker

    def on_mount(self) -> None:
        self.update_toggle_ui()
        table = self.query_one("#settings-table", DataTable)
        table.cursor_foreground_priority = "renderable"
        table.add_column("Section", key="cat", width=11)
        table.add_column("Action", key="act", width=34)
        table.add_column("Key", key="key", width=14)
        table.add_column("", key="stat", width=1)

        # Group rows by section (ACTION_INFO interleaves them). Handlers map a
        # cursor row back to its action through self._row_ids.
        cat_order = list(dict.fromkeys(cat for cat, _ in ACTION_INFO.values()))
        self._row_ids = sorted(ACTION_INFO, key=lambda a: cat_order.index(ACTION_INFO[a][0]))
        prev_cat = None
        for act_id in self._row_ids:
            cat, title = ACTION_INFO[act_id]
            key_text, marker = self._key_cells(act_id)
            # Name each section once; repeating it on every row is noise.
            cat_cell = f"[#555555]{escape(cat)}[/]" if cat != prev_cat else ""
            prev_cat = cat
            table.add_row(cat_cell, escape(self._action_label(title)), key_text, marker, key=act_id)

        try:
            self.query_one("#adv-mode-toggle", AdvModeToggle).focus()
        except Exception:
            table.focus()

    @staticmethod
    def _row(label: str, value: str, on: Optional[bool] = None) -> str:
        """One settings line: label on the left, plain value on the right; 'off' values are dimmed."""
        colour = "#5f5f5f" if on is False else ("#6cc483" if on else "#e2e2e2")
        return f"{label:<24}[{colour}]{escape(value)}[/]"

    def update_toggle_ui(self) -> None:
        app = self.spoff_app
        try:
            is_adv = bool(getattr(app, "advanced_mode", False))
            footer = self.query_one("#settings-footer", Static)
            footer.update("" if is_adv else f"[dim]{self.SETTINGS_HINT}[/dim]")
            self.query_one("#settings-close-hint", Static).update("" if is_adv else "[dim]esc[/dim]")

            # "Advanced mode" only hides the key hints, so name it for what it does.
            self.query_one("#adv-mode-toggle", Static).update(
                self._row("Key hints", "hidden" if is_adv else "shown", on=not is_adv))
            notif = bool(getattr(app, "notifications_enabled", True))
            self.query_one("#notifications-toggle", Static).update(
                self._row("Notifications", "on" if notif else "off", on=notif))
            trans = bool(getattr(app, "transparency", True))
            op_pct = int(round(getattr(app, "transparency_opacity", 0.85) * 100))
            self.query_one("#transparency-toggle", Static).update(
                self._row("Transparent background", f"on · {op_pct}%" if trans else "off", on=trans))

            engine = "Spotify" if getattr(app, "search_engine", "ytmusic") == "spotify" else "YouTube Music"
            self.query_one("#engine-toggle", Static).update(self._row("Search with", engine))
            inst = bool(getattr(app, "instant_search", True))
            self.query_one("#instant-search-toggle", Static).update(
                self._row("Focus search on open", "on" if inst else "off", on=inst))
            upd = bool(getattr(app, "auto_update", True))
            self.query_one("#auto-update-toggle", Static).update(
                self._row("Update automatically", "on" if upd else "off", on=upd))

            vis = getattr(app, "visualizer", None)
            vis_on = bool(getattr(app, "vis_enabled", True)) and getattr(vis, "style", "bars") != "off"
            self.query_one("#vis-toggle", Static).update(self._row("Visualizer", "on" if vis_on else "off", on=vis_on))
            style = vis.get_style_name() if vis else "Bars"
            self.query_one("#vis-style-toggle", Static).update(self._row("Style", style, on=None if vis_on else False))
            colour = vis.get_color_name() if vis else "Green"
            self.query_one("#vis-color-toggle", Static).update(self._row("Colour", colour, on=None if vis_on else False))

            eq_eng = getattr(app, "eq_engine", None)
            p_name = eq_eng.preset_name if eq_eng else "AKG Reference"
            state = "bypassed" if eq_eng is not None and eq_eng.bypassed else p_name
            self.query_one("#eq-settings-nav-toggle", Static).update(self._row("Equalizer", f"{state}  ›"))
        except Exception:
            logger.debug("Could not refresh settings rows", exc_info=True)

    def toggle_advanced_mode(self) -> None:
        new_state = self.spoff_app.toggle_advanced_mode()
        self.update_toggle_ui()
        state_text = "enabled" if new_state else "disabled"
        self.query_one("#settings-status-line", Static).update(f"Advanced Mode: {state_text}")

    def toggle_notifications(self) -> None:
        new_state = self.spoff_app.toggle_notifications()
        self.update_toggle_ui()
        state_text = "enabled" if new_state else "disabled"
        self.query_one("#settings-status-line", Static).update(f"Notifications: {state_text}")

    def toggle_transparency(self) -> None:
        new_state = self.spoff_app.toggle_transparency()
        self.update_toggle_ui()
        state_text = "enabled" if new_state else "disabled"
        self.query_one("#settings-status-line", Static).update(f"UI Transparency: {state_text}")

    def toggle_instant_search(self) -> None:
        new_state = self.spoff_app.toggle_instant_search()
        self.update_toggle_ui()
        state_text = "enabled" if new_state else "disabled"
        self.query_one("#settings-status-line", Static).update(f"Instant Search: {state_text}")

    def toggle_auto_update(self) -> None:
        new_state = self.spoff_app.toggle_auto_update()
        self.update_toggle_ui()
        state_text = "enabled" if new_state else "disabled"
        self.query_one("#settings-status-line", Static).update(f"Auto-Update: {state_text}")

    def toggle_search_engine(self) -> None:
        new_engine = self.spoff_app.toggle_search_engine()
        self.update_toggle_ui()
        label = "Spotify" if new_engine == "spotify" else "YouTube Music"
        self.query_one("#settings-status-line", Static).update(f"Search Engine: {label}")

    def toggle_visualizer(self) -> None:
        new_state = self.spoff_app.toggle_visualizer()
        self.update_toggle_ui()
        state_text = "enabled" if new_state else "disabled"
        self.query_one("#settings-status-line", Static).update(f"CAVA Visualizer: {state_text}")

    def cycle_visualizer_style(self) -> None:
        if hasattr(self.spoff_app, "cycle_visualizer_style"):
            self.spoff_app.cycle_visualizer_style()
        self.update_toggle_ui()
        style_name = self.spoff_app.visualizer.get_style_name() if hasattr(self.spoff_app, "visualizer") else ""
        self.query_one("#settings-status-line", Static).update(f"Visualizer Style: {style_name}")

    def cycle_visualizer_color(self) -> None:
        if hasattr(self.spoff_app, "cycle_visualizer_color"):
            self.spoff_app.cycle_visualizer_color()
        self.update_toggle_ui()
        color_name = self.spoff_app.visualizer.get_color_name() if hasattr(self.spoff_app, "visualizer") else ""
        self.query_one("#settings-status-line", Static).update(f"Visualizer Theme: {color_name}")

    def start_rebinding(self, act_id: str) -> None:
        if isinstance(self.app.screen, RebindKeyModal):
            return
        cat, title = ACTION_INFO.get(act_id, ("General", act_id))
        cur_key = self.spoff_app.keybindings.get(act_id, "")
        def_key = DEFAULT_KEYBINDINGS.get(act_id, "")

        def _on_rebind_done(new_key: Optional[str]) -> None:
            if new_key is not None:
                self.apply_rebound_key(act_id, new_key)
            else:
                self.query_one("#settings-status-line", Static).update("[dim]Rebinding cancelled.[/dim]")
            try:
                table = self.query_one("#settings-table", DataTable)
                table.focus()
            except Exception:
                pass

        self.app.push_screen(
            RebindKeyModal(act_id, cat, title, cur_key, def_key, self.spoff_app.keybindings),
            _on_rebind_done
        )

    def apply_rebound_key(self, act_id: str, new_key: str) -> None:
        if not act_id:
            return

        _, act_title = ACTION_INFO.get(act_id, ("General", act_id))
        new_key = canonicalize_key(new_key) if new_key else ""

        conflicting_act = None
        if new_key:
            for other_id, bound in self.spoff_app.keybindings.items():
                if other_id != act_id and canonicalize_key(bound) == new_key:
                    conflicting_act = other_id
                    break

        if not new_key:
            status_msg = f"Unbound '{act_title}'."
        else:
            disp_key = format_key_display(new_key)
            status_msg = f"Bound '{act_title}' to {disp_key}."
            if conflicting_act:
                _, conf_title = ACTION_INFO.get(conflicting_act, ("General", conflicting_act))
                self.spoff_app.set_custom_keybinding(conflicting_act, "")
                status_msg += f" (Unbound conflicting '{conf_title}')"
                self._refresh_row(conflicting_act)

        self.spoff_app.set_custom_keybinding(act_id, new_key)
        self._refresh_row(act_id)
        self.query_one("#settings-status-line", Static).update(status_msg)

    def _refresh_row(self, act_id: str) -> None:
        table = self.query_one("#settings-table", DataTable)
        key_text, marker = self._key_cells(act_id)
        try:
            table.update_cell(act_id, "key", key_text)
            table.update_cell(act_id, "stat", marker)
        except Exception:
            pass

    def action_unbind_selected_key(self) -> None:
        table = self.query_one("#settings-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            act_id = self._row_ids[table.cursor_row]
            self.spoff_app.set_custom_keybinding(act_id, "")
            self._refresh_row(act_id)
            _, title = ACTION_INFO.get(act_id, ("General", act_id))
            self.query_one("#settings-status-line", Static).update(
                f"Unbound '{title}'."
            )

    def action_reset_selected_key(self) -> None:
        table = self.query_one("#settings-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            act_id = self._row_ids[table.cursor_row]
            self.spoff_app.reset_keybinding(act_id)
            self._refresh_row(act_id)
            _, title = ACTION_INFO.get(act_id, ("General", act_id))
            def_key = DEFAULT_KEYBINDINGS.get(act_id, "")
            self.query_one("#settings-status-line", Static).update(
                f"Reset '{title}' to default ({format_key_display(def_key)})."
            )

    def action_reset_all_keys(self) -> None:
        # One stray Shift+R used to wipe every custom binding with no way back.
        def _on_confirm(confirmed: Optional[bool]) -> None:
            if not confirmed:
                return
            self.spoff_app.reset_all_keybindings()
            for act_id in ACTION_INFO.keys():
                self._refresh_row(act_id)
            self.query_one("#settings-status-line", Static).update("All keybindings reset to factory defaults.")

        self.app.push_screen(
            ConfirmModal("Reset all keybindings", "Reset every shortcut to its default? Your custom bindings will be lost.", confirm_label="Reset"),
            _on_confirm,
        )

    def open_eq_settings(self) -> None:
        if hasattr(self.spoff_app, "eq_engine") and self.spoff_app.eq_engine:
            self.app.push_screen(EQSettingsModal(self.spoff_app.eq_engine))

    def action_dismiss_or_cancel(self) -> None:
        self.dismiss(None)

    def action_switch_focus(self) -> None:
        toggle_ids = [
            "adv-mode-toggle",
            "notifications-toggle",
            "transparency-toggle",
            "engine-toggle",
            "instant-search-toggle",
            "auto-update-toggle",
            "vis-toggle",
            "vis-style-toggle",
            "vis-color-toggle",
            "eq-settings-nav-toggle",
        ]
        focused_id = self.focused.id if self.focused else None
        if focused_id in toggle_ids:
            idx = toggle_ids.index(focused_id)
            if idx < len(toggle_ids) - 1:
                self.query_one(f"#{toggle_ids[idx + 1]}", Static).focus()
            else:
                self.query_one("#settings-table", DataTable).focus()
        else:
            self.query_one("#adv-mode-toggle", AdvModeToggle).focus()

    def action_select_or_toggle(self) -> None:
        focused_id = self.focused.id if self.focused else None
        if focused_id == "adv-mode-toggle":
            self.toggle_advanced_mode()
        elif focused_id == "notifications-toggle":
            self.toggle_notifications()
        elif focused_id == "transparency-toggle":
            self.toggle_transparency()
        elif focused_id == "instant-search-toggle":
            self.toggle_instant_search()
        elif focused_id == "auto-update-toggle":
            self.toggle_auto_update()
        elif focused_id == "engine-toggle":
            self.toggle_search_engine()
        elif focused_id == "vis-toggle":
            self.toggle_visualizer()
        elif focused_id == "vis-style-toggle":
            self.cycle_visualizer_style()
        elif focused_id == "vis-color-toggle":
            self.cycle_visualizer_color()
        elif focused_id == "eq-settings-nav-toggle":
            self.open_eq_settings()
        elif self.focused and self.focused.id == "settings-table":
            table = self.query_one("#settings-table", DataTable)
            if table.cursor_row is not None and table.row_count > 0:
                act_id = self._row_ids[table.cursor_row]
                self.start_rebinding(act_id)

    def action_cursor_down(self) -> None:
        table = self.query_one("#settings-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one("#settings-table", DataTable)
        if table.row_count == 0 or table.cursor_row == 0:
            self.query_one("#eq-settings-nav-toggle", EQSettingsNavToggle).focus()
        else:
            table.action_cursor_up()

    def on_key(self, event: events.Key) -> None:
        table = self.query_one("#settings-table", DataTable)
        toggle_ids = ["adv-mode-toggle", "notifications-toggle", "transparency-toggle", "engine-toggle", "instant-search-toggle", "auto-update-toggle", "vis-toggle", "vis-style-toggle", "vis-color-toggle", "eq-settings-nav-toggle"]
        focused_id = self.focused.id if self.focused else None

        if focused_id in toggle_ids:
            idx = toggle_ids.index(focused_id)
            if event.key in ("j", "down") or event.character == "j":
                if idx < len(toggle_ids) - 1:
                    self.query_one(f"#{toggle_ids[idx + 1]}", Static).focus()
                else:
                    table.focus()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("k", "up") or event.character == "k":
                if idx > 0:
                    self.query_one(f"#{toggle_ids[idx - 1]}", Static).focus()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("enter", "return", "ctrl+m", "space") or event.character in (" ",):
                if focused_id == "adv-mode-toggle":
                    self.toggle_advanced_mode()
                elif focused_id == "notifications-toggle":
                    self.toggle_notifications()
                elif focused_id == "transparency-toggle":
                    self.toggle_transparency()
                elif focused_id == "instant-search-toggle":
                    self.toggle_instant_search()
                elif focused_id == "auto-update-toggle":
                    self.toggle_auto_update()
                elif focused_id == "engine-toggle":
                    self.toggle_search_engine()
                elif focused_id == "vis-toggle":
                    self.toggle_visualizer()
                elif focused_id == "vis-style-toggle":
                    self.cycle_visualizer_style()
                elif focused_id == "vis-color-toggle":
                    self.cycle_visualizer_color()
                elif focused_id == "eq-settings-nav-toggle":
                    self.open_eq_settings()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("escape", "q"):
                self.dismiss(None)
                event.prevent_default()
                event.stop()
                return
        elif self.focused and self.focused.id == "settings-table":
            if event.key in ("enter", "return", "ctrl+m"):
                row_idx = table.cursor_row
                if row_idx is not None and 0 <= row_idx < table.row_count:
                    row_key, _ = table.coordinate_to_cell_key(Coordinate(row_idx, 0))
                    self.start_rebinding(str(row_key.value))
                    event.prevent_default()
                    event.stop()
                    return
            elif event.key in ("j", "down") or event.character == "j":
                table.action_cursor_down()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("k", "up") or event.character == "k":
                if table.row_count == 0 or table.cursor_row == 0:
                    self.query_one("#eq-settings-nav-toggle", EQSettingsNavToggle).focus()
                else:
                    table.action_cursor_up()
                event.prevent_default()
                event.stop()
                return
            elif (event.key in ("G", "shift+g") or event.character == "G") and table.row_count > 0:
                table.move_cursor(row=table.row_count - 1)
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("home",) and table.row_count > 0:
                table.move_cursor(row=0)
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("u", "delete"):
                self.action_unbind_selected_key()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("r", "backspace"):
                self.action_reset_selected_key()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("R", "shift+r") or event.character == "R":
                self.action_reset_all_keys()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("escape", "q"):
                self.dismiss(None)
                event.prevent_default()
                event.stop()
                return

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        act_id = str(event.row_key.value)
        self.start_rebinding(act_id)

class AddToPlaylistModal(SafeModalScreen[Optional[Tuple[str, str]]]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("tab", "switch_focus", "Switch Focus", show=False),
        Binding("down", "cursor_down_input", "Down", show=False),
        Binding("j", "cursor_down_table", "Down", show=False),
        Binding("k", "cursor_up_table", "Up", show=False),
        Binding("i", "focus_input", "New Playlist", show=False),
        Binding("a", "focus_input", "New Playlist", show=False),
        Binding("q", "dismiss_modal", "Cancel", show=False),
    ]

    def __init__(self, track: Dict[str, Any], playlists: List[Dict[str, Any]]):
        super().__init__()
        self.track = track
        self.playlists = playlists

    def compose(self) -> ComposeResult:
        title = self.track.get("title", "Unknown")
        artist = self.track.get("artist", "Unknown")
        with Vertical(id="modal-dialog"):
            yield Static("ADD TO PLAYLIST", id="modal-title")
            yield Static(f"Track: [bold #ffffff]{escape(title)}[/]  [#767676]—[/]  [#cccccc]{escape(artist)}[/]", id="modal-track-info")
            yield Input(placeholder="Create new playlist: type name...", id="modal-input")
            yield Static("OR CHOOSE EXISTING PLAYLIST", id="modal-subtitle")
            yield DataTable(id="modal-table", cursor_type="row", show_header=False)
            hint_text = "" if getattr(self.app, "advanced_mode", False) else "[dim]j/k: select playlist  |  i/Tab: new name  |  Enter: confirm  |  Esc: cancel[/dim]"
            yield Static(hint_text, id="modal-hint")

    def on_mount(self) -> None:
        table = self.query_one("#modal-table", DataTable)
        table.cursor_foreground_priority = "renderable"
        table.add_columns("Playlist")
        track_obj = getattr(self, "track", None) or {}
        try:
            liked_tracks = load_liked_songs()
            liked_count = len(liked_tracks)
        except Exception:
            liked_tracks = []
            liked_count = 0
        l_idx = liked_index(liked_tracks, track_obj)
        liked_pos = f" (#{l_idx + 1})" if l_idx is not None else ""
        liked_badge = f"  [#e5c07b]• In playlist{liked_pos}[/]" if l_idx is not None else ""
        table.add_row(f"★ Liked Songs  [dim]({liked_count} tracks)[/dim]{liked_badge}", key="target_liked_songs")
        if self.playlists:
            for p in self.playlists:
                p_name = escape(str(p.get("name") or "Untitled"))
                p_id = str(p.get("id") or "")
                tracks_count = len(p.get("tracks", []))
                is_spotify = (len(p_id) == 22 and p_id.isalnum()) or bool(p.get("spotify_id"))
                tag = " [#569f68]Spotify[/]" if is_spotify else ""
                t_idx = get_track_index_in_playlist(p, track_obj)
                t_pos = f" (#{t_idx + 1})" if t_idx is not None else ""
                in_badge = f"  [#e5c07b]• In playlist{t_pos}[/]" if t_idx is not None else ""
                table.add_row(f"{p_name}{tag}  [dim]({tracks_count} tracks)[/dim]{in_badge}", key=p_id)
        table.focus()
        if hasattr(self, "_update_hint_for_selection"):
            self._update_hint_for_selection()

    def _update_hint_for_selection(self) -> None:
        try:
            hint_widget = self.query_one("#modal-hint", Static)
        except Exception:
            return
        track_obj = getattr(self, "track", None) or {}
        title = str(track_obj.get("title") or "Track")
        advanced = False
        try:
            advanced = bool(getattr(self.app, "advanced_mode", False))
        except Exception:
            pass
        default_hint = "" if advanced else "[dim]j/k: select playlist  |  i/Tab: new name  |  Enter: confirm  |  Esc: cancel[/dim]"

        if self.focused and getattr(self.focused, "id", None) == "modal-input":
            try:
                inp = self.query_one("#modal-input", Input)
                val = inp.value.strip()
                if val and self.playlists:
                    match = next((p for p in self.playlists if (p.get("name") or "").strip().casefold() == val.casefold()), None)
                    if match:
                        m_idx = get_track_index_in_playlist(match, track_obj)
                        if m_idx is not None:
                            hint_widget.update(f"[#e5c07b]Notice: '{escape(title)}' is already track #{m_idx + 1} in '{escape(match.get('name') or val)}'[/]")
                            return
            except Exception:
                pass
            hint_widget.update(default_hint)
            return

        try:
            table = self.query_one("#modal-table", DataTable)
            idx = table.cursor_row
            if idx == 0:
                l_idx = liked_index(load_liked_songs(), track_obj)
                if l_idx is not None:
                    hint_widget.update(f"[#e5c07b]Notice: '{escape(title)}' is already track #{l_idx + 1} in Liked Songs[/]")
                    return
            elif idx is not None and 1 <= idx <= len(self.playlists):
                p = self.playlists[idx - 1]
                t_idx = get_track_index_in_playlist(p, track_obj)
                if t_idx is not None:
                    p_name = p.get("name") or "this playlist"
                    hint_widget.update(f"[#e5c07b]Notice: '{escape(title)}' is already track #{t_idx + 1} in '{escape(p_name)}' (Enter to view / add)[/]")
                    return
        except Exception:
            pass
        hint_widget.update(default_hint)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_hint_for_selection()

    def on_input_changed(self, event: Input.Changed) -> None:
        if getattr(event.input, "id", None) == "modal-input":
            self._update_hint_for_selection()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_switch_focus(self) -> None:
        if self.focused and self.focused.id == "modal-input":
            self.query_one("#modal-table", DataTable).focus()
        else:
            self.query_one("#modal-input", Input).focus()
        self._update_hint_for_selection()

    def action_focus_input(self) -> None:
        self.query_one("#modal-input", Input).focus()
        self._update_hint_for_selection()

    def action_cursor_down_input(self) -> None:
        if self.focused and self.focused.id == "modal-input":
            table = self.query_one("#modal-table", DataTable)
            if table.row_count > 0:
                table.focus()
        elif self.focused and self.focused.id == "modal-table":
            self.query_one("#modal-table", DataTable).action_cursor_down()
        self._update_hint_for_selection()

    def action_cursor_down_table(self) -> None:
        if self.focused and self.focused.id == "modal-table":
            self.query_one("#modal-table", DataTable).action_cursor_down()
        self._update_hint_for_selection()

    def action_cursor_up_table(self) -> None:
        if self.focused and self.focused.id == "modal-table":
            table = self.query_one("#modal-table", DataTable)
            if table.row_count == 0 or table.cursor_row == 0:
                self.query_one("#modal-input", Input).focus()
            else:
                table.action_cursor_up()
        self._update_hint_for_selection()

    def on_key(self, event: events.Key) -> None:
        table = self.query_one("#modal-table", DataTable)
        inp = self.query_one("#modal-input", Input)

        if self.focused and self.focused.id == "modal-table":
            if event.key in ("enter", "return", "ctrl+m"):
                idx = table.cursor_row
                if idx == 0:
                    self.dismiss(("liked", "liked_songs"))
                elif idx is not None and 1 <= idx <= len(self.playlists):
                    self.dismiss(("select", self.playlists[idx - 1]["id"]))
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("j", "down") or event.character == "j":
                table.action_cursor_down()
                self._update_hint_for_selection()
                event.prevent_default()
                event.stop()
            elif event.key in ("k", "up") or event.character == "k":
                if table.row_count == 0 or table.cursor_row == 0:
                    inp.focus()
                else:
                    table.action_cursor_up()
                self._update_hint_for_selection()
                event.prevent_default()
                event.stop()
            elif (event.key in ("G", "shift+g") or event.character == "G") and table.row_count > 0:
                table.move_cursor(row=table.row_count - 1)
                self._update_hint_for_selection()
                event.prevent_default()
                event.stop()
            elif event.key in ("home",) and table.row_count > 0:
                table.move_cursor(row=0)
                self._update_hint_for_selection()
                event.prevent_default()
                event.stop()
            elif event.key in ("i", "a") and event.character in ("i", "a"):
                inp.focus()
                self._update_hint_for_selection()
                event.prevent_default()
                event.stop()
            elif event.key in ("escape", "q"):
                self.dismiss(None)
                event.prevent_default()
                event.stop()
        elif self.focused and self.focused.id == "modal-input":
            if event.key in ("enter", "return", "ctrl+m"):
                val = inp.value.strip()
                if val:
                    self.dismiss(("create", val))
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("down", "tab"):
                if table.row_count > 0:
                    table.focus()
                    self._update_hint_for_selection()
                    event.prevent_default()
                    event.stop()
            elif event.key == "escape":
                if inp.value:
                    inp.value = ""
                    self._update_hint_for_selection()
                    event.prevent_default()
                    event.stop()
                elif table.row_count > 0:
                    table.focus()
                    self._update_hint_for_selection()
                    event.prevent_default()
                    event.stop()
                else:
                    self.dismiss(None)
                    event.prevent_default()
                    event.stop()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "modal-input":
            val = event.value.strip()
            if val:
                self.dismiss(("create", val))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "modal-table":
            idx = event.cursor_row
            if idx == 0:
                self.dismiss(("liked", "liked_songs"))
            elif idx is not None and 1 <= idx <= len(self.playlists):
                self.dismiss(("select", self.playlists[idx - 1]["id"]))

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.data_table.id == "modal-table":
            idx = event.coordinate.row
            if idx == 0:
                self.dismiss(("liked", "liked_songs"))
            elif idx is not None and 1 <= idx <= len(self.playlists):
                self.dismiss(("select", self.playlists[idx - 1]["id"]))

class ConfirmModal(SafeModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", "No", show=False),
        Binding("enter", "confirm", "Confirm"),
        Binding("y", "confirm", "Yes", show=False),
        Binding("d", "confirm", "Delete", show=False),
        Binding("delete", "confirm", "Delete", show=False),
    ]

    def __init__(self, title: str, message: str, confirm_label: str = "Delete"):
        super().__init__()
        self.modal_title = title
        self.modal_message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self.modal_title, id="confirm-title")
            yield Static(self.modal_message, id="confirm-message")
            c_hint = f"[bold #ffffff]{self.confirm_label}[/]    [#767676]Cancel[/]" if getattr(self.app, "advanced_mode", False) else f"[bold #ffffff][Y / Enter][/] {self.confirm_label}    [#767676][N / Esc] Cancel[/]"
            yield Static(c_hint, id="confirm-hint")

    def on_click(self, event: events.Click) -> None:
        try:
            target = event.target
            target_id = getattr(target, "id", "")
            if target_id == "confirm-hint":
                w = getattr(target, "size", None)
                width = w.width if w else 20
                if event.x > width // 2:
                    self.dismiss(False)
                else:
                    self.dismiss(True)
        except Exception:
            pass

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class RenamePlaylistModal(SafeModalScreen[Optional[str]]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("enter", "submit_name", "Rename", priority=True),
    ]

    def __init__(self, current_name: str):
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog"):
            yield Static("RENAME PLAYLIST", id="rename-title")
            yield Static(f"Current: [bold #ffffff]{escape(self.current_name)}[/]", id="rename-sub")
            yield Input(value=self.current_name, placeholder="Enter new playlist name...", id="rename-input")
            hint_text = "" if getattr(self.app, "advanced_mode", False) else "[dim]Enter: save name  |  Esc: cancel[/dim]"
            yield Static(hint_text, id="rename-hint")

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#rename-input", Input)
            inp.focus()
            inp.select_on_focus = True
        except Exception:
            pass

    def action_submit_name(self) -> None:
        try:
            val = self.query_one("#rename-input", Input).value.strip()
            self.dismiss(val if val else None)
        except Exception:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class ClonePlaylistModal(SafeModalScreen[Optional[str]]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("enter", "submit_name", "Clone", priority=True),
    ]

    def __init__(self, original_name: str, track_count: int = 0):
        super().__init__()
        self.original_name = original_name
        self.track_count = track_count
        self.default_clone_name = f"{original_name} (Copy)"

    def compose(self) -> ComposeResult:
        with Vertical(id="clone-dialog"):
            yield Static("CLONE / COPY PLAYLIST", id="clone-title")
            yield Static(
                f"Source: [bold #ffffff]{escape(self.original_name)}[/]  [dim]({self.track_count} tracks)[/dim]",
                id="clone-sub"
            )
            yield Input(value=self.default_clone_name, placeholder="Enter name for cloned playlist...", id="clone-input")
            hint_text = "" if getattr(self.app, "advanced_mode", False) else "[dim]Enter: create copy  |  Esc: cancel[/dim]"
            yield Static(hint_text, id="clone-hint")

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#clone-input", Input)
            inp.focus()
            inp.select_on_focus = True
        except Exception:
            pass

    def action_submit_name(self) -> None:
        try:
            val = self.query_one("#clone-input", Input).value.strip()
            self.dismiss(val if val else self.default_clone_name)
        except Exception:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else self.default_clone_name)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class VisibilityToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, PlaylistSettingsModal):
            self.screen.toggle_visibility()


class PlaylistField(Horizontal):
    """A text field in normal mode: focusable as a row, edited only after enter/i."""
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, PlaylistSettingsModal):
            self.screen.start_editing(self)


class PlaylistSettingsModal(SafeModalScreen[Optional[Dict[str, Any]]]):
    """Name, description, and visibility for one playlist. Returns the edited values.

    Vim-style: normal mode moves between rows with j/k; enter or i edits a text
    field, and esc or enter leaves the field again.
    """
    ROW_IDS = ["plset-name-field", "plset-desc-field", "plset-visibility"]

    def __init__(self, playlist: Dict[str, Any], linked: bool, followed: bool):
        super().__init__()
        self.playlist = playlist
        self.linked = linked
        self.followed = followed
        self.public = bool(playlist.get("public", False))

    def compose(self) -> ComposeResult:
        if self.followed:
            where = "followed · edits stay in Spoff"
        elif self.linked:
            where = "synced with Spotify"
        else:
            where = "local · applied when it syncs"
        with Vertical(id="plset-dialog"):
            with Horizontal(id="plset-header"):
                yield Static("PLAYLIST", id="plset-title")
                yield Static(f"[#5a5a5a]{where}[/]", id="plset-where")
            with PlaylistField(id="plset-name-field", classes="plset-field"):
                yield Static("Name", classes="plset-label")
                yield Input(value=str(self.playlist.get("name") or ""), max_length=100,
                            id="plset-name", classes="plset-input")
            with PlaylistField(id="plset-desc-field", classes="plset-field"):
                yield Static("Description", classes="plset-label")
                yield Input(value=str(self.playlist.get("description") or ""), placeholder="none",
                            max_length=300, id="plset-desc", classes="plset-input")
            yield VisibilityToggle(id="plset-visibility", classes="setting-toggle-item")
            # Hidden with key hints off (advanced mode), like the other screens.
            hints = "" if getattr(self.app, "advanced_mode", False) else \
                "[dim]j/k move · enter edit · space public/private · esc done[/dim]"
            yield Static(hints, id="plset-footer", classes="has-hints" if hints else "")

    def on_mount(self) -> None:
        for inp in self.query(Input):
            inp.can_focus = False  # normal mode: rows take focus, not the text boxes
        self._render_visibility()
        self.query_one("#plset-name-field", PlaylistField).focus()

    def _render_visibility(self) -> None:
        label = "public" if self.public else "private"
        self.query_one("#plset-visibility", Static).update(
            SettingsModal._row("Visibility", label, on=self.public or None))

    def toggle_visibility(self) -> None:
        self.public = not self.public
        self._render_visibility()

    def start_editing(self, field: "PlaylistField") -> None:
        inp = field.query_one(Input)
        inp.can_focus = True
        inp.focus()
        inp.cursor_position = len(inp.value)

    def stop_editing(self) -> None:
        inp = self.focused
        if isinstance(inp, Input):
            field = inp.parent
            inp.can_focus = False
            if isinstance(field, PlaylistField):
                field.focus()

    def _move(self, step: int) -> None:
        current = self.focused.id if self.focused is not None else None
        idx = self.ROW_IDS.index(current) if current in self.ROW_IDS else 0
        self.query_one(f"#{self.ROW_IDS[(idx + step) % len(self.ROW_IDS)]}").focus()

    def on_key(self, event: events.Key) -> None:
        key = event.key
        focused = self.focused
        if isinstance(focused, Input):
            # Edit mode: only esc leaves; everything else is typing (enter is handled on submit).
            if key == "escape":
                self.stop_editing()
            else:
                return
        elif key in ("j", "down", "tab"):
            self._move(1)
        elif key in ("k", "up", "shift+tab"):
            self._move(-1)
        elif key in ("g", "home"):
            self.query_one(f"#{self.ROW_IDS[0]}").focus()
        elif key in ("G", "end"):
            self.query_one(f"#{self.ROW_IDS[-1]}").focus()
        elif isinstance(focused, PlaylistField) and key in ("enter", "i", "a"):
            self.start_editing(focused)
        elif focused is not None and focused.id == "plset-visibility" and key in ("enter", "space", "h", "l", "left", "right"):
            self.toggle_visibility()
        elif key in ("escape", "q"):
            self.action_save()  # leaving the menu saves
        else:
            return
        event.prevent_default()
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.stop_editing()

    def action_save(self) -> None:
        # A cleared name falls back to the old one, so leaving never gets stuck.
        name = self.query_one("#plset-name", Input).value.strip() or str(self.playlist.get("name") or "Playlist")
        description = " ".join(self.query_one("#plset-desc", Input).value.split())
        self.dismiss({"name": name, "description": description, "public": self.public})


class DeletePlaylistModal(SafeModalScreen[bool]):
    """Modal requiring explicit typing of the playlist name to permanently delete."""
    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", priority=True),
        Binding("enter", "submit_delete", "Delete", priority=True),
    ]

    def __init__(self, playlist_name: str, track_count: int = 0):
        super().__init__()
        self.playlist_name = playlist_name.strip() or "Playlist"
        self.track_count = track_count
        self.modal_title = "DELETE PLAYLIST"

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-playlist-dialog"):
            yield Static("DELETE PLAYLIST", id="delete-playlist-title")
            tracks_str = f" ({self.track_count} tracks)" if self.track_count else ""
            yield Static(
                f"This will permanently delete '[bold #ff5555]{escape(self.playlist_name)}[/]'{tracks_str} from your library and Spotify.",
                id="delete-playlist-warning"
            )
            yield Static(
                f"To confirm, type [bold #ffffff]{escape(self.playlist_name)}[/] below:",
                id="delete-playlist-instruction"
            )
            yield Input(placeholder=self.playlist_name, id="delete-playlist-input")
            hint_text = "" if getattr(self.app, "advanced_mode", False) else "[dim]Enter: confirm deletion  |  Esc: cancel[/dim]"
            yield Static(hint_text, id="delete-playlist-hint")

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#delete-playlist-input", Input)
            inp.focus()
            inp.value = ""
        except Exception:
            pass

    def action_submit_delete(self) -> None:
        try:
            inp = self.query_one("#delete-playlist-input", Input)
            val = inp.value.strip()
            target = self.playlist_name.strip()
            if val and (val == target or val.casefold() == target.casefold()):
                self.dismiss(True)
                return

            try:
                hint = self.query_one("#delete-playlist-hint", Static)
                if not val:
                    hint.update(f"[bold #ff5555]Type '[bold #ffffff]{escape(target)}[/]' to confirm, or Esc to cancel.[/]")
                else:
                    hint.update(f"[bold #ff5555]Name mismatch! Expected '[bold #ffffff]{escape(target)}[/]', got '[bold #888888]{escape(val)}[/]'.[/]")
            except Exception:
                pass

            try:
                app = getattr(self, "_app", None) or getattr(self, "app", None)
                if app and hasattr(app, "notify_user"):
                    app.notify_user(f"Playlist deletion aborted: name mismatch. Type '{target}' to confirm.")
            except Exception:
                pass
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_submit_delete()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(False)


class DuplicateTrackModal(SafeModalScreen[Optional[str]]):
    """Modal displayed when a track already exists in the target playlist."""
    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", priority=True),
        Binding("enter", "jump_track", "Jump to Track", priority=True),
        Binding("v", "jump_track", "Jump to Track", show=False),
        Binding("a", "add_anyway", "Add Anyway", priority=True),
    ]

    def __init__(self, track: Dict[str, Any], playlist_name: str, track_index: int, playlist_id: str):
        super().__init__()
        self.track = track
        self.playlist_name = playlist_name
        self.track_index = track_index
        self.playlist_id = playlist_id

    def compose(self) -> ComposeResult:
        t_title = str(self.track.get("title") or "Track")
        t_artist = str(self.track.get("artist") or "Unknown Artist")
        track_num = self.track_index + 1
        with Vertical(id="duplicate-dialog"):
            yield Static("ALREADY IN PLAYLIST", id="rename-title")
            yield Static(
                f"[bold #ffffff]{escape(t_title)}[/] by [#abb2bf]{escape(t_artist)}[/]\nis already in [bold #61afef]{escape(self.playlist_name)}[/] at position [bold #e5c07b]#{track_num}[/].\n",
                id="rename-sub"
            )
            yield Static(
                f"  [bold #569f68]Enter / v[/]  — Jump to track [bold #e5c07b]#{track_num}[/] in '{escape(self.playlist_name)}'\n"
                f"  [bold #61afef]a[/]          — Add anyway (create duplicate)\n"
                f"  [bold #e06c75]Esc[/]        — Cancel",
                id="duplicate-actions"
            )

    def action_jump_track(self) -> None:
        self.dismiss("jump")

    def action_add_anyway(self) -> None:
        self.dismiss("add")

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)


class FilterTracksModal(SafeModalScreen[Optional[int]]):
    """Modal to quickly search and jump to a track within the current playlist or view."""
    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", priority=True),
        Binding("enter", "select_track", "Jump to Track", priority=True),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, tracks: List[Dict[str, Any]], view_name: str = "Playlist"):
        super().__init__()
        self.tracks = tracks
        self.view_name = view_name
        self.matching_indices: List[int] = list(range(len(tracks)))

    def compose(self) -> ComposeResult:
        with Vertical(id="filter-dialog"):
            yield Static(f"FIND IN {self.view_name.upper()}", id="rename-title")
            yield Input(placeholder="Type song title or artist...", id="filter-input")
            yield DataTable(id="filter-table", cursor_type="row", show_header=False)
            yield Static("[dim]Type to filter  |  Enter: jump to song  |  Esc: cancel[/dim]", id="rename-hint")

    def on_mount(self) -> None:
        table = self.query_one("#filter-table", DataTable)
        table.cursor_foreground_priority = "renderable"
        table.add_columns("Track")
        self._populate_table("")
        try:
            inp = self.query_one("#filter-input", Input)
            inp.focus()
        except Exception:
            pass

    def _populate_table(self, query: str) -> None:
        table = self.query_one("#filter-table", DataTable)
        table.clear()
        self.matching_indices = []
        q = query.strip().casefold()
        for idx, t in enumerate(self.tracks):
            title = str(t.get("title") or "")
            artist = str(t.get("artist") or "")
            if not q or q in title.casefold() or q in artist.casefold():
                self.matching_indices.append(idx)
                table.add_row(f"[bold #e5c07b]#{idx + 1:2d}[/]  [bold #ffffff]{escape(title)}[/]  [dim #abb2bf]— {escape(artist)}[/]", key=str(idx))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._populate_table(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_select_track()

    def action_select_track(self) -> None:
        try:
            table = self.query_one("#filter-table", DataTable)
            row_idx = table.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self.matching_indices):
                self.dismiss(self.matching_indices[row_idx])
                return
            elif self.matching_indices:
                self.dismiss(self.matching_indices[0])
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#filter-table", DataTable).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#filter-table", DataTable).action_cursor_up()
        except Exception:
            pass

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)


class SpotifyAuthModal(SafeModalScreen[Optional[str]]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("s", "sync_library", "Sync", show=False),
        Binding("S", "sync_library", "Sync", show=False),
        Binding("r", "relink_account", "Re-link", show=False),
        Binding("R", "relink_account", "Re-link", show=False),
        Binding("o", "logout_account", "Log Out", show=False),
        Binding("O", "logout_account", "Log Out", show=False),
        Binding("q", "dismiss_modal", "Close", show=False),
    ]

    def __init__(self, first_run: bool = False):
        super().__init__()
        self.first_run = first_run
        self.auth_session = load_spotify_auth()
        self.server: Optional[OAuthCallbackServer] = None
        self.pkce_verifier: Optional[str] = None
        self.is_logging_in: bool = False
        self._login_attempt: Optional[object] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="spotify-dialog"):
            is_connected = bool(self.auth_session and (self.auth_session.get("access_token") or self.auth_session.get("refresh_token")))
            title = "Connect Spotify" if (self.first_run and not is_connected) else "Spotify Account"
            with Horizontal(id="spotify-header-bar"):
                yield Static(title, id="spotify-title")
                yield Static("[dim]esc[/dim]", id="spotify-close-hint")

            if is_connected:
                user = self.auth_session.get("user", {})
                name = user.get("display_name") or user.get("id") or "Spotify User"
                email = user.get("email") or ""
                plan = user.get("product", "free").capitalize()
                u_id = user.get("id") or ""

                user_line = f"User    [#e2e2e2]{escape(str(name))}[/]"
                if email:
                    user_line += f"  [#555555]({escape(str(email))})[/]"
                elif u_id and u_id != name:
                    user_line += f"  [#555555](@{escape(str(u_id))})[/]"

                yield Static(user_line, id="spotify-user-info")
                yield Static(f"Plan    [#e2e2e2]Spotify {escape(str(plan))}[/]", id="spotify-desc")

                can_modify = has_modify_scopes()
                if can_modify:
                    yield Static("[#6cc483]● Sync active[/]  [dim]Changes sync to your Spotify account[/dim]", id="spotify-status")
                else:
                    yield Static("[#c4a768]▲ Permissions update available[/]  [dim]Re-link once to enable two-way sync[/dim]", id="spotify-status")

                is_adv = getattr(self.app, "advanced_mode", False)
                with Horizontal(id="spotify-actions"):
                    yield Button("Sync" if is_adv else r"\[S] Sync", variant="primary", id="btn-sync")
                    relink_label = "Re-link" if is_adv else r"\[R] Re-link"
                    if not can_modify:
                        yield Button(relink_label, variant="warning", id="btn-relink")
                    else:
                        yield Button(relink_label, id="btn-relink")
                    yield Button("Log Out" if is_adv else r"\[O] Log Out", variant="error", id="btn-logout")
                    yield Button("Close" if is_adv else r"\[Esc] Close", id="btn-close")

                yield Static("", id="spotify-instruction")
                yield Static("", id="spotify-hint")

            else:
                is_adv = getattr(self.app, "advanced_mode", False)
                if self.first_run:
                    yield Static("Connect your Spotify account to sync your playlists and Liked Songs into Spoff, and enable two-way synchronization. You can also skip and use local offline playback anytime.", id="spotify-desc")
                else:
                    yield Static("Connect your Spotify account to sync your playlists and Liked Songs into Spoff, and enable two-way synchronization.", id="spotify-desc")
                yield Static("[dim]Not connected[/dim]", id="spotify-status")

                with Horizontal(id="spotify-actions"):
                    yield Button("Log In" if is_adv else r"\[Enter] Log In", variant="primary", id="btn-login")
                    if is_adv:
                        close_label = "Skip" if self.first_run else "Cancel"
                    else:
                        close_label = r"\[Esc] Skip" if self.first_run else r"\[Esc] Cancel"
                    yield Button(close_label, id="btn-close")

                yield Static("", id="spotify-instruction")
                yield Static("", id="spotify-hint")

    def on_mount(self) -> None:
        is_connected = bool(self.auth_session and (self.auth_session.get("access_token") or self.auth_session.get("refresh_token")))
        if is_connected:
            try:
                self.query_one("#btn-sync", Button).focus()
            except Exception:
                pass
            # Auto-refresh user profile in background if missing
            user = self.auth_session.get("user", {})
            if not user or not user.get("display_name"):
                def _fetch_bg():
                    tok = get_valid_token()
                    if tok and self.auth_session is not None:
                        prof = fetch_current_user_profile(tok)
                        if prof:
                            with storage_transaction():
                                current = load_spotify_auth()
                                if not current or current.get("access_token") != tok:
                                    return
                                current["user"] = prof
                                save_spotify_auth(current)
                            name = prof.get("display_name") or prof.get("id") or "Spotify User"
                            email = prof.get("email") or ""
                            line = f"User    [#e2e2e2]{escape(str(name))}[/]"
                            if email:
                                line += f"  [#555555]({escape(str(email))})[/]"
                            def _update():
                                if not self.is_mounted:
                                    return
                                self.auth_session = current
                                try:
                                    self.query_one("#spotify-user-info", Static).update(line)
                                except Exception:
                                    pass
                            self.app.call_from_thread(_update)
                threading.Thread(target=_fetch_bg, daemon=True).start()
        else:
            try:
                self.query_one("#btn-login", Button).focus()
            except Exception:
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-sync":
            self.action_sync_library()
        elif btn_id == "btn-relink":
            self.action_relink_account()
        elif btn_id == "btn-logout":
            self.action_logout_account()
        elif btn_id == "btn-close":
            self.action_dismiss_modal()
        elif btn_id == "btn-login":
            self.start_browser_login()

    def action_dismiss_modal(self) -> None:
        self._login_attempt = None
        if self.server:
            self.server.stop()
            self.server = None
        self.dismiss(None)

    def action_sync_library(self) -> None:
        if self.auth_session and (self.auth_session.get("access_token") or self.auth_session.get("refresh_token")):
            self.dismiss("sync_now")

    def action_relink_account(self) -> None:
        self.start_browser_login()

    def action_logout_account(self) -> None:
        if not self.auth_session:
            return

        def _on_confirm(confirmed: Optional[bool]) -> None:
            if not confirmed:
                return
            self._login_attempt = None
            logout_spotify()
            self.auth_session = None
            self.dismiss("logged_out")

        self.app.push_screen(
            ConfirmModal("Log out of Spotify", "Log out? Your library stays in Spoff; sync stops until you log in again.", confirm_label="Log Out"),
            _on_confirm,
        )

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input):
            return

        if event.key in ("s", "S") or event.character in ("s", "S"):
            self.action_sync_library()
            event.prevent_default()
            event.stop()
        elif event.key in ("r", "R") or event.character in ("r", "R"):
            self.action_relink_account()
            event.prevent_default()
            event.stop()
        elif event.key in ("o", "O") or event.character in ("o", "O"):
            self.action_logout_account()
            event.prevent_default()
            event.stop()
        elif event.key in ("q", "Q"):
            self.action_dismiss_modal()
            event.prevent_default()
            event.stop()
        elif event.key in ("left", "up") or event.character in ("h", "k"):
            self.action_focus_prev_button()
            event.prevent_default()
            event.stop()
        elif event.key in ("right", "down") or event.character in ("l", "j"):
            self.action_focus_next_button()
            event.prevent_default()
            event.stop()
        elif event.key in ("enter", "return", "ctrl+m") and not isinstance(self.focused, Button):
            if self.auth_session and (self.auth_session.get("access_token") or self.auth_session.get("refresh_token")):
                self.action_sync_library()
            else:
                self.start_browser_login()
            event.prevent_default()
            event.stop()

    def action_focus_prev_button(self) -> None:
        buttons = [b for b in self.query(Button) if b.display]
        if not buttons:
            return
        if isinstance(self.focused, Button) and self.focused in buttons:
            idx = buttons.index(self.focused)
            prev_idx = (idx - 1) % len(buttons)
            buttons[prev_idx].focus()
        else:
            buttons[-1].focus()

    def action_focus_next_button(self) -> None:
        buttons = [b for b in self.query(Button) if b.display]
        if not buttons:
            return
        if isinstance(self.focused, Button) and self.focused in buttons:
            idx = buttons.index(self.focused)
            next_idx = (idx + 1) % len(buttons)
            buttons[next_idx].focus()
        else:
            buttons[0].focus()

    def start_browser_login(self) -> None:
        if self.is_logging_in:
            return
        self.is_logging_in = True
        self.pkce_verifier, _challenge = generate_pkce_pair()
        auth_url, state = build_auth_url(self.pkce_verifier)

        try:
            self.query_one("#spotify-status", Static).update("[#c4a768]Waiting for authorization in browser...[/]")
            inst = self.query_one("#spotify-instruction", Static)
            inst.update(
                f"[dim]If your browser did not open, visit:[/dim]\n[#4f8a5e]{auth_url}[/]"
            )
            inst.display = True
            hint = self.query_one("#spotify-hint", Static)
            hint.update("[dim]Listening on 127.0.0.1:8989/login  |  Esc: Cancel[/dim]")
            hint.display = True
        except Exception:
            pass

        def _on_callback(code: Optional[str], err: Optional[str]):
            if err:
                def _show_err():
                    try:
                        self.query_one("#spotify-status", Static).update(f"[#c47676]Login failed: {err}[/]")
                    except Exception:
                        pass
                    self.is_logging_in = False
                self.app.call_from_thread(_show_err)
            elif code:
                def _do_proc():
                    self.process_auth_code(code)
                self.app.call_from_thread(_do_proc)

        try:
            self.server = OAuthCallbackServer(port=SPOTIFY_PORT)
            self.server.start(_on_callback, expected_state=state)
        except Exception as e:
            logger.error(f"Failed to start OAuth server: {e}")
            try:
                self.query_one("#spotify-status", Static).update(f"[#c47676]Could not bind port {SPOTIFY_PORT}: {e}[/]")
            except Exception:
                pass
            self.is_logging_in = False
            return

        def _open():
            import webbrowser
            try:
                webbrowser.open(auth_url)
            except Exception as e:
                logger.error(f"Failed to open browser: {e}")
        threading.Thread(target=_open, daemon=True).start()

    def process_auth_code(self, code: str) -> None:
        attempt = object()
        self._login_attempt = attempt
        try:
            self.query_one("#spotify-status", Static).update("[dim]Exchanging tokens and fetching profile...[/dim]")
        except Exception:
            pass
        verifier = self.pkce_verifier or secrets.token_urlsafe(32)

        def _worker():
            tokens = exchange_code_for_tokens(code, verifier)
            if tokens and "access_token" in tokens:
                prof = fetch_current_user_profile(tokens["access_token"])
                if prof:
                    tokens["user"] = prof

            def _finish():
                if not getattr(self, "is_mounted", True) or getattr(self, "_login_attempt", None) is not attempt:
                    return
                if not tokens or not tokens.get("access_token"):
                    try:
                        self.query_one("#spotify-status", Static).update("[#c47676]Token exchange failed. Please try again.[/]")
                    except Exception:
                        pass
                    self.is_logging_in = False
                    return

                save_spotify_auth(tokens)
                if self.server:
                    self.server.stop()
                    self.server = None
                self.dismiss("login_success")

            self.app.call_from_thread(_finish)

        threading.Thread(target=_worker, daemon=True).start()

class UpdateModal(SafeModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", "No", show=False),
        Binding("enter", "confirm", "Update"),
        Binding("y", "confirm", "Yes", show=False),
        Binding("u", "confirm", "Update", show=False),
    ]

    def __init__(self, update_info: Dict[str, Any]):
        super().__init__()
        self.update_info = update_info
        self.is_updating = False
        self._update_complete = False

    def compose(self) -> ComposeResult:
        with Vertical(id="update-dialog"):
            yield Static("SPOFF UPDATE AVAILABLE", id="update-title")
            cur_sha = self.update_info.get("local_sha", "unknown")
            new_sha = self.update_info.get("remote_sha", "latest")
            msg = self.update_info.get("message", "")
            author = self.update_info.get("author", "vrdq")
            date = self.update_info.get("date", "")[:10]

            yield Static(f"Installed: [dim]{escape(cur_sha)}[/]  →  GitHub push: [bold #569f68]{escape(new_sha)}[/]", id="update-versions")
            yield Static(f"Commit: [bold #ffffff]{escape(msg)}[/]  [dim]by {escape(author)} ({escape(date)})[/]", id="update-commit")
            yield Static("Pull latest updates and sync dependencies from github.com/vrdq/spoff?", id="update-prompt")
            yield Static("", id="update-status")
            u_hint = "[bold #569f68]Update Now[/]    [#767676]Later[/]" if getattr(self.app, "advanced_mode", False) else "[bold #569f68][Enter / Y][/] Update Now    [#767676][Esc / N][/] Later"
            yield Static(u_hint, id="update-hint")

    def action_confirm(self) -> None:
        if getattr(self, "_update_complete", False):
            self.dismiss(True)
            return
        if self.is_updating:
            return
        self.is_updating = True
        try:
            self.query_one("#update-status", Static).update("[bold #c4a768]Pulling latest changes from GitHub...[/]")
        except Exception:
            pass

        def _worker():
            try:
                ok, msg = perform_update()
            except Exception as exc:
                logger.exception("Update failed")
                ok, msg = False, f"Update failed: {exc}"

            def _done():
                try:
                    if ok:
                        self._update_complete = True
                        try:
                            self.query_one("#update-status", Static).update(f"[bold #569f68]{escape(msg)} Restart Spoff to apply.[/]")
                            self.query_one("#update-hint", Static).update("[dim]Press Esc or Enter to close[/dim]")
                        except Exception:
                            pass
                    else:
                        try:
                            self.query_one("#update-status", Static).update(f"[bold #c47676]{escape(msg)}[/]")
                        except Exception:
                            pass
                finally:
                    self.is_updating = False
            self.app.call_from_thread(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def action_cancel(self) -> None:
        self.dismiss(False)

class HelpModal(SafeModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("enter", "dismiss_modal", "Close"),
        Binding("space", "dismiss_modal", "Close", show=False),
        Binding("colon", "dismiss_modal", "Close", show=False),
        Binding("shift+semicolon", "dismiss_modal", "Close", show=False),
        Binding("question_mark", "dismiss_modal", "Close", show=False),
        Binding("q", "dismiss_modal", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        kb = getattr(self.app, "keybindings", {})

        def cap(key: str) -> str:
            return f"[#e2e2e2 on #2a2a2a]\u2800{escape(key)}\u2800[/]"

        def kcap(act_id: str, default: str) -> str:
            return cap(format_key_display(kb.get(act_id, default)))

        def make_sec_table(rows: List[Tuple[str, str]]) -> Table:
            t = Table.grid(padding=(0, 2))
            t.add_column(width=22, no_wrap=True)
            t.add_column(style="#888888", no_wrap=True)
            for k, d in rows:
                t.add_row(k, d)
            return t

        sep = "[#555555] / [/]"
        comma = "[#555555], [/]"

        k_s1 = kcap("nav_search", "1")
        k_s2 = kcap("nav_playlist", "2")
        k_s3 = kcap("nav_offline", "3")
        k_s4 = kcap("nav_lyrics", "4")
        k_s5 = kcap("nav_liked", "5")
        k_sett = kcap("open_settings", ",")
        k_srch = kcap("focus_search", "/")
        k_play = kcap("toggle_play", "space")
        k_shuf = kcap("toggle_shuffle", "s")
        k_rep = kcap("toggle_repeat", "r")
        k_prev = kcap("prev_track", "p")
        k_next = kcap("next_track", "n")
        k_eng = kcap("switch_engine", "ctrl+e")
        k_share = kcap("share_track", "c")

        nav_rows = [
            (f"{k_s1} {k_s2} {k_s3} {k_s5}", "Search / Playlists / Offline / Liked"),
            (k_s4, "Synchronized lyrics view"),
            (f"{cap('h')}{sep}{cap('→')}{comma}{cap('Tab')}", "Switch sidebar / main pane"),
            (f"{cap('j')}{sep}{cap('k')}{comma}{cap('Arrows')}", "Navigate table rows"),
            (f"{cap('gg')}{sep}{cap('Home')}", "Jump to top row"),
            (f"{cap('G')}{sep}{cap('End')}", "Jump to bottom row"),
            (f"{cap('Ctrl+d')}{sep}{cap('Ctrl+u')}", "Scroll page down / up"),
            (cap("Tab"), "Cycle sidebar / table"),
            (cap("Enter"), "Play track / open playlist"),
            (f"{cap('k')} [dim](top row)[/dim]", "Jump into input box"),
            (cap("Esc"), "Unfocus / back to playlist"),
        ]

        k_dl = kcap("download_offline", "b")
        k_vis_cycle = kcap("toggle_visualizer", "v")
        k_vis_toggle = kcap("toggle_vis_on_off", "V")

        playback_rows = [
            (f"{k_play}{comma}{cap('F8')}", "Play / pause toggle"),
            (k_shuf, "Toggle shuffle mode"),
            (k_rep, "Cycle repeat (off / all / 1)"),
            (f"{k_prev}{sep}{k_next}{comma}{cap('F7/F9')}", "Previous / next track"),
            (k_dl, "Download song / playlist offline"),
            (k_share, "Copy track link to clipboard"),
            (f"{cap('Left')}{sep}{cap('Right')}", "Seek -/+ 5 seconds"),
            (f"{k_vis_cycle}{comma}{cap('Click')}", "Cycle visualizer mode"),
            (f"{k_vis_toggle}{comma}{cap('Shift+V')}", "Toggle visualizer on / off"),
            (cap("C"), "Cycle visualizer color theme"),
            (f"{cap('e')}{sep}{cap('E')}", "Parametric EQ / toggle bypass"),
            (cap("F1"), "Mute / unmute audio"),
            (f"{cap('F2')}{sep}{cap('F3')}", "Volume down / up 5%"),
        ]

        seek_rows = [
            (f"{cap('Left')}{sep}{cap('Right')}", "Seek -/+ 5s on bar"),
            (f"{cap('h')}{sep}{cap('l')}", "Seek -/+ 5s on bar"),
            (f"{cap('H')}{sep}{cap('L')}", "Fast seek -/+ 15s on bar"),
            (f"{cap('0')} – {cap('9')}", "Jump to 0% – 90% of song"),
            (f"{cap('Enter')}{comma}{cap('Click')}", "Jump to lyric timestamp"),
            (f"{cap('Esc')}{sep}{cap('k')}", "Return to table"),
        ]

        k_like = kcap("like_track", "l")
        k_spot = kcap("open_spotify_auth", "L")
        k_add = kcap("add_to_playlist", "a")
        k_share_pl = kcap("share_playlist", "y")
        k_del = kcap("delete_item", "d")
        k_del_pl = kcap("delete_playlist", "D")
        k_ren_pl = kcap("rename_playlist", "R")
        k_cln_pl = kcap("clone_playlist", "Y")
        k_pl_sett = kcap("playlist_settings", "S")
        k_imp = kcap("focus_import", "i")
        k_upd = kcap("check_update", "u")
        k_quit = kcap("quit_app", "q")

        playlist_rows = [
            (f"{cap('J')}{sep}{cap('K')}{comma}{cap('Shift+↑↓')}", "Reorder songs in playlist"),
            (k_like, "Like / unlike song (Spotify sync)"),
            (f"{k_add}{comma}{cap('+')}", "Add track to playlist"),
            (f"{k_ren_pl}{comma}{cap('F2')}", "Rename selected playlist"),
            (f"{k_cln_pl}{comma}{cap('Alt+c')}", "Clone / copy playlist"),
            (k_pl_sett, "Playlist settings (name, public)"),
            (k_share_pl, "Copy playlist link to clipboard"),
            (k_imp, "New playlist / import link"),
            (k_spot, "Spotify login & sync"),
            (k_sett, "Settings & rebind keys"),
            (k_srch, "Focus search box"),
            (k_del, "Remove track / playlist"),
            (k_del_pl, "Delete whole playlist"),
            (k_eng, "Switch engine (YTM / Spotify)"),
            (k_upd, "Check / pull updates"),
            (k_quit, "Quit Spoff"),
        ]

        with Vertical(id="help-dialog"):
            with Horizontal(id="help-header-bar"):
                yield Static("Keybindings", id="help-title")
                h_close = "" if getattr(self.app, "advanced_mode", False) else "[dim]esc[/dim]"
                yield Static(h_close, id="help-close-hint")

            with Horizontal(id="help-body"):
                with Vertical(classes="help-col"):
                    yield Static("Navigation & views", classes="help-sec-title")
                    yield Static(make_sec_table(nav_rows), classes="help-sec-table")
                    yield Static("Playback controls", classes="help-sec-title")
                    yield Static(make_sec_table(playback_rows), classes="help-sec-table")

                with Vertical(id="help-col-sep"):
                    pass

                with Vertical(classes="help-col"):
                    yield Static("Seek & timestamps", classes="help-sec-title")
                    yield Static(make_sec_table(seek_rows), classes="help-sec-table")
                    yield Static("Playlists & library", classes="help-sec-title")
                    yield Static(make_sec_table(playlist_rows), classes="help-sec-table")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class EqualizerModal(SafeModalScreen[None]):
    """
    Studio-grade 10-band Parametric Equalizer Modal Screen.
    Provides live interactive manipulation of RBJ biquad filters,
    digital headroom anti-clipping metering, real-time MPV IPC audio graph compilation,
    dynamic high-resolution Braille frequency response curve visualization,
    built-in acoustic calibration presets (including Samsung AKG Master Reference),
    A-B bypass testing, and EqualizerAPO export.
    """
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", priority=True),
        Binding("q", "dismiss_modal", "Close", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("left", "gain_down", "Gain -0.5dB", show=False),
        Binding("h", "gain_down", "Gain -0.5dB", show=False),
        Binding("right", "gain_up", "Gain +0.5dB", show=False),
        Binding("l", "gain_up", "Gain +0.5dB", show=False),
        Binding("shift+left", "gain_down_fast", "Gain -2.0dB", show=False),
        Binding("H", "gain_down_fast", "Gain -2.0dB", show=False),
        Binding("shift+right", "gain_up_fast", "Gain +2.0dB", show=False),
        Binding("L", "gain_up_fast", "Gain +2.0dB", show=False),
        Binding("left_square_bracket", "q_down", "Q -0.1", show=False),
        Binding("right_square_bracket", "q_up", "Q +0.1", show=False),
        Binding("left_curly_bracket", "freq_down", "Freq -5%", show=False),
        Binding("right_curly_bracket", "freq_up", "Freq +5%", show=False),
        Binding("less_than", "freq_down", "Freq -5%", show=False),
        Binding("greater_than", "freq_up", "Freq +5%", show=False),
        Binding("comma", "freq_down", "Freq -5%", show=False),
        Binding("full_stop", "freq_up", "Freq +5%", show=False),
        Binding("t", "cycle_filter_type", "Type", show=False),
        Binding("space", "toggle_band", "Toggle Band", show=False),
        Binding("b", "toggle_bypass", "Bypass A-B", show=False),
        Binding("B", "toggle_bypass", "Bypass A-B", show=False),
        Binding("p", "next_preset", "Next Preset", show=False),
        Binding("P", "prev_preset", "Prev Preset", show=False),
        Binding("a", "auto_headroom", "Auto Headroom", show=False),
        Binding("A", "auto_headroom", "Auto Headroom", show=False),
        Binding("r", "reset_preset", "Reset Preset", show=False),
        Binding("c", "copy_apo", "Copy APO", show=False),
        Binding("C", "copy_apo", "Copy APO", show=False),
        Binding("s", "open_settings", "EQ Settings", show=False),
        Binding("S", "open_settings", "EQ Settings", show=False),
    ]

    def __init__(self, engine: ParametricEQEngine):
        super().__init__()
        self.engine: ParametricEQEngine = engine

    def compose(self) -> ComposeResult:
        with Vertical(id="eq-dialog"):
            with Horizontal(id="eq-header-bar"):
                yield Static("EQUALIZER", id="eq-title")
                yield Static("", id="eq-status-pill")
                yield Static("[dim]esc[/dim]", id="eq-close-hint")

            with Vertical(id="eq-info-panel"):
                yield Static("", id="eq-preset-info")
                yield Static("", id="eq-headroom-meter")

            with Vertical(id="eq-curve-box"):
                yield Static("", id="eq-curve-plot")

            yield DataTable(id="eq-table", cursor_type="row", show_header=True)
            yield Static(
                "[#555555]←→ gain · shift ±2 dB · \\[ ] Q · , . frequency · t type · space on/off\n"
                "b bypass · p P preset · a lower preamp · s settings · c copy · esc close[/]",
                id="eq-footer"
            )

    def on_mount(self) -> None:
        table = self.query_one("#eq-table", DataTable)
        table.cursor_foreground_priority = "renderable"
        table.add_column("Band", key="idx", width=5)
        table.add_column("Type", key="type", width=10)
        table.add_column("Freq", key="freq", width=9)
        table.add_column("Gain", key="gain", width=8)
        table.add_column("", key="curve", width=13)
        table.add_column("Q", key="q", width=5)
        table.add_column("", key="state", width=3)
        table.add_column("Label", key="role", width=30)

        self.rebuild_table()
        self.update_header_and_curve()
        table.focus()

    def update_header_and_curve(self) -> None:
        eng = self.engine
        self.query_one("#eq-status-pill", Static).update("[#c4a768]bypassed[/]  " if eng.bypassed else "")
        self.query_one("#eq-preset-info", Static).update(
            f"[#ffffff]{escape(str(eng.preset_name))}[/]  [#555555]{len(eng.bands)} bands[/]"
        )
        peak_gain, peak_freq = eng.calculate_peak_gain(num_points=250)
        meter = self.query_one("#eq-headroom-meter", Static)
        if eng.bypassed:
            meter.update(f"[#888888]Bypassed, playing unchanged audio. Preamp {eng.preamp_db:+.1f} dB when on.[/]")
        elif peak_gain <= 0.0:
            meter.update(f"Preamp {eng.preamp_db:+.1f} dB    [#888888]peak {peak_gain:+.1f} dB, no clipping[/]")
        else:
            meter.update(
                f"Preamp {eng.preamp_db:+.1f} dB    [#e06c75]peak {peak_gain:+.1f} dB at "
                f"{format_frequency(peak_freq)}, may clip.[/] [#888888]Press a to lower the preamp.[/]"
            )
        self.query_one("#eq-curve-plot", Static).update(render_curve(eng, width=68, height=7))

    def rebuild_table(self) -> None:
        table = self.query_one("#eq-table", DataTable)
        saved_cursor = table.cursor_coordinate
        table.clear()

        preamp_gain = f"{self.engine.preamp_db:+.1f} dB"
        p_bar = format_gain_bar(self.engine.preamp_db)
        p_state = ""
        table.add_row("pre", "", "", preamp_gain, p_bar, "", p_state, "[#888888]Preamp[/]", key="row_preamp")

        for b in self.engine.bands:
            f_str = f"{b.frequency:.0f} Hz" if b.frequency < 1000 else f"{b.frequency/1000:.1f} kHz"
            g_str = f"{b.gain_db:+.1f} dB"
            b_bar = format_gain_bar(b.gain_db)
            q_str = f"{b.q:.2f}"
            s_str = "[#6cc483]on[/]" if b.enabled else "[#5f5f5f]off[/]"
            table.add_row(
                f"{b.index}",
                {"PK": "peak", "LSC": "low shelf", "HSC": "high shelf"}.get(b.filter_type.value, b.filter_type.value),
                f_str,
                g_str,
                b_bar,
                q_str,
                s_str,
                b.label,
                key=f"row_band_{b.index}"
            )

        if saved_cursor is not None and saved_cursor.row < len(table.rows):
            safe_col = min(saved_cursor.column, max(0, len(table.columns) - 1)) if table.columns else 0
            table.move_cursor(row=saved_cursor.row, column=safe_col)

    def _sync_and_refresh(self, row_only: Optional[int] = None) -> None:
        app: Any = self.app
        if hasattr(app, "player") and app.player:
            app.player.apply_eq()
        save_eq_settings(self.engine.to_dict())
        self.update_header_and_curve()
        if row_only is not None and 0 <= row_only <= len(self.engine.bands):
            table = self.query_one("#eq-table", DataTable)
            if row_only == 0:
                p_gain = f"{self.engine.preamp_db:+.1f} dB"
                p_bar = format_gain_bar(self.engine.preamp_db)
                p_state = ""
                table.update_cell("row_preamp", "gain", p_gain)
                table.update_cell("row_preamp", "curve", p_bar)
                table.update_cell("row_preamp", "state", p_state)
            else:
                b = self.engine.bands[row_only - 1]
                f_str = f"{b.frequency:.0f} Hz" if b.frequency < 1000 else f"{b.frequency/1000:.1f} kHz"
                g_str = f"{b.gain_db:+.1f} dB"
                b_bar = format_gain_bar(b.gain_db)
                q_str = f"{b.q:.2f}"
                s_str = "[#6cc483]on[/]" if b.enabled else "[#5f5f5f]off[/]"
                rk = f"row_band_{b.index}"
                table.update_cell(rk, "type", {"PK": "peak", "LSC": "low shelf", "HSC": "high shelf"}.get(b.filter_type.value, b.filter_type.value))
                table.update_cell(rk, "freq", f_str)
                table.update_cell(rk, "gain", g_str)
                table.update_cell(rk, "curve", b_bar)
                table.update_cell(rk, "q", q_str)
                table.update_cell(rk, "state", s_str)
                table.update_cell(rk, "role", b.label)
        else:
            self.rebuild_table()

    def action_cursor_up(self) -> None:
        table = self.query_one("#eq-table", DataTable)
        table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one("#eq-table", DataTable)
        table.action_cursor_down()

    def action_gain_down(self) -> None:
        self._adjust_gain(-0.5)

    def action_gain_up(self) -> None:
        self._adjust_gain(+0.5)

    def action_gain_down_fast(self) -> None:
        self._adjust_gain(-2.0)

    def action_gain_up_fast(self) -> None:
        self._adjust_gain(+2.0)

    def _adjust_gain(self, delta: float) -> None:
        table = self.query_one("#eq-table", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if row == 0:
            new_p = round(self.engine.preamp_db + delta, 1)
            self.engine.set_preamp(new_p)
            self._sync_and_refresh(row_only=0)
        elif 1 <= row <= len(self.engine.bands):
            b = self.engine.bands[row - 1]
            new_g = round(b.gain_db + delta, 1)
            self.engine.set_band(b.index, gain_db=new_g)
            self._sync_and_refresh(row_only=row)

    def action_q_down(self) -> None:
        self._adjust_q(-0.05)

    def action_q_up(self) -> None:
        self._adjust_q(+0.05)

    def _adjust_q(self, delta: float) -> None:
        table = self.query_one("#eq-table", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if 1 <= row <= len(self.engine.bands):
            b = self.engine.bands[row - 1]
            new_q = round(max(0.1, min(25.0, b.q + delta)), 2)
            self.engine.set_band(b.index, q=new_q)
            self._sync_and_refresh(row_only=row)

    def action_freq_down(self) -> None:
        self._adjust_freq(0.95)

    def action_freq_up(self) -> None:
        self._adjust_freq(1.05)

    def _adjust_freq(self, factor: float) -> None:
        table = self.query_one("#eq-table", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if 1 <= row <= len(self.engine.bands):
            b = self.engine.bands[row - 1]
            new_f = round(max(10.0, min(22000.0, b.frequency * factor)), 1)
            self.engine.set_band(b.index, frequency=new_f)
            self._sync_and_refresh(row_only=row)

    def action_cycle_filter_type(self) -> None:
        table = self.query_one("#eq-table", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if 1 <= row <= len(self.engine.bands):
            b = self.engine.bands[row - 1]
            if b.filter_type == FilterType.PEAKING:
                next_t = FilterType.LOW_SHELF
            elif b.filter_type == FilterType.LOW_SHELF:
                next_t = FilterType.HIGH_SHELF
            else:
                next_t = FilterType.PEAKING
            self.engine.set_band(b.index, filter_type=next_t)
            self._sync_and_refresh(row_only=row)

    def action_toggle_band(self) -> None:
        table = self.query_one("#eq-table", DataTable)
        row = table.cursor_row if table.cursor_row is not None else 0
        if row == 0:
            self.action_toggle_bypass()
        elif 1 <= row <= len(self.engine.bands):
            b = self.engine.bands[row - 1]
            self.engine.set_band(b.index, enabled=not b.enabled)
            self._sync_and_refresh(row_only=row)

    def action_toggle_bypass(self) -> None:
        bypassed = self.engine.toggle_bypass()
        self._sync_and_refresh()
        status_lbl = "bypassed" if bypassed else f"on: {self.engine.preset_name}"
        self.notify(f"EQ {status_lbl}")

    def action_next_preset(self) -> None:
        names = [p.name for p in BUILTIN_PRESETS]
        cur_idx = 0
        if self.engine.preset_name in names:
            cur_idx = names.index(self.engine.preset_name)
        next_idx = (cur_idx + 1) % len(BUILTIN_PRESETS)
        self.engine.load_preset(BUILTIN_PRESETS[next_idx])
        self._sync_and_refresh()
        self.notify(f"Preset: {self.engine.preset_name}")

    def action_prev_preset(self) -> None:
        names = [p.name for p in BUILTIN_PRESETS]
        cur_idx = 0
        if self.engine.preset_name in names:
            cur_idx = names.index(self.engine.preset_name)
        prev_idx = (cur_idx - 1) % len(BUILTIN_PRESETS)
        self.engine.load_preset(BUILTIN_PRESETS[prev_idx])
        self._sync_and_refresh()
        self.notify(f"Preset: {self.engine.preset_name}")

    def action_auto_headroom(self) -> None:
        rec = self.engine.auto_preamp_headroom(margin_db=0.5)
        self.engine.set_preamp(rec)
        self._sync_and_refresh(row_only=0)
        self.notify(
            f"Preamp lowered to {rec:+.1f} dB so boosts can't clip"
        )

    def action_reset_preset(self) -> None:
        for p in BUILTIN_PRESETS:
            if p.name == self.engine.preset_name:
                self.engine.load_preset(p)
                break
        else:
            self.engine.load_preset(SAMSUNG_AKG_REFERENCE_PRESET)
        self._sync_and_refresh()
        self.notify(f"Reset {self.engine.preset_name}")

    def action_copy_apo(self) -> None:
        apo = self.engine.to_equalizer_apo()
        copy_to_clipboard(apo, self.app)
        self.notify(
            "Copied as EqualizerAPO text"
        )

    def action_open_settings(self) -> None:
        self.app.push_screen(EQSettingsModal(self.engine))

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        k = event.key
        ch = event.character
        if k in ("escape", "q"):
            self.action_dismiss_modal()
            event.stop()
            event.prevent_default()
        elif k in ("s", "S") or ch in ("s", "S"):
            self.action_open_settings()
            event.stop()
            event.prevent_default()
        elif k in ("left", "h"):
            self.action_gain_down()
            event.stop()
            event.prevent_default()
        elif k in ("right", "l"):
            self.action_gain_up()
            event.stop()
            event.prevent_default()
        elif k in ("shift+left", "H") or ch == "H":
            self.action_gain_down_fast()
            event.stop()
            event.prevent_default()
        elif k in ("shift+right", "L") or ch == "L":
            self.action_gain_up_fast()
            event.stop()
            event.prevent_default()
        elif k == "left_square_bracket" or ch == "[":
            self.action_q_down()
            event.stop()
            event.prevent_default()
        elif k == "right_square_bracket" or ch == "]":
            self.action_q_up()
            event.stop()
            event.prevent_default()
        elif k in ("left_curly_bracket", "less_than", "comma") or ch in ("{", "<", ","):
            self.action_freq_down()
            event.stop()
            event.prevent_default()
        elif k in ("right_curly_bracket", "greater_than", "full_stop") or ch in ("}", ">", "."):
            self.action_freq_up()
            event.stop()
            event.prevent_default()
        elif k == "t" or ch == "t":
            self.action_cycle_filter_type()
            event.stop()
            event.prevent_default()
        elif k == "space" or ch == " ":
            self.action_toggle_band()
            event.stop()
            event.prevent_default()
        elif k in ("b", "B") or ch in ("b", "B"):
            self.action_toggle_bypass()
            event.stop()
            event.prevent_default()
        elif k == "p" or ch == "p":
            self.action_next_preset()
            event.stop()
            event.prevent_default()
        elif k == "P" or ch == "P":
            self.action_prev_preset()
            event.stop()
            event.prevent_default()
        elif k in ("a", "A") or ch in ("a", "A"):
            self.action_auto_headroom()
            event.stop()
            event.prevent_default()
        elif k in ("r", "R") or ch in ("r", "R"):
            self.action_reset_preset()
            event.stop()
            event.prevent_default()
        elif k in ("c", "C") or ch in ("c", "C"):
            self.action_copy_apo()
            event.stop()
            event.prevent_default()


class EQSampleRateToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.cycle_sample_rate()


class EQPrecisionToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.cycle_precision()


class EQAutoHeadroomToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.toggle_auto_headroom()


class EQHeadroomMarginToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.cycle_headroom_margin()


class EQIntersampleGuardToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.toggle_intersample_guard()


class EQCurveStyleToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.cycle_curve_style()


class EQCurveRangeToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.cycle_curve_range()


class EQTargetProfileToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.cycle_target_profile()


class EQImportClipboardAction(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.import_from_clipboard()


class EQExportClipboardAction(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.export_to_clipboard()


class EQResetDefaultsAction(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.reset_to_reference()


class EQOpenLiveEditorAction(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, EQSettingsModal):
            self.screen.open_live_editor()


class EQSettingsModal(SafeModalScreen[None]):
    """
    Studio-grade DSP Engine & Parametric EQ Configuration Modal.
    Provides fine-grained audiophile controls for:
      - Internal DSP sampling rate (44.1k to 192k)
      - Floating-point processing precision (64-bit f64 vs 32-bit f32)
      - Anti-denormal subnormal flush protection (DAZ/FTZ)
      - Automated digital headroom anti-clipping management
      - True-peak inter-sample safety margins
      - Frequency response curve visualization engine & dynamic range
      - Industry acoustic target reference profiles (Samsung AKG, Harman, IEF, DF, FF)
      - Live AutoEQ / Peace clipboard preset import and EqualizerAPO export.
    """
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", priority=True),
        Binding("q", "dismiss_modal", "Close", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "select_or_toggle", "Select", show=False),
        Binding("space", "select_or_toggle", "Toggle", show=False),
        Binding("tab", "switch_focus", "Next Focus", show=False),
        Binding("shift+tab", "switch_focus_back", "Prev Focus", show=False),
        Binding("e", "open_live_editor", "Live EQ", show=False),
        Binding("r", "reset_to_reference", "Reset", show=False),
        Binding("i", "import_from_clipboard", "Import", show=False),
        Binding("c", "export_to_clipboard", "Export", show=False),
    ]

    EQ_TOGGLE_IDS = [
        "eq-opt-target-profile",
        "eq-opt-sample-rate",
        "eq-opt-precision",
        "eq-opt-auto-headroom",
        "eq-opt-headroom-margin",
        "eq-opt-intersample-guard",
        "eq-opt-curve-style",
        "eq-opt-curve-range",
        "eq-act-open-live-editor",
        "eq-act-import-clipboard",
        "eq-act-export-clipboard",
        "eq-act-reset-defaults",
    ]

    def __init__(self, engine: ParametricEQEngine):
        super().__init__()
        self.engine: ParametricEQEngine = engine

    def compose(self) -> ComposeResult:
        with Vertical(id="eq-settings-dialog"):
            with Horizontal(id="eq-settings-header"):
                yield Static("EQUALIZER SETTINGS", id="eq-settings-title")
                yield Static("", id="eq-settings-pill")
                yield Static("[dim]esc[/dim]", id="eq-settings-close-hint")

            yield Static("", id="eq-settings-status-info")
            yield Static("", id="eq-settings-status-sub")

            with Vertical(id="eq-settings-options-container"):
                yield EQTargetProfileToggle(id="eq-opt-target-profile", classes="eq-setting-item")
                yield Static("PROCESSING", classes="eq-settings-section-title")
                yield EQSampleRateToggle(id="eq-opt-sample-rate", classes="eq-setting-item")
                yield EQPrecisionToggle(id="eq-opt-precision", classes="eq-setting-item")
                yield Static("CLIPPING PROTECTION", classes="eq-settings-section-title")
                yield EQAutoHeadroomToggle(id="eq-opt-auto-headroom", classes="eq-setting-item")
                yield EQHeadroomMarginToggle(id="eq-opt-headroom-margin", classes="eq-setting-item")
                yield EQIntersampleGuardToggle(id="eq-opt-intersample-guard", classes="eq-setting-item")
                yield Static("CURVE", classes="eq-settings-section-title")
                yield EQCurveStyleToggle(id="eq-opt-curve-style", classes="eq-setting-item")
                yield EQCurveRangeToggle(id="eq-opt-curve-range", classes="eq-setting-item")
                yield Static("ACTIONS", classes="eq-settings-section-title")
                yield EQOpenLiveEditorAction(id="eq-act-open-live-editor", classes="eq-setting-item")
                yield EQImportClipboardAction(id="eq-act-import-clipboard", classes="eq-setting-item")
                yield EQExportClipboardAction(id="eq-act-export-clipboard", classes="eq-setting-item")
                yield EQResetDefaultsAction(id="eq-act-reset-defaults", classes="eq-setting-item")

            yield Static("", id="eq-settings-status-line")
            yield Static(
                "[dim]enter change · e editor · i import · c copy · r reset · esc close[/dim]",
                id="eq-settings-footer"
            )

    def on_mount(self) -> None:
        self.update_ui()
        try:
            self.query_one("#eq-opt-target-profile", EQTargetProfileToggle).focus()
        except Exception:
            pass

    @staticmethod
    def _row(label: str, value: str, note: str = "", on: Optional[bool] = None) -> str:
        colour = "#5f5f5f" if on is False else ("#6cc483" if on else "#e2e2e2")
        note_part = f"  [#5a5a5a]{escape(note)}[/]" if note else ""
        return f"{label:<22}[{colour}]{escape(value)}[/]{note_part}"

    def update_ui(self) -> None:
        eng = self.engine
        try:
            self.query_one("#eq-settings-pill", Static).update("[#c4a768]bypassed[/]  " if eng.bypassed else "")
            peak_gain, peak_freq = eng.calculate_peak_gain(num_points=250)
            if eng.bypassed:
                level = "[#888888]bypassed[/]"
            elif peak_gain <= 0.0:
                level = f"peak {peak_gain:+.1f} dB, no clipping"
            else:
                level = f"[#e06c75]peak {peak_gain:+.1f} dB at {format_frequency(peak_freq)}, may clip[/]"
            self.query_one("#eq-settings-status-info", Static).update(
                f" Preamp {eng.preamp_db:+.1f} dB    {level}"
            )
            self.query_one("#eq-settings-status-sub", Static).update("")

            self.query_one("#eq-opt-target-profile", Static).update(
                self._row("Preset", eng.preset_name, f"{len(eng.bands)} bands"))
            self.query_one("#eq-opt-sample-rate", Static).update(
                self._row("Sample rate", f"{eng.sample_rate / 1000:g} kHz", "48 kHz suits most devices"))
            self.query_one("#eq-opt-precision", Static).update(
                self._row("Filter precision", "64-bit" if eng.precision == "f64" else "32-bit"))
            self.query_one("#eq-opt-auto-headroom", Static).update(
                self._row("Auto preamp", "on" if eng.auto_headroom else "off",
                          "lowers preamp so boosts can't clip", on=eng.auto_headroom))
            self.query_one("#eq-opt-headroom-margin", Static).update(
                self._row("Safety margin", f"{eng.headroom_margin:.1f} dB", on=None if eng.auto_headroom else False))
            self.query_one("#eq-opt-intersample-guard", Static).update(
                self._row("True-peak guard", "on" if eng.intersample_guard else "off",
                          "extra 0.2 dB", on=eng.intersample_guard))
            self.query_one("#eq-opt-curve-style", Static).update(
                self._row("Style", eng.curve_style))
            self.query_one("#eq-opt-curve-range", Static).update(
                self._row("Range", f"±{int(eng.curve_range_db)} dB"))

            self.query_one("#eq-act-open-live-editor", Static).update("Open the editor")
            self.query_one("#eq-act-import-clipboard", Static).update(
                "Import from clipboard  [#5a5a5a]AutoEQ or EqualizerAPO text[/]")
            self.query_one("#eq-act-export-clipboard", Static).update(
                "Copy as EqualizerAPO text")
            self.query_one("#eq-act-reset-defaults", Static).update(
                f"Reset to {escape(SAMSUNG_AKG_REFERENCE_PRESET.name)}")
        except Exception as e:
            logger.error(f"Error updating EQSettingsModal UI: {e}")

    def _sync_and_save(self) -> None:
        app: Any = self.app
        if hasattr(app, "player") and app.player:
            app.player.apply_eq()
        save_eq_settings(self.engine.to_dict())
        self.update_ui()

    def cycle_sample_rate(self) -> None:
        rates = [44100.0, 48000.0, 88200.0, 96000.0, 192000.0]
        cur = self.engine.sample_rate
        cur_idx = rates.index(cur) if cur in rates else 1
        nxt = rates[(cur_idx + 1) % len(rates)]
        clamped = sum(b.frequency > nxt * 0.495 for b in self.engine.bands)
        self.engine.set_sample_rate(nxt)
        self._sync_and_save()
        adjustment = f" Adjusted {clamped} band(s) to fit this rate." if clamped else ""
        self.query_one("#eq-settings-status-line", Static).update(
            f"Sample rate: {nxt/1000.0:g} kHz.{adjustment}"
        )

    def cycle_precision(self) -> None:
        nxt = "f32" if self.engine.precision == "f64" else "f64"
        self.engine.set_precision(nxt)
        self._sync_and_save()
        lbl = "64-bit" if nxt == "f64" else "32-bit"
        self.query_one("#eq-settings-status-line", Static).update(
            f"Filter precision: {lbl}."
        )

    def toggle_auto_headroom(self) -> None:
        nxt = not self.engine.auto_headroom
        self.engine.set_auto_headroom(nxt)
        if nxt:
            rec = self.engine.auto_preamp_headroom()
            self.engine.set_preamp(rec)
        self._sync_and_save()
        lbl = f"on, preamp {self.engine.preamp_db:+.1f} dB" if nxt else "off, preamp is manual"
        self.query_one("#eq-settings-status-line", Static).update(
            f"Auto preamp {lbl}."
        )

    def cycle_headroom_margin(self) -> None:
        margins = [0.5, 1.0, 1.5, 2.0, 0.0]
        cur = self.engine.headroom_margin
        cur_idx = margins.index(cur) if cur in margins else 0
        nxt = margins[(cur_idx + 1) % len(margins)]
        self.engine.set_headroom_margin(nxt)
        if self.engine.auto_headroom:
            rec = self.engine.auto_preamp_headroom()
            self.engine.set_preamp(rec)
        self._sync_and_save()
        self.query_one("#eq-settings-status-line", Static).update(
            f"Safety margin: {nxt:.1f} dB (preamp {self.engine.preamp_db:+.1f} dB)."
        )

    def toggle_intersample_guard(self) -> None:
        nxt = not self.engine.intersample_guard
        self.engine.set_intersample_guard(nxt)
        if self.engine.auto_headroom:
            rec = self.engine.auto_preamp_headroom()
            self.engine.set_preamp(rec)
        self._sync_and_save()
        lbl = "on" if nxt else "off"
        self.query_one("#eq-settings-status-line", Static).update(
            f"True-peak guard {lbl}."
        )

    def cycle_curve_style(self) -> None:
        styles = ["braille", "blocks", "outline"]
        cur = self.engine.curve_style
        cur_idx = styles.index(cur) if cur in styles else 0
        nxt = styles[(cur_idx + 1) % len(styles)]
        self.engine.set_curve_style(nxt)
        self._sync_and_save()
        self.query_one("#eq-settings-status-line", Static).update(
            f"Curve style: {nxt}."
        )

    def cycle_curve_range(self) -> None:
        ranges = [12.0, 18.0, 24.0]
        cur = self.engine.curve_range_db
        cur_idx = ranges.index(cur) if cur in ranges else 0
        nxt = ranges[(cur_idx + 1) % len(ranges)]
        self.engine.set_curve_range_db(nxt)
        self._sync_and_save()
        self.query_one("#eq-settings-status-line", Static).update(
            f"Curve range: ±{int(nxt)} dB."
        )

    def cycle_target_profile(self) -> None:
        names = [p.name for p in BUILTIN_PRESETS]
        cur = self.engine.preset_name
        cur_idx = names.index(cur) if cur in names else 0
        nxt_preset = BUILTIN_PRESETS[(cur_idx + 1) % len(BUILTIN_PRESETS)]
        self.engine.load_preset(nxt_preset)
        if self.engine.auto_headroom:
            rec = self.engine.auto_preamp_headroom()
            self.engine.set_preamp(rec)
        self._sync_and_save()
        self.query_one("#eq-settings-status-line", Static).update(
            f"Preset: {escape(nxt_preset.name)}."
        )
        
    def import_from_clipboard(self) -> None:
        text = read_from_clipboard(self.app)
        if not text:
            self.query_one("#eq-settings-status-line", Static).update(
                "[#e06c75]The clipboard has no text. Copy an AutoEQ or EqualizerAPO preset first.[/]"
            )
            self.notify("Clipboard is empty or unreadable.", title="AutoEQ Import", severity="warning")
            return

        try:
            preset = parse_equalizer_apo(text, sample_rate=self.engine.sample_rate)
            if not preset:
                self.query_one("#eq-settings-status-line", Static).update(
                    "[#e06c75]No filter lines in the clipboard. Expected lines like: Filter 1: ON PK Fc 100 Hz Gain 2 dB Q 1[/]"
                )
                self.notify("No valid EqualizerAPO / AutoEQ filters found in clipboard.", title="AutoEQ Import", severity="warning")
                return

            self.engine.load_preset(preset)
        except (ValueError, TypeError, OverflowError) as exc:
            self.query_one("#eq-settings-status-line", Static).update(
                f"[bold #e06c75]Cannot import EQ: {escape(str(exc))}[/]"
            )
            self.notify(f"Cannot import EQ: {exc}", title="AutoEQ Import Error", severity="error")
            return
        if self.engine.auto_headroom:
            rec = self.engine.auto_preamp_headroom()
            self.engine.set_preamp(rec)
        self._sync_and_save()
        msg = f"Imported '{preset.name}': {len(preset.bands)} bands, preamp {self.engine.preamp_db:+.1f} dB."
        self.query_one("#eq-settings-status-line", Static).update(escape(msg))
        self.notify(msg, title="AutoEQ Imported")

    def export_to_clipboard(self) -> None:
        apo = self.engine.to_equalizer_apo()
        copied = copy_to_clipboard(apo, self.app)
        if copied:
            self.query_one("#eq-settings-status-line", Static).update(
                "Copied as EqualizerAPO text."
            )
            self.notify("Copied as EqualizerAPO text")
        else:
            self.query_one("#eq-settings-status-line", Static).update("[dim #c47676]Failed to write to clipboard.[/]")

    def reset_to_reference(self) -> None:
        self.engine.load_preset(SAMSUNG_AKG_REFERENCE_PRESET)
        if self.engine.auto_headroom:
            rec = self.engine.auto_preamp_headroom()
            self.engine.set_preamp(rec)
        self._sync_and_save()
        self.query_one("#eq-settings-status-line", Static).update(
            f"Reset to {escape(SAMSUNG_AKG_REFERENCE_PRESET.name)}."
        )
        
    def open_live_editor(self) -> None:
        self.dismiss(None)
        if not isinstance(self.app.screen, EqualizerModal):
            self.app.push_screen(EqualizerModal(self.engine))

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        focused_id = self.focused.id if self.focused else None
        if focused_id in self.EQ_TOGGLE_IDS:
            idx = self.EQ_TOGGLE_IDS.index(focused_id)
            nxt_idx = (idx + 1) % len(self.EQ_TOGGLE_IDS)
            self.query_one(f"#{self.EQ_TOGGLE_IDS[nxt_idx]}", Static).focus()
        else:
            self.query_one("#eq-opt-target-profile", EQTargetProfileToggle).focus()

    def action_cursor_up(self) -> None:
        focused_id = self.focused.id if self.focused else None
        if focused_id in self.EQ_TOGGLE_IDS:
            idx = self.EQ_TOGGLE_IDS.index(focused_id)
            prev_idx = (idx - 1) % len(self.EQ_TOGGLE_IDS)
            self.query_one(f"#{self.EQ_TOGGLE_IDS[prev_idx]}", Static).focus()
        else:
            self.query_one("#eq-opt-target-profile", EQTargetProfileToggle).focus()

    def action_switch_focus(self) -> None:
        self.action_cursor_down()

    def action_switch_focus_back(self) -> None:
        self.action_cursor_up()

    def action_select_or_toggle(self) -> None:
        focused_id = self.focused.id if self.focused else None
        if focused_id == "eq-opt-sample-rate":
            self.cycle_sample_rate()
        elif focused_id == "eq-opt-precision":
            self.cycle_precision()
        elif focused_id == "eq-opt-auto-headroom":
            self.toggle_auto_headroom()
        elif focused_id == "eq-opt-headroom-margin":
            self.cycle_headroom_margin()
        elif focused_id == "eq-opt-intersample-guard":
            self.toggle_intersample_guard()
        elif focused_id == "eq-opt-curve-style":
            self.cycle_curve_style()
        elif focused_id == "eq-opt-curve-range":
            self.cycle_curve_range()
        elif focused_id == "eq-opt-target-profile":
            self.cycle_target_profile()
        elif focused_id == "eq-act-import-clipboard":
            self.import_from_clipboard()
        elif focused_id == "eq-act-export-clipboard":
            self.export_to_clipboard()
        elif focused_id == "eq-act-reset-defaults":
            self.reset_to_reference()
        elif focused_id == "eq-act-open-live-editor":
            self.open_live_editor()

    def on_key(self, event: events.Key) -> None:
        k = event.key
        ch = event.character
        if k in ("escape", "q"):
            self.action_dismiss_modal()
            event.stop()
            event.prevent_default()
        elif k in ("j", "down") or ch == "j":
            self.action_cursor_down()
            event.stop()
            event.prevent_default()
        elif k in ("k", "up") or ch == "k":
            self.action_cursor_up()
            event.stop()
            event.prevent_default()
        elif k in ("enter", "return", "ctrl+m", "space") or ch == " ":
            self.action_select_or_toggle()
            event.stop()
            event.prevent_default()
        elif k in ("i", "I") or ch in ("i", "I"):
            self.import_from_clipboard()
            event.stop()
            event.prevent_default()
        elif k in ("c", "C") or ch in ("c", "C"):
            self.export_to_clipboard()
            event.stop()
            event.prevent_default()
        elif k in ("r", "R") or ch in ("r", "R"):
            self.reset_to_reference()
            event.stop()
            event.prevent_default()
        elif k in ("e", "E") or ch in ("e", "E"):
            self.open_live_editor()
            event.stop()
            event.prevent_default()


class ScrubBar(ProgressBar):
    can_focus = True

    BINDINGS = [
        Binding("h", "scrub_bwd", "Seek -5s", show=False),
        Binding("l", "scrub_fwd", "Seek +5s", show=False),
        Binding("left", "scrub_bwd", "Seek -5s", show=False),
        Binding("right", "scrub_fwd", "Seek +5s", show=False),
        Binding("H", "scrub_bwd_fast", "Seek -15s", show=False),
        Binding("L", "scrub_fwd_fast", "Seek +15s", show=False),
        Binding("shift+left", "scrub_bwd_fast", "Seek -15s", show=False),
        Binding("shift+right", "scrub_fwd_fast", "Seek +15s", show=False),
        Binding("up", "return_to_table", "Return", show=False),
        Binding("k", "return_to_table", "Return", show=False),
        Binding("b", "return_to_table", "Return", show=False),
        Binding("escape", "return_to_table", "Return", show=False),
        Binding("space", "toggle_play", "Play/Pause", show=False),
    ]

    def action_scrub_bwd(self) -> None:
        app: Any = self.app
        app.action_seek_bwd()

    def action_scrub_fwd(self) -> None:
        app: Any = self.app
        app.action_seek_fwd()

    def action_scrub_bwd_fast(self) -> None:
        app: Any = self.app
        app.player.seek(-15)
        app.update_player_hud()

    def action_scrub_fwd_fast(self) -> None:
        app: Any = self.app
        app.player.seek(15)
        app.update_player_hud()

    def action_return_to_table(self) -> None:
        app: Any = self.app
        if app.active_tab == "lyrics":
            app.query_one("#lyrics-table", DataTable).focus()
        else:
            app.query_one("#track-table", DataTable).focus()

    def action_toggle_play(self) -> None:
        app: Any = self.app
        app.action_toggle_play()

    def on_focus(self) -> None:
        app: Any = self.app
        app.update_player_hud()

    def on_blur(self) -> None:
        app: Any = self.app
        app.update_player_hud()

    def on_key(self, event: events.Key) -> None:
        # A substring test would accept "" and multi-digit names; require one digit.
        if len(event.key) == 1 and event.key.isdigit():
            pct = int(event.key) / 10.0
            app: Any = self.app
            app.seek_to_percent(pct)
            event.prevent_default()
            event.stop()

    def on_click(self, event: events.Click) -> None:
        self.focus()
        if self.total and self.total > 0 and self.size.width > 0:
            pct = max(0.0, min(1.0, event.x / float(self.size.width)))
            app: Any = self.app
            app.seek_to_percent(pct)
            event.prevent_default()
            event.stop()

class SidebarSplitter(Widget):
    DEFAULT_CSS = """
    SidebarSplitter {
        width: 1;
        height: 100%;
        background: transparent;
        border-left: solid #262626;
    }
    SidebarSplitter:hover {
        border-left: solid #569f68;
    }
    SidebarSplitter.-dragging {
        border-left: solid #569f68;
        background: #18221b;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._dragging = False
        self._drag_start_x = 0
        self._start_width = 44

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:
            self._dragging = True
            self.add_class("-dragging")
            self._drag_start_x = event.screen_x
            sidebar = self.app.query_one("#sidebar")
            self._start_width = int(sidebar.outer_size.width or (sidebar.styles.width.value if sidebar.styles.width else 44))
            self.capture_mouse(True)
            event.prevent_default()
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            delta_x = event.screen_x - self._drag_start_x
            max_w = max(35, self.app.size.width - 30)
            new_w = max(20, min(max_w, self._start_width + delta_x))
            sidebar = self.app.query_one("#sidebar")
            sidebar.styles.width = new_w
            event.prevent_default()
            event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.remove_class("-dragging")
            self.release_mouse()
            sidebar = self.app.query_one("#sidebar")
            try:
                final_w = int(sidebar.outer_size.width or (sidebar.styles.width.value if sidebar.styles.width else 44))
                save_sidebar_width(final_w)
            except Exception:
                pass
            event.prevent_default()
            event.stop()

    def on_click(self, event: events.Click) -> None:
        if event.chain == 2:
            sidebar = self.app.query_one("#sidebar")
            sidebar.styles.width = 44
            try:
                save_sidebar_width(44)
            except Exception:
                pass
            app: Any = self.app
            if hasattr(app, "notify_user"):
                app.notify_user("Playlists sidebar width reset to default (44).")
            event.prevent_default()
            event.stop()

class SearchEnginePill(Static):
    can_focus = False

    def on_click(self) -> None:
        app: Any = self.app
        if hasattr(app, "toggle_search_engine"):
            app.toggle_search_engine()

def _set_kitty_opacity(op: str) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not (sys.stdout and hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return
    if os.environ.get("KITTY_WINDOW_ID") or os.environ.get("TERM") == "xterm-kitty":
        def _worker():
            try:
                subprocess.run(
                    ["kitten", "@", "set-background-opacity", op],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=0.25,
                )
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

class SpoffTUI(App):
    CSS = """
    * {
        transition: none !important;
        scrollbar-background: transparent;
        scrollbar-color: #555555;
        scrollbar-color-hover: #e2e2e2;
        scrollbar-color-active: #ffffff;
        scrollbar-size-horizontal: 0;
        scrollbar-size-vertical: 1;
    }

    App {
        background: ansi_default;
    }

    Input {
        scrollbar-size-horizontal: 0 !important;
        scrollbar-size-vertical: 0 !important;
    }

    Screen {
        background: ansi_default;
        color: #e2e2e2;
        layout: vertical;
    }

    /* TOP BAR */
    #top-bar {
        height: 3;
        dock: top;
        background: transparent;
        border-bottom: solid #262626;
        padding: 1 2 0 2;
        align: left middle;
    }

    #nav-bar {
        width: 1fr;
        color: #555555;
    }

    /* MAIN TWO-COLUMN SPLIT */
    #main-layout {
        height: 1fr;
        padding: 1 1 0 1;
    }

    #sidebar {
        width: 44;
        height: 100%;
        background: transparent;
        padding: 0 1;
    }

    .pane-title {
        height: 1;
        color: #767676;
        text-style: bold;
        margin-bottom: 1;
    }

    /* DATA TABLES & HEADERS */
    DataTable {
        background: transparent;
    }

    DataTable > .datatable--header {
        background: transparent;
        color: #767676;
        text-style: bold;
    }

    DataTable:focus {
        background-tint: transparent;
    }

    DataTable:focus > .datatable--header {
        background: transparent;
        background-tint: transparent;
        color: #767676;
        text-style: bold;
    }

    DataTable > .datatable--even-row,
    DataTable > .datatable--odd-row {
        background: transparent;
        color: #888888;
    }

    DataTable > .datatable--cursor {
        background: #1c1c1c;
        color: #cccccc;
        text-style: bold;
    }

    DataTable:focus > .datatable--cursor {
        background: #262626;
        color: #ffffff;
        text-style: bold;
    }

    #side-table > .datatable--cursor {
        background: #1c1c1c;
        color: #cccccc;
    }

    #side-table:focus > .datatable--cursor {
        background: #262626;
        color: #ffffff;
    }

    #side-table > .datatable--hover {
        background: transparent;
        color: #ffffff;
    }

    DataTable > .datatable--hover {
        background: transparent;
    }

    DataTable > .datatable--header-hover {
        background: transparent;
        color: #e2e2e2;
    }

    DataTable > .datatable--fixed {
        background: transparent;
        color: #767676;
    }

    #side-table {
        height: 1fr;
        border: none;
        background: transparent;
    }

    #sidebar-hint {
        height: auto;
        color: #929292;
        margin-top: 1;
    }

    #content-pane {
        width: 1fr;
        height: 100%;
        padding: 0 2;
    }

    .action-input {
        background: transparent;
        border: solid #2a2a2a;
        color: #e2e2e2;
        height: 3;
        margin-bottom: 1;
        padding: 0 1;
        scrollbar-size-horizontal: 0 !important;
        scrollbar-size-vertical: 0 !important;
    }

    .action-input:focus {
        border: solid #767676;
    }

    #search-header-row {
        height: 3;
        margin-bottom: 1;
    }

    #search-header-row > #search-box {
        width: 1fr;
        height: 3;
        margin-bottom: 0;
    }

    #engine-selector-pill {
        width: auto;
        height: 3;
        border: solid #2a2a2a;
        background: transparent;
        padding: 0 1;
        margin-left: 1;
        content-align: center middle;
    }

    #engine-selector-pill:hover {
        border: solid #444444;
        color: #ffffff;
    }

    #deck-stats-pill {
        width: auto;
        margin-right: 2;
    }

    #track-table {
        height: 1fr;
        border: none;
        background: transparent;
    }

    #track-table > .datatable--cursor {
        background: #1c1c1c;
        color: #cccccc;
        text-style: bold;
    }

    #track-table:focus > .datatable--cursor {
        background: #262626;
        color: #ffffff;
        text-style: bold;
    }

    #lyrics-pane {
        height: 1fr;
        display: none;
    }

    #lyrics-header {
        height: 1;
        margin-bottom: 1;
        color: #767676;
    }

    #lyrics-table {
        height: 1fr;
        border: none;
        background: transparent;
    }

    #lyrics-table > .datatable--cursor {
        background: #1c1c1c;
        color: #cccccc;
        text-style: bold;
    }

    #lyrics-table:focus > .datatable--cursor {
        background: #262626;
        color: #ffffff;
        text-style: bold;
    }

    #shuf-pill, #rep-pill {
        width: auto;
        margin-right: 1;
    }

    /* BOTTOM TRANSPORT DECK */
    #player-deck {
        dock: bottom;
        height: 5;
        background: transparent;
        border-top: solid #262626;
        padding: 0 2;
    }

    #player-deck:focus-within {
        border-top: solid #383838;
    }

    /* MODAL: ADD TO PLAYLIST */
    AddToPlaylistModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #modal-dialog {
        width: 70;
        height: auto;
        max-height: 22;
        background: #181818;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #modal-title {
        text-style: bold;
        color: #ffffff;
        margin-bottom: 1;
    }

    #modal-track-info {
        color: #767676;
        margin-bottom: 1;
    }

    #modal-input {
        background: transparent;
        border: solid #2a2a2a;
        color: #e2e2e2;
        height: 3;
        margin-bottom: 1;
        padding: 0 1;
        scrollbar-size-horizontal: 0 !important;
        scrollbar-size-vertical: 0 !important;
    }

    #modal-input:focus {
        border: solid #767676;
    }

    #modal-subtitle {
        color: #555555;
        text-style: bold;
        margin-bottom: 1;
    }

    #modal-table {
        height: auto;
        max-height: 8;
        border: none;
        background: transparent;
        scrollbar-size-horizontal: 0 !important;
        scrollbar-size-vertical: 1;
    }

    #modal-table > .datatable--cursor {
        background: #262626;
        color: #ffffff;
        text-style: bold;
    }

    #modal-hint {
        color: #555555;
        margin-top: 1;
    }

    /* MODAL: CONFIRM */
    ConfirmModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #confirm-dialog {
        width: 56;
        height: auto;
        background: #181818;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #confirm-title {
        text-style: bold;
        color: #ffffff;
        margin-bottom: 1;
    }

    #confirm-message {
        color: #e2e2e2;
        margin-bottom: 1;
    }

    #confirm-hint {
        color: #767676;
        margin-top: 1;
    }

    /* MODAL: RENAME & CLONE PLAYLIST & DUPLICATE TRACK & FILTER & DELETE PLAYLIST */
    RenamePlaylistModal, ClonePlaylistModal, DuplicateTrackModal, FilterTracksModal, DeletePlaylistModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #rename-dialog, #clone-dialog, #duplicate-dialog, #filter-dialog, #delete-playlist-dialog {
        width: 66;
        height: auto;
        background: #181818;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #filter-dialog {
        height: 18;
    }

    #filter-table {
        height: 10;
        background: #121212;
        border: solid #222222;
        margin-top: 1;
        margin-bottom: 1;
    }

    #duplicate-actions {
        margin-top: 1;
        color: #dcdfe4;
    }

    #rename-title, #clone-title {
        text-style: bold;
        color: #ffffff;
        margin-bottom: 1;
    }

    #delete-playlist-title {
        text-style: bold;
        color: #ff5555;
        margin-bottom: 1;
    }

    #delete-playlist-warning {
        color: #e2e2e2;
        margin-bottom: 1;
    }

    #delete-playlist-instruction {
        color: #888888;
        margin-bottom: 1;
    }

    #rename-sub, #clone-sub {
        color: #888888;
        margin-bottom: 1;
    }

    #rename-input, #clone-input {
        margin-bottom: 1;
        background: #121212;
        border: solid #333333;
        color: #ffffff;
    }

    #delete-playlist-input {
        margin-bottom: 1;
        background: #121212;
        border: solid #442222;
        color: #ffffff;
    }

    #rename-input:focus, #clone-input:focus {
        border: solid #569f68;
    }

    #delete-playlist-input:focus {
        border: solid #ff5555;
    }

    #rename-hint, #clone-hint, #delete-playlist-hint {
        color: #767676;
    }

    #notification-line {
        height: 1;
        color: #929292;
        text-style: italic;
    }

    /* TOAST / NOTIFICATION POPUPS */
    ToastRack {
        dock: bottom;
        align: right bottom;
        margin-bottom: 5;
        width: 1fr;
        max-width: 100%;
        height: auto;
        background: transparent;
        overflow-y: hidden;
        scrollbar-size-vertical: 0;
    }

    ToastHolder {
        align-horizontal: right;
        width: 1fr;
        height: auto;
    }

    Toast {
        width: auto;
        min-width: 38;
        max-width: 76;
        height: auto;
        background: #161616;
        color: #d0d0d0;
        border: solid #2c2c2c;
        border-left: solid #569f68;
        padding: 0 2;
        margin-top: 1;
        margin-right: 0;
    }

    Toast .toast--title {
        color: #569f68;
        text-style: bold;
    }

    Toast.-information {
        background: #161616;
        border: solid #2c2c2c;
        border-left: solid #569f68;
        color: #d0d0d0;
    }

    Toast.-information .toast--title {
        color: #569f68;
        text-style: bold;
    }

    Toast.-warning {
        background: #161616;
        border: solid #2c2c2c;
        border-left: solid #c4a768;
        color: #d0d0d0;
    }

    Toast.-warning .toast--title {
        color: #c4a768;
        text-style: bold;
    }

    Toast.-error {
        background: #161616;
        border: solid #2c2c2c;
        border-left: solid #e06c75;
        color: #d0d0d0;
    }

    Toast.-error .toast--title {
        color: #e06c75;
        text-style: bold;
    }

    #deck-line-1 {
        height: 1;
    }

    #deck-track {
        width: 1fr;
    }

    #deck-line-2 {
        height: 1;
        align: left middle;
        padding: 0;
    }

    #time-elapsed, #time-total {
        width: 6;
        color: #767676;
    }

    #deck-line-2:focus-within > #time-elapsed,
    #deck-line-2:focus-within > #time-total {
        color: #ffffff;
        text-style: bold;
    }

    #playback-bar {
        width: 1fr;
        margin: 0 1;
        color: #ffffff;
    }

    #playback-bar > Bar > .bar--bar {
        color: #767676;
        background: #262626;
    }

    #playback-bar > Bar > .bar--complete {
        color: #767676;
        background: #262626;
    }

    #playback-bar:focus > Bar > .bar--bar {
        color: #569f68;
        background: #262626;
    }

    #playback-bar:focus > Bar > .bar--complete {
        color: #569f68;
        background: #262626;
    }

    #deck-line-3 {
        height: 1;
        color: #767676;
    }

    /* MODAL: HELP */
    HelpModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #help-dialog {
        width: 114;
        max-width: 96%;
        height: auto;
        background: #141414;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #help-header-bar {
        height: 2;
        width: 100%;
        border-bottom: solid #222222;
        margin-bottom: 1;
    }

    #help-title {
        width: 1fr;
        text-style: bold;
        color: #ffffff;
    }

    #help-close-hint {
        width: auto;
        color: #555555;
    }

    #help-body {
        height: auto;
        width: 100%;
    }

    .help-col {
        width: 1fr;
        height: auto;
    }

    #help-col-sep {
        width: 1;
        height: 100%;
        border-left: solid #222222;
        margin: 0 1;
    }

    .help-sec-title {
        height: 1;
        margin-top: 1;
        margin-bottom: 0;
        text-style: bold;
        color: #4f8a5e;
    }

    .help-sec-table {
        margin-bottom: 1;
    }

    /* MODAL: EQUALIZER */
    EqualizerModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #eq-dialog {
        width: 108;
        max-width: 98%;
        height: auto;
        max-height: 94%;
        background: #141414;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #eq-header-bar {
        height: 2;
        width: 100%;
        border-bottom: solid #222222;
        margin-bottom: 0;
    }

    #eq-title {
        width: 1fr;
        text-style: bold;
        color: #ffffff;
    }

    #eq-status-pill {
        width: auto;
        margin-right: 2;
        text-style: bold;
    }

    #eq-close-hint {
        width: auto;
        color: #555555;
    }

    #eq-info-panel {
        height: auto;
        width: 100%;
        margin-top: 1;
        margin-bottom: 1;
    }

    #eq-curve-box {
        height: auto;
        width: 100%;
        background: #0d0d0d;
        border: solid #222222;
        padding: 0 1;
        margin-bottom: 1;
    }

    #eq-curve-plot {
        width: 100%;
        height: auto;
    }

    #eq-table {
        height: 13;
        background: transparent;
        margin-bottom: 1;
    }

    #eq-footer {
        height: auto;
        width: 100%;
        color: #767676;
    }

    /* MODAL: EQ SETTINGS */
    EQSettingsModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #eq-settings-dialog {
        width: 78;
        max-width: 98%;
        height: auto;
        max-height: 94%;
        overflow-y: auto;
        background: #141414;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #eq-settings-header {
        height: 2;
        width: 100%;
        border-bottom: solid #222222;
        margin-bottom: 1;
    }

    #eq-settings-title {
        width: 1fr;
        text-style: bold;
        color: #ffffff;
    }

    #eq-settings-pill, #eq-settings-close-hint {
        width: auto;
        color: #555555;
    }

    #eq-settings-status-info {
        color: #888888;
    }

    #eq-settings-status-sub {
        height: 0;
    }

    #eq-settings-options-container {
        height: auto;
        width: 100%;
        margin-bottom: 1;
    }

    .eq-settings-section-title {
        height: 1;
        width: 100%;
        color: #4f8a5e;
        margin-top: 1;
        text-style: bold;
    }

    #eq-settings-status-line {
        height: 1;
        width: 100%;
        color: #888888;
    }

    #eq-settings-footer {
        height: auto;
        width: 100%;
        color: #555555;
    }

    /* MODAL: SPOTIFY AUTH */
    SpotifyAuthModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #spotify-dialog {
        width: 80;
        height: auto;
        background: #141414;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #spotify-header-bar {
        height: 2;
        width: 100%;
        border-bottom: solid #222222;
        margin-bottom: 1;
    }

    #spotify-title {
        width: 1fr;
        text-style: bold;
        color: #ffffff;
    }

    #spotify-close-hint {
        width: auto;
        color: #555555;
    }

    #spotify-user-info {
        color: #e2e2e2;
        margin-bottom: 0;
    }

    #spotify-desc {
        color: #888888;
        margin-bottom: 1;
    }

    #spotify-status {
        color: #6cc483;
        margin-bottom: 1;
    }

    #spotify-instruction {
        display: none;
        color: #888888;
        margin-top: 1;
        margin-bottom: 0;
    }

    #spotify-hint {
        display: none;
        color: #555555;
        margin-top: 1;
        margin-bottom: 0;
    }

    #spotify-actions {
        width: 100%;
        height: auto;
        margin-top: 1;
        margin-bottom: 0;
        align: left middle;
    }

    #spotify-actions > Button {
        margin-right: 1;
        height: 3;
        min-width: 14;
        background: #222222;
        color: #e2e2e2;
        border: tall #333333;
        padding: 0 1;
    }

    #spotify-actions > Button:hover {
        background: #2d2d2d;
        border: tall #4f8a5e;
        color: #ffffff;
    }

    #spotify-actions > Button:focus {
        background: #333333;
        border: tall #4f8a5e;
        color: #ffffff;
        text-style: bold;
    }

    #spotify-actions > Button.-primary {
        background: #18271c;
        color: #6cc483;
        border: tall #2d4f36;
    }

    #spotify-actions > Button.-primary:hover {
        background: #203425;
        color: #72b984;
        border: tall #4f8a5e;
    }

    #spotify-actions > Button.-primary:focus {
        background: #4f8a5e;
        color: #131313;
        border: tall #6cc483;
        text-style: bold;
    }

    #spotify-actions > Button.-warning {
        background: #282115;
        color: #c4a768;
        border: tall #564420;
    }

    #spotify-actions > Button.-warning:hover {
        background: #352c1c;
        color: #e2c07a;
        border: tall #c4a768;
    }

    #spotify-actions > Button.-warning:focus {
        background: #c4a768;
        color: #131313;
        border: tall #e2c07a;
        text-style: bold;
    }

    #spotify-actions > Button.-error {
        background: #261717;
        color: #c47676;
        border: tall #562525;
    }

    #spotify-actions > Button.-error:hover {
        background: #341e1e;
        color: #df8888;
        border: tall #c47676;
    }

    #spotify-actions > Button.-error:focus {
        background: #c47676;
        color: #131313;
        border: tall #df8888;
        text-style: bold;
    }

    #spotify-manual-hint {
        color: #767676;
        margin-top: 1;
        margin-bottom: 0;
    }

    #spotify-hint {
        display: none;
        color: #767676;
        margin-top: 1;
        margin-bottom: 0;
    }

    #spotify-pill {
        width: auto;
        margin-right: 2;
        color: #555555;
    }

    #spotify-pill:hover {
        color: #ffffff;
    }

    #settings-pill {
        width: auto;
        margin-right: 0;
        color: #555555;
    }

    #settings-pill:hover {
        color: #ffffff;
    }

    #update-pill {
        width: auto;
        margin-right: 2;
        color: #c4a768;
        text-style: bold;
    }

    #deck-visualizer {
        width: 24;
        height: 1;
        margin-right: 2;
    }

    /* MODAL: SETTINGS & KEYBINDS */
    RebindKeyModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #rebind-dialog {
        width: 70;
        max-width: 92%;
        height: auto;
        background: #141414;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #rebind-header {
        height: 2;
        width: 100%;
        border-bottom: solid #222222;
        margin-bottom: 1;
    }

    #rebind-title {
        width: 1fr;
        text-style: bold;
        color: #ffffff;
    }

    #rebind-close-hint {
        width: auto;
        color: #555555;
    }

    #rebind-action-info {
        height: 1;
        margin-bottom: 0;
    }

    #rebind-curr-info {
        height: 1;
        color: #888888;
        margin-bottom: 1;
    }

    #rebind-capture-container {
        width: 100%;
        height: auto;
        background: #0e0e0e;
        border: solid #222222;
        padding: 1 2;
        margin-bottom: 1;
        align: center middle;
    }

    #rebind-capture-box {
        text-align: center;
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #rebind-capture-box:focus {
        color: #ffffff;
    }

    #rebind-key-display {
        text-align: center;
        width: 100%;
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    #rebind-conflict-warning {
        text-align: center;
        width: 100%;
        height: 1;
    }

    #rebind-buttons {
        height: 3;
        width: 100%;
        align: right middle;
    }

    #rebind-buttons > Button {
        margin-left: 1;
        height: 3;
        min-width: 12;
        background: #222222;
        color: #e2e2e2;
        border: tall #333333;
        padding: 0 1;
    }

    #rebind-buttons > Button:hover {
        background: #2d2d2d;
        border: tall #569f68;
        color: #ffffff;
    }

    #rebind-buttons > Button:focus {
        background: #282828;
        border: tall #444444;
        color: #ffffff;
        text-style: bold;
    }

    SettingsModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #settings-dialog {
        width: 74;
        max-width: 96%;
        height: 94%;
        max-height: 44;
        background: #141414;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #settings-header {
        height: 2;
        width: 100%;
        border-bottom: solid #222222;
        margin-bottom: 1;
    }

    #settings-title {
        width: 1fr;
        text-style: bold;
        color: #ffffff;
    }

    #settings-close-hint {
        width: auto;
        color: #555555;
    }

    #settings-options-container {
        height: auto;
        width: 100%;
        margin-bottom: 1;
    }

    .settings-section, #settings-table-title {
        height: 1;
        color: #4f8a5e;
        text-style: bold;
        margin-top: 1;
    }

    .settings-section:first-of-type {
        margin-top: 0;
    }

    .setting-toggle-item, .eq-setting-item {
        height: 1;
        width: 100%;
        background: transparent;
        color: #9a9a9a;
        padding: 0 1;
        margin: 0;
        border-left: outer transparent;
    }

    /* The one accent in these lists: a green bar marks the focused row. */
    .setting-toggle-item:focus, .eq-setting-item:focus {
        background: #1e1e1e;
        color: #ffffff;
        border-left: outer #569f68;
    }

    #settings-table {
        height: 1fr;
        min-height: 5;
        background: transparent;
    }

    #settings-table > .datatable--cursor {
        background: transparent;
        color: #cccccc;
    }

    #settings-table:focus > .datatable--cursor {
        background: #1e1e1e;
        color: #ffffff;
    }

    #settings-status-line {
        height: 1;
        margin-top: 1;
        color: #888888;
    }

    #settings-footer {
        height: 1;
        color: #555555;
    }

    /* MODAL: PLAYLIST SETTINGS */
    PlaylistSettingsModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #plset-dialog {
        width: 74;
        max-width: 96%;
        height: auto;
        background: #141414;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #plset-header {
        height: 2;
        border-bottom: solid #222222;
        margin-bottom: 1;
    }

    #plset-title {
        width: 1fr;
        text-style: bold;
        color: #ffffff;
    }

    #plset-where {
        width: auto;
    }

    /* Rows match the Settings screen: label left, value right, green bar on focus. */
    .plset-field {
        height: 1;
        padding: 0 1;
        border-left: outer transparent;
    }

    .plset-field:focus, .plset-field:focus-within {
        background: #1e1e1e;
        border-left: outer #569f68;
    }

    .plset-label {
        width: 24;
        color: #9a9a9a;
    }

    .plset-field:focus > .plset-label, .plset-field:focus-within > .plset-label {
        color: #ffffff;
    }

    /* The text box only looks like a box while editing. */
    .plset-input {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
        color: #e2e2e2;
    }

    .plset-input:focus {
        background: #2a2a2a;
        border: none;
    }

    .plset-input > .input--placeholder {
        color: #5f5f5f;
    }

    #plset-footer.has-hints {
        margin-top: 1;
    }

    #plset-footer {
        height: auto;
        color: #555555;
    }

    /* MODAL: UPDATE */
    UpdateModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #update-dialog {
        width: 72;
        height: auto;
        background: #181818;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #update-title {
        text-style: bold;
        color: #ffffff;
        margin-bottom: 1;
    }

    #update-versions {
        color: #cccccc;
        margin-bottom: 1;
    }

    #update-commit {
        color: #e2e2e2;
        margin-bottom: 1;
    }

    #update-prompt {
        color: #767676;
        margin-bottom: 1;
    }

    #update-status {
        color: #569f68;
        margin-bottom: 1;
    }

    #update-hint {
        color: #767676;
        margin-top: 1;
    }
    """
    TITLE = "SPOFF"

    BINDINGS = [
        Binding("space", "toggle_play", "Play/Pause"),
        Binding("q", "quit_app", "Quit"),
        Binding("escape", "clear_or_unfocus", "Back"),
        Binding("right", "seek_fwd", "+5s"),
        Binding("left", "seek_bwd", "-5s"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("f1", "vol_mute", "Mute", show=False),
        Binding("f2", "vol_down", "Vol-", show=False),
        Binding("f3", "vol_up", "Vol+", show=False),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Prev"),
        Binding("audio_prev", "prev_track", "Prev", show=False),
        Binding("audio_play", "toggle_play", "Play/Pause", show=False),
        Binding("audio_pause", "toggle_play", "Play/Pause", show=False),
        Binding("audio_next", "next_track", "Next", show=False),
        Binding("mediaprevioustrack", "prev_track", "Prev", show=False),
        Binding("mediaplaypause", "toggle_play", "Play/Pause", show=False),
        Binding("medianexttrack", "next_track", "Next", show=False),
        Binding("slash", "focus_search", "Search"),
        Binding("f", "find_in_view", "Find Track"),
        Binding("ctrl+f", "find_in_view", "Find Track", show=False),
        Binding("b", "download_offline", "Download Offline"),
        Binding("B", "bulk_download_playlist", "Download Playlist", show=False),
        Binding("shift+b", "bulk_download_playlist", "Download Playlist", show=False),
        Binding("i", "focus_import", "Import"),
        Binding("a", "add_to_playlist", "Add to Playlist"),
        Binding("+", "add_to_playlist", "Add to Playlist", show=False),
        Binding("c", "share_track", "Share Track"),
        Binding("y", "share_playlist", "Share Playlist"),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("r", "toggle_repeat", "Repeat"),
        Binding("L", "open_spotify_auth", "Spotify", show=False),
        Binding("shift+l", "open_spotify_auth", "Spotify", show=False),
        Binding("u", "check_update", "Update", show=False),
        Binding("U", "check_update", "Update", show=False),
        Binding("colon", "show_help", "Help", show=False),
        Binding("shift+semicolon", "show_help", "Help", show=False),
        Binding("comma", "open_settings", "Settings", show=False),
        Binding("1", "nav_search", "Search"),
        Binding("2", "nav_playlist", "Playlist"),
        Binding("3", "nav_offline", "Offline"),
        Binding("4", "nav_lyrics", "Lyrics"),
        Binding("5", "nav_liked", "Liked Songs"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("J", "move_item_down", "Move Down", show=False),
        Binding("K", "move_item_up", "Move Up", show=False),
        Binding("shift+down", "move_item_down", "Move Down", show=False),
        Binding("shift+up", "move_item_up", "Move Up", show=False),
        Binding("h", "focus_sidebar", "Sidebar", show=False),
        Binding("right", "focus_tracks", "Tracks", show=False),
        Binding("l", "like_track", "Like Song", show=False),
        Binding("tab", "toggle_focus", "Switch Pane", show=False, priority=True),
        Binding("e", "open_equalizer", "EQ"),
        Binding("E", "toggle_eq_bypass", "Toggle EQ", show=False),
        Binding("alt+e", "open_eq_settings", "EQ Settings", show=False),
        Binding("R", "rename_playlist", "Rename Playlist", show=False),
        Binding("shift+r", "rename_playlist", "Rename Playlist", show=False),
        Binding("Y", "clone_playlist", "Copy Playlist", show=False),
        Binding("shift+y", "clone_playlist", "Copy Playlist", show=False),
        Binding("alt+c", "clone_playlist", "Copy Playlist", show=False),
        Binding("v", "toggle_visualizer", "Visualizer", show=False),
        Binding("V", "toggle_vis_on_off", "Visualizer On/Off", show=False),
        Binding("shift+v", "toggle_vis_on_off", "Visualizer On/Off", show=False),
    ]

    def _dispatch_mpris(self, callback, *args):
        try:
            loop = getattr(self, "_loop", None)
            if loop and loop.is_running():
                loop.call_soon_threadsafe(callback, *args)
            else:
                self.call_from_thread(callback, *args)
        except Exception as e:
            logger.error(f"Error dispatching MPRIS callback: {e}")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        now = time.monotonic()
        if not getattr(self, "_is_ready", False) or (now - getattr(self, "_mount_time", now) < 0.45):
            return False
        return super().check_action(action, parameters)

    def __init__(self, visualizer_enabled: Optional[bool] = None, notifications_enabled: Optional[bool] = None):
        super().__init__()
        self._thread_id: int = threading.get_ident()
        self._mount_time: float = time.monotonic()
        self._is_ready: bool = False
        self.volume: int = get_saved_volume()
        self.advanced_mode: bool = get_saved_advanced_mode()
        self.custom_keybindings: Dict[str, str] = get_custom_keybindings()
        self.keybindings: Dict[str, str] = {**DEFAULT_KEYBINDINGS, **self.custom_keybindings}
        eq_data = load_eq_settings()
        default_preset = BUILTIN_PRESETS[0] if BUILTIN_PRESETS else SAMSUNG_AKG_REFERENCE_PRESET
        if eq_data:
            try:
                self.eq_engine = ParametricEQEngine.from_dict(eq_data)
            except Exception as e:
                logger.error(f"Error loading saved EQ configuration: {e}")
                self.eq_engine = ParametricEQEngine(default_preset)
        else:
            self.eq_engine = ParametricEQEngine(default_preset)

        self.player = MPVController(initial_volume=self.volume, eq_engine=self.eq_engine)
        self.vis_style: str = get_saved_visualizer_style()
        self.vis_color: str = get_saved_visualizer_color()
        if visualizer_enabled is not None:
            self.vis_enabled: bool = visualizer_enabled
        else:
            self.vis_enabled: bool = get_saved_visualizer_enabled() and (self.vis_style != "off")

        effective_style = "off" if not self.vis_enabled else (self.vis_style if self.vis_style != "off" else "bars")
        self.visualizer = CavaVisualizer(bars=24, style=effective_style, color=self.vis_color)
        mpris_callbacks = {
            "play_pause": lambda: self._dispatch_mpris(self.action_toggle_play),
            "play": lambda: self._dispatch_mpris(self._mpris_play),
            "pause": lambda: self._dispatch_mpris(self._mpris_pause),
            "next": lambda: self._dispatch_mpris(self.action_next_track),
            "prev": lambda: self._dispatch_mpris(self.action_prev_track),
            "stop": lambda: self._dispatch_mpris(self._mpris_stop),
            "seek": lambda sec: self._dispatch_mpris(self._mpris_seek, sec),
            "set_position": lambda sec: self._dispatch_mpris(self._mpris_set_pos, sec),
            "set_volume": lambda vol: self._dispatch_mpris(self._mpris_set_vol, vol),
            "set_loop_status": lambda mode: self._dispatch_mpris(self._mpris_set_loop_status, mode),
            "set_shuffle": lambda val: self._dispatch_mpris(self._mpris_set_shuffle, val),
            "quit": lambda: self._dispatch_mpris(self.action_quit_app),
        }
        self.mpris = MPRISService(mpris_callbacks)
        self.update_info: Optional[Dict[str, Any]] = None
        self._spotify_jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spoff-spotify")
        self._queue_origin: Optional[Dict[str, Any]] = None
        self._committed_playback: Optional[Dict[str, Any]] = None
        self.queue: List[Dict[str, Any]] = []
        self.current_index: int = -1
        self._pending_track: Optional[Dict[str, Any]] = None
        self._bulk_download_in_progress: bool = False
        self._download_statuses: Dict[str, Tuple[str, object]] = {}
        self._status_message: str = ""
        self._status_notice_until: float = 0.0
        self._active_single_downloads: int = 0
        self.playlists: List[Dict[str, Any]] = []
        self.current_playlist_tracks: List[Dict[str, Any]] = []
        self.current_playlist_id: Optional[str] = None
        self.current_liked_tracks: List[Dict[str, Any]] = []
        self.search_results: List[Dict[str, Any]] = []
        self.active_tab: str = "search"
        self._closing: bool = False
        self._failed_indices: set[int] = set()
        self._thread_id: int = threading.get_ident()
        self._play_request_id: int = 0
        self.shuffle_mode: bool = False
        self.repeat_mode: str = "off"  # "off", "all", "one"
        self._shuffle_history: List[int] = []
        self.current_lyrics: Optional[Dict[str, Any]] = None
        self._active_lyric_idx: int = -1
        self.last_browsing_tab: str = "playlist"
        self.search_engine: str = get_saved_search_engine()
        self.transparency: bool = get_saved_transparency()
        self.transparency_opacity: float = get_saved_transparency_opacity()
        self.ansi_color = True
        self.instant_search: bool = get_saved_instant_search()
        self.auto_update: bool = get_saved_auto_update()
        if notifications_enabled is not None:
            self.notifications_enabled: bool = bool(notifications_enabled)
        else:
            self.notifications_enabled: bool = get_saved_notifications_enabled()
        self.player.playback_finished_callback = self.on_track_finished
        if self.transparency:
            self.add_class("transparent-mode")
        atexit.register(self._cleanup_on_exit)

    def _on_ui(self, callback, *args, **kwargs):
        if getattr(self, "_closing", False):
            return
        if threading.get_ident() == getattr(self, "_thread_id", None):
            return callback(*args, **kwargs)
        try:
            return self.call_from_thread(callback, *args, **kwargs)
        except RuntimeError:
            if not getattr(self, "is_running", False):
                try:
                    return callback(*args, **kwargs)
                except Exception:
                    return None
            raise

    def set_advanced_mode(self, enabled: bool) -> None:
        self.advanced_mode = bool(enabled)
        save_advanced_mode(self.advanced_mode)
        self.apply_advanced_mode()

    def get_advanced_mode(self) -> bool:
        return self.advanced_mode

    def set_transparency(self, enabled: bool) -> None:
        self.transparency = bool(enabled)
        save_transparency(self.transparency)
        self.apply_transparency()

    def get_transparency(self) -> bool:
        return self.transparency

    def set_transparency_opacity(self, opacity: float) -> None:
        self.transparency_opacity = max(0.1, min(1.0, float(opacity)))
        save_transparency_opacity(self.transparency_opacity)
        self.apply_transparency()

    def get_transparency_opacity(self) -> float:
        return self.transparency_opacity

    def get_auto_update(self) -> bool:
        return self.auto_update

    def set_auto_update(self, enabled: bool) -> None:
        self.auto_update = bool(enabled)
        save_auto_update(self.auto_update)

    def toggle_advanced_mode(self) -> bool:
        self.advanced_mode = not self.advanced_mode
        save_advanced_mode(self.advanced_mode)
        self.apply_advanced_mode()
        return self.advanced_mode

    def toggle_transparency(self) -> bool:
        self.transparency = not self.transparency
        save_transparency(self.transparency)
        self.apply_transparency()
        return self.transparency

    def toggle_instant_search(self) -> bool:
        self.instant_search = not self.instant_search
        save_instant_search(self.instant_search)
        return self.instant_search

    def set_instant_search(self, enabled: bool) -> None:
        self.instant_search = bool(enabled)
        save_instant_search(self.instant_search)

    def toggle_auto_update(self) -> bool:
        self.auto_update = not self.auto_update
        save_auto_update(self.auto_update)
        return self.auto_update

    def toggle_notifications(self) -> bool:
        self.notifications_enabled = not self.notifications_enabled
        save_notifications_enabled(self.notifications_enabled)
        if not self.notifications_enabled:
            def _clear():
                try:
                    self._status_message = ""
                    self._status_notice_until = 0.0
                    SpoffTUI._render_status_line(self)
                except Exception:
                    pass
            if threading.get_ident() == getattr(self, "_thread_id", None):
                _clear()
            else:
                try:
                    self.call_from_thread(_clear)
                except Exception:
                    pass
        return self.notifications_enabled

    def apply_transparency(self) -> None:
        self.ansi_color = True
        bg_val = "ansi_default" if self.transparency else "#121212"
        self.styles.background = bg_val
        self.set_class(self.transparency, "transparent-mode")
        try:
            for s in self.screen_stack:
                if not isinstance(s, ModalScreen):
                    s.styles.background = bg_val
                    s.set_class(self.transparency, "transparent-mode")
                    s.refresh(layout=True)
        except Exception:
            pass
        try:
            if hasattr(self, "screen") and not isinstance(self.screen, ModalScreen):
                self.screen.styles.background = bg_val
                self.screen.set_class(self.transparency, "transparent-mode")
                self.screen.refresh(layout=True)
        except Exception:
            pass
        _set_kitty_opacity(f"{self.transparency_opacity:.2f}" if self.transparency else "1.0")

    def set_search_engine(self, engine: str) -> None:
        self.search_engine = "spotify" if engine == "spotify" else "ytmusic"
        save_search_engine(self.search_engine)
        self.update_engine_pill()

    def toggle_search_engine(self) -> str:
        self.search_engine = "spotify" if self.search_engine == "ytmusic" else "ytmusic"
        save_search_engine(self.search_engine)
        self.update_engine_pill()
        label = "Spotify" if self.search_engine == "spotify" else "YouTube Music"
        self.notify_user(f"Search engine switched to {label}.")
        try:
            s_box = self.query_one("#search-box", Input)
            q = s_box.value.strip()
            if q and self.active_tab == "search":
                self.do_search(q)
        except Exception:
            pass
        return self.search_engine

    def update_engine_pill(self) -> None:
        try:
            pill = self.query_one("#engine-selector-pill", SearchEnginePill)
            s_box = self.query_one("#search-box", Input)
            if self.search_engine == "spotify":
                pill.update("[#888888]Spotify[/]")
                s_box.placeholder = "Search Spotify (artists, tracks)..."
            else:
                pill.update("[#888888]YouTube Music[/]")
                s_box.placeholder = "Search YouTube Music (artists, tracks)..."
        except Exception:
            pass

    def apply_advanced_mode(self) -> None:
        self._update_nav_bar()
        self.update_spotify_pill()
        self.update_settings_pill()
        self.update_engine_pill()
        try:
            sb_hint = self.query_one("#sidebar-hint", Static)
            if self.advanced_mode:
                sb_hint.styles.display = "none"
            else:
                sb_hint.styles.display = "block"
                sb_hint.update("[dim]Enter: open  |  Del: delete[/dim]")
        except Exception:
            pass

        try:
            deck_l3 = self.query_one("#deck-line-3", Static)
            player_deck = self.query_one("#player-deck", Vertical)
            if self.advanced_mode:
                deck_l3.styles.display = "none"
                player_deck.styles.height = 4
            else:
                deck_l3.styles.display = "block"
                player_deck.styles.height = 5
        except Exception:
            pass

        self.update_player_hud()

    def action_switch_engine(self) -> None:
        self.toggle_search_engine()

    def action_toggle_visualizer(self) -> None:
        self.cycle_visualizer_style()
        style_name = self.visualizer.get_style_name()
        self.notify_user(f"Visualizer: {style_name}")

    def action_toggle_vis_on_off(self) -> None:
        new_state = self.toggle_visualizer()
        status_text = "Enabled" if new_state else "Disabled (Hidden)"
        self.notify_user(f"Visualizer: {status_text}")

    def action_cycle_vis_color(self) -> None:
        self.cycle_visualizer_color()
        color_name = self.visualizer.get_color_name()
        self.notify_user(f"Visualizer Theme: {color_name}")

    def toggle_visualizer(self) -> bool:
        self.vis_enabled = not self.vis_enabled
        save_visualizer_enabled(self.vis_enabled)
        if not self.vis_enabled:
            self.visualizer.stop()
            self.visualizer.set_style("off")
            self.vis_style = "off"
            save_visualizer_style("off")
        else:
            prev_style = get_saved_visualizer_style()
            if prev_style == "off":
                prev_style = "bars"
            self.vis_style = prev_style
            self.visualizer.set_style(prev_style)
            save_visualizer_style(prev_style)
            self.visualizer.start()
        self._apply_visualizer_visibility()
        return self.vis_enabled

    def _apply_visualizer_visibility(self) -> None:
        try:
            widget = self.query_one("#deck-visualizer", VisualizerWidget)
            widget.display = bool(self.vis_enabled and self.visualizer.style != "off")
        except Exception:
            pass

    def cycle_visualizer_style(self) -> str:
        new_style = self.visualizer.cycle_style()
        self.vis_style = new_style
        save_visualizer_style(new_style)
        if new_style == "off":
            self.vis_enabled = False
            save_visualizer_enabled(False)
        else:
            self.vis_enabled = True
            save_visualizer_enabled(True)
        self._apply_visualizer_visibility()
        return new_style

    def cycle_visualizer_color(self) -> str:
        new_color = self.visualizer.cycle_color()
        self.vis_color = new_color
        save_visualizer_color(new_color)
        return new_color

    def save_visualizer_preferences(self) -> None:
        self.vis_style = self.visualizer.style
        self.vis_color = self.visualizer.color
        save_visualizer_style(self.vis_style)
        save_visualizer_color(self.vis_color)

    def set_custom_keybinding(self, action_id: str, new_key: str) -> None:
        if new_key is None:
            new_key = ""
        new_key = str(new_key).strip()
        if new_key == DEFAULT_KEYBINDINGS.get(action_id):
            self.custom_keybindings.pop(action_id, None)
        else:
            self.custom_keybindings[action_id] = new_key
        save_custom_keybindings(self.custom_keybindings)
        self.apply_keybindings()

    def reset_keybinding(self, action_id: str) -> None:
        self.custom_keybindings.pop(action_id, None)
        save_custom_keybindings(self.custom_keybindings)
        self.apply_keybindings()

    def reset_all_keybindings(self) -> None:
        self.custom_keybindings.clear()
        reset_custom_keybindings()
        self.apply_keybindings()

    def apply_keybindings(self) -> None:
        self.keybindings = {**DEFAULT_KEYBINDINGS, **self.custom_keybindings}
        try:
            system_bindings = {k: v for k, v in self._bindings.key_to_bindings.items() if any(getattr(b, "system", False) for b in v)}
            self._bindings.key_to_bindings.clear()
            self._bindings.key_to_bindings.update(system_bindings)
            self._bindings.bind("escape", "clear_or_unfocus", show=False)
            for act_id, key in self.keybindings.items():
                if key:
                    self._bindings.bind(key, act_id, show=False)

            assigned_keys = {k.lower() for k in self.keybindings.values() if k}

            def bind_secondary(key: str, act_id: str):
                if self.keybindings.get(act_id) != "" and key.lower() not in assigned_keys:
                    self._bindings.bind(key, act_id, show=False)

            # Secondary navigational and helper bindings
            bind_secondary("down", "cursor_down")
            bind_secondary("up", "cursor_up")
            bind_secondary("shift+down", "move_item_down")
            bind_secondary("shift+up", "move_item_up")
            bind_secondary("delete", "delete_item")
            bind_secondary("shift+delete", "delete_playlist")
            bind_secondary("+", "add_to_playlist")
            if self.keybindings.get("open_spotify_auth") in ("L", "shift+l"):
                bind_secondary("shift+l", "open_spotify_auth")
            if self.keybindings.get("rename_playlist") in ("R", "shift+r"):
                bind_secondary("shift+r", "rename_playlist")
            if self.keybindings.get("clone_playlist") in ("Y", "shift+y"):
                bind_secondary("shift+y", "clone_playlist")
            bind_secondary("alt+c", "clone_playlist")
            if self.keybindings.get("toggle_vis_on_off") in ("V", "shift+v"):
                bind_secondary("shift+v", "toggle_vis_on_off")

            # Dual playback & volume secondary bindings (hardware Fn & dedicated media keys only)
            bind_secondary("f8", "toggle_play")
            bind_secondary("audio_play", "toggle_play")
            bind_secondary("audio_pause", "toggle_play")
            bind_secondary("mediaplaypause", "toggle_play")

            bind_secondary("f9", "next_track")
            bind_secondary("audio_next", "next_track")
            bind_secondary("medianexttrack", "next_track")

            bind_secondary("f7", "prev_track")
            bind_secondary("audio_prev", "prev_track")
            bind_secondary("mediaprevioustrack", "prev_track")

            bind_secondary("f1", "vol_mute")
            bind_secondary("audio_mute", "vol_mute")
            bind_secondary("f2", "vol_down")
            bind_secondary("audio_lower_volume", "vol_down")
            bind_secondary("f3", "vol_up")
            bind_secondary("audio_raise_volume", "vol_up")
        except Exception:
            pass
        self._update_nav_bar()
        self.update_spotify_pill()
        self.update_settings_pill()
        self.update_player_hud()

    def action_open_settings(self) -> None:
        self.push_screen(SettingsModal())

    def _save_playback_state(self, track: Optional[Dict[str, Any]] = None):
        try:
            committed = getattr(self, "_committed_playback", None)
            if committed is not None and track is None:
                save_last_played(committed)
                return

            t = track or getattr(getattr(self, "player", None), "current_track", None)
            c_idx = getattr(self, "current_index", -1)
            if t:
                origin = getattr(self, "_queue_origin", None)
                if origin and origin.get("tab"):
                    tab = origin["tab"]
                    pid = origin.get("playlist_id") if tab == "playlist" else None
                else:
                    tab = getattr(self, "active_tab", "playlist")
                    pid = getattr(self, "current_playlist_id", None) if tab == "playlist" else None

                # If track was not explicitly passed and we have a diverging queue origin,
                # do not mix pending browsing origin with current_track from previous playlist!
                if track is None and origin is not None:
                    tab = getattr(self, "active_tab", "search")
                    pid = ""

                save_last_played({
                    "playlist_id": pid,
                    "tab": tab,
                    "track_id": t.get("id"),
                    "track_title": t.get("title"),
                    "track_artist": t.get("artist"),
                    "track_index": c_idx,
                })
        except Exception as e:
            logger.debug(f"Could not persist last played state: {e}")

    def _submit_spotify_job(self, fn, *args, **kwargs):
        def _handle_result(res):
            if isinstance(res, tuple) and len(res) == 2:
                ok, msg = res
                if not ok and msg:
                    logger.warning(f"Spotify sync task error: {msg}")
                    if any(w in msg.lower() for w in ("403", "modify", "permission", "scope", "forbidden", "not owner")):
                        self.call_from_thread(self.notify_user, f"Spotify: {msg}")

        jobs = getattr(self, "_spotify_jobs", None)
        if jobs is not None:
            fut = jobs.submit(fn, *args, **kwargs)
            def _on_done(f):
                try:
                    res = f.result()
                    _handle_result(res)
                except Exception as exc:
                    logger.error(f"Spotify job exception: {exc}")
            fut.add_done_callback(_on_done)
            return fut

        def _thread_target():
            try:
                res = fn(*args, **kwargs)
                _handle_result(res)
            except Exception as exc:
                logger.error(f"Spotify thread job exception: {exc}")

        t = threading.Thread(target=_thread_target, daemon=True)
        t.start()
        return t

    def _cleanup_on_exit(self):
        if getattr(self, "_exit_cleaned_up", False):
            return
        self._exit_cleaned_up = True
        try:
            atexit.unregister(self._cleanup_on_exit)
        except Exception:
            pass
        self._closing = True
        self._play_request_id += 1
        try:
            cur_tab = getattr(self, "active_tab", "search")
            cur_pid = getattr(self, "current_playlist_id", None) if cur_tab == "playlist" else None
            save_last_tab(cur_tab, cur_pid)
        except Exception:
            pass
        try:
            self._save_playback_state()
        except Exception:
            pass
        try:
            vol_to_save = self.player.get_volume()
            if isinstance(vol_to_save, (int, float)):
                save_volume(int(vol_to_save))
        except Exception:
            pass
        try:
            if hasattr(self, "_spotify_jobs") and self._spotify_jobs:
                self._spotify_jobs.shutdown(wait=False, cancel_futures=False)
        except Exception:
            pass
        try:
            self.visualizer.stop()
        except Exception:
            pass
        try:
            if self.mpris:
                self.mpris.stop()
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            _set_kitty_opacity("default")
        except Exception:
            pass

    def on_unmount(self):
        self._cleanup_on_exit()

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-bar"):
            yield Static(r"[bold #ffffff]\[1] Search[/]    [#555555]\[2] Playlists    \[3] Offline    \[4] Lyrics    \[5] Liked Songs[/]", id="nav-bar")
            yield Static("", id="update-pill")
            yield Static("[#555555]L: Spotify[/]", id="spotify-pill")
            yield Static("[#555555],: Settings[/]", id="settings-pill")

        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Static("PLAYLISTS", classes="pane-title")
                yield Input(placeholder="Create a playlist or paste a link", id="sidebar-import-input", classes="action-input")
                yield DataTable(id="side-table", cursor_type="row", show_header=False, show_cursor=False)
                yield Static("" if self.advanced_mode else "[dim]Enter: open  |  Del: delete[/dim]", id="sidebar-hint")

            yield SidebarSplitter(id="sidebar-splitter")

            with Vertical(id="content-pane"):
                with Horizontal(id="search-header-row"):
                    yield Input(placeholder="Search YouTube Music...", id="search-box", classes="action-input")
                    yield SearchEnginePill("", id="engine-selector-pill")
                yield DataTable(id="track-table", cursor_type="row", show_header=True)
                with Vertical(id="lyrics-pane"):
                    yield Static("", id="lyrics-header")
                    yield DataTable(id="lyrics-table", cursor_type="row", show_header=False)

        with Vertical(id="player-deck"):
            yield Static("", id="notification-line")
            with Horizontal(id="deck-line-1"):
                yield Static("[dim]No track playing[/dim]", id="deck-track")
                yield Static("", id="deck-stats-pill")
                yield VisualizerWidget(self.visualizer, id="deck-visualizer")
                yield Static("", id="shuf-pill")
                yield Static("", id="rep-pill")
            with Horizontal(id="deck-line-2"):
                yield Static("00:00", id="time-elapsed")
                yield ScrubBar(total=100, show_eta=False, id="playback-bar")
                yield Static("00:00", id="time-total")
            yield Static("Enter: play  |  Space: pause  |  l: like  |  s: shuf  |  r: rep  |  : help  |  q: quit", id="deck-line-3")

    def on_mount(self) -> None:
        self._thread_id = threading.get_ident()
        self.title = "SPOFF"
        try:
            sys.stdout.write("\033]0;SPOFF\007")
            sys.stdout.flush()
        except Exception:
            pass
        self.player.start_mpv()
        self.player.set_volume(self.volume)
        self.apply_transparency()
        saved_sidebar_w = get_saved_sidebar_width()
        self.query_one("#sidebar").styles.width = saved_sidebar_w
        try:
            self.playlists = load_saved_playlists()
        except Exception as e:
            logger.error(f"Failed to load saved playlists on mount: {e}")
            self.playlists = []
        self.apply_advanced_mode()
        self.apply_keybindings()
        self.update_engine_pill()
        self.mpris.start()
        if self.mpris:
            self.mpris.update_volume(self.volume)
            self.mpris.update_loop_status(self.repeat_mode)
            self.mpris.update_shuffle(self.shuffle_mode)
        if self.vis_enabled and self.visualizer.style != "off":
            self.visualizer.start()
        self._apply_visualizer_visibility()
        self.check_github_updates_bg()
        self.backfill_playlists_art_bg()

        st = self.query_one("#side-table", DataTable)
        st.cursor_foreground_priority = "renderable"
        st.add_column("Playlist", width=38)
        for idx, p in enumerate(self.playlists):
            st.add_row(Text(str(p.get("name") or "Untitled")), key=str(idx))

        tt = self.query_one("#track-table", DataTable)
        tt.cursor_foreground_priority = "renderable"
        tt.add_columns("Source", "Title", "Artist", "Duration")

        lt = self.query_one("#lyrics-table", DataTable)
        lt.cursor_foreground_priority = "renderable"
        lt.add_column("Time", width=7)
        lt.add_column("Lyric")

        self.set_interval(0.5, self.update_player_hud)
        logger.info("Spoff engine active.")

        SpoffTUI._restore_last_view_state(self)

        if is_first_launch():
            mark_first_launch_done()
            if not load_spotify_auth():
                self.call_after_refresh(lambda: self.action_open_spotify_auth(first_run=True))
        elif load_spotify_auth():
            self.set_timer(1.0, lambda: self._sync_liked_from_spotify_bg(force=True))

        if not self.notifications_enabled:
            try:
                self.query_one("#notification-line", Static).update("")
            except Exception:
                pass

        try:
            if getattr(self, "_driver", None) and hasattr(self._driver, "write"):
                self._driver.write("\x1b[?25l")
            sys.stdout.write("\x1b[?25l")
            sys.stdout.flush()
        except Exception:
            pass

        self._mount_time = time.monotonic()
        self.set_timer(0.45, self._mark_ready)

    def _mark_ready(self) -> None:
        self._is_ready = True

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if isinstance(event.widget, DataTable) and event.widget.id == "side-table":
            event.widget.show_cursor = True
        try:
            if not isinstance(event.widget, Input):
                if getattr(self, "_driver", None) and hasattr(self._driver, "write"):
                    self._driver.write("\x1b[?25l")
                if hasattr(self, "cursor_position"):
                    self.cursor_position = Offset(0, 0)
                for inp in self.query(Input):
                    if inp is not event.widget:
                        if hasattr(inp, "_pause_blink"):
                            inp._pause_blink(visible=False)
                        inp._cursor_visible = False
                        inp.refresh()
            else:
                if getattr(self, "_driver", None) and hasattr(self._driver, "write"):
                    self._driver.write("\x1b[?25l")
        except Exception:
            pass

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        if isinstance(event.widget, DataTable) and event.widget.id == "side-table":
            event.widget.show_cursor = False
        try:
            if isinstance(event.widget, Input):
                if hasattr(event.widget, "_pause_blink"):
                    event.widget._pause_blink(visible=False)
                event.widget._cursor_visible = False
                event.widget.refresh()
                if getattr(self, "_driver", None) and hasattr(self._driver, "write"):
                    self._driver.write("\x1b[?25l")
                if hasattr(self, "cursor_position"):
                    self.cursor_position = Offset(0, 0)
        except Exception:
            pass

    def post_display_hook(self) -> None:
        super().post_display_hook()
        try:
            if not isinstance(self.focused, Input):
                if getattr(self, "_driver", None) and hasattr(self._driver, "write"):
                    self._driver.write("\x1b[?25l")
        except Exception:
            pass

    def _restore_last_view_state(self) -> None:
        st = self.query_one("#side-table", DataTable)
        tt = self.query_one("#track-table", DataTable)
        last_state = get_saved_last_played()
        saved_tab = get_saved_last_tab() or last_state.get("tab") or "search"
        saved_pid = get_saved_last_playlist_id() or last_state.get("playlist_id")
        saved_tid = last_state.get("track_id")
        saved_title = last_state.get("track_title")
        saved_artist = last_state.get("track_artist")
        saved_t_idx = last_state.get("track_index")

        if saved_tab == "liked":
            try:
                liked = load_liked_songs()
            except Exception as e:
                logger.error(f"Failed to load liked songs during state restoration: {e}")
                liked = []
            target_track_row = 0
            if saved_tid and liked:
                for r_idx, t in enumerate(liked):
                    if t.get("id") == saved_tid:
                        target_track_row = r_idx
                        break
            self.switch_view("liked", select_row=target_track_row if liked else None)
            tt.focus()
        elif saved_tab == "offline":
            try:
                offline_tracks = list(load_offline_index().values())
            except Exception as e:
                logger.error(f"Failed to load offline index during state restoration: {e}")
                offline_tracks = []
            target_track_row = 0
            if saved_tid and offline_tracks:
                for r_idx, t in enumerate(offline_tracks):
                    if t.get("id") == saved_tid:
                        target_track_row = r_idx
                        break
            self.switch_view("offline", select_row=target_track_row if offline_tracks else None)
            tt.focus()
        elif saved_tab == "lyrics":
            self.switch_view("lyrics")
        elif saved_tab == "playlist":
            if self.playlists:
                matched_pl_idx = None
                if saved_pid:
                    for p_idx, p in enumerate(self.playlists):
                        if p.get("id") == saved_pid:
                            matched_pl_idx = p_idx
                            break

                if matched_pl_idx is not None:
                    target_pl_idx = matched_pl_idx
                    pl = self.playlists[target_pl_idx]
                    pl_tracks = list(pl.get("tracks", []))
                    target_track_row = 0
                    if pl_tracks:
                        found_row = None
                        if saved_tid:
                            for r_idx, t in enumerate(pl_tracks):
                                if t.get("id") == saved_tid:
                                    found_row = r_idx
                                    break
                        if found_row is None and saved_title:
                            for r_idx, t in enumerate(pl_tracks):
                                if t.get("title") == saved_title and (not saved_artist or t.get("artist") == saved_artist):
                                    found_row = r_idx
                                    break
                        if found_row is None and isinstance(saved_t_idx, int) and 0 <= saved_t_idx < len(pl_tracks):
                            found_row = saved_t_idx
                        if found_row is not None:
                            target_track_row = found_row

                    st.move_cursor(row=target_pl_idx)
                    self.load_playlist_by_index(target_pl_idx, focus_tracks=True, select_row=target_track_row)
                    tt.focus()
                else:
                    self.load_playlist_by_index(0, focus_tracks=False)
                    st.move_cursor(row=0)
            else:
                self.switch_view("playlist")
        elif saved_tab == "search":
            self.switch_view("search")
            tt.focus()
        elif self.playlists:
            matched_pl_idx = None
            if saved_pid:
                for p_idx, p in enumerate(self.playlists):
                    if p.get("id") == saved_pid:
                        matched_pl_idx = p_idx
                        break

            if matched_pl_idx is not None:
                target_pl_idx = matched_pl_idx
                pl = self.playlists[target_pl_idx]
                pl_tracks = list(pl.get("tracks", []))
                target_track_row = 0
                if pl_tracks:
                    found_row = None
                    if saved_tid:
                        for r_idx, t in enumerate(pl_tracks):
                            if t.get("id") == saved_tid:
                                found_row = r_idx
                                break
                    if found_row is None and saved_title:
                        for r_idx, t in enumerate(pl_tracks):
                            if t.get("title") == saved_title and (not saved_artist or t.get("artist") == saved_artist):
                                found_row = r_idx
                                break
                    if found_row is None and isinstance(saved_t_idx, int) and 0 <= saved_t_idx < len(pl_tracks):
                        found_row = saved_t_idx
                    if found_row is not None:
                        target_track_row = found_row

                st.move_cursor(row=target_pl_idx)
                self.load_playlist_by_index(target_pl_idx, focus_tracks=True, select_row=target_track_row)
                tt.focus()
            else:
                self.switch_view("search")
                tt.focus()
        else:
            self.switch_view("search")
            tt.focus()
            try:
                self.query_one("#sidebar-hint", Static).update("[dim]Enter name or link above to create[/dim]")
            except Exception:
                pass

    def notify(self, *args, **kwargs) -> None:
        if not getattr(self, "notifications_enabled", True):
            return
        return super().notify(*args, **kwargs)

    @staticmethod
    def _is_track_offline(track: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(track, dict):
            return False
        if track.get("is_offline") or track.get("filepath"):
            return True
        t_id = stable_track_id(track)
        if t_id and get_cached_track_path(t_id):
            return True
        return False

    def _render_status_line(self) -> None:
        """Use one quiet status surface; resume background progress after notices."""
        pending = getattr(self, "_pending_track", None)
        check_offline = getattr(self, "_is_track_offline", SpoffTUI._is_track_offline)
        if pending is not None and not check_offline(pending):
            title = pending.get("title") or "song"
            text = f"Loading '{title}'…"
        elif getattr(self, "_search_loading_request_id", None) is not None:
            text = getattr(self, "_search_status_text", "Searching…")
        elif time.monotonic() < getattr(self, "_status_notice_until", 0.0):
            text = getattr(self, "_status_message", "")
        else:
            downloads = getattr(self, "_download_statuses", {})
            text = next(reversed(downloads.values()))[0] if downloads else getattr(self, "_status_message", "")
        try:
            self.query_one("#notification-line", Static).update(escape(text))
        except (NoMatches, AttributeError, ScreenStackError):
            pass

    def notify_user(self, text: str, force: bool = False):
        if text and not force and not getattr(self, "notifications_enabled", True):
            return
        def _update():
            self._status_message = text
            self._status_notice_until = time.monotonic() + 3 if text else 0.0
            SpoffTUI._render_status_line(self)
            if text and hasattr(self, "set_timer") and getattr(self, "_is_mounted", False):
                def _clear_notice():
                    if time.monotonic() >= getattr(self, "_status_notice_until", 0.0):
                        SpoffTUI._render_status_line(self)
                try:
                    self.set_timer(3.0, _clear_notice)
                except Exception:
                    pass
        if threading.get_ident() == getattr(self, "_thread_id", None):
            _update()
        else:
            try:
                self.call_from_thread(_update)
            except Exception:
                pass

    def _update_loading_status(self) -> None:
        if getattr(self, "_is_mounted", False):
            SpoffTUI._render_status_line(self)

    def set_download_status(self, text: str, clear_after: Optional[float] = None, *, channel: str = "download") -> None:
        """Show download progress in the existing grey line, without badges/toasts."""
        def _update():
            statuses = getattr(self, "_download_statuses", None)
            if statuses is None:
                statuses = self._download_statuses = {}
            stamp = object()
            statuses.pop(channel, None)
            statuses[channel] = (text, stamp)
            self._status_message = ""
            self._status_notice_until = 0.0
            SpoffTUI._render_status_line(self)
            if clear_after:
                def _clear():
                    current = statuses.get(channel)
                    if current is not None and current[1] is stamp:
                        statuses.pop(channel)
                        SpoffTUI._render_status_line(self)
                self.set_timer(clear_after, _clear)

        if threading.get_ident() == getattr(self, "_thread_id", None):
            _update()
        else:
            try:
                self.call_from_thread(_update)
            except Exception:
                pass

    def on_click(self, event) -> None:
        if getattr(event, "widget", None):
            if event.widget.id == "spotify-pill":
                self.action_open_spotify_auth()
                return
            elif event.widget.id == "settings-pill":
                self.action_open_settings()
                return
            elif event.widget.id == "update-pill":
                self.action_check_update()
                return
            elif event.widget.id == "shuf-pill":
                self.action_toggle_shuffle()
                return
            elif event.widget.id == "rep-pill":
                self.action_toggle_repeat()
                return
            elif event.widget.id == "nav-bar":
                tabs = [
                    ("search", "nav_search", "Search"),
                    ("playlist", "nav_playlist", "Playlists"),
                    ("offline", "nav_offline", "Offline"),
                    ("lyrics", "nav_lyrics", "Lyrics"),
                    ("liked", "nav_liked", "Liked Songs"),
                ]
                cur_x = 0
                for mode, act_id, label in tabs:
                    if self.advanced_mode:
                        lbl = label
                    else:
                        k = format_key_display(self.keybindings.get(act_id, ""))
                        lbl = f"[{k}] {label}"
                    tab_w = len(lbl) + 4
                    if cur_x <= event.x < cur_x + tab_w:
                        self.switch_view(mode)
                        return
                    cur_x += tab_w
                return
        if self.focused is None or not getattr(self.focused, "can_focus", False):
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()


    def on_resize(self, event: events.Resize) -> None:
        self._last_rendered_width = event.size.width

    def on_key(self, event: events.Key) -> None:
        try:
            if isinstance(self.screen, ModalScreen):
                return
        except Exception:
            pass

        now = time.monotonic()
        if not getattr(self, "_is_ready", False) or (now - getattr(self, "_mount_time", now) < 0.45):
            event.prevent_default()
            event.stop()
            return

        if self.focused is None:
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()

        # 1. Hardware media keys and global Fn/F playback & volume keys
        k = str(getattr(event, "key", "")).lower()
        name = str(getattr(event, "name", "")).lower()

        # Check if an F-key was explicitly bound to a different action by the user
        f_custom_action = None
        if k in ("f1", "f2", "f3", "f7", "f8", "f9"):
            for act, b in self.keybindings.items():
                if b and key_matches(k, None, b):
                    f_custom_action = act
                    break

        if self.keybindings.get("prev_track") != "" and (k in ("audio_prev", "mediaprevioustrack") or name in ("audio_prev", "mediaprevioustrack") or (k == "f7" and f_custom_action in (None, "prev_track"))):
            self.action_prev_track()
            event.prevent_default()
            event.stop()
            return
        elif self.keybindings.get("toggle_play") != "" and (k in ("audio_play", "audio_pause", "mediaplaypause") or name in ("audio_play", "audio_pause", "mediaplaypause") or (k == "f8" and f_custom_action in (None, "toggle_play"))):
            self.action_toggle_play()
            event.prevent_default()
            event.stop()
            return
        elif self.keybindings.get("next_track") != "" and (k in ("audio_next", "medianexttrack") or name in ("audio_next", "medianexttrack") or (k == "f9" and f_custom_action in (None, "next_track"))):
            self.action_next_track()
            event.prevent_default()
            event.stop()
            return
        elif self.keybindings.get("vol_mute") != "" and (k in ("audio_mute",) or name in ("audio_mute",) or (k == "f1" and f_custom_action in (None, "vol_mute"))):
            self.action_vol_mute()
            event.prevent_default()
            event.stop()
            return
        elif self.keybindings.get("vol_down") != "" and (k in ("audio_lower_volume",) or name in ("audio_lower_volume",) or (k == "f2" and f_custom_action in (None, "vol_down"))):
            self.action_vol_down()
            event.prevent_default()
            event.stop()
            return
        elif self.keybindings.get("vol_up") != "" and (k in ("audio_raise_volume",) or name in ("audio_raise_volume",) or (k == "f3" and f_custom_action in (None, "vol_up"))):
            self.action_vol_up()
            event.prevent_default()
            event.stop()
            return

        # Global modifier/compound shortcuts (e.g. Ctrl+1, Ctrl+2, Ctrl+E) accessible even when typing in an Input
        ek_lower = (event.key or "").lower()
        if "+" in ek_lower or ek_lower.startswith("ctrl+") or ek_lower.startswith("alt+"):
            is_input = isinstance(self.focused, Input)
            readline_input_keys = {"ctrl+a", "ctrl+u", "ctrl+k", "ctrl+w", "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z"}

            if not (is_input and ek_lower in readline_input_keys):
                for act_id, bound in self.keybindings.items():
                    if bound and ("+" in bound or bound.startswith("ctrl+") or bound.startswith("alt+")):
                        if key_matches(event.key, getattr(event, "character", None), bound):
                            act_method = getattr(self, f"action_{act_id}", None)
                            if callable(act_method):
                                act_method()
                                event.prevent_default()
                                event.stop()
                                return

        # 2. Input widget handling: type text, leave on down/tab, unfocus on escape, submit on enter/return
        if isinstance(self.focused, Input):
            if event.key in ("enter", "return", "ctrl+m"):
                self.focused.post_message(Input.Submitted(self.focused, self.focused.value))
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("down", "tab"):
                if self.focused.id == "search-box":
                    self.query_one("#track-table", DataTable).focus()
                    event.prevent_default()
                    event.stop()
                    return
                elif self.focused.id == "sidebar-import-input":
                    self.query_one("#side-table", DataTable).focus()
                    event.prevent_default()
                    event.stop()
                    return
            elif event.key in ("shift+tab", "backtab"):
                if self.focused.id == "search-box":
                    self.query_one("#side-table", DataTable).focus()
                    event.prevent_default()
                    event.stop()
                    return
                elif self.focused.id == "sidebar-import-input":
                    self.query_one("#track-table", DataTable).focus()
                    event.prevent_default()
                    event.stop()
                    return
            elif event.key == "left" and self.focused.id == "search-box" and not self.focused.value.strip():
                self.action_focus_sidebar()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "escape":
                self.action_clear_or_unfocus()
                event.prevent_default()
                event.stop()
                return
            return

        # 3. ScrubBar mode handling
        if isinstance(self.focused, ScrubBar):
            if event.key in ("b", "escape"):
                self.action_clear_or_unfocus()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "q":
                self.action_quit_app()
                event.prevent_default()
                event.stop()
                return
            return

        # Vim G (jump to bottom of table)
        if (event.key in ("G", "shift+g") or event.character == "G") and not isinstance(self.focused, (Input, ScrubBar)):
            if isinstance(self.focused, DataTable) and self.focused.row_count > 0:
                self.focused.move_cursor(row=self.focused.row_count - 1)
                event.prevent_default()
                event.stop()
                return

        # Vim gg / Home (jump to top of table)
        if event.key == "home" and isinstance(self.focused, DataTable) and self.focused.row_count > 0:
            self.focused.move_cursor(row=0)
            event.prevent_default()
            event.stop()
            return
        if (event.key == "g" or event.character == "g") and isinstance(self.focused, DataTable) and not isinstance(self.focused, Input):
            now = time.time()
            if getattr(self, "_last_g_time", 0) and (now - self._last_g_time) < 0.5:
                self._last_g_time = 0
                if self.focused.row_count > 0:
                    self.focused.move_cursor(row=0)
                    event.prevent_default()
                    event.stop()
                    return
            else:
                self._last_g_time = now

        # Vim Ctrl+D / Ctrl+U (page scrolling)
        if key_matches(event.key, getattr(event, "character", None), "ctrl+d") and isinstance(self.focused, DataTable):
            self.focused.action_page_down()
            event.prevent_default()
            event.stop()
            return
        elif key_matches(event.key, getattr(event, "character", None), "ctrl+u") and isinstance(self.focused, DataTable):
            self.focused.action_page_up()
            event.prevent_default()
            event.stop()
            return

        # 4. Jump up into Input from row 0 of DataTable on Up / cursor_up key
        is_up_key = (
            event.key in ("up",)
            or key_matches(event.key, getattr(event, "character", None), self.keybindings.get("cursor_up", "k"))
        )
        if is_up_key and self.focused and self.focused.id == "track-table":
            table = self.query_one("#track-table", DataTable)
            if (table.row_count == 0 or table.cursor_row == 0) and self.active_tab == "search":
                self.query_one("#search-box", Input).focus()
                event.prevent_default()
                event.stop()
                return
        elif is_up_key and self.focused and self.focused.id == "side-table":
            table = self.query_one("#side-table", DataTable)
            if table.row_count == 0 or table.cursor_row == 0:
                self.query_one("#sidebar-import-input", Input).focus()
                event.prevent_default()
                event.stop()
                return

        # 5. Enter / Return key handling (play track, open playlist, or seek lyrics)
        if event.key in ("enter", "return", "ctrl+m"):
            if isinstance(self.focused, DataTable):
                if self.focused.id == "track-table":
                    row_idx = self.focused.cursor_row if self.focused.cursor_row is not None else 0
                    if 0 <= row_idx < self.focused.row_count:
                        self.play_current_table_row(row_idx)
                    event.prevent_default()
                    event.stop()
                    return
                elif self.focused.id == "side-table":
                    idx = self.focused.cursor_row if self.focused.cursor_row is not None else 0
                    if 0 <= idx < len(self.playlists):
                        self.load_playlist_by_index(idx, focus_tracks=True)
                    event.prevent_default()
                    event.stop()
                    return
                elif self.focused.id == "lyrics-table":
                    idx = self.focused.cursor_row
                    if self.current_lyrics and self.current_lyrics.get("lines"):
                        lines = self.current_lyrics["lines"]
                        if idx is not None and 0 <= idx < len(lines):
                            t = lines[idx].get("time")
                            if t is not None:
                                self.player.seek_absolute(t)
                                self.notify_user(f"Seeked to {format_time(t)}")
                                old_idx = self._active_lyric_idx
                                self._active_lyric_idx = idx
                                self._highlight_lyric_line(old_idx, idx, lines)
                    event.prevent_default()
                    event.stop()
                    return
            elif not isinstance(self.focused, Button):
                try:
                    tt = self.query_one("#track-table", DataTable)
                    row_idx = tt.cursor_row if tt.cursor_row is not None else 0
                    if 0 <= row_idx < tt.row_count:
                        self.play_current_table_row(row_idx)
                        event.prevent_default()
                        event.stop()
                        return
                except Exception:
                    pass

        # Right in the sidebar opens the playlist, as the sidebar hint says.
        # Without this, the default seek_fwd binding on "right" wins and seeks.
        if event.key == "right" and self.focused and self.focused.id == "side-table":
            self.action_focus_tracks()
            event.prevent_default()
            event.stop()
            return

        # 6. Dynamic match against self.keybindings
        matched_action = None
        for act_id, bound_key in self.keybindings.items():
            if bound_key and key_matches(event.key, getattr(event, "character", None), bound_key):
                matched_action = act_id
                break

        # Fallback secondary aliases (hardware/arrow keys only, never letter keys)
        if not matched_action:
            if event.key in ("down",) and self.keybindings.get("cursor_down") != "":
                matched_action = "cursor_down"
            elif event.key in ("up",) and self.keybindings.get("cursor_up") != "":
                matched_action = "cursor_up"
            elif (event.key in ("left",)) and not isinstance(self.focused, Input) and self.keybindings.get("focus_sidebar") != "":
                matched_action = "focus_sidebar"
            elif (event.key in ("right",)) and not isinstance(self.focused, Input) and self.keybindings.get("focus_tracks") != "":
                matched_action = "focus_tracks"
            elif (event.key in ("shift+down",)) and not isinstance(self.focused, Input) and self.keybindings.get("move_item_down") != "":
                matched_action = "move_item_down"
            elif (event.key in ("shift+up",)) and not isinstance(self.focused, Input) and self.keybindings.get("move_item_up") != "":
                matched_action = "move_item_up"
            elif event.key in ("ctrl+comma",) and not isinstance(self.focused, Input) and self.keybindings.get("open_settings") != "":
                matched_action = "open_settings"
            elif (
                (event.key in ("shift+r", "R") or getattr(event, "character", None) == "R")
                and not isinstance(self.focused, (Input, ScrubBar))
                and self.keybindings.get("rename_playlist") != ""
            ):
                matched_action = "rename_playlist"
            elif (
                (event.key in ("shift+y", "Y") or getattr(event, "character", None) == "Y" or event.key == "alt+c")
                and not isinstance(self.focused, (Input, ScrubBar))
                and self.keybindings.get("clone_playlist") != ""
            ):
                matched_action = "clone_playlist"
            elif (
                event.key in ("f2",)
                and not isinstance(self.focused, (Input, ScrubBar))
                and (
                    (self.focused and getattr(self.focused, "id", None) == "side-table")
                    or self.active_tab == "playlist"
                )
                and self.keybindings.get("rename_playlist") != ""
            ):
                matched_action = "rename_playlist"
            elif (
                event.key in ("delete",)
                and not isinstance(self.focused, (Input, ScrubBar))
                and not any(k.lower() == "delete" for k in self.keybindings.values() if k)
                and self.keybindings.get("delete_item") != ""
            ):
                matched_action = "delete_item"
            elif (
                event.key in ("shift+delete",)
                and not isinstance(self.focused, (Input, ScrubBar))
                and not any(k.lower() == "shift+delete" for k in self.keybindings.values() if k)
                and self.keybindings.get("delete_playlist") != ""
            ):
                matched_action = "delete_playlist"
            elif event.key == "+" and self.keybindings.get("add_to_playlist") != "":
                matched_action = "add_to_playlist"
            elif (self.keybindings.get("bulk_download_playlist") != "" and (event.key in ("shift+b", "B") or getattr(event, "character", None) == "B") and not isinstance(self.focused, (Input, ScrubBar))):
                matched_action = "bulk_download_playlist"
            elif event.key in ("f1",) and self.keybindings.get("vol_mute") != "":
                matched_action = "vol_mute"
            elif event.key in ("f2",) and self.keybindings.get("vol_down") != "":
                matched_action = "vol_down"
            elif event.key in ("f3",) and self.keybindings.get("vol_up") != "":
                matched_action = "vol_up"

        if matched_action:
            act_method = getattr(self, f"action_{matched_action}", None)
            if callable(act_method):
                act_method()
                event.prevent_default()
                event.stop()
                return

    def action_nav_search(self): self.switch_view("search")
    def action_nav_playlist(self): self.switch_view("playlist", focus_sidebar=True)
    def action_nav_liked(self): self.switch_view("liked")
    def action_nav_offline(self): self.switch_view("offline")
    def action_nav_lyrics(self): self.action_toggle_lyrics()

    def action_toggle_lyrics(self):
        if isinstance(self.focused, Input):
            return
        if self.active_tab == "lyrics":
            prev = getattr(self, "last_browsing_tab", "playlist")
            if prev == "lyrics":
                prev = "playlist"
            self.switch_view(prev)
        else:
            self.last_browsing_tab = self.active_tab
            self.switch_view("lyrics")

    def action_toggle_shuffle(self):
        if isinstance(self.focused, Input):
            return
        self.shuffle_mode = not self.shuffle_mode
        self._shuffle_history = []
        status = "ON" if self.shuffle_mode else "OFF"
        self.notify_user(f"Shuffle: {status}")
        if self.mpris:
            self.mpris.update_shuffle(self.shuffle_mode)
        self.update_player_hud()

    def action_toggle_repeat(self):
        if isinstance(self.focused, Input):
            return
        modes = ["off", "all", "one"]
        curr_idx = modes.index(self.repeat_mode) if self.repeat_mode in modes else 0
        self.repeat_mode = modes[(curr_idx + 1) % len(modes)]
        labels = {"off": "OFF", "all": "ALL", "one": "SINGLE TRACK"}
        self.notify_user(f"Repeat: {labels[self.repeat_mode]}")
        if self.mpris:
            self.mpris.update_loop_status(self.repeat_mode)
        self.update_player_hud()

    def switch_view(self, view: str, focus_sidebar: Optional[bool] = None, select_row: Optional[int] = None):
        self.active_tab = view
        search_row = self.query_one("#search-header-row", Horizontal)
        track_table = self.query_one("#track-table", DataTable)
        lyrics_pane = self.query_one("#lyrics-pane", Vertical)
        lyrics_table = self.query_one("#lyrics-table", DataTable)

        search_row.display = (view == "search")
        track_table.display = (view != "lyrics")
        lyrics_pane.display = (view == "lyrics")

        self._update_nav_bar()

        if view == "search":
            self.render_tracks(self.search_results, reset_cursor=True)
            if getattr(self, "instant_search", True) and not self.search_results:
                try:
                    self.query_one("#search-box", Input).focus()
                except Exception:
                    track_table.focus()
            else:
                if not (self.focused and self.focused.id == "side-table"):
                    track_table.focus()
            if not self.search_results:
                if getattr(self, "instant_search", True):
                    self.notify_user("" if self.advanced_mode else "Search: Type query and press Enter")
                else:
                    self.notify_user("" if self.advanced_mode else "Search: Press / or Up arrow to type query")
            else:
                self.notify_user("")
        elif view == "playlist":
            self.playlists = load_saved_playlists()
            selected = next((p for p in self.playlists
                             if p.get("id") == self.current_playlist_id), None)
            if selected is None and self.playlists:
                selected = self.playlists[0]
            self.current_playlist_id = selected.get("id") if selected else None
            self.current_playlist_tracks = list(selected.get("tracks") or []) if selected else []
            self.refresh_side_table()

            self.render_tracks(self.current_playlist_tracks, select_row=select_row, reset_cursor=(select_row is None))
            st = self.query_one("#side-table", DataTable)
            should_focus_sidebar = focus_sidebar if focus_sidebar is not None else True
            if should_focus_sidebar:
                if self.playlists:
                    st.focus()
                    if self.current_playlist_id:
                        for p_idx, p in enumerate(self.playlists):
                            if p.get("id") == self.current_playlist_id:
                                try:
                                    st.move_cursor(row=p_idx)
                                except Exception:
                                    pass
                                break
                else:
                    self.query_one("#sidebar-import-input", Input).focus()
            else:
                track_table.focus()

            if not self.playlists:
                self.notify_user("No playlists yet — enter name in sidebar to create")
            elif not self.current_playlist_tracks:
                self.notify_user("Playlist is empty" if self.advanced_mode else "Playlist is empty — add songs from search with 'a'")
            else:
                self.notify_user("")
        elif view == "liked":
            self.current_liked_tracks = load_liked_songs()
            self.render_tracks(self.current_liked_tracks, select_row=select_row, reset_cursor=(select_row is None))
            if not (self.focused and self.focused.id == "side-table"):
                track_table.focus()
            self._sync_liked_from_spotify_bg()
            if not self.current_liked_tracks:
                self.notify_user("Liked Songs is empty — like songs with 'a' or sync from Spotify")
            else:
                self.notify_user("")
        elif view == "offline":
            offline_tracks = list(load_offline_index().values())
            self.render_tracks(offline_tracks, select_row=select_row, reset_cursor=(select_row is None))
            if not (self.focused and self.focused.id == "side-table"):
                track_table.focus()
            if not offline_tracks:
                self.notify_user("Offline library is empty — cached tracks appear here")
            else:
                self.notify_user("")
        elif view == "lyrics":
            self.render_lyrics()
            if not (self.focused and self.focused.id == "side-table"):
                lyrics_table.focus()
            self.notify_user("" if self.advanced_mode else "Lyrics: Enter or Click any line to jump to that moment")

        if view in ("search", "playlist", "liked", "offline", "lyrics"):
            try:
                save_last_tab(view, getattr(self, "current_playlist_id", None) if view == "playlist" else None)
            except Exception:
                pass

    def _update_nav_bar(self) -> None:
        tabs = [
            ("search", "nav_search", "Search"),
            ("playlist", "nav_playlist", "Playlists"),
            ("offline", "nav_offline", "Offline"),
            ("lyrics", "nav_lyrics", "Lyrics"),
            ("liked", "nav_liked", "Liked Songs"),
        ]
        parts = []
        for mode, act_id, label in tabs:
            if self.advanced_mode:
                display_label = label
            else:
                k = format_key_display(self.keybindings.get(act_id, ""))
                display_label = f"\\[{k}] {label}"
            if mode == self.active_tab:
                parts.append(f"[bold #ffffff]{display_label}[/]")
            else:
                parts.append(f"[#555555]{display_label}[/]")
        try:
            self.query_one("#nav-bar", Static).update("    ".join(parts))
        except Exception:
            pass

    def render_lyrics(self):
        lh = self.query_one("#lyrics-header", Static)
        lt = self.query_one("#lyrics-table", DataTable)

        curr = self.player.current_track
        if not curr:
            lh.update("[dim]NO TRACK PLAYING[/dim]")
            lt.clear()
            lt.add_row("", "[dim]Play a song to view lyrics[/dim]")
            return

        title = curr.get("title", "Unknown")
        artist = curr.get("artist", "Unknown")

        if self.current_lyrics is None:
            lh.update(f"[bold #ffffff]{escape(title)}[/]  [#767676]—[/]  [#cccccc]{escape(artist)}[/]  [dim #767676]FETCHING LYRICS...[/dim]")
            lt.clear()
            lt.add_row("", "[dim]Searching synchronized lyrics on LRCLIB...[/dim]")
            return

        lyr = self.current_lyrics
        if lyr.get("synced"):
            tag = "[bold #569f68]SYNCED[/]"
        elif lyr.get("instrumental"):
            tag = "[dim #d08770]INSTRUMENTAL[/dim]"
        else:
            tag = "[dim]PLAIN[/dim]"

        lh.update(f"[bold #ffffff]{escape(title)}[/]  [#767676]—[/]  [#cccccc]{escape(artist)}[/]  {tag}")

        lt.clear()
        if lyr.get("instrumental"):
            lt.add_row("", "[dim]— Instrumental Track (No Lyrics) —[/dim]")
            return

        lines = lyr.get("lines", [])
        if not lines:
            lt.add_row("", "[dim]No lyrics found for this track[/dim]")
            return

        pos, _ = self.player.get_progress()
        active_idx = get_active_lyric_index(lines, pos) if lyr.get("synced") else -1
        self._active_lyric_idx = active_idx

        for idx, line in enumerate(lines):
            t_sec = line.get("time")
            time_label = format_time(t_sec) if t_sec is not None else ""
            text = escape(line.get("text") or "♪")

            if idx == active_idx:
                lt.add_row(f"[#cccccc]{time_label}[/]", f"[bold #ffffff]{text}[/]", key=str(idx))
            elif active_idx != -1 and idx < active_idx:
                lt.add_row(f"[dim #555555]{time_label}[/]", f"[#555555]{text}[/]", key=str(idx))
            else:
                lt.add_row(f"[dim #555555]{time_label}[/]", f"[#888888]{text}[/]", key=str(idx))

        if 0 <= active_idx < len(lines):
            try:
                lt.move_cursor(row=active_idx)
            except Exception:
                pass

    def _highlight_lyric_line(self, old_idx: int, new_idx: int, lines: List[Dict[str, Any]]):
        try:
            lt = self.query_one("#lyrics-table", DataTable)
        except Exception:
            return
        if 0 <= old_idx < lt.row_count and old_idx < len(lines):
            old_line = lines[old_idx]
            t_str = format_time(old_line.get("time", 0))
            try:
                lt.update_cell_at(Coordinate(old_idx, 0), f"[dim #555555]{t_str}[/]")
                lt.update_cell_at(Coordinate(old_idx, 1), f"[#555555]{escape(old_line.get('text') or '♪')}[/]")
            except Exception:
                pass
        if 0 <= new_idx < lt.row_count and new_idx < len(lines):
            new_line = lines[new_idx]
            t_str = format_time(new_line.get("time", 0))
            try:
                lt.update_cell_at(Coordinate(new_idx, 0), f"[#cccccc]{t_str}[/]")
                lt.update_cell_at(Coordinate(new_idx, 1), f"[bold #ffffff]{escape(new_line.get('text') or '♪')}[/]")
                lt.move_cursor(row=new_idx)
            except Exception:
                pass

    def render_tracks(self, tracks: List[Dict[str, Any]], select_row: Optional[int] = None, reset_cursor: bool = False):
        table = self.query_one("#track-table", DataTable)
        old_cursor = table.cursor_row
        table.clear()
        spot_name = "Spotify"
        ytm_name = "YTMusic"
        loc_name = "Local disk"
        curr_track = getattr(getattr(self, "player", None), "current_track", None)
        curr_id = curr_track.get("id") if curr_track else None
        playing_idx: Optional[int] = None

        for idx, t in enumerate(tracks):
            t_id = stable_track_id(t)
            is_cached = get_cached_track_path(t_id) is not None if t_id else False
            raw_src = str(t.get("source", "")).lower()
            if raw_src == "local" or str(t.get("filepath", "")).startswith("/"):
                src = "local"
            elif raw_src == "spotify" or (not raw_src and t_id and len(t_id) == 22 and not t.get("url")):
                src = "spotify"
            elif raw_src in ("ytmusic", "youtube") or (not raw_src and (len(t_id) == 11 or "youtube.com" in str(t.get("url", "")) or "youtu.be" in str(t.get("url", "")))) or self.active_tab == "search":
                src = "ytmusic"
            else:
                src = raw_src or "remote"

            sep = "[dim #555555] | [/]"
            if src == "local":
                type_tag = f"[#569f68]{loc_name}[/]"
            elif src == "spotify":
                status = "[#569f68]offline[/]" if is_cached else "[#c4a768]stream[/]"
                type_tag = f"[#569f68]{spot_name}[/]{sep}{status}"
            elif src == "ytmusic":
                status = "[#569f68]offline[/]" if is_cached else "[#c4a768]stream[/]"
                type_tag = f"[#e06c75]{ytm_name}[/]{sep}{status}"
            else:
                type_tag = "[#569f68]offline[/]" if is_cached else "[dim]remote[/dim]"

            try:
                dur_ms = float(t.get("duration_ms") or 0)
            except (ValueError, TypeError):
                dur_ms = 0.0
            dur = format_time(dur_ms / 1000.0)
            safe_title = escape(str(t.get("title") or ""))
            safe_artist = escape(str(t.get("artist") or ""))

            is_playing = False
            if curr_track:
                if t_id and curr_id and t_id == curr_id:
                    is_playing = True
                elif t.get("title") == curr_track.get("title") and t.get("artist") == curr_track.get("artist"):
                    is_playing = True

            if is_playing:
                playing_idx = idx

            title_col = safe_title

            table.add_row(type_tag, title_col, safe_artist, dur, key=str(idx))
        if tracks:
            if select_row is not None:
                target = max(0, min(select_row, len(tracks) - 1))
            elif reset_cursor:
                target = playing_idx if playing_idx is not None else 0
            elif old_cursor is not None and old_cursor >= 0:
                target = min(old_cursor, len(tracks) - 1)
            elif playing_idx is not None:
                target = playing_idx
            else:
                target = 0
            try:
                table.move_cursor(row=target)
            except Exception:
                pass

    def action_focus_search(self):
        self.switch_view("search")
        self.query_one("#search-box", Input).focus()

    def action_find_in_view(self):
        if isinstance(self.focused, Input):
            return
        tracks = self._get_current_view_tracks()
        if not tracks:
            self.notify_user("Current view has no tracks to find.", force=True)
            return
        view_label = "Playlist" if self.active_tab == "playlist" else ("Liked Songs" if self.active_tab == "liked" else "Tracks")

        def handle_find_result(target_idx: Optional[int]):
            if target_idx is not None and 0 <= target_idx < len(tracks):
                self.render_tracks(tracks, select_row=target_idx)
                try:
                    tt = self.query_one("#track-table", DataTable)
                    tt.focus()
                    tt.move_cursor(row=target_idx)
                except Exception:
                    pass
                t = tracks[target_idx]
                t_title = t.get("title", "Track")
                self.notify_user(f"Selected #{target_idx + 1}: '{t_title}'.", force=True)

        self.push_screen(FilterTracksModal(tracks, view_label), handle_find_result)

    def action_focus_import(self):
        self.query_one("#sidebar-import-input", Input).focus()

    def action_focus_sidebar(self):
        if not isinstance(self.focused, Input):
            st = self.query_one("#side-table", DataTable)
            if self.playlists:
                st.focus()
                if st.cursor_row is None and self.current_playlist_id:
                    for p_idx, p in enumerate(self.playlists):
                        if p.get("id") == self.current_playlist_id:
                            try:
                                st.move_cursor(row=p_idx)
                            except Exception:
                                pass
                            break
            else:
                self.query_one("#sidebar-import-input", Input).focus()

    def action_focus_tracks(self):
        if not isinstance(self.focused, Input):
            if self.focused and self.focused.id == "side-table":
                st = self.query_one("#side-table", DataTable)
                idx = st.cursor_row
                if idx is not None and 0 <= idx < len(self.playlists):
                    pl = self.playlists[idx]
                    if self.active_tab != "playlist" or self.current_playlist_id != pl.get("id") or not self.current_playlist_tracks:
                        self.load_playlist_by_index(idx, focus_tracks=True)
                        return
                    else:
                        self.query_one("#track-table", DataTable).focus()
                        return
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()

    def action_clear_or_unfocus(self):
        f = self.focused
        if isinstance(f, Input):
            if f.id == "sidebar-import-input":
                self.query_one("#side-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()
        elif f and f.id == "playback-bar":
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()
        elif (f and f.id == "lyrics-table") or self.active_tab == "lyrics":
            prev = getattr(self, "last_browsing_tab", "playlist")
            if prev == "lyrics":
                prev = "playlist"
            self.switch_view(prev)
        else:
            self.query_one("#track-table", DataTable).focus()

    def action_toggle_focus(self):
        f = self.focused
        if f is None:
            self.query_one("#track-table", DataTable).focus()
            return
        if f.id == "sidebar-import-input":
            if self.playlists:
                self.query_one("#side-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()
        elif f.id == "side-table":
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()
        elif f.id == "search-box":
            self.query_one("#track-table", DataTable).focus()
        elif f.id in ("track-table", "lyrics-table"):
            self.query_one("#side-table", DataTable).focus()
        elif f.id == "playback-bar":
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()
        else:
            self.query_one("#track-table", DataTable).focus()

    def action_cursor_down(self):
        f = self.focused
        if f is None:
            if self.active_tab == "playlist" and self.playlists:
                self.action_focus_sidebar()
            else:
                self.query_one("#track-table", DataTable).focus()
            return

        if isinstance(f, DataTable):
            if f.id == "track-table" and f.row_count == 0 and self.active_tab == "playlist":
                self.action_focus_sidebar()
                return
            if f.cursor_row is None and f.row_count > 0:
                f.move_cursor(row=0)
                return
            f.action_cursor_down()
        elif isinstance(f, Input):
            if f.id == "search-box":
                self.query_one("#track-table", DataTable).focus()
            elif f.id == "sidebar-import-input":
                self.query_one("#side-table", DataTable).focus()

    def action_cursor_up(self):
        f = self.focused
        if f is None:
            if self.active_tab == "playlist" and self.playlists:
                self.action_focus_sidebar()
            else:
                self.query_one("#track-table", DataTable).focus()
            return

        if isinstance(f, DataTable):
            if f.id == "track-table":
                if f.row_count == 0 and self.active_tab == "playlist":
                    self.action_focus_sidebar()
                    return
                if (f.row_count == 0 or f.cursor_row == 0) and self.active_tab == "search":
                    self.query_one("#search-box", Input).focus()
                    return
            elif f.id == "side-table":
                if f.row_count == 0 or f.cursor_row == 0:
                    self.query_one("#sidebar-import-input", Input).focus()
                    return
            if f.cursor_row is None and f.row_count > 0:
                f.move_cursor(row=0)
                return
            f.action_cursor_up()
        elif isinstance(f, ScrubBar):
            self.query_one("#track-table", DataTable).focus()

    def action_move_item_up(self):
        if isinstance(self.focused, Input):
            return

        # 1. Reordering playlists in the sidebar
        if self.focused and getattr(self.focused, "id", None) == "side-table":
            st = self.query_one("#side-table", DataTable)
            idx = st.cursor_row
            if idx is not None and 1 <= idx < len(self.playlists):
                target_pl = self.playlists[idx]
                p_id = target_pl.get("id")
                if p_id and move_saved_playlist(p_id, -1):
                    new_idx = idx - 1
                    self.playlists = load_saved_playlists()
                    self.refresh_side_table(target_row=new_idx)
            return

        # 2. Dragging/reordering songs in a playlist
        if self.active_tab == "playlist" and self.current_playlist_tracks:
            tt = self.query_one("#track-table", DataTable)
            idx = tt.cursor_row
            if idx is not None and 1 <= idx < len(self.current_playlist_tracks):
                new_idx = idx - 1
                before = list(self.current_playlist_tracks)
                track = self.current_playlist_tracks[idx]

                if self.current_playlist_id and move_playlist_track(self.current_playlist_id, track, -1, index=idx):
                    self.playlists = load_saved_playlists()
                    for p in self.playlists:
                        if p.get("id") == self.current_playlist_id:
                            self.current_playlist_tracks = list(p.get("tracks", []))
                            break
                    after = list(self.current_playlist_tracks)

                    # Sync reordering to Spotify in background if this is a Spotify playlist
                    if self.current_playlist_id != "spotify_liked_songs":
                        target_pl = next((p for p in self.playlists if p.get("id") == self.current_playlist_id), None)
                        spotify_pl_id = extract_spotify_playlist_id(target_pl) or extract_spotify_playlist_id(self.current_playlist_id)
                        if spotify_pl_id and not is_client_side_track(track):
                            old_remote = sum(1 for t in before[:idx] if not is_client_side_track(t))
                            new_remote = sum(1 for t in after[:new_idx] if not is_client_side_track(t))
                            if old_remote != new_remote:
                                self._submit_spotify_job(
                                    reorder_spotify_playlist_track,
                                    spotify_pl_id, old_remote, new_remote
                                )

                    is_queue_mirroring = (
                        bool(self.queue)
                        and len(self.queue) == len(self.current_playlist_tracks)
                        and 0 <= idx < len(self.queue)
                        and 0 <= new_idx < len(self.queue)
                        and (self.queue[idx] == track or (track.get("id") and self.queue[idx].get("id") == track.get("id")))
                    )
                    if is_queue_mirroring:
                        if self.current_index == idx:
                            self.current_index = new_idx
                        elif self.current_index == new_idx:
                            self.current_index = idx
                        q_item = self.queue.pop(idx)
                        self.queue.insert(new_idx, q_item)

                    self.render_tracks(self.current_playlist_tracks, select_row=new_idx)
                    tt.focus()
                    return
        elif self.active_tab == "liked" and self.current_liked_tracks:
            tt = self.query_one("#track-table", DataTable)
            idx = tt.cursor_row
            if idx is not None and 1 <= idx < len(self.current_liked_tracks):
                SpoffTUI._reorder_liked_song(self, idx, -1)
                return
        elif self.active_tab in ("search", "offline"):
            self.notify_user("Reordering songs is available in Playlists and Liked Songs.")

    def _reorder_liked_song(self, idx: int, delta: int) -> None:
        if not self.current_liked_tracks or not (0 <= idx < len(self.current_liked_tracks)):
            return
        new_candidate = idx + delta
        if not (0 <= new_candidate < len(self.current_liked_tracks)):
            return
        track = self.current_liked_tracks[idx]
        is_queue_mirroring = (
            bool(self.queue) and len(self.queue) == len(self.current_liked_tracks)
            and all(liked_index([queued], visible) is not None
                    for queued, visible in zip(self.queue, self.current_liked_tracks, strict=False))
        )
        moved = move_liked_track(track, delta, index=idx)
        self.current_liked_tracks = load_liked_songs()
        new_idx = liked_index(self.current_liked_tracks, track)
        if moved and is_queue_mirroring:
            # The queue is a playback snapshot; move within that snapshot,
            # not to an index from a concurrently changed library.
            if self.current_index == idx:
                self.current_index = new_candidate
            elif self.current_index == new_candidate:
                self.current_index = idx
            q_item = self.queue.pop(idx)
            self.queue.insert(new_candidate, q_item)

        self.render_tracks(self.current_liked_tracks, select_row=new_idx)
        try:
            self.query_one("#track-table", DataTable).focus()
        except Exception:
            pass

    def action_move_item_down(self):
        if isinstance(self.focused, Input):
            return

        # 1. Reordering playlists in the sidebar
        if self.focused and getattr(self.focused, "id", None) == "side-table":
            st = self.query_one("#side-table", DataTable)
            idx = st.cursor_row
            if idx is not None and 0 <= idx < len(self.playlists) - 1:
                target_pl = self.playlists[idx]
                p_id = target_pl.get("id")
                if p_id and move_saved_playlist(p_id, 1):
                    new_idx = idx + 1
                    self.playlists = load_saved_playlists()
                    self.refresh_side_table(target_row=new_idx)
            return

        # 2. Dragging/reordering songs in a playlist
        if self.active_tab == "playlist" and self.current_playlist_tracks:
            tt = self.query_one("#track-table", DataTable)
            idx = tt.cursor_row
            if idx is not None and 0 <= idx < len(self.current_playlist_tracks) - 1:
                new_idx = idx + 1
                before = list(self.current_playlist_tracks)
                track = self.current_playlist_tracks[idx]

                if self.current_playlist_id and move_playlist_track(self.current_playlist_id, track, 1, index=idx):
                    self.playlists = load_saved_playlists()
                    for p in self.playlists:
                        if p.get("id") == self.current_playlist_id:
                            self.current_playlist_tracks = list(p.get("tracks", []))
                            break
                    after = list(self.current_playlist_tracks)

                    if self.current_playlist_id != "spotify_liked_songs":
                        target_pl = next((p for p in self.playlists if p.get("id") == self.current_playlist_id), None)
                        spotify_pl_id = extract_spotify_playlist_id(target_pl) or extract_spotify_playlist_id(self.current_playlist_id)
                        if spotify_pl_id and not is_client_side_track(track):
                            old_remote = sum(1 for t in before[:idx] if not is_client_side_track(t))
                            new_remote = sum(1 for t in after[:new_idx] if not is_client_side_track(t))
                            if old_remote != new_remote:
                                self._submit_spotify_job(
                                    reorder_spotify_playlist_track,
                                    spotify_pl_id, old_remote, new_remote
                                )

                    is_queue_mirroring = (
                        bool(self.queue)
                        and len(self.queue) == len(self.current_playlist_tracks)
                        and 0 <= idx < len(self.queue)
                        and 0 <= new_idx < len(self.queue)
                        and (self.queue[idx] == track or (track.get("id") and self.queue[idx].get("id") == track.get("id")))
                    )
                    if is_queue_mirroring:
                        if self.current_index == idx:
                            self.current_index = new_idx
                        elif self.current_index == new_idx:
                            self.current_index = idx
                        q_item = self.queue.pop(idx)
                        self.queue.insert(new_idx, q_item)

                    self.render_tracks(self.current_playlist_tracks, select_row=new_idx)
                    tt.focus()
                    return
        elif self.active_tab == "liked" and self.current_liked_tracks:
            tt = self.query_one("#track-table", DataTable)
            idx = tt.cursor_row
            if idx is not None and 0 <= idx < len(self.current_liked_tracks) - 1:
                SpoffTUI._reorder_liked_song(self, idx, 1)
                return
        elif self.active_tab in ("search", "offline"):
            self.notify_user("Reordering songs is available in Playlists and Liked Songs.")

    def action_focus_bar(self):
        if self.focused and self.focused.id == "playback-bar":
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()
        else:
            self.query_one("#playback-bar", ScrubBar).focus()

    def seek_to_percent(self, pct: float):
        pos, dur = self.player.get_progress()
        if dur > 0:
            target_sec = max(0.0, min(dur, pct * dur))
            self.player.seek_absolute(target_sec)
            self.notify_user(f"Seek to {int(pct * 100)}% ({format_time(target_sec)})")
            self.update_player_hud()

    def _get_current_view_tracks(self) -> List[Dict[str, Any]]:
        if self.active_tab == "search":
            return list(self.search_results)
        elif self.active_tab == "playlist":
            return list(self.current_playlist_tracks)
        elif self.active_tab == "liked":
            return list(self.current_liked_tracks if self.current_liked_tracks else load_liked_songs())
        elif self.active_tab == "offline":
            return list(load_offline_index().values())
        return []

    def _sync_table_cursor_to_index(self, idx: int):
        try:
            view_tracks = self._get_current_view_tracks()
            if not view_tracks:
                return
            tt = self.query_one("#track-table", DataTable)
            if tt.row_count == 0:
                return

            if 0 <= idx < len(self.queue):
                target = self.queue[idx]
            elif 0 <= idx < len(view_tracks):
                target = view_tracks[idx]
            else:
                return

            t_id = target.get("id")
            t_title = target.get("title")
            t_artist = target.get("artist")

            # Check if target matches view_tracks at idx directly
            if 0 <= idx < len(view_tracks):
                v_tr = view_tracks[idx]
                if (t_id and v_tr.get("id") == t_id) or (t_title and v_tr.get("title") == t_title and v_tr.get("artist") == t_artist):
                    if 0 <= idx < tt.row_count:
                        if getattr(tt, "cursor_row", None) != idx:
                            tt.move_cursor(row=idx)
                    return

            # Otherwise search view_tracks for target track
            for v_idx, v_tr in enumerate(view_tracks):
                if (t_id and v_tr.get("id") == t_id) or (t_title and v_tr.get("title") == t_title and v_tr.get("artist") == t_artist):
                    if 0 <= v_idx < tt.row_count:
                        if getattr(tt, "cursor_row", None) != v_idx:
                            tt.move_cursor(row=v_idx)
                    return
        except Exception:
            pass

    def _is_same_track(self, t1: Optional[Dict[str, Any]], t2: Optional[Dict[str, Any]]) -> bool:
        if not t1 or not t2:
            return False
        if t1 is t2:
            return True
        id1 = str(t1.get("id") or "").strip()
        id2 = str(t2.get("id") or "").strip()
        sp1 = str(t1.get("spotify_id") or "").strip()
        sp2 = str(t2.get("spotify_id") or "").strip()
        u1 = str(t1.get("uri") or "").strip()
        u2 = str(t2.get("uri") or "").strip()
        su1 = str(t1.get("spotify_uri") or "").strip()
        su2 = str(t2.get("spotify_uri") or "").strip()

        # Check explicit ID or cross-provider Spotify ID / URI match
        if id1 and id2 and id1 == id2:
            return True
        if sp1 and (sp1 == id2 or sp1 == sp2):
            return True
        if sp2 and (sp2 == id1 or sp2 == sp1):
            return True
        uris1 = {u for u in (u1, su1) if u.startswith("spotify:track:")}
        uris2 = {u for u in (u2, su2) if u.startswith("spotify:track:")}
        if uris1 and uris2 and (uris1 & uris2):
            return True

        # If both have IDs and neither matched above, they are distinct recordings
        if id1 and id2:
            return False

        # Fallback to Title and Artist when ID is missing from either
        t1_title = str(t1.get("title") or "").strip().casefold()
        t2_title = str(t2.get("title") or "").strip().casefold()
        t1_artist = str(t1.get("artist") or "").strip().casefold()
        t2_artist = str(t2.get("artist") or "").strip().casefold()
        return bool(t1_title and t1_title == t2_title and t1_artist == t2_artist)

    def _start_or_resume_playback(self):
        f = self.focused
        if isinstance(f, DataTable) and getattr(f, "id", None) == "track-table":
            tracks = self._get_current_view_tracks()
            row_idx = getattr(f, "cursor_row", None)
            if row_idx is not None and 0 <= row_idx < len(tracks):
                self.play_current_table_row(row_idx)
                return

        if self.queue and 0 <= self.current_index < len(self.queue):
            self.play_index(self.current_index)
            return

        try:
            tt = self.query_one("#track-table", DataTable)
            if tt.row_count > 0:
                row_idx = tt.cursor_row if tt.cursor_row is not None and 0 <= tt.cursor_row < tt.row_count else 0
                self.play_current_table_row(row_idx)
                return
        except Exception:
            pass

        if self.queue:
            self.play_index(0)
            return

        view_tracks = self._get_current_view_tracks()
        if view_tracks:
            self.queue = list(view_tracks)
            self._queue_origin = {
                "tab": self.active_tab,
                "playlist_id": self.current_playlist_id if self.active_tab == "playlist" else None,
            }
            self.play_index(0)
        else:
            self.notify_user("No tracks available to play.")

    def action_toggle_play(self):
        f = self.focused
        if isinstance(f, DataTable) and getattr(f, "id", None) == "track-table":
            row_idx = getattr(f, "cursor_row", None)
            tracks = self._get_current_view_tracks()
            if row_idx is not None and 0 <= row_idx < len(tracks):
                selected_track = tracks[row_idx]
                active_track = getattr(self, "_pending_track", None) or getattr(getattr(self, "player", None), "current_track", None)
                if not self._is_same_track(selected_track, active_track):
                    self.play_current_table_row(row_idx)
                    return

        pending_track = getattr(self, "_pending_track", None)
        check_offline = getattr(self, "_is_track_offline", SpoffTUI._is_track_offline)
        if pending_track is not None:
            if not check_offline(pending_track):
                track_title = pending_track.get("title", "track") if isinstance(pending_track, dict) else "track"
                self.notify_user(f"Loading '{track_title}'...")
            return

        if self.player.current_track is not None:
            self.player.toggle_pause()
            self.update_player_hud()
        else:
            self._start_or_resume_playback()

    def action_seek_fwd(self):
        self.player.seek(5)
        self.update_player_hud()

    def action_seek_bwd(self):
        self.player.seek(-5)
        self.update_player_hud()

    def action_vol_mute(self):
        if self.volume > 0:
            self._prev_volume = self.volume
            self.volume = 0
            self.player.set_volume(0)
            save_volume(0)
            self.notify_user("Volume: Muted")
        else:
            self.volume = getattr(self, "_prev_volume", 80) or 80
            self.player.set_volume(self.volume)
            save_volume(self.volume)
            self.notify_user(f"Volume: {self.volume}%")
        if self.mpris:
            self.mpris.update_volume(self.volume)
        self.update_player_hud()

    def action_vol_up(self):
        self.volume = min(100, self.volume + 5)
        self.player.set_volume(self.volume)
        save_volume(self.volume)
        self.notify_user(f"Volume: {self.volume}%")
        if self.mpris:
            self.mpris.update_volume(self.volume)
        self.update_player_hud()

    def action_vol_down(self):
        self.volume = max(0, self.volume - 5)
        self.player.set_volume(self.volume)
        save_volume(self.volume)
        self.notify_user(f"Volume: {self.volume}%")
        if self.mpris:
            self.mpris.update_volume(self.volume)
        self.update_player_hud()

    def action_open_equalizer(self):
        if not getattr(self, "_is_ready", False):
            return
        if isinstance(self.screen, EqualizerModal):
            return
        self.push_screen(EqualizerModal(self.eq_engine))

    def action_open_eq_settings(self):
        if not getattr(self, "_is_ready", False):
            return
        if isinstance(self.screen, EQSettingsModal):
            return
        if hasattr(self, "eq_engine") and self.eq_engine:
            self.push_screen(EQSettingsModal(self.eq_engine))

    def action_toggle_eq_bypass(self):
        if hasattr(self, "player") and self.player:
            bypassed = self.player.toggle_eq_bypass()
            save_eq_settings(self.eq_engine.to_dict())
            if bypassed:
                self.notify_user("EQ bypassed")
            else:
                self.notify_user(f"EQ on: {self.eq_engine.preset_name}")
            self.update_player_hud()

    def action_toggle_notifications(self):
        state = self.toggle_notifications()
        if state:
            self.notify_user("Notifications enabled")

    def action_show_help(self):
        if not getattr(self, "_is_ready", False):
            return
        self.push_screen(HelpModal())

    def update_spotify_pill(self):
        try:
            pill = self.query_one("#spotify-pill", Static)
            k = format_key_display(self.keybindings.get("open_spotify_auth", "L"))
            pill.update("[#555555]Spotify[/]" if self.advanced_mode else f"[#555555]{k}: Spotify[/]")
        except Exception:
            pass

    def update_settings_pill(self):
        try:
            pill = self.query_one("#settings-pill", Static)
            k = format_key_display(self.keybindings.get("open_settings", ","))
            pill.update("[#555555]Settings[/]" if self.advanced_mode else f"[#555555]{k}: Settings[/]")
        except Exception:
            pass

    def action_open_spotify_auth(self, first_run: bool = False):
        def _handle_result(res: Optional[str]):
            self.update_spotify_pill()
            if res == "sync_now":
                self.do_spotify_sync()
            elif res == "login_success":
                self.notify_user("Spotify account connected! Synchronizing library...")
                self.do_spotify_sync()
            elif res == "logged_out":
                self.notify_user("Logged out of Spotify.")

        self.push_screen(SpotifyAuthModal(first_run=first_run), _handle_result)

    def do_spotify_sync(self):
        auth_data = load_spotify_auth()
        if not auth_data:
            self.notify_user("Please log in to Spotify first.")
            return

        self.notify_user("Syncing Spotify playlists and Liked Songs...")

        def _sync_worker():
            def _progress(msg: str):
                self._on_ui(self.notify_user, msg)

            try:
                token = get_valid_token()
                if not token:
                    self._on_ui(self.notify_user, "Spotify session expired. Please log in again.")
                    return

                synced_count = sync_spotify_library(token, progress_callback=_progress)
                loaded_playlists = load_saved_playlists()
                loaded_liked = load_liked_songs()

                def _refresh_ui():
                    self.playlists = loaded_playlists
                    self.refresh_side_table()
                    self.update_spotify_pill()
                    self.current_liked_tracks = loaded_liked
                    if self.active_tab == "liked":
                        self.render_tracks(self.current_liked_tracks)
                    if synced_count > 0:
                        self.notify_user(f"Synced {synced_count} Spotify playlists/collections into your library!")
                        if self.playlists and self.active_tab == "playlist":
                            matched_idx = None
                            if self.current_playlist_id:
                                for p_i, pl in enumerate(self.playlists):
                                    if pl.get("id") == self.current_playlist_id or (pl.get("spotify_id") and pl.get("spotify_id") == self.current_playlist_id):
                                        matched_idx = p_i
                                        break
                            target_idx = matched_idx if matched_idx is not None else 0
                            self.load_playlist_by_index(target_idx, focus_tracks=False)
                    else:
                        self.notify_user("Spotify library sync completed.")

                self._on_ui(_refresh_ui)
            except Exception as e:
                logger.error(f"Error syncing Spotify library: {e}")
                self._on_ui(self.notify_user, f"Sync error: {e}")

        self._submit_spotify_job(_sync_worker)

    def _sync_liked_from_spotify_bg(self, force: bool = False):
        """
        Background task to sync the user's Spotify Liked Songs into Spoff's local storage.
        Debounced by 30 seconds to avoid spamming the Spotify API on rapid tab switching.
        """
        now = time.monotonic()
        last_sync = getattr(self, "_last_spotify_liked_sync_time", 0.0)
        if not force and (now - last_sync < 30.0):
            return
        self._last_spotify_liked_sync_time = now

        def _worker():
            try:
                token = get_valid_token()
                if not token:
                    return
                initial_liked = load_liked_songs()
                remote_liked = fetch_liked_songs(token, max_tracks=None)
                if remote_liked is None:
                    return
                remote_liked = apply_pending_unlikes(remote_liked, token)
                with storage_transaction():
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

                    filtered_remote = [
                        rt for rt in remote_liked
                        if not (
                            (str(rt.get("id") or "") in removed_during_fetch)
                            or (str(rt.get("spotify_id") or "") in removed_during_fetch)
                            or (str(rt.get("uri") or "") in removed_during_fetch)
                        )
                    ]
                    merged = merge_spotify_and_client_tracks(filtered_remote, current_liked)
                    if merged != current_liked:
                        save_liked_songs(merged)
                if merged != current_liked:
                    def _update_ui():
                        self.current_liked_tracks = load_liked_songs()
                        if self.active_tab == "liked":
                            f = self.focused
                            curr_row = f.cursor_row if (isinstance(f, DataTable) and f.id == "track-table" and f.cursor_row is not None) else None
                            self.render_tracks(self.current_liked_tracks, select_row=curr_row)
                        self.update_player_hud()
                    self._on_ui(_update_ui)
            except Exception as e:
                logger.debug(f"Background Spotify liked sync error: {e}")

        self._submit_spotify_job(_worker)

    def _mpris_play(self):
        if self.player.is_paused:
            self.player.resume()
            self.update_player_hud()
        elif self.player.current_track is None:
            self.action_toggle_play()

    def _mpris_pause(self):
        if not self.player.is_paused and self.player.current_track:
            self.player.pause()
            self.update_player_hud()

    def _mpris_stop(self):
        self._play_request_id = getattr(self, "_play_request_id", 0) + 1
        self._pending_track = None
        self.player.stop()
        self.current_index = -1
        if self.mpris:
            self.mpris.update_track(None)
        self.update_player_hud()

    def _mpris_seek(self, sec: float):
        self.player.seek(sec)
        pos = self.player.get_position()
        if self.mpris:
            self.mpris.emit_seeked(pos)
        self.update_player_hud()

    def _mpris_set_pos(self, sec: float):
        self.player.seek_absolute(sec)
        if self.mpris:
            self.mpris.emit_seeked(sec)
        self.update_player_hud()

    def _mpris_set_vol(self, vol: int):
        self.volume = max(0, min(100, vol))
        self.player.set_volume(self.volume)
        save_volume(self.volume)
        self.update_player_hud()

    def _mpris_set_loop_status(self, mode: str):
        mapping = {"None": "off", "Track": "one", "Playlist": "all"}
        self.repeat_mode = mapping.get(mode, "off")
        labels = {"off": "OFF", "all": "ALL", "one": "SINGLE TRACK"}
        self.notify_user(f"Repeat: {labels.get(self.repeat_mode, 'OFF')}")
        self.update_player_hud()

    def _mpris_set_shuffle(self, val: bool):
        self.shuffle_mode = bool(val)
        self._shuffle_history = []
        status = "ON" if self.shuffle_mode else "OFF"
        self.notify_user(f"Shuffle: {status}")
        self.update_player_hud()

    @work(thread=True)
    def check_github_updates_bg(self):
        time.sleep(2.5)
        try:
            info = check_for_updates()
            if info and info.get("has_update"):
                self.update_info = info
                # Never auto-pull a git working clone: it rebases the developer's
                # checkout (and uncommitted work) behind their back. Notify instead.
                if self.auto_update and not is_git_checkout():
                    def _auto_start():
                        try:
                            self.query_one("#update-pill", Static).update("[bold #c4a768]▲ Updating...[/]")
                        except Exception:
                            pass
                    self.call_from_thread(_auto_start)

                    ok, msg = perform_update()
                    def _auto_done():
                        if ok:
                            try:
                                self.query_one("#update-pill", Static).update("[bold #569f68]✓ Updated[/]")
                            except Exception:
                                pass
                            sha = info.get("remote_sha", "")
                            self.notify_user(f"Spoff updated to {sha}! Restart to apply.")
                        else:
                            try:
                                pill_text = "[bold #c4a768]▲ Update[/]" if self.advanced_mode else "[bold #c4a768]▲ Update (u)[/]"
                                self.query_one("#update-pill", Static).update(pill_text)
                            except Exception:
                                pass
                            self.notify_user(f"Auto-update: {msg} (Press 'u' to update)")
                    self.call_from_thread(_auto_done)
                else:
                    def _notify():
                        try:
                            pill_text = "[bold #c4a768]▲ Update[/]" if self.advanced_mode else "[bold #c4a768]▲ Update (u)[/]"
                            self.query_one("#update-pill", Static).update(pill_text)
                        except Exception:
                            pass
                        msg = info.get("message", "")
                        sha = info.get("remote_sha", "")
                        self.notify_user(f"Update available: {sha} ({msg})" if self.advanced_mode else f"Update available: {sha} ({msg}) — Press 'u' to update")
                    self.call_from_thread(_notify)
        except Exception as e:
            logger.debug(f"Background update check failed: {e}")

    def action_check_update(self):
        def _handle(confirmed: Optional[bool]):
            if confirmed:
                self.notify_user("Updated to latest version! Please restart Spoff.")
                try:
                    self.query_one("#update-pill", Static).update("[bold #569f68]✓ Up to date[/]")
                except Exception:
                    pass

        if self.update_info:
            self.push_screen(UpdateModal(self.update_info), _handle)
        else:
            self.notify_user("Checking for updates on GitHub...")
            def _check():
                info = check_for_updates()
                if info and info.get("has_update"):
                    self.update_info = info
                    def _show():
                        try:
                            self.query_one("#update-pill", Static).update("[bold #c4a768]▲ Update (u)[/]")
                        except Exception:
                            pass
                        self.push_screen(UpdateModal(info), _handle)
                    self.call_from_thread(_show)
                else:
                    self.notify_user("Spoff is up to date on the latest GitHub commit.")
            threading.Thread(target=_check, daemon=True).start()

    def backfill_playlists_art_bg(self) -> None:
        """
        Background worker that resolves missing art_url and artist_art_url
        for saved playlist tracks without blocking or freezing UI.
        """
        def _worker():
            time.sleep(1.0)
            work_items = []
            for p in load_saved_playlists():
                p_id = p.get("id")
                if not p_id:
                    continue
                for t in p.get("tracks", []):
                    if isinstance(t, dict) and t.get("id") and (not t.get("art_url") or not t.get("artist_art_url")):
                        work_items.append((p_id, t.get("id"), dict(t)))
            for t in load_liked_songs():
                if isinstance(t, dict) and t.get("id") and (not t.get("art_url") or not t.get("artist_art_url")):
                    work_items.append(("liked", t.get("id"), dict(t)))
            updated = False
            for p_id, t_id, t in work_items:
                if getattr(self, "_closing", False):
                    return
                try:
                    art = resolve_track_artwork(t, timeout=3.0)
                    if art and (art.get("art_url") or art.get("artist_art_url")):
                        if merge_track_artwork(p_id, t_id, art):
                            updated = True
                except Exception:
                    pass
                time.sleep(0.12)
            if updated and not getattr(self, "_closing", False):
                def _refresh():
                    self.playlists = load_saved_playlists()
                    self.current_liked_tracks = load_liked_songs()
                    self.refresh_side_table()
                    if self.active_tab == "liked":
                        self.render_tracks(self.current_liked_tracks)
                if hasattr(self, "call_from_thread"):
                    self.call_from_thread(_refresh)

        threading.Thread(target=_worker, daemon=True).start()

    def action_next_track(self):
        if not self.queue:
            view_tracks = self._get_current_view_tracks()
            if view_tracks:
                self.queue = list(view_tracks)
                act_tab = getattr(self, "active_tab", "search")
                curr_pl = getattr(self, "current_playlist_id", None)
                self._queue_origin = {
                    "tab": act_tab,
                    "playlist_id": curr_pl if act_tab == "playlist" else None,
                }
                self.current_index = -1

        if not self.queue:
            self.notify_user("No tracks available in queue.")
            return

        if self.shuffle_mode and len(self.queue) > 1:
            if self.current_index >= 0:
                self._shuffle_history.append(self.current_index)
                if len(self._shuffle_history) > 200:
                    self._shuffle_history = self._shuffle_history[-200:]
            candidates = [i for i in range(len(self.queue)) if i != self.current_index]
            next_idx = random.choice(candidates)
            self.play_index(next_idx)
            return

        if self.current_index + 1 < len(self.queue):
            next_idx = self.current_index + 1 if self.current_index >= 0 else 0
            self.play_index(next_idx)
        elif self.repeat_mode == "all":
            self.play_index(0)
        else:
            self._play_request_id += 1
            self.player.stop()
            self.current_index = -1
            self._pending_track = None
            if self.mpris:
                self.mpris.update_track(None)
            self.notify_user("End of queue reached.")
            self.update_player_hud()

    def action_prev_track(self):
        pos, _ = self.player.get_progress()
        if pos > 3.0:
            self.player.seek_absolute(0)
            self.notify_user("Restarted track.")
            self.update_player_hud()
            return

        if not self.queue:
            view_tracks = self._get_current_view_tracks()
            if view_tracks:
                self.queue = list(view_tracks)
                self._queue_origin = {
                    "tab": self.active_tab,
                    "playlist_id": self.current_playlist_id if self.active_tab == "playlist" else None,
                }

        if not self.queue:
            self.notify_user("No tracks available in queue.")
            return

        if self.shuffle_mode and self._shuffle_history:
            prev_idx = self._shuffle_history.pop()
            if 0 <= prev_idx < len(self.queue):
                self.play_index(prev_idx)
                return

        if self.current_index > 0:
            prev_idx = self.current_index - 1
            self.play_index(prev_idx)
        elif self.repeat_mode == "all" and len(self.queue) > 1:
            self.play_index(len(self.queue) - 1)
        elif self.current_index == 0:
            self.player.seek_absolute(0)
            self.notify_user("Restarted track.")
            self.update_player_hud()
        else:
            self.play_index(0)

    def action_quit_app(self):
        self._closing = True
        self._play_request_id += 1
        self._cleanup_on_exit()
        self.exit()

    def play_current_table_row(self, row_idx: int):
        tracks = self._get_current_view_tracks()

        if 0 <= row_idx < len(tracks):
            selected_track = tracks[row_idx]
            active_track = getattr(self, "_pending_track", None) or getattr(getattr(self, "player", None), "current_track", None)
            if self._is_same_track(selected_track, active_track):
                check_offline = getattr(self, "_is_track_offline", SpoffTUI._is_track_offline)
                if getattr(self, "_pending_track", None) is not None:
                    if not check_offline(self._pending_track):
                        track_title = self._pending_track.get("title", "track") if isinstance(self._pending_track, dict) else "track"
                        self.notify_user(f"Loading '{track_title}'...")
                    return
                if getattr(self, "player", None) and self.player.current_track is not None:
                    self.player.toggle_pause()
                    self.update_player_hud()
                    return

            act_tab = getattr(self, "active_tab", "search")
            curr_pl = getattr(self, "current_playlist_id", None)
            self.queue = list(tracks)
            self._queue_origin = {
                "tab": act_tab,
                "playlist_id": curr_pl if act_tab == "playlist" else None,
            }
            self._failed_indices.clear()
            self._shuffle_history.clear()
            self.play_index(row_idx)

    def refresh_side_table(self, target_row: Optional[int] = None):
        try:
            st = self.query_one("#side-table", DataTable)
        except Exception:
            return
        cur_cursor = getattr(st, "cursor_row", None)
        st.clear()
        selected_idx = 0
        for idx, p in enumerate(self.playlists):
            raw_name = str(p.get("name") or "Untitled")
            is_active = bool(self.current_playlist_id and p.get("id") == self.current_playlist_id)
            if is_active:
                selected_idx = idx
                styled_text = Text.from_markup(f"[bold #ffffff]{escape(raw_name)}[/]")
            else:
                styled_text = Text.from_markup(f"[#888888]{escape(raw_name)}[/]")
            st.add_row(styled_text, key=str(idx))

        if target_row is not None and 0 <= target_row < len(self.playlists):
            target_cursor = target_row
        elif isinstance(cur_cursor, int) and 0 <= cur_cursor < len(self.playlists):
            target_cursor = cur_cursor
        else:
            target_cursor = selected_idx

        if self.playlists:
            try:
                st.move_cursor(row=target_cursor)
            except Exception:
                pass
        try:
            hint = self.query_one("#sidebar-hint", Static)
            if self.advanced_mode:
                hint.styles.display = "none"
                hint.update("")
            else:
                hint.styles.display = "block"
                if self.playlists:
                    hint.update("Enter / →: open")
                else:
                    hint.update("Create your first playlist above.")
        except Exception:
            pass

    def load_playlist_by_index(self, idx: int, focus_tracks: bool = False, select_row: Optional[int] = None):
        if 0 <= idx < len(self.playlists):
            pl = self.playlists[idx]
            self.current_playlist_id = pl.get("id")
            name = pl.get("name", "Playlist")
            self.refresh_side_table(target_row=idx)

            st = self.query_one("#side-table", DataTable)
            try:
                st.move_cursor(row=idx)
            except Exception:
                pass

            if pl.get("tracks"):
                self.current_playlist_tracks = list(pl["tracks"])
                if not self.queue or self.current_index == -1:
                    self.queue = list(self.current_playlist_tracks)
                    self._queue_origin = {
                        "tab": "playlist",
                        "playlist_id": self.current_playlist_id,
                    }
                    self.current_index = select_row if (select_row is not None and 0 <= select_row < len(self.queue)) else -1
                self.notify_user(f"Loaded playlist '{name}' ({len(self.current_playlist_tracks)} tracks).")
                self.switch_view("playlist", focus_sidebar=not focus_tracks, select_row=select_row)
                if focus_tracks:
                    self.query_one("#track-table", DataTable).focus()
                else:
                    st.focus()
                return

            url = pl.get("url", "")
            if url:
                self.import_playlist_url(url)
            else:
                self.current_playlist_tracks = []
                self.render_tracks([])
                self.notify_user(f"Opened empty playlist '{name}'." if self.advanced_mode else f"Opened empty playlist '{name}'. Press 'a' on any song to add it.")
                self.switch_view("playlist", focus_sidebar=not focus_tracks, select_row=select_row)
                if focus_tracks:
                    self.query_one("#track-table", DataTable).focus()
                else:
                    st.focus()

    def action_like_track(self):
        if not getattr(self, "_is_ready", False):
            return
        if isinstance(self.focused, Input):
            return

        track = None
        f = self.focused

        # 1. Prefer highlighted row in #track-table if focused on it
        if isinstance(f, DataTable) and getattr(f, "id", None) == "track-table":
            row_idx = f.cursor_row
            tracks = self._get_current_view_tracks()
            if row_idx is not None and 0 <= row_idx < len(tracks):
                track = tracks[row_idx]

        # 2. If no track selected from focused table, prefer current playing track
        if not track and getattr(getattr(self, "player", None), "current_track", None):
            track = self.player.current_track

        # 3. Fallback to track-table cursor row even if focus is elsewhere (e.g. lyrics/deck)
        if not track:
            try:
                tt = self.query_one("#track-table", DataTable)
                row_idx = tt.cursor_row
                tracks = self._get_current_view_tracks()
                if row_idx is not None and 0 <= row_idx < len(tracks):
                    track = tracks[row_idx]
            except Exception:
                pass

        if not track:
            self.notify_user("No track selected or playing to like.", force=True)
            return

        t_title = str(track.get("title") or "Track")
        liked = is_track_liked(track)

        if liked:
            remove_liked_track(track)
            self.current_liked_tracks = load_liked_songs()
            if self.active_tab == "liked":
                f = self.focused
                new_row = None
                if isinstance(f, DataTable) and f.id == "track-table" and f.cursor_row is not None:
                    if self.current_liked_tracks:
                        new_row = max(0, min(f.cursor_row, len(self.current_liked_tracks) - 1))
                self.render_tracks(self.current_liked_tracks, select_row=new_row)
                if self.queue:
                    q_idx = None
                    target_row = f.cursor_row if (isinstance(f, DataTable) and f.cursor_row is not None) else None
                    if target_row is not None and 0 <= target_row < len(self.queue) and (self.queue[target_row] == track or (track.get("id") and self.queue[target_row].get("id") == track.get("id"))):
                        q_idx = target_row
                    else:
                        for i, q_item in enumerate(self.queue):
                            if q_item == track or (track.get("id") and q_item.get("id") == track.get("id")):
                                q_idx = i
                                break
                    if q_idx is not None:
                        was_current = (q_idx == self.current_index)
                        if was_current and getattr(self, "_pending_track", None) is not None:
                            self._play_request_id += 1
                            self._pending_track = None
                        self.queue.pop(q_idx)
                        if not self.queue:
                            self.current_index = -1
                        elif was_current:
                            self.current_index = q_idx - 1
                        elif q_idx < self.current_index:
                            self.current_index -= 1
                        if hasattr(self, "_shuffle_history") and self._shuffle_history is not None:
                            self._shuffle_history.clear()
                        self.update_player_hud()
            self.notify_user(f"Removed '{t_title}' from Liked Songs.")

            def _sync_unlike_bg():
                ok, msg = remove_track_from_spotify_account("liked", "Liked Songs", track)
                if ok:
                    self._on_ui(self.notify_user, f"'{t_title}' removed from Spotify Liked Songs.")
                elif msg and not msg.startswith("Not logged in") and not msg.startswith("Track not found"):
                    logger.info(f"Spotify unlike sync notice: {msg}")
            self._submit_spotify_job(_sync_unlike_bg)
        else:
            add_track_to_liked_songs(track)
            self.current_liked_tracks = load_liked_songs()
            if getattr(self, "active_tab", "") == "liked":
                f = self.focused
                curr_row = f.cursor_row if (isinstance(f, DataTable) and f.id == "track-table" and f.cursor_row is not None) else None
                self.render_tracks(self.current_liked_tracks, select_row=curr_row)
            self.notify_user(f"Added '{t_title}' to Liked Songs.")

            def _sync_like_bg():
                ok, msg = add_track_to_spotify_account("liked", "Liked Songs", track)
                if ok:
                    self._on_ui(self.notify_user, f"'{t_title}' synced to Spotify Liked Songs.", force=True)
                    if track.get("spotify_id") or track.get("spotify_uri"):
                        def _update_liked_storage():
                            with storage_transaction():
                                current = load_liked_songs()
                                idx = liked_index(current, track)
                                if idx is not None:
                                    if track.get("spotify_id"):
                                        current[idx]["spotify_id"] = track["spotify_id"]
                                    if track.get("spotify_uri"):
                                        current[idx]["spotify_uri"] = track["spotify_uri"]
                                    save_liked_songs(current)
                                self.current_liked_tracks = current
                        self._on_ui(_update_liked_storage)
                elif msg and not msg.startswith("Not logged in") and not msg.startswith("Could not find"):
                    logger.info(f"Spotify like sync notice: {msg}")
                    if "permission" in msg.lower() or "re-link" in msg.lower():
                        self._on_ui(self.notify_user, msg, force=True)
            self._submit_spotify_job(_sync_like_bg)

    def action_add_to_playlist(self):
        f = self.focused
        row_idx = None
        if isinstance(f, DataTable) and f.id == "track-table":
            row_idx = f.cursor_row
        elif self.query_one("#track-table", DataTable).cursor_row is not None:
            row_idx = self.query_one("#track-table", DataTable).cursor_row

        track = None
        tracks = self._get_current_view_tracks()

        if row_idx is not None and 0 <= row_idx < len(tracks):
            track = tracks[row_idx]
        elif self.player.current_track:
            track = self.player.current_track

        if not track:
            self.notify_user("Select a track first, or play a song to add it to a playlist.", force=True)
            return

        self.prompt_add_track_to_playlist(track)

    def prompt_add_track_to_playlist(self, track: Dict[str, Any]):
        def handle_modal_result(result: Optional[Tuple[str, str]]):
            if not result:
                return
            mode, val = result
            t_title = str(track.get("title") or "Track")
            if mode == "liked" or val in ("liked_songs", "target_liked_songs"):
                added = add_track_to_liked_songs(track)
                self.current_liked_tracks = load_liked_songs()
                if self.active_tab == "liked":
                    self.render_tracks(self.current_liked_tracks)
                if added:
                    self.notify_user(f"Added '{t_title}' to Liked Songs.", force=True)
                    def _sync_liked_bg():
                        ok, msg = add_track_to_spotify_account("liked", "Liked Songs", track)
                        if ok:
                            self._on_ui(self.notify_user, f"'{t_title}' synced to Spotify Liked Songs.", force=True)
                            if track.get("spotify_id") or track.get("spotify_uri"):
                                def _update_liked_storage():
                                    current = load_liked_songs()
                                    idx = liked_index(current, track)
                                    if idx is not None:
                                        if track.get("spotify_id"):
                                            current[idx]["spotify_id"] = track["spotify_id"]
                                        if track.get("spotify_uri"):
                                            current[idx]["spotify_uri"] = track["spotify_uri"]
                                        save_liked_songs(current)
                                        self.current_liked_tracks = current
                                self._on_ui(_update_liked_storage)
                        elif msg and not msg.startswith("Not logged in") and not msg.startswith("Could not find"):
                            logger.info(f"Spotify liked sync notice: {msg}")
                            if "permission" in msg.lower() or "re-link" in msg.lower():
                                self._on_ui(self.notify_user, msg, force=True)
                    self._submit_spotify_job(_sync_liked_bg)
                else:
                    self.notify_user(f"'{t_title}' is already in Liked Songs.", force=True)

            elif mode == "create":
                new_pl = create_local_playlist(val)
                add_track_to_playlist(new_pl["id"], track)
                self.playlists = load_saved_playlists()
                self.current_playlist_id = new_pl["id"]
                self.current_playlist_tracks = [track]
                self.refresh_side_table()
                if self.active_tab == "playlist":
                    self.render_tracks(self.current_playlist_tracks)
                self.notify_user(f"Created playlist '{val}' and added '{t_title}'.", force=True)

                def _sync_create_bg():
                    ok, msg = add_track_to_spotify_account(new_pl["id"], val, track)
                    if ok:
                        self._on_ui(self.notify_user, f"'{t_title}' synced to Spotify playlist '{val}'.", force=True)
                        def _refresh_after_create_sync():
                            self.playlists = load_saved_playlists()
                            self.refresh_side_table()
                            if self.current_playlist_id == new_pl["id"]:
                                for p_sync in self.playlists:
                                    if p_sync.get("id") == new_pl["id"]:
                                        self.current_playlist_tracks = list(p_sync.get("tracks", []))
                                        if self.active_tab == "playlist":
                                            self.render_tracks(self.current_playlist_tracks)
                                        break
                        self._on_ui(_refresh_after_create_sync)
                    elif msg and not msg.startswith("Not logged in"):
                        logger.info(f"Spotify sync notice: {msg}")
                        if "permission" in msg.lower() or "re-link" in msg.lower():
                            self._on_ui(self.notify_user, msg, force=True)
                self._submit_spotify_job(_sync_create_bg)

            elif mode == "select":
                target_pl = None
                target_pl_idx = 0
                pl_name = "Playlist"
                for idx, p in enumerate(self.playlists):
                    if p.get("id") == val:
                        target_pl = p
                        target_pl_idx = idx
                        pl_name = p.get("name") or "Playlist"
                        break

                dup_idx = get_track_index_in_playlist(target_pl, track) if target_pl else None

                def _do_add(allow_dup: bool = False):
                    nonlocal pl_name
                    added = add_track_to_playlist(val, track, allow_duplicate=allow_dup)
                    self.playlists = load_saved_playlists()
                    for p in self.playlists:
                        if p.get("id") == val:
                            pl_name = p.get("name", pl_name)
                            self.current_playlist_id = val
                            self.current_playlist_tracks = list(p.get("tracks", []))
                            if self.active_tab == "playlist":
                                self.render_tracks(self.current_playlist_tracks)
                            break
                    if added:
                        msg = f"Added duplicate '{t_title}' to '{pl_name}'." if allow_dup else f"Added '{t_title}' to '{pl_name}'."
                        self.notify_user(msg, force=True)
                        def _sync_select_bg():
                            ok, msg = add_track_to_spotify_account(val, pl_name, track)
                            if ok:
                                self._on_ui(self.notify_user, f"'{t_title}' synced to Spotify playlist '{pl_name}'.", force=True)
                                def _refresh_after_sync():
                                    self.playlists = load_saved_playlists()
                                    self.refresh_side_table()
                                    if self.current_playlist_id == val:
                                        for p_sync in self.playlists:
                                            if p_sync.get("id") == val:
                                                self.current_playlist_tracks = list(p_sync.get("tracks", []))
                                                if self.active_tab == "playlist":
                                                    self.render_tracks(self.current_playlist_tracks)
                                                break
                                self._on_ui(_refresh_after_sync)
                            elif msg and not msg.startswith("Not logged in"):
                                logger.info(f"Spotify sync notice: {msg}")
                                if "permission" in msg.lower() or "re-link" in msg.lower():
                                    self._on_ui(self.notify_user, msg, force=True)
                        self._submit_spotify_job(_sync_select_bg)
                    else:
                        self.notify_user(f"'{t_title}' is already in '{pl_name}'.", force=True)
                    self.refresh_side_table()

                if dup_idx is not None:
                    def handle_dup_decision(decision: Optional[str]):
                        if decision == "jump":
                            self.load_playlist_by_index(target_pl_idx, focus_tracks=True, select_row=dup_idx)
                            self.notify_user(f"Showing '{t_title}' at #{dup_idx + 1} in '{pl_name}'.", force=True)
                        elif decision == "add":
                            _do_add(allow_dup=True)

                    self.push_screen(DuplicateTrackModal(track, pl_name, dup_idx, val), handle_dup_decision)
                    return

                _do_add(allow_dup=False)

        self.push_screen(AddToPlaylistModal(track, self.playlists), handle_modal_result)

    def action_share_track(self):
        if not getattr(self, "_is_ready", False):
            return
        f = None
        try:
            f = self.focused
            if isinstance(f, Input):
                return
        except Exception:
            f = None

        # If user is focused on the playlists sidebar table, share the highlighted playlist instead
        if f and getattr(f, "id", None) == "side-table":
            self.action_share_playlist()
            return

        row_idx = None
        if isinstance(f, DataTable) and f.id == "track-table":
            row_idx = f.cursor_row
        elif self.active_tab in ("search", "playlist", "liked", "offline"):
            try:
                tt = self.query_one("#track-table", DataTable)
                if tt.cursor_row is not None:
                    row_idx = tt.cursor_row
            except Exception:
                pass

        tracks = []
        if self.active_tab == "search":
            tracks = getattr(self, "search_results", [])
        elif self.active_tab == "playlist":
            tracks = getattr(self, "current_playlist_tracks", [])
        elif self.active_tab == "liked":
            tracks = getattr(self, "current_liked_tracks", None) or load_liked_songs()
        elif self.active_tab == "offline":
            tracks = list(load_offline_index().values())

        track = None
        if row_idx is not None and 0 <= row_idx < len(tracks) and f and f.id == "track-table":
            track = tracks[row_idx]
        elif self.player.current_track:
            track = self.player.current_track
        elif row_idx is not None and 0 <= row_idx < len(tracks):
            track = tracks[row_idx]

        if not track:
            if self.active_tab == "playlist" or (f and getattr(f, "id", None) == "side-table"):
                self.action_share_playlist()
                return
            self.notify_user("No track selected or playing to share.")
            return

        title = track.get("title", "Unknown Track")
        share_url, source_label = resolve_track_url(track)

        if not share_url:
            self.notify_user(f"Could not generate share link for '{title}'.")
            return

        copied = copy_to_clipboard(share_url, self)

        if copied:
            self.notify_user(f"Copied {source_label} link for '{title}' to clipboard")
        else:
            self.notify_user(f"Share link: {share_url}")

    def action_share_playlist(self):
        if not getattr(self, "_is_ready", False):
            return
        if isinstance(self.focused, Input):
            return

        target_pl = None

        # 1. If focused on side-table, use sidebar cursor row
        if self.focused and getattr(self.focused, "id", None) == "side-table" and isinstance(self.focused, DataTable):
            row_idx = self.focused.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self.playlists):
                target_pl = self.playlists[row_idx]

        # 2. If viewing playlist tab or we have current_playlist_id
        if not target_pl and self.active_tab == "playlist" and hasattr(self, "current_playlist_id") and self.current_playlist_id:
            for p in self.playlists:
                if p.get("id") == self.current_playlist_id:
                    target_pl = p
                    break

        # 3. If in search or offline view, or no playlist found, delegate to share_track if track available
        if not target_pl:
            if self.focused and getattr(self.focused, "id", None) == "side-table":
                self.notify_user("No playlist selected to share.")
                return
            if self.active_tab in ("search", "offline"):
                self.action_share_track()
                return
            if hasattr(self, "current_playlist_id") and self.current_playlist_id:
                for p in self.playlists:
                    if p.get("id") == self.current_playlist_id:
                        target_pl = p
                        break
            if not target_pl and self.playlists:
                try:
                    st = self.query_one("#side-table", DataTable)
                    idx = st.cursor_row if st.cursor_row is not None else 0
                    if 0 <= idx < len(self.playlists):
                        target_pl = self.playlists[idx]
                except Exception:
                    pass

        if not target_pl:
            if self.player.current_track:
                self.action_share_track()
                return
            self.notify_user("No playlist selected or open to share.")
            return

        name = target_pl.get("name", "Playlist")

        share_url, source_label = resolve_playlist_url(target_pl, default_engine=self.search_engine)
        if not share_url:
            self.notify_user(f"Could not generate share link for '{name}'.")
            return

        copied = copy_to_clipboard(share_url, self)

        if copied:
            self.notify_user(f"Copied {source_label} link for '{name}' to clipboard")
        else:
            self.notify_user(f"Share link: {share_url}")

    def action_download_offline(self):
        if not getattr(self, "_is_ready", False) or isinstance(self.focused, Input):
            return

        # 1. If focused on side-table (playlists sidebar) -> bulk download highlighted playlist
        if self.focused and getattr(self.focused, "id", None) == "side-table" and isinstance(self.focused, DataTable):
            row_idx = self.focused.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self.playlists):
                self._bulk_download_playlist(self.playlists[row_idx])
                return

        # 2. If focused on track-table and has selected row -> download that song
        if self.focused and getattr(self.focused, "id", None) == "track-table" and isinstance(self.focused, DataTable):
            row_idx = self.focused.cursor_row
            tracks = self._get_current_view_tracks()
            if row_idx is not None and 0 <= row_idx < len(tracks):
                self._download_single_track(tracks[row_idx])
                return

        # 3. If in playlist tab and no track is selected or table is empty -> bulk download current playlist
        if self.active_tab == "playlist" and hasattr(self, "current_playlist_id") and self.current_playlist_id:
            for p in self.playlists:
                if p.get("id") == self.current_playlist_id:
                    self._bulk_download_playlist(p)
                    return

        # 4. Fallback: currently playing track
        if self.player.current_track:
            self._download_single_track(self.player.current_track)
            return

        # 5. Fallback: highlighted playlist in sidebar
        if self.playlists:
            try:
                st = self.query_one("#side-table", DataTable)
                idx = st.cursor_row if st.cursor_row is not None else 0
                if 0 <= idx < len(self.playlists):
                    self._bulk_download_playlist(self.playlists[idx])
                    return
            except Exception:
                pass

        self.notify_user("No song or playlist selected to download.")

    def action_bulk_download_playlist(self):
        if not getattr(self, "_is_ready", False) or isinstance(self.focused, Input):
            return

        if self.active_tab == "liked":
            liked_tracks = list(self.current_liked_tracks if self.current_liked_tracks else load_liked_songs())
            if liked_tracks:
                self._bulk_download_playlist({"name": "Liked Songs", "tracks": liked_tracks})
                return
            else:
                self.notify_user("Liked Songs is empty.")
                return

        target_pl = None
        if self.focused and getattr(self.focused, "id", None) == "side-table" and isinstance(self.focused, DataTable):
            row_idx = self.focused.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self.playlists):
                target_pl = self.playlists[row_idx]

        if not target_pl and self.active_tab == "playlist" and hasattr(self, "current_playlist_id") and self.current_playlist_id:
            for p in self.playlists:
                if p.get("id") == self.current_playlist_id:
                    target_pl = p
                    break

        if not target_pl and self.playlists:
            try:
                st = self.query_one("#side-table", DataTable)
                idx = st.cursor_row if st.cursor_row is not None else 0
                if 0 <= idx < len(self.playlists):
                    target_pl = self.playlists[idx]
            except Exception:
                pass

        if target_pl:
            self._bulk_download_playlist(target_pl)
        else:
            self.notify_user("No playlist selected to download.")

    def _download_single_track(self, track: Dict[str, Any]):
        def _dl_status(text: str, clear_after: Optional[float] = None):
            if hasattr(self, "set_download_status"):
                self.set_download_status(text, clear_after, channel="single")
            else:
                self.notify_user(text, force=True)

        t_id = stable_track_id(track)
        title = track.get("title") or "Unknown Track"
        artist = track.get("artist") or "Unknown Artist"

        track_url = track.get("url")
        if not track_url and t_id and len(t_id) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', t_id):
            track_url = f"https://www.youtube.com/watch?v={t_id}"

        self._active_single_downloads = getattr(self, "_active_single_downloads", 0) + 1
        count = self._active_single_downloads
        _dl_status(f"Downloading '{title}'…" if count == 1 else f"Downloading {count} songs…")

        def _on_done(path):
            self._active_single_downloads = max(0, getattr(self, "_active_single_downloads", 1) - 1)
            rem = self._active_single_downloads
            if rem > 0:
                _dl_status(f"Saved '{title}' offline. {rem} downloads remaining…")
            else:
                _dl_status(f"Saved '{title}' offline.", clear_after=4.0)
            def _refresh():
                if self.active_tab == "offline":
                    self.render_tracks(list(load_offline_index().values()))
                elif self.active_tab == "playlist":
                    self.render_tracks(self.current_playlist_tracks)
                elif self.active_tab == "search":
                    self.render_tracks(self.search_results)
                elif self.active_tab == "liked":
                    self.render_tracks(self.current_liked_tracks)
            self._on_ui(_refresh)

        def _on_err(err):
            self._active_single_downloads = max(0, getattr(self, "_active_single_downloads", 1) - 1)
            rem = self._active_single_downloads
            if rem > 0:
                _dl_status(f"Could not download '{title}'. {rem} downloads remaining…")
            else:
                _dl_status(f"Could not download '{title}'. Try again.", clear_after=6.0)

        download_track_to_cache(
            t_id,
            title,
            artist,
            on_complete=lambda path: self._on_ui(_on_done, path),
            direct_url=track_url,
            track_meta=track,
            on_error=lambda err: self._on_ui(_on_err, err)
        )

    def _bulk_download_playlist(self, playlist: Dict[str, Any]):
        if getattr(self, "_bulk_download_in_progress", False):
            self.notify_user("Bulk download already in progress. Please wait for it to complete.")
            return

        name = playlist.get("name") or "Playlist"
        tracks = list(playlist.get("tracks") or [])
        if not tracks:
            self.notify_user(f"Playlist '{name}' has no tracks to download.")
            return

        total = len(tracks)
        self._bulk_download_in_progress = True

        def _dl_status(text: str, clear_after: Optional[float] = None):
            if hasattr(self, "set_download_status"):
                self.set_download_status(text, clear_after, channel="bulk")
            else:
                self.call_from_thread(self.notify_user, text)

        def _worker():
            success_count = 0
            fail_count = 0
            try:
                # Batch register any existing cached tracks with new metadata in background
                try:
                    with storage_transaction():
                        idx = load_offline_index()
                        idx_changed = False
                        for t in tracks:
                            tid = stable_track_id(t)
                            c = get_cached_track_path(tid)
                            if c:
                                entry = idx.get(tid)
                                if not entry or entry.get("title") != t.get("title") or entry.get("artist") != t.get("artist"):
                                    try:
                                        dur_ms = int(float(t.get("duration_ms") or 0))
                                    except (ValueError, TypeError):
                                        dur_ms = 0
                                    try:
                                        size_bytes = c.stat().st_size
                                    except OSError:
                                        continue
                                    idx[tid] = {
                                        **(entry or {}),
                                        "id": tid,
                                        "title": t.get("title", "Unknown"),
                                        "artist": t.get("artist", "Unknown"),
                                        "duration_ms": dur_ms,
                                        "filepath": str(c.resolve()),
                                        "size_bytes": size_bytes,
                                        "is_offline": True,
                                    }
                                    idx_changed = True
                        if idx_changed:
                            save_offline_index(idx)
                except Exception as e:
                    logger.debug(f"Metadata repair error during bulk download: {e}")

                needed: List[Dict[str, Any]] = []
                for t in tracks:
                    tid = stable_track_id(t)
                    c = get_cached_track_path(tid)
                    if not c or not cached_audio_matches_duration(c, t.get("duration_ms")):
                        needed.append(t)

                if not needed:
                    _dl_status(f"'{name}' is already available offline ({total} {'song' if total == 1 else 'songs'}).", clear_after=4.0)
                    def _refresh_cached_rows():
                        if self.active_tab == "offline":
                            self.render_tracks(list(load_offline_index().values()))
                        elif self.active_tab == "liked":
                            self.render_tracks(self.current_liked_tracks)
                        elif self.active_tab == "playlist":
                            self.render_tracks(self.current_playlist_tracks)
                        elif self.active_tab == "search":
                            self.render_tracks(self.search_results)
                    self.call_from_thread(_refresh_cached_rows)
                    return

                to_dl_count = len(needed)
                unit = "song" if to_dl_count == 1 else "songs"
                _dl_status(f"Downloading {to_dl_count} {unit} from '{name}'…")
                for idx, t in enumerate(needed, 1):
                    t_title = t.get("title") or "Unknown"
                    t_artist = t.get("artist") or "Unknown"
                    t_id = stable_track_id(t)
                    t_url = playback_direct_url(t)
                    _dl_status(f"Downloading {idx}/{to_dl_count} from '{name}': {t_title}")

                    dl_ok = [False]

                    def _done(path, ok_ref=dl_ok):
                        ok_ref[0] = True

                    download_track_to_cache(
                        t_id,
                        t_title,
                        t_artist,
                        on_complete=_done,
                        direct_url=t_url,
                        track_meta=t,
                        blocking=True
                    )

                    if dl_ok[0]:
                        success_count += 1
                        def _refresh_table():
                            if self.active_tab == "playlist":
                                self.render_tracks(self.current_playlist_tracks)
                            elif self.active_tab == "offline":
                                self.render_tracks(list(load_offline_index().values()))
                            elif self.active_tab == "liked":
                                self.render_tracks(self.current_liked_tracks)
                            elif self.active_tab == "search":
                                self.render_tracks(self.search_results)
                        self.call_from_thread(_refresh_table)
                    else:
                        fail_count += 1

                msg = f"Saved {success_count}/{to_dl_count} {unit} from '{name}' offline."
                if fail_count:
                    msg += f" {fail_count} failed; try downloading the playlist again."
                _dl_status(msg, clear_after=6.0 if fail_count else 4.0)
                def _final_refresh():
                    if self.active_tab == "playlist":
                        self.render_tracks(self.current_playlist_tracks)
                    elif self.active_tab == "offline":
                        self.render_tracks(list(load_offline_index().values()))
                    elif self.active_tab == "liked":
                        self.render_tracks(self.current_liked_tracks)
                    elif self.active_tab == "search":
                        self.render_tracks(self.search_results)
                self.call_from_thread(_final_refresh)
            except Exception:
                logger.exception("Bulk download failed for %s", name)
                _dl_status(f"Could not finish downloading '{name}'. Try again.", clear_after=6.0)
            finally:
                self._bulk_download_in_progress = False

        _dl_status(f"Checking offline songs in '{name}'…")
        try:
            threading.Thread(target=_worker, daemon=True).start()
        except Exception:
            self._bulk_download_in_progress = False
            _dl_status("Could not start the download. Try again.", clear_after=6.0)

    def _get_target_playlist(self) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        """Resolves the active or selected playlist and its index in self.playlists based on focus/active tab."""
        target_pl = None
        target_idx = None

        # 1. If focused on side-table, use sidebar cursor
        if self.focused and getattr(self.focused, "id", None) == "side-table" and isinstance(self.focused, DataTable):
            row_idx = self.focused.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self.playlists):
                target_idx = row_idx
                target_pl = self.playlists[row_idx]

        # 2. If viewing playlist tab or we have current_playlist_id
        if not target_pl and hasattr(self, "current_playlist_id") and self.current_playlist_id:
            for idx, p in enumerate(self.playlists):
                if p.get("id") == self.current_playlist_id:
                    target_idx = idx
                    target_pl = p
                    break

        # 3. Fallback to sidebar cursor
        if not target_pl and self.playlists:
            try:
                st = self.query_one("#side-table", DataTable)
                idx = st.cursor_row if st.cursor_row is not None else 0
                if 0 <= idx < len(self.playlists):
                    target_idx = idx
                    target_pl = self.playlists[idx]
            except Exception:
                pass

        return target_idx, target_pl

    def action_delete_playlist(self):
        if isinstance(self.focused, Input):
            return

        if self.active_tab == "liked":
            self.notify_user("Cannot delete Liked Songs.")
            return

        if self.active_tab != "playlist":
            self.notify_user("Switch to Playlists tab to delete playlists.")
            return

        target_idx, target_pl = self._get_target_playlist()
        if not target_pl:
            self.notify_user("No playlist selected to delete.")
            return

        pname = target_pl.get("name", "Playlist")
        pl_id = str(target_pl.get("id") or target_pl.get("name") or "")
        if not pl_id:
            self.notify_user("Cannot delete playlist: missing playlist ID.")
            return

        if pl_id in ("liked_songs", "spotify_liked_songs"):
            self.notify_user("Cannot delete Liked Songs.")
            return

        def handle_delete_confirm(confirmed: Optional[bool]) -> None:
            if not confirmed:
                return
            remote_id = extract_spotify_playlist_id(target_pl) or extract_spotify_playlist_id(pl_id)
            if remote_id:
                record_deleted_spotify_playlist_id(remote_id)

            remove_saved_playlist(pl_id)
            self.playlists = load_saved_playlists()
            self.refresh_side_table()

            try:
                st = self.query_one("#side-table", DataTable)
                if self.playlists:
                    new_row = max(0, min(target_idx or 0, len(self.playlists) - 1))
                    st.move_cursor(row=new_row)
                else:
                    st.clear()
            except Exception:
                pass

            if self.current_playlist_id == pl_id:
                if self.playlists:
                    new_row = max(0, min(target_idx or 0, len(self.playlists) - 1))
                    self.load_playlist_by_index(new_row, focus_tracks=(self.active_tab == "playlist"))
                else:
                    self.current_playlist_id = None
                    self.current_playlist_tracks = []
                    if self.active_tab == "playlist":
                        self.render_tracks([])

            # Sync playlist deletion/unfollow with Spotify account in background
            if remote_id:
                self.notify_user(f"Deleted '{pname}' locally. Syncing with Spotify...")
                def _sync_del_pl_bg():
                    ok, msg = delete_spotify_playlist(pl_id, pname, remote_id=remote_id)
                    if ok:
                        self.call_from_thread(self.notify_user, f"Deleted '{pname}' locally and from Spotify.")
                    else:
                        if "not found on spotify" not in msg.lower():
                            self.call_from_thread(self.notify_user, f"Failed to delete '{pname}' from Spotify: {msg}")
                self._submit_spotify_job(_sync_del_pl_bg)
            else:
                self.notify_user(f"Deleted playlist '{pname}'.")

        track_count = len(target_pl.get("tracks", [])) if isinstance(target_pl.get("tracks"), list) else 0
        self.push_screen(
            DeletePlaylistModal(pname, track_count=track_count),
            handle_delete_confirm
        )

    def action_rename_playlist(self):
        if isinstance(self.focused, Input):
            return

        if self.active_tab == "liked":
            self.notify_user("Cannot rename Liked Songs.")
            return

        if self.active_tab != "playlist":
            self.notify_user("Switch to Playlists tab to rename playlists.")
            return

        target_idx, target_pl = self._get_target_playlist()
        if not target_pl:
            self.notify_user("No playlist selected to rename.")
            return

        pl_id = str(target_pl.get("id") or target_pl.get("name") or "")
        if not pl_id:
            self.notify_user("Cannot rename playlist: missing playlist ID.")
            return

        if pl_id in ("liked_songs", "spotify_liked_songs"):
            self.notify_user("Cannot rename Liked Songs.")
            return

        cur_name = target_pl.get("name", "Playlist")

        def handle_rename_submit(new_name: Optional[str]) -> None:
            if not new_name or not new_name.strip() or new_name.strip() == cur_name:
                return
            clean_name = new_name.strip()
            ok = rename_saved_playlist(pl_id, clean_name)
            if ok:
                self.playlists = load_saved_playlists()
                self.refresh_side_table()

                st = self.query_one("#side-table", DataTable)
                if self.playlists:
                    new_row = max(0, min(target_idx if target_idx is not None else 0, len(self.playlists) - 1))
                    st.move_cursor(row=new_row)

                if self.current_playlist_id == pl_id:
                    if self.playlists:
                        new_row = max(0, min(target_idx if target_idx is not None else 0, len(self.playlists) - 1))
                        self.load_playlist_by_index(new_row, focus_tracks=bool(self.focused and getattr(self.focused, "id", None) == "track-table"))

                # Sync rename to Spotify account asynchronously if linked
                remote_id = extract_spotify_playlist_id(target_pl) or extract_spotify_playlist_id(pl_id)
                def _sync_rename_bg():
                    s_ok, s_msg = rename_spotify_playlist(pl_id, clean_name, remote_id=remote_id)
                    if s_ok:
                        self.call_from_thread(self.notify_user, f"Renamed on Spotify: '{clean_name}'")
                self._submit_spotify_job(_sync_rename_bg)

                self.notify_user(f"Renamed playlist to '{clean_name}'.")
            else:
                self.notify_user("Failed to rename playlist.")

        self.push_screen(
            RenamePlaylistModal(cur_name),
            handle_rename_submit
        )

    def action_playlist_settings(self):
        if isinstance(self.focused, Input):
            return
        on_sidebar = bool(self.focused and getattr(self.focused, "id", None) == "side-table")
        if self.active_tab == "liked" and not on_sidebar:
            self.notify_user("Liked Songs has no playlist settings.")
            return
        if self.active_tab != "playlist" and not on_sidebar:
            self.notify_user("Select a playlist in the sidebar first.")
            return
        _, target_pl = self._get_target_playlist()
        if not target_pl or not target_pl.get("id"):
            self.notify_user("No playlist selected.")
            return

        pl_id = str(target_pl["id"])
        remote_id = extract_spotify_playlist_id(target_pl)
        me = ((load_spotify_auth() or {}).get("user") or {}).get("id")
        owner = target_pl.get("owner_id")
        followed = bool(remote_id and owner and me and owner != me)

        def _on_done(result: Optional[Dict[str, Any]]) -> None:
            if not result:
                return
            current = {"name": target_pl.get("name") or "", "description": target_pl.get("description") or "",
                       "public": bool(target_pl.get("public", False))}
            changes = {k: v for k, v in result.items() if current.get(k) != v}
            if not changes:
                return
            update_playlist_details(pl_id, changes)
            self.playlists = load_saved_playlists()
            self.refresh_side_table()
            name = result["name"]
            if not remote_id or followed:
                self.notify_user(f"Saved settings for '{name}'.")
                return
            self.notify_user(f"Saved settings for '{name}'. Updating Spotify…")

            def _push():
                ok, msg = update_spotify_playlist_details(pl_id, remote_id=remote_id, **changes)
                text = f"'{name}' updated on Spotify." if ok else f"Saved in Spoff, but Spotify said: {msg}"
                self._on_ui(self.notify_user, text, force=True)
            self._submit_spotify_job(_push)

        self.push_screen(PlaylistSettingsModal(target_pl, linked=bool(remote_id), followed=followed), _on_done)

    def action_clone_playlist(self):
        if isinstance(self.focused, Input):
            return

        if self.active_tab == "liked":
            target_idx = None
            target_pl = {"id": "liked_songs", "name": "Liked Songs", "tracks": getattr(self, "current_liked_tracks", [])}
        else:
            target_idx, target_pl = self._get_target_playlist()
        if not target_pl:
            self.notify_user("No playlist selected to copy.")
            return

        pl_id = str(target_pl.get("id") or target_pl.get("name") or "")
        if not pl_id:
            self.notify_user("Cannot copy playlist: missing playlist ID.")
            return

        cur_name = target_pl.get("name", "Playlist")
        tracks = target_pl.get("tracks", [])
        track_count = len(tracks) if isinstance(tracks, list) else 0

        def handle_clone_submit(cloned_name: Optional[str]) -> None:
            if not cloned_name or not cloned_name.strip():
                return
            clean_name = cloned_name.strip()
            cloned_pl = clone_saved_playlist(pl_id, clean_name)
            if cloned_pl:
                self.playlists = load_saved_playlists()
                self.refresh_side_table()

                new_idx = None
                for idx, p in enumerate(self.playlists):
                    if p.get("id") == cloned_pl.get("id"):
                        new_idx = idx
                        break

                if new_idx is not None:
                    try:
                        st = self.query_one("#side-table", DataTable)
                        st.move_cursor(row=new_idx)
                    except Exception:
                        pass
                    self.load_playlist_by_index(new_idx, focus_tracks=False)

                self.notify_user(f"Copied '{cur_name}' -> '{clean_name}' ({track_count} tracks).")

                # Sync clone to Spotify account in background if logged in
                def _sync_clone_bg():
                    s_ok, sp_id, s_msg = clone_spotify_playlist(
                        local_playlist_id=cloned_pl.get("id", ""),
                        cloned_name=clean_name,
                        tracks=cloned_pl.get("tracks", []),
                    )
                    self.call_from_thread(self.notify_user, s_msg)
                self._submit_spotify_job(_sync_clone_bg)
            else:
                self.notify_user("Failed to copy playlist.")

        self.push_screen(
            ClonePlaylistModal(cur_name, track_count),
            handle_clone_submit
        )

    def action_delete_item(self):
        f = self.focused
        if isinstance(f, Input):
            return

        if self.active_tab == "playlist" and f and getattr(f, "id", None) == "side-table":
            self.action_delete_playlist()
            return

        row_idx = None
        if f and getattr(f, "id", None) == "track-table":
            row_idx = getattr(f, "cursor_row", None)
        else:
            try:
                tt = self.query_one("#track-table", DataTable)
                row_idx = getattr(tt, "cursor_row", None)
            except Exception:
                row_idx = None

        if self.active_tab in ("playlist", "liked", "offline", "search"):
            if self.active_tab == "playlist":
                if not self.current_playlist_tracks or row_idx is None or row_idx < 0 or row_idx >= len(self.current_playlist_tracks):
                    self.notify_user("No track selected to remove.", force=True)
                    return

                # Otherwise, removing a track from the playlist requires explicit confirmation
                t = self.current_playlist_tracks[row_idx]
                t_title = t.get("title", "Track")
                pl_id = self.current_playlist_id
                if not pl_id:
                    self.notify_user("No playlist currently active.")
                    return
                pl_name = next((p.get("name", "Playlist") for p in self.playlists if p.get("id") == pl_id), "Playlist")

                target_track = t

                def handle_remove_track_confirm(confirmed: Optional[bool]) -> None:
                    if not confirmed:
                        return
                    removed_track = remove_track_from_playlist_by_index_or_track(pl_id, track=target_track, index=row_idx)
                    if not removed_track:
                        self.notify_user("Failed to remove track from playlist.")
                        return

                    self.playlists = load_saved_playlists()
                    for p in self.playlists:
                        if p.get("id") == pl_id:
                            self.current_playlist_tracks = list(p.get("tracks") or [])
                            break

                    if self.current_playlist_id == pl_id:
                        new_row = max(0, min(row_idx if row_idx is not None else 0, len(self.current_playlist_tracks) - 1)) if self.current_playlist_tracks else None
                        self.render_tracks(self.current_playlist_tracks, select_row=new_row)
                    self.refresh_side_table()

                    def _sync_remove_bg():
                        ok, msg = remove_track_from_spotify_account(pl_id, pl_name, removed_track)
                        if ok:
                            if "client-side" not in msg.lower() and "not found" not in msg.lower():
                                self._on_ui(self.notify_user, f"Removed '{t_title}' from Spotify playlist '{pl_name}'.")
                        elif msg and not msg.startswith("Not logged in"):
                            logger.info(f"Failed to remove '{t_title}' from Spotify: {msg}")
                    self._submit_spotify_job(_sync_remove_bg)

                    if self.queue:
                        q_idx = None
                        if row_idx is not None and 0 <= row_idx < len(self.queue) and (self.queue[row_idx] == removed_track or (removed_track.get("id") and self.queue[row_idx].get("id") == removed_track.get("id"))):
                            q_idx = row_idx
                        else:
                            for i, q_item in enumerate(self.queue):
                                if q_item == removed_track or (removed_track.get("id") and q_item.get("id") == removed_track.get("id")):
                                    q_idx = i
                                    break
                        if q_idx is not None:
                            was_current = self.current_index == q_idx
                            if was_current and getattr(self, "_pending_track", None) is not None:
                                self._play_request_id = getattr(self, "_play_request_id", 0) + 1
                                self._pending_track = None
                            self.queue.pop(q_idx)
                            if not self.queue:
                                self.current_index = -1
                            elif was_current:
                                self.current_index = q_idx - 1
                            elif q_idx < self.current_index:
                                self.current_index -= 1
                            if hasattr(self, "_shuffle_history") and self._shuffle_history is not None:
                                self._shuffle_history.clear()
                            if hasattr(self, "update_player_hud"):
                                self.update_player_hud()

                    self.notify_user(f"Removed '{t_title}' from playlist '{pl_name}'.")

                self.push_screen(
                    ConfirmModal(
                        title="REMOVE TRACK",
                        message=f"Remove '[bold #ffffff]{escape(t_title)}[/]' from playlist '[bold #ffffff]{escape(pl_name)}[/]'?\n[dim]To delete the entire playlist, press Shift+D[/dim]",
                        confirm_label="Remove"
                    ),
                    handle_remove_track_confirm
                )
                return

            elif self.active_tab == "liked":
                liked_tracks = list(self.current_liked_tracks if self.current_liked_tracks else load_liked_songs())
                if not liked_tracks or row_idx is None or row_idx < 0 or row_idx >= len(liked_tracks):
                    self.notify_user("No liked song selected to remove.")
                    return

                t = liked_tracks[row_idx]
                t_title = t.get("title", "Track")

                def handle_remove_liked_confirm(confirmed: Optional[bool]) -> None:
                    if not confirmed:
                        return
                    remove_liked_track(t)
                    self.current_liked_tracks = load_liked_songs()
                    new_row = max(0, min(row_idx, len(self.current_liked_tracks) - 1)) if self.current_liked_tracks else None
                    self.render_tracks(self.current_liked_tracks, select_row=new_row)

                    def _sync_remove_liked_bg():
                        ok, msg = remove_track_from_spotify_account("liked", "Liked Songs", t)
                        if ok:
                            self._on_ui(self.notify_user, f"Removed '{t_title}' from Spotify Liked Songs.")
                    self._submit_spotify_job(_sync_remove_liked_bg)

                    if self.queue:
                        q_idx = None
                        if 0 <= row_idx < len(self.queue) and (self.queue[row_idx] == t or (t.get("id") and self.queue[row_idx].get("id") == t.get("id"))):
                            q_idx = row_idx
                        else:
                            for i, q_item in enumerate(self.queue):
                                if q_item == t or (t.get("id") and q_item.get("id") == t.get("id")):
                                    q_idx = i
                                    break
                        if q_idx is not None:
                            was_current = (q_idx == self.current_index)
                            if was_current and getattr(self, "_pending_track", None) is not None:
                                self._play_request_id = getattr(self, "_play_request_id", 0) + 1
                                self._pending_track = None
                            self.queue.pop(q_idx)
                            if not self.queue:
                                self.current_index = -1
                            elif was_current:
                                self.current_index = q_idx - 1
                            elif q_idx < self.current_index:
                                self.current_index -= 1
                            if hasattr(self, "_shuffle_history") and self._shuffle_history is not None:
                                self._shuffle_history.clear()
                            if hasattr(self, "update_player_hud"):
                                self.update_player_hud()

                    self.notify_user(f"Removed '{t_title}' from Liked Songs.")

                self.push_screen(
                    ConfirmModal(
                        title="REMOVE LIKED SONG",
                        message=f"Remove '[bold #ffffff]{escape(t_title)}[/]' from your Liked Songs?",
                        confirm_label="Remove"
                    ),
                    handle_remove_liked_confirm
                )
                return

            elif self.active_tab == "offline":
                offline_tracks = list(load_offline_index().values())
                if row_idx is not None and 0 <= row_idx < len(offline_tracks):
                    t = offline_tracks[row_idx]
                    t_title = t.get("title", "Track")
                    if t.get("id"):
                        delete_cached_track(t["id"])
                    remaining = list(load_offline_index().values())
                    new_row = max(0, min(row_idx, len(remaining) - 1)) if remaining else None
                    self.render_tracks(remaining, select_row=new_row)
                    if self.queue:
                        q_idx = None
                        if 0 <= row_idx < len(self.queue) and (self.queue[row_idx] == t or (t.get("id") and self.queue[row_idx].get("id") == t.get("id"))):
                            q_idx = row_idx
                        else:
                            for i, q_item in enumerate(self.queue):
                                if q_item == t or (t.get("id") and q_item.get("id") == t.get("id")):
                                    q_idx = i
                                    break
                        if q_idx is not None:
                            was_current = (q_idx == self.current_index)
                            if was_current and getattr(self, "_pending_track", None) is not None:
                                self._play_request_id = getattr(self, "_play_request_id", 0) + 1
                                self._pending_track = None
                            self.queue.pop(q_idx)
                            if not self.queue:
                                self.current_index = -1
                            elif was_current:
                                self.current_index = q_idx - 1
                            elif q_idx < self.current_index:
                                self.current_index -= 1
                            if hasattr(self, "_shuffle_history") and self._shuffle_history is not None:
                                self._shuffle_history.clear()
                            self.update_player_hud()
                    self.notify_user(f"Removed '{t_title}' from offline disk cache.")

            elif self.active_tab == "search":
                if row_idx is not None and 0 <= row_idx < len(self.search_results):
                    t = self.search_results.pop(row_idx)
                    new_row = max(0, min(row_idx, len(self.search_results) - 1)) if self.search_results else None
                    self.render_tracks(self.search_results, select_row=new_row)
                    if self.queue:
                        q_idx = None
                        if 0 <= row_idx < len(self.queue) and (self.queue[row_idx] == t or (t.get("id") and self.queue[row_idx].get("id") == t.get("id"))):
                            q_idx = row_idx
                        else:
                            for i, q_item in enumerate(self.queue):
                                if q_item == t or (t.get("id") and q_item.get("id") == t.get("id")):
                                    q_idx = i
                                    break
                        if q_idx is not None:
                            was_current = (q_idx == self.current_index)
                            if was_current and getattr(self, "_pending_track", None) is not None:
                                self._play_request_id = getattr(self, "_play_request_id", 0) + 1
                                self._pending_track = None
                            self.queue.pop(q_idx)
                            if not self.queue:
                                self.current_index = -1
                            elif was_current:
                                self.current_index = q_idx - 1
                            elif q_idx < self.current_index:
                                self.current_index -= 1
                            if hasattr(self, "_shuffle_history") and self._shuffle_history is not None:
                                self._shuffle_history.clear()
                            self.update_player_hud()
                    self.notify_user(f"Removed '{t.get('title')}' from search results.")
        else:
            if self.active_tab == "playlist":
                self.action_delete_playlist()

    def _handle_track_end(self, request_id: int, reason: str = "eof", track: Optional[Dict[str, Any]] = None, source: Optional[str] = None):
        if getattr(self, "_closing", False) or not getattr(self, "is_mounted", False):
            return
        if request_id != getattr(self, "_play_request_id", None):
            return
        if reason == "error":
            t = track or getattr(getattr(self, "player", None), "current_track", None)
            if t:
                was_cached = False
                try:
                    was_cached = quarantine_cached_track(t.get("id", ""), source)
                except Exception:
                    pass
                if was_cached:
                    retry_key = f"cache_retry_{t.get('id', '')}_{request_id}"
                    if not getattr(self, "_cache_retried", None):
                        self._cache_retried = set()
                    if retry_key not in self._cache_retried:
                        self._cache_retried.add(retry_key)
                        if hasattr(self, "play_index"):
                            self.play_index(getattr(self, "current_index", 0))
                        return
                self._playback_failed(request_id, t)
            return
        if self.repeat_mode == "one" and self.current_index >= 0:
            self.play_index(self.current_index)
        else:
            self.action_next_track()

    def on_track_finished(self, reason: str = "eof"):
        self._on_ui(self._handle_track_end, getattr(self, "_play_request_id", 0), reason, getattr(getattr(self, "player", None), "current_track", None), None)

    def update_player_hud(self):
        if not getattr(self, "is_mounted", False):
            return
        SpoffTUI._update_loading_status(self)
        try:
            pos, dur = self.player.get_progress()
            self.query_one("#time-elapsed", Static).update(format_time(pos))
            self.query_one("#time-total", Static).update(format_time(dur) if dur > 0 else "--:--")

            bar = self.query_one("#playback-bar", ScrubBar)
            if dur > 0:
                bar.total = dur
                bar.progress = pos
            else:
                bar.total = 100.0
                bar.progress = 0.0
        except Exception:
            return

        curr = self.player.current_track
        is_scrubbing = (self.focused and self.focused.id == "playback-bar")

        is_paused = self.player.is_paused if curr else False

        # Update shuffle & repeat indicators
        shuf_badge = "[#ffffff]SHUF[/]" if self.shuffle_mode else ""
        self.query_one("#shuf-pill", Static).update(shuf_badge)

        rep_badge = ""
        if self.repeat_mode == "all":
            rep_badge = "[#ffffff]REP[/]"
        elif self.repeat_mode == "one":
            rep_badge = "[#ffffff]REP-1[/]"
        self.query_one("#rep-pill", Static).update(rep_badge)

        # Update synced lyrics tracking
        if self.active_tab == "lyrics" and self.current_lyrics and self.current_lyrics.get("synced"):
            lines = self.current_lyrics.get("lines", [])
            active_idx = get_active_lyric_index(lines, pos)
            if active_idx != self._active_lyric_idx:
                old_idx = self._active_lyric_idx
                self._active_lyric_idx = active_idx
                self._highlight_lyric_line(old_idx, active_idx, lines)

        # MPRIS Desktop Media Integration
        if self.mpris:
            self.mpris.update_position(pos)
            if dur > 0:
                self.mpris.update_duration(dur)
            self.mpris.update_volume(self.volume)
            self.mpris.update_status(curr is not None, is_paused)

        if curr:
            safe_title = escape(str(curr.get("title", "") or "Unknown Track"))
            safe_artist = escape(str(curr.get("artist", "") or "")).strip()
            if safe_artist and safe_artist.lower() not in ("unknown", "unknown artist", ""):
                track_display = f"[bold #ffffff]{safe_title}[/]  [#555555]—[/]  [#cccccc]{safe_artist}[/]"
            else:
                track_display = f"[bold #ffffff]{safe_title}[/]"
            self.query_one("#deck-track", Static).update(track_display)
        else:
            self.query_one("#deck-track", Static).update("[dim]No track playing[/dim]")

        queue_len = len(self.queue)
        queue_pos = f"{self.current_index + 1}/{queue_len}" if queue_len > 0 and self.current_index >= 0 else ""
        vol_str = "Muted" if self.volume == 0 else f"{self.volume}%"

        if self.advanced_mode:
            if is_scrubbing:
                stat_text = "[bold #c4a768]SEEKING[/]"
            else:
                eq_pill = "  [#ffffff]EQ[/]" if (hasattr(self, "eq_engine") and not self.eq_engine.bypassed) else ""
                q_text = f"  Q: {queue_pos}" if queue_pos else ""
                stat_text = f"[#767676]Vol: {vol_str}{q_text}[/]{eq_pill}"
            try:
                stats_pill = self.query_one("#deck-stats-pill", Static)
                stats_pill.update(stat_text)
                stats_pill.styles.display = "block"
            except Exception:
                pass
            try:
                deck_l3 = self.query_one("#deck-line-3", Static)
                deck_l3.update("")
                deck_l3.styles.display = "none"
            except Exception:
                pass
        else:
            try:
                stats_pill = self.query_one("#deck-stats-pill", Static)
                stats_pill.update("")
                stats_pill.styles.display = "none"
            except Exception:
                pass
            if is_scrubbing:
                hints = "Seek: h/l (-/+5s)  |  H/L (-/+15s)  |  0-9: jump %  |  Space: pause  |  Esc: back"
            elif self.active_tab == "lyrics":
                lyr_k = format_key_display(self.keybindings.get("nav_lyrics", "4"))
                hints = f"Enter/Click: seek to line  |  Space: pause  |  s: shuf  |  r: rep  |  Esc/{lyr_k}: back  |  q: quit"
            else:
                if self.focused and getattr(self.focused, "id", None) == "side-table":
                    pl_ren = self.keybindings.get("rename_playlist", "R")
                    ren_hint = f"{format_key_display(pl_ren)}: rename  |  " if pl_ren else ""
                    pl_cln = self.keybindings.get("clone_playlist", "Y")
                    cln_hint = f"{format_key_display(pl_cln)}: copy  |  " if pl_cln else ""
                    pl_del = self.keybindings.get("delete_playlist", "D")
                    del_hint = f"{format_key_display(pl_del)}: del  |  " if pl_del else ""
                    pl_share = self.keybindings.get("share_playlist", "y")
                    pl_set = self.keybindings.get("playlist_settings", "S")
                    set_hint = f"{format_key_display(pl_set)}: settings  |  " if pl_set else ""
                    share_hint = f"{set_hint}{ren_hint}{cln_hint}{del_hint}" + (f"{format_key_display(pl_share)}: share pl  |  " if pl_share else "")
                else:
                    share_bound = self.keybindings.get("share_track", "")
                    share_hint = f"{format_key_display(share_bound)}: share  |  " if share_bound else ""
                like_k = format_key_display(self.keybindings.get("like_track", "l"))
                like_hint = f"{like_k}: like  |  " if like_k else ""
                shuf_k = format_key_display(self.keybindings.get("toggle_shuffle", "s"))
                rep_k = format_key_display(self.keybindings.get("toggle_repeat", "r"))
                vis_k = format_key_display(self.keybindings.get("toggle_visualizer", "v"))
                eq_k = format_key_display(self.keybindings.get("open_equalizer", "e"))
                eq_hint = f"{eq_k}: eq  |  " if eq_k else ""
                help_k = format_key_display(self.keybindings.get("show_help", ":"))
                help_label = ": help" if help_k in (":", "colon") else f"{help_k}: help"
                quit_k = format_key_display(self.keybindings.get("quit_app", "q"))
                q_hint = f"Queue: {queue_pos}  |  " if queue_pos else ""
                hints = f"Vol: {vol_str}  |  {q_hint}{share_hint}{like_hint}{shuf_k}: shuf  |  {rep_k}: rep  |  {vis_k}: vis  |  {eq_hint}{help_label}  |  {quit_k}: quit"
            try:
                deck_l3 = self.query_one("#deck-line-3", Static)
                deck_l3.update(escape(hints))
                deck_l3.styles.display = "block"
            except Exception:
                pass

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-box":
            q = event.value.strip()
            if q:
                self.do_search(q)
        elif event.input.id == "sidebar-import-input":
            u = event.value.strip()
            if u:
                event.input.value = ""
                if u.startswith("http://") or u.startswith("https://") or "spotify.com" in u or "youtube.com" in u or "youtu.be" in u or u.startswith("PL") or u.startswith("MPREb_"):
                    self.import_playlist_url(u)
                else:
                    new_pl = create_local_playlist(u)
                    self.playlists = load_saved_playlists()
                    self.refresh_side_table()
                    self.load_playlist_by_index(0, focus_tracks=True)
                    self.notify_user(f"Created playlist '{u}'." if self.advanced_mode else f"Created playlist '{u}'. Press 'a' on any song to add it.", force=True)
                    if has_modify_scopes():
                        def _bg_create_pl():
                            try:
                                sync_playlist_tracks_to_spotify(new_pl["id"])
                            except Exception as e:
                                logger.debug(f"Failed to sync newly created playlist '{u}' to Spotify: {e}")
                        self._submit_spotify_job(_bg_create_pl)

    def do_search(self, query: str):
        if self.active_tab != "search":
            self.switch_view("search")
        self._search_request_id = getattr(self, "_search_request_id", 0) + 1
        req_id = self._search_request_id
        engine = self.search_engine
        engine_name = "Spotify" if engine == "spotify" else "YouTube Music"
        is_url = bool(re.search(r'^(?:https?://|spotify:)', query.strip()))
        self._search_status_text = "Looking up that track link…" if is_url else f"Searching {engine_name} for '{query}'…"
        self.notify_user(self._search_status_text, force=True)
        self._search_loading_request_id = req_id
        SpoffTUI._update_loading_status(self)
        try:
            self._search_worker(query, req_id, engine, engine_name)
        except Exception:
            self._search_loading_request_id = None
            SpoffTUI._update_loading_status(self)
            logger.exception("Could not start search worker")
            self.notify_user("Could not start the search. Please try again.", force=True)

    @work(thread=True)
    def _search_worker(self, query: str, req_id: int, engine: str, engine_name: str):
        if getattr(self, "_closing", False) or req_id != getattr(self, "_search_request_id", None):
            return
        try:
            SpoffTUI._run_search(self, query, req_id, engine, engine_name)
        except Exception:
            logger.exception("Search failed for %r", query)
            def _error():
                if req_id == getattr(self, "_search_request_id", None):
                    self.notify_user("Search failed. Please try again.", force=True)
            self.call_from_thread(_error)
        finally:
            def _finished():
                if req_id == getattr(self, "_search_loading_request_id", None):
                    self._search_loading_request_id = None
                    SpoffTUI._update_loading_status(self)
            self.call_from_thread(_finished)

    def _run_search(self, query: str, req_id: int, engine: str, engine_name: str):
        results = []
        fallback_msg = None
        direct_track = resolve_direct_track_url(query)
        if direct_track:
            results = [direct_track]
            engine_name = "Direct Link"
            fallback_msg = f"Resolved direct track: '{direct_track.get('title', 'Track')}' by {direct_track.get('artist', 'Artist')}."
        elif query.strip().startswith(("https://", "http://", "spotify:")):
            fallback_msg = "Could not resolve that track link. Check the link and try again."
        elif engine == "spotify":
            ok, sp_results, err_msg = search_spotify_tracks(query, limit=25)
            if ok and sp_results:
                results = sp_results
            else:
                # If Spotify failed (not logged in or API error) or returned no results,
                # seamlessly fall back to YouTube Music so searching always produces results.
                yt_results = live_search_tracks(query, limit=25)
                if yt_results:
                    results = yt_results
                    engine_name = "YouTube Music (Spotify fallback)"
                    fallback_msg = f"Spotify search unavailable ({err_msg or 'no tracks'}). Showing YouTube Music results."
                elif not ok:
                    def _notify_err():
                        if req_id != getattr(self, "_search_request_id", None):
                            return
                        self.search_results = []
                        if self.active_tab == "search":
                            self.render_tracks([])
                        self.notify_user(err_msg or "Failed to search Spotify.", force=True)
                    self.call_from_thread(_notify_err)
                    return
        else:
            results = live_search_tracks(query, limit=25)

        def _update_ui():
            if req_id != getattr(self, "_search_request_id", None):
                return
            self.search_results = results
            if self.active_tab == "search":
                self.render_tracks(results, select_row=0 if results else None)
                if results:
                    self.query_one("#track-table", DataTable).focus()
                else:
                    self.query_one("#search-box", Input).focus()
            if results:
                hint_str = "" if self.advanced_mode else " Press Enter to play."
                msg = fallback_msg or f"Found {len(results)} tracks on {engine_name} for '{query}'.{hint_str}"
                self.notify_user(msg, force=True)
            else:
                self.notify_user(fallback_msg or f"No tracks found on {engine_name} for '{query}'. Try different keywords.", force=True)

        self.call_from_thread(_update_ui)


    @work(thread=True)
    def import_playlist_url(self, url: str):
        parsed_sp = parse_spotify_url(url)
        parsed_yt = parse_ytmusic_url(url)

        tracks = []
        name = "Imported Playlist"
        pid = None

        if parsed_sp or ("spotify.com" in url or url.startswith("spotify:")):
            self.notify_user("Fetching tracks from Spotify link...")
            if parsed_sp and parsed_sp[0] == "track":
                single = fetch_spotify_track(url)
                pl = ({"id": f"local_spotify_track_{parsed_sp[1]}",
                       "name": single.get("title") or "Imported Track",
                       "tracks": [single]} if single else None)
            elif parsed_sp and parsed_sp[0] == "album":
                pl = fetch_spotify_album(url)
                if pl:
                    pl["id"] = f"local_spotify_album_{parsed_sp[1]}"
            else:
                pl = fetch_spotify_playlist(url)

            if pl:
                tracks = pl.get("tracks", [])
                name = pl.get("name", "Spotify Playlist")
                pid = pl["id"]
            else:
                self.notify_user("Could not load Spotify link. Please check that the link is public.")
                return

        elif parsed_yt or ("youtube.com" in url or "youtu.be" in url or url.startswith("PL") or url.startswith("MPREb_")):
            self.notify_user("Fetching tracks from YouTube Music link...")
            if parsed_yt and parsed_yt[0] == "album":
                pl = fetch_ytmusic_album(url)
            elif parsed_yt and parsed_yt[0] == "track":
                pl = fetch_ytmusic_track(url)
                if pl:
                    tracks = [pl]
                    name = pl.get("title", "YouTube Track")
                    pid = pl.get("id", "pl_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12])
            else:
                pl = fetch_ytmusic_playlist(url)

            if pl and not tracks:
                tracks = pl.get("tracks", [])
                name = pl.get("name", "YouTube Music Playlist")
                pid = pl.get("id", "pl_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12])
            elif not pl and not tracks:
                self.notify_user("Could not load YouTube Music playlist. Please check that the link is valid and public.")
                return
        else:
            self.notify_user("Fetching tracks from music link...")
            pl = fetch_spotify_playlist(url)
            if not pl:
                pl = fetch_ytmusic_playlist(url)
            if pl:
                tracks = pl.get("tracks", [])
                name = pl.get("name", "Music Playlist")
                pid = pl.get("id", "pl_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12])
            else:
                self.notify_user("Could not recognize or load playlist link.")
                return

        if not pid:
            pid = f"pl_{secrets.token_hex(6)}"

        new_entry = {"id": pid, "name": name, "url": url, "tracks": tracks}
        if parsed_sp and parsed_sp[0] == "playlist":
            new_entry["spotify_id"] = parsed_sp[1]
            remove_deleted_spotify_playlist_id(parsed_sp[1])
        elif parsed_sp:
            remove_deleted_spotify_playlist_id(parsed_sp[1])
        add_saved_playlist(new_entry)

        def _update():
            self.playlists = load_saved_playlists()
            self.current_playlist_id = pid
            self.current_playlist_tracks = tracks
            self.refresh_side_table()
            self.notify_user(f"Imported '{name}' ({len(tracks)} tracks).")
            self.switch_view("playlist", focus_sidebar=False)
            self.query_one("#track-table", DataTable).focus()
        self.call_from_thread(_update)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not getattr(self, "is_mounted", False):
            return
        table_id = event.data_table.id
        if table_id == "side-table":
            idx = event.cursor_row
            if idx is not None and 0 <= idx < len(self.playlists):
                self._on_sidebar_playlist_highlighted(idx)

    def _on_sidebar_playlist_highlighted(self, idx: int) -> None:
        # Browsing the sidebar must not replace playback/download feedback.
        # The row contains the count; opening remains an explicit action.
        return

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        if table_id == "side-table":
            idx = event.cursor_row
            if idx is not None and 0 <= idx < len(self.playlists):
                self.load_playlist_by_index(idx, focus_tracks=True)
        elif table_id == "track-table":
            self.play_current_table_row(event.cursor_row)
        elif table_id == "lyrics-table":
            idx = event.cursor_row
            if self.current_lyrics and self.current_lyrics.get("lines"):
                lines = self.current_lyrics["lines"]
                if idx is not None and 0 <= idx < len(lines):
                    t = lines[idx].get("time")
                    if t is not None:
                        self.player.seek_absolute(t)
                        self.notify_user(f"Seeked to {format_time(t)}")
                        old_idx = self._active_lyric_idx
                        self._active_lyric_idx = idx
                        self._highlight_lyric_line(old_idx, idx, lines)

    def play_index(self, index: int):
        if not (0 <= index < len(self.queue)):
            return
        self.current_index = index
        self._failed_indices.discard(index)
        track = self.queue[index]
        self._pending_track = track
        SpoffTUI._update_loading_status(self)
        self.current_lyrics = None
        self._active_lyric_idx = -1
        if getattr(self, "active_tab", "") == "lyrics":
            self.render_lyrics()
        player = getattr(self, "player", None)
        if player and player.current_track is not None:
            player.pause()
        self._sync_table_cursor_to_index(index)
        self._play_request_id += 1
        req_id = self._play_request_id
        self.start_playback(track, req_id)

    def _commit_playback(self, req_id: int, source: str, track: Dict[str, Any]) -> bool:
        if getattr(self, "_closing", False) or req_id != getattr(self, "_play_request_id", None):
            return False
        pending = getattr(self, "_pending_track", None)
        if pending is not None:
            if liked_index([pending], track) is None:
                return False
            if not (0 <= self.current_index < len(self.queue)):
                return False
            if liked_index([self.queue[self.current_index]], track) is None:
                return False
        cb = lambda reason="eof": self._on_ui(self._handle_track_end, req_id, reason, track, source)
        if hasattr(self.player, "register_pending_callback"):
            self.player.register_pending_callback(cb, req_id)
        else:
            self.player.playback_finished_callback = cb

        if not self.player.load_and_play(source, track):
            self.notify_user(f"Could not start playback: {track.get('title', 'Track')}")
            self._playback_failed(req_id, track)
            return False
        self._pending_track = None
        self.notify_user("")
        if self.mpris:
            try:
                dur_s = float(track.get("duration_ms") or 0) / 1000.0
            except (ValueError, TypeError):
                dur_s = self.player.get_duration()
            self.mpris.update_track(track, dur_s)
        self.update_player_hud()

        origin = getattr(self, "_queue_origin", None)
        if origin and origin.get("tab"):
            tab = origin["tab"]
            pid = origin.get("playlist_id") if tab == "playlist" else None
        else:
            tab = getattr(self, "active_tab", "playlist")
            pid = getattr(self, "current_playlist_id", None) if tab == "playlist" else None

        self._committed_playback = {
            "playlist_id": pid,
            "tab": tab,
            "track_id": track.get("id"),
            "track_title": track.get("title"),
            "track_artist": track.get("artist"),
            "track_index": getattr(self, "current_index", -1),
        }
        self._save_playback_state(track)
        return True

    def _playback_failed(self, req_id: int, track: Dict[str, Any]):
        if getattr(self, "_closing", False) or req_id != getattr(self, "_play_request_id", None):
            return
        self._pending_track = None
        invalidate_stream_cache(track.get("title", ""), track.get("artist", ""), playback_direct_url(track))
        self._failed_indices.add(self.current_index)
        if not self.queue:
            self._play_request_id += 1
            self.player.stop()
            self.current_index = -1
            self._pending_track = None
            if self.mpris:
                self.mpris.update_status(False, False)
                self.mpris.update_track(None)
            self.update_player_hud()
            self.notify_user("No playable tracks remain in this queue.", force=True)
            return
        for offset in range(1, len(self.queue) + 1):
            candidate = (self.current_index + offset) % len(self.queue)
            if candidate not in self._failed_indices:
                self.play_index(candidate)
                return
        self._play_request_id += 1
        self.player.stop()
        self.current_index = -1
        self._pending_track = None
        if self.mpris:
            self.mpris.update_status(False, False)
            self.mpris.update_track(None)
        self.update_player_hud()
        self.notify_user("No playable tracks remain in this queue.", force=True)

    @work(thread=True)
    def start_playback(self, track: Dict[str, Any], req_id: int):
        try:
            SpoffTUI._resolve_playback(self, track, req_id)
        except Exception:
            logger.exception("Playback preparation failed for %s", track.get("title"))
            self.call_from_thread(self._playback_failed, req_id, track)

    def _resolve_playback(self, track: Dict[str, Any], req_id: int):
        def is_current():
            return (not getattr(self, "_closing", False)
                    and req_id == getattr(self, "_play_request_id", None))

        if not is_current():
            return

        t_id = stable_track_id(track)
        title = track.get("title", "Unknown")
        artist = track.get("artist", "Unknown")

        # Asynchronously fetch synced lyrics in background
        def _fetch_lyr_bg():
            if not is_current():
                return
            try:
                dur_ms = track.get("duration_ms")
                lyr = fetch_lyrics(title, artist, dur_ms)
            except Exception:
                logger.exception("Failed to fetch lyrics")
                lyr = None
            def _publish_lyrics():
                if is_current():
                    self.current_lyrics = lyr
                    if self.active_tab == "lyrics":
                        self.render_lyrics()
            self._on_ui(_publish_lyrics)
        threading.Thread(target=_fetch_lyr_bg, daemon=True).start()

        # Attach cached artwork immediately if available
        try:
            cached_art = get_cached_artwork(track)
        except Exception:
            logger.exception("Failed to get cached artwork")
            cached_art = {}
        if cached_art and is_current():
            if not track.get("art_url") and cached_art.get("art_url"):
                track["art_url"] = cached_art["art_url"]
            if not track.get("artist_art_url") and cached_art.get("artist_art_url"):
                track["artist_art_url"] = cached_art["artist_art_url"]
            if not track.get("album_art_url") and cached_art.get("album_art_url"):
                track["album_art_url"] = cached_art["album_art_url"]

        def _fetch_art_bg():
            if not is_current():
                return
            try:
                art_dict = resolve_track_artwork(track)
            except Exception:
                logger.exception("Failed to resolve track artwork")
                art_dict = {}
            if art_dict and (art_dict.get("art_url") or art_dict.get("artist_art_url") or art_dict.get("album_art_url")):
                def _publish_art():
                    if not is_current():
                        return
                    if not track.get("art_url") and art_dict.get("art_url"):
                        track["art_url"] = art_dict["art_url"]
                    if not track.get("artist_art_url") and art_dict.get("artist_art_url"):
                        track["artist_art_url"] = art_dict["artist_art_url"]
                    if not track.get("album_art_url") and art_dict.get("album_art_url"):
                        track["album_art_url"] = art_dict["album_art_url"]
                    if self.mpris:
                        try:
                            dur_s = float(track.get("duration_ms") or 0) / 1000.0
                        except (ValueError, TypeError):
                            dur_s = 0.0
                        self.mpris.update_track(track, dur_s)
                self._on_ui(_publish_art)
        threading.Thread(target=_fetch_art_bg, daemon=True).start()

        cached = get_cached_track_path(t_id)
        if cached and not cached_audio_matches_duration(cached, track.get("duration_ms")):
            logger.warning("Ignoring mismatched cached recording for %s", t_id)
            cached = None
        if cached:
            if not is_current():
                return
            try:
                register_cached_track(t_id, track, cached)
            except Exception:
                pass
            self.call_from_thread(self._commit_playback, req_id, str(cached), track)
            return

        if not is_current():
            return

        self.notify_user(f"Connecting stream for '{title}'...")

        track_url = playback_direct_url(track)

        res = search_and_resolve_stream(
            title, artist, direct_url=track_url, expected_duration_ms=track.get("duration_ms")
        )
        if not is_current():
            return

        if not res or not res.get("stream_url"):
            self.notify_user(f"Could not find a playable matching recording for '{title}'.", force=True)
            self.call_from_thread(self._playback_failed, req_id, track)
            return

        if res and res.get("thumbnail") and not track.get("art_url"):
            track["art_url"] = res["thumbnail"]
            def _publish_res_art():
                if req_id == getattr(self, "_play_request_id", None) and self.mpris:
                    try:
                        dur_s = float(track.get("duration_ms") or 0) / 1000.0
                    except (ValueError, TypeError):
                        dur_s = 0.0
                    self.mpris.update_track(track, dur_s)
            self._on_ui(_publish_res_art)

        stream_url = res["stream_url"]
        committed = self.call_from_thread(self._commit_playback, req_id, stream_url, track)
        if not committed or getattr(self, "_closing", False) or req_id != getattr(self, "_play_request_id", None):
            return

        def on_cached(path):
            def _publish_cached_state():
                # Playlist/search rows derive their offline marker from the
                # cache filesystem. Refresh the currently visible source as
                # soon as background caching completes; switching to Offline
                # used to be the only thing that rebuilt these rows.
                self.notify_user(f"Saved '{title}' to offline library.")
                if self.active_tab == "offline":
                    self.render_tracks(list(load_offline_index().values()))
                elif self.active_tab == "playlist":
                    self.render_tracks(self.current_playlist_tracks)
                elif self.active_tab == "search":
                    self.render_tracks(self.search_results)
                elif self.active_tab == "liked":
                    self.render_tracks(self.current_liked_tracks)
            self._on_ui(_publish_cached_state)

        download_track_to_cache(t_id, title, artist, on_complete=on_cached, direct_url=track_url, track_meta=track)

SpotatoTUI = SpoffTUI

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--update", "-u", "update"):
        run_cli_update()
        return

    # On Linux, request kernel delivery of SIGHUP if the parent terminal/process dies,
    # preventing orphaned instances from surviving terminal window closure.
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        # PR_SET_PDEATHSIG = 1
        libc.prctl(1, signal.SIGHUP, 0, 0, 0)
    except Exception:
        pass

    app: Optional[SpoffTUI] = None

    # Saved so a signal exit can undo Textual's raw mode; os._exit skips the
    # driver's own teardown and would otherwise leave the shell unusable.
    try:
        import termios
        saved_tty = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        termios = None
        saved_tty = None

    def _restore_terminal():
        try:
            # Leave alt screen, show cursor, stop mouse tracking and bracketed paste,
            # pop the kitty keyboard protocol.
            sys.stdout.write(
                "\x1b[?1049l\x1b[?25h\x1b[?1000l\x1b[?1002l\x1b[?1003l"
                "\x1b[?1006l\x1b[?1015l\x1b[?2004l\x1b[<u"
            )
            sys.stdout.flush()
        except Exception:
            pass
        if termios is not None and saved_tty is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved_tty)
            except Exception:
                pass

    def _signal_handler(sig, frame):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass

        def _hard_exit():
            time.sleep(1.0)
            os._exit(128 + sig)
        threading.Thread(target=_hard_exit, daemon=True).start()

        if app is not None:
            try:
                app._cleanup_on_exit()
            except Exception:
                pass
        if sig != signal.SIGHUP:
            _restore_terminal()
        os._exit(0 if sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT) else (128 + sig))

    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
        try:
            signal.signal(sig, _signal_handler)
        except Exception:
            pass

    def _parent_watchdog():
        initial_ppid = os.getppid()
        while True:
            time.sleep(1.5)
            curr_ppid = os.getppid()
            if curr_ppid == 1 and initial_ppid != 1:
                if app is not None:
                    try:
                        app._cleanup_on_exit()
                    except Exception:
                        pass
                os._exit(0)

    watchdog_thread = threading.Thread(target=_parent_watchdog, daemon=True)
    watchdog_thread.start()

    vis_arg: Optional[bool] = None
    if any(arg in sys.argv for arg in ("--no-visualizer", "--no-vis", "--no-cava", "--disable-visualizer")):
        vis_arg = False
    elif any(arg in sys.argv for arg in ("--visualizer", "--vis", "--cava", "--enable-visualizer")):
        vis_arg = True

    notif_arg: Optional[bool] = None
    if any(arg in sys.argv for arg in ("--no-notifs", "--no-notif", "--not-notifs", "--no-notifications", "--disable-notifications", "--notifications=off")):
        notif_arg = False
    elif any(arg in sys.argv for arg in ("--notifs", "--notif", "--notifications", "--enable-notifications", "--notifications=on")):
        notif_arg = True

    exit_code = 0
    try:
        app = SpoffTUI(visualizer_enabled=vis_arg, notifications_enabled=notif_arg)
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.critical(f"Fatal application error: {e}", exc_info=True)
        exit_code = 1
        raise
    finally:
        try:
            if app:
                app._cleanup_on_exit()
        except Exception:
            pass
        if exit_code != 0:
            sys.exit(exit_code)

if __name__ == "__main__":
    main()
