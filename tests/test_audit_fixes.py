import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import threading
import types

from spoff import storage, auth, streamer, player, visualizer, app

class AuditFixesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE
        self.old_config_file = storage.CONFIG_FILE
        self.old_index_file = storage.INDEX_FILE
        self.old_auth_file = auth.AUTH_FILE
        self.old_streamer_cache_dir = streamer.CACHE_DIR
        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.INDEX_FILE = storage.DATA_DIR / "offline_index.json"
        auth.AUTH_FILE = storage.DATA_DIR / "spotify_auth.json"
        streamer.CACHE_DIR = storage.CACHE_DIR
        storage._atomic_json_dump(storage.PLAYLISTS_FILE, [])
        storage._atomic_json_dump(storage.INDEX_FILE, {})

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        storage.CONFIG_FILE = self.old_config_file
        storage.INDEX_FILE = self.old_index_file
        auth.AUTH_FILE = self.old_auth_file
        streamer.CACHE_DIR = self.old_streamer_cache_dir
        self.temp_dir.cleanup()

    def test_auth_file_mode_is_0600(self):
        auth.save_spotify_auth({"access_token": "secret_token"})
        self.assertEqual(auth.AUTH_FILE.stat().st_mode & 0o777, 0o600)

    def test_path_traversal_is_blocked(self):
        victim = storage.DATA_DIR / "victim.m4a"
        victim.write_bytes(b"x" * 10001)
        self.assertIsNone(storage.get_cached_track_path("../victim"))
        self.assertFalse(storage.delete_cached_track("../victim"))
        self.assertTrue(victim.exists())

    def test_supported_audio_extensions_discovered_and_deleted(self):
        for ext in (".m4a", ".opus", ".mp3", ".webm", ".ogg", ".flac"):
            p = storage.CACHE_DIR / f"test-track{ext}"
            p.write_bytes(b"x" * 10001)
            storage.register_cached_track("test-track", {}, p)
            self.assertEqual(storage.get_cached_track_path("test-track"), p)
            self.assertTrue(storage.delete_cached_track("test-track"))
            self.assertFalse(p.exists())

    def test_same_pid_atomic_writers_dont_collide(self):
        target = storage.DATA_DIR / "atomic-test.json"
        errors = []
        def write(val):
            try:
                storage._atomic_json_dump(target, {"value": val})
            except Exception as e:
                errors.append(e)
        ts = [threading.Thread(target=write, args=(i,)) for i in range(10)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(len(errors), 0)

    def test_malformed_playlist_element_does_not_crash(self):
        storage.PLAYLISTS_FILE.write_text('[null, {"id": "p1"}]')
        storage.add_saved_playlist({"id": "new"})
        pls = storage.load_saved_playlists()
        self.assertTrue(any(p["id"] == "new" for p in pls))

    def test_share_empty_sidebar_does_not_recurse(self):
        fake = types.SimpleNamespace(
            _is_ready=True,
            focused=types.SimpleNamespace(id="side-table"),
            active_tab="search",
            playlists=[],
            current_playlist_id=None,
            notify_user=Mock()
        )
        fake.action_share_track = types.MethodType(app.SpoffTUI.action_share_track, fake)
        fake.action_share_playlist = types.MethodType(app.SpoffTUI.action_share_playlist, fake)
        fake.action_share_track()
        fake.notify_user.assert_called_with("No playlist selected to share.")

    def test_sidebar_boundary_does_not_reorder_tracks(self):
        side = types.SimpleNamespace(id="side-table", cursor_row=0)
        table = Mock(cursor_row=1)
        a, b = [{"id": "a"}, {"id": "b"}]
        fake = types.SimpleNamespace(
            focused=side,
            playlists=[{"id": "p"}],
            active_tab="playlist",
            current_playlist_tracks=[a, b],
            current_playlist_id=None,
            queue=[],
            render_tracks=Mock(),
            query_one=lambda sel, *args: side if sel == "#side-table" else table
        )
        app.SpoffTUI.action_move_item_up(fake)
        self.assertEqual(fake.current_playlist_tracks, [a, b])

    def test_visualizer_arbitrary_sizes_render(self):
        for bars, style in [(12, "braille"), (23, "stereo"), (18, "wave"), (15, "dots")]:
            with self.subTest(bars=bars, style=style):
                v = visualizer.CavaVisualizer(bars=bars, style=style)
                markup = v.get_markup(True, False)
                self.assertIsInstance(markup, str)

    def test_stop_waits_for_process(self):
        p = player.MPVController(socket_path=str(storage.DATA_DIR / "test.sock"))
        proc = Mock()
        p.process = proc
        p.stop()
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        self.assertIsNone(p.process)

    def test_failed_ipc_load_sets_playing_false(self):
        p = player.MPVController(socket_path=str(storage.DATA_DIR / "test.sock"))
        with patch.object(p, "start_mpv"), patch.object(p, "_send_command", return_value=False):
            ok = p.load_and_play("/no/file", {"id": "a"})
            self.assertFalse(ok)
            self.assertIsNone(p.current_track)

    def test_stream_cache_safe_eviction(self):
        class EvictingDict(dict):
            def get(self, key, default=None):
                val = super().get(key, default)
                self.pop(key, None)
                return val
        cache = EvictingDict({"title::artist": ({"stream_url": "fake"}, 0)})
        with patch("spoff.ytmusic.get_ytmusic_client", return_value=None), patch.object(streamer, "_stream_cache", cache), patch.object(streamer.yt_dlp, "YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": [{"url": "direct", "title": "Title", "artist": "Artist"}]}
            # Ensures KeyError is not raised during safe get
            res = streamer.search_and_resolve_stream("Title", "Artist")
            self.assertIsNotNone(res)


    def test_delete_current_song_advances_to_successor(self):
        class Table:
            id = "track-table"
            cursor_row = 0
            def move_cursor(self, **kw): pass
        a, b, c_track = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        fake = types.SimpleNamespace(
            focused=Table(),
            active_tab="search",
            search_results=[a, b, c_track],
            queue=[a, b, c_track],
            current_index=0,
            render_tracks=Mock(),
            update_player_hud=Mock(),
            notify_user=Mock(),
            repeat_mode="off",
            shuffle_mode=False,
            play_index=Mock(),
            _sync_table_cursor_to_index=Mock()
        )
        fake.action_delete_item = types.MethodType(app.SpoffTUI.action_delete_item, fake)
        fake.action_next_track = types.MethodType(app.SpoffTUI.action_next_track, fake)
        fake.action_delete_item()
        self.assertEqual(fake.queue, [b, c_track])
        # Next track must play b (successor), NOT c
        fake.action_next_track()
        fake.play_index.assert_called_once_with(0)

    def test_escape_key_restored_in_apply_keybindings(self):
        fake = types.SimpleNamespace(
            custom_keybindings={},
            keybindings={},
            _bindings=types.SimpleNamespace(key_to_bindings={}, bind=Mock()),
            _update_nav_bar=Mock(),
            update_spotify_pill=Mock(),
            update_settings_pill=Mock(),
            update_player_hud=Mock()
        )
        fake.apply_keybindings = types.MethodType(app.SpoffTUI.apply_keybindings, fake)
        fake.apply_keybindings()
        calls = [c[0] for c in fake._bindings.bind.call_args_list]
        self.assertTrue(any(c[0] == "escape" and c[1] == "clear_or_unfocus" for c in calls))

    def test_duplicate_download_shares_future_and_callbacks(self):
        started = threading.Event()
        proceed = threading.Event()
        calls = []

        def mock_worker(*args, **kwargs):
            started.set()
            proceed.wait(timeout=3)
            p = storage.CACHE_DIR / "active-dup.m4a"
            p.write_bytes(b"x" * 10001)
            storage.register_cached_track("active-dup", {"title": "T", "artist": "A"}, p)
            return p

        with patch.object(streamer, "_run_download_process", side_effect=mock_worker):
            t1 = threading.Thread(target=streamer.download_track_to_cache,
                                  args=("active-dup", "T", "A"),
                                  kwargs={"on_complete": lambda p: calls.append(1), "blocking": True})
            t1.start()
            self.assertTrue(started.wait(timeout=2))

            t2 = threading.Thread(target=streamer.download_track_to_cache,
                                  args=("active-dup", "T", "A"),
                                  kwargs={"on_complete": lambda p: calls.append(2), "blocking": True})
            t2.start()

            proceed.set()
            t1.join(timeout=3)
            t2.join(timeout=3)

        self.assertEqual(sorted(calls), [1, 2])
        storage.delete_cached_track("active-dup")

    def test_artwork_cache_malformed_entries_filtered(self):
        from spoff import art
        with patch.object(art, "_memory_art_cache", {"bad": None}), patch.object(art, "_cache_loaded", True):
            res = art.get_cached_artwork({"id": "bad"})
            self.assertEqual(res, {})

    def test_mpris_setposition_bounds_and_track_validation(self):
        from spoff import mpris
        seek_calls = []
        obj = mpris.SpoffMPRISDbus({"set_position": lambda p: seek_calls.append(p)})
        obj.Metadata = {
            "mpris:trackid": "/org/mpris/MediaPlayer2/track/t_valid",
            "mpris:length": 180000000
        }
        # Wrong track ID is rejected
        obj.SetPosition("/wrong/track", 1000000)
        self.assertEqual(len(seek_calls), 0)

        # Negative position is rejected
        obj.SetPosition("/org/mpris/MediaPlayer2/track/t_valid", -5)
        self.assertEqual(len(seek_calls), 0)

        # Position exceeding length is rejected
        obj.SetPosition("/org/mpris/MediaPlayer2/track/t_valid", 200000000)
        self.assertEqual(len(seek_calls), 0)

        # Valid track and position is accepted
        obj.SetPosition("/org/mpris/MediaPlayer2/track/t_valid", 30000000)
        self.assertEqual(seek_calls, [30.0])

    def test_mpris_setters_emit_properties_changed(self):
        from spoff import mpris
        service = mpris.MPRISService({})
        obj = mpris.SpoffMPRISDbus({}, service=service)
        service.dbus_obj = obj
        with patch.object(service, "_emit_changed") as mock_emit:
            obj.Shuffle = True
            mock_emit.assert_called_with({"Shuffle": True})

            obj.Volume = 0.5
            mock_emit.assert_called_with({"Volume": 0.5})

            obj.LoopStatus = "Track"
            mock_emit.assert_called_with({"LoopStatus": "Track"})

    def test_lyrics_validation(self):
        from spoff import lyrics
        # Malformed lines
        res = lyrics.get_active_lyric_index([None, {"text": "Hello"}], 10.0)
        self.assertEqual(res, -1)
        # Valid lines with unsorted timestamps are sorted properly
        valid_lines = [
            {"time": 20.0, "text": "Second"},
            {"time": 5.0, "text": "First"}
        ]
        idx = lyrics.get_active_lyric_index(valid_lines, 6.0)
        self.assertEqual(idx, 1)

    def test_updater_flock_prevents_concurrent_runs(self):
        import os
        from spoff import updater
        import fcntl
        updater.UPDATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(updater.UPDATE_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            ok, msg = updater.perform_update()
            self.assertFalse(ok)
            self.assertIn("already running", msg)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_transparency_rendering_and_toggling(self):
        from spoff.app import SpoffTUI

        app = SpoffTUI()
        app.transparency = True
        app.apply_transparency()
        self.assertTrue(app.ansi_color)
        self.assertEqual(app.styles.background.ansi, -1)

        app.toggle_transparency()
        self.assertFalse(app.transparency)
        self.assertTrue(app.ansi_color)
        self.assertEqual(str(app.styles.background), "Color(18, 18, 18)")
        self.assertFalse(app.has_class("transparent-mode"))

        app.toggle_transparency()
        self.assertTrue(app.transparency)
        self.assertTrue(app.has_class("transparent-mode"))

    def test_subprocess_pdeathsig_and_destructors(self):
        self.assertFalse(hasattr(player, "_preexec_deathsig"))
        self.assertFalse(hasattr(visualizer, "_preexec_deathsig"))
        self.assertTrue(hasattr(player.MPVController, "__del__"))
        self.assertTrue(hasattr(visualizer.CavaVisualizer, "__del__"))

    def test_active_playlist_and_playing_track_indicators(self):
        from textual.widgets import DataTable
        from spoff.app import SpoffTUI

        tui = SpoffTUI()
        tui.playlists = [
            {"id": "pl_1", "name": "Favorites", "tracks": []},
            {"id": "pl_2", "name": "Chill Beats", "tracks": []},
        ]
        tui.current_playlist_id = "pl_2"

        # Mock query_one for side-table
        side_table = Mock(spec=DataTable)
        side_rows = []
        side_table.add_row = lambda text, key=None: side_rows.append((text, key))
        side_table.clear = Mock()
        side_table.move_cursor = Mock()

        with patch.object(tui, "query_one", return_value=side_table):
            tui.refresh_side_table()

        self.assertEqual(len(side_rows), 2)
        fav_text = side_rows[0][0].plain
        chill_text = side_rows[1][0].plain
        self.assertEqual(fav_text, "Favorites")
        self.assertEqual(chill_text, "Chill Beats")
        self.assertNotIn("●", chill_text)
        self.assertTrue(any(span.style == "bold #ffffff" for span in side_rows[1][0].spans))

        # Verify playing indicator in render_tracks
        track_table = Mock(spec=DataTable)
        track_rows = []
        track_table.cursor_row = None
        track_table.add_row = lambda *cols, key=None: track_rows.append((cols, key))
        track_table.clear = Mock()
        track_table.move_cursor = Mock()

        tui.player.current_track = {"id": "t2", "title": "505", "artist": "Arctic Monkeys"}
        tracks = [
            {"id": "t1", "title": "Song One", "artist": "Artist A"},
            {"id": "t2", "title": "505", "artist": "Arctic Monkeys"},
        ]

        with patch.object(tui, "query_one", return_value=track_table):
            tui.render_tracks(tracks)

        self.assertEqual(len(track_rows), 2)
        # cols: (type_tag, title_col, artist_col, dur)
        self.assertNotIn("▶", track_rows[0][0][1])
        self.assertNotIn("▶", track_rows[1][0][1])
        self.assertEqual(track_rows[1][0][1], "505")
        track_table.move_cursor.assert_called_with(row=1)

if __name__ == "__main__":
    unittest.main()
