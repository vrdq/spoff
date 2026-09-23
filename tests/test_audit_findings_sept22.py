"""
Regression tests verifying all 16 audit findings from 22 September 2026.
Asserts that each defect identified in spoff-audit/2026-09-22/REPORT.md is fixed.
"""

import ast
import json
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spoff import storage, auth, player, eq, streamer, ytmusic, app


def track(identifier):
    return dict(id=identifier, title=identifier, artist="Artist")


def make_fake_app(**kwargs):
    values = dict(
        notify_user=Mock(),
        render_tracks=Mock(),
        query_one=lambda *a: Mock(),
        player=Mock(),
        mpris=Mock(),
        _closing=False,
        _play_request_id=1,
        active_tab="search",
        current_index=0,
        queue=[],
        _failed_indices=set(),
        _pending_track=None,
        call_from_thread=lambda f, *a, **k: f(*a, **k),
        _on_ui=lambda f, *a, **k: f(*a, **k),
        update_player_hud=Mock(),
        _save_playback_state=Mock(),
    )
    values.update(kwargs)
    return types.SimpleNamespace(**values)


class TestAuditFindingsSept22(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.orig_data_dir = storage.DATA_DIR
        self.orig_cache_dir = storage.CACHE_DIR
        self.orig_playlists_file = storage.PLAYLISTS_FILE
        self.orig_liked_file = storage.LIKED_SONGS_FILE
        self.orig_config_file = storage.CONFIG_FILE
        self.orig_index_file = storage.INDEX_FILE
        self.orig_auth_file = auth.AUTH_FILE

        storage.DATA_DIR = self.temp_path
        storage.CACHE_DIR = self.temp_path / "cache"
        storage.PLAYLISTS_FILE = self.temp_path / "playlists.json"
        storage.LIKED_SONGS_FILE = self.temp_path / "liked_songs.json"
        storage.CONFIG_FILE = self.temp_path / "config.json"
        storage.INDEX_FILE = self.temp_path / "offline_index.json"
        auth.AUTH_FILE = self.temp_path / "spotify_auth.json"

        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)

        storage.save_config({})
        storage.save_liked_songs([])
        storage.save_saved_playlists([])
        storage.save_offline_index({})

    def tearDown(self):
        storage.DATA_DIR = self.orig_data_dir
        storage.CACHE_DIR = self.orig_cache_dir
        storage.PLAYLISTS_FILE = self.orig_playlists_file
        storage.LIKED_SONGS_FILE = self.orig_liked_file
        storage.CONFIG_FILE = self.orig_config_file
        storage.INDEX_FILE = self.orig_index_file
        auth.AUTH_FILE = self.orig_auth_file
        self.temp_dir.cleanup()

    # Finding 1: Liked song reordering preserves concurrent additions
    def test_01_liked_reorder_preserves_concurrent_addition(self):
        a, b, added = track("a"), track("b"), track("new")
        storage.save_liked_songs([added, a, b])
        table = Mock(cursor_row=1)
        f = make_fake_app(
            focused=types.SimpleNamespace(id="track-table"),
            active_tab="liked",
            current_liked_tracks=[a, b],
            query_one=lambda *a: table,
        )
        app.SpoffTUI.action_move_item_up(f)
        # Fix: 'new' is not overwritten; result is ['new', 'b', 'a']
        self.assertEqual([t["id"] for t in storage.load_liked_songs()], ["new", "b", "a"])

    # Finding 2: Missing identifiers do not match wrong tracks
    def test_02_missing_uri_makes_stale_index_match_correct_track(self):
        a, b, x = track("a"), track("b"), track("x")
        storage.save_saved_playlists([dict(id="p", tracks=[x, a, b])])
        self.assertTrue(storage.move_playlist_track("p", b, -1, index=1))
        # Fix: moving 'b' up targets 'b' (at index 2) to swap with 'a' (at index 1)
        # Result must be ['x', 'b', 'a'], NOT accidentally matching 'x' at index 0
        self.assertEqual([t["id"] for t in storage.load_saved_playlists()[0]["tracks"]], ["x", "b", "a"])

    # Finding 3: Playback persistence does not overwrite browsed tab
    def test_03_cleanup_preserves_browsing_tab(self):
        storage.save_last_tab("liked")
        committed = dict(tab="playlist", playlist_id="A", track_id="a", track_index=0)
        f = make_fake_app(
            active_tab="liked",
            current_playlist_id="B",
            _committed_playback=committed,
            visualizer=Mock(),
            _cleanup_on_exit=Mock(),
        )
        f._save_playback_state = lambda: app.SpoffTUI._save_playback_state(f)
        with patch.object(app, "_set_kitty_opacity"):
            app.SpoffTUI._cleanup_on_exit(f)
        # Fix: last_tab remains 'liked'
        self.assertEqual(storage.get_saved_last_tab(), "liked")

    # Property listeners must not guess which load owns an event.
    # Full load/EOF routing is exercised in test_playback_ipc_ownership.py.
    def test_04_property_listener_does_not_consume_playback_callbacks(self):
        controller = player.MPVController()
        first, second = Mock(), Mock()
        controller.register_pending_callback(first, 1)
        controller.register_pending_callback(second, 2)
        stop = threading.Event()
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock(return_value=False)
        events = [
            dict(event="start-file", playlist_entry_id=10),
            dict(event="end-file", playlist_entry_id=10, reason="stop"),
            dict(event="start-file", playlist_entry_id=11),
            dict(event="end-file", playlist_entry_id=11, reason="eof"),
        ]

        def recv(size):
            if events:
                return (json.dumps(events.pop(0)) + "\n").encode()
            stop.set()
            return b""

        sock.recv.side_effect = recv
        with patch.object(player.os.path, "exists", return_value=True), patch.object(player.socket, "socket", return_value=sock):
            controller._ipc_listener(stop)

        first.assert_not_called()
        second.assert_not_called()

    # Finding 5: Deleting pending track cancels playback request
    def test_05_delete_pending_track_cancels_playback_request(self):
        a, b = track("a"), track("b")
        f = make_fake_app(
            focused=types.SimpleNamespace(id="track-table", cursor_row=0),
            active_tab="search",
            search_results=[a, b],
            queue=[a, b],
            current_index=0,
            _pending_track=a,
            _play_request_id=1,
            update_player_hud=Mock(),
            _save_playback_state=Mock(),
        )
        app.SpoffTUI.action_delete_item(f)
        self.assertEqual(f.queue, [b])
        # Fix: _pending_track is cleared and late commit is rejected
        self.assertIsNone(f._pending_track)
        self.assertFalse(app.SpoffTUI._commit_playback(f, 1, "fake-source", a))
        f.player.load_and_play.assert_not_called()

    # Finding 6: Malformed EQ clipboard does not crash UI action
    def test_06_invalid_clipboard_eq_does_not_crash_modal(self):
        status_mock = Mock()
        f = types.SimpleNamespace(
            app=Mock(),
            query_one=lambda sel, *a: status_mock,
            notify=Mock(),
            engine=Mock(),
        )
        with patch.object(app, "read_from_clipboard", return_value="Filter 1: ON PK Fc 1000 Hz Gain 99 dB Q 1"):
            # Fix: Gracefully handles ValueError and displays status message
            app.EQSettingsModal.import_from_clipboard(f)
            f.notify.assert_called_once()
            self.assertEqual(f.notify.call_args[1].get("severity"), "error")

    # Finding 7: Lowering sample rate clamps frequencies within Nyquist
    def test_07_lower_sample_rate_clamps_band_frequencies(self):
        preset = eq.EQPreset("High rate", "", 0, [eq.EQBand(1, eq.FilterType.PEAKING, 30000, 2, 1)])
        engine = eq.ParametricEQEngine(preset, sample_rate=96000)
        engine.set_sample_rate(44100)
        # Fix: Band frequency clamped to <= 44100 * 0.495 (~21829.5)
        self.assertNotIn("f=30000.0", engine.to_ffmpeg_af())
        reloaded = eq.ParametricEQEngine.from_dict(engine.to_dict())
        self.assertEqual(reloaded.sample_rate, 44100)

    # Finding 8: Editing EQ while bypassed retains automatic headroom
    def test_08_edit_while_bypassed_preserves_automatic_headroom(self):
        preset = eq.EQPreset("Boost", "", -7, [eq.EQBand(1, eq.FilterType.PEAKING, 1000, 6, 1)])
        engine = eq.ParametricEQEngine(preset, bypassed=True)
        engine.set_band(1, gain_db=12)
        # Fix: Preamp remains attenuated (< -12 dB), not zeroed
        self.assertLess(engine.preamp_db, -11.0)
        engine.set_bypassed(False)
        self.assertLess(engine.get_magnitude_at_freq(1000), 1.0)

    # Finding 9: Share action targets highlighted track in Liked Songs tab
    def test_09_share_liked_song_copies_selected_track(self):
        f = make_fake_app(
            _is_ready=True,
            active_tab="liked",
            focused=Mock(spec=app.DataTable, id="track-table", cursor_row=0),
            current_liked_tracks=[track("liked")],
        )
        f.player.current_track = None
        app.SpoffTUI.action_share_track(f)
        # Fix: Share action detects the selected liked track and copies its link
        f.notify_user.assert_called_once_with("Copied YouTube Music link for 'liked' to clipboard")

    # Finding 10: Synthesized direct URLs are purged from stream cache
    def test_10_synthesized_direct_url_cache_invalidated_on_error(self):
        t = dict(id="abcdefghijk", title="Song", artist="Artist")
        key = "https://www.youtube.com/watch?v=abcdefghijk::song::artist"
        f = make_fake_app(update_player_hud=Mock())
        with patch.dict(streamer._stream_cache, {key: ({"stream_url": "expired"}, 0)}, clear=True):
            app.SpoffTUI._playback_failed(f, 1, t)
            # Fix: Cached entry with synthesized URL is removed
            self.assertNotIn(key, streamer._stream_cache)

    # Finding 11: Spotify profile refresh does not overwrite fresh credentials
    def test_11_spotify_profile_refresh_does_not_overwrite_fresh_tokens(self):
        stale = dict(access_token="old", refresh_token="old_refresh", expires_at=0)
        fresh = dict(access_token="fresh", refresh_token="new_refresh", expires_at=9999999999)
        auth.save_spotify_auth(stale)
        f = types.SimpleNamespace(
            auth_session=dict(stale),
            is_mounted=True,
            query_one=Mock(),
            app=types.SimpleNamespace(call_from_thread=lambda fn: fn()),
        )
        with patch("spoff.app.get_valid_token", return_value="fresh"), \
             patch("spoff.app.fetch_current_user_profile", return_value=dict(display_name="Test")), \
             patch("threading.Thread", side_effect=lambda target, **kw: types.SimpleNamespace(start=target)):
            auth.save_spotify_auth(fresh)
            app.SpotifyAuthModal.on_mount(f)
        # Fix: auth credentials on disk remain fresh
        self.assertEqual(auth.load_spotify_auth()["access_token"], "fresh")
        self.assertEqual(auth.load_spotify_auth()["refresh_token"], "new_refresh")

    # Finding 12: Offline index normalizes records and prevents render crashes
    def test_12_offline_index_normalizes_records(self):
        storage.save_offline_index({"broken": {"title": [], "artist": "Artist"}})
        loaded = list(storage.load_offline_index().values())
        self.assertEqual(loaded[0]["id"], "broken")
        self.assertEqual(loaded[0]["title"], "Unknown Track")

        table_mock = Mock(cursor_row=None)
        f = make_fake_app(query_one=lambda *a: table_mock)
        # Fix: render_tracks executes without TypeError
        app.SpoffTUI.render_tracks(f, loaded)

    # Finding 13: ParametricEQEngine retains anti-denormal compatibility
    def test_13_anti_denormal_backward_compatibility(self):
        engine = eq.ParametricEQEngine()
        self.assertTrue(engine.anti_denormal)
        engine.set_anti_denormal(False)
        self.assertFalse(engine.anti_denormal)

    # Finding 14: String infinite duration handled safely without OverflowError
    def test_14_safe_duration_ms_handles_inf_and_nan(self):
        self.assertEqual(ytmusic._safe_duration_ms("inf"), 0)
        self.assertEqual(ytmusic._safe_duration_ms("-inf"), 0)
        self.assertEqual(ytmusic._safe_duration_ms("nan"), 0)
        self.assertEqual(ytmusic._safe_duration_ms(float("inf")), 0)
        self.assertEqual(ytmusic._safe_duration_ms(float("nan")), 0)

    # Finding 15: Subprocess launches do not use preexec_fn
    def test_15_subprocess_launches_without_unsafe_preexec(self):
        with patch("subprocess.Popen") as mock_popen, \
             patch("spoff.player.get_direct_hardware_audio_device", return_value=None):
            ctl = player.MPVController()
            ctl.start_mpv()
            self.assertTrue(mock_popen.called)
            kwargs = mock_popen.call_args[1]
            self.assertNotIn("preexec_fn", kwargs)

    # Finding 16: SpotifyAuthModal compose does not block event loop
    def test_16_spotify_auth_nonblocking_event_loop(self):
        source_code = (Path(__file__).parent.parent / "spoff/app.py").read_text(encoding="utf-8")
        tree = ast.parse(source_code)
        modal_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SpotifyAuthModal")
        compose_fn = next(n for n in modal_cls.body if isinstance(n, ast.FunctionDef) and n.name == "compose")
        calls = [node.func.id for node in ast.walk(compose_fn) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        self.assertNotIn("get_valid_token", calls)

    def test_failed_reorder_reloads_library_without_moving_queue(self):
        a, b = track("a"), track("b")
        storage.save_liked_songs([a])  # b removed since rendering.
        f = make_fake_app(current_liked_tracks=[a, b], queue=[a, b], current_index=1)
        app.SpoffTUI._reorder_liked_song(f, 1, -1)
        self.assertEqual([t["id"] for t in f.current_liked_tracks], ["a"])
        self.assertEqual(f.queue, [a, b])
        self.assertEqual(f.current_index, 1)

    def test_reorder_keeps_playing_occurrence_when_library_has_new_rows(self):
        a, b, c, added = (track(x) for x in ("a", "b", "c", "new"))
        storage.save_liked_songs([added, a, b, c])
        f = make_fake_app(current_liked_tracks=[a, b, c], queue=[a, b, c], current_index=1)
        app.SpoffTUI._reorder_liked_song(f, 1, 1)
        self.assertEqual([t["id"] for t in f.current_liked_tracks], ["new", "a", "c", "b"])
        self.assertEqual([t["id"] for t in f.queue], ["a", "c", "b"])
        self.assertIs(f.queue[f.current_index], b)

    def test_deleting_other_duplicate_preserves_pending_occurrence(self):
        a = track("a")
        f = make_fake_app(
            focused=types.SimpleNamespace(id="track-table", cursor_row=0),
            active_tab="search", search_results=[a, a], queue=[a, a],
            current_index=1, _pending_track=a,
        )
        app.SpoffTUI.action_delete_item(f)
        self.assertEqual(f._play_request_id, 1)
        self.assertIs(f._pending_track, a)
        self.assertTrue(app.SpoffTUI._commit_playback(f, 1, "source", a))

    def test_unlike_pending_track_rejects_late_commit(self):
        a, b = track("a"), track("b")
        storage.save_liked_songs([a, b])
        focused = Mock(spec=app.DataTable, id="track-table", cursor_row=0)
        f = make_fake_app(
            _is_ready=True, active_tab="liked", focused=focused,
            current_liked_tracks=[a, b], queue=[a, b], current_index=0,
            _pending_track=a, _get_current_view_tracks=lambda: [a, b],
            _submit_spotify_job=Mock(),
        )
        f.player.current_track = None
        app.SpoffTUI.action_like_track(f)
        self.assertIsNone(f._pending_track)
        self.assertFalse(app.SpoffTUI._commit_playback(f, 1, "source", a))

    def test_high_rate_clipboard_import_uses_engine_sample_rate(self):
        engine = eq.ParametricEQEngine(sample_rate=96000)
        f = types.SimpleNamespace(app=Mock(), query_one=Mock(), notify=Mock(),
                                  engine=engine, _sync_and_save=Mock())
        text = "Filter 1: ON PK Fc 30000 Hz Gain 2 dB Q 1"
        with patch.object(app, "read_from_clipboard", return_value=text):
            app.EQSettingsModal.import_from_clipboard(f)
        self.assertEqual(engine.bands[0].frequency, 30000)
        f._sync_and_save.assert_called_once()

    def test_invalid_import_does_not_mutate_engine(self):
        engine = eq.ParametricEQEngine()
        before = engine.to_dict()
        f = types.SimpleNamespace(app=Mock(), query_one=Mock(), notify=Mock(), engine=engine)
        with patch.object(app, "read_from_clipboard", return_value="Filter 1: ON PK Fc 1000 Hz Gain 99 dB Q 1"):
            app.EQSettingsModal.import_from_clipboard(f)
        self.assertEqual(engine.to_dict(), before)

    def test_commit_rejects_missing_queue_occurrence(self):
        a = track("a")
        f = make_fake_app(_pending_track=a, queue=[], current_index=-1)
        self.assertFalse(app.SpoffTUI._commit_playback(f, 1, "source", a))
        f.player.load_and_play.assert_not_called()

    def test_profile_result_cannot_restore_logged_out_session(self):
        credentials = dict(access_token="token", refresh_token="refresh", expires_at=9999999999)
        auth.save_spotify_auth(credentials)
        f = types.SimpleNamespace(auth_session=dict(credentials), query_one=Mock(), is_mounted=True,
                                  app=types.SimpleNamespace(call_from_thread=Mock()))
        def profile(token):
            auth.logout_spotify()
            return {"display_name": "Old account"}
        with patch.object(app, "get_valid_token", return_value="token"), \
             patch.object(app, "fetch_current_user_profile", side_effect=profile), \
             patch.object(app.threading, "Thread", side_effect=lambda target, **kw: types.SimpleNamespace(start=target)):
            app.SpotifyAuthModal.on_mount(f)
        self.assertIsNone(auth.load_spotify_auth())
        f.app.call_from_thread.assert_not_called()

    def test_profile_worker_publishes_modal_state_only_on_ui_thread(self):
        credentials = dict(access_token="token", refresh_token="refresh", expires_at=9999999999)
        auth.save_spotify_auth(credentials)
        entered, release = threading.Event(), threading.Event()
        updates, threads = [], []
        real_thread = threading.Thread
        def spawn(**kwargs):
            thread = real_thread(**kwargs)
            threads.append(thread)
            return thread
        def token():
            entered.set()
            assert release.wait(2)
            return "token"
        f = types.SimpleNamespace(auth_session=dict(credentials), query_one=Mock(), is_mounted=True,
                                  app=types.SimpleNamespace(call_from_thread=updates.append))
        with patch.object(app, "get_valid_token", side_effect=token), \
             patch.object(app, "fetch_current_user_profile", return_value={"display_name": "Test"}), \
             patch.object(app.threading, "Thread", side_effect=spawn):
            try:
                app.SpotifyAuthModal.on_mount(f)
                self.assertTrue(entered.wait(1))
                self.assertNotIn("user", f.auth_session)
            finally:
                release.set()
                for thread in threads:
                    thread.join(2)
        self.assertNotIn("user", f.auth_session)
        self.assertEqual(len(updates), 1)
        updates[0]()
        self.assertEqual(f.auth_session["user"]["display_name"], "Test")
