import os
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
        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.INDEX_FILE = storage.DATA_DIR / "offline_index.json"
        auth.AUTH_FILE = storage.DATA_DIR / "spotify_auth.json"
        storage._atomic_json_dump(storage.PLAYLISTS_FILE, [])
        storage._atomic_json_dump(storage.INDEX_FILE, {})

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
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
        with patch.object(streamer, "_stream_cache", cache), patch.object(streamer.yt_dlp, "YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": [{"url": "direct"}]}
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
        p = storage.CACHE_DIR / "dup-track.m4a"
        p.write_bytes(b"x" * 10001)
        calls = []
        def done1(path): calls.append(1)
        def done2(path): calls.append(2)
        streamer.download_track_to_cache("dup-track", "T", "A", on_complete=done1, blocking=True)
        streamer.download_track_to_cache("dup-track", "T", "A", on_complete=done2, blocking=True)
        self.assertEqual(calls, [1, 2])

if __name__ == "__main__":

    unittest.main()
