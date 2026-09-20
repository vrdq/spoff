import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spoff import app, auth, player, storage, streamer


class TestAuditRemainingFixed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self._orig_data = storage.DATA_DIR
        self._orig_cache = storage.CACHE_DIR
        self._orig_playlists = storage.PLAYLISTS_FILE
        self._orig_liked = storage.LIKED_SONGS_FILE
        self._orig_config = storage.CONFIG_FILE
        self._orig_index = storage.INDEX_FILE
        self._orig_streamer_cache = streamer.CACHE_DIR

        storage.DATA_DIR = self.tmp_path / "data"
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.INDEX_FILE = storage.DATA_DIR / "offline_index.json"
        streamer.CACHE_DIR = storage.CACHE_DIR

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
        streamer.CACHE_DIR = self._orig_streamer_cache
        self._tmp.cleanup()

    def test_r1_playlist_removal_returns_track_and_updates_queue(self):
        track = dict(id="local_test", title="Test Track", artist="Artist", source="youtube")
        storage.save_saved_playlists([dict(id="local_playlist", name="Test", tracks=[track])])

        # Test storage function returns removed track dict
        removed = storage.remove_track_from_playlist_by_index_or_track("local_playlist", track, index=0)
        self.assertIsInstance(removed, dict)
        self.assertEqual(removed.get("id"), "local_test")

        # Test app action_delete_item does not crash and updates queue
        storage.save_saved_playlists([dict(id="local_playlist", name="Test", tracks=[track])])
        f = types.SimpleNamespace(
            focused=types.SimpleNamespace(id="track-table", cursor_row=0),
            active_tab="playlist",
            current_playlist_id="local_playlist",
            current_playlist_tracks=[track],
            playlists=storage.load_saved_playlists(),
            queue=[track],
            current_index=0,
            refresh_side_table=Mock(),
            _submit_spotify_job=Mock(),
            push_screen=Mock(),
            render_tracks=Mock(),
            notify_user=Mock(),
            update_player_hud=Mock(),
        )
        app.SpoffTUI.action_delete_item(f)
        self.assertTrue(f.push_screen.called)
        confirm = f.push_screen.call_args.args[1]
        confirm(True)
        self.assertEqual(f.queue, [])
        self.assertEqual(f.current_index, -1)
        self.assertEqual(storage.load_saved_playlists()[0]["tracks"], [])

    def test_r2_failed_stream_urls_evicted_from_cache(self):
        with patch.dict(streamer._stream_cache, {"song::artist": ({"url": "expired"}, 0)}, clear=True):
            self.assertIn("song::artist", streamer._stream_cache)
            streamer.invalidate_stream_cache("Song", "Artist")
            self.assertNotIn("song::artist", streamer._stream_cache)

        with patch.dict(streamer._stream_cache, {"https://direct.url::song::artist": ({"url": "expired"}, 0)}, clear=True):
            self.assertIn("https://direct.url::song::artist", streamer._stream_cache)
            streamer.invalidate_stream_cache("Song", "Artist", direct_url="https://direct.url")
            self.assertNotIn("https://direct.url::song::artist", streamer._stream_cache)

    def test_r3_update_info_initialized_on_fresh_app(self):
        with patch("spoff.app.MPRISService", Mock()):
            instance = app.SpoffTUI.__new__(app.SpoffTUI)
            app.SpoffTUI.__init__(instance)
            self.assertTrue(hasattr(instance, "update_info"))
            self.assertIsNone(instance.update_info)

    def test_r4_download_saturation_calls_callback_without_lock(self):
        with patch.object(streamer, "_download_slots") as slots:
            slots.acquire.return_value = False
            lock_acquired_in_callback = []

            def _err_cb(e):
                # Attempt to acquire the download lock; must succeed because it is released
                acquired = streamer._download_lock.acquire(blocking=False)
                if acquired:
                    streamer._download_lock.release()
                lock_acquired_in_callback.append(acquired)

            streamer.download_track_to_cache(
                "test_id_saturation",
                "Title",
                "Artist",
                on_error=_err_cb,
            )
            self.assertEqual(lock_acquired_in_callback, [True])

    def test_r5_player_error_quarantines_and_deletes_corrupt_cached_file(self):
        val_id = "test_track_r5"
        cache_file = storage.CACHE_DIR / f"{val_id}.opus"
        cache_file.write_bytes(b"0" * 15000)
        storage.save_offline_index({val_id: {"id": val_id, "file": str(cache_file)}})

        self.assertTrue(cache_file.exists())
        self.assertIsNotNone(storage.get_cached_track_path(val_id))

        ok = storage.quarantine_cached_track(val_id, str(cache_file))
        self.assertTrue(ok)
        self.assertFalse(cache_file.exists())
        self.assertIsNone(storage.get_cached_track_path(val_id))

        # Test quarantine when source is empty / None (automatic resolution)
        cache_file2 = storage.CACHE_DIR / f"{val_id}.opus"
        cache_file2.write_bytes(b"0" * 15000)
        storage.save_offline_index({val_id: {"id": val_id, "file": str(cache_file2)}})
        self.assertTrue(cache_file2.exists())
        ok2 = storage.quarantine_cached_track(val_id, None)
        self.assertTrue(ok2)
        self.assertFalse(cache_file2.exists())

    def test_r6_exit_does_not_overwrite_global_hyprland_opacity(self):
        instance = types.SimpleNamespace(
            player=Mock(),
            cava=None,
            mpris=None,
            _closing=False,
            _play_request_id=0,
            repeat_mode="off",
            _cleanup_on_exit=app.SpoffTUI._cleanup_on_exit,
        )
        with patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "sig"}):
            with patch("subprocess.run") as mock_run:
                app.SpoffTUI._cleanup_on_exit(instance)
                calls = [c.args[0] for c in mock_run.call_args_list if c.args]
                for cmd in calls:
                    self.assertNotIn("decoration:active_opacity", cmd)
                    self.assertNotIn("decoration:inactive_opacity", cmd)

    def test_r7_empty_queue_next_updates_origin(self):
        f = types.SimpleNamespace(
            queue=[],
            current_index=-1,
            active_tab="playlist",
            current_playlist_id="playlist_xyz",
            _queue_origin=None,
            shuffle_mode=False,
            _failed_indices=set(),
            _get_current_view_tracks=lambda: [{"id": "t1", "title": "Track 1", "artist": "Artist"}],
            play_index=Mock(),
            notify_user=Mock(),
        )
        app.SpoffTUI.action_next_track(f)
        self.assertIsNotNone(f._queue_origin)
        self.assertEqual(f._queue_origin["tab"], "playlist")
        self.assertEqual(f._queue_origin["playlist_id"], "playlist_xyz")
        self.assertEqual(len(f.queue), 1)

    def test_f1_remote_error_does_not_delete_cached_download(self):
        val_id = "test_downloaded_f1"
        cache_file = storage.CACHE_DIR / f"{val_id}.m4a"
        cache_file.write_bytes(b"downloaded audio data" * 1000)
        track = dict(id=val_id, title="Song", artist="Artist")
        storage.register_cached_track(val_id, track, cache_file)

        self.assertTrue(cache_file.exists())
        self.assertIn(val_id, storage.load_offline_index())

        f = types.SimpleNamespace(
            is_mounted=True,
            _closing=False,
            _play_request_id=1,
            _playback_failed=Mock(),
            player=Mock(),
        )
        app.SpoffTUI._handle_track_end(f, 1, "error", track, "https://example.com/stream.m3u8")

        # Remote playback error must NOT delete healthy offline cache or remove it from index
        self.assertTrue(cache_file.exists())
        self.assertIn(val_id, storage.load_offline_index())

    def test_f2_old_backend_eof_ignored_by_new_track(self):
        controller = player.MPVController()
        stop = threading.Event()
        called_reasons = []

        cb = lambda reason="eof": called_reasons.append(reason)
        controller.register_pending_callback(cb, request_id=2)

        # Simulate socket sending an old EOF event from playlist_entry_id 99
        import json
        import socket
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock(return_value=False)
        events = [
            json.dumps({"event": "end-file", "reason": "eof", "playlist_entry_id": 99}) + "\n",
            json.dumps({"event": "start-file", "playlist_entry_id": 100}) + "\n",
            json.dumps({"event": "end-file", "reason": "eof", "playlist_entry_id": 100}) + "\n",
        ]
        def recv(size):
            if events:
                return events.pop(0).encode()
            stop.set()
            return b""
        sock.recv.side_effect = recv

        with patch.object(player.os.path, "exists", return_value=True), patch.object(player.socket, "socket", return_value=sock):
            controller._ipc_listener(stop)

        # Only entry 100's EOF should have triggered the callback (once), entry 99 was ignored
        self.assertEqual(called_reasons, ["eof"])

    def test_f3_quitting_during_pending_switch_saves_committed_state(self):
        track_a = dict(id="track_A", title="Song A", artist="Artist A")
        committed_rec = {
            "playlist_id": "pl_A",
            "tab": "playlist",
            "track_id": "track_A",
            "track_title": "Song A",
            "track_artist": "Artist A",
            "track_index": 0,
        }
        f = types.SimpleNamespace(
            _committed_playback=committed_rec,
            _queue_origin=dict(tab="playlist", playlist_id="pl_B"),
            current_index=2,
            player=types.SimpleNamespace(current_track=track_a),
        )

        app.SpoffTUI._save_playback_state(f)
        state = storage.get_saved_last_played()

        # Must persist committed state (pl_A), not pending mixed state (pl_B with track_A)
        self.assertEqual(state.get("playlist_id"), "pl_A")
        self.assertEqual(state.get("track_id"), "track_A")
        self.assertEqual(state.get("track_index"), 0)

    def test_f4_get_volume_returns_integer_without_attribute_error(self):
        controller = player.MPVController(initial_volume=63)
        vol = controller.get_volume()
        self.assertIsInstance(vol, int)
        self.assertEqual(vol, 63)


if __name__ == "__main__":
    unittest.main()
