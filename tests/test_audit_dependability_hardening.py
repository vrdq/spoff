"""Regression and hardening tests for player, storage, and bulk download dependability."""
import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch, MagicMock

import pytest

from spoff.player import MPVController
from spoff import storage
from spoff.app import SpoffTUI


def test_watch_playback_handles_stream_close_failure_and_invokes_callback():
    controller = MPVController(socket_path="/tmp/fake_spoff.sock")
    fake_sock = Mock(spec=socket.socket)
    controller._playback_socket = fake_sock

    fake_stream = Mock()
    fake_stream.readline.return_value = json.dumps({
        "event": "end-file",
        "playlist_entry_id": 42,
        "reason": "eof"
    }).encode("utf-8")
    # Simulate stream.close() throwing BrokenPipeError / OSError
    fake_stream.close.side_effect = BrokenPipeError("Stream closed unexpectedly")

    callback_called_with = []
    def callback(reason):
        callback_called_with.append(reason)

    controller._watch_playback(fake_sock, fake_stream, 42, callback, deferred=[])

    # Callback must be invoked with "eof" despite stream.close() exception
    assert callback_called_with == ["eof"]
    # fake_sock must be closed
    fake_sock.close.assert_called()
    # controller._playback_socket must be cleared
    assert controller._playback_socket is None


def test_watch_playback_handles_sock_close_failure_and_invokes_callback():
    controller = MPVController(socket_path="/tmp/fake_spoff.sock")
    fake_sock = Mock(spec=socket.socket)
    fake_sock.close.side_effect = OSError("Socket error on close")
    controller._playback_socket = fake_sock

    fake_stream = Mock()
    fake_stream.readline.return_value = json.dumps({
        "event": "end-file",
        "playlist_entry_id": 10,
        "reason": "eof"
    }).encode("utf-8")

    callback_called_with = []
    def callback(reason):
        callback_called_with.append(reason)

    controller._watch_playback(fake_sock, fake_stream, 10, callback, deferred=[])

    assert callback_called_with == ["eof"]
    assert controller._playback_socket is None


def test_close_playback_socket_swallows_sock_close_oserror():
    controller = MPVController(socket_path="/tmp/fake_spoff.sock")
    fake_sock = Mock(spec=socket.socket)
    fake_sock.shutdown.side_effect = OSError("Transport endpoint not connected")
    fake_sock.close.side_effect = OSError("EBADF")
    controller._playback_socket = fake_sock

    # Must not raise
    controller._close_playback_socket()
    assert controller._playback_socket is None


def test_player_stop_handles_dead_or_failing_playback_thread():
    controller = MPVController(socket_path="/tmp/fake_spoff.sock")
    fake_thread = Mock(spec=threading.Thread)
    fake_thread.is_alive.return_value = False
    controller._playback_thread = fake_thread

    # Stopped without calling join on dead thread
    controller.stop()
    fake_thread.join.assert_not_called()

    # If alive but join raises
    alive_thread = Mock(spec=threading.Thread)
    alive_thread.is_alive.return_value = True
    alive_thread.join.side_effect = RuntimeError("Thread error")
    controller._playback_thread = alive_thread

    controller.stop()
    alive_thread.join.assert_called_once_with(timeout=0.5)


def test_bulk_download_metadata_repair_resilient_to_transient_missing_cache(tmp_path):
    # Setup two tracks: one with missing/unlinked cache file during stat, one valid
    track1 = {"id": "trk1", "title": "Track 1", "artist": "Artist 1", "duration_ms": 100000}
    track2 = {"id": "trk2", "title": "Track 2", "artist": "Artist 2", "duration_ms": 200000}

    cache_file1 = Mock(spec=Path)
    cache_file1.stat.side_effect = FileNotFoundError("File unlinked")

    real_file2 = tmp_path / "trk2.m4a"
    real_file2.write_bytes(b"dummy audio content")

    saved_indexes = []
    mock_idx = {}

    def fake_get_cached(tid):
        if tid == "trk1":
            return cache_file1
        elif tid == "trk2":
            return real_file2
        return None

    fake_app = SimpleNamespace(
        active_tab="playlist",
        current_playlist_tracks=[track1, track2],
        search_results=[],
        current_liked_tracks=[],
        call_from_thread=lambda fn, *a: fn(*a),
        notify_user=Mock(),
        set_download_status=Mock(),
    )

    spawned_threads = []
    real_thread = threading.Thread
    def intercept_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        spawned_threads.append(t)
        return t

    with patch("spoff.app.load_offline_index", return_value=mock_idx), \
         patch("spoff.app.save_offline_index", side_effect=lambda idx: saved_indexes.append(dict(idx))), \
         patch("spoff.app.get_cached_track_path", side_effect=fake_get_cached), \
         patch("spoff.app.cached_audio_matches_duration", return_value=True), \
         patch("spoff.app.download_track_to_cache"), \
         patch("threading.Thread", side_effect=intercept_thread):

        SpoffTUI._bulk_download_playlist(fake_app, {"name": "Test PL", "tracks": [track1, track2]})
        for t in spawned_threads:
            t.join(timeout=3.0)

    # Metadata repair should NOT abort when track1's file disappears:
    # track2 must be repaired in index
    assert len(saved_indexes) > 0
    last_idx = saved_indexes[-1]
    assert "trk2" in last_idx
    assert last_idx["trk2"]["title"] == "Track 2"


def test_reconcile_offline_cache_resilient_to_transient_unlink(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    good_file = cache_dir / "validtrack1.m4a"
    good_file.write_bytes(b"some audio data")

    ghost_file = cache_dir / "ghosttrack.m4a"
    ghost_file.write_bytes(b"transient data")

    with patch.object(storage, "_get_cache_dir", return_value=cache_dir):
        # First test normal reconciliation
        idx, changed = storage._reconcile_offline_cache({})
        assert "validtrack1" in idx
        assert "ghosttrack" in idx

        # Now simulate ghost_file disappearing during stat in second pass
        orig_stat = Path.stat
        def selective_stat(path_obj, *args, **kwargs):
            if path_obj.name == "ghosttrack.m4a":
                raise FileNotFoundError("Unlinked concurrently")
            return orig_stat(path_obj, *args, **kwargs)

        with patch.object(Path, "stat", selective_stat):
            idx2, changed2 = storage._reconcile_offline_cache({})
            # validtrack1 must still be reconciled successfully
            assert "validtrack1" in idx2


def test_register_cached_track_handles_stat_failure(tmp_path):
    missing_file = tmp_path / "nonexistent.m4a"
    with pytest.raises(ValueError, match="Incomplete cached audio file"):
        storage.register_cached_track("track123", {"title": "T", "artist": "A"}, missing_file)
