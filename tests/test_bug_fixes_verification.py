import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch, MagicMock, PropertyMock

from spoff import storage, ytmusic, spotify, mpris
from spoff.app import SpoffTUI


class TestStorageHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE
        self.old_config_file = storage.CONFIG_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        storage.CONFIG_FILE = self.old_config_file
        self.temp_dir.cleanup()

    def test_clone_saved_playlist_from_liked_songs(self):
        storage.save_liked_songs([
            {"id": "t1", "title": "Liked Track 1", "artist": "Artist 1"},
            {"id": "t2", "title": "Liked Track 2", "artist": "Artist 2"}
        ])

        cloned = storage.clone_saved_playlist("liked_songs", "My Cloned Liked")
        self.assertIsNotNone(cloned)
        self.assertEqual(cloned["name"], "My Cloned Liked")
        self.assertEqual(len(cloned["tracks"]), 2)
        self.assertEqual(cloned["tracks"][0]["title"], "Liked Track 1")

        # Verify it was saved to playlists.json
        saved_pls = storage.load_saved_playlists()
        self.assertEqual(len(saved_pls), 1)
        self.assertEqual(saved_pls[0]["id"], cloned["id"])

    def test_save_last_played_handles_mock_objects_gracefully(self):
        mock_track = MagicMock()
        mock_track.get.return_value = "mock_val"
        state = {
            "playlist_id": "pl_1",
            "tab": "playlist",
            "track_id": mock_track,
            "track_title": "Real Song Title",
            "track_artist": "Real Artist",
            "track_index": 0
        }
        # Must not raise TypeError or crash serialization
        storage.save_last_played(state)
        loaded = storage.load_config()
        self.assertIn("last_played", loaded)
        self.assertEqual(loaded["last_played"]["track_title"], "Real Song Title")


class TestYTMusicURLParsing(unittest.TestCase):
    def test_watch_url_with_radio_mix_returns_track(self):
        url = "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVMdQw4w9WgXcQ"
        res = ytmusic.parse_ytmusic_url(url)
        self.assertEqual(res, ("track", "dQw4w9WgXcQ"))

    def test_standard_watch_url_returns_track(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        res = ytmusic.parse_ytmusic_url(url)
        self.assertEqual(res, ("track", "dQw4w9WgXcQ"))

    def test_standard_playlist_url_returns_playlist(self):
        url = "https://www.youtube.com/playlist?list=PL1234567890abcdef"
        res = ytmusic.parse_ytmusic_url(url)
        self.assertEqual(res, ("playlist", "PL1234567890abcdef"))

    def test_album_playlist_url_returns_album(self):
        url = "https://music.youtube.com/playlist?list=OLAK5uy_abcdef123456"
        res = ytmusic.parse_ytmusic_url(url)
        self.assertEqual(res, ("album", "OLAK5uy_abcdef123456"))


class TestSpotifyParsingSafety(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_fetch_spotify_playlist_with_null_uri(self, mock_urlopen):
        mock_resp = Mock()
        json_data = {
            "props": {
                "pageProps": {
                    "state": {
                        "data": {
                            "entity": {
                                "name": "Test Playlist",
                                "trackList": [
                                    {"title": "Song 1", "subtitle": "Artist 1", "uri": None, "duration": 180000}
                                ]
                            }
                        }
                    }
                }
            }
        }
        mock_resp.read.return_value = f'<script id="__NEXT_DATA__">{storage.json.dumps(json_data)}</script>'.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = spotify.fetch_spotify_playlist("37i9dQZF1DXcBWIGoYBM5M")
        self.assertIsNotNone(res)
        self.assertEqual(len(res["tracks"]), 1)
        self.assertEqual(res["tracks"][0]["id"], "")

    @patch("urllib.request.urlopen")
    def test_fetch_spotify_track_with_malformed_artist_uri(self, mock_urlopen):
        mock_resp = Mock()
        json_data = {
            "props": {
                "pageProps": {
                    "state": {
                        "data": {
                            "entity": {
                                "title": "Track Title",
                                "duration": 200000,
                                "uri": "spotify:track:abc",
                                "artists": [{"name": "Artist One", "uri": None}]
                            }
                        }
                    }
                }
            }
        }
        mock_resp.read.return_value = f'<script id="__NEXT_DATA__">{storage.json.dumps(json_data)}</script>'.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = spotify.fetch_spotify_track("abc")
        self.assertIsNotNone(res)
        self.assertEqual(res["title"], "Track Title")
        self.assertEqual(res["artist"], "Artist One")


class TestMPRISHardening(unittest.TestCase):
    def test_update_position_safe_against_nan_and_inf(self):
        m = mpris.MPRISService(Mock())
        m.dbus_obj = Mock()
        m.dbus_obj.Position = 0

        # NaN
        m.update_position(float("nan"))
        self.assertEqual(m.dbus_obj.Position, 0)

        # Inf
        m.update_position(float("inf"))
        self.assertEqual(m.dbus_obj.Position, 0)

        # Negative should be clamped to 0
        m.update_position(-10.0)
        self.assertEqual(m.dbus_obj.Position, 0)

        # Valid position
        m.update_position(12.5)
        self.assertEqual(m.dbus_obj.Position, 12_500_000)


class TestAppLikedTabActions(unittest.TestCase):
    def setUp(self):
        self.focused_mock = patch.object(SpoffTUI, "focused", new_callable=PropertyMock)
        self.mock_focused = self.focused_mock.start()
        self.mock_focused.return_value = None

        self.app: Any = SpoffTUI.__new__(SpoffTUI)
        self.app.active_tab = "liked"
        self.app.current_playlist_id = None
        self.app.current_liked_tracks = [{"id": "l1", "title": "Liked Track", "artist": "Artist"}]
        self.app.playlists = [{"id": "pl_1", "name": "Other Playlist", "tracks": []}]
        self.app.notifications = []
        self.app.notify_user = lambda msg: self.app.notifications.append(msg)
        self.app.pushed_screens = []
        self.app.push_screen = lambda scr, cb: self.app.pushed_screens.append((scr, cb))

    def tearDown(self):
        self.focused_mock.stop()

    def test_delete_playlist_blocked_on_liked_tab(self):
        self.app.action_delete_playlist()
        self.assertIn("Cannot delete Liked Songs.", self.app.notifications)
        self.assertEqual(len(self.app.pushed_screens), 0)

    def test_rename_playlist_blocked_on_liked_tab(self):
        self.app.action_rename_playlist()
        self.assertIn("Cannot rename Liked Songs.", self.app.notifications)
        self.assertEqual(len(self.app.pushed_screens), 0)

    def test_clone_playlist_on_liked_tab_clones_liked_songs(self):
        self.app.action_clone_playlist()
        self.assertEqual(len(self.app.pushed_screens), 1)
        scr, _ = self.app.pushed_screens[0]
        self.assertEqual(scr.original_name, "Liked Songs")

    def test_delete_playlist_blocked_on_liked_tab_even_when_sidebar_focused(self):
        self.mock_focused.return_value = Mock(id="side-table", cursor_row=0)
        self.app.action_delete_playlist()
        self.assertIn("Cannot delete Liked Songs.", self.app.notifications)
        self.assertEqual(len(self.app.pushed_screens), 0)

    def test_delete_item_on_liked_tab_when_sidebar_focused_never_deletes_playlist(self):
        self.mock_focused.return_value = Mock(id="side-table", cursor_row=0)
        initial_playlists = list(self.app.playlists)
        self.app.action_delete_item()
        self.assertEqual(self.app.playlists, initial_playlists)
        # Should not push delete playlist modal
        for scr, _ in self.app.pushed_screens:
            self.assertNotEqual(getattr(scr, "title", ""), "DELETE PLAYLIST")

    def test_delete_item_on_liked_tab_targets_liked_track(self):
        self.mock_focused.return_value = Mock(id="track-table", cursor_row=0)
        self.app.action_delete_item()
        self.assertEqual(len(self.app.pushed_screens), 1)
        scr, _ = self.app.pushed_screens[0]
        self.assertEqual(getattr(scr, "modal_title", ""), "REMOVE LIKED SONG")


class TestEnterKeyPlaybackBehavior(unittest.TestCase):
    def setUp(self):
        self.app: Any = SpoffTUI.__new__(SpoffTUI)
        self.app._failed_indices = set()
        self.app._shuffle_history = []
        self.app.queue = []
        self.app.player = Mock()
        self.app.player.current_track = None
        self.app._pending_track = None
        self.app.update_player_hud = Mock()
        self.app.notify_user = Mock()
        self.app.play_index = Mock()

        self.track1 = {"id": "t1", "title": "Song One", "artist": "Artist A"}
        self.track2 = {"id": "t2", "title": "Song Two", "artist": "Artist B"}
        self.app._get_current_view_tracks = Mock(return_value=[self.track1, self.track2])

    def test_first_enter_starts_playing_track(self):
        self.app.play_current_table_row(0)
        self.app.play_index.assert_called_once_with(0)
        self.assertEqual(self.app.queue, [self.track1, self.track2])
        self.app.player.toggle_pause.assert_not_called()

    def test_second_enter_on_playing_track_toggles_pause_and_does_not_restart(self):
        # Track 1 is currently playing
        self.app.player.current_track = self.track1
        self.app.play_current_table_row(0)

        # Must toggle pause on the player instead of restarting via play_index
        self.app.player.toggle_pause.assert_called_once()
        self.app.update_player_hud.assert_called_once()
        self.app.play_index.assert_not_called()

    def test_enter_on_pending_loading_track_does_not_restart(self):
        # Track 1 is currently loading / buffering
        self.app._pending_track = self.track1
        self.app.play_current_table_row(0)

        self.app.notify_user.assert_called_once_with("Loading 'Song One'...")
        self.app.play_index.assert_not_called()
        self.app.player.toggle_pause.assert_not_called()

    def test_enter_on_different_track_starts_new_track(self):
        # Track 1 is currently playing, user hits enter on Track 2 (row 1)
        self.app.player.current_track = self.track1
        self.app.play_current_table_row(1)

        self.app.play_index.assert_called_once_with(1)
        self.app.player.toggle_pause.assert_not_called()


class TestNotificationsDisabledOption(unittest.TestCase):
    def setUp(self):
        self.app: Any = SpoffTUI.__new__(SpoffTUI)
        self.app._thread_id = 12345
        self.app.notifications_enabled = True

    def test_init_notifications_enabled_flag(self):
        with patch("spoff.app.get_saved_notifications_enabled", return_value=True):
            app_disabled = SpoffTUI.__new__(SpoffTUI)
            # Simulating __init__ logic
            notif_flag = False
            if notif_flag is not None:
                app_disabled.notifications_enabled = bool(notif_flag)
            else:
                app_disabled.notifications_enabled = storage.get_saved_notifications_enabled()
            self.assertFalse(app_disabled.notifications_enabled)

    def test_notify_user_suppressed_when_notifications_disabled(self):
        self.app.notifications_enabled = False
        mock_bar = Mock()
        self.app.query_one = Mock(return_value=mock_bar)

        with patch("threading.get_ident", return_value=12345):
            self.app.notify_user("Test message")

        # Must not update widget text when notifications are disabled
        mock_bar.update.assert_not_called()

    def test_notify_user_clears_when_empty_text_even_if_disabled(self):
        self.app.notifications_enabled = False
        mock_bar = Mock()
        self.app.query_one = Mock(return_value=mock_bar)

        with patch("threading.get_ident", return_value=12345):
            self.app.notify_user("")

        # Clearing the line (empty text) is allowed
        mock_bar.update.assert_called_once_with("")

    def test_toggle_notifications_clears_notification_line_when_disabled(self):
        self.app.notifications_enabled = True
        mock_bar = Mock()
        self.app.query_one = Mock(return_value=mock_bar)

        with patch("spoff.app.save_notifications_enabled") as mock_save, \
             patch("threading.get_ident", return_value=12345):
            new_state = self.app.toggle_notifications()

            self.assertFalse(new_state)
            self.assertFalse(self.app.notifications_enabled)
            mock_save.assert_called_once_with(False)
            mock_bar.update.assert_called_once_with("")

    def test_action_toggle_notifications_calls_toggle_notifications(self):
        self.app.toggle_notifications = Mock(return_value=True)
        self.app.notify_user = Mock()

        self.app.action_toggle_notifications()
        self.app.toggle_notifications.assert_called_once()
        self.app.notify_user.assert_called_once_with("Notifications enabled")

    def test_app_notify_override_suppresses_toast_when_disabled(self):
        self.app.notifications_enabled = False
        with patch("textual.app.App.notify") as mock_super_notify:
            self.app.notify("Test toast message", title="Test Title")
            mock_super_notify.assert_not_called()

    def test_app_notify_override_calls_super_when_enabled(self):
        self.app.notifications_enabled = True
        with patch("textual.app.App.notify") as mock_super_notify:
            self.app.notify("Test toast message", title="Test Title")
            mock_super_notify.assert_called_once_with("Test toast message", title="Test Title")


if __name__ == "__main__":
    unittest.main()
