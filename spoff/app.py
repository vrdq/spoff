import sys
import os
import signal

if "TEXTUAL_FPS" not in os.environ:
    os.environ["TEXTUAL_FPS"] = "60"

import time
import logging
import threading
import atexit
import secrets
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple
import random
from pathlib import Path

from rich.markup import escape
from rich.table import Table
from textual import events, work
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static, Input, DataTable, ProgressBar, Button
from textual.coordinate import Coordinate
from textual.binding import Binding

try:
    from .spotify import fetch_spotify_playlist, fetch_spotify_album, parse_spotify_url
    from .storage import (
        load_saved_playlists, save_saved_playlists, add_saved_playlist, remove_saved_playlist,
        create_local_playlist, add_track_to_playlist, remove_track_from_playlist,
        update_playlist_tracks, get_cached_track_path, load_offline_index,
        delete_cached_track, CACHE_DIR, LOG_FILE, is_first_launch, mark_first_launch_done,
        get_saved_volume, save_volume, get_saved_sidebar_width, save_sidebar_width,
        get_saved_advanced_mode, save_advanced_mode, get_saved_search_engine, save_search_engine,
        get_saved_transparency, save_transparency, get_custom_keybindings,
        save_custom_keybindings, reset_custom_keybindings
    )
    from .streamer import search_and_resolve_stream, download_track_to_cache
    from .search import live_search_tracks
    from .player import MPVController
    from .auth import (
        load_spotify_auth, save_spotify_auth, logout_spotify, get_valid_token,
        generate_pkce_pair, build_auth_url, exchange_code_for_tokens,
        fetch_current_user_profile, sync_spotify_library, OAuthCallbackServer,
        SPOTIFY_PORT, add_track_to_spotify_account, remove_track_from_spotify_account,
        reorder_spotify_playlist_track, delete_spotify_playlist, has_modify_scopes,
        search_spotify_tracks
    )
    from .lyrics import fetch_lyrics, get_active_lyric_index
    from .mpris import MPRISService
    from .visualizer import VisualizerWidget, CavaVisualizer
    from .updater import check_for_updates, perform_update, run_cli_update
except ImportError:
    from spotify import fetch_spotify_playlist, fetch_spotify_album, parse_spotify_url
    from storage import (
        load_saved_playlists, save_saved_playlists, add_saved_playlist, remove_saved_playlist,
        create_local_playlist, add_track_to_playlist, remove_track_from_playlist,
        update_playlist_tracks, get_cached_track_path, load_offline_index,
        delete_cached_track, CACHE_DIR, LOG_FILE, is_first_launch, mark_first_launch_done,
        get_saved_volume, save_volume, get_saved_sidebar_width, save_sidebar_width,
        get_saved_advanced_mode, save_advanced_mode, get_saved_search_engine, save_search_engine,
        get_saved_transparency, save_transparency, get_custom_keybindings,
        save_custom_keybindings, reset_custom_keybindings
    )
    from streamer import search_and_resolve_stream, download_track_to_cache
    from search import live_search_tracks
    from player import MPVController
    from auth import (
        load_spotify_auth, save_spotify_auth, logout_spotify, get_valid_token,
        generate_pkce_pair, build_auth_url, exchange_code_for_tokens,
        fetch_current_user_profile, sync_spotify_library, OAuthCallbackServer,
        SPOTIFY_PORT, add_track_to_spotify_account, remove_track_from_spotify_account,
        reorder_spotify_playlist_track, delete_spotify_playlist, has_modify_scopes,
        search_spotify_tracks
    )
    from lyrics import fetch_lyrics, get_active_lyric_index
    from mpris import MPRISService
    from visualizer import VisualizerWidget, CavaVisualizer
    from updater import check_for_updates, perform_update, run_cli_update

logger = logging.getLogger("spoff")

def format_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"

DEFAULT_KEYBINDINGS: Dict[str, str] = {
    "toggle_play": "space",
    "next_track": "n",
    "prev_track": "p",
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
    "focus_bar": "b",
    "focus_import": "i",
    "add_to_playlist": "a",
    "delete_item": "d",
    "delete_playlist": "D",
    "open_spotify_auth": "L",
    "nav_search": "1",
    "nav_playlist": "2",
    "nav_offline": "3",
    "nav_lyrics": "4",
    "open_settings": "comma",
    "show_help": "colon",
    "check_update": "u",
    "quit_app": "q",
    "focus_sidebar": "h",
    "focus_tracks": "l",
    "toggle_focus": "tab",
    "move_item_up": "K",
    "move_item_down": "J",
    "switch_engine": "ctrl+e",
}

ACTION_INFO: Dict[str, Tuple[str, str]] = {
    "toggle_play": ("Playback", "Play / Pause Toggle"),
    "next_track": ("Playback", "Next Track"),
    "prev_track": ("Playback", "Previous Track"),
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
    "focus_bar": ("Playback", "Focus Seek Bar"),
    "focus_import": ("Playlists", "New Playlist / Import"),
    "add_to_playlist": ("Playlists", "Add Song to Playlist"),
    "delete_item": ("Playlists", "Delete Selected Item"),
    "delete_playlist": ("Playlists", "Delete Entire Playlist"),
    "open_spotify_auth": ("Integrations", "Spotify Menu & Login"),
    "nav_search": ("Navigation", "Switch to Search"),
    "nav_playlist": ("Navigation", "Switch to Playlists"),
    "nav_offline": ("Navigation", "Switch to Offline"),
    "nav_lyrics": ("Navigation", "Synchronized Lyrics"),
    "switch_engine": ("Navigation", "Switch Search Engine (YTM/Spotify)"),
    "open_settings": ("General", "Settings & Keybinds"),
    "show_help": ("General", "Help & Reference"),
    "check_update": ("General", "Check for Updates"),
    "quit_app": ("General", "Quit Spoff"),
    "focus_sidebar": ("Navigation", "Focus Sidebar (h)"),
    "focus_tracks": ("Navigation", "Focus Main Table (l)"),
    "toggle_focus": ("Navigation", "Cycle Sidebar / Main"),
    "move_item_up": ("Playlists", "Reorder Song Up (K)"),
    "move_item_down": ("Playlists", "Reorder Song Down (J)"),
}

def format_key_display(k: str) -> str:
    if not k:
        return "[dim]None[/dim]"
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
        "delete": "Delete",
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
            res.append(p)
        elif lower.startswith("f") and lower[1:].isdigit():
            res.append(lower.upper())
        else:
            res.append(p.capitalize())
    return "+".join(res)

def normalize_captured_key(event_key: str, event_char: Optional[str]) -> str:
    named_keys = {
        "space", "enter", "tab", "escape", "backspace", "delete",
        "up", "down", "left", "right",
        "home", "end", "pageup", "pagedown",
    }
    ek_lower = str(event_key or "").lower()
    if ek_lower in named_keys:
        return ek_lower
    if ek_lower.startswith("f") and ek_lower[1:].isdigit():
        return ek_lower

    ec = str(event_char or "")
    if ec and len(ec) == 1:
        if ec in (",", "/", ":", ";", "?", "+", "-"):
            return ec
        if ec.isupper():
            return ec
        return ec
    return str(event_key or "")

def key_matches(event_key: str, event_char: Optional[str], bound_key: str) -> bool:
    if not bound_key:
        return False
    bound = bound_key.strip()
    ek = str(event_key or "").strip()
    ec = str(event_char or "")

    if ek == bound:
        return True

    punct_map = {
        "comma": ",",
        ",": "comma",
        "slash": "/",
        "/": "slash",
        "colon": ":",
        ":": "colon",
        "semicolon": ";",
        ";": "semicolon",
        "question_mark": "?",
        "?": "question_mark",
        "plus": "+",
        "+": "plus",
        "minus": "-",
        "-": "minus",
    }
    if bound in punct_map:
        target = punct_map[bound]
        if ek == target or ec == target or ec == bound:
            return True
    if ek == "colon" and bound in (":", "colon"):
        return True
    if ek == "shift+semicolon" and bound in (":", "colon"):
        return True

    if bound in ("space", " ") and (ek == "space" or ec == " "):
        return True

    if len(bound) == 1 and bound.isupper():
        if ec == bound or ek == f"shift+{bound.lower()}" or ek == bound:
            return True
    elif len(bound) == 1 and bound.islower():
        if (ec == bound or ek == bound) and (not ec or not ec.isupper()):
            return True

    return False

class AdvModeToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_advanced_mode()

class TransparencyToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_transparency()

class SearchEngineToggle(Static):
    can_focus = True

    def on_click(self) -> None:
        if isinstance(self.screen, SettingsModal):
            self.screen.toggle_search_engine()

class SettingsModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_or_cancel", "Close", priority=True),
        Binding("q", "dismiss_or_cancel", "Close", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "select_or_toggle", "Select", show=False),
        Binding("space", "select_or_toggle", "Toggle", show=False),
        Binding("backspace", "reset_selected_key", "Reset Key", show=False),
        Binding("r", "reset_selected_key", "Reset Key", show=False),
        Binding("R", "reset_all_keys", "Reset All", show=False),
        Binding("tab", "switch_focus", "Switch Focus", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.is_rebinding: bool = False
        self.rebinding_action: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            with Horizontal(id="settings-header"):
                yield Static("SETTINGS & KEYBINDS", id="settings-title")
                yield Static("[dim]Esc / q to close[/dim]", id="settings-close-hint")

            with Vertical(id="settings-options-container"):
                yield AdvModeToggle(id="adv-mode-toggle", classes="setting-toggle-item")
                yield TransparencyToggle(id="transparency-toggle", classes="setting-toggle-item")
                yield SearchEngineToggle(id="engine-toggle", classes="setting-toggle-item")

            yield Static("REBINDABLE ACTIONS", id="settings-table-title")
            yield DataTable(id="settings-table", cursor_type="row", show_header=True)
            yield Static("", id="settings-status-line")
            yield Static("[dim]Enter: rebind  |  Backspace / r: reset key  |  R: reset all  |  j/k: navigate[/dim]", id="settings-footer")

    def on_mount(self) -> None:
        self.update_toggle_ui()
        table = self.query_one("#settings-table", DataTable)
        table.cursor_foreground_priority = "renderable"
        table.add_column("Category", key="cat", width=14)
        table.add_column("Action", key="act", width=30)
        table.add_column("Keybind", key="key", width=18)
        table.add_column("Status", key="stat", width=12)

        for act_id in ACTION_INFO.keys():
            cat, title = ACTION_INFO[act_id]
            cur_key = self.app.keybindings.get(act_id, "")
            is_default = (cur_key == DEFAULT_KEYBINDINGS.get(act_id))
            status_str = "[dim]Default[/dim]" if is_default else "[bold #569f68]Custom[/]"
            table.add_row(cat, title, format_key_display(cur_key), status_str, key=act_id)

        table.focus()

    def update_toggle_ui(self) -> None:
        try:
            adv_toggle = self.query_one("#adv-mode-toggle", AdvModeToggle)
            if getattr(self.app, "advanced_mode", False):
                adv_toggle.update("[bold #569f68]● ENABLED[/]   [#ffffff]Advanced Mode[/]  [dim]— Keybind strings & HUD hints hidden[/dim]")
                self.query_one("#settings-footer", Static).update("")
                self.query_one("#settings-close-hint", Static).update("")
            else:
                adv_toggle.update("[#767676]○ DISABLED[/]  [#cccccc]Advanced Mode[/]  [dim]— Press Space/Enter to hide keybind indicators[/dim]")
                self.query_one("#settings-footer", Static).update("[dim]Enter: rebind  |  Backspace / r: reset key  |  R: reset all  |  j/k: navigate[/dim]")
                self.query_one("#settings-close-hint", Static).update("[dim]Esc / q to close[/dim]")

            trans_toggle = self.query_one("#transparency-toggle", TransparencyToggle)
            if getattr(self.app, "transparency", True):
                trans_toggle.update("[bold #569f68]● ENABLED[/]   [#ffffff]UI Transparency[/]  [dim]— Terminal background & blur shines through[/dim]")
            else:
                trans_toggle.update("[#767676]○ DISABLED[/]  [#cccccc]UI Transparency[/]  [dim]— Solid dark opaque background[/dim]")

            eng_toggle = self.query_one("#engine-toggle", SearchEngineToggle)
            if getattr(self.app, "search_engine", "ytmusic") == "spotify":
                eng_toggle.update("[bold #569f68]● SPOTIFY[/]   [#ffffff]Search Engine[/]  [dim]— Official Spotify catalogue (syncs with Spotify)[/dim]")
            else:
                eng_toggle.update("[bold #ffffff]● YT MUSIC[/]  [#cccccc]Search Engine[/]  [dim]— YouTube Music streams (local playlists only)[/dim]")
        except Exception:
            pass

    def toggle_advanced_mode(self) -> None:
        new_state = self.app.toggle_advanced_mode()
        self.update_toggle_ui()
        state_text = "[bold #569f68]Enabled[/]" if new_state else "[dim]Disabled[/]"
        self.query_one("#settings-status-line", Static).update(f"Advanced Mode {state_text}.")

    def toggle_transparency(self) -> None:
        new_state = self.app.toggle_transparency()
        self.update_toggle_ui()
        state_text = "[bold #569f68]Enabled[/]" if new_state else "[dim]Disabled[/]"
        self.query_one("#settings-status-line", Static).update(f"UI Transparency {state_text}.")

    def toggle_search_engine(self) -> None:
        new_engine = self.app.toggle_search_engine()
        self.update_toggle_ui()
        label = "Spotify" if new_engine == "spotify" else "YouTube Music"
        self.query_one("#settings-status-line", Static).update(f"Search engine set to {label}.")

    def start_rebinding(self, act_id: str) -> None:
        self.is_rebinding = True
        self.rebinding_action = act_id
        _, act_title = ACTION_INFO.get(act_id, ("General", act_id))
        self.query_one("#settings-status-line", Static).update(
            f"[bold #569f68]► Press any key to bind to '{act_title}' (Esc to cancel)...[/]"
        )
        self._refresh_row(act_id, key_override="[bold #569f68]PRESS KEY...[/]")

    def apply_rebound_key(self, new_key: str) -> None:
        act_id = self.rebinding_action
        self.is_rebinding = False
        self.rebinding_action = None

        if not act_id:
            return

        _, act_title = ACTION_INFO.get(act_id, ("General", act_id))

        conflicting_act = None
        for other_id, bound in self.app.keybindings.items():
            if other_id != act_id and bound == new_key:
                conflicting_act = other_id
                break

        status_msg = f"Bound [bold #ffffff]'{act_title}'[/] to [bold #569f68]{format_key_display(new_key)}[/]."
        if conflicting_act:
            _, conf_title = ACTION_INFO.get(conflicting_act, ("General", conflicting_act))
            self.app.set_custom_keybinding(conflicting_act, "")
            status_msg += f" [dim](Unbound conflicting '{conf_title}')[/dim]"
            self._refresh_row(conflicting_act)

        self.app.set_custom_keybinding(act_id, new_key)
        self._refresh_row(act_id)
        self.query_one("#settings-status-line", Static).update(status_msg)

    def cancel_rebinding(self) -> None:
        act_id = self.rebinding_action
        self.is_rebinding = False
        self.rebinding_action = None
        if act_id:
            self._refresh_row(act_id)
        self.query_one("#settings-status-line", Static).update("[dim]Rebinding cancelled.[/dim]")

    def _refresh_row(self, act_id: str, key_override: Optional[str] = None) -> None:
        table = self.query_one("#settings-table", DataTable)
        cat, title = ACTION_INFO.get(act_id, ("General", act_id))
        cur_key = self.app.keybindings.get(act_id, "")
        is_default = (cur_key == DEFAULT_KEYBINDINGS.get(act_id))

        if key_override:
            disp_key = key_override
            disp_status = "[bold #569f68]Capturing[/]"
        else:
            disp_key = format_key_display(cur_key)
            disp_status = "[dim]Default[/dim]" if is_default else "[bold #569f68]Custom[/]"

        try:
            table.update_cell(act_id, "cat", cat)
            table.update_cell(act_id, "act", title)
            table.update_cell(act_id, "key", disp_key)
            table.update_cell(act_id, "stat", disp_status)
        except Exception:
            pass

    def action_reset_selected_key(self) -> None:
        if self.is_rebinding:
            return
        table = self.query_one("#settings-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            act_id = list(ACTION_INFO.keys())[table.cursor_row]
            self.app.reset_keybinding(act_id)
            self._refresh_row(act_id)
            _, title = ACTION_INFO.get(act_id, ("General", act_id))
            def_key = DEFAULT_KEYBINDINGS.get(act_id, "")
            self.query_one("#settings-status-line", Static).update(
                f"Reset '{title}' to default ({format_key_display(def_key)})."
            )

    def action_reset_all_keys(self) -> None:
        if self.is_rebinding:
            return
        self.app.reset_all_keybindings()
        for act_id in ACTION_INFO.keys():
            self._refresh_row(act_id)
        self.query_one("#settings-status-line", Static).update("All keybindings reset to factory defaults.")

    def action_dismiss_or_cancel(self) -> None:
        if self.is_rebinding:
            self.cancel_rebinding()
        else:
            self.dismiss(None)

    def action_switch_focus(self) -> None:
        if self.is_rebinding:
            return
        toggle_ids = ["adv-mode-toggle", "transparency-toggle", "engine-toggle"]
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
        if self.is_rebinding:
            return
        focused_id = self.focused.id if self.focused else None
        if focused_id == "adv-mode-toggle":
            self.toggle_advanced_mode()
        elif focused_id == "transparency-toggle":
            self.toggle_transparency()
        elif focused_id == "engine-toggle":
            self.toggle_search_engine()
        elif self.focused and self.focused.id == "settings-table":
            table = self.query_one("#settings-table", DataTable)
            if table.cursor_row is not None and table.row_count > 0:
                act_id = list(ACTION_INFO.keys())[table.cursor_row]
                self.start_rebinding(act_id)

    def action_cursor_down(self) -> None:
        table = self.query_one("#settings-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one("#settings-table", DataTable)
        if table.row_count == 0 or table.cursor_row == 0:
            self.query_one("#engine-toggle", SearchEngineToggle).focus()
        else:
            table.action_cursor_up()

    def on_key(self, event: events.Key) -> None:
        if self.is_rebinding:
            event.prevent_default()
            event.stop()
            if event.key in ("escape",):
                self.cancel_rebinding()
            else:
                new_key = normalize_captured_key(event.key, event.character)
                self.apply_rebound_key(new_key)
            return

        table = self.query_one("#settings-table", DataTable)
        toggle_ids = ["adv-mode-toggle", "transparency-toggle", "engine-toggle"]
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
            elif event.key in ("enter", "space") or event.character in (" ",):
                if focused_id == "adv-mode-toggle":
                    self.toggle_advanced_mode()
                elif focused_id == "transparency-toggle":
                    self.toggle_transparency()
                elif focused_id == "engine-toggle":
                    self.toggle_search_engine()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("escape", "q"):
                self.dismiss(None)
                event.prevent_default()
                event.stop()
                return
        elif self.focused and self.focused.id == "settings-table":
            if event.key in ("j", "down") or event.character == "j":
                table.action_cursor_down()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("k", "up") or event.character == "k":
                if table.row_count == 0 or table.cursor_row == 0:
                    self.query_one("#engine-toggle", SearchEngineToggle).focus()
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
        if not self.is_rebinding:
            act_id = str(event.row_key.value)
            self.start_rebinding(act_id)

class AddToPlaylistModal(ModalScreen[Optional[Tuple[str, str]]]):
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
        if self.playlists:
            for p in self.playlists:
                p_name = p.get("name", "Untitled")
                p_id = p.get("id", "")
                tracks_count = len(p.get("tracks", []))
                is_spotify = p_id == "spotify_liked_songs" or (len(p_id) == 22 and p_id.isalnum()) or bool(p.get("spotify_id"))
                tag = " [#569f68][Spotify][/]" if is_spotify else ""
                table.add_row(f"{p_name}{tag}  [dim]({tracks_count} tracks)[/dim]")
            table.focus()
        else:
            table.display = False
            self.query_one("#modal-subtitle", Static).update("[dim]No existing playlists yet — type a name above to create one[/dim]")
            self.query_one("#modal-input", Input).focus()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_switch_focus(self) -> None:
        if self.focused and self.focused.id == "modal-input":
            if self.playlists:
                self.query_one("#modal-table", DataTable).focus()
        else:
            self.query_one("#modal-input", Input).focus()

    def action_focus_input(self) -> None:
        self.query_one("#modal-input", Input).focus()

    def action_cursor_down_input(self) -> None:
        if self.focused and self.focused.id == "modal-input":
            if self.playlists:
                self.query_one("#modal-table", DataTable).focus()
        elif self.focused and self.focused.id == "modal-table":
            self.query_one("#modal-table", DataTable).action_cursor_down()

    def action_cursor_down_table(self) -> None:
        if self.focused and self.focused.id == "modal-table":
            self.query_one("#modal-table", DataTable).action_cursor_down()

    def action_cursor_up_table(self) -> None:
        if self.focused and self.focused.id == "modal-table":
            table = self.query_one("#modal-table", DataTable)
            if table.row_count == 0 or table.cursor_row == 0:
                self.query_one("#modal-input", Input).focus()
            else:
                table.action_cursor_up()

    def on_key(self, event: events.Key) -> None:
        table = self.query_one("#modal-table", DataTable)
        inp = self.query_one("#modal-input", Input)

        if self.focused and self.focused.id == "modal-table":
            if event.key in ("j", "down") or event.character == "j":
                table.action_cursor_down()
                event.prevent_default()
                event.stop()
            elif event.key in ("k", "up") or event.character == "k":
                if table.row_count == 0 or table.cursor_row == 0:
                    inp.focus()
                else:
                    table.action_cursor_up()
                event.prevent_default()
                event.stop()
            elif (event.key in ("G", "shift+g") or event.character == "G") and table.row_count > 0:
                table.move_cursor(row=table.row_count - 1)
                event.prevent_default()
                event.stop()
            elif event.key in ("home",) and table.row_count > 0:
                table.move_cursor(row=0)
                event.prevent_default()
                event.stop()
            elif event.key in ("i", "a") and event.character in ("i", "a"):
                inp.focus()
                event.prevent_default()
                event.stop()
            elif event.key in ("escape", "q"):
                self.dismiss(None)
                event.prevent_default()
                event.stop()
        elif self.focused and self.focused.id == "modal-input":
            if event.key in ("down", "tab"):
                if self.playlists:
                    table.focus()
                    event.prevent_default()
                    event.stop()
            elif event.key == "escape":
                if inp.value:
                    inp.value = ""
                    event.prevent_default()
                    event.stop()
                elif self.playlists:
                    table.focus()
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
            if 0 <= idx < len(self.playlists):
                self.dismiss(("select", self.playlists[idx]["id"]))

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.data_table.id == "modal-table":
            idx = event.coordinate.row
            if 0 <= idx < len(self.playlists):
                self.dismiss(("select", self.playlists[idx]["id"]))

class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", "No", show=False),
        Binding("enter", "confirm", "Confirm"),
        Binding("y", "confirm", "Yes", show=False),
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

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

class SpotifyAuthModal(ModalScreen[Optional[str]]):
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

    def compose(self) -> ComposeResult:
        with Vertical(id="spotify-dialog"):
            if self.first_run and not (self.auth_session and get_valid_token()):
                yield Static("WELCOME TO SPOFF · SPOTIFY SETUP", id="spotify-title")
            else:
                yield Static("SPOTIFY ACCOUNT", id="spotify-title")
            if self.auth_session and get_valid_token():
                user = self.auth_session.get("user", {})
                name = user.get("display_name") or user.get("id") or "Spotify User"
                email = user.get("email") or ""
                plan = user.get("product", "free").capitalize()
                u_id = user.get("id") or ""

                user_line = f"User: [bold #ffffff]{escape(str(name))}[/]"
                if email:
                    user_line += f"  [#767676]({escape(str(email))})[/]"
                elif u_id and u_id != name:
                    user_line += f"  [#767676](@{escape(str(u_id))})[/]"

                yield Static(user_line, id="spotify-user-info")
                yield Static(f"Plan: [bold #569f68]Spotify {escape(str(plan))}[/]", id="spotify-desc")

                can_modify = has_modify_scopes()
                if can_modify:
                    yield Static("[bold #569f68]● Two-way synchronization active[/]  [dim](changes sync to your Spotify account)[/dim]", id="spotify-status")
                else:
                    yield Static("[bold #c4a768]▲ Permissions update available[/]  [dim](re-link once to enable two-way sync)[/dim]", id="spotify-status")

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
                yield Static("[dim]Status: Not connected[/dim]", id="spotify-status")

                with Horizontal(id="spotify-actions"):
                    yield Button("Browser Login" if is_adv else r"\[Enter] Browser Login", variant="primary", id="btn-login")
                    if is_adv:
                        close_label = "Skip" if self.first_run else "Cancel"
                    else:
                        close_label = r"\[Esc] Skip" if self.first_run else r"\[Esc] Cancel"
                    yield Button(close_label, id="btn-close")

                yield Static("", id="spotify-instruction")
                yield Static("", id="spotify-hint")

    def on_mount(self) -> None:
        if self.auth_session and get_valid_token():
            try:
                self.query_one("#btn-sync", Button).focus()
            except Exception:
                pass
            # Auto-refresh user profile in background if missing
            user = self.auth_session.get("user", {})
            if not user or not user.get("display_name"):
                def _fetch_bg():
                    tok = get_valid_token()
                    if tok:
                        prof = fetch_current_user_profile(tok)
                        if prof:
                            self.auth_session["user"] = prof
                            save_spotify_auth(self.auth_session)
                            name = prof.get("display_name") or prof.get("id") or "Spotify User"
                            email = prof.get("email") or ""
                            line = f"User: [bold #ffffff]{escape(str(name))}[/]"
                            if email:
                                line += f"  [#767676]({escape(str(email))})[/]"
                            def _update():
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
        if self.server:
            self.server.stop()
            self.server = None
        self.dismiss(None)

    def action_sync_library(self) -> None:
        if self.auth_session and get_valid_token():
            self.dismiss("sync_now")

    def action_relink_account(self) -> None:
        self.start_browser_login()

    def action_logout_account(self) -> None:
        if self.auth_session:
            logout_spotify()
            self.auth_session = None
            self.dismiss("logged_out")

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
        elif event.key == "enter" and not isinstance(self.focused, Button):
            if self.auth_session and get_valid_token():
                self.action_sync_library()
            else:
                self.start_browser_login()
            event.prevent_default()
            event.stop()

    def action_focus_prev_button(self) -> None:
        buttons = [b for b in self.query(Button) if b.display]
        if not buttons:
            return
        if self.focused in buttons:
            idx = buttons.index(self.focused)
            prev_idx = (idx - 1) % len(buttons)
            buttons[prev_idx].focus()
        else:
            buttons[-1].focus()

    def action_focus_next_button(self) -> None:
        buttons = [b for b in self.query(Button) if b.display]
        if not buttons:
            return
        if self.focused in buttons:
            idx = buttons.index(self.focused)
            next_idx = (idx + 1) % len(buttons)
            buttons[next_idx].focus()
        else:
            buttons[0].focus()

    def start_browser_login(self) -> None:
        if self.is_logging_in:
            return
        self.is_logging_in = True
        self.pkce_verifier, challenge = generate_pkce_pair()
        auth_url, state = build_auth_url(self.pkce_verifier)

        try:
            self.query_one("#spotify-status", Static).update("[bold #c4a768]Waiting for authorization in browser...[/]")
            inst = self.query_one("#spotify-instruction", Static)
            inst.update(
                f"[dim]If your browser did not open, visit:[/dim]\n[#569f68]{auth_url}[/]"
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
                        self.query_one("#spotify-status", Static).update(f"[bold #c47676]Login failed: {err}[/]")
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
            self.server.start(_on_callback)
        except Exception as e:
            logger.error(f"Failed to start OAuth server: {e}")
            try:
                self.query_one("#spotify-status", Static).update(f"[bold #c47676]Could not bind port {SPOTIFY_PORT}: {e}[/]")
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
                save_spotify_auth(tokens)
                def _finish():
                    if self.server:
                        self.server.stop()
                        self.server = None
                    self.dismiss("login_success")
                self.app.call_from_thread(_finish)
            else:
                def _fail():
                    try:
                        self.query_one("#spotify-status", Static).update("[bold #c47676]Token exchange failed. Please try again.[/]")
                    except Exception:
                        pass
                    self.is_logging_in = False
                self.app.call_from_thread(_fail)

        threading.Thread(target=_worker, daemon=True).start()

class UpdateModal(ModalScreen[bool]):
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
        if self.is_updating:
            return
        self.is_updating = True
        try:
            self.query_one("#update-status", Static).update("[bold #c4a768]Pulling latest changes from GitHub...[/]")
        except Exception:
            pass

        def _worker():
            ok, msg = perform_update()
            def _done():
                if ok:
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
                self.is_updating = False
            self.app.call_from_thread(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def action_cancel(self) -> None:
        self.dismiss(False)

class HelpModal(ModalScreen[None]):
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
        def make_sec_table(rows: List[Tuple[str, str]]) -> Table:
            t = Table.grid(padding=(0, 2))
            t.add_column(style="bold #ffffff", width=18, no_wrap=True)
            t.add_column(style="#b0b0b0", no_wrap=True)
            for k, d in rows:
                t.add_row(k, d)
            return t

        nav_rows = [
            ("1 / 2 / 3", "Search / Playlists / Offline"),
            ("4", "Synchronized lyrics view"),
            ("h / l", "Switch Sidebar / Main pane"),
            ("j / k, Arrows", "Navigate table rows"),
            ("gg / Home", "Jump to top row"),
            ("G / End", "Jump to bottom row"),
            ("Ctrl+d / Ctrl+u", "Scroll page down / up"),
            ("Tab", "Cycle Sidebar / Table"),
            ("Enter", "Play track / Open playlist"),
            ("k (at top row)", "Jump up into input box"),
            ("Esc", "Unfocus / Back to playlist"),
        ]

        playback_rows = [
            ("Space, Fn+F8", "Play / Pause toggle"),
            ("s", "Toggle shuffle mode"),
            ("r", "Cycle repeat (off / all / 1)"),
            ("p / n, Fn+F7/F9", "Previous / Next track"),
            ("Left / Right", "Seek -/+ 5 seconds"),
            ("F1", "Mute / Unmute audio"),
            ("F2 / F3", "Volume down / up 5%"),
        ]

        seek_rows = [
            ("b", "Toggle seek bar focus"),
            ("h / l", "Seek -/+ 5s on bar"),
            ("H / L", "Fast seek -/+ 15s on bar"),
            ("0 – 9", "Jump to 0% – 90% of song"),
            ("Enter / Click", "Jump to lyric timestamp"),
            ("b / Esc / k", "Return to table"),
        ]

        playlist_rows = [
            ("J / K, Shift+↑↓", "Reorder songs in playlist"),
            ("a, +", "Add track to playlist"),
            ("i", "New playlist / import link"),
            ("Shift+L, S", "Spotify login & sync"),
            (",", "Settings & Rebind keys"),
            ("/", "Focus search box"),
            ("Del, d, x", "Remove track / playlist"),
            ("D, Shift+Del", "Delete whole playlist"),
            ("u / U", "Check / pull updates"),
            ("q", "Quit Spoff"),
        ]

        with Vertical(id="help-dialog"):
            with Horizontal(id="help-header-bar"):
                yield Static("KEYBINDINGS & USAGE GUIDE", id="help-title")
                h_close = "" if getattr(self.app, "advanced_mode", False) else "[dim]Esc / Enter / : to close[/dim]"
                yield Static(h_close, id="help-close-hint")

            with Horizontal(id="help-body"):
                with Vertical(classes="help-col"):
                    yield Static("[bold #569f68]NAVIGATION & VIEWS[/]", classes="help-sec-title")
                    yield Static(make_sec_table(nav_rows), classes="help-sec-table")
                    yield Static("[bold #569f68]PLAYBACK CONTROLS[/]", classes="help-sec-title")
                    yield Static(make_sec_table(playback_rows), classes="help-sec-table")

                with Vertical(id="help-col-sep"):
                    pass

                with Vertical(classes="help-col"):
                    yield Static("[bold #569f68]SEEK & TIMESTAMPS[/]", classes="help-sec-title")
                    yield Static(make_sec_table(seek_rows), classes="help-sec-table")
                    yield Static("[bold #569f68]PLAYLISTS & LIBRARY[/]", classes="help-sec-title")
                    yield Static(make_sec_table(playlist_rows), classes="help-sec-table")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

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
        self.app.action_seek_bwd()

    def action_scrub_fwd(self) -> None:
        self.app.action_seek_fwd()

    def action_scrub_bwd_fast(self) -> None:
        self.app.player.seek(-15)
        self.app.update_player_hud()

    def action_scrub_fwd_fast(self) -> None:
        self.app.player.seek(15)
        self.app.update_player_hud()

    def action_return_to_table(self) -> None:
        if self.app.active_tab == "lyrics":
            self.app.query_one("#lyrics-table", DataTable).focus()
        else:
            self.app.query_one("#track-table", DataTable).focus()

    def action_toggle_play(self) -> None:
        self.app.action_toggle_play()

    def on_focus(self) -> None:
        self.app.update_player_hud()

    def on_blur(self) -> None:
        self.app.update_player_hud()

    def on_key(self, event: events.Key) -> None:
        if event.key in "0123456789":
            pct = int(event.key) / 10.0
            self.app.seek_to_percent(pct)
            event.prevent_default()
            event.stop()

    def on_click(self, event: events.Click) -> None:
        self.focus()
        if self.total and self.total > 0 and self.size.width > 0:
            pct = max(0.0, min(1.0, event.x / float(self.size.width)))
            self.app.seek_to_percent(pct)
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
            self.app.notify_user("Playlists sidebar width reset to default (44).")
            event.prevent_default()
            event.stop()

class SearchEnginePill(Static):
    can_focus = False

    def on_click(self) -> None:
        if hasattr(self.app, "toggle_search_engine"):
            self.app.toggle_search_engine()

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
        padding: 0 2;
        align: left middle;
    }

    #nav-bar {
        width: 1fr;
        color: #555555;
    }

    #status-pill {
        width: auto;
        text-style: bold;
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
    }

    DataTable > .datatable--cursor {
        background: #252525;
    }

    DataTable:focus > .datatable--cursor {
        background: #2e2e2e;
    }

    #side-table > .datatable--cursor {
        background: #252525;
    }

    #side-table:focus > .datatable--cursor {
        background: #2e2e2e;
    }

    DataTable > .datatable--hover {
        background: #1f1f1f;
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
        height: 1;
        color: #767676;
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
        border: solid #569f68;
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
        background: #252525;
    }

    #lyrics-table:focus > .datatable--cursor {
        background: #2e2e2e;
    }

    #shuf-pill, #rep-pill {
        width: auto;
        margin-right: 2;
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
        background: #2e2e2e;
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

    #notification-line {
        height: 1;
        color: #888888;
        text-style: italic;
    }

    #deck-line-1 {
        height: 1;
    }

    #deck-track {
        text-style: bold;
        width: 1fr;
        color: #ffffff;
    }

    #deck-source {
        width: auto;
        text-style: bold;
        margin-right: 2;
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
        width: 110;
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
        color: #569f68;
    }

    .help-sec-table {
        margin-bottom: 1;
    }

    /* MODAL: SPOTIFY AUTH */
    SpotifyAuthModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #spotify-dialog {
        width: 80;
        height: auto;
        background: #181818;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #spotify-title {
        text-style: bold;
        color: #ffffff;
        margin-bottom: 1;
    }

    #spotify-user-info {
        color: #e2e2e2;
        margin-bottom: 0;
    }

    #spotify-desc {
        color: #cccccc;
        margin-bottom: 1;
    }

    #spotify-status {
        color: #c4a768;
        margin-bottom: 1;
    }

    #spotify-instruction {
        display: none;
        color: #888888;
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
        border: tall #569f68;
        color: #ffffff;
    }

    #spotify-actions > Button:focus {
        background: #333333;
        border: tall #569f68;
        color: #ffffff;
        text-style: bold;
    }

    #spotify-actions > Button.-primary {
        background: #18271c;
        color: #569f68;
        border: tall #36603e;
    }

    #spotify-actions > Button.-primary:hover {
        background: #203425;
        color: #72b984;
        border: tall #569f68;
    }

    #spotify-actions > Button.-primary:focus {
        background: #569f68;
        color: #131313;
        border: tall #72b984;
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

    #settings-pill {
        width: auto;
        margin-right: 2;
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
        width: 14;
        height: 1;
        margin-right: 2;
    }

    /* MODAL: SETTINGS & KEYBINDS */
    SettingsModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #settings-dialog {
        width: 88;
        max-width: 96%;
        height: auto;
        max-height: 34;
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

    .setting-toggle-item {
        height: 3;
        width: 100%;
        background: #1a1a1a;
        border: solid #282828;
        padding: 0 1;
        margin-bottom: 1;
        content-align: left middle;
    }

    .setting-toggle-item:focus {
        border: solid #569f68;
        background: #1c261e;
    }

    #settings-table-title {
        height: 1;
        text-style: bold;
        color: #767676;
        margin-bottom: 0;
    }

    #settings-table {
        height: 10;
        border: solid #222222;
        background: transparent;
    }

    #settings-table > .datatable--cursor {
        background: #252525;
    }

    #settings-table:focus > .datatable--cursor {
        background: #2e2e2e;
    }

    #settings-status-line {
        height: 1;
        margin-top: 1;
        color: #569f68;
    }

    #settings-footer {
        height: 1;
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

    BINDINGS = [
        Binding("space", "toggle_play", "Play/Pause"),
        Binding("q", "quit_app", "Quit"),
        Binding("escape", "clear_or_unfocus", "Back"),
        Binding("delete", "delete_item", "Delete"),
        Binding("d", "delete_item", "Delete", show=False),
        Binding("x", "delete_item", "Delete", show=False),
        Binding("D", "delete_playlist", "Delete Playlist", show=False),
        Binding("shift+delete", "delete_playlist", "Delete Playlist", show=False),
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
        Binding("b", "focus_bar", "Seek Bar"),
        Binding("i", "focus_import", "Import"),
        Binding("a", "add_to_playlist", "Add to Playlist"),
        Binding("+", "add_to_playlist", "Add to Playlist", show=False),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("r", "toggle_repeat", "Repeat"),
        Binding("L", "open_spotify_auth", "Spotify", show=False),
        Binding("shift+l", "open_spotify_auth", "Spotify", show=False),
        Binding("S", "open_spotify_auth", "Spotify", show=False),
        Binding("u", "check_update", "Update", show=False),
        Binding("U", "check_update", "Update", show=False),
        Binding("colon", "show_help", "Help", show=False),
        Binding("shift+semicolon", "show_help", "Help", show=False),
        Binding("question_mark", "show_help", "Help", show=False),
        Binding("comma", "open_settings", "Settings", show=False),
        Binding("1", "nav_search", "Search"),
        Binding("2", "nav_playlist", "Playlist"),
        Binding("3", "nav_offline", "Offline"),
        Binding("4", "nav_lyrics", "Lyrics"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("J", "move_item_down", "Move Down", show=False),
        Binding("K", "move_item_up", "Move Up", show=False),
        Binding("shift+down", "move_item_down", "Move Down", show=False),
        Binding("shift+up", "move_item_up", "Move Up", show=False),
        Binding("h", "focus_sidebar", "Sidebar", show=False),
        Binding("l", "focus_tracks", "Tracks", show=False),
        Binding("tab", "toggle_focus", "Switch Pane", show=False, priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.volume: int = get_saved_volume()
        self.advanced_mode: bool = get_saved_advanced_mode()
        self.custom_keybindings: Dict[str, str] = get_custom_keybindings()
        self.keybindings: Dict[str, str] = {**DEFAULT_KEYBINDINGS, **self.custom_keybindings}
        self.player = MPVController(initial_volume=self.volume)
        self.visualizer = CavaVisualizer(bars=14)
        mpris_callbacks = {
            "play_pause": lambda: self.call_from_thread(self.action_toggle_play),
            "play": lambda: self.call_from_thread(self._mpris_play),
            "pause": lambda: self.call_from_thread(self._mpris_pause),
            "next": lambda: self.call_from_thread(self.action_next_track),
            "prev": lambda: self.call_from_thread(self.action_prev_track),
            "stop": lambda: self.call_from_thread(self._mpris_stop),
            "seek": lambda sec: self.call_from_thread(self._mpris_seek, sec),
            "set_position": lambda sec: self.call_from_thread(self._mpris_set_pos, sec),
            "set_volume": lambda vol: self.call_from_thread(self._mpris_set_vol, vol),
            "quit": lambda: self.call_from_thread(self.action_quit_app),
        }
        self.mpris = MPRISService(mpris_callbacks)
        self.update_info: Optional[Dict[str, Any]] = None
        self.queue: List[Dict[str, Any]] = []
        self.current_index: int = -1
        self.playlists: List[Dict[str, Any]] = []
        self.current_playlist_tracks: List[Dict[str, Any]] = []
        self.current_playlist_id: Optional[str] = None
        self.search_results: List[Dict[str, Any]] = []
        self.active_tab: str = "search"
        self._play_request_id: int = 0
        self.shuffle_mode: bool = False
        self.repeat_mode: str = "off"  # "off", "all", "one"
        self._shuffle_history: List[int] = []
        self.current_lyrics: Optional[Dict[str, Any]] = None
        self._active_lyric_idx: int = -1
        self.last_browsing_tab: str = "playlist"
        self.search_engine: str = get_saved_search_engine()
        self.transparency: bool = get_saved_transparency()
        self.player.playback_finished_callback = self.on_track_finished
        atexit.register(self._cleanup_on_exit)

    def set_advanced_mode(self, enabled: bool) -> None:
        self.advanced_mode = bool(enabled)
        save_advanced_mode(self.advanced_mode)
        self.apply_advanced_mode()

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

    def apply_transparency(self) -> None:
        bg_val = "ansi_default" if self.transparency else "#121212"
        self.styles.background = bg_val
        try:
            for s in self.screen_stack:
                if not isinstance(s, ModalScreen):
                    s.styles.background = bg_val
        except Exception:
            pass

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
                pill.update("[#767676]YTM[/]  [bold #569f68 on #18271c] SPOTIFY [/]")
                s_box.placeholder = "Search Spotify (artists, tracks)..."
            else:
                pill.update("[bold #ffffff on #2e2e2e] YT MUSIC [/]  [#767676]SPOT[/]")
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

    def set_custom_keybinding(self, action_id: str, new_key: str) -> None:
        if not new_key or new_key == DEFAULT_KEYBINDINGS.get(action_id):
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
            for act_id, key in self.keybindings.items():
                if key:
                    self._bindings.bind(key, act_id, show=False)
            # Secondary navigational and helper bindings
            self._bindings.bind("down", "cursor_down", show=False)
            self._bindings.bind("up", "cursor_up", show=False)
            self._bindings.bind("shift+down", "move_item_down", show=False)
            self._bindings.bind("shift+up", "move_item_up", show=False)
            self._bindings.bind("shift+delete", "delete_playlist", show=False)
            self._bindings.bind("x", "delete_item", show=False)
            self._bindings.bind("+", "add_to_playlist", show=False)
            self._bindings.bind("shift+l", "open_spotify_auth", show=False)
            self._bindings.bind("S", "open_spotify_auth", show=False)
        except Exception:
            pass
        self._update_nav_bar()
        self.update_spotify_pill()
        self.update_settings_pill()
        self.update_player_hud()

    def action_open_settings(self) -> None:
        self.push_screen(SettingsModal())

    def _cleanup_on_exit(self):
        try:
            vol_to_save = self.volume if self.volume > 0 else (getattr(self, "_prev_volume", 80) or 80)
            save_volume(vol_to_save)
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

    def on_unmount(self):
        self._cleanup_on_exit()

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-bar"):
            yield Static(r"[bold #ffffff]\[1] Search[/]    [#555555]\[2] Playlists    \[3] Offline    \[4] Lyrics[/]", id="nav-bar")
            yield Static("", id="update-pill")
            yield Static("[#555555]L: Spotify[/]", id="spotify-pill")
            yield Static("[#555555],: Settings[/]", id="settings-pill")
            yield Static("[dim]STANDBY[/dim]", id="status-pill")

        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Static("PLAYLISTS", classes="pane-title")
                yield Input(placeholder="New playlist name or Spotify link", id="sidebar-import-input", classes="action-input")
                yield DataTable(id="side-table", cursor_type="row", show_header=False)
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
                yield Static("No track playing", id="deck-track")
                yield Static("", id="deck-stats-pill")
                yield VisualizerWidget(self.visualizer, id="deck-visualizer")
                yield Static("[dim]SHUF[/dim]", id="shuf-pill")
                yield Static("[dim]REP[/dim]", id="rep-pill")
                yield Static("[dim]IDLE[/dim]", id="deck-source")
            with Horizontal(id="deck-line-2"):
                yield Static("00:00", id="time-elapsed")
                yield ScrubBar(total=100, show_eta=False, id="playback-bar")
                yield Static("00:00", id="time-total")
            yield Static("Enter: play  |  Space: pause  |  s: shuf  |  r: rep  |  4: lyrics  |  : help  |  q: quit", id="deck-line-3")

    def on_mount(self) -> None:
        self.player.start_mpv()
        self.player.set_volume(self.volume)
        self.apply_transparency()
        saved_sidebar_w = get_saved_sidebar_width()
        self.query_one("#sidebar").styles.width = saved_sidebar_w
        self.playlists = load_saved_playlists()
        self.apply_advanced_mode()
        self.apply_keybindings()
        self.update_engine_pill()
        self.mpris.start()
        if self.mpris:
            self.mpris.update_volume(self.volume)
        self.visualizer.start()
        self.check_github_updates_bg()

        st = self.query_one("#side-table", DataTable)
        st.cursor_foreground_priority = "renderable"
        st.add_column("Playlist", width=38)
        for idx, p in enumerate(self.playlists):
            st.add_row(p.get("name", "Untitled"), key=str(idx))

        tt = self.query_one("#track-table", DataTable)
        tt.cursor_foreground_priority = "renderable"
        tt.add_columns("Source", "Title", "Artist", "Duration")

        lt = self.query_one("#lyrics-table", DataTable)
        lt.cursor_foreground_priority = "renderable"
        lt.add_column("Time", width=7)
        lt.add_column("Lyric")

        self.set_interval(0.5, self.update_player_hud)
        logger.info("Spoff engine active.")

        if self.playlists:
            st.move_cursor(row=0)
            self.load_playlist_by_index(0, focus_tracks=True)
            tt.focus()
        else:
            self.switch_view("search")
            tt.focus()
            try:
                self.query_one("#sidebar-hint", Static).update("[dim]Enter name or link above to create[/dim]")
            except Exception:
                pass

        if is_first_launch():
            mark_first_launch_done()
            if not load_spotify_auth():
                self.call_after_refresh(lambda: self.action_open_spotify_auth(first_run=True))

    def notify_user(self, text: str):
        def _update():
            try:
                bar = self.query_one("#notification-line", Static)
                bar.update(escape(text))
            except Exception:
                pass
        try:
            self.call_from_thread(_update)
        except Exception:
            _update()

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
        if self.focused is None or not getattr(self.focused, "can_focus", False):
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()

    def on_key(self, event) -> None:
        if self.focused is None:
            if self.active_tab == "lyrics":
                self.query_one("#lyrics-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()

        # 1. Hardware media keys
        k = str(getattr(event, "key", "")).lower()
        name = str(getattr(event, "name", "")).lower()
        if k in ("audio_prev", "mediaprevioustrack") or name in ("audio_prev", "mediaprevioustrack"):
            self.action_prev_track()
            event.prevent_default()
            event.stop()
            return
        elif k in ("audio_play", "audio_pause", "mediaplaypause") or name in ("audio_play", "audio_pause", "mediaplaypause"):
            self.action_toggle_play()
            event.prevent_default()
            event.stop()
            return
        elif k in ("audio_next", "medianexttrack") or name in ("audio_next", "medianexttrack"):
            self.action_next_track()
            event.prevent_default()
            event.stop()
            return
        elif k in ("audio_mute",) or name in ("audio_mute",):
            self.action_vol_mute()
            event.prevent_default()
            event.stop()
            return
        elif k in ("audio_lower_volume",) or name in ("audio_lower_volume",):
            self.action_vol_down()
            event.prevent_default()
            event.stop()
            return
        elif k in ("audio_raise_volume",) or name in ("audio_raise_volume",):
            self.action_vol_up()
            event.prevent_default()
            event.stop()
            return

        # Switch engine shortcut (e.g. Ctrl+E, accessible even when typing)
        if key_matches(event.key, getattr(event, "character", None), self.keybindings.get("switch_engine", "ctrl+e")):
            self.action_switch_engine()
            event.prevent_default()
            event.stop()
            return

        # 2. Input widget handling: type text, leave on down/tab, unfocus on escape
        if isinstance(self.focused, Input):
            if event.key in ("down", "tab"):
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

        # 4. Jump up into Input from row 0 of DataTable on k / Up
        is_up_key = (
            event.key in ("up", "k")
            or event.character == "k"
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

        # 5. Dynamic match against self.keybindings
        matched_action = None
        for act_id, bound_key in self.keybindings.items():
            if bound_key and key_matches(event.key, getattr(event, "character", None), bound_key):
                matched_action = act_id
                break

        # Fallback secondary aliases
        if not matched_action:
            if event.key in ("j", "down") or event.character == "j":
                matched_action = "cursor_down"
            elif event.key in ("k", "up") or event.character == "k":
                matched_action = "cursor_up"
            elif (event.key == "h" or event.character == "h") and not isinstance(self.focused, Input):
                matched_action = "focus_sidebar"
            elif (event.key == "l" or event.character == "l") and not isinstance(self.focused, Input):
                matched_action = "focus_tracks"
            elif (event.key in ("J", "shift+down") or event.character == "J") and not isinstance(self.focused, Input):
                matched_action = "move_item_down"
            elif (event.key in ("K", "shift+up") or event.character == "K") and not isinstance(self.focused, Input):
                matched_action = "move_item_up"
            elif (event.key in ("colon", ":", "shift+semicolon", "question_mark") or event.character in (":", "?")) and not isinstance(self.focused, Input):
                matched_action = "show_help"
            elif event.key in ("ctrl+comma",) and not isinstance(self.focused, Input):
                matched_action = "open_settings"
            elif (event.key in ("S", "shift+s") or event.character == "S") and self.keybindings.get("open_spotify_auth") == "L":
                matched_action = "open_spotify_auth"
            elif (event.key in ("shift+delete",) or event.character == "D") and self.keybindings.get("delete_playlist") == "D":
                matched_action = "delete_playlist"
            elif (event.key in ("x", "delete") or event.character in ("x", "d")) and self.keybindings.get("delete_item") == "d":
                matched_action = "delete_item"
            elif (event.key == "+" or event.character in ("+", "a")) and self.keybindings.get("add_to_playlist") == "a":
                matched_action = "add_to_playlist"
            elif (event.key in ("U",) or event.character in ("u", "U")) and self.keybindings.get("check_update") == "u":
                matched_action = "check_update"
            elif event.key in ("f1",):
                matched_action = "vol_mute"
            elif event.key in ("f2",):
                matched_action = "vol_down"
            elif event.key in ("f3",):
                matched_action = "vol_up"

        if matched_action:
            act_method = getattr(self, f"action_{matched_action}", None)
            if callable(act_method):
                act_method()
                event.prevent_default()
                event.stop()
                return

    def action_nav_search(self): self.switch_view("search")
    def action_nav_playlist(self): self.switch_view("playlist")
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
        self.update_player_hud()

    def action_toggle_repeat(self):
        if isinstance(self.focused, Input):
            return
        modes = ["off", "all", "one"]
        curr_idx = modes.index(self.repeat_mode) if self.repeat_mode in modes else 0
        self.repeat_mode = modes[(curr_idx + 1) % len(modes)]
        labels = {"off": "OFF", "all": "ALL", "one": "SINGLE TRACK"}
        self.notify_user(f"Repeat: {labels[self.repeat_mode]}")
        self.update_player_hud()

    def switch_view(self, view: str):
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
            self.render_tracks(self.search_results)
            if not (self.focused and self.focused.id == "side-table"):
                track_table.focus()
            if not self.search_results:
                self.notify_user("" if self.advanced_mode else "Search: Press / or Up arrow to type query")
            else:
                self.notify_user("")
        elif view == "playlist":
            self.render_tracks(self.current_playlist_tracks)
            if not (self.focused and self.focused.id == "side-table"):
                track_table.focus()
            if not self.playlists:
                self.notify_user("No playlists yet — enter name in sidebar to create")
            elif not self.current_playlist_tracks:
                self.notify_user("Playlist is empty" if self.advanced_mode else "Playlist is empty — add songs from search with 'a'")
            else:
                self.notify_user("")
        elif view == "offline":
            offline_tracks = list(load_offline_index().values())
            self.render_tracks(offline_tracks)
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

    def _update_nav_bar(self) -> None:
        tabs = [
            ("search", "nav_search", "Search"),
            ("playlist", "nav_playlist", "Playlists"),
            ("offline", "nav_offline", "Offline"),
            ("lyrics", "nav_lyrics", "Lyrics"),
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
            lh.update(f"[bold #ffffff]{escape(title)}[/]  [#767676]—[/]  [#cccccc]{escape(artist)}[/]  [dim #767676]· FETCHING LYRICS...[/dim]")
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
                lt.add_row(f"[bold #569f68]{time_label}[/]", f"[bold #ffffff]▶  {text}[/]", key=str(idx))
            elif active_idx != -1 and idx < active_idx:
                lt.add_row(f"[dim #3a3a3a]{time_label}[/]", f"[#555555]   {text}[/]", key=str(idx))
            else:
                lt.add_row(f"[dim #555555]{time_label}[/]", f"[#888888]   {text}[/]", key=str(idx))

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
                lt.update_cell_at(Coordinate(old_idx, 0), f"[dim #3a3a3a]{t_str}[/]")
                lt.update_cell_at(Coordinate(old_idx, 1), f"[#555555]   {escape(old_line.get('text') or '♪')}[/]")
            except Exception:
                pass
        if 0 <= new_idx < lt.row_count and new_idx < len(lines):
            new_line = lines[new_idx]
            t_str = format_time(new_line.get("time", 0))
            try:
                lt.update_cell_at(Coordinate(new_idx, 0), f"[bold #569f68]{t_str}[/]")
                lt.update_cell_at(Coordinate(new_idx, 1), f"[bold #ffffff]▶  {escape(new_line.get('text') or '♪')}[/]")
                lt.move_cursor(row=new_idx)
            except Exception:
                pass

    def render_tracks(self, tracks: List[Dict[str, Any]], select_row: Optional[int] = None):
        table = self.query_one("#track-table", DataTable)
        old_cursor = table.cursor_row
        table.clear()
        for idx, t in enumerate(tracks):
            t_id = t.get("id") or str(hash(t.get("title", "") + t.get("artist", "")))
            is_cached = get_cached_track_path(t_id) is not None if t_id else False
            if is_cached:
                type_tag = "[bold #2aa198]OFFLINE[/]"
            elif t.get("source") == "spotify":
                type_tag = "[bold #569f68]SPOT[/]"
            elif t.get("source") in ("ytmusic", "youtube") or self.active_tab == "search":
                type_tag = "[#888888]YTM[/]"
            else:
                type_tag = "[dim]REMOTE[/dim]"
            dur_ms = t.get("duration_ms") or 0
            dur = format_time(dur_ms / 1000)
            table.add_row(type_tag, escape(t.get("title", "")), escape(t.get("artist", "")), dur, key=str(idx))
        if tracks:
            if select_row is not None:
                target = max(0, min(select_row, len(tracks) - 1))
            elif old_cursor is not None and 0 <= old_cursor < len(tracks):
                target = old_cursor
            else:
                target = 0
            try:
                table.move_cursor(row=target)
            except Exception:
                pass

    def action_focus_search(self):
        self.switch_view("search")
        self.query_one("#search-box", Input).focus()

    def action_focus_import(self):
        self.query_one("#sidebar-import-input", Input).focus()

    def action_focus_sidebar(self):
        if not isinstance(self.focused, Input):
            st = self.query_one("#side-table", DataTable)
            if self.playlists:
                st.focus()
            else:
                self.query_one("#sidebar-import-input", Input).focus()

    def action_focus_tracks(self):
        if not isinstance(self.focused, Input):
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
            self.query_one("#track-table", DataTable).focus()
            return

        if isinstance(f, DataTable):
            f.action_cursor_down()
        elif isinstance(f, Input):
            if f.id == "search-box":
                self.query_one("#track-table", DataTable).focus()
            elif f.id == "sidebar-import-input":
                self.query_one("#side-table", DataTable).focus()

    def action_cursor_up(self):
        f = self.focused
        if f is None:
            self.query_one("#track-table", DataTable).focus()
            return

        if isinstance(f, DataTable):
            if f.id == "track-table":
                if (f.row_count == 0 or f.cursor_row == 0) and self.active_tab == "search":
                    self.query_one("#search-box", Input).focus()
                    return
            elif f.id == "side-table":
                if f.row_count == 0 or f.cursor_row == 0:
                    self.query_one("#sidebar-import-input", Input).focus()
                    return
            f.action_cursor_up()
        elif isinstance(f, ScrubBar):
            self.query_one("#track-table", DataTable).focus()

    def action_move_item_up(self):
        if isinstance(self.focused, Input):
            return

        # 1. Reordering playlists in the sidebar
        if self.focused and self.focused.id == "side-table":
            st = self.query_one("#side-table", DataTable)
            idx = st.cursor_row
            if idx is not None and 1 <= idx < len(self.playlists):
                new_idx = idx - 1
                p = self.playlists.pop(idx)
                self.playlists.insert(new_idx, p)
                save_saved_playlists(self.playlists)
                st.clear()
                for i, pl in enumerate(self.playlists):
                    st.add_row(pl.get("name", "Untitled"), key=str(i))
                st.move_cursor(row=new_idx)
                return

        # 2. Dragging/reordering songs in a playlist
        if self.active_tab == "playlist" and self.current_playlist_tracks:
            tt = self.query_one("#track-table", DataTable)
            idx = tt.cursor_row
            if idx is not None and 1 <= idx < len(self.current_playlist_tracks):
                new_idx = idx - 1
                track = self.current_playlist_tracks.pop(idx)
                self.current_playlist_tracks.insert(new_idx, track)

                if self.current_playlist_id:
                    update_playlist_tracks(self.current_playlist_id, self.current_playlist_tracks)
                    for p in self.playlists:
                        if p.get("id") == self.current_playlist_id:
                            p["tracks"] = list(self.current_playlist_tracks)
                            break

                    # Sync reordering to Spotify in background if this is a Spotify playlist
                    if self.current_playlist_id != "spotify_liked_songs":
                        is_spotify = (len(self.current_playlist_id) == 22 and self.current_playlist_id.isalnum()) or any(
                            p.get("id") == self.current_playlist_id and p.get("spotify_id") for p in self.playlists
                        )
                        if is_spotify:
                            threading.Thread(
                                target=reorder_spotify_playlist_track,
                                args=(self.current_playlist_id, idx, new_idx),
                                daemon=True
                            ).start()

                if self.current_index == idx:
                    self.current_index = new_idx
                elif self.current_index == new_idx:
                    self.current_index = idx

                self.render_tracks(self.current_playlist_tracks, select_row=new_idx)
                tt.focus()
                return
        elif self.active_tab in ("search", "offline"):
            self.notify_user("Reordering songs is available in Playlists.")

    def action_move_item_down(self):
        if isinstance(self.focused, Input):
            return

        # 1. Reordering playlists in the sidebar
        if self.focused and self.focused.id == "side-table":
            st = self.query_one("#side-table", DataTable)
            idx = st.cursor_row
            if idx is not None and 0 <= idx < len(self.playlists) - 1:
                new_idx = idx + 1
                p = self.playlists.pop(idx)
                self.playlists.insert(new_idx, p)
                save_saved_playlists(self.playlists)
                st.clear()
                for i, pl in enumerate(self.playlists):
                    st.add_row(pl.get("name", "Untitled"), key=str(i))
                st.move_cursor(row=new_idx)
                return

        # 2. Dragging/reordering songs in a playlist
        if self.active_tab == "playlist" and self.current_playlist_tracks:
            tt = self.query_one("#track-table", DataTable)
            idx = tt.cursor_row
            if idx is not None and 0 <= idx < len(self.current_playlist_tracks) - 1:
                new_idx = idx + 1
                track = self.current_playlist_tracks.pop(idx)
                self.current_playlist_tracks.insert(new_idx, track)

                if self.current_playlist_id:
                    update_playlist_tracks(self.current_playlist_id, self.current_playlist_tracks)
                    for p in self.playlists:
                        if p.get("id") == self.current_playlist_id:
                            p["tracks"] = list(self.current_playlist_tracks)
                            break

                    if self.current_playlist_id != "spotify_liked_songs":
                        is_spotify = (len(self.current_playlist_id) == 22 and self.current_playlist_id.isalnum()) or any(
                            p.get("id") == self.current_playlist_id and p.get("spotify_id") for p in self.playlists
                        )
                        if is_spotify:
                            threading.Thread(
                                target=reorder_spotify_playlist_track,
                                args=(self.current_playlist_id, idx, new_idx),
                                daemon=True
                            ).start()

                if self.current_index == idx:
                    self.current_index = new_idx
                elif self.current_index == new_idx:
                    self.current_index = idx

                self.render_tracks(self.current_playlist_tracks, select_row=new_idx)
                tt.focus()
                return
        elif self.active_tab in ("search", "offline"):
            self.notify_user("Reordering songs is available in Playlists.")

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
        elif self.active_tab == "offline":
            return list(load_offline_index().values())
        return []

    def _sync_table_cursor_to_index(self, idx: int):
        try:
            tt = self.query_one("#track-table", DataTable)
            if 0 <= idx < tt.row_count:
                tt.move_cursor(row=idx)
        except Exception:
            pass

    def _start_or_resume_playback(self):
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
            self.play_index(0)
        else:
            self.notify_user("No tracks available to play.")

    def action_toggle_play(self):
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
        self.update_player_hud()

    def action_vol_up(self):
        self.volume = min(100, self.volume + 5)
        self.player.set_volume(self.volume)
        save_volume(self.volume)
        self.notify_user(f"Volume: {self.volume}%")
        self.update_player_hud()

    def action_vol_down(self):
        self.volume = max(0, self.volume - 5)
        self.player.set_volume(self.volume)
        save_volume(self.volume)
        self.notify_user(f"Volume: {self.volume}%")
        self.update_player_hud()

    def action_show_help(self):
        self.push_screen(HelpModal())

    def update_spotify_pill(self):
        try:
            auth_data = load_spotify_auth()
            pill = self.query_one("#spotify-pill", Static)
            if auth_data and get_valid_token():
                user = auth_data.get("user", {})
                name = user.get("display_name") or user.get("id") or "Connected"
                pill.update(f"[bold #569f68]● {escape(str(name))}[/]")
            else:
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

    @work(thread=True)
    def do_spotify_sync(self):
        self.notify_user("Syncing Spotify playlists and Liked Songs...")
        auth_data = load_spotify_auth()
        if not auth_data:
            self.notify_user("Please log in to Spotify first.")
            return

        token = get_valid_token()
        if not token:
            self.notify_user("Spotify session expired. Please log in again.")
            return

        def _progress(msg: str):
            self.notify_user(msg)

        try:
            synced_count = sync_spotify_library(token, progress_callback=_progress)
            self.playlists = load_saved_playlists()

            def _refresh_ui():
                self.refresh_side_table()
                self.update_spotify_pill()
                if synced_count > 0:
                    self.notify_user(f"Synced {synced_count} Spotify playlists/collections into your library!")
                    if self.playlists and self.active_tab == "playlist":
                        self.load_playlist_by_index(0, focus_tracks=True)
                else:
                    self.notify_user("Spotify library sync completed.")

            self.call_from_thread(_refresh_ui)
        except Exception as e:
            logger.error(f"Error syncing Spotify library: {e}")
            self.notify_user(f"Sync error: {e}")

    def _mpris_play(self):
        if self.player.is_paused:
            self.player.toggle_pause()
            self.update_player_hud()

    def _mpris_pause(self):
        if not self.player.is_paused and self.player.current_track:
            self.player.toggle_pause()
            self.update_player_hud()

    def _mpris_stop(self):
        self.player.stop()
        self.current_index = -1
        if self.mpris:
            self.mpris.update_track(None)
        self.update_player_hud()

    def _mpris_seek(self, sec: float):
        self.player.seek(sec)
        self.update_player_hud()

    def _mpris_set_pos(self, sec: float):
        self.player.seek_absolute(sec)
        self.update_player_hud()

    def _mpris_set_vol(self, vol: int):
        self.volume = max(0, min(100, vol))
        self.player.set_volume(self.volume)
        save_volume(self.volume)
        self.update_player_hud()

    @work(thread=True)
    def check_github_updates_bg(self):
        time.sleep(2.5)
        try:
            info = check_for_updates()
            if info and info.get("has_update"):
                self.update_info = info
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
        if self.update_info:
            def _handle(confirmed):
                if confirmed:
                    self.notify_user("Updated to latest version! Please restart Spoff.")
                    try:
                        self.query_one("#update-pill", Static).update("[bold #569f68]✓ Up to date[/]")
                    except Exception:
                        pass
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
                        self.push_screen(UpdateModal(info))
                    self.call_from_thread(_show)
                else:
                    self.notify_user("Spoff is up to date on the latest GitHub commit.")
            threading.Thread(target=_check, daemon=True).start()

    def action_next_track(self):
        if not self.queue:
            view_tracks = self._get_current_view_tracks()
            if view_tracks:
                self.queue = list(view_tracks)
                self.current_index = -1

        if not self.queue:
            self.notify_user("No tracks available in queue.")
            return

        if self.repeat_mode == "one" and self.current_index >= 0:
            self.play_index(self.current_index)
            return

        if self.shuffle_mode and len(self.queue) > 1:
            if self.current_index >= 0:
                self._shuffle_history.append(self.current_index)
            candidates = [i for i in range(len(self.queue)) if i != self.current_index]
            next_idx = random.choice(candidates)
            self.play_index(next_idx)
            self._sync_table_cursor_to_index(next_idx)
            return

        if self.current_index + 1 < len(self.queue):
            next_idx = self.current_index + 1 if self.current_index >= 0 else 0
            self.play_index(next_idx)
            self._sync_table_cursor_to_index(next_idx)
        elif self.repeat_mode == "all":
            self.play_index(0)
            self._sync_table_cursor_to_index(0)
        else:
            self.player.stop()
            self.current_index = -1
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

        if not self.queue:
            self.notify_user("No tracks available in queue.")
            return

        if self.shuffle_mode and self._shuffle_history:
            prev_idx = self._shuffle_history.pop()
            if 0 <= prev_idx < len(self.queue):
                self.play_index(prev_idx)
                self._sync_table_cursor_to_index(prev_idx)
                return

        if self.current_index > 0:
            prev_idx = self.current_index - 1
            self.play_index(prev_idx)
            self._sync_table_cursor_to_index(prev_idx)
        elif self.current_index == 0:
            self.player.seek_absolute(0)
            self.notify_user("Restarted track.")
            self.update_player_hud()
        else:
            self.play_index(0)
            self._sync_table_cursor_to_index(0)

    def action_quit_app(self):
        try:
            save_volume(self.volume)
        except Exception:
            pass
        self.player.stop()
        self.exit()

    def play_current_table_row(self, row_idx: int):
        if self.active_tab == "search":
            tracks = self.search_results
        elif self.active_tab == "playlist":
            tracks = self.current_playlist_tracks
        elif self.active_tab == "offline":
            tracks = list(load_offline_index().values())
        else:
            tracks = []

        if 0 <= row_idx < len(tracks):
            self.queue = list(tracks)
            self.play_index(row_idx)

    def refresh_side_table(self):
        st = self.query_one("#side-table", DataTable)
        st.clear()
        selected_idx = 0
        for idx, p in enumerate(self.playlists):
            st.add_row(p.get("name", "Untitled"), key=str(idx))
            if self.current_playlist_id and p.get("id") == self.current_playlist_id:
                selected_idx = idx
        if self.playlists:
            try:
                st.move_cursor(row=selected_idx)
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
                    hint.update("[dim]Enter: open  |  Del: delete[/dim]")
                else:
                    hint.update("[dim]Enter name or link above to create[/dim]")
        except Exception:
            pass

    def load_playlist_by_index(self, idx: int, focus_tracks: bool = False):
        if 0 <= idx < len(self.playlists):
            pl = self.playlists[idx]
            self.current_playlist_id = pl.get("id")
            name = pl.get("name", "Playlist")

            st = self.query_one("#side-table", DataTable)
            try:
                st.move_cursor(row=idx)
            except Exception:
                pass

            if pl.get("tracks"):
                self.current_playlist_tracks = list(pl["tracks"])
                if not self.queue or self.current_index == -1:
                    self.queue = list(self.current_playlist_tracks)
                    self.current_index = -1
                self.notify_user(f"Loaded playlist '{name}' ({len(self.current_playlist_tracks)} tracks).")
                self.switch_view("playlist")
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
                self.switch_view("playlist")
                if focus_tracks:
                    self.query_one("#track-table", DataTable).focus()
                else:
                    st.focus()

    def action_add_to_playlist(self):
        f = self.focused
        row_idx = None
        if isinstance(f, DataTable) and f.id == "track-table":
            row_idx = f.cursor_row
        elif self.query_one("#track-table", DataTable).cursor_row is not None:
            row_idx = self.query_one("#track-table", DataTable).cursor_row

        track = None
        if self.active_tab == "search":
            tracks = self.search_results
        elif self.active_tab == "playlist":
            tracks = self.current_playlist_tracks
        elif self.active_tab == "offline":
            tracks = list(load_offline_index().values())
        else:
            tracks = []

        if row_idx is not None and 0 <= row_idx < len(tracks):
            track = tracks[row_idx]
        elif self.player.current_track:
            track = self.player.current_track

        if not track:
            self.notify_user("Select a track first, or play a song to add it to a playlist.")
            return

        self.prompt_add_track_to_playlist(track)

    def prompt_add_track_to_playlist(self, track: Dict[str, Any]):
        def handle_modal_result(result: Optional[Tuple[str, str]]):
            if not result:
                return
            mode, val = result
            t_title = track.get("title", "Track")
            if mode == "create":
                new_pl = create_local_playlist(val)
                add_track_to_playlist(new_pl["id"], track)
                self.playlists = load_saved_playlists()
                self.refresh_side_table()
                self.notify_user(f"Created playlist '{val}' and added '{t_title}'.")

                # Asynchronous two-way sync to Spotify account (Spotify tracks only)
                is_yt = (
                    track.get("source") in ("ytmusic", "youtube")
                    or not (
                        track.get("uri", "").startswith("spotify:track:")
                        or (track.get("id") and len(str(track.get("id"))) == 22 and track.get("source") == "spotify")
                    )
                )
                if not is_yt:
                    def _sync_create_bg():
                        ok, msg = add_track_to_spotify_account(new_pl["id"], val, track)
                        if ok:
                            self.call_from_thread(self.notify_user, f"'{t_title}' synced to Spotify playlist '{val}'.")
                            self.playlists = load_saved_playlists()
                            self.call_from_thread(self.refresh_side_table)
                        elif msg and not msg.startswith("Not logged in"):
                            logger.info(f"Spotify sync notice: {msg}")
                            if "permission" in msg.lower() or "re-link" in msg.lower():
                                self.call_from_thread(self.notify_user, msg)
                    threading.Thread(target=_sync_create_bg, daemon=True).start()

            elif mode == "select":
                added = add_track_to_playlist(val, track)
                self.playlists = load_saved_playlists()
                pl_name = val
                for p in self.playlists:
                    if p["id"] == val:
                        pl_name = p.get("name", "Playlist")
                        if self.active_tab == "playlist" and self.current_playlist_id == val:
                            self.current_playlist_tracks = list(p.get("tracks", []))
                            self.render_tracks(self.current_playlist_tracks)
                        break
                if added:
                    self.notify_user(f"Added '{t_title}' to '{pl_name}'.")
                    # Asynchronous two-way sync to Spotify account (Spotify tracks only)
                    is_yt = (
                        track.get("source") in ("ytmusic", "youtube")
                        or not (
                            track.get("uri", "").startswith("spotify:track:")
                            or (track.get("id") and len(str(track.get("id"))) == 22 and track.get("source") == "spotify")
                        )
                    )
                    if not is_yt:
                        def _sync_select_bg():
                            ok, msg = add_track_to_spotify_account(val, pl_name, track)
                            if ok:
                                self.call_from_thread(self.notify_user, f"'{t_title}' synced to Spotify playlist '{pl_name}'.")
                            elif msg and not msg.startswith("Not logged in"):
                                logger.info(f"Spotify sync notice: {msg}")
                                if "permission" in msg.lower() or "re-link" in msg.lower():
                                    self.call_from_thread(self.notify_user, msg)
                        threading.Thread(target=_sync_select_bg, daemon=True).start()
                else:
                    self.notify_user(f"'{t_title}' is already in '{pl_name}'.")
                self.refresh_side_table()

        self.push_screen(AddToPlaylistModal(track, self.playlists), handle_modal_result)

    def action_delete_playlist(self):
        if isinstance(self.focused, Input):
            return

        target_pl = None
        target_idx = None

        # 1. If focused on side-table, use sidebar cursor
        if self.focused and self.focused.id == "side-table":
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
            st = self.query_one("#side-table", DataTable)
            idx = st.cursor_row if st.cursor_row is not None else 0
            if 0 <= idx < len(self.playlists):
                target_idx = idx
                target_pl = self.playlists[idx]

        if not target_pl:
            self.notify_user("No playlist selected to delete.")
            return

        pname = target_pl.get("name", "Playlist")
        pl_id = target_pl.get("id")

        def handle_delete_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            remove_saved_playlist(pl_id)
            self.playlists = load_saved_playlists()
            self.refresh_side_table()

            st = self.query_one("#side-table", DataTable)
            if self.playlists:
                new_row = max(0, min(target_idx or 0, len(self.playlists) - 1))
                st.move_cursor(row=new_row)
            else:
                st.clear()

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
            def _sync_del_pl_bg():
                ok, msg = delete_spotify_playlist(pl_id, pname)
                if ok:
                    self.call_from_thread(self.notify_user, f"Deleted '{pname}' locally and from Spotify.")
            threading.Thread(target=_sync_del_pl_bg, daemon=True).start()

            self.notify_user(f"Deleted playlist '{pname}'.")

        self.push_screen(
            ConfirmModal(
                title="DELETE PLAYLIST",
                message=f"Permanently delete playlist '[bold #ffffff]{escape(pname)}[/]' from your library?",
                confirm_label="Delete"
            ),
            handle_delete_confirm
        )

    def action_delete_item(self):
        if isinstance(self.focused, Input):
            return

        f = self.focused
        if f and f.id == "side-table":
            self.action_delete_playlist()
            return

        elif f and f.id == "track-table":
            row_idx = f.cursor_row
            if self.active_tab == "playlist":
                # If playlist is empty, delete the playlist itself
                if not self.current_playlist_tracks or row_idx is None or row_idx >= len(self.current_playlist_tracks):
                    self.action_delete_playlist()
                    return

                # Otherwise, removing a track from the playlist requires explicit confirmation
                t = self.current_playlist_tracks[row_idx]
                t_title = t.get("title", "Track")
                pl_id = self.current_playlist_id
                pl_name = next((p.get("name", "Playlist") for p in self.playlists if p.get("id") == pl_id), "Playlist")

                def handle_remove_track_confirm(confirmed: bool) -> None:
                    if not confirmed:
                        return
                    if 0 <= row_idx < len(self.current_playlist_tracks):
                        removed_track = self.current_playlist_tracks.pop(row_idx)
                        self.render_tracks(self.current_playlist_tracks)
                        if self.current_playlist_tracks:
                            new_row = max(0, min(row_idx, len(self.current_playlist_tracks) - 1))
                            f.move_cursor(row=new_row)
                        if pl_id:
                            update_playlist_tracks(pl_id, self.current_playlist_tracks)
                            self.playlists = load_saved_playlists()
                            self.refresh_side_table()

                            def _sync_remove_bg():
                                ok, msg = remove_track_from_spotify_account(pl_id, pl_name, removed_track)
                                if ok:
                                    self.call_from_thread(self.notify_user, f"Removed '{t_title}' from Spotify playlist '{pl_name}'.")
                            threading.Thread(target=_sync_remove_bg, daemon=True).start()

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

            elif self.active_tab == "offline":
                offline_tracks = list(load_offline_index().values())
                if row_idx is not None and 0 <= row_idx < len(offline_tracks):
                    t = offline_tracks[row_idx]
                    t_title = t.get("title", "Track")
                    delete_cached_track(t["id"])
                    remaining = list(load_offline_index().values())
                    self.render_tracks(remaining)
                    if remaining:
                        new_row = max(0, min(row_idx, len(remaining) - 1))
                        f.move_cursor(row=new_row)
                    self.notify_user(f"Removed '{t_title}' from offline disk cache.")

            elif self.active_tab == "search":
                if row_idx is not None and 0 <= row_idx < len(self.search_results):
                    t = self.search_results.pop(row_idx)
                    self.render_tracks(self.search_results)
                    if self.search_results:
                        new_row = max(0, min(row_idx, len(self.search_results) - 1))
                        f.move_cursor(row=new_row)
                    self.notify_user(f"Removed '{t.get('title')}' from search results.")
        else:
            if self.active_tab == "playlist":
                self.action_delete_playlist()

    def on_track_finished(self):
        if self.repeat_mode == "one" and self.current_index >= 0:
            self.call_from_thread(self.play_index, self.current_index)
        else:
            self.call_from_thread(self.action_next_track)

    def update_player_hud(self):
        pos, dur = self.player.get_progress()
        self.query_one("#time-elapsed", Static).update(format_time(pos))
        self.query_one("#time-total", Static).update(format_time(dur) if dur > 0 else "--:--")

        bar = self.query_one("#playback-bar", ScrubBar)
        if dur > 0:
            bar.total = dur
            bar.progress = pos

        curr = self.player.current_track
        is_scrubbing = (self.focused and self.focused.id == "playback-bar")

        is_paused = self.player.is_paused if curr else False

        # Update shuffle & repeat indicators
        shuf_badge = "[bold #569f68]SHUF[/]" if self.shuffle_mode else "[dim]SHUF[/dim]"
        self.query_one("#shuf-pill", Static).update(shuf_badge)

        rep_badge = "[dim]REP[/dim]"
        if self.repeat_mode == "all":
            rep_badge = "[bold #569f68]REP[/]"
        elif self.repeat_mode == "one":
            rep_badge = "[bold #569f68]REP-1[/]"
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
            self.mpris.update_volume(self.volume)
            self.mpris.update_status(curr is not None, is_paused)

        if curr:
            if self.player.is_paused:
                state_pill = "[bold #c4a768][PAUSED][/]"
            else:
                state_pill = "[bold #569f68][PLAYING][/]"
            self.query_one("#status-pill", Static).update(state_pill)

            is_cached = get_cached_track_path(curr.get("id", "")) is not None
            src = "[bold #569f68]LOCAL DISK[/]" if is_cached else "[bold #c4a768]STREAMING[/]"
            self.query_one("#deck-source", Static).update(src)
            safe_title = escape(str(curr.get("title", "")))
            safe_artist = escape(str(curr.get("artist", "")))
            self.query_one("#deck-track", Static).update(f"{safe_title}  -  {safe_artist}")
        else:
            self.query_one("#status-pill", Static).update("[dim]STANDBY[/dim]")
            self.query_one("#deck-source", Static).update("[dim]IDLE[/dim]")
            self.query_one("#deck-track", Static).update("No track playing")

        queue_len = len(self.queue)
        queue_pos = f"{self.current_index + 1}/{queue_len}" if queue_len > 0 and self.current_index >= 0 else "empty"
        vol_str = "Muted" if self.volume == 0 else f"{self.volume}%"

        if self.advanced_mode:
            if is_scrubbing:
                stat_text = "[bold #c4a768]SEEKING[/]"
            else:
                stat_text = f"[#767676]Vol: {vol_str}  Q: {queue_pos}[/]"
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
                shuf_k = format_key_display(self.keybindings.get("toggle_shuffle", "s"))
                rep_k = format_key_display(self.keybindings.get("toggle_repeat", "r"))
                lyr_k = format_key_display(self.keybindings.get("nav_lyrics", "4"))
                seek_k = format_key_display(self.keybindings.get("focus_bar", "b"))
                sett_k = format_key_display(self.keybindings.get("open_settings", ","))
                help_k = format_key_display(self.keybindings.get("show_help", ":"))
                help_label = ": help" if help_k in (":", "colon") else f"{help_k}: help"
                quit_k = format_key_display(self.keybindings.get("quit_app", "q"))
                hints = f"Vol: {vol_str}  |  Queue: {queue_pos}  |  {shuf_k}: shuf  |  {rep_k}: rep  |  {lyr_k}: lyrics  |  {seek_k}: seek  |  {sett_k}: set  |  {help_label}  |  {quit_k}: quit"
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
                if u.startswith("http://") or u.startswith("https://") or "spotify.com" in u:
                    self.import_playlist_url(u)
                else:
                    new_pl = create_local_playlist(u)
                    self.playlists = load_saved_playlists()
                    self.refresh_side_table()
                    self.load_playlist_by_index(0, focus_tracks=True)
                    self.notify_user(f"Created playlist '{u}'." if self.advanced_mode else f"Created playlist '{u}'. Press 'a' on any song to add it.")

    @work(thread=True)
    def do_search(self, query: str):
        engine_name = "Spotify" if self.search_engine == "spotify" else "YouTube Music"
        self.notify_user(f"Searching {engine_name} for '{query}'...")

        if self.search_engine == "spotify":
            ok, results, err_msg = search_spotify_tracks(query, limit=25)
            if not ok:
                self.search_results = []
                def _notify_err():
                    if self.active_tab == "search":
                        self.render_tracks([])
                    self.notify_user(err_msg or "Failed to search Spotify.")
                self.call_from_thread(_notify_err)
                return
        else:
            results = live_search_tracks(query, limit=25)

        self.search_results = results

        def _update_ui():
            if self.active_tab == "search":
                self.render_tracks(results)
                if results:
                    self.query_one("#track-table", DataTable).focus()
                else:
                    self.query_one("#search-box", Input).focus()
            if results:
                hint_str = "" if self.advanced_mode else " Press Enter to play."
                self.notify_user(f"Found {len(results)} tracks on {engine_name} for '{query}'.{hint_str}")
            else:
                self.notify_user(f"No tracks found on {engine_name} for '{query}'. Try different keywords.")

        self.call_from_thread(_update_ui)


    @work(thread=True)
    def import_playlist_url(self, url: str):
        self.notify_user("Fetching tracks from Spotify link...")
        parsed = parse_spotify_url(url)
        tracks = []
        name = "Spotify Playlist"

        pl = None
        if parsed and parsed[0] == "album":
            pl = fetch_spotify_album(url)
        else:
            pl = fetch_spotify_playlist(url)

        if pl:
            tracks = pl.get("tracks", [])
            name = pl.get("name", name)
            pid = pl["id"]
        else:
            self.notify_user("Could not load Spotify playlist. Please check that the link is public.")
            return

        add_saved_playlist({"id": pid, "name": name, "url": url, "tracks": tracks})
        self.playlists = load_saved_playlists()
        self.current_playlist_id = pid
        self.current_playlist_tracks = tracks

        def _update():
            self.refresh_side_table()
            self.notify_user(f"Imported playlist '{name}' ({len(tracks)} tracks).")
            self.switch_view("playlist")
            self.query_one("#track-table", DataTable).focus()
        self.call_from_thread(_update)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        if table_id == "side-table":
            idx = event.cursor_row
            if idx is not None and 0 <= idx < len(self.playlists):
                self.load_playlist_by_index(idx, focus_tracks=False)
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
        track = self.queue[index]
        self._sync_table_cursor_to_index(index)
        self._play_request_id += 1
        req_id = self._play_request_id
        self.start_playback(track, req_id)

    @work(thread=True)
    def start_playback(self, track: Dict[str, Any], req_id: int):
        t_id = track.get("id") or str(hash(track.get("title", "") + track.get("artist", "")))
        title = track.get("title", "Unknown")
        artist = track.get("artist", "Unknown")

        # Asynchronously fetch synced lyrics in background
        self._active_lyric_idx = -1
        self.current_lyrics = None
        if self.active_tab == "lyrics":
            self.call_from_thread(self.render_lyrics)

        def _fetch_lyr_bg():
            dur_ms = track.get("duration_ms")
            lyr = fetch_lyrics(title, artist, dur_ms)
            if req_id == self._play_request_id:
                self.current_lyrics = lyr
                if self.active_tab == "lyrics":
                    self.call_from_thread(self.render_lyrics)
        threading.Thread(target=_fetch_lyr_bg, daemon=True).start()

        if self.mpris:
            dur_sec = float(track.get("duration_ms", 0)) / 1000.0
            self.mpris.update_track(track, dur_sec)

        cached = get_cached_track_path(t_id)
        if cached:
            if req_id != self._play_request_id:
                return
            self.notify_user("")
            self.player.load_and_play(str(cached), track)
            return

        self.notify_user(f"Connecting stream for '{title}'...")

        res = search_and_resolve_stream(title, artist)
        if req_id != self._play_request_id:
            return

        if not res or not res.get("stream_url"):
            self.notify_user(f"Could not stream '{title}'. Track may be unavailable.")
            return

        stream_url = res["stream_url"]
        self.notify_user("")
        self.player.load_and_play(stream_url, track)

        def on_cached(path):
            self.notify_user(f"Saved '{title}' to offline library.")
            if self.active_tab == "offline":
                def _refresh():
                    self.render_tracks(list(load_offline_index().values()))
                self.call_from_thread(_refresh)

        download_track_to_cache(t_id, title, artist, on_complete=on_cached)

SpotatoTUI = SpoffTUI

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--update", "-u", "update"):
        run_cli_update()
        return

    def _signal_handler(sig, frame):
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGHUP, _signal_handler)
    except Exception:
        pass

    app = SpoffTUI()
    app.run()

if __name__ == "__main__":
    main()
