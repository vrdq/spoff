import unittest
from unittest.mock import Mock, patch, MagicMock, PropertyMock
from textual.widgets import DataTable

from spoff.player import MPVController
from spoff.app import SpoffTUI

class TestPlaybackSelection(unittest.TestCase):
    def test_mpv_controller_pause_and_resume(self):
        ctrl = MPVController(socket_path="/tmp/test_sock.sock")
        ctrl._send_command = Mock(return_value=True)

        ctrl.pause()
        self.assertTrue(ctrl.is_paused)
        ctrl._send_command.assert_called_with(["set_property", "pause", True])

        ctrl.resume()
        self.assertFalse(ctrl.is_paused)
        ctrl._send_command.assert_called_with(["set_property", "pause", False])

    def test_is_same_track(self):
        app = SpoffTUI.__new__(SpoffTUI)

        t1 = {"id": "song1", "title": "Track One", "artist": "Artist A"}
        t2 = {"id": "song1", "title": "Other Title", "artist": "Other Artist"}
        t3 = {"id": "song2", "title": "Track One", "artist": "Artist A"}
        t4 = {"id": "song3", "title": "track one", "artist": "artist a"}
        t5 = {"id": "song4", "title": "Track Two", "artist": "Artist A"}

        # Same object
        self.assertTrue(app._is_same_track(t1, t1))
        # Same ID
        self.assertTrue(app._is_same_track(t1, t2))
        # Same Title and Artist (case insensitive)
        self.assertTrue(app._is_same_track(t1, t3))
        self.assertTrue(app._is_same_track(t1, t4))
        # Different Title
        self.assertFalse(app._is_same_track(t1, t5))
        # None values
        self.assertFalse(app._is_same_track(None, t1))
        self.assertFalse(app._is_same_track(t1, None))

    def test_space_plays_highlighted_search_track_when_different(self):
        app = SpoffTUI.__new__(SpoffTUI)
        app.player = Mock()
        app.player.current_track = {"id": "old_playlist_track", "title": "Old Song", "artist": "Artist 1"}
        app._pending_track = None
        app.update_player_hud = Mock()
        app.play_current_table_row = Mock()

        # Mock focused DataTable
        mock_table = MagicMock(spec=DataTable)
        mock_table.id = "track-table"
        mock_table.cursor_row = 1

        app.search_results = [
            {"id": "s1", "title": "First Result", "artist": "Artist A"},
            {"id": "s2", "title": "Second Result", "artist": "Artist B"},
        ]
        app.active_tab = "search"

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_table):
            # When Space is pressed on row 1 (which is different from old_playlist_track)
            app.action_toggle_play()

        # It must call play_current_table_row(1) and NOT player.toggle_pause()
        app.play_current_table_row.assert_called_once_with(1)
        app.player.toggle_pause.assert_not_called()

    def test_space_toggles_pause_when_highlighted_track_is_currently_playing(self):
        app = SpoffTUI.__new__(SpoffTUI)
        app.player = Mock()
        current = {"id": "s1", "title": "First Result", "artist": "Artist A"}
        app.player.current_track = current
        app._pending_track = None
        app.update_player_hud = Mock()
        app.play_current_table_row = Mock()

        # Mock focused DataTable on the same track
        mock_table = MagicMock(spec=DataTable)
        mock_table.id = "track-table"
        mock_table.cursor_row = 0

        app.search_results = [current]
        app.active_tab = "search"

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_table):
            # When Space is pressed on row 0 (which is the current track)
            app.action_toggle_play()

        # It must toggle pause, NOT re-trigger play_current_table_row
        app.player.toggle_pause.assert_called_once()
        app.play_current_table_row.assert_not_called()

    def test_space_does_not_unpause_stale_track_while_new_track_is_pending(self):
        app = SpoffTUI.__new__(SpoffTUI)
        app.player = Mock()
        app.player.current_track = {"id": "old_song", "title": "Old Song"}
        app._pending_track = {"id": "new_song", "title": "New Song"}
        app.notify_user = Mock()
        app.update_player_hud = Mock()
        app.play_current_table_row = Mock()

        # Focus is on track-table on the pending track
        mock_table = MagicMock(spec=DataTable)
        mock_table.id = "track-table"
        mock_table.cursor_row = 0

        app.search_results = [{"id": "new_song", "title": "New Song"}]
        app.active_tab = "search"

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_table):
            app.action_toggle_play()

        # Must not unpause the old song or retrigger playback
        app.player.toggle_pause.assert_not_called()
        app.play_current_table_row.assert_not_called()
        app.notify_user.assert_called_with("Loading 'New Song'...")

    def test_play_index_pauses_previous_player_and_sets_pending_track(self):
        app = SpoffTUI.__new__(SpoffTUI)
        app.player = Mock()
        app.player.current_track = {"id": "song_a", "title": "Song A"}
        app.player.pause = Mock()
        app.queue = [{"id": "song_b", "title": "Song B"}]
        app.current_index = -1
        app._failed_indices = set()
        app._sync_table_cursor_to_index = Mock()
        app._play_request_id = 0
        app.start_playback = Mock()

        app.play_index(0)

        self.assertEqual(app._pending_track, {"id": "song_b", "title": "Song B"})
        app.player.pause.assert_called_once()
        app.start_playback.assert_called_once()

    def test_commit_playback_clears_pending_track(self):
        app = SpoffTUI.__new__(SpoffTUI)
        app.player = Mock()
        app.player.load_and_play = Mock(return_value=True)
        app._play_request_id = 1
        app._closing = False
        app._pending_track = {"id": "song_b", "title": "Song B"}
        app.notify_user = Mock()
        app.mpris = None
        app.update_player_hud = Mock()
        app._save_playback_state = Mock()

        success = app._commit_playback(1, "http://stream.url", {"id": "song_b", "title": "Song B"})
        self.assertTrue(success)
        self.assertIsNone(app._pending_track)


if __name__ == "__main__":
    unittest.main()
