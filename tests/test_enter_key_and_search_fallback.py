"""
Tests for Enter, Return, and Ctrl+M key submission, Spotify search fallback to YouTube Music,
and text cursor suppression when not focused on an Input.
"""

import threading
import unittest
from unittest.mock import Mock, patch
from rich.markup import escape

from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Input, Static, DataTable
from textual.containers import Vertical
from textual.binding import Binding
from textual.geometry import Offset

from spoff.app import SpoffTUI, SafeModalScreen


class TestEnterKeyAndSearchFallback(unittest.IsolatedAsyncioTestCase):
    def test_notify_user_respects_force_flag_when_notifications_disabled(self):
        """Verifies that notify_user updates the status line when force=True, even if notifications_enabled is False."""
        fake_app = SpoffTUI(notifications_enabled=False)
        mock_bar = Mock()
        fake_app.query_one = Mock(return_value=mock_bar)
        fake_app._thread_id = threading.get_ident()

        # With force=False and notifications_enabled=False, notification line should not be updated
        fake_app.notify_user("Ignored ambient notification", force=False)
        mock_bar.update.assert_not_called()

        # With force=True, notification line MUST be updated
        fake_app.notify_user("Direct user feedback", force=True)
        mock_bar.update.assert_called_once_with(escape("Direct user feedback"))

    def test_do_search_switches_to_search_tab_and_forces_notification(self):
        """Verifies that do_search switches to 'search' view and calls notify_user with force=True."""
        fake_app = SpoffTUI()
        fake_app.active_tab = "playlists"
        fake_app.switch_view = Mock()
        fake_app.notify_user = Mock()
        fake_app._search_worker = Mock()

        fake_app.do_search("bohemian rhapsody")

        fake_app.switch_view.assert_called_once_with("search")
        fake_app.notify_user.assert_called_once()
        self.assertTrue(fake_app.notify_user.call_args[1].get("force"))
        fake_app._search_worker.assert_called_once()

    @patch("spoff.app.search_spotify_tracks")
    @patch("spoff.app.live_search_tracks")
    def test_search_worker_falls_back_to_ytmusic_when_spotify_fails(self, mock_yt, mock_sp):
        """Verifies that when Spotify search fails (e.g. unauthenticated), search automatically falls back to YouTube Music."""
        fake_app = SpoffTUI()
        fake_app.active_tab = "search"
        fake_app.render_tracks = Mock()
        fake_app.notify_user = Mock()
        fake_app.query_one = Mock(return_value=Mock())
        fake_app._search_request_id = 42

        # Simulate Spotify unauthenticated failure
        mock_sp.return_value = (False, [], "Not logged in to Spotify. Press Shift+L or click Spotify to log in.")
        mock_yt.return_value = [
            {"id": "yt123", "title": "Bohemian Rhapsody", "artist": "Queen", "source": "ytmusic"}
        ]

        # Call worker synchronously using the underlying __wrapped__ function
        with patch.object(fake_app, "call_from_thread", side_effect=lambda fn, *a: fn(*a)):
            fake_app._search_worker.__wrapped__(fake_app, "bohemian rhapsody", 42, "spotify", "Spotify")

        mock_sp.assert_called_once_with("bohemian rhapsody", limit=25)
        mock_yt.assert_called_once_with("bohemian rhapsody", limit=25)
        self.assertEqual(len(fake_app.search_results), 1)
        self.assertEqual(fake_app.search_results[0]["id"], "yt123")
        fake_app.render_tracks.assert_called_once()
        fake_app.notify_user.assert_called_once()
        self.assertTrue(fake_app.notify_user.call_args[1].get("force"))

    def test_input_widget_submission_on_enter_return_and_ctrl_m(self):
        """Verifies that SpoffTUI.on_key dispatches Input.Submitted on 'enter', 'return', and 'ctrl+m'."""
        from unittest.mock import PropertyMock
        fake_app = SpoffTUI()
        fake_app._is_ready = True
        fake_app._mount_time = 0.0
        mock_input = Mock(spec=Input)
        mock_input.value = "test_query"

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_input):
            for key_name in ("enter", "return", "ctrl+m"):
                mock_input.reset_mock()
                key_event = events.Key(key_name, "\r" if "m" in key_name or "return" in key_name else "enter")
                key_event.prevent_default = Mock()
                key_event.stop = Mock()

                fake_app.on_key(key_event)

                # Verify Input.Submitted message was posted
                self.assertEqual(mock_input.post_message.call_count, 1)
                posted_msg = mock_input.post_message.call_args[0][0]
                self.assertIsInstance(posted_msg, Input.Submitted)
                self.assertEqual(posted_msg.value, "test_query")
                key_event.prevent_default.assert_called_once()
                key_event.stop.assert_called_once()

    async def test_safe_modal_screen_handles_return_and_ctrl_m(self):
        """Verifies that SafeModalScreen converts return and ctrl+m into enter events for bindings."""
        class TestModal(SafeModalScreen[bool]):
            BINDINGS = [
                Binding("enter", "confirm", "Confirm"),
            ]
            def __init__(self):
                super().__init__()
                self.confirmed = False

            def action_confirm(self):
                self.confirmed = True
                self.dismiss(True)

        class HostApp(App):
            def on_mount(self):
                self.push_screen(TestModal())

        app = HostApp()
        async with app.run_test() as pilot:
            await pilot.press("return")
            self.assertFalse(isinstance(app.screen, TestModal))

    def test_text_cursor_is_hidden_when_table_is_focused(self):
        """Verifies that the text cursor is suppressed/hidden and cursor_position is reset when navigating tables."""
        fake_app = SpoffTUI()
        mock_driver = Mock()
        fake_app._driver = mock_driver
        mock_table = Mock(spec=DataTable)
        mock_input = Mock(spec=Input)
        mock_input._cursor_visible = True
        fake_app.query = Mock(return_value=[mock_input])
        fake_app.cursor_position = Offset(10, 5)

        # Focus moved to table
        fake_app.on_descendant_focus(events.DescendantFocus(mock_table))

        mock_driver.write.assert_called_with("\x1b[?25l")
        self.assertEqual(fake_app.cursor_position, Offset(0, 0))
        mock_input._pause_blink.assert_called_once_with(visible=False)
        self.assertFalse(mock_input._cursor_visible)

        # Blur event on input
        mock_driver.reset_mock()
        mock_input._pause_blink.reset_mock()
        fake_app.cursor_position = Offset(12, 4)
        fake_app.on_descendant_blur(events.DescendantBlur(mock_input))

        mock_driver.write.assert_called_with("\x1b[?25l")
        self.assertEqual(fake_app.cursor_position, Offset(0, 0))
        mock_input._pause_blink.assert_called_once_with(visible=False)
        self.assertFalse(mock_input._cursor_visible)

    def test_post_display_hook_hides_terminal_cursor_when_not_input(self):
        """Verifies that post_display_hook writes hide cursor escape code when focused widget is not an Input."""
        from unittest.mock import PropertyMock
        fake_app = SpoffTUI()
        mock_driver = Mock()
        fake_app._driver = mock_driver
        mock_table = Mock(spec=DataTable)

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_table):
            fake_app.post_display_hook()
            mock_driver.write.assert_called_with("\x1b[?25l")

    def test_is_track_in_playlist_matches_id_and_casefolded_title(self):
        """Verifies that is_track_in_playlist correctly identifies tracks by ID or case-insensitive title/artist."""
        from spoff.storage import is_track_in_playlist

        pl = {
            "id": "pl1",
            "name": "My Playlist",
            "tracks": [
                {"id": "sp123", "title": "Karma Police", "artist": "Radiohead"},
                {"id": "yt456", "title": "Creep", "artist": "Radiohead"},
            ]
        }

        # Match by ID
        self.assertTrue(is_track_in_playlist(pl, {"id": "sp123", "title": "Different Title", "artist": "Different Artist"}))
        # Match by case-insensitive title and artist
        self.assertTrue(is_track_in_playlist(pl, {"id": "different_id", "title": "karma police", "artist": "radiohead"}))
        # Not in playlist
        self.assertFalse(is_track_in_playlist(pl, {"id": "other_id", "title": "No Surprises", "artist": "Radiohead"}))

    @patch("spoff.app.add_track_to_liked_songs", return_value=False)
    @patch("spoff.app.load_liked_songs", return_value=[])
    def test_duplicate_liked_song_displays_forced_notification(self, mock_load, mock_add):
        """Verifies that attempting to add a song already in Liked Songs notifies the user with force=True."""
        fake_app = SpoffTUI()
        fake_app.notify_user = Mock()
        fake_app.push_screen = Mock(side_effect=lambda modal, cb: cb(("liked", "liked_songs")))

        track = {"id": "t1", "title": "Bohemian Rhapsody", "artist": "Queen"}
        fake_app.prompt_add_track_to_playlist(track)

        fake_app.notify_user.assert_called_once_with("'Bohemian Rhapsody' is already in Liked Songs.", force=True)

    @patch("spoff.app.add_track_to_playlist", return_value=False)
    @patch("spoff.app.load_saved_playlists")
    def test_duplicate_playlist_track_displays_forced_notification(self, mock_load, mock_add):
        """Verifies that attempting to add a song already in a playlist notifies the user with playlist name and force=True."""
        fake_app = SpoffTUI()
        fake_app.notify_user = Mock()
        fake_app.refresh_side_table = Mock()
        fake_app.playlists = [{"id": "pl_rock", "name": "Classic Rock", "tracks": []}]
        mock_load.return_value = fake_app.playlists
        fake_app.push_screen = Mock(side_effect=lambda modal, cb: cb(("select", "pl_rock")))

        track = {"id": "t1", "title": "Bohemian Rhapsody", "artist": "Queen"}
        fake_app.prompt_add_track_to_playlist(track)

        fake_app.notify_user.assert_called_once_with("'Bohemian Rhapsody' is already in 'Classic Rock'.", force=True)

    def test_add_to_playlist_modal_hint_displays_notice_for_duplicate_track(self):
        """Verifies that AddToPlaylistModal updates the hint with a notice when selecting a playlist containing the song."""
        from spoff.app import AddToPlaylistModal
        track = {"id": "t1", "title": "Bohemian Rhapsody", "artist": "Queen"}
        playlists = [
            {"id": "pl1", "name": "Rock Legends", "tracks": [track]},
            {"id": "pl2", "name": "Chill Beats", "tracks": []},
        ]
        modal = AddToPlaylistModal(track, playlists)
        mock_hint = Mock(spec=Static)
        mock_table = Mock(spec=DataTable)
        mock_table.cursor_row = 1  # 0 is Liked Songs, 1 is Rock Legends

        modal.query_one = Mock(side_effect=lambda sel, *args: mock_hint if sel == "#modal-hint" else mock_table)
        modal.focused = mock_table

        modal._update_hint_for_selection()

        update_arg = mock_hint.update.call_args[0][0]
        self.assertIn("already track #1 in 'Rock Legends'", update_arg)


