import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spoff import storage
from spoff.streamer import download_track_to_cache
from spoff.app import SpoffTUI


class TestOfflineCacheReconciliation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE
        self.old_offline_index_file = storage.INDEX_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.INDEX_FILE = storage.DATA_DIR / "offline_index.json"

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        storage.INDEX_FILE = self.old_offline_index_file
        self.temp_dir.cleanup()

    def test_reconcile_missing_from_offline_index_matched_in_playlists(self):
        # Save a playlist with track metadata
        playlists = [{
            "id": "pl1",
            "name": "Favorites",
            "tracks": [
                {
                    "id": "track_alpha",
                    "title": "Midnight City",
                    "artist": "M83",
                    "album": "Hurry Up, We're Dreaming",
                    "duration_ms": 243000,
                }
            ]
        }]
        storage.save_saved_playlists(playlists)

        # Create physical cached audio file (> 10000 bytes)
        cached_file = storage.CACHE_DIR / "track_alpha.m4a"
        cached_file.write_bytes(b"A" * 15000)

        # Initially offline_index.json does not exist
        self.assertFalse(storage.INDEX_FILE.exists())

        # Load offline index -> should trigger self-healing reconciliation
        offline_tracks = storage.load_offline_index()

        self.assertIn("track_alpha", offline_tracks)
        track = offline_tracks["track_alpha"]
        self.assertEqual(track["title"], "Midnight City")
        self.assertEqual(track["artist"], "M83")
        self.assertEqual(track["album"], "Hurry Up, We're Dreaming")
        self.assertEqual(track["filepath"], str(cached_file.resolve()))

        # Check offline_index.json was persisted
        self.assertTrue(storage.INDEX_FILE.exists())

    def test_reconcile_missing_from_offline_index_matched_in_liked_songs(self):
        liked = [{
            "id": "track_beta",
            "title": "Get Lucky",
            "artist": "Daft Punk",
            "album": "Random Access Memories",
            "duration_ms": 248000,
        }]
        storage.save_liked_songs(liked)

        cached_file = storage.CACHE_DIR / "track_beta.mp3"
        cached_file.write_bytes(b"B" * 12000)

        offline_tracks = storage.load_offline_index()
        self.assertIn("track_beta", offline_tracks)
        self.assertEqual(offline_tracks["track_beta"]["title"], "Get Lucky")
        self.assertEqual(offline_tracks["track_beta"]["artist"], "Daft Punk")

    def test_reconcile_unmatched_cached_file_generates_fallback(self):
        cached_file = storage.CACHE_DIR / "track_gamma.m4a"
        cached_file.write_bytes(b"C" * 20000)

        offline_tracks = storage.load_offline_index()
        self.assertIn("track_gamma", offline_tracks)
        track = offline_tracks["track_gamma"]
        self.assertEqual(track["title"], "Offline Track (track_gamma)")
        self.assertEqual(track["artist"], "Offline Library")
        self.assertTrue(track["is_offline"])


    def test_reconcile_ignores_small_or_temporary_files(self):
        # Tiny file <= 10000 bytes
        tiny_file = storage.CACHE_DIR / "tiny.m4a"
        tiny_file.write_bytes(b"T" * 500)

        # Temp hidden file
        hidden_file = storage.CACHE_DIR / ".stage.m4a"
        hidden_file.write_bytes(b"H" * 25000)

        offline_tracks = storage.load_offline_index()
        self.assertNotIn("tiny", offline_tracks)
        self.assertNotIn(".stage", offline_tracks)

    def test_download_track_to_cache_registers_existing_file(self):
        cached_file = storage.CACHE_DIR / "existing_track.m4a"
        cached_file.write_bytes(b"E" * 16000)

        done_called = False
        def on_done(path):
            nonlocal done_called
            done_called = True

        track_meta = {
            "id": "existing_track",
            "title": "Existing Song",
            "artist": "Existing Artist",
        }

        download_track_to_cache(
            "existing_track",
            "Existing Song",
            "Existing Artist",
            on_complete=on_done,
            track_meta=track_meta,
            blocking=True,
        )

        self.assertTrue(done_called)
        index = storage.load_offline_index()
        self.assertIn("existing_track", index)
        self.assertEqual(index["existing_track"]["title"], "Existing Song")

    def test_app_start_playback_registers_existing_cache_file(self):
        cached_file = storage.CACHE_DIR / "cached_play.m4a"
        cached_file.write_bytes(b"P" * 15000)

        app = SpoffTUI()
        app.player = MagicMock()
        app._commit_playback = MagicMock()
        app.call_from_thread = MagicMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))

        track = {
            "id": "cached_play",
            "title": "Play Song",
            "artist": "Play Artist",
        }

        app._play_request_id = 42
        SpoffTUI.start_playback.__wrapped__(app, track, req_id=42)

        # offline index should now have cached_play
        index = storage.load_offline_index()
        self.assertIn("cached_play", index)
        self.assertEqual(index["cached_play"]["title"], "Play Song")


if __name__ == "__main__":
    unittest.main()
