import sys
import os
import time
import logging
import threading
import atexit
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, Input, DataTable, ProgressBar
from textual.binding import Binding
from textual import work

try:
    from .spotify import fetch_spotify_playlist, fetch_spotify_album, parse_spotify_url
    from .storage import (
        load_saved_playlists, add_saved_playlist, remove_saved_playlist,
        create_local_playlist, add_track_to_playlist, remove_track_from_playlist,
        update_playlist_tracks, get_cached_track_path, load_offline_index,
        delete_cached_track, CACHE_DIR, LOG_FILE
    )
    from .streamer import search_and_resolve_stream, download_track_to_cache
    from .search import live_search_tracks
    from .player import MPVController
except ImportError:
    from spotify import fetch_spotify_playlist, fetch_spotify_album, parse_spotify_url
    from storage import (
        load_saved_playlists, add_saved_playlist, remove_saved_playlist,
        create_local_playlist, add_track_to_playlist, remove_track_from_playlist,
        update_playlist_tracks, get_cached_track_path, load_offline_index,
        delete_cached_track, CACHE_DIR, LOG_FILE
    )
    from streamer import search_and_resolve_stream, download_track_to_cache
    from search import live_search_tracks
    from player import MPVController

logger = logging.getLogger("spoff")

def format_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"

class AddToPlaylistModal(ModalScreen[Optional[Tuple[str, str]]]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("tab", "switch_focus", "Switch Focus", show=False),
        Binding("down", "cursor_down_input", "Down", show=False),
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
            yield Static(f"Track: [bold #ffffff]{escape(title)}[/]  -  [#767676]{escape(artist)}[/]", id="modal-track-info")
            yield Input(placeholder="Type new playlist name & Enter...", id="modal-input")
            yield Static("OR CHOOSE EXISTING PLAYLIST", id="modal-subtitle")
            yield DataTable(id="modal-table", cursor_type="row", show_header=False)
            yield Static("[dim]Enter: Select  |  Tab/Down: Switch  |  Esc: Cancel[/dim]", id="modal-hint")

    def on_mount(self) -> None:
        table = self.query_one("#modal-table", DataTable)
        table.add_columns("Playlist")
        if self.playlists:
            for p in self.playlists:
                p_name = p.get("name", "Untitled")
                tracks_count = len(p.get("tracks", []))
                table.add_row(f"{p_name}  [dim]({tracks_count} tracks)[/dim]")
        else:
            table.display = False
            self.query_one("#modal-subtitle", Static).update("[dim]No existing playlists yet - type a name above to create one[/dim]")
        self.query_one("#modal-input", Input).focus()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def action_switch_focus(self) -> None:
        if self.focused and self.focused.id == "modal-input":
            self.query_one("#modal-table", DataTable).focus()
        else:
            self.query_one("#modal-input", Input).focus()

    def action_cursor_down_input(self) -> None:
        if self.focused and self.focused.id == "modal-input":
            self.query_one("#modal-table", DataTable).focus()
        elif self.focused and self.focused.id == "modal-table":
            self.query_one("#modal-table", DataTable).action_cursor_down()

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
            yield Static(f"[bold #ffffff][Y / Enter][/] {self.confirm_label}    [#767676][N / Esc] Cancel[/]", id="confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

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

    Input {
        scrollbar-size-horizontal: 0 !important;
        scrollbar-size-vertical: 0 !important;
    }

    Screen {
        background: transparent;
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

    #logo {
        text-style: bold;
        width: 14;
        color: #ffffff;
    }

    #nav-bar {
        width: 1fr;
        padding-left: 2;
        color: #767676;
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
        width: 34;
        height: 100%;
        border-right: solid #262626;
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
        color: #888888;
    }

    DataTable:focus > .datatable--cursor {
        background: #e2e2e2;
        color: #131313;
        text-style: bold;
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

    #track-table {
        height: 1fr;
        border: none;
        background: transparent;
    }

    /* BOTTOM TRANSPORT DECK */
    #player-deck {
        dock: bottom;
        height: 5;
        background: transparent;
        border-top: solid #262626;
        padding: 0 2;
    }

    /* MODAL: ADD TO PLAYLIST */
    AddToPlaylistModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #modal-dialog {
        width: 60;
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
        background: #e2e2e2;
        color: #131313;
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
    }

    #deck-line-2 {
        height: 1;
        align: left middle;
    }

    #time-elapsed, #time-total {
        width: 6;
        color: #767676;
    }

    #playback-bar {
        width: 1fr;
        margin: 0 1;
        color: #ffffff;
    }

    #playback-bar > Bar > .bar--bar {
        color: #ffffff;
        background: #2e2e2e;
    }

    #playback-bar > Bar > .bar--complete {
        color: #ffffff;
        background: #2e2e2e;
    }

    #deck-line-3 {
        height: 1;
        color: #767676;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_play", "Play/Pause"),
        Binding("q", "quit_app", "Quit"),
        Binding("escape", "clear_or_unfocus", "Back"),
        Binding("delete", "delete_item", "Delete"),
        Binding("d", "delete_item", "Delete", show=False),
        Binding("x", "delete_item", "Delete", show=False),
        Binding("right", "seek_fwd", "+5s"),
        Binding("left", "seek_bwd", "-5s"),
        Binding("up", "vol_up", "Vol+"),
        Binding("down", "vol_down", "Vol-"),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Prev"),
        Binding("f7", "prev_track", "Prev", show=False),
        Binding("f8", "toggle_play", "Play/Pause", show=False),
        Binding("f9", "next_track", "Next", show=False),
        Binding("audio_prev", "prev_track", "Prev", show=False),
        Binding("audio_play", "toggle_play", "Play/Pause", show=False),
        Binding("audio_pause", "toggle_play", "Play/Pause", show=False),
        Binding("audio_next", "next_track", "Next", show=False),
        Binding("slash", "focus_search", "Search"),
        Binding("i", "focus_import", "Import"),
        Binding("a", "add_to_playlist", "Add to Playlist"),
        Binding("+", "add_to_playlist", "Add to Playlist", show=False),
        Binding("1", "nav_search", "Search"),
        Binding("2", "nav_playlist", "Playlist"),
        Binding("3", "nav_offline", "Offline"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("h", "focus_sidebar", "Sidebar", show=False),
        Binding("l", "focus_tracks", "Tracks", show=False),
        Binding("tab", "toggle_focus", "Switch Pane", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.player = MPVController()
        self.queue: List[Dict[str, Any]] = []
        self.current_index: int = -1
        self.playlists: List[Dict[str, Any]] = []
        self.current_playlist_tracks: List[Dict[str, Any]] = []
        self.current_playlist_id: Optional[str] = None
        self.search_results: List[Dict[str, Any]] = []
        self.active_tab: str = "search"
        self.volume: int = 80
        self._play_request_id: int = 0
        self.player.playback_finished_callback = self.on_track_finished
        atexit.register(self._cleanup_on_exit)

    def _cleanup_on_exit(self):
        try:
            self.player.stop()
        except Exception:
            pass

    def on_unmount(self):
        self._cleanup_on_exit()

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-bar"):
            yield Static("SPOFF", id="logo")
            yield Static("[bold #ffffff][1] Search[/]    [#666666][2] Current Playlist[/]    [#666666][3] Offline Library[/]", id="nav-bar")
            yield Static("[dim]STANDBY[/dim]", id="status-pill")

        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Static("PLAYLISTS", classes="pane-title")
                yield Input(placeholder="Name or Spotify link...", id="sidebar-import-input", classes="action-input")
                yield DataTable(id="side-table", cursor_type="row", show_header=False)
                yield Static("[dim]Enter: open  |  Del: remove[/dim]", id="sidebar-hint")

            with Vertical(id="content-pane"):
                yield Input(placeholder="Search any song or artist & press Enter...", id="search-box", classes="action-input")
                yield DataTable(id="track-table", cursor_type="row", show_header=True)

        with Vertical(id="player-deck"):
            yield Static("Ready. Press Enter on any song to play.", id="notification-line")
            with Horizontal(id="deck-line-1"):
                yield Static("No track playing", id="deck-track")
                yield Static("[dim]IDLE[/dim]", id="deck-source")
            with Horizontal(id="deck-line-2"):
                yield Static("00:00", id="time-elapsed")
                yield ProgressBar(total=100, show_eta=False, id="playback-bar")
                yield Static("00:00", id="time-total")
            yield Static("Enter: Play  |  Space: Pause  |  a: Add to Playlist  |  /: Search  |  Tab: Switch Pane  |  Del: Remove  |  q: Quit", id="deck-line-3")

    def on_mount(self) -> None:
        self.player.start_mpv()
        self.playlists = load_saved_playlists()

        st = self.query_one("#side-table", DataTable)
        st.add_columns("Playlist")
        for p in self.playlists:
            st.add_row(p.get("name", "Untitled"))

        tt = self.query_one("#track-table", DataTable)
        tt.add_columns("Type", "Title", "Artist", "Duration")

        self.set_interval(0.5, self.update_player_hud)
        logger.info("Spotato engine active.")

        if self.playlists:
            self.load_playlist_by_index(0)

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

    def on_key(self, event) -> None:
        if event.key in ("f7", "audio_prev"):
            self.action_prev_track()
            event.prevent_default()
        elif event.key in ("f8", "audio_play", "audio_pause"):
            self.action_toggle_play()
            event.prevent_default()
        elif event.key in ("f9", "audio_next"):
            self.action_next_track()
            event.prevent_default()
        elif event.key == "down" and isinstance(self.focused, Input):
            if self.focused.id == "search-box":
                self.query_one("#track-table", DataTable).focus()
                event.prevent_default()
            elif self.focused.id == "sidebar-import-input":
                self.query_one("#side-table", DataTable).focus()
                event.prevent_default()

    def action_nav_search(self): self.switch_view("search")
    def action_nav_playlist(self): self.switch_view("playlist")
    def action_nav_offline(self): self.switch_view("offline")

    def switch_view(self, view: str):
        self.active_tab = view
        search_box = self.query_one("#search-box", Input)
        track_table = self.query_one("#track-table", DataTable)

        def tab_str(key: str, label: str) -> str:
            if view == key:
                return f"[bold #ffffff]{label}[/]"
            return f"[#666666]{label}[/]"

        t1 = tab_str("search", "[1] Search")
        t2 = tab_str("playlist", "[2] Current Playlist")
        t3 = tab_str("offline", "[3] Offline Library")
        self.query_one("#nav-bar", Static).update(f"{t1}    {t2}    {t3}")

        search_box.display = (view == "search")
        track_table.display = True

        if view == "search":
            self.render_tracks(self.search_results)
        elif view == "playlist":
            self.render_tracks(self.current_playlist_tracks)
        elif view == "offline":
            self.render_tracks(list(load_offline_index().values()))

    def render_tracks(self, tracks: List[Dict[str, Any]]):
        table = self.query_one("#track-table", DataTable)
        table.clear()
        for idx, t in enumerate(tracks):
            t_id = t.get("id") or str(hash(t.get("title", "") + t.get("artist", "")))
            is_cached = get_cached_track_path(t_id) is not None if t_id else False
            type_tag = "[bold #569f68]OFFLINE[/]" if is_cached else "[dim]REMOTE[/dim]"
            dur_ms = t.get("duration_ms") or 0
            dur = format_time(dur_ms / 1000)
            table.add_row(type_tag, escape(t.get("title", "")), escape(t.get("artist", "")), dur, key=str(idx))

    def action_focus_search(self):
        self.switch_view("search")
        self.query_one("#search-box", Input).focus()

    def action_focus_import(self):
        self.query_one("#sidebar-import-input", Input).focus()

    def action_focus_sidebar(self):
        if not isinstance(self.focused, Input):
            self.query_one("#side-table", DataTable).focus()

    def action_focus_tracks(self):
        if not isinstance(self.focused, Input):
            self.query_one("#track-table", DataTable).focus()

    def action_clear_or_unfocus(self):
        f = self.focused
        if isinstance(f, Input):
            self.query_one("#track-table", DataTable).focus()

    def action_toggle_focus(self):
        f = self.focused
        if f and f.id == "side-table":
            self.query_one("#track-table", DataTable).focus()
        else:
            self.query_one("#side-table", DataTable).focus()

    def action_cursor_down(self):
        f = self.focused
        if isinstance(f, DataTable):
            f.action_cursor_down()

    def action_cursor_up(self):
        f = self.focused
        if isinstance(f, DataTable):
            f.action_cursor_up()

    def action_toggle_play(self):
        self.player.toggle_pause()
        self.update_player_hud()

    def action_seek_fwd(self): self.player.seek(5)
    def action_seek_bwd(self): self.player.seek(-5)

    def action_vol_up(self):
        self.volume = min(100, self.volume + 5)
        self.player.set_volume(self.volume)

    def action_vol_down(self):
        self.volume = max(0, self.volume - 5)
        self.player.set_volume(self.volume)

    def action_next_track(self):
        if self.current_index + 1 < len(self.queue):
            self.play_index(self.current_index + 1)
        else:
            self.player.stop()
            self.current_index = -1
            self.notify_user("End of queue reached.")
            self.update_player_hud()

    def action_prev_track(self):
        if self.current_index > 0:
            self.play_index(self.current_index - 1)
        else:
            self.player.seek(-self.player._last_pos)

    def action_quit_app(self):
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
        for p in self.playlists:
            st.add_row(p.get("name", "Untitled"))

    def load_playlist_by_index(self, idx: int):
        if 0 <= idx < len(self.playlists):
            pl = self.playlists[idx]
            self.current_playlist_id = pl.get("id")
            name = pl.get("name", "Playlist")

            if pl.get("tracks"):
                self.current_playlist_tracks = list(pl["tracks"])
                self.notify_user(f"Loaded '{name}' ({len(self.current_playlist_tracks)} tracks).")
                self.switch_view("playlist")
                self.query_one("#track-table", DataTable).focus()
                return

            url = pl.get("url", "")
            if url:
                self.import_playlist_url(url)
            else:
                self.current_playlist_tracks = []
                self.render_tracks([])
                self.notify_user(f"Opened empty playlist '{name}'. Press 'a' on any song to add tracks.")
                self.switch_view("playlist")
                self.query_one("#track-table", DataTable).focus()

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
            self.notify_user("Select a track or play a song to add to a playlist.")
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
                else:
                    self.notify_user(f"'{t_title}' is already in '{pl_name}'.")
                self.refresh_side_table()

        self.push_screen(AddToPlaylistModal(track, self.playlists), handle_modal_result)

    def action_delete_item(self):
        if isinstance(self.focused, Input):
            return

        f = self.focused
        if f and f.id == "side-table":
            row_idx = f.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self.playlists):
                target_pl = self.playlists[row_idx]
                pname = target_pl.get("name", "Playlist")

                def handle_delete_confirm(confirmed: bool) -> None:
                    if not confirmed:
                        return
                    remove_saved_playlist(target_pl["id"])
                    self.playlists = load_saved_playlists()
                    self.refresh_side_table()

                    st = self.query_one("#side-table", DataTable)
                    if self.playlists:
                        new_row = max(0, min(row_idx, len(self.playlists) - 1))
                        st.move_cursor(row=new_row)

                    if self.current_playlist_id == target_pl["id"]:
                        if self.playlists:
                            new_row = max(0, min(row_idx, len(self.playlists) - 1))
                            self.load_playlist_by_index(new_row)
                        else:
                            self.current_playlist_id = None
                            self.current_playlist_tracks = []
                            if self.active_tab == "playlist":
                                self.render_tracks([])

                    self.notify_user(f"Removed playlist '{pname}' from library.")

                self.push_screen(
                    ConfirmModal(
                        title="DELETE PLAYLIST",
                        message=f"Are you sure you want to remove '[bold #ffffff]{escape(pname)}[/]' from your library?",
                        confirm_label="Delete"
                    ),
                    handle_delete_confirm
                )

        elif f and f.id == "track-table":
            row_idx = f.cursor_row
            if row_idx is None:
                return

            if self.active_tab == "offline":
                offline_tracks = list(load_offline_index().values())
                if 0 <= row_idx < len(offline_tracks):
                    t = offline_tracks[row_idx]
                    t_title = t.get("title", "Track")
                    delete_cached_track(t["id"])
                    remaining = list(load_offline_index().values())
                    self.render_tracks(remaining)
                    if remaining:
                        new_row = max(0, min(row_idx, len(remaining) - 1))
                        f.move_cursor(row=new_row)
                    self.notify_user(f"Deleted cached file for '{t_title}'.")

            elif self.active_tab == "playlist":
                if 0 <= row_idx < len(self.current_playlist_tracks):
                    t = self.current_playlist_tracks.pop(row_idx)
                    t_title = t.get("title", "Track")
                    self.render_tracks(self.current_playlist_tracks)
                    if self.current_playlist_tracks:
                        new_row = max(0, min(row_idx, len(self.current_playlist_tracks) - 1))
                        f.move_cursor(row=new_row)
                    if hasattr(self, "current_playlist_id") and self.current_playlist_id:
                        update_playlist_tracks(self.current_playlist_id, self.current_playlist_tracks)
                        self.playlists = load_saved_playlists()
                        self.refresh_side_table()
                    self.notify_user(f"Removed '{t_title}' from playlist.")

            elif self.active_tab == "search":
                if 0 <= row_idx < len(self.search_results):
                    t = self.search_results.pop(row_idx)
                    self.render_tracks(self.search_results)
                    if self.search_results:
                        new_row = max(0, min(row_idx, len(self.search_results) - 1))
                        f.move_cursor(row=new_row)
                    self.notify_user(f"Dismissed search result: '{t.get('title')}'.")

    def on_track_finished(self):
        self.call_from_thread(self.action_next_track)

    def update_player_hud(self):
        pos, dur = self.player.get_progress()
        self.query_one("#time-elapsed", Static).update(format_time(pos))
        self.query_one("#time-total", Static).update(format_time(dur) if dur > 0 else "--:--")

        bar = self.query_one("#playback-bar", ProgressBar)
        if dur > 0:
            bar.total = dur
            bar.progress = pos

        curr = self.player.current_track
        if curr:
            if self.player.is_paused:
                state_pill = "[bold #c4a768][PAUSED][/]"
            else:
                state_pill = "[bold #569f68][PLAYING][/]"
            self.query_one("#status-pill", Static).update(state_pill)

            is_cached = get_cached_track_path(curr.get("id", "")) is not None
            if is_cached:
                src = "[bold #569f68]LOCAL DISK[/]"
            else:
                src = "[bold #c4a768]STREAMING[/]"
            self.query_one("#deck-source", Static).update(src)
            safe_title = escape(str(curr.get("title", "")))
            safe_artist = escape(str(curr.get("artist", "")))
            self.query_one("#deck-track", Static).update(f"{safe_title}  -  {safe_artist}")
        else:
            self.query_one("#status-pill", Static).update("[dim]STANDBY[/dim]")
            self.query_one("#deck-source", Static).update("[dim]IDLE[/dim]")
            self.query_one("#deck-track", Static).update("No track playing")

        queue_len = len(self.queue)
        queue_pos = f"{self.current_index + 1}/{queue_len}" if queue_len > 0 and self.current_index >= 0 else "Empty"
        hints = f"Vol: {self.volume}%  |  Queue: {queue_pos}  |  Enter: Play  |  Space/F8: Pause  |  F7/F9: Prev/Next  |  a: Add  |  /: Search  |  Tab: Pane  |  Del: Remove  |  q: Quit"
        self.query_one("#deck-line-3", Static).update(escape(hints))

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
                    self.load_playlist_by_index(0)
                    self.notify_user(f"Created playlist '{u}'. Press 'a' on any song to add tracks.")

    @work(thread=True)
    def do_search(self, query: str):
        self.notify_user(f"Searching for '{query}'...")
        results = live_search_tracks(query, limit=25)
        self.search_results = results

        def _update_ui():
            if self.active_tab == "search":
                self.render_tracks(results)
                self.query_one("#track-table", DataTable).focus()
            self.notify_user(f"Found {len(results)} tracks for '{query}'. Press Enter to play.")

        self.call_from_thread(_update_ui)

    @work(thread=True)
    def import_playlist_url(self, url: str):
        self.notify_user("Fetching playlist tracks...")
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
            self.notify_user("Could not load playlist. Check if the link is valid.")
            return

        add_saved_playlist({"id": pid, "name": name, "url": url, "tracks": tracks})
        self.playlists = load_saved_playlists()
        self.current_playlist_id = pid
        self.current_playlist_tracks = tracks

        def _update():
            self.refresh_side_table()
            self.notify_user(f"Loaded '{name}' ({len(tracks)} tracks).")
            self.switch_view("playlist")
            self.query_one("#track-table", DataTable).focus()
        self.call_from_thread(_update)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        if table_id == "side-table":
            idx = event.cursor_row
            if idx is not None and 0 <= idx < len(self.playlists):
                self.load_playlist_by_index(idx)
        elif table_id == "track-table":
            self.play_current_table_row(event.cursor_row)

    def play_index(self, index: int):
        if not (0 <= index < len(self.queue)):
            return
        self.current_index = index
        track = self.queue[index]
        self._play_request_id += 1
        req_id = self._play_request_id
        self.start_playback(track, req_id)

    @work(thread=True)
    def start_playback(self, track: Dict[str, Any], req_id: int):
        t_id = track.get("id") or str(hash(track.get("title", "") + track.get("artist", "")))
        title = track.get("title", "Unknown")
        artist = track.get("artist", "Unknown")

        cached = get_cached_track_path(t_id)
        if cached:
            if req_id != self._play_request_id:
                return
            self.notify_user(f"Playing '{title}' offline from disk.")
            self.player.load_and_play(str(cached), track)
            return

        self.notify_user(f"Connecting '{title}'...")

        res = search_and_resolve_stream(title, artist)
        if req_id != self._play_request_id:
            return

        if not res or not res.get("stream_url"):
            self.notify_user(f"Failed to stream '{title}'.")
            return

        stream_url = res["stream_url"]
        self.notify_user(f"Streaming '{title}' (caching to disk)...")
        self.player.load_and_play(stream_url, track)

        def on_cached(path):
            self.notify_user(f"Cached '{title}' to offline library.")
            if self.active_tab == "offline":
                def _refresh():
                    self.render_tracks(list(load_offline_index().values()))
                self.call_from_thread(_refresh)

        download_track_to_cache(t_id, title, artist, on_complete=on_cached)

SpotatoTUI = SpoffTUI

def main():
    app = SpoffTUI()
    app.run()

if __name__ == "__main__":
    main()
