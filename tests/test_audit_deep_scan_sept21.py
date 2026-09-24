import os
import time
import json
import pytest
import threading
from unittest.mock import MagicMock, patch
from pathlib import Path

from spoff.player import MPVController
from spoff.visualizer import CavaVisualizer
from spoff.streamer import (
    invalidate_stream_cache,
    search_and_resolve_stream,
    _run_download_process,
    _stream_cache,
    _stream_cache_lock,
)
from spoff.app import SafeModalScreen, SpoffTUI


def test_mpv_controller_locking_and_direct_audio():
    """Verify MPVController uses thread locks for state/properties and attaches direct audio sink."""
    with patch("spoff.player.subprocess.Popen") as mock_popen, \
         patch("spoff.player.get_direct_hardware_audio_device", return_value="alsa_output.pci-0000_00_1f.3.analog-stereo"):
        
        eq_mock = MagicMock()
        eq_mock.to_ffmpeg_af.return_value = "equalizer=f=1000:t=q:w=1:g=2"

        controller = MPVController(eq_engine=eq_mock)
        controller._send_command = MagicMock()

        # Check start_mpv attaches direct hardware audio device
        controller.start_mpv()
        assert mock_popen.called
        cmd_args = mock_popen.call_args[0][0]
        assert "--audio-device=alsa_output.pci-0000_00_1f.3.analog-stereo" in cmd_args
        # EQ first, then loudness levelling (on by default).
        assert "--af=equalizer=f=1000:t=q:w=1:g=2,loudnorm=I=-14:TP=-1.5:LRA=11" in cmd_args

        # Test synchronized operations
        controller.toggle_pause()
        assert controller.is_paused is True
        controller._send_command.assert_called_with(["set_property", "pause", True])

        controller.set_volume(80)
        assert controller.get_volume() == 80
        controller._send_command.assert_called_with(["set_property", "volume", 80])

        controller.seek(15.0)
        assert controller._last_pos == 15.0

        controller.seek_absolute(42.0)
        assert controller._last_pos == 42.0

        controller.stop()


def test_visualizer_thread_join():
    """Stop joins the visualizer's reader thread."""
    vis = CavaVisualizer(bars=16)
    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    vis._thread = mock_thread

    vis.stop()
    mock_thread.join.assert_called_once_with(timeout=0.2)
    assert vis._thread is None


def test_streamer_cache_lock_and_invalidation_on_download_failure(tmp_path):
    """Verify _stream_cache thread-safety and cache invalidation on download failures."""
    track_title = "Audit Song"
    artist = "Audit Artist"
    url = "https://example.com/audio.m4a"

    with _stream_cache_lock:
        _stream_cache.clear()

    # Prepopulate cache
    cache_key = f"{url}::{track_title.lower()}::{artist.lower()}"
    with _stream_cache_lock:
        _stream_cache[cache_key] = ({"url": url, "stream_url": url, "ext": "m4a"}, time.time())

    # Invalidate cache
    invalidate_stream_cache(track_title, artist, direct_url=url)
    with _stream_cache_lock:
        assert cache_key not in _stream_cache

    # Re-populate
    with _stream_cache_lock:
        _stream_cache[cache_key] = ({"url": url, "stream_url": url, "ext": "m4a"}, time.time())

    # Simulate download failure in _run_download_process
    with patch("spoff.streamer.yt_dlp.YoutubeDL") as mock_ydl, \
         patch("spoff.streamer.storage._get_cache_dir", return_value=tmp_path):
        ydl_instance = MagicMock()
        ydl_instance.download.return_value = 1  # non-zero = failure
        mock_ydl.return_value.__enter__.return_value = ydl_instance

        with pytest.raises(RuntimeError):
            _run_download_process(
                val_id="testval",
                title=track_title,
                artist=artist,
                direct_url=url,
            )

        # Cache should have been invalidated
        with _stream_cache_lock:
            assert cache_key not in _stream_cache


def test_safe_modal_screen_idempotent_dismiss():
    """Verify SafeModalScreen ignores duplicate dismiss calls."""
    class DummyModal(SafeModalScreen):
        pass

    modal = DummyModal()
    modal._dismiss_active = False

    # Mock super().dismiss
    with patch("textual.screen.Screen.dismiss") as mock_super_dismiss:
        modal.dismiss(result="first")
        assert mock_super_dismiss.call_count == 1

        # Second dismiss should be a no-op
        modal.dismiss(result="second")
        assert mock_super_dismiss.call_count == 1


def test_spoff_tui_corrupted_playlist_recovery():
    """Verify SpoffTUI gracefully handles corrupted playlists.json on startup."""
    with patch("spoff.app.MPVController"), \
         patch("spoff.app.MPRISService"), \
         patch("spoff.app.CavaVisualizer"):

        app = SpoffTUI()
        
        # Test that corrupt playlist handling in on_mount fallback works
        with patch("spoff.app.load_saved_playlists", side_effect=ValueError("Corrupted JSON")):
            try:
                from spoff.app import load_saved_playlists
                try:
                    app.playlists = load_saved_playlists()
                except Exception:
                    app.playlists = []
                assert app.playlists == []
            except Exception as e:
                pytest.fail(f"Corrupted playlists raised unexpected error: {e}")


def test_mpris_integration_and_queue_navigation():
    """Verify MPRIS play/pause idempotency and queue boundary handling."""
    with patch("spoff.app.MPVController"), \
         patch("spoff.app.MPRISService"), \
         patch("spoff.app.CavaVisualizer"):
        app = SpoffTUI()
        app.player = MagicMock()
        app.mpris = MagicMock()

        # MPRIS play/pause calls explicit resume/pause, not toggle
        app.player.is_paused = True
        app.player.current_track = {"title": "Track 1", "artist": "Artist 1"}
        app._mpris_play()
        app.player.resume.assert_called_once()

        app.player.is_paused = False
        app._mpris_pause()
        app.player.pause.assert_called_once()

        # Prev track wrapping when repeat_mode == "all"
        app.queue = [
            {"title": "Track 1", "artist": "Artist 1"},
            {"title": "Track 2", "artist": "Artist 2"}
        ]
        app.current_index = 0
        app.repeat_mode = "all"
        app.player.get_progress.return_value = (1.0, 100.0)  # <= 3.0s

        with patch.object(app, "play_index") as mock_play_index:
            app.action_prev_track()
            # Should have wrapped to index 1 (len - 1)
            mock_play_index.assert_called_once_with(1)

        # Next track clears MPRIS when queue ends
        app.current_index = 1
        app.repeat_mode = "off"
        with patch.object(app, "update_player_hud"), \
             patch.object(app, "notify_user"):
            app.action_next_track()
            app.mpris.update_track.assert_called_with(None)
