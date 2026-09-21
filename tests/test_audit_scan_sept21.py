import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spoff import app, art, eq, player, storage, streamer


class TestAuditScanSept21(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self._orig_data = storage.DATA_DIR
        self._orig_cache = storage.CACHE_DIR
        self._orig_playlists = storage.PLAYLISTS_FILE
        self._orig_liked = storage.LIKED_SONGS_FILE
        self._orig_config = storage.CONFIG_FILE
        self._orig_index = storage.INDEX_FILE

        storage.DATA_DIR = self.tmp_path / "data"
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.INDEX_FILE = storage.DATA_DIR / "offline_index.json"

        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)

        storage.save_saved_playlists([])
        storage.save_liked_songs([])
        storage.save_offline_index({})

    def tearDown(self):
        storage.DATA_DIR = self._orig_data
        storage.CACHE_DIR = self._orig_cache
        storage.PLAYLISTS_FILE = self._orig_playlists
        storage.LIKED_SONGS_FILE = self._orig_liked
        storage.CONFIG_FILE = self._orig_config
        storage.INDEX_FILE = self._orig_index
        self._tmp.cleanup()

    # --- S1 Tests ---
    def test_s1_malformed_playlist_document_raises_and_creates_backup(self):
        # Non-list playlists document
        storage.PLAYLISTS_FILE.write_text(json.dumps({"not": "a_list"}), encoding="utf-8")
        corrupted_file = storage.PLAYLISTS_FILE.with_suffix(".json.corrupted")
        if corrupted_file.exists():
            corrupted_file.unlink()

        with self.assertRaises(ValueError):
            storage.load_saved_playlists()

        self.assertTrue(corrupted_file.exists())
        self.assertEqual(corrupted_file.read_text(encoding="utf-8"), '{"not": "a_list"}')

    def test_s1_legacy_liked_migration_aborts_on_malformed_tracks(self):
        # If spotify_liked_songs contains malformed tracks (e.g. tracks is not a list), abort migration
        legacy_data = [
            {"id": "spotify_liked_songs", "name": "Liked Songs", "tracks": "corrupted_string_not_list"},
            {"id": "pl_valid", "name": "My Playlist", "tracks": []}
        ]
        storage.PLAYLISTS_FILE.write_text(json.dumps(legacy_data), encoding="utf-8")
        
        pls = storage.load_saved_playlists()
        # Ensure migration was aborted: spotify_liked_songs is preserved and not silently deleted/overwritten
        pl_ids = [p["id"] for p in pls]
        self.assertIn("spotify_liked_songs", pl_ids)
        self.assertIn("pl_valid", pl_ids)

    # --- S2 Tests ---
    def test_s2_youtube_thumbnail_not_promoted_to_album_art_url(self):
        self.assertFalse(art.is_valid_album_art_url("https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"))
        self.assertFalse(art.is_valid_album_art_url("https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"))
        self.assertTrue(art.is_valid_album_art_url("https://is1-ssl.mzstatic.com/image/thumb/Music123/v4/cover.jpg"))

        track = {"id": "dQw4w9WgXcQ", "title": "Test Song", "artist": "Test Artist"}
        # Resolve track artwork with external providers returning nothing
        with patch("spoff.art.fetch_spotify_embed_art", return_value=(None, None)), \
             patch("spoff.art.fetch_deezer_art", return_value=(None, None)), \
             patch("spoff.art.fetch_itunes_art", return_value=None):
            res = art.resolve_track_artwork(track, timeout=1.0)
            self.assertIsNone(res.get("album_art_url"))
            self.assertIn("youtube", res.get("art_url", ""))

    # --- S3 Tests ---
    def test_s3_state_restoration_search_view(self):
        app_mock = types.SimpleNamespace(
            playlists=[{"id": "pl1", "name": "P1", "tracks": [{"id": "t1", "title": "T1", "artist": "A1"}]}],
            current_liked_tracks=[],
            switch_view=Mock(),
            load_playlist_by_index=Mock(),
            query_one=Mock(),
            render_tracks=Mock()
        )
        st_mock = Mock()
        tt_mock = Mock()
        app_mock.query_one.side_effect = lambda sel, *args, **kwargs: st_mock if "sidebar-table" in sel or "side-table" in sel else tt_mock

        # When saved_tab is search
        with patch("spoff.app.get_saved_last_played", return_value={"tab": "search", "playlist_id": "pl1"}):
            app.SpoffTUI._restore_last_view_state(app_mock)
            app_mock.switch_view.assert_called_with("search")
            app_mock.load_playlist_by_index.assert_not_called()

    def test_s3_state_restoration_unknown_playlist_id(self):
        app_mock = types.SimpleNamespace(
            playlists=[{"id": "pl1", "name": "P1", "tracks": [{"id": "t1", "title": "T1", "artist": "A1"}]}],
            current_liked_tracks=[],
            switch_view=Mock(),
            load_playlist_by_index=Mock(),
            query_one=Mock(),
            render_tracks=Mock()
        )
        st_mock = Mock()
        tt_mock = Mock()
        app_mock.query_one.side_effect = lambda sel, *args, **kwargs: st_mock if "sidebar-table" in sel or "side-table" in sel else tt_mock

        # When saved_tab is playlist but saved_pid is unknown
        with patch("spoff.app.get_saved_last_played", return_value={"tab": "playlist", "playlist_id": "nonexistent_pl", "track_index": 5}):
            app.SpoffTUI._restore_last_view_state(app_mock)
            # Should safely load playlist 0 without using the foreign track index 5
            app_mock.load_playlist_by_index.assert_called_once_with(0, focus_tracks=False)

    # --- S5 Tests ---
    def test_s5_cache_dir_dynamic_resolution(self):
        # Redirect DATA_DIR and ensure delete_cached_track / quarantine_cached_track look in redirected location
        new_data_dir = self.tmp_path / "custom_data"
        new_cache_dir = new_data_dir / "cache"
        new_cache_dir.mkdir(parents=True, exist_ok=True)
        storage.DATA_DIR = new_data_dir
        storage.CACHE_DIR = storage.DATA_DIR / "cache"

        test_track_id = "test_track_123"
        audio_file = new_cache_dir / f"{test_track_id}.m4a"
        audio_file.write_bytes(b"dummy audio content")

        storage.save_offline_index({test_track_id: {"title": "Test", "artist": "Artist"}})

        # Quarantine
        success = storage.quarantine_cached_track(test_track_id, source=audio_file)
        self.assertTrue(success)
        self.assertFalse(audio_file.exists())
        self.assertIsNone(storage.get_cached_track_path(test_track_id))

        # Delete
        audio_file2 = new_cache_dir / f"{test_track_id}.mp3"
        audio_file2.write_bytes(b"dummy mp3 content")
        self.assertTrue(audio_file2.exists())
        del_success = storage.delete_cached_track(test_track_id)
        self.assertTrue(del_success)
        self.assertFalse(audio_file2.exists())

    # --- S6 Tests ---
    def test_s6_load_offline_index_backs_up_non_dict(self):
        storage.INDEX_FILE.write_text(json.dumps(["item1", "item2"]), encoding="utf-8")
        corrupted_file = storage.INDEX_FILE.with_suffix(".json.corrupted")
        if corrupted_file.exists():
            corrupted_file.unlink()

        idx = storage.load_offline_index()
        self.assertEqual(idx, {})
        self.assertTrue(corrupted_file.exists())
        self.assertEqual(corrupted_file.read_text(encoding="utf-8"), '["item1", "item2"]')

    # --- S7 Tests ---
    def test_s7_anti_denormal_engine_toggle(self):
        engine = eq.ParametricEQEngine()
        self.assertTrue(engine.anti_denormal)
        engine.set_anti_denormal(False)
        self.assertFalse(engine.anti_denormal)
        engine.set_anti_denormal(True)
        self.assertTrue(engine.anti_denormal)

    # --- S8 Tests ---
    def test_s8_preexec_deathsig_callable(self):
        # Should execute without throwing an unhandled exception
        player._preexec_deathsig()


if __name__ == "__main__":
    unittest.main()
