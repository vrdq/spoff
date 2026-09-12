import os
import sys
import unittest
import tempfile
import shutil
import json
from unittest.mock import patch, MagicMock

# Ensure spoff can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spoff import storage
from spoff.auth import rename_spotify_playlist
from spoff.app import (
    DEFAULT_KEYBINDINGS, ACTION_INFO, SpoffTUI,
    RenamePlaylistModal, ClonePlaylistModal
)


from pathlib import Path

class TestPlaylistStorageOps(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"

        self.initial_playlists = [
            {
                "id": "pl_1",
                "name": "Synthwave Chill",
                "url": "https://example.com/synth",
                "tracks": [
                    {"id": "t1", "title": "Nightcall", "artist": "Kavinsky"},
                    {"id": "t2", "title": "Resonance", "artist": "HOME"},
                ]
            },
            {
                "id": "pl_2",
                "name": "Rock Classics",
                "url": "",
                "tracks": [
                    {"id": "t3", "title": "Back in Black", "artist": "AC/DC"}
                ]
            }
        ]
        storage.save_saved_playlists(self.initial_playlists)

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        self.temp_dir.cleanup()

    def test_rename_saved_playlist_success(self):
        ok = storage.rename_saved_playlist("pl_1", "Synthwave Vibes")
        self.assertTrue(ok)

        pls = storage.load_saved_playlists()
        self.assertEqual(len(pls), 2)
        self.assertEqual(pls[0]["name"], "Synthwave Vibes")
        self.assertEqual(pls[0]["id"], "pl_1")
        self.assertEqual(len(pls[0]["tracks"]), 2)

    def test_rename_saved_playlist_whitespace_strip(self):
        ok = storage.rename_saved_playlist("pl_1", "   Deep Chill   ")
        self.assertTrue(ok)

        pls = storage.load_saved_playlists()
        self.assertEqual(pls[0]["name"], "Deep Chill")

    def test_rename_saved_playlist_invalid_or_missing(self):
        # Empty new name
        self.assertFalse(storage.rename_saved_playlist("pl_1", ""))
        self.assertFalse(storage.rename_saved_playlist("pl_1", "   "))

        # Missing playlist ID
        self.assertFalse(storage.rename_saved_playlist("", "New Name"))
        self.assertFalse(storage.rename_saved_playlist("non_existent_id", "New Name"))

    def test_clone_saved_playlist_default_name(self):
        cloned = storage.clone_saved_playlist("pl_1")
        self.assertIsNotNone(cloned)
        self.assertEqual(cloned["name"], "Synthwave Chill (Copy)")
        self.assertTrue(cloned["id"].startswith("local_"))
        self.assertEqual(len(cloned["tracks"]), 2)
        self.assertEqual(cloned["tracks"][0]["title"], "Nightcall")

        pls = storage.load_saved_playlists()
        self.assertEqual(len(pls), 3)
        # Cloned item is inserted immediately after original
        self.assertEqual(pls[1]["id"], cloned["id"])
        self.assertEqual(pls[1]["name"], "Synthwave Chill (Copy)")

    def test_clone_saved_playlist_custom_name(self):
        cloned = storage.clone_saved_playlist("pl_2", "Ultimate Rock")
        self.assertIsNotNone(cloned)
        self.assertEqual(cloned["name"], "Ultimate Rock")
        self.assertEqual(len(cloned["tracks"]), 1)

        pls = storage.load_saved_playlists()
        self.assertEqual(len(pls), 3)
        self.assertEqual(pls[2]["name"], "Ultimate Rock")

    def test_clone_saved_playlist_deep_copy(self):
        cloned = storage.clone_saved_playlist("pl_1", "Cloned Synth")
        self.assertIsNotNone(cloned)

        # Mutate the cloned track list
        cloned_tracks = cloned["tracks"]
        cloned_tracks.append({"id": "t99", "title": "New Song", "artist": "Unknown"})
        storage.update_playlist_tracks(cloned["id"], cloned_tracks)

        # Verify original playlist is completely unaffected
        pls = storage.load_saved_playlists()
        orig = next(p for p in pls if p["id"] == "pl_1")
        cloned_saved = next(p for p in pls if p["id"] == cloned["id"])

        self.assertEqual(len(orig["tracks"]), 2)
        self.assertEqual(len(cloned_saved["tracks"]), 3)

    def test_clone_saved_playlist_missing_id(self):
        self.assertIsNone(storage.clone_saved_playlist(""))
        self.assertIsNone(storage.clone_saved_playlist("does_not_exist"))


class TestSpotifyRenameSync(unittest.TestCase):
    def test_rename_liked_songs_rejected(self):
        ok, msg = rename_spotify_playlist("spotify_liked_songs", "My Favourites", token="fake_token")
        self.assertFalse(ok)
        self.assertIn("Liked Songs", msg)

    def test_rename_without_auth_rejected(self):
        with patch("spoff.auth.get_valid_token", return_value=None):
            ok, msg = rename_spotify_playlist("37i9dQZF1DXcBWIGoYBM5M", "New Name")
            self.assertFalse(ok)
            self.assertIn("Not logged in", msg)

    @patch("spoff.auth.get_valid_token", return_value="valid_mock_token")
    @patch("spoff.auth.has_modify_scopes", return_value=True)
    @patch("spoff.auth.spotify_api_request")
    def test_rename_spotify_playlist_success(self, mock_api, mock_scopes, mock_token):
        mock_api.return_value = (True, {}, "")
        sp_id = "37i9dQZF1DXcBWIGoYBM5M"  # 22 chars alnum
        ok, msg = rename_spotify_playlist(sp_id, "Renamed Hits", token="valid_mock_token")
        self.assertTrue(ok)
        self.assertIn("Renamed playlist", msg)
        mock_api.assert_called_once_with(
            f"/playlists/{sp_id}",
            method="PUT",
            body={"name": "Renamed Hits"},
            token="valid_mock_token"
        )


class TestPlaylistBindingsAndModals(unittest.TestCase):
    def test_keybindings_configured(self):
        self.assertIn("rename_playlist", DEFAULT_KEYBINDINGS)
        self.assertEqual(DEFAULT_KEYBINDINGS["rename_playlist"], "R")
        self.assertIn("clone_playlist", DEFAULT_KEYBINDINGS)
        self.assertEqual(DEFAULT_KEYBINDINGS["clone_playlist"], "Y")

        self.assertIn("rename_playlist", ACTION_INFO)
        self.assertIn("clone_playlist", ACTION_INFO)

    def test_rename_modal_behavior(self):
        modal = RenamePlaylistModal(current_name="My Playlist")
        self.assertEqual(modal.current_name, "My Playlist")

    def test_clone_modal_behavior(self):
        modal = ClonePlaylistModal(original_name="Chill Beats", track_count=15)
        self.assertEqual(modal.original_name, "Chill Beats")
        self.assertEqual(modal.track_count, 15)
        self.assertEqual(modal.default_clone_name, "Chill Beats (Copy)")


from unittest.mock import PropertyMock

class TestPlaylistAppLogic(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(SpoffTUI)
        self.app.playlists = [
            {"id": "pl_1", "name": "Favorites", "tracks": []},
            {"id": "spotify_liked_songs", "name": "Liked Songs", "tracks": []},
            {"id": "pl_2", "name": "Chill Out", "tracks": []},
        ]
        self.focused_mock = PropertyMock(return_value=None)
        self.focused_patch = patch.object(SpoffTUI, "focused", self.focused_mock)
        self.focused_patch.start()
        self.app.current_playlist_id = None
        self.app.active_tab = "playlist"
        self.app.notifications = []
        self.app.notify_user = lambda msg: self.app.notifications.append(msg)
        self.app.pushed_screens = []
        self.app.push_screen = lambda scr, cb: self.app.pushed_screens.append((scr, cb))

    def tearDown(self):
        self.focused_patch.stop()

    def test_get_target_playlist_via_current_id(self):
        self.app.current_playlist_id = "pl_2"
        idx, pl = SpoffTUI._get_target_playlist(self.app)
        self.assertEqual(idx, 2)
        self.assertEqual(pl["name"], "Chill Out")

    def test_action_rename_liked_songs_blocked(self):
        self.app.current_playlist_id = "spotify_liked_songs"
        SpoffTUI.action_rename_playlist(self.app)
        self.assertIn("Cannot rename Liked Songs.", self.app.notifications)
        self.assertEqual(len(self.app.pushed_screens), 0)

    def test_action_delete_liked_songs_blocked(self):
        self.app.current_playlist_id = "spotify_liked_songs"
        SpoffTUI.action_delete_playlist(self.app)
        self.assertIn("Cannot delete Liked Songs.", self.app.notifications)
        self.assertEqual(len(self.app.pushed_screens), 0)

    def test_action_rename_pushes_modal(self):
        self.app.current_playlist_id = "pl_1"
        SpoffTUI.action_rename_playlist(self.app)
        self.assertEqual(len(self.app.pushed_screens), 1)
        scr, cb = self.app.pushed_screens[0]
        self.assertIsInstance(scr, RenamePlaylistModal)
        self.assertEqual(scr.current_name, "Favorites")

    def test_action_clone_pushes_modal(self):
        self.app.current_playlist_id = "pl_1"
        SpoffTUI.action_clone_playlist(self.app)
        self.assertEqual(len(self.app.pushed_screens), 1)
        scr, cb = self.app.pushed_screens[0]
        self.assertIsInstance(scr, ClonePlaylistModal)
        self.assertEqual(scr.original_name, "Favorites")


if __name__ == "__main__":
    unittest.main()

