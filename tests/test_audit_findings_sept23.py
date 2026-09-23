"""
Regression tests verifying all 13 audit findings from 23 September 2026.
Asserts that each defect identified in spoff-audit/2026-09-23/REPORT.md is fixed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from spoff import storage, auth, player, eq, streamer, app, mpris


def track(identifier, title=None, artist="Artist"):
    return dict(id=identifier, title=title or identifier, artist=artist)


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


class TestAuditFindingsSept23(unittest.TestCase):
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
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = self.temp_path / "playlists.json"
        storage.LIKED_SONGS_FILE = self.temp_path / "liked_songs.json"
        storage.CONFIG_FILE = self.temp_path / "config.json"
        storage.INDEX_FILE = self.temp_path / "offline_index.json"
        auth.AUTH_FILE = self.temp_path / "spotify_auth.json"

    def tearDown(self):
        storage.DATA_DIR = self.orig_data_dir
        storage.CACHE_DIR = self.orig_cache_dir
        storage.PLAYLISTS_FILE = self.orig_playlists_file
        storage.LIKED_SONGS_FILE = self.orig_liked_file
        storage.CONFIG_FILE = self.orig_config_file
        storage.INDEX_FILE = self.orig_index_file
        auth.AUTH_FILE = self.orig_auth_file
        self.temp_dir.cleanup()

    def test_01_supported_python_can_import_storage(self):
        """Finding 1: Python 3.10-3.13 can import spoff.storage without NameError for Tuple."""
        res = subprocess.run(
            [sys.executable, "-c", "import spoff.storage; print('OK')"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("OK", res.stdout)

    def test_02_canceled_login_does_not_save_credentials(self):
        """Finding 2: Canceled/dismissed Spotify login does not commit credentials."""
        workers = []
        fake_modal = types.SimpleNamespace(
            query_one=Mock(),
            pkce_verifier="verifier",
            server=Mock(),
            dismiss=Mock(),
            app=types.SimpleNamespace(call_from_thread=lambda fn: fn()),
        )

        def capture(target, **kw):
            workers.append(target)
            return types.SimpleNamespace(start=lambda: None)

        with patch.object(app.threading, "Thread", side_effect=capture), \
             patch.object(app, "exchange_code_for_tokens", return_value={"access_token": "canceled-account"}), \
             patch.object(app, "fetch_current_user_profile", return_value={"id": "old-account"}):
            app.SpotifyAuthModal.process_auth_code(fake_modal, "code")
            app.SpotifyAuthModal.action_dismiss_modal(fake_modal)
            auth.logout_spotify()
            workers[0]()

        self.assertIsNone(auth.load_spotify_auth())

    def test_03_aborted_migration_preserves_legacy_containers(self):
        """Finding 3: Malformed legacy container aborts migration and preserves all containers."""
        good = {"id": "spotify_liked_songs", "name": "Liked", "tracks": [{"id": "valuable", "title": "Song", "artist": "Artist"}]}
        bad = {"id": "spotify_liked_songs", "name": "Liked", "tracks": "recoverable malformed data"}
        storage.save_saved_playlists([good, bad])

        storage.create_local_playlist("New playlist")
        saved = json.loads(storage.PLAYLISTS_FILE.read_text())
        saved_ids = [p["id"] for p in saved]
        self.assertIn("spotify_liked_songs", saved_ids)
        # Verify valid container tracks were not lost
        valid_containers = [p for p in saved if p.get("id") == "spotify_liked_songs" and isinstance(p.get("tracks"), list)]
        self.assertEqual(len(valid_containers), 1)
        self.assertEqual(valid_containers[0]["tracks"][0]["id"], "valuable")

    def test_04_sync_preserves_unmigrated_legacy_container(self):
        """Finding 3 / 4: Spotify sync does not drop unmigrated legacy containers."""
        legacy = {"id": "spotify_liked_songs", "name": "Liked", "tracks": "recoverable malformed data"}
        storage.save_saved_playlists([legacy])

        with patch.object(auth, "fetch_liked_songs", return_value=[]), \
             patch.object(auth, "fetch_user_playlists", return_value=[]):
            auth.sync_spotify_library("fake-token")

        saved = json.loads(storage.PLAYLISTS_FILE.read_text())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["id"], "spotify_liked_songs")

    def test_05_auto_headroom_handles_supported_high_frequency_band(self):
        """Finding 4: Auto headroom handles high-frequency bands above 20 kHz."""
        preset = eq.EQPreset("High rate", "", 0, [eq.EQBand(1, eq.FilterType.PEAKING, 30000, 12, 10)])
        engine = eq.ParametricEQEngine(preset, sample_rate=96000)
        engine.set_auto_headroom(True)

        peak_gain, peak_freq = engine.calculate_peak_gain()
        self.assertLess(peak_gain, 0.0)
        self.assertLess(engine.get_magnitude_at_freq(30000), 0.0)

    def test_06_missing_files_pruned_from_offline_library(self):
        """Finding 5: Disappeared audio files are pruned from offline index."""
        storage.save_offline_index({
            "gone": {"id": "gone", "title": "Gone", "artist": "Artist", "filepath": str(storage.CACHE_DIR / "gone.m4a")}
        })
        self.assertNotIn("gone", storage.load_offline_index())
        self.assertIsNone(storage.get_cached_track_path("gone"))

    def test_07_valid_small_audio_registered_and_retrieved(self):
        """Finding 6: Valid compressed audio files under 10 KB are accepted."""
        tiny_path = storage.CACHE_DIR / "tiny_id.m4a"
        tiny_path.write_bytes(b"M4A_SAMPLE_DATA_500_BYTES" * 20)
        self.assertLess(tiny_path.stat().st_size, 10000)

        storage.register_cached_track("tiny_id", {"title": "Tiny Track", "artist": "Artist"}, tiny_path)
        cached = storage.get_cached_track_path("tiny_id")
        self.assertIsNotNone(cached)
        self.assertEqual(Path(cached), tiny_path)

    def test_08_stable_track_id_deterministic(self):
        """Finding 7: Fallback track identity is deterministic and collision-resistant."""
        id1 = storage.stable_track_id({"title": "Song", "artist": "Artist"})
        id2 = storage.stable_track_id({"title": "Song", "artist": "Artist"})
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("local_"))

        # Separator prevents ambiguous concatenation collisions ("ab" + "c" vs "a" + "bc")
        id_ab_c = storage.stable_track_id({"title": "ab", "artist": "c"})
        id_a_bc = storage.stable_track_id({"title": "a", "artist": "bc"})
        self.assertNotEqual(id_ab_c, id_a_bc)

    def test_09_obsolete_playback_requests_do_not_launch_work(self):
        """Finding 8: Obsolete or closing playback requests do not launch threads or resolvers."""
        fake = make_fake_app(_closing=True, _play_request_id=99)
        t = {"id": "a", "title": "Song", "artist": "Artist"}

        with patch.object(app.threading, "Thread") as threads, \
             patch.object(app, "get_cached_artwork", return_value={}), \
             patch.object(app, "get_cached_track_path", return_value=None), \
             patch.object(app, "search_and_resolve_stream", return_value=None) as resolver:
            for req in range(20):
                app.SpoffTUI.start_playback.__wrapped__(fake, t, req)

        self.assertEqual(threads.call_count, 0)
        self.assertEqual(resolver.call_count, 0)

    def test_10_exit_preserves_queued_remote_edits(self):
        """Finding 9: Application exit uses cancel_futures=False on spotify jobs executor."""
        entered, release = threading.Event(), threading.Event()
        executor = ThreadPoolExecutor(max_workers=1)

        def first():
            entered.set()
            release.wait(3)

        executor.submit(first)
        self.assertTrue(entered.wait(1))
        remote_edit = Mock()
        pending = executor.submit(remote_edit)
        fake = make_fake_app(_spotify_jobs=executor, _cleanup_on_exit=Mock())

        try:
            with patch.object(app, "_set_kitty_opacity"):
                app.SpoffTUI._cleanup_on_exit(fake)
            self.assertFalse(pending.cancelled())
        finally:
            release.set()
            executor.shutdown(wait=True)

    def test_11_client_duplicates_preserved_in_sync_merge(self):
        """Finding 10: Spotify sync merge preserves intentional client duplicate tracks."""
        client = [
            {"id": "c1", "title": "Dupe", "artist": "A"},
            {"id": "c2", "title": "Dupe", "artist": "A"},
        ]
        spotify = [
            {"id": "s1", "title": "Different", "artist": "B"},
        ]
        merged = auth.merge_spotify_and_client_tracks(spotify, client)
        self.assertEqual(len(merged), 3)

    def test_12_mpris_duration_handling_and_seek(self):
        """Finding 11: MPRIS unknown duration allows seeking and duration updates dynamically."""
        mock_set_pos = Mock()
        service = mpris.MPRISService({"set_position": mock_set_pos})
        dbus_obj = mpris.SpoffMPRISDbus({"set_position": mock_set_pos}, service=service)
        dbus_obj.Metadata = {"mpris:trackid": "/org/mpris/MediaPlayer2/track/1"}
        dbus_obj.CanSeek = True
        service.dbus_obj = dbus_obj

        # SetPosition should not be rejected when duration is unknown/None
        dbus_obj.SetPosition("/org/mpris/MediaPlayer2/track/1", 5000000)
        mock_set_pos.assert_called_with(5.0)

        # Updating duration sets length metadata
        service.update_duration(120.0)
        self.assertEqual(dbus_obj.Metadata.get("mpris:length").unpack(), 120000000)

    def test_13_main_propagates_fatal_exceptions(self):
        """Finding 12: Fatal exceptions in main exit with non-zero status."""
        code = "import sys; from unittest.mock import patch; from spoff.app import main; " \
               "patch('spoff.app.SpoffTUI.run', side_effect=RuntimeError('Fatal')).start(); " \
               "sys.exit(main())"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_14_bulk_download_avoids_repeated_full_rescan(self):
        """Finding 13: Bulk download performs reconciliation in background, not on UI thread."""
        fake = make_fake_app(
            _bulk_download_in_progress=False,
            playlists=[{"id": "pl1", "name": "PL", "tracks": [{"id": f"t{i}", "title": f"T{i}", "artist": "A"} for i in range(10)]}],
            active_tab="playlists",
            current_playlist_idx=0,
        )

        with patch.object(storage, "_reconcile_offline_cache") as reconcile, \
             patch.object(app.threading, "Thread") as mock_thread:
            app.SpoffTUI._bulk_download_playlist(fake, fake.playlists[0])

        # On the UI thread, _reconcile_offline_cache should not be called in a loop
        self.assertEqual(reconcile.call_count, 0)
        mock_thread.assert_called_once()
