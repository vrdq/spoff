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

    def test_is_track_liked_and_remove_with_artist(self):
        track1 = {"id": "t1", "title": "My Song", "artist": "Singer"}
        self.assertFalse(storage.is_track_liked(track1))
        storage.add_track_to_liked_songs(track1)
        self.assertTrue(storage.is_track_liked(track1))
        # Matching by title and artist without ID
        self.assertTrue(storage.is_track_liked({"title": "My Song", "artist": "Singer"}))
        # Different artist
        self.assertFalse(storage.is_track_liked({"title": "My Song", "artist": "Other Singer"}))

        # Remove with artist check
        self.assertFalse(storage.remove_track_from_liked_songs("My Song", artist="Other Singer"))
        self.assertTrue(storage.remove_track_from_liked_songs("My Song", artist="Singer"))
        self.assertFalse(storage.is_track_liked(track1))

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
        self.app._is_ready = True
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
        self.assertEqual(DEFAULT_KEYBINDINGS.get("like_track"), "l")
        self.assertIn("nav_liked", ACTION_INFO)
        self.assertIn("like_track", ACTION_INFO)

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
             patch.object(self.app, "render_tracks", side_effect=lambda tr, *a, **kw: rendered.append(tr)), \
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
        tt.cursor_row = 0
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

    def test_sync_table_cursor_does_not_jump_to_previously_playing_track(self):
        tt = Mock(spec=DataTable)
        tt.row_count = 2
        tt.cursor_row = 1  # User just selected row 1
        tt.move_cursor = Mock()

        self.app.active_tab = "playlist"
        self.app.current_playlist_tracks = [
            {"id": "t1", "title": "Song 1", "artist": "Artist 1"},
            {"id": "t2", "title": "Song 2", "artist": "Artist 2"}
        ]
        self.app.queue = list(self.app.current_playlist_tracks)
        # Player is still on the PREVIOUS track (Song 1)
        self.app.player.current_track = {"id": "t1", "title": "Song 1", "artist": "Artist 1"}

        with patch.object(self.app, "query_one", return_value=tt):
            self.app._sync_table_cursor_to_index(1)

        # Cursor was already on row 1, so move_cursor should NOT be called (no jumping to row 0)
        tt.move_cursor.assert_not_called()

    def test_commit_playback_does_not_wipe_table(self):
        self.app.player = Mock()
        self.app.player.load_and_play.return_value = True
        self.app.mpris = None
        self.app._play_request_id = 1
        with patch.object(self.app, "render_tracks") as mock_render, \
             patch.object(self.app, "update_player_hud") as mock_hud:
            self.app._commit_playback(1, "stream_url", {"title": "Song 2"})
            mock_render.assert_not_called()
            mock_hud.assert_called_once()

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

    def test_action_like_track_toggle_and_sync(self):
        self.app.active_tab = "playlist"
        tt = Mock(spec=DataTable)
        tt.cursor_row = 0
        test_track = {"id": "4iV5W9uYEdYUVa79Axb7Rh", "title": "New Track", "artist": "New Artist", "source": "spotify"}
        self.app.current_playlist_tracks = [test_track]

        mock_add_spotify = Mock(return_value=(True, "Synced to Spotify Liked Songs"))
        mock_rm_spotify = Mock(return_value=(True, "Removed from Spotify Liked Songs"))

        # 1. Like track when not liked
        with patch.object(self.app, "query_one", return_value=tt), \
             patch("spoff.app.add_track_to_spotify_account", mock_add_spotify), \
             patch("spoff.app.remove_track_from_spotify_account", mock_rm_spotify), \
             patch("threading.Thread", side_effect=lambda target, daemon: Mock(start=lambda: target())):
            self.app.action_like_track()

        # Should be added to storage
        self.assertTrue(storage.is_track_liked(test_track))
        # Notification sent
        self.assertTrue(any("Added 'New Track' to Liked Songs" in n for n in self.app.notifications))
        # Spotify sync invoked
        mock_add_spotify.assert_called_once_with("liked", "Liked Songs", test_track)

        # 2. Pressing like again should UNLIKE and remove
        self.app.notifications.clear()
        with patch.object(self.app, "query_one", return_value=tt), \
             patch("spoff.app.add_track_to_spotify_account", mock_add_spotify), \
             patch("spoff.app.remove_track_from_spotify_account", mock_rm_spotify), \
             patch("threading.Thread", side_effect=lambda target, daemon: Mock(start=lambda: target())):
            self.app.action_like_track()

        # Should be removed from storage
        self.assertFalse(storage.is_track_liked(test_track))
        # Notification sent
        self.assertTrue(any("Removed 'New Track' from Liked Songs" in n for n in self.app.notifications))
        # Spotify unlike sync invoked
        mock_rm_spotify.assert_called_once_with("liked", "Liked Songs", test_track)


class TestDeckTrackFormatting(unittest.TestCase):
    def setUp(self):
        self.app = SpoffTUI()
        self.app.player = Mock()
        self.app.player.get_progress.return_value = (10, 100)
        self.app.player.is_paused = False
        self.app.mpris = None
        self.focused_patch = patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=None)
        self.focused_patch.start()

    def tearDown(self):
        self.focused_patch.stop()

    def test_deck_track_with_artist_uses_dimmed_em_dash_separator(self):
        from collections import defaultdict
        self.app.player.current_track = {"id": "1", "title": "Cool Song", "artist": "Cool Artist"}
        widgets = defaultdict(Mock)
        self.app.query_one = Mock(side_effect=lambda sel, *args, **kwargs: widgets[sel])

        self.app.update_player_hud()

        updated_text = widgets["#deck-track"].update.call_args[0][0]
        self.assertEqual(updated_text, "[bold #ffffff]Cool Song[/]  [#555555]—[/]  [#cccccc]Cool Artist[/]")
        self.assertNotIn("  -  ", updated_text)

    def test_deck_track_without_artist_has_no_trailing_dash_or_white_dash(self):
        from collections import defaultdict
        # Empty artist
        self.app.player.current_track = {"id": "2", "title": "Solo Song", "artist": ""}
        widgets = defaultdict(Mock)
        self.app.query_one = Mock(side_effect=lambda sel, *args, **kwargs: widgets[sel])

        self.app.update_player_hud()

        updated_text = widgets["#deck-track"].update.call_args[0][0]
        self.assertEqual(updated_text, "[bold #ffffff]Solo Song[/]")
        self.assertNotIn("-", updated_text)

        # "Unknown Artist"
        self.app.player.current_track = {"id": "3", "title": "Stream Item", "artist": "Unknown Artist"}
        self.app.update_player_hud()
        updated_text2 = widgets["#deck-track"].update.call_args[0][0]
        self.assertEqual(updated_text2, "[bold #ffffff]Stream Item[/]")
        self.assertNotIn("-", updated_text2)

    def test_deck_track_no_track_playing(self):
        from collections import defaultdict
        self.app.player.current_track = None
        widgets = defaultdict(Mock)
        self.app.query_one = Mock(side_effect=lambda sel, *args, **kwargs: widgets[sel])

        self.app.update_player_hud()

        updated_text = widgets["#deck-track"].update.call_args[0][0]
        self.assertEqual(updated_text, "[dim]No track playing[/dim]")

    def test_hud_declutter_no_status_pill_or_deck_source(self):
        from collections import defaultdict
        # When playing
        self.app.player.current_track = {"id": "1", "title": "Song", "artist": "Artist"}
        widgets = defaultdict(Mock)
        self.app.query_one = Mock(side_effect=lambda sel, *args, **kwargs: widgets[sel])
        self.app.update_player_hud()
        self.assertNotIn("#status-pill", widgets)
        self.assertNotIn("#deck-source", widgets)

        # When idle
        self.app.player.current_track = None
        widgets.clear()
        self.app.update_player_hud()
        self.assertNotIn("#status-pill", widgets)
        self.assertNotIn("#deck-source", widgets)

    def test_shuf_and_rep_pill_empty_when_inactive(self):
        from collections import defaultdict
        widgets = defaultdict(Mock)
        self.app.query_one = Mock(side_effect=lambda sel, *args, **kwargs: widgets[sel])

        # Both inactive
        self.app.shuffle_mode = False
        self.app.repeat_mode = "off"
        self.app.update_player_hud()
        self.assertEqual(widgets["#shuf-pill"].update.call_args[0][0], "")
        self.assertEqual(widgets["#rep-pill"].update.call_args[0][0], "")

        # Active states
        self.app.shuffle_mode = True
        self.app.repeat_mode = "all"
        self.app.update_player_hud()
        self.assertEqual(widgets["#shuf-pill"].update.call_args[0][0], "[#ffffff]SHUF[/]")
        self.assertEqual(widgets["#rep-pill"].update.call_args[0][0], "[#ffffff]REP[/]")

        self.app.repeat_mode = "one"
        self.app.update_player_hud()
        self.assertEqual(widgets["#rep-pill"].update.call_args[0][0], "[#ffffff]REP-1[/]")

    def test_deck_line_3_omits_queue_empty_and_duplicate_hints(self):
        from collections import defaultdict
        widgets = defaultdict(Mock)
        self.app.query_one = Mock(side_effect=lambda sel, *args, **kwargs: widgets[sel])
        self.app.advanced_mode = False

        # Empty queue
        self.app.queue = []
        self.app.current_index = -1
        self.app.update_player_hud()
        hints = widgets["#deck-line-3"].update.call_args[0][0]
        self.assertNotIn("Queue: empty", hints)
        self.assertNotIn(": set", hints)
        self.assertNotIn(": lyrics", hints)
        self.assertNotIn(": offline", hints)

        # Active queue
        self.app.queue = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        self.app.current_index = 1
        self.app.update_player_hud()
        hints_q = widgets["#deck-line-3"].update.call_args[0][0]
        self.assertIn("Queue: 2/3", hints_q)

    def test_engine_pill_clean_display(self):
        widgets = {"#engine-selector-pill": Mock(), "#search-box": Mock()}
        self.app.query_one = Mock(side_effect=lambda sel, *args, **kwargs: widgets[sel])

        self.app.search_engine = "spotify"
        self.app.update_engine_pill()
        widgets["#engine-selector-pill"].update.assert_called_with("[#888888]Spotify[/]")

        self.app.search_engine = "ytmusic"
        self.app.update_engine_pill()
        widgets["#engine-selector-pill"].update.assert_called_with("[#888888]YouTube Music[/]")

    def test_liked_songs_reorder_queue_mirroring(self):
        self.app.active_tab = "liked"
        tracks = [{"id": "1", "title": "A"}, {"id": "2", "title": "B"}, {"id": "3", "title": "C"}]
        storage.save_liked_songs(tracks)
        self.app.current_liked_tracks = list(tracks)
        self.app.queue = list(tracks)
        self.app.current_index = 1
        self.app.focused = None

        mock_tt = Mock()
        mock_tt.cursor_row = 1
        self.app.query_one = Mock(return_value=mock_tt)

        with patch.object(self.app, "render_tracks"), \
             patch("spoff.app.save_liked_songs"):
            self.app.action_move_item_up()

        self.assertEqual([t["id"] for t in self.app.current_liked_tracks], ["2", "1", "3"])
        self.assertEqual([t["id"] for t in self.app.queue], ["2", "1", "3"])
        self.assertEqual(self.app.current_index, 0)

        mock_tt.cursor_row = 0
        with patch.object(self.app, "render_tracks"), \
             patch("spoff.app.save_liked_songs"):
            self.app.action_move_item_down()

        self.assertEqual([t["id"] for t in self.app.current_liked_tracks], ["1", "2", "3"])
        self.assertEqual([t["id"] for t in self.app.queue], ["1", "2", "3"])
        self.assertEqual(self.app.current_index, 1)

    def test_liked_songs_unlike_queue_sync(self):
        self.app.active_tab = "liked"
        self.app._is_ready = True
        tracks = [{"id": "1", "title": "A", "artist": "Art1"}, {"id": "2", "title": "B", "artist": "Art2"}]
        self.app.current_liked_tracks = list(tracks)
        self.app.queue = list(tracks)
        self.app.current_index = 1

        mock_tt = Mock(spec=DataTable)
        mock_tt.id = "track-table"
        mock_tt.cursor_row = 1

        with patch.object(SpoffTUI, "focused", new_callable=PropertyMock, return_value=mock_tt), \
             patch("spoff.app.is_track_liked", return_value=True), \
             patch("spoff.app.remove_track_from_liked_songs"), \
             patch("spoff.app.load_liked_songs", return_value=[{"id": "1", "title": "A", "artist": "Art1"}]), \
             patch.object(self.app, "render_tracks") as mock_render, \
             patch("spoff.app.is_client_side_track", return_value=True):
            self.app.action_like_track()

        self.assertEqual(len(self.app.queue), 1)
        self.assertEqual(self.app.queue[0]["id"], "1")
        self.assertEqual(self.app.current_index, 0)
        # Verify select_row was clamped
        mock_render.assert_called_with([{"id": "1", "title": "A", "artist": "Art1"}], select_row=0)

    def test_on_track_finished_closing_guard(self):
        mock_call = Mock()
        self.app.call_from_thread = mock_call

        self.app._closing = True
        self.app.is_mounted = True
        self.app.on_track_finished()
        mock_call.assert_not_called()

        self.app._closing = False
        self.app.is_mounted = False
        self.app.on_track_finished()
        mock_call.assert_not_called()

    def test_render_tracks_clamps_old_cursor(self):
        mock_table = Mock(spec=DataTable)
        mock_table.cursor_row = 10  # Previous cursor was at row 10
        self.app.query_one = Mock(return_value=mock_table)
        self.app.player.current_track = None

        tracks = [{"id": f"t{i}", "title": f"Track {i}"} for i in range(5)]
        self.app.render_tracks(tracks)

        # Because len(tracks) is 5, max index is 4. Old cursor was 10.
        # Should clamp to 4 instead of resetting to 0.
        mock_table.move_cursor.assert_called_with(row=4)

    def test_render_tracks_reset_cursor_on_tab_switch(self):
        mock_table = Mock(spec=DataTable)
        mock_table.cursor_row = 10
        self.app.query_one = Mock(return_value=mock_table)
        self.app.player.current_track = None

        tracks = [{"id": f"t{i}", "title": f"Track {i}"} for i in range(5)]
        self.app.render_tracks(tracks, reset_cursor=True)

        # When reset_cursor is True, should reset to 0 (or playing_idx if playing)
        mock_table.move_cursor.assert_called_with(row=0)

    def test_refresh_side_table_preserves_cursor_when_clear_resets_cursor(self):
        side_table = Mock(spec=DataTable)
        side_table.cursor_row = 1  # User cursor hovering on pl_2 (index 1)
        side_rows = []
        side_table.add_row = lambda text, key=None: side_rows.append((text, key))
        # Simulate Textual DataTable.clear() resetting cursor_row to None
        def _mock_clear():
            side_table.cursor_row = None
        side_table.clear = Mock(side_effect=_mock_clear)
        side_table.move_cursor = Mock()

        self.app.playlists = [
            {"id": "pl_1", "name": "Chill Beats"},
            {"id": "pl_2", "name": "Vaporwave"},
        ]

        with patch.object(self.app, "query_one", return_value=side_table):
            # Active playlist is pl_1 (index 0)
            self.app.current_playlist_id = "pl_1"
            self.app.refresh_side_table()

        # Target cursor should be preserved as index 1, NOT reverted to index 0 (active playlist)
        side_table.move_cursor.assert_called_with(row=1)

    def test_refresh_side_table_explicit_target_row(self):
        side_table = Mock(spec=DataTable)
        side_table.cursor_row = 0
        side_rows = []
        side_table.add_row = lambda text, key=None: side_rows.append((text, key))
        def _mock_clear():
            side_table.cursor_row = None
        side_table.clear = Mock(side_effect=_mock_clear)
        side_table.move_cursor = Mock()

        self.app.playlists = [
            {"id": "pl_1", "name": "Chill Beats"},
            {"id": "pl_2", "name": "Vaporwave"},
        ]

        with patch.object(self.app, "query_one", return_value=side_table):
            self.app.current_playlist_id = "pl_1"
            self.app.refresh_side_table(target_row=1)

        side_table.move_cursor.assert_called_with(row=1)

    def test_action_focus_tracks_same_playlist_preserves_track_table(self):
        side_table = Mock(spec=DataTable)
        side_table.id = "side-table"
        side_table.cursor_row = 0  # Points to pl_1

        track_table = Mock(spec=DataTable)
        track_table.id = "track-table"
        track_table.focus = Mock()

        def mock_query(sel, *args):
            if sel == "#side-table":
                return side_table
            if sel == "#track-table":
                return track_table
            return Mock()

        self.app.playlists = [
            {"id": "pl_1", "name": "Chill Beats"},
            {"id": "pl_2", "name": "Vaporwave"},
        ]

        self.app.current_playlist_id = "pl_1"
        self.app.current_playlist_tracks = [{"id": "t1", "title": "Track 1"}]
        self.app.load_playlist_by_index = Mock()

        with patch.object(self.app, "query_one", side_effect=mock_query), \
             patch.object(type(self.app), "focused", new_callable=PropertyMock, return_value=side_table):
            self.app.action_focus_tracks()

        # Should NOT reload playlist (which would reset tracks and cursor)
        self.app.load_playlist_by_index.assert_not_called()
        track_table.focus.assert_called_once()

    def test_render_tracks_select_row_overrides_old_cursor(self):
        mock_table = Mock(spec=DataTable)
        mock_table.cursor_row = 15  # Stale cursor from previous search/table
        self.app.query_one = Mock(return_value=mock_table)
        self.app.player.current_track = None

        tracks = [{"id": f"t{i}", "title": f"Track {i}"} for i in range(25)]
        self.app.render_tracks(tracks, select_row=0)

        # select_row=0 should explicitly override old_cursor=15
        mock_table.move_cursor.assert_called_with(row=0)



class TestVisualizerRemovalAndToggle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_config_file = storage.CONFIG_FILE
        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CONFIG_FILE = self.old_config_file
        self.temp_dir.cleanup()

    def test_storage_visualizer_enabled_persistence(self):
        self.assertTrue(storage.get_saved_visualizer_enabled())
        storage.save_visualizer_enabled(False)
        self.assertFalse(storage.get_saved_visualizer_enabled())
        storage.save_visualizer_enabled(True)
        self.assertTrue(storage.get_saved_visualizer_enabled())

    def test_storage_visualizer_style_off(self):
        storage.save_visualizer_style("off")
        self.assertEqual(storage.get_saved_visualizer_style(), "off")
        cfg = storage.load_config()
        cfg.pop("visualizer_enabled", None)
        storage.save_config(cfg)
        self.assertFalse(storage.get_saved_visualizer_enabled())

    def test_cava_visualizer_off_cycle(self):
        from spoff.visualizer import CavaVisualizer, STYLE_NAMES
        vis = CavaVisualizer(style="bars")
        self.assertIn("off", STYLE_NAMES)
        vis.set_style("off")
        self.assertEqual(vis.style, "off")
        self.assertFalse(vis.running)
        self.assertEqual(vis.get_markup(is_playing=True, is_paused=False), "")
        next_style = vis.cycle_style()
        self.assertNotEqual(next_style, "off")

    def test_app_toggle_visualizer_and_deck_visibility(self):
        with patch("spoff.app.MPVController"), \
             patch("spoff.app.MPRISService"), \
             patch("spoff.app.ParametricEQEngine"):
            app = SpoffTUI()
            mock_widget = Mock()
            app.query_one = Mock(return_value=mock_widget)

            app.vis_enabled = True
            app.visualizer.set_style("bars")

            new_state = app.toggle_visualizer()
            self.assertFalse(new_state)
            self.assertFalse(app.vis_enabled)
            self.assertEqual(app.visualizer.style, "off")
            self.assertFalse(mock_widget.display)

            new_state = app.toggle_visualizer()
            self.assertTrue(new_state)
            self.assertTrue(app.vis_enabled)
            self.assertNotEqual(app.visualizer.style, "off")
            self.assertTrue(mock_widget.display)

    def test_app_init_cli_visualizer_flag(self):
        with patch("spoff.app.MPVController"), \
             patch("spoff.app.MPRISService"), \
             patch("spoff.app.ParametricEQEngine"):
            app_disabled = SpoffTUI(visualizer_enabled=False)
            self.assertFalse(app_disabled.vis_enabled)
            self.assertEqual(app_disabled.visualizer.style, "off")


class TestLastPlayedRestoration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_config_file = storage.CONFIG_FILE
        self.old_playlists_file = storage.PLAYLISTS_FILE
        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CONFIG_FILE = self.old_config_file
        storage.PLAYLISTS_FILE = self.old_playlists_file
        self.temp_dir.cleanup()

    def test_storage_save_and_get_last_played(self):
        self.assertEqual(storage.get_saved_last_played(), {})
        state = {
            "playlist_id": "pl_test",
            "tab": "playlist",
            "track_id": "trk_123",
            "track_title": "Cool Song",
            "track_artist": "Cool Artist",
            "track_index": 3,
        }
        storage.save_last_played(state)
        loaded = storage.get_saved_last_played()
        self.assertEqual(loaded.get("playlist_id"), "pl_test")
        self.assertEqual(loaded.get("track_id"), "trk_123")
        self.assertEqual(loaded.get("track_index"), 3)

    def test_on_mount_restores_last_played_track_in_playlist(self):
        playlists = [
            {
                "id": "pl_1",
                "name": "First Playlist",
                "tracks": [{"id": "t1", "title": "Song 1"}, {"id": "t2", "title": "Song 2"}, {"id": "t3", "title": "Song 3"}],
            },
            {
                "id": "pl_2",
                "name": "Second Playlist",
                "tracks": [{"id": "t4", "title": "Song 4"}, {"id": "t5", "title": "Song 5"}],
            },
        ]
        storage.save_saved_playlists(playlists)
        storage.save_last_played({
            "playlist_id": "pl_1",
            "tab": "playlist",
            "track_id": "t2",
            "track_title": "Song 2",
            "track_index": 1,
        })

        with patch("spoff.app.MPVController"), \
             patch("spoff.app.MPRISService"), \
             patch("spoff.app.ParametricEQEngine"), \
             patch.object(SpoffTUI, "check_github_updates_bg"), \
             patch.object(SpoffTUI, "backfill_playlists_art_bg"), \
             patch.object(SpoffTUI, "apply_transparency"):
            app = SpoffTUI()
            app.playlists = playlists

            side_table = Mock(spec=DataTable)
            side_table.move_cursor = Mock()
            side_table.cursor_row = None
            side_table.add_column = Mock()
            side_table.add_row = Mock()

            track_table = Mock(spec=DataTable)
            track_table.move_cursor = Mock()
            track_table.focus = Mock()
            track_table.add_columns = Mock()
            track_table.add_row = Mock()

            lyrics_table = Mock(spec=DataTable)
            lyrics_table.add_column = Mock()

            def mock_query(sel, *args, **kwargs):
                if sel == "#side-table":
                    return side_table
                if sel == "#track-table":
                    return track_table
                if sel == "#lyrics-table":
                    return lyrics_table
                if sel == "#sidebar":
                    return Mock()
                return Mock()

            app.query_one = Mock(side_effect=mock_query)
            app.set_interval = Mock()
            app.set_timer = Mock()

            app.load_playlist_by_index = Mock()

            app.on_mount()

            # Verify load_playlist_by_index was called for playlist 0 with select_row=1
            app.load_playlist_by_index.assert_called_with(0, focus_tracks=True, select_row=1)
            side_table.move_cursor.assert_called_with(row=0)
            track_table.focus.assert_called()

    def test_on_mount_restores_last_played_second_playlist(self):
        playlists = [
            {
                "id": "pl_1",
                "name": "First Playlist",
                "tracks": [{"id": "t1", "title": "Song 1"}],
            },
            {
                "id": "pl_2",
                "name": "Second Playlist",
                "tracks": [{"id": "t4", "title": "Song 4"}, {"id": "t5", "title": "Song 5"}],
            },
        ]
        storage.save_saved_playlists(playlists)
        storage.save_last_played({
            "playlist_id": "pl_2",
            "tab": "playlist",
            "track_id": "t5",
            "track_title": "Song 5",
            "track_index": 1,
        })

        with patch("spoff.app.MPVController"), \
             patch("spoff.app.MPRISService"), \
             patch("spoff.app.ParametricEQEngine"), \
             patch.object(SpoffTUI, "check_github_updates_bg"), \
             patch.object(SpoffTUI, "backfill_playlists_art_bg"), \
             patch.object(SpoffTUI, "apply_transparency"):
            app = SpoffTUI()
            app.playlists = playlists

            side_table = Mock(spec=DataTable)
            side_table.move_cursor = Mock()
            side_table.add_column = Mock()
            side_table.add_row = Mock()

            track_table = Mock(spec=DataTable)
            track_table.move_cursor = Mock()
            track_table.focus = Mock()
            track_table.add_columns = Mock()
            track_table.add_row = Mock()

            lyrics_table = Mock(spec=DataTable)
            lyrics_table.add_column = Mock()

            def mock_query(sel, *args, **kwargs):
                if sel == "#side-table":
                    return side_table
                if sel == "#track-table":
                    return track_table
                if sel == "#lyrics-table":
                    return lyrics_table
                if sel == "#sidebar":
                    return Mock()
                return Mock()

            app.query_one = Mock(side_effect=mock_query)
            app.set_interval = Mock()
            app.set_timer = Mock()

            app.load_playlist_by_index = Mock()

            app.on_mount()

            # Verify load_playlist_by_index was called for playlist 1 with select_row=1
            app.load_playlist_by_index.assert_called_with(1, focus_tracks=True, select_row=1)
            side_table.move_cursor.assert_called_with(row=1)

    def test_save_playback_state_records_correct_track(self):
        with patch("spoff.app.MPVController"), \
             patch("spoff.app.MPRISService"), \
             patch("spoff.app.ParametricEQEngine"):
            app = SpoffTUI()
            app.current_playlist_id = "pl_123"
            app.active_tab = "playlist"
            app.current_index = 4
            app.player.current_track = {"id": "trk_4", "title": "Track 4", "artist": "Singer"}

            app._save_playback_state()

            saved = storage.get_saved_last_played()
            self.assertEqual(saved.get("playlist_id"), "pl_123")
            self.assertEqual(saved.get("track_id"), "trk_4")
            self.assertEqual(saved.get("track_title"), "Track 4")
            self.assertEqual(saved.get("track_index"), 4)


if __name__ == "__main__":
    unittest.main()
