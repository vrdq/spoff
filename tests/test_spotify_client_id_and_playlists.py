import unittest
from unittest.mock import patch
import os
import tempfile
from pathlib import Path

from spoff import auth, storage


class TestSpotifyClientIdAndPlaylists(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_data_dir = storage.DATA_DIR
        self.orig_auth_file = auth.AUTH_FILE
        self.orig_config_file = storage.CONFIG_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        auth.AUTH_FILE = storage.DATA_DIR / "spotify_auth.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        self.orig_env = os.environ.get("SPOTIFY_CLIENT_ID")
        if "SPOTIFY_CLIENT_ID" in os.environ:
            del os.environ["SPOTIFY_CLIENT_ID"]

    def tearDown(self):
        if self.orig_env is not None:
            os.environ["SPOTIFY_CLIENT_ID"] = self.orig_env
        elif "SPOTIFY_CLIENT_ID" in os.environ:
            del os.environ["SPOTIFY_CLIENT_ID"]
        storage.DATA_DIR = self.orig_data_dir
        auth.AUTH_FILE = self.orig_auth_file
        storage.CONFIG_FILE = self.orig_config_file
        self.temp_dir.cleanup()

    def test_default_client_id(self):
        self.assertEqual(auth.get_spotify_client_id(), auth.SPOTIFY_CLIENT_ID)
        self.assertEqual(auth.get_saved_custom_client_id(), "")

    def test_env_client_id(self):
        custom_id = "test_env_client_id_12345"
        os.environ["SPOTIFY_CLIENT_ID"] = custom_id
        self.assertEqual(auth.get_spotify_client_id(), custom_id)
        self.assertEqual(auth.get_saved_custom_client_id(), custom_id)

    def test_config_client_id(self):
        custom_id = "test_config_client_id_67890"
        auth.save_spotify_client_id(custom_id)
        self.assertEqual(auth.get_spotify_client_id(), custom_id)
        self.assertEqual(auth.get_saved_custom_client_id(), custom_id)

    def test_auth_session_client_id_priority(self):
        auth.save_spotify_client_id("config_id")
        auth.save_spotify_auth({"access_token": "acc", "client_id": "session_id"})
        self.assertEqual(auth.get_spotify_client_id(), "session_id")
        self.assertEqual(auth.get_saved_custom_client_id(), "session_id")

    def test_build_auth_url_custom_client_id(self):
        custom_id = "custom_client_id_abc"
        url, state = auth.build_auth_url("test_verifier", client_id=custom_id)
        self.assertIn(f"client_id={custom_id}", url)

        auth.save_spotify_client_id(custom_id)
        url_auto, _ = auth.build_auth_url("test_verifier")
        self.assertIn(f"client_id={custom_id}", url_auto)

    def test_exchange_code_custom_client_id(self):
        custom_id = "custom_client_id_exchange"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = b'{"access_token": "tok123", "refresh_token": "ref123", "expires_in": 3600}'
            tokens = auth.exchange_code_for_tokens("test_code", "test_ver", client_id=custom_id)
            self.assertIsNotNone(tokens)
            self.assertEqual(tokens.get("client_id"), custom_id)
            self.assertEqual(tokens.get("access_token"), "tok123")

    def test_refresh_token_custom_client_id(self):
        custom_id = "custom_client_id_refresh"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = b'{"access_token": "tok_refreshed", "expires_in": 3600}'
            tokens = auth.refresh_spotify_token("test_refresh_tok", client_id=custom_id)
            self.assertIsNotNone(tokens)
            self.assertEqual(tokens.get("client_id"), custom_id)
            self.assertEqual(tokens.get("access_token"), "tok_refreshed")

    def test_fetch_user_playlists_handles_none_tracks_safely(self):
        fake_page = {
            "items": [
                {
                    "id": "pl1" * 7 + "a",
                    "name": "Playlist With Null Tracks",
                    "tracks": None,  # Can be None in Spotify API responses
                    "owner": {"id": "user1"},
                    "external_urls": {"spotify": "https://open.spotify.com/playlist/pl1"}
                },
                {
                    "id": "pl2" * 7 + "b",
                    "name": "Playlist With Items Dict",
                    "items": {"total": 42},  # Modern Spotify structure
                    "owner": None,
                    "external_urls": {"spotify": "https://open.spotify.com/playlist/pl2"}
                }
            ],
            "next": None
        }
        with patch.object(auth, "spotify_api_get", return_value=fake_page):
            playlists = auth.fetch_user_playlists("fake_token")
            self.assertIsNotNone(playlists)
            self.assertEqual(len(playlists), 2)
            self.assertEqual(playlists[0]["name"], "Playlist With Null Tracks")
            self.assertEqual(playlists[0]["tracks_count"], 0)
            self.assertEqual(playlists[1]["name"], "Playlist With Items Dict")
            self.assertEqual(playlists[1]["tracks_count"], 42)

    def test_fetch_playlist_tracks_handles_track_and_item_keys(self):
        fake_tracks_page = {
            "items": [
                {
                    # Old Spotify format: entry['track']
                    "track": {
                        "id": "track_old",
                        "name": "Old Song",
                        "artists": [{"name": "Old Artist"}],
                        "duration_ms": 120000,
                        "uri": "spotify:track:track_old"
                    }
                },
                {
                    # New Spotify format: entry['item']
                    "item": {
                        "id": "track_new",
                        "name": "New Song",
                        "artists": [{"name": "New Artist"}],
                        "duration_ms": 180000,
                        "uri": "spotify:track:track_new"
                    }
                }
            ],
            "next": None
        }
        with patch.object(auth, "spotify_api_get", return_value=fake_tracks_page):
            tracks = auth.fetch_playlist_tracks("fake_token", "test_pl_id")
            self.assertIsNotNone(tracks)
            self.assertEqual(len(tracks), 2)
            self.assertEqual(tracks[0]["title"], "Old Song")
            self.assertEqual(tracks[0]["artist"], "Old Artist")
            self.assertEqual(tracks[1]["title"], "New Song")
            self.assertEqual(tracks[1]["artist"], "New Artist")

    def test_fetch_playlist_tracks_falls_back_to_items_endpoint(self):
        def fake_get(endpoint, token):
            if "/tracks" in endpoint:
                return None  # simulates 404 or deprecated /tracks
            if "/items" in endpoint:
                return {
                    "items": [
                        {
                            "item": {
                                "id": "song_items",
                                "name": "Item Song",
                                "artists": [{"name": "Item Artist"}],
                                "duration_ms": 150000,
                                "uri": "spotify:track:song_items"
                            }
                        }
                    ],
                    "next": None
                }
            return None

        with patch.object(auth, "spotify_api_get", side_effect=fake_get):
            tracks = auth.fetch_playlist_tracks("fake_token", "test_pl_fallback")
            self.assertIsNotNone(tracks)
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0]["title"], "Item Song")

    def test_sync_spotify_library_preserves_playlist_when_track_fetch_fails(self):
        pl_id = "x" * 22
        user_pls = [{"id": pl_id, "name": "Protected Followed Playlist", "tracks_count": 10}]
        with patch.object(auth, "has_modify_scopes", return_value=False), \
             patch.object(auth, "fetch_liked_songs", return_value=[]), \
             patch.object(auth, "fetch_user_playlists", return_value=user_pls), \
             patch.object(auth, "fetch_playlist_tracks", return_value=None):  # e.g. 403 on followed playlist
            auth.sync_spotify_library("fake_token")
            saved = storage.load_saved_playlists()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["id"], pl_id)
            self.assertEqual(saved[0]["name"], "Protected Followed Playlist")
            self.assertEqual(saved[0]["tracks"], [])


if __name__ == "__main__":
    unittest.main()
