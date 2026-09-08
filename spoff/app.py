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
from pathlib import Path

from rich.markup import escape
from rich.table import Table
from textual import events, work
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, Input, DataTable, ProgressBar, Button
from textual.binding import Binding

try:
    from .spotify import fetch_spotify_playlist, fetch_spotify_album, parse_spotify_url
    from .storage import (
        load_saved_playlists, save_saved_playlists, add_saved_playlist, remove_saved_playlist,
        create_local_playlist, add_track_to_playlist, remove_track_from_playlist,
        update_playlist_tracks, get_cached_track_path, load_offline_index,
        delete_cached_track, CACHE_DIR, LOG_FILE, is_first_launch, mark_first_launch_done,
        get_saved_volume, save_volume
    )
    from .streamer import search_and_resolve_stream, download_track_to_cache
    from .search import live_search_tracks
    from .player import MPVController
    from .auth import (
        load_spotify_auth, save_spotify_auth, logout_spotify, get_valid_token,
        generate_pkce_pair, build_auth_url, exchange_code_for_tokens,
        fetch_current_user_profile, sync_spotify_library, OAuthCallbackServer,
        SPOTIFY_PORT, add_track_to_spotify_account, remove_track_from_spotify_account,
        reorder_spotify_playlist_track, delete_spotify_playlist, has_modify_scopes
    )
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
        get_saved_volume, save_volume
    )
    from streamer import search_and_resolve_stream, download_track_to_cache
    from search import live_search_tracks
    from player import MPVController
    from auth import (
        load_spotify_auth, save_spotify_auth, logout_spotify, get_valid_token,
        generate_pkce_pair, build_auth_url, exchange_code_for_tokens,
        fetch_current_user_profile, sync_spotify_library, OAuthCallbackServer,
        SPOTIFY_PORT, add_track_to_spotify_account, remove_track_from_spotify_account,
        reorder_spotify_playlist_track, delete_spotify_playlist, has_modify_scopes
    )
    from mpris import MPRISService
    from visualizer import VisualizerWidget, CavaVisualizer
    from updater import check_for_updates, perform_update, run_cli_update

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
            yield Input(placeholder="New playlist name...", id="modal-input")
            yield Static("OR CHOOSE EXISTING PLAYLIST", id="modal-subtitle")
            yield DataTable(id="modal-table", cursor_type="row", show_header=False)
            yield Static("[dim]Enter: select / create  |  Tab: switch  |  Esc: cancel[/dim]", id="modal-hint")

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
        else:
            table.display = False
            self.query_one("#modal-subtitle", Static).update("[dim]No existing playlists yet — type a name above to create one[/dim]")
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

                with Horizontal(id="spotify-actions"):
                    yield Button(r"\[S] Sync", variant="primary", id="btn-sync")
                    if not can_modify:
                        yield Button(r"\[R] Re-link", variant="warning", id="btn-relink")
                    else:
                        yield Button(r"\[R] Re-link", id="btn-relink")
                    yield Button(r"\[O] Log Out", variant="error", id="btn-logout")
                    yield Button(r"\[Esc] Close", id="btn-close")

                yield Static("", id="spotify-instruction")
                yield Static("", id="spotify-hint")

            else:
                if self.first_run:
                    yield Static("Connect your Spotify account to sync your playlists and Liked Songs into Spoff, and enable two-way synchronization. You can also skip and use local offline playback anytime.", id="spotify-desc")
                else:
                    yield Static("Connect your Spotify account to sync your playlists and Liked Songs into Spoff, and enable two-way synchronization.", id="spotify-desc")
                yield Static("[dim]Status: Not connected[/dim]", id="spotify-status")

                with Horizontal(id="spotify-actions"):
                    yield Button(r"\[Enter] Browser Login", variant="primary", id="btn-login")
                    yield Button(r"\[Esc] Skip" if self.first_run else r"\[Esc] Cancel", id="btn-close")

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
        elif event.key == "left" or event.character == "h":
            self.action_focus_prev_button()
            event.prevent_default()
            event.stop()
        elif event.key == "right" or event.character == "l":
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
            yield Static("[bold #569f68][Enter / Y][/] Update Now    [#767676][Esc / N][/] Later", id="update-hint")

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
        left_table = Table.grid(padding=(0, 2))
        left_table.add_column(style="bold #ffffff", width=16)
        left_table.add_column(style="#cccccc", width=26)

        left_table.add_row("[bold #569f68]VIEWS & NAV[/]", "")
        left_table.add_row("1", "Search songs & artists")
        left_table.add_row("2", "Playlists & favorites")
        left_table.add_row("3", "Offline library (cached)")
        left_table.add_row("h / l", "Sidebar / Tracks pane")
        left_table.add_row("j / k, Arrows", "Navigate table rows")
        left_table.add_row("k (at top row)", "Jump into search/import bar")
        left_table.add_row("Tab", "Cycle input, table, seek bar")
        left_table.add_row("Enter", "Play track / Open playlist")
        left_table.add_row("", "")
        left_table.add_row("[bold #569f68]PLAYBACK[/]", "")
        left_table.add_row("Space, Fn+F8", "Play / Pause toggle")
        left_table.add_row("F1", "Mute / Unmute audio")
        left_table.add_row("F2 / F3", "Volume -/+ 5%")
        left_table.add_row("p / n, Fn+F7/F9", "Previous / Next track")
        left_table.add_row("Left / Right", "Seek -/+ 5 seconds")

        right_table = Table.grid(padding=(0, 2))
        right_table.add_column(style="bold #ffffff", width=16)
        right_table.add_column(style="#cccccc", width=26)

        right_table.add_row("[bold #569f68]SEEK / PROGRESS[/]", "")
        right_table.add_row("b", "Focus song seek bar")
        right_table.add_row("h / l", "Seek -5s / +5s on bar")
        right_table.add_row("H / L", "Fast seek -15s / +15s")
        right_table.add_row("0 – 9", "Jump to 0% – 90% of song")
        right_table.add_row("Esc, k, Up", "Return to tracks table")
        right_table.add_row("", "")
        right_table.add_row("[bold #569f68]PLAYLISTS & ACTIONS[/]", "")
        right_table.add_row("J / K, Shift+Arrows", "Reorder / drag songs")
        right_table.add_row("a, +", "Add track to playlist")
        right_table.add_row("i", "New playlist / import link")
        right_table.add_row("L / S", "Spotify login & sync")
        right_table.add_row("u / U", "Check / pull GitHub update")
        right_table.add_row("/", "Focus search box")
        right_table.add_row("Del, d, x", "Remove track / playlist")
        right_table.add_row("D, Shift+Del", "Delete whole playlist")
        right_table.add_row(": / Shift+;", "Show keybindings guide")
        right_table.add_row("q", "Quit Spoff")

        main_table = Table.grid(padding=(0, 2))
        main_table.add_column()
        main_table.add_column()
        main_table.add_row(left_table, right_table)

        with Vertical(id="help-dialog"):
            yield Static("KEYBINDINGS & USAGE GUIDE", id="help-title")
            yield Static(main_table, id="help-text")
            yield Static("[dim]Press Esc, Enter, or : to close[/dim]", id="help-hint")

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

    #player-deck:focus-within {
        border-top: solid #383838;
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
        width: 90;
        height: auto;
        background: #181818;
        border: solid #2a2a2a;
        padding: 1 2;
    }

    #help-title {
        text-style: bold;
        color: #ffffff;
        margin-bottom: 1;
    }

    #help-text {
        color: #cccccc;
    }

    #help-hint {
        color: #555555;
        margin-top: 1;
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
        Binding("up", "vol_up", "Vol+"),
        Binding("down", "vol_down", "Vol-"),
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
        Binding("L", "open_spotify_auth", "Spotify", show=False),
        Binding("s", "open_spotify_auth", "Spotify", show=False),
        Binding("S", "open_spotify_auth", "Spotify", show=False),
        Binding("u", "check_update", "Update", show=False),
        Binding("U", "check_update", "Update", show=False),
        Binding("colon", "show_help", "Help", show=False),
        Binding("shift+semicolon", "show_help", "Help", show=False),
        Binding("question_mark", "show_help", "Help", show=False),
        Binding("1", "nav_search", "Search"),
        Binding("2", "nav_playlist", "Playlist"),
        Binding("3", "nav_offline", "Offline"),
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
        self.player.playback_finished_callback = self.on_track_finished
        atexit.register(self._cleanup_on_exit)

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
            yield Static(r"[bold #ffffff]\[1] Search[/]    [#555555]\[2] Playlists    \[3] Offline[/]", id="nav-bar")
            yield Static("", id="update-pill")
            yield Static("[#555555]L: Spotify[/]", id="spotify-pill")
            yield Static("[dim]STANDBY[/dim]", id="status-pill")

        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Static("PLAYLISTS", classes="pane-title")
                yield Input(placeholder="New playlist name or Spotify link", id="sidebar-import-input", classes="action-input")
                yield DataTable(id="side-table", cursor_type="row", show_header=False)
                yield Static("[dim]Enter: open  |  Del: delete[/dim]", id="sidebar-hint")

            with Vertical(id="content-pane"):
                yield Input(placeholder="Search songs or artists...", id="search-box", classes="action-input")
                yield DataTable(id="track-table", cursor_type="row", show_header=True)

        with Vertical(id="player-deck"):
            yield Static("", id="notification-line")
            with Horizontal(id="deck-line-1"):
                yield Static("No track playing", id="deck-track")
                yield VisualizerWidget(self.visualizer, id="deck-visualizer")
                yield Static("[dim]IDLE[/dim]", id="deck-source")
            with Horizontal(id="deck-line-2"):
                yield Static("00:00", id="time-elapsed")
                yield ScrubBar(total=100, show_eta=False, id="playback-bar")
                yield Static("00:00", id="time-total")
            yield Static("Enter: play  |  Space: pause  |  b: seek  |  : help  |  q: quit", id="deck-line-3")

    def on_mount(self) -> None:
        self.player.start_mpv()
        self.player.set_volume(self.volume)
        self.playlists = load_saved_playlists()
        self.update_spotify_pill()
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
            elif event.widget.id == "update-pill":
                self.action_check_update()
                return
        if self.focused is None or not getattr(self.focused, "can_focus", False):
            self.query_one("#track-table", DataTable).focus()

    def on_key(self, event) -> None:
        if self.focused is None:
            self.query_one("#track-table", DataTable).focus()

        k = str(getattr(event, "key", "")).lower()
        name = str(getattr(event, "name", "")).lower()

        if k in ("f1", "audio_mute") or name in ("f1", "audio_mute"):
            self.action_vol_mute()
            event.prevent_default()
            event.stop()
            return
        elif k in ("f2", "audio_lower_volume") or name in ("f2", "audio_lower_volume"):
            self.action_vol_down()
            event.prevent_default()
            event.stop()
            return
        elif k in ("f3", "audio_raise_volume") or name in ("f3", "audio_raise_volume"):
            self.action_vol_up()
            event.prevent_default()
            event.stop()
            return
        elif k in ("audio_prev", "mediaprevioustrack") or name in ("audio_prev", "mediaprevioustrack"):
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
        elif (event.key in ("colon", ":", "shift+semicolon", "question_mark") or event.character in (":", "?")) and not isinstance(self.focused, Input):
            self.action_show_help()
            event.prevent_default()
            event.stop()
            return
        elif (event.key in ("s", "S", "L") or event.character in ("s", "S", "L")) and not isinstance(self.focused, Input) and not (isinstance(self.focused, ScrubBar) and event.character == "L"):
            self.action_open_spotify_auth()
            event.prevent_default()
            event.stop()
            return
        elif (event.key in ("u", "U") or event.character in ("u", "U")) and not isinstance(self.focused, Input):
            self.action_check_update()
            event.prevent_default()
            event.stop()
            return
        elif (event.key in ("J", "K", "shift+down", "shift+up") or event.character in ("J", "K")) and not isinstance(self.focused, Input):
            if event.key in ("J", "shift+down") or event.character == "J":
                self.action_move_item_down()
            else:
                self.action_move_item_up()
            event.prevent_default()
            event.stop()
            return
        elif (event.key in ("D", "shift+d", "shift+delete") or event.character == "D") and not isinstance(self.focused, Input):
            self.action_delete_playlist()
            event.prevent_default()
            event.stop()
            return
        elif event.key == "down" and isinstance(self.focused, Input):
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
        elif event.key in ("up", "k") and self.focused and self.focused.id == "track-table":
            table = self.query_one("#track-table", DataTable)
            if (table.row_count == 0 or table.cursor_row == 0) and self.active_tab == "search":
                self.query_one("#search-box", Input).focus()
                event.prevent_default()
                event.stop()
                return
        elif event.key in ("up", "k") and self.focused and self.focused.id == "side-table":
            table = self.query_one("#side-table", DataTable)
            if table.row_count == 0 or table.cursor_row == 0:
                self.query_one("#sidebar-import-input", Input).focus()
                event.prevent_default()
                event.stop()
                return
        elif event.key in ("down", "j") and self.focused and self.focused.id == "track-table":
            table = self.query_one("#track-table", DataTable)
            if table.row_count == 0 or (table.cursor_row is not None and table.cursor_row >= table.row_count - 1):
                self.query_one("#playback-bar", ScrubBar).focus()
                event.prevent_default()
                event.stop()
                return

    def action_nav_search(self): self.switch_view("search")
    def action_nav_playlist(self): self.switch_view("playlist")
    def action_nav_offline(self): self.switch_view("offline")

    def switch_view(self, view: str):
        self.active_tab = view
        search_box = self.query_one("#search-box", Input)
        track_table = self.query_one("#track-table", DataTable)

        search_box.display = (view == "search")
        track_table.display = True

        tabs = [("search", "1", "Search"), ("playlist", "2", "Playlists"), ("offline", "3", "Offline")]
        parts = []
        for mode, num, label in tabs:
            if mode == view:
                parts.append(f"[bold #ffffff]\\[{num}] {label}[/]")
            else:
                parts.append(f"[#555555]\\[{num}] {label}[/]")
        try:
            self.query_one("#nav-bar", Static).update("    ".join(parts))
        except Exception:
            pass

        if view == "search":
            self.render_tracks(self.search_results)
            if not (self.focused and self.focused.id == "side-table"):
                track_table.focus()
            if not self.search_results:
                self.notify_user("Search: Press / or Up arrow to type query")
            else:
                self.notify_user("")
        elif view == "playlist":
            self.render_tracks(self.current_playlist_tracks)
            if not (self.focused and self.focused.id == "side-table"):
                track_table.focus()
            if not self.playlists:
                self.notify_user("No playlists yet — enter name in sidebar to create")
            elif not self.current_playlist_tracks:
                self.notify_user("Playlist is empty — add songs from search with 'a'")
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

    def render_tracks(self, tracks: List[Dict[str, Any]], select_row: Optional[int] = None):
        table = self.query_one("#track-table", DataTable)
        old_cursor = table.cursor_row
        table.clear()
        for idx, t in enumerate(tracks):
            t_id = t.get("id") or str(hash(t.get("title", "") + t.get("artist", "")))
            is_cached = get_cached_track_path(t_id) is not None if t_id else False
            type_tag = "[bold #569f68]OFFLINE[/]" if is_cached else "[dim]REMOTE[/dim]"
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
            self.query_one("#track-table", DataTable).focus()

    def action_clear_or_unfocus(self):
        f = self.focused
        if isinstance(f, Input):
            if f.id == "sidebar-import-input":
                self.query_one("#side-table", DataTable).focus()
            else:
                self.query_one("#track-table", DataTable).focus()
        elif f and f.id == "playback-bar":
            self.query_one("#track-table", DataTable).focus()
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
            self.query_one("#track-table", DataTable).focus()
        elif f.id == "search-box":
            self.query_one("#track-table", DataTable).focus()
        elif f.id == "track-table":
            self.query_one("#playback-bar", ScrubBar).focus()
        elif f.id == "playback-bar":
            self.query_one("#side-table", DataTable).focus()
        else:
            self.query_one("#track-table", DataTable).focus()

    def action_cursor_down(self):
        f = self.focused
        if f is None:
            self.query_one("#track-table", DataTable).focus()
            return

        if isinstance(f, DataTable):
            if f.id == "track-table":
                if f.row_count == 0 or (f.cursor_row is not None and f.cursor_row >= f.row_count - 1):
                    self.query_one("#playback-bar", ScrubBar).focus()
                    return
            elif f.id == "side-table":
                if f.row_count == 0 or (f.cursor_row is not None and f.cursor_row >= f.row_count - 1):
                    self.query_one("#playback-bar", ScrubBar).focus()
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
                pill.update("[#555555]L: Spotify[/]")
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
                        self.query_one("#update-pill", Static).update("[bold #c4a768]▲ Update (u)[/]")
                    except Exception:
                        pass
                    msg = info.get("message", "")
                    sha = info.get("remote_sha", "")
                    self.notify_user(f"Update available: {sha} ({msg}) — Press 'u' to update")
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

        if self.queue:
            if self.current_index + 1 < len(self.queue):
                next_idx = self.current_index + 1 if self.current_index >= 0 else 0
                self.play_index(next_idx)
                self._sync_table_cursor_to_index(next_idx)
            else:
                self.player.stop()
                self.current_index = -1
                self.notify_user("End of queue reached.")
                self.update_player_hud()
        else:
            self.notify_user("No tracks available in queue.")

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

        if self.queue:
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
        else:
            self.notify_user("No tracks available in queue.")

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
                self.notify_user(f"Opened empty playlist '{name}'. Press 'a' on any song to add it.")
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

                # Asynchronous two-way sync to Spotify account
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
                    # Asynchronous two-way sync to Spotify account
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

        if is_scrubbing:
            hints = "Seek: h/l (-/+5s)  |  H/L (-/+15s)  |  0-9: jump %  |  Space: pause  |  Esc: back"
        else:
            queue_len = len(self.queue)
            queue_pos = f"{self.current_index + 1}/{queue_len}" if queue_len > 0 and self.current_index >= 0 else "empty"
            vol_str = "Muted" if self.volume == 0 else f"{self.volume}%"
            hints = f"Vol: {vol_str}  |  Queue: {queue_pos}  |  Enter: play  |  Space: pause  |  b: seek  |  : help  |  q: quit"
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
                    self.load_playlist_by_index(0, focus_tracks=True)
                    self.notify_user(f"Created playlist '{u}'. Press 'a' on any song to add it.")

    @work(thread=True)
    def do_search(self, query: str):
        self.notify_user(f"Searching for '{query}'...")
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
                self.notify_user(f"Found {len(results)} tracks for '{query}'. Press Enter to play.")
            else:
                self.notify_user(f"No tracks found for '{query}'. Try different keywords.")

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
