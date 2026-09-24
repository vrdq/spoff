import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, PropertyMock
from textual.widgets import DataTable

from spoff import auth, storage
from spoff.app import SpoffTUI


class TestSpotifyLikedSongsSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE
        self.old_config_file = storage.CONFIG_FILE
        self.old_auth_file = auth.AUTH_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        auth.AUTH_FILE = storage.DATA_DIR / "spotify_auth.json"

        storage.save_liked_songs([])

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        storage.CONFIG_FILE = self.old_config_file
        auth.AUTH_FILE = self.old_auth_file
        self.temp_dir.cleanup()

    def test_resolve_spotify_track_info_cached_id(self):
        """Spotify tracks with a known ID resolve immediately; YouTube Music tracks never resolve."""
        sp_track = {"id": "local_x", "title": "Song", "artist": "A", "source": "spotify",
                    "spotify_id": "4cOdK2wGLETKBW3PvgPWqT"}
        self.assertEqual(auth.resolve_spotify_track_info(sp_track),
                         ("4cOdK2wGLETKBW3PvgPWqT", "spotify:track:4cOdK2wGLETKBW3PvgPWqT"))
        yt_track = dict(sp_track, id="dQw4w9WgXcQ", source="ytmusic")
        self.assertIsNone(auth.resolve_spotify_track_info(yt_track))
    def test_resolve_spotify_track_info_via_search(self):
        """YouTube Music tracks are never matched against Spotify search."""
        track = {
            "id": "dQw4w9WgXcQ",
            "title": "As the World Caves In",
            "artist": "Matt Maltese",
            "source": "ytmusic",
        }
        with patch.object(auth, "search_spotify_track") as mock_search:
            self.assertIsNone(auth.resolve_spotify_track_info(track, token="valid_token"))
            mock_search.assert_not_called()
        self.assertNotIn("spotify_id", track)
    def test_add_client_side_track_to_spotify_liked_songs(self):
        """Liking a YouTube Music track stays local and never calls the Spotify API."""
        yt_track = {
            "id": "dQw4w9WgXcQ",
            "title": "As the World Caves In",
            "artist": "Matt Maltese",
            "source": "ytmusic",
        }
        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "search_spotify_track") as mock_search, \
             patch.object(auth, "spotify_api_request") as mock_api:
            ok, _ = auth.add_track_to_spotify_account("liked", "Liked Songs", yt_track, token="valid_token")
            self.assertFalse(ok)
            mock_search.assert_not_called()
            mock_api.assert_not_called()
    def test_remove_client_side_track_from_spotify_liked_songs(self):
        """Unliking a YouTube Music track never removes anything from Spotify."""
        yt_track = {
            "id": "dQw4w9WgXcQ",
            "title": "As the World Caves In",
            "artist": "Matt Maltese",
            "source": "ytmusic",
            "spotify_id": "0v1XpBHm95PZTGmsyQe69r",
        }
        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request") as mock_api:
            ok, _ = auth.remove_track_from_spotify_account("liked", "Liked Songs", yt_track, token="valid_token")
            self.assertFalse(ok)
            mock_api.assert_not_called()
    def test_merge_spotify_and_client_tracks_deduplicates_counterpart(self):
        """When merging remote Spotify liked tracks with existing local client tracks, counterpart tracks are not duplicated."""
        existing_client = [
            {
                "id": "dQw4w9WgXcQ",
                "title": "As the World Caves In",
                "artist": "Matt Maltese",
                "source": "ytmusic",
                "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
                "spotify_id": "0v1XpBHm95PZTGmsyQe69r",
            },
            {
                "id": "local_track_only",
                "title": "Indie Offline Song",
                "artist": "Local Band",
                "source": "local",
            }
        ]
        spotify_remote = [
            {
                "id": "0v1XpBHm95PZTGmsyQe69r",
                "title": "As the World Caves In",
                "artist": "Matt Maltese",
                "source": "spotify",
                "uri": "spotify:track:0v1XpBHm95PZTGmsyQe69r",
            }
        ]
        merged = auth.merge_spotify_and_client_tracks(spotify_remote, existing_client)
        # Should have exactly 2 tracks, NOT 3 (the YouTube counterpart was merged into the Spotify track)
        self.assertEqual(len(merged), 2)
        # The matched track contains the Spotify ID and preserves the YouTube URL
        matched_st = next(t for t in merged if t["id"] == "0v1XpBHm95PZTGmsyQe69r")
        self.assertEqual(matched_st["url"], "https://youtube.com/watch?v=dQw4w9WgXcQ")
        # The offline local track is preserved
        self.assertTrue(any(t["id"] == "local_track_only" for t in merged))

    def test_app_action_like_track_triggers_spotify_sync(self):
        """action_like_track in SpoffApp calls add_track_to_spotify_account for ytmusic tracks and updates storage."""
        app = SpoffTUI()
        app._is_ready = True
        app.active_tab = "search"
        yt_track = {
            "id": "abc12345678",
            "title": "Search Song",
            "artist": "Search Artist",
            "source": "ytmusic",
        }
        app.search_results = [yt_track]

        mock_table = Mock(spec=DataTable)
        mock_table.id = "track-table"
        mock_table.cursor_row = 0

        submitted_jobs = []
        def fake_submit(job):
            submitted_jobs.append(job)
            job()

        app._submit_spotify_job = fake_submit
        app.query_one = Mock(return_value=mock_table)

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_table), \
             patch("spoff.app.add_track_to_spotify_account") as mock_add_sp:
            def fake_add(pl_id, pl_name, track):
                track["spotify_id"] = "resolved_sp_id_12345678"
                return True, "Synced to Spotify Liked Songs"
            mock_add_sp.side_effect = fake_add

            app.action_like_track()

            mock_add_sp.assert_called_once_with("liked", "Liked Songs", yt_track)
            self.assertTrue(storage.is_track_liked(yt_track))
            saved = storage.load_liked_songs()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].get("spotify_id"), "resolved_sp_id_12345678")

    def test_app_action_unlike_track_triggers_spotify_removal(self):
        """action_like_track on already-liked ytmusic track calls remove_track_from_spotify_account."""
        app = SpoffTUI()
        app._is_ready = True
        app.active_tab = "liked"
        yt_track = {
            "id": "abc12345678",
            "title": "Liked Song",
            "artist": "Liked Artist",
            "source": "ytmusic",
            "spotify_id": "resolved_sp_id_12345678",
            "duration_ms": 0,
        }
        storage.add_track_to_liked_songs(yt_track)
        app.current_liked_tracks = storage.load_liked_songs()

        mock_table = Mock(spec=DataTable)
        mock_table.id = "track-table"
        mock_table.cursor_row = 0

        submitted_jobs = []
        def fake_submit(job):
            submitted_jobs.append(job)
            job()

        app._submit_spotify_job = fake_submit
        app.query_one = Mock(return_value=mock_table)

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_table), \
             patch("spoff.app.remove_track_from_spotify_account", return_value=(True, "Removed from Spotify Liked Songs")) as mock_rm_sp:
            app.action_like_track()
            mock_rm_sp.assert_called_once_with("liked", "Liked Songs", yt_track)
            self.assertFalse(storage.is_track_liked(yt_track))


if __name__ == "__main__":
    unittest.main()
