import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spoff import storage, mpris, player
from spoff.app import SpoffTUI


class TestFlawlessBugScanFixes(unittest.TestCase):
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

    def test_mpris_update_track_with_none_artist_and_title(self):
        """Verify update_track handles None artist and title without AttributeError."""
        mock_mpris = Mock(spec=mpris.MPRISService)
        mock_mpris.dbus_obj = Mock()
        mock_mpris._emit_changed = Mock()
        mock_mpris._last_track = None

        track_with_nones = {
            "id": "track_123",
            "title": None,
            "artist": None,
            "duration_ms": 180000,
        }

        # Call unbound method to exercise actual logic
        mpris.MPRISService.update_track(mock_mpris, track_with_nones, duration_sec=180.0)
        self.assertTrue(mock_mpris._emit_changed.called)
        metadata = mock_mpris.dbus_obj.Metadata
        self.assertIn("xesam:title", metadata)
        self.assertIn("xesam:artist", metadata)

    def test_player_stop_exception_in_close_socket_resilience(self):
        """Verify player stop() initializes playback_to_join safely even if socket close raises."""
        p = player.MPVController()
        # Mock _close_playback_socket to raise an error
        with patch.object(p, "_close_playback_socket", side_effect=RuntimeError("Socket error")):
            try:
                p.stop()
            except RuntimeError:
                pass
            except UnboundLocalError:
                self.fail("player.stop() raised UnboundLocalError!")

    def test_storage_add_track_to_playlist_normalization(self):
        """Verify add_track_to_playlist normalizes track schema and rejects invalid track objects."""
        storage.save_saved_playlists([
            {"id": "pl_test", "name": "Test PL", "tracks": []}
        ])

        # Reject malformed tracks and invalid playlist IDs
        self.assertFalse(storage.add_track_to_playlist("pl_test", None))  # type: ignore
        self.assertFalse(storage.add_track_to_playlist("pl_test", "not_a_dict"))  # type: ignore
        self.assertFalse(storage.add_track_to_playlist("", {"title": "Song", "artist": "Artist"}))

        # Accept valid track and ensure normalized fields
        valid_track = {
            "id": "valid_1",
            "title": "Normalized Track",
            "artist": "Artist Name",
            "duration_ms": 200000,
            "url": "https://www.youtube.com/watch?v=12345678901",
        }
        added = storage.add_track_to_playlist("pl_test", valid_track)
        self.assertTrue(added)

        pls = storage.load_saved_playlists()
        self.assertEqual(len(pls[0]["tracks"]), 1)
        self.assertEqual(pls[0]["tracks"][0]["title"], "Normalized Track")

    def test_app_playback_failed_empty_queue_safety(self):
        """Verify _playback_failed cleanly stops without ZeroDivisionError when queue is empty."""
        app = SpoffTUI()
        app.queue = []
        app.current_index = -1
        app._play_request_id = 42
        app.player = Mock()
        app.mpris = Mock()
        app.notify_user = Mock()
        app.update_player_hud = Mock()

        # Should not raise ZeroDivisionError
        app._playback_failed(42, {"title": "Failed Song", "artist": "Failed Artist"})
        self.assertEqual(app.current_index, -1)
        self.assertTrue(app.player.stop.called)


if __name__ == "__main__":
    unittest.main()
