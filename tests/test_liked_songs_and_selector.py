import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, PropertyMock

# Ensure spoff is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spoff import storage
from spoff.app import DEFAULT_KEYBINDINGS, ACTION_INFO, SpoffTUI
from spoff.auth import sync_spotify_library, remove_track_from_spotify_account, add_track_to_spotify_account
from textual.widgets import DataTable


class TestLikedSongsStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        self.temp_dir.cleanup()

    def test_load_save_liked_songs(self):
        self.assertEqual(storage.load_liked_songs(), [])
        tracks = [
            {"id": "t1", "title": "Song 1", "artist": "Artist 1"},
            {"id": "t2", "title": "Song 2", "artist": "Artist 2"}
        ]
        storage.save_liked_songs(tracks)
        loaded = storage.load_liked_songs()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["title"], "Song 1")
        self.assertEqual(loaded[1]["title"], "Song 2")

    def test_add_track_to_liked_songs_and_deduplication(self):
        track1 = {"id": "t1", "title": "Song 1", "artist": "Artist 1"}
        self.assertTrue(storage.add_track_to_liked_songs(track1))
        # Adding exact same ID again should return False
        self.assertFalse(storage.add_track_to_liked_songs(track1))

        # Adding same title and artist with different ID should also be recognized as duplicate
        track1_dup = {"id": "t_alt", "title": "Song 1", "artist": "Artist 1"}
        self.assertFalse(storage.add_track_to_liked_songs(track1_dup))

        # Adding different track should succeed and prepend
        track2 = {"id": "t2", "title": "Song 2", "artist": "Artist 2"}
        self.assertTrue(storage.add_track_to_liked_songs(track2))
        loaded = storage.load_liked_songs()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["id"], "t2")  # prepended

    def test_remove_track_from_liked_songs(self):
        tracks = [
            {"id": "t1", "title": "Song 1", "artist": "Artist 1"},
            {"id": "t2", "title": "Song 2", "artist": "Artist 2"}
        ]
        storage.save_liked_songs(tracks)

        self.assertTrue(storage.remove_track_from_liked_songs("t1"))
        loaded = storage.load_liked_songs()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "t2")

        # Remove by title
        self.assertTrue(storage.remove_track_from_liked_songs("Song 2"))
        self.assertEqual(storage.load_liked_songs(), [])

        # Non-existent track returns False
        self.assertFalse(storage.remove_track_from_liked_songs("non_existent"))

    def test_migration_from_playlists_json_spotify_liked_songs(self):
        # Suppose playlists.json has an old spotify_liked_songs entry
        old_playlists = [
            {
                "id": "spotify_liked_songs",
                "name": "Liked Songs",
                "tracks": [
                    {"id": "migrated_1", "title": "Old Liked 1", "artist": "Artist A"}
                ]
            },
            {
                "id": "pl_regular",
                "name": "My Regular Playlist",
                "tracks": []
            }
        ]
        storage.save_saved_playlists(old_playlists)

        # load_saved_playlists should filter out spotify_liked_songs and migrate its tracks
        pls = storage.load_saved_playlists()
        self.assertEqual(len(pls), 1)
        self.assertEqual(pls[0]["id"], "pl_regular")

        # And liked_songs.json should now contain the migrated tracks
        liked = storage.load_liked_songs()
        self.assertEqual(len(liked), 1)
        self.assertEqual(liked[0]["id"], "migrated_1")


class TestPlaylistSelectorAndLikedTabAppLogic(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"

        self.app = object.__new__(SpoffTUI)
        self.app.player = Mock()
        self.app.player.current_track = None
        self.app.player.get_progress = Mock(return_value=(0.0, 0.0))
        self.app.player.is_paused = False
        self.app.playlists = [
            {"id": "pl_1", "name": "Chill Beats", "tracks": [{"id": "t1", "title": "Beats 1"}]},
            {"id": "pl_2", "name": "Vaporwave", "tracks": [{"id": "t2", "title": "Wave 1"}]},
        ]
        self.app.current_playlist_id = "pl_1"
        self.app.current_playlist_tracks = [{"id": "t1", "title": "Beats 1"}]
        self.app.current_liked_tracks = [
            {"id": "l1", "title": "Liked Track 1", "artist": "Artist 1"},
            {"id": "l2", "title": "Liked Track 2", "artist": "Artist 2"}
        ]
        storage.save_liked_songs(self.app.current_liked_tracks)

        self.app.active_tab = "playlist"
        self.app.advanced_mode = False
        self.app.queue = []
        self.app.current_index = -1
        self.app._failed_indices = set()
        self.app._shuffle_history = []
        self.app.notifications = []
        self.app.notify_user = lambda msg: self.app.notifications.append(msg)
        self.app.pushed_screens = []
        self.app.push_screen = lambda scr, cb: self.app.pushed_screens.append((scr, cb))
        self.app.call_from_thread = lambda fn, *args: fn(*args)

        self.focused_mock = PropertyMock(return_value=None)
        self.focused_patch = patch.object(SpoffTUI, "focused", self.focused_mock)
        self.focused_patch.start()

    def tearDown(self):
        self.focused_patch.stop()
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        self.temp_dir.cleanup()

    def test_keybindings_configuration(self):
        self.assertEqual(DEFAULT_KEYBINDINGS.get("nav_search"), "1")
        self.assertEqual(DEFAULT_KEYBINDINGS.get("nav_playlist"), "2")
        self.assertEqual(DEFAULT_KEYBINDINGS.get("nav_offline"), "3")
        self.assertEqual(DEFAULT_KEYBINDINGS.get("nav_lyrics"), "4")
        self.assertEqual(DEFAULT_KEYBINDINGS.get("nav_liked"), "5")
        self.assertIn("nav_liked", ACTION_INFO)

    def test_playlist_active_bold_and_inactive_normal(self):
        side_table = Mock(spec=DataTable)
        side_rows = []
        side_table.add_row = lambda text, key=None: side_rows.append((text, key))
        side_table.clear = Mock()
        side_table.move_cursor = Mock()
        side_table.cursor_row = 1  # User cursor hovering on pl_2

        with patch.object(self.app, "query_one", return_value=side_table):
            self.app.refresh_side_table()

        self.assertEqual(len(side_rows), 2)
        # pl_1 is the active playlist -> MUST have bold #ffffff style
        pl_1_text = side_rows[0][0]
        self.assertEqual(pl_1_text.plain, "Chill Beats")
        self.assertTrue(any(span.style == "bold #ffffff" for span in pl_1_text.spans))

        # pl_2 is unselected -> MUST NOT have bold, must have #888888 style
        pl_2_text = side_rows[1][0]
        self.assertEqual(pl_2_text.plain, "Vaporwave")
        self.assertFalse(any(span.style == "bold #ffffff" for span in pl_2_text.spans))
        self.assertTrue(any(span.style == "#888888" for span in pl_2_text.spans))

    def test_sidebar_highlight_does_not_mutate_current_playlist_id(self):
        # When user navigates cursor over row 1 (pl_2) in side-table
        self.assertEqual(self.app.current_playlist_id, "pl_1")
        self.app._on_sidebar_playlist_highlighted(1)
        # current_playlist_id should remain pl_1!
        self.assertEqual(self.app.current_playlist_id, "pl_1")

    def test_switch_view_liked(self):
        track_table = Mock(spec=DataTable)
        search_row = Mock()
        lyrics_pane = Mock()
        lyrics_table = Mock()

        def mock_query(sel, *args):
            if sel == "#search-header-row":
                return search_row
            if sel == "#track-table":
                return track_table
            if sel == "#lyrics-pane":
                return lyrics_pane
            if sel == "#lyrics-table":
                return lyrics_table
            return Mock()

        rendered = []
        with patch.object(self.app, "query_one", side_effect=mock_query), \
             patch.object(self.app, "render_tracks", side_effect=lambda tr: rendered.append(tr)), \
             patch.object(self.app, "_update_nav_bar"):
            self.app.switch_view("liked")

        self.assertEqual(self.app.active_tab, "liked")
        self.assertEqual(len(rendered), 1)
        self.assertEqual(len(rendered[0]), 2)
        self.assertEqual(rendered[0][0]["id"], "l1")

    def test_get_current_view_tracks_liked(self):
        self.app.active_tab = "liked"
        tracks = self.app._get_current_view_tracks()
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["id"], "l1")

    def test_play_current_table_row_liked(self):
        self.app.active_tab = "liked"
        played_idx = []
        with patch.object(self.app, "play_index", side_effect=lambda idx: played_idx.append(idx)):
            self.app.play_current_table_row(1)

        self.assertEqual(len(self.app.queue), 2)
        self.assertEqual(played_idx, [1])

    def test_reorder_liked_songs_up_and_down(self):
        self.app.active_tab = "liked"
        tt = Mock(spec=DataTable)
        tt.cursor_row = 1
        with patch.object(self.app, "query_one", return_value=tt), \
             patch.object(self.app, "render_tracks"):
            # Move index 1 up to index 0
            self.app.action_move_item_up()

        self.assertEqual(self.app.current_liked_tracks[0]["id"], "l2")
        self.assertEqual(self.app.current_liked_tracks[1]["id"], "l1")
        self.assertEqual(storage.load_liked_songs()[0]["id"], "l2")

        # Now move index 0 down to index 1
        tt.cursor_row = 0
        with patch.object(self.app, "query_one", return_value=tt), \
             patch.object(self.app, "render_tracks"):
            self.app.action_move_item_down()

        self.assertEqual(self.app.current_liked_tracks[0]["id"], "l1")
        self.assertEqual(self.app.current_liked_tracks[1]["id"], "l2")
        self.assertEqual(storage.load_liked_songs()[0]["id"], "l1")

    def test_render_lyrics_and_selector(self):
        lh = Mock()
        lt = Mock(spec=DataTable)
        lt_rows = []
        lt.add_row = lambda *cols, key=None: lt_rows.append((cols, key))
        lt.clear = Mock()
        lt.move_cursor = Mock()
        lt.update_cell_at = Mock()
        lt.row_count = 2

        self.app.player.current_track = {"title": "Test Song", "artist": "Test Artist"}
        self.app.player.get_progress = Mock(return_value=(10.5, 60.0))
        lines = [
            {"time": 0.0, "text": "First Line"},
            {"time": 10.0, "text": "Second Line"}
        ]
        self.app.current_lyrics = {"synced": True, "lines": lines}

        def mock_query(sel, *args):
            if sel == "#lyrics-header":
                return lh
            if sel == "#lyrics-table":
                return lt
            return Mock()

        with patch.object(self.app, "query_one", side_effect=mock_query):
            self.app.render_lyrics()

        self.assertEqual(len(lt_rows), 2)
        # Verify no ▶ arrow in lyrics
        self.assertNotIn("▶", lt_rows[0][0][1])
        self.assertNotIn("▶", lt_rows[1][0][1])
        # Verify cursor moved to active index (index 1)
        lt.move_cursor.assert_called_with(row=1)

        # Test _highlight_lyric_line
        with patch.object(self.app, "query_one", side_effect=mock_query):
            self.app._highlight_lyric_line(0, 1, lines)
        lt.move_cursor.assert_called_with(row=1)
        # Check update_cell_at calls do not contain ▶
        for call_args in lt.update_cell_at.call_args_list:
            coord, content = call_args[0]
            self.assertNotIn("▶", str(content))

    def test_sync_table_cursor_to_index(self):
        tt = Mock(spec=DataTable)
        tt.row_count = 2
        tt.move_cursor = Mock()

        self.app.active_tab = "playlist"
        self.app.current_playlist_tracks = [
            {"id": "t1", "title": "Song 1", "artist": "Artist 1"},
            {"id": "t2", "title": "Song 2", "artist": "Artist 2"}
        ]
        self.app.player.current_track = {"id": "t2", "title": "Song 2", "artist": "Artist 2"}

        with patch.object(self.app, "query_one", return_value=tt):
            self.app._sync_table_cursor_to_index(1)

        tt.move_cursor.assert_called_with(row=1)

    def test_nav_bar_order_liked_is_last(self):
        nav_bar = Mock()
        self.app.keybindings = DEFAULT_KEYBINDINGS.copy()
        with patch.object(self.app, "query_one", return_value=nav_bar):
            self.app._update_nav_bar()

        nav_bar.update.assert_called_once()
        rendered_text = nav_bar.update.call_args[0][0]
        parts = rendered_text.split("    ")
        self.assertEqual(len(parts), 5)
        self.assertIn("Search", parts[0])
        self.assertIn("Playlists", parts[1])
        self.assertIn("Offline", parts[2])
        self.assertIn("Lyrics", parts[3])
        self.assertIn("Liked Songs", parts[4])


if __name__ == "__main__":
    unittest.main()
