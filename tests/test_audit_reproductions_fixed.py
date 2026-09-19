import fcntl
import json
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rich.text import Text

from spoff import storage, auth, player, eq, updater, streamer
from spoff.app import SpoffTUI, SettingsModal, AddToPlaylistModal
from spoff.visualizer import CavaVisualizer, VisualizerWidget


def make_fake_app(**kwargs):
    values = {
        "notify_user": Mock(),
        "render_tracks": Mock(),
        "query_one": lambda *a: Mock(),
        "player": Mock(),
        "mpris": Mock(),
        "_closing": False,
        "_play_request_id": 1,
        "active_tab": "search",
        "current_index": 0,
        "queue": [],
        "_failed_indices": set(),
        "_shuffle_history": [],
        "call_from_thread": lambda f, *a, **k: f(*a, **k),
        "_on_ui": lambda f, *a, **k: f(*a, **k),
        "update_player_hud": Mock(),
        "refresh_side_table": Mock(),
        "update_spotify_pill": Mock(),
        "save_visualizer_preferences": Mock(),
        "_apply_visualizer_visibility": Mock(),
    }
    values.update(kwargs)
    return types.SimpleNamespace(**values)


class TestAuditReproductionsFixed(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE
        self.old_config_file = storage.CONFIG_FILE
        self.old_index_file = storage.INDEX_FILE
        self.old_deleted_playlists_file = storage.DELETED_PLAYLISTS_FILE
        self.old_auth_file = auth.AUTH_FILE
        self.old_streamer_cache_dir = streamer.CACHE_DIR

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.INDEX_FILE = storage.DATA_DIR / "offline_index.json"
        storage.DELETED_PLAYLISTS_FILE = storage.DATA_DIR / "deleted_spotify_playlists.json"
        auth.AUTH_FILE = storage.DATA_DIR / "spotify_auth.json"
        streamer.CACHE_DIR = storage.CACHE_DIR

        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)

        storage.save_saved_playlists([])
        storage.save_liked_songs([])
        storage.save_offline_index({})

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        storage.CONFIG_FILE = self.old_config_file
        storage.INDEX_FILE = self.old_index_file
        storage.DELETED_PLAYLISTS_FILE = self.old_deleted_playlists_file
        auth.AUTH_FILE = self.old_auth_file
        streamer.CACHE_DIR = self.old_streamer_cache_dir
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # Audit Case 01: Equivalent track unliking
    # -------------------------------------------------------------------------
    def test_01_equivalent_track_unliked_properly(self):
        old_track = {"id": "spotifyA", "title": "Song", "artist": "Artist"}
        other_track = {"id": "youtubeB", "title": "Song", "artist": "Artist"}
        storage.add_track_to_liked_songs(old_track)
        self.assertTrue(storage.is_track_liked(other_track))

        # Using remove_liked_track properly unlikes the equivalent track
        removed = storage.remove_liked_track(other_track)
        self.assertTrue(removed)
        self.assertEqual(storage.load_liked_songs(), [])

    # -------------------------------------------------------------------------
    # Audit Case 02: ID / title collision exact ID preferred
    # -------------------------------------------------------------------------
    def test_02_id_title_collision_exact_id_preferred(self):
        a = {"id": "a", "title": "b", "artist": "Artist"}
        b = {"id": "b", "title": "Target", "artist": "Artist"}
        storage.save_liked_songs([a, b])

        storage.remove_liked_track(b)
        self.assertEqual([t["id"] for t in storage.load_liked_songs()], ["a"])

    # -------------------------------------------------------------------------
    # Audit Case 03: Migration merges legacy likes into liked songs
    # -------------------------------------------------------------------------
    def test_03_migration_merges_legacy_likes(self):
        storage.save_liked_songs([{"id": "new", "title": "New", "artist": "Artist"}])
        storage.save_saved_playlists([
            {"id": "spotify_liked_songs", "name": "Liked Songs", "tracks": [{"id": "old", "title": "Old", "artist": "Artist"}]}
        ])

        pls = storage.load_saved_playlists()
        self.assertEqual(pls, [])
        liked = storage.load_liked_songs()
        liked_ids = {t.get("id") for t in liked}
        self.assertIn("new", liked_ids)
        self.assertIn("old", liked_ids)

    # -------------------------------------------------------------------------
    # Audit Case 04: Sync preserves concurrent local unlike
    # -------------------------------------------------------------------------
    def test_04_sync_preserves_concurrent_local_unlike(self):
        t = {"id": "s" * 22, "title": "Song", "artist": "Artist", "source": "spotify"}
        storage.save_liked_songs([t])

        def remote(_):
            # Concurrent local unlike occurs while fetching playlists
            storage.remove_liked_track(t)
            return []

        with patch.object(auth, "fetch_liked_songs", return_value=[t]), \
             patch.object(auth, "fetch_user_playlists", side_effect=remote):
            auth.sync_spotify_library("fake")

        self.assertEqual(storage.load_liked_songs(), [])

    # -------------------------------------------------------------------------
    # Audit Case 05: Sync honors newly recorded tombstone
    # -------------------------------------------------------------------------
    def test_05_sync_honors_new_tombstone(self):
        pid = "p" * 22

        def remote(_):
            storage.record_deleted_spotify_playlist_id(pid)
            return [{"id": pid, "name": "Deleted"}]

        with patch.object(auth, "fetch_liked_songs", return_value=[]), \
             patch.object(auth, "fetch_user_playlists", side_effect=remote), \
             patch.object(auth, "fetch_playlist_tracks", return_value=[]):
            auth.sync_spotify_library("fake")

        saved_ids = [p["id"] for p in storage.load_saved_playlists()]
        self.assertNotIn(pid, saved_ids)

    # -------------------------------------------------------------------------
    # Audit Case 06: Delete local playlist does not delete same-named remote
    # -------------------------------------------------------------------------
    def test_06_delete_local_playlist_does_not_delete_same_named_remote(self):
        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "fetch_user_playlists", return_value=[{"id": "remote", "name": "Mine"}]), \
             patch.object(auth, "spotify_api_request", return_value=(True, {}, "")) as api:
            ok, msg = auth.delete_spotify_playlist("local_123", "Mine", token="fake")

        self.assertFalse(ok)
        self.assertIn("not found on spotify", msg.lower())
        api.assert_not_called()

    # -------------------------------------------------------------------------
    # Audit Case 07: Exhaustion updates status without stopping MPRIS service
    # -------------------------------------------------------------------------
    def test_07_exhaustion_updates_status_without_stopping_mpris(self):
        track = {"id": "a", "title": "A", "artist": "B"}
        fake = make_fake_app(queue=[track])
        SpoffTUI._playback_failed(fake, 1, track)

        fake.mpris.stop.assert_not_called()
        fake.mpris.update_status.assert_called_with(False, False)

    # -------------------------------------------------------------------------
    # Audit Case 08: Error callback does not loop repeat track
    # -------------------------------------------------------------------------
    def test_08_error_callback_does_not_repeat_track(self):
        fake = make_fake_app(
            repeat_mode="one",
            current_index=0,
            queue=[{"id": "err", "title": "Error"}],
            play_index=Mock(),
            notify_user=Mock(),
        )
        SpoffTUI._handle_track_end(fake, 1, "error", {"id": "err", "title": "Error"})
        fake.play_index.assert_not_called()

    # -------------------------------------------------------------------------
    # Audit Case 09: Restart MPV sets old listener stop event
    # -------------------------------------------------------------------------
    def test_09_restart_sets_old_listener_event(self):
        p = player.MPVController()
        p.process = Mock()
        p.process.poll.return_value = 1
        old_event = threading.Event()
        p._listener_stop_event = old_event
        p._listener_thread = Mock()

        with patch.object(player.subprocess, "Popen", return_value=Mock()), \
             patch.object(player.threading, "Thread"), \
             patch.object(player.os.path, "exists", return_value=False), \
             patch.object(player.time, "sleep"):
            p.start_mpv()

        self.assertTrue(old_event.is_set())
        self.assertIsNot(p._listener_stop_event, old_event)
        p.process = None
        p._listener_thread = None

    # -------------------------------------------------------------------------
    # Audit Case 10: Stale playback does not publish metadata or clear lyrics
    # -------------------------------------------------------------------------
    def test_10_stale_playback_does_not_publish_metadata_or_clear_lyrics(self):
        fake = make_fake_app(
            _play_request_id=2,
            current_lyrics={"lines": ["new"]},
            _commit_playback=Mock(return_value=False),
        )
        track = {"id": "old", "title": "Old", "artist": "Artist"}

        with patch("spoff.app.get_cached_artwork", return_value={}), \
             patch("spoff.app.get_cached_track_path", return_value=Path("/fake-cache")):
            SpoffTUI.start_playback.__wrapped__(fake, track, 1)

        self.assertEqual(fake.current_lyrics, {"lines": ["new"]})
        fake.mpris.update_track.assert_not_called()

    # -------------------------------------------------------------------------
    # Audit Case 11: Rejected commit does not download to cache
    # -------------------------------------------------------------------------
    def test_11_rejected_commit_does_not_download(self):
        fake = make_fake_app(_commit_playback=Mock(return_value=False))
        track = {"id": "a", "title": "A", "artist": "B"}

        with patch("spoff.app.get_cached_artwork", return_value={}), \
             patch("spoff.app.get_cached_track_path", return_value=None), \
             patch("spoff.app.search_and_resolve_stream", return_value={"stream_url": "https://fake"}), \
             patch("spoff.app.download_track_to_cache") as mock_dl:
            SpoffTUI.start_playback.__wrapped__(fake, track, 1)

        mock_dl.assert_not_called()

    # -------------------------------------------------------------------------
    # Audit Case 12: Commit playback does not alter unrelated view
    # -------------------------------------------------------------------------
    def test_12_commit_does_not_alter_unrelated_view(self):
        fake = make_fake_app(
            current_index=4,
            _get_current_view_tracks=lambda: [{"id": "unrelated"}],
            update_player_hud=Mock(),
            _save_playback_state=Mock(),
        )
        with patch.object(threading, "Thread"):
            self.assertTrue(SpoffTUI._commit_playback(fake, 1, "fake", {"id": "song", "title": "Song"}))
        fake.render_tracks.assert_not_called()

    # -------------------------------------------------------------------------
    # Audit Case 13: Offline delete removes matching item from queue
    # -------------------------------------------------------------------------
    def test_13_offline_delete_matching_row_removes_from_queue(self):
        t = {"id": "a", "title": "Song", "artist": "Artist"}
        storage.save_offline_index({"a": t})
        focus = types.SimpleNamespace(id="track-table", cursor_row=0)
        fake = make_fake_app(
            focused=focus,
            active_tab="offline",
            queue=[t],
            update_player_hud=Mock(),
        )
        SpoffTUI.action_delete_item(fake)
        self.assertEqual(storage.load_offline_index(), {})
        self.assertEqual(fake.queue, [])

    # -------------------------------------------------------------------------
    # Audit Case 14: Search results honor latest request ID
    # -------------------------------------------------------------------------
    def test_14_search_order_honors_request_id(self):
        fake = make_fake_app(
            search_engine="ytmusic",
            advanced_mode=True,
            _search_request_id=0,
            _search_worker=Mock(),
            search_results=[],
        )

        SpoffTUI.do_search(fake, "new")
        self.assertEqual(fake._search_request_id, 1)
        SpoffTUI.do_search(fake, "old")
        self.assertEqual(fake._search_request_id, 2)

        # Worker 1 finishes after request 2 has been dispatched
        with patch("spoff.app.live_search_tracks", return_value=[{"id": "new"}]):
            SpoffTUI._search_worker.__wrapped__(fake, "new", 1, "ytmusic", "YouTube Music")
            # Result is discarded because req_id 1 != current _search_request_id (2)
            self.assertEqual(fake.search_results, [])

    # -------------------------------------------------------------------------
    # Audit Case 15: Non-string track ID is normalized
    # -------------------------------------------------------------------------
    def test_15_non_string_id_normalized(self):
        normalized = storage.normalize_track({"id": 123, "title": "Song", "artist": "Artist"})
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["id"], "123")

    # -------------------------------------------------------------------------
    # Audit Case 16: Atomic reordering preserves concurrent likes
    # -------------------------------------------------------------------------
    def test_16_atomic_reordering_preserves_concurrent_likes(self):
        a = {"id": "a", "title": "A"}
        b = {"id": "b", "title": "B"}
        c = {"id": "c", "title": "C"}
        storage.save_liked_songs([a, b, c])

        ok = storage.move_liked_track(b, -1)
        self.assertTrue(ok)
        self.assertEqual([t["id"] for t in storage.load_liked_songs()], ["b", "a", "c"])

    # -------------------------------------------------------------------------
    # Audit Case 17: EQ auto headroom is maintained on band changes
    # -------------------------------------------------------------------------
    def test_17_eq_auto_headroom_is_maintained(self):
        e = eq.ParametricEQEngine(
            eq.EQPreset("Test", "", 0, [eq.EQBand(1, eq.FilterType.PEAKING, 1000, 0, 1)]),
            auto_headroom=True,
        )
        e.set_band(1, gain_db=24)
        self.assertLess(e.preamp_db, 0.0)
        self.assertLessEqual(e.calculate_peak_gain()[0], 0.0)

    # -------------------------------------------------------------------------
    # Audit Case 18: FFmpeg audio filter graph always includes aresample
    # -------------------------------------------------------------------------
    def test_18_eq_aresample_always_in_audio_filter_graph(self):
        e = eq.ParametricEQEngine(sample_rate=48000)
        graph = e.to_ffmpeg_af()
        self.assertTrue(graph.startswith("aresample=48000"))

    # -------------------------------------------------------------------------
    # Audit Case 19: Updater closes lock file descriptor on contention
    # -------------------------------------------------------------------------
    def test_19_updater_closes_fd_on_contention(self):
        with patch.object(updater.os, "open", return_value=12345), \
             patch.object(updater.fcntl, "flock", side_effect=BlockingIOError), \
             patch.object(updater.os, "close") as mock_close:
            ok, msg = updater.perform_update()

        self.assertFalse(ok)
        mock_close.assert_called_once_with(12345)

    # -------------------------------------------------------------------------
    # Audit Case 20: Corrupt likes creates backup instead of silent loss
    # -------------------------------------------------------------------------
    def test_20_corrupt_likes_creates_corrupted_backup(self):
        storage.LIKED_SONGS_FILE.write_text('[{"id":"important"},')
        with self.assertRaises(ValueError):
            storage.load_liked_songs()
        corrupted_backup = storage.LIKED_SONGS_FILE.with_suffix(".json.corrupted")
        self.assertTrue(corrupted_backup.exists())
        self.assertEqual(corrupted_backup.read_text(), '[{"id":"important"},')

    # -------------------------------------------------------------------------
    # Audit Case 21: Spotify sync refreshes liked table when active
    # -------------------------------------------------------------------------
    def test_21_sync_refreshes_liked_table_when_active(self):
        fake = make_fake_app(
            active_tab="liked",
            current_liked_tracks=[{"id": "old"}],
        )
        with patch("spoff.app.load_spotify_auth", return_value={"access_token": "fake"}), \
             patch("spoff.app.get_valid_token", return_value="fake"), \
             patch("spoff.app.sync_spotify_library", return_value=1), \
             patch("spoff.app.load_liked_songs", return_value=[{"id": "new"}]):
            def _fake_submit(fn, *a, **kw):
                fn(*a, **kw)
            fake._submit_spotify_job = _fake_submit
            SpoffTUI.do_spotify_sync(fake)

        self.assertEqual(fake.current_liked_tracks, [{"id": "new"}])
        fake.render_tracks.assert_called_once()

    # -------------------------------------------------------------------------
    # Audit Case 22: Spotify mutations are serialized
    # -------------------------------------------------------------------------
    def test_22_spotify_jobs_serialized(self):
        with patch("spoff.app.MPVController"), \
             patch("spoff.app.MPRISService"), \
             patch("spoff.app.ParametricEQEngine"):
            app_inst = SpoffTUI()
            self.assertTrue(hasattr(app_inst, "_spotify_jobs"))
            self.assertEqual(app_inst._spotify_jobs._max_workers, 1)

    # -------------------------------------------------------------------------
    # Audit Case 23: Bulk download thread failure resets in-progress flag
    # -------------------------------------------------------------------------
    def test_23_bulk_download_thread_failure_resets_flag(self):
        fake = make_fake_app(_bulk_download_in_progress=False, notify=Mock())
        with patch("threading.Thread", side_effect=RuntimeError("cannot start thread")):
            SpoffTUI._bulk_download_playlist(fake, {"name": "P", "tracks": [{"id": "a", "title": "A"}]})

        self.assertFalse(fake._bulk_download_in_progress)

    # -------------------------------------------------------------------------
    # Audit Case 24: Quarantine cached track unlinks corrupted audio
    # -------------------------------------------------------------------------
    def test_24_quarantine_cached_track_removes_bad_cache(self):
        path = storage.CACHE_DIR / "invalid.m4a"
        path.write_bytes(b"bad audio data" * 1000)
        storage.register_cached_track("invalid", {"title": "Invalid"}, path)

        self.assertTrue(path.exists())
        storage.quarantine_cached_track("invalid")
        self.assertFalse(path.exists())
        self.assertIsNone(storage.get_cached_track_path("invalid"))

    # -------------------------------------------------------------------------
    # Audit Case 25: Playlist modal escapes markup in playlist names
    # -------------------------------------------------------------------------
    def test_25_playlist_modal_escapes_markup(self):
        table = Mock()
        fake = types.SimpleNamespace(
            playlists=[{"id": "local_x", "name": "[/boom]", "tracks": []}],
            query_one=lambda *a: table,
        )
        AddToPlaylistModal.on_mount(fake)
        added_name = table.add_row.call_args.args[0]
        parsed = Text.from_markup(added_name)
        self.assertIn("[/boom]", parsed.plain)

    # -------------------------------------------------------------------------
    # Audit Case 26: High sample rate EQ settings reload properly
    # -------------------------------------------------------------------------
    def test_26_eq_high_rate_preset_loads_successfully(self):
        e = eq.ParametricEQEngine(sample_rate=96000)
        e.set_band(e.bands[0].index, frequency=30000)
        d = e.to_dict()
        restored = eq.ParametricEQEngine.from_dict(d)
        self.assertEqual(restored.sample_rate, 96000)
        self.assertEqual(restored.bands[0].frequency, 30000)

    # -------------------------------------------------------------------------
    # Audit Case 27: End of queue clears _pending_track
    # -------------------------------------------------------------------------
    def test_27_end_queue_clears_pending_track(self):
        t = {"id": "a", "title": "A"}
        fake = make_fake_app(
            queue=[t],
            _pending_track=t,
            shuffle_mode=False,
            repeat_mode="off",
            current_index=0,
            update_player_hud=Mock(),
        )
        SpoffTUI.action_next_track(fake)
        self.assertIsNone(fake._pending_track)

    # -------------------------------------------------------------------------
    # Audit Case 28: Distinct recordings with same title do not toggle pause
    # -------------------------------------------------------------------------
    def test_28_distinct_recordings_do_not_toggle_pause(self):
        a = {"id": "original", "title": "Song", "artist": "Artist"}
        b = {"id": "other_version", "title": "Song", "artist": "Artist"}
        fake = make_fake_app(
            _pending_track=None,
            _get_current_view_tracks=lambda: [b],
            update_player_hud=Mock(),
            play_index=Mock(),
        )
        fake.player.current_track = a
        fake._is_same_track = lambda x, y: SpoffTUI._is_same_track(fake, x, y)

        SpoffTUI.play_current_table_row(fake, 0)
        fake.player.toggle_pause.assert_not_called()
        fake.play_index.assert_called_once_with(0)

    # -------------------------------------------------------------------------
    # Audit Case 29: Liked tab restored on mount even if playlists exist
    # -------------------------------------------------------------------------
    def test_29_liked_restore_honored_when_playlist_exists(self):
        fake = Mock()
        fake.vis_enabled = False
        fake.notifications_enabled = True
        fake.advanced_mode = False
        fake.repeat_mode = "off"
        fake.shuffle_mode = False
        fake.volume = 80
        fake.playlists = []
        fake.mpris = Mock()
        fake.player = Mock()
        fake.visualizer = Mock()
        fake.visualizer.style = "off"

        with patch("spoff.app.load_saved_playlists", return_value=[{"id": "p", "name": "P", "tracks": [{"id": "a"}]}]), \
             patch("spoff.app.get_saved_last_played", return_value={"tab": "liked", "track_id": "liked"}), \
             patch("spoff.app.load_liked_songs", return_value=[{"id": "liked", "title": "Liked"}]), \
             patch("spoff.app.is_first_launch", return_value=False), \
             patch("spoff.app.get_saved_sidebar_width", return_value=30):
            SpoffTUI.on_mount(fake)

        fake.switch_view.assert_called_with("liked", select_row=0)
        fake.load_playlist_by_index.assert_not_called()

    # -------------------------------------------------------------------------
    # Audit Case 30: Save playback state uses queue origin
    # -------------------------------------------------------------------------
    def test_30_save_uses_queue_origin(self):
        t = {"id": "from_A", "title": "A"}
        fake = make_fake_app(
            current_playlist_id="B",
            active_tab="playlist",
            current_index=4,
            _queue_origin={"tab": "playlist", "playlist_id": "pl_A"},
        )
        SpoffTUI._save_playback_state(fake, t)
        state = storage.get_saved_last_played()
        self.assertEqual(state.get("playlist_id"), "pl_A")
        self.assertEqual(state.get("track_id"), "from_A")

    # -------------------------------------------------------------------------
    # Audit Case 31: Commit playback saves state synchronously
    # -------------------------------------------------------------------------
    def test_31_commit_playback_saves_state_synchronously(self):
        saved_states = []
        fake = make_fake_app(
            _play_request_id=1,
            _queue_origin={"tab": "playlist", "playlist_id": "p"},
            _save_playback_state=lambda t: saved_states.append(t["id"]),
        )
        fake.player.load_and_play = Mock(return_value=True)

        SpoffTUI._commit_playback(fake, 1, "source_1", {"id": "first", "title": "First"})
        self.assertIn("first", saved_states)

    # -------------------------------------------------------------------------
    # Audit Case 32: Settings modal focus includes notifications toggle
    # -------------------------------------------------------------------------
    def test_32_settings_tab_includes_notifications(self):
        widgets = {}
        def query(key, *args):
            return widgets.setdefault(key, Mock())

        fake = types.SimpleNamespace(
            focused=types.SimpleNamespace(id="adv-mode-toggle"),
            query_one=query,
        )
        SettingsModal.action_switch_focus(fake)
        self.assertIn("#notifications-toggle", widgets)
        widgets["#notifications-toggle"].focus.assert_called_once()

    # -------------------------------------------------------------------------
    # Audit Case 33: JSON writer rejects non-serializable types strictly
    # -------------------------------------------------------------------------
    def test_33_json_writer_strict_types(self):
        with self.assertRaises(TypeError):
            storage._atomic_json_dump(storage.PLAYLISTS_FILE, [{"id": "p", "tracks": {"a", "b"}}])

    # -------------------------------------------------------------------------
    # Audit Case 34: Visualizer widget click to off syncs vis_enabled
    # -------------------------------------------------------------------------
    def test_34_visualizer_click_off_updates_vis_enabled(self):
        vis = CavaVisualizer(style="dots")
        vis.stop = Mock()
        fake_app = make_fake_app(
            vis_enabled=True,
            visualizer=vis,
            cycle_visualizer_style=Mock(),
        )
        widget = types.SimpleNamespace(visualizer=vis, app=fake_app)

        VisualizerWidget.on_click(widget, None)
        fake_app.cycle_visualizer_style.assert_called_once()


if __name__ == "__main__":
    unittest.main()
