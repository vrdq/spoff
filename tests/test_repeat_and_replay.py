import types
import unittest
from unittest.mock import Mock, patch

from spoff.player import MPVController
from spoff.app import SpoffTUI
from spoff.visualizer import VisualizerWidget


def make_fake_app(**kwargs):
    values = {
        "focused": None,
        "notify_user": Mock(),
        "render_tracks": Mock(),
        "query_one": lambda *a: Mock(),
        "player": Mock(),
        "mpris": Mock(),
        "_shutting_down": False,
        "_play_request_id": 1,
        "active_tab": "playlist",
        "current_index": 0,
        "queue": [],
        "_failed_indices": set(),
        "_shuffle_history": [],
        "call_from_thread": lambda f, *a, **k: f(*a, **k),
        "_on_ui": lambda f, *a, **k: f(*a, **k),
        "update_player_hud": Mock(),
        "play_index": Mock(),
        "_is_same_track": lambda t1, t2: (
            isinstance(t1, dict) and isinstance(t2, dict) and t1.get("id") == t2.get("id")
        ),
        "_is_track_offline": lambda t: False,
        "_get_current_view_tracks": lambda: [],
    }
    values.update(kwargs)
    return types.SimpleNamespace(**values)


class TestRepeatAndReplay(unittest.TestCase):
    def test_mpv_started_with_no_config_and_keep_open_no(self):
        controller = MPVController(socket_path="/tmp/test_spoff_mpv_flags.sock")
        with patch("subprocess.Popen") as mock_popen, \
             patch("os.path.exists", return_value=True), \
             patch("threading.Thread"):
            controller.start_mpv()
            self.assertTrue(mock_popen.called)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--no-config", cmd)
            self.assertIn("--keep-open=no", cmd)

    def test_mpv_is_active_property(self):
        controller = MPVController(socket_path="/tmp/test_spoff_mpv_active.sock")
        # Initially no playback socket
        self.assertFalse(controller.is_active)

        # When playback socket is set
        controller._playback_socket = Mock()
        self.assertTrue(controller.is_active)

        # After playback socket is cleared (e.g. on EOF)
        controller._playback_socket = None
        self.assertFalse(controller.is_active)

        # On spotify
        controller._on_spotify = True
        mock_spotify = Mock()
        mock_spotify.is_active = True
        controller.spotify = mock_spotify
        self.assertTrue(controller.is_active)
        mock_spotify.is_active = False
        self.assertFalse(controller.is_active)

    def test_play_current_table_row_replays_finished_or_inactive_track(self):
        track = {"id": "t1", "title": "Track 1", "artist": "Artist 1"}
        mock_player = Mock()
        mock_player.current_track = track
        mock_player.get_progress.return_value = (180.0, 180.0)
        mock_player.is_active = False

        fake_app = make_fake_app(
            player=mock_player,
            queue=[track],
            current_index=0,
            _get_current_view_tracks=lambda: [track],
        )

        SpoffTUI.play_current_table_row(fake_app, 0)
        fake_app.play_index.assert_called_once_with(0)
        mock_player.toggle_pause.assert_not_called()

    def test_play_current_table_row_replays_when_at_end_of_duration(self):
        track = {"id": "t1", "title": "Track 1", "artist": "Artist 1"}
        mock_player = Mock()
        mock_player.current_track = track
        # Pos is within 1s of duration
        mock_player.get_progress.return_value = (179.8, 180.0)
        mock_player.is_active = True

        fake_app = make_fake_app(
            player=mock_player,
            queue=[track],
            current_index=0,
            _get_current_view_tracks=lambda: [track],
        )

        SpoffTUI.play_current_table_row(fake_app, 0)
        fake_app.play_index.assert_called_once_with(0)
        mock_player.toggle_pause.assert_not_called()

    def test_play_current_table_row_toggles_pause_mid_song(self):
        track = {"id": "t1", "title": "Track 1", "artist": "Artist 1"}
        mock_player = Mock()
        mock_player.current_track = track
        mock_player.get_progress.return_value = (45.0, 180.0)
        mock_player.is_active = True

        fake_app = make_fake_app(
            player=mock_player,
            queue=[track],
            current_index=0,
            _get_current_view_tracks=lambda: [track],
        )

        SpoffTUI.play_current_table_row(fake_app, 0)
        mock_player.toggle_pause.assert_called_once()
        fake_app.play_index.assert_not_called()

    def test_action_toggle_play_replays_when_inactive_or_at_end(self):
        track = {"id": "t1", "title": "Track 1", "artist": "Artist 1"}
        mock_player = Mock()
        mock_player.current_track = track
        mock_player.get_progress.return_value = (180.0, 180.0)
        mock_player.is_active = False

        fake_app = make_fake_app(
            player=mock_player,
            current_index=0,
            _pending_track=None,
        )

        SpoffTUI.action_toggle_play(fake_app)
        fake_app.play_index.assert_called_once_with(0)
        mock_player.toggle_pause.assert_not_called()

    def test_repeat_one_replays_current_track_on_eof(self):
        track = {"id": "t1", "title": "Track 1", "artist": "Artist 1"}
        fake_app = make_fake_app(
            repeat_mode="one",
            current_index=0,
            queue=[track],
            is_mounted=True,
        )

        SpoffTUI._handle_track_end(fake_app, 1, "eof", track, "/path/to/song.opus")
        fake_app.play_index.assert_called_once_with(0)

    def test_visualizer_idle_when_player_inactive(self):
        track = {"id": "t1", "title": "Track 1", "artist": "Artist 1"}
        mock_player = Mock()
        mock_player.current_track = track
        mock_player.is_active = False
        mock_player.is_paused = False

        mock_cava = Mock()
        mock_cava.fps = 30
        mock_cava.style = "bars"

        fake_app = types.SimpleNamespace(player=mock_player, vis_enabled=True)
        widget = VisualizerWidget(mock_cava)
        widget._app = fake_app
        widget.display = True
        widget.update = Mock()

        # Run tick
        widget._idle_counter = 11  # triggers idle branch
        widget._tick()

        # is_playing should be False because is_active is False
        mock_cava.get_markup.assert_called_with(False, False)


if __name__ == "__main__":
    unittest.main()
