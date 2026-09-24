import threading
from unittest.mock import MagicMock, patch

from spoff.player import MPVController
from spoff.spotify_audio import SpotifyAudio


def _audio(tmp_path):
    audio = SpotifyAudio(lambda: "", data_dir=tmp_path)
    audio.device_id = "dev1"
    return audio


def test_play_waits_for_librespot_to_confirm_then_tracks_position(tmp_path):
    audio = _audio(tmp_path)
    ended = []
    with patch("spoff.spotify_audio.auth.spotify_api_request", return_value=(True, {}, "")) as api:
        threading.Timer(0.1, audio._handle_event, ("playing", "ABC", "0", "")).start()
        assert audio.play("spotify:track:ABC", 200.0, ended.append, timeout=3)
    assert api.call_args.args[0] == "/me/player/play?device_id=dev1"
    assert api.call_args.kwargs["body"] == {"uris": ["spotify:track:ABC"], "position_ms": 0}

    audio._handle_event("seeked", "ABC", "60000", "")
    assert 60.0 <= audio.position() < 61.0
    audio._handle_event("paused", "ABC", "61000", "")
    assert audio.is_paused and audio.position() == 61.0

    audio._handle_event("end_of_track", "OLD", "", "")   # stale event from the last song
    assert ended == []
    audio._handle_event("end_of_track", "ABC", "", "")
    assert ended == ["eof"]


def test_unavailable_song_fails_play_so_the_app_can_use_youtube(tmp_path):
    audio = _audio(tmp_path)
    with patch("spoff.spotify_audio.auth.spotify_api_request", return_value=(True, {}, "")):
        threading.Timer(0.1, audio._handle_event, ("unavailable", "XYZ", "", "")).start()
        assert not audio.play("spotify:track:XYZ", 100.0, lambda r: None, timeout=3)


def test_player_routes_spotify_uris_and_controls_to_librespot():
    player = MPVController(socket_path="/nonexistent.sock")
    spotify = MagicMock()
    spotify.play.return_value = True
    spotify.is_paused = False
    spotify.position.return_value = 42.0
    spotify.duration = 200.0
    player.spotify = spotify
    finished = []
    player.register_pending_callback(finished.append)

    with patch.object(player, "_send_command", return_value=True):
        assert player.load_and_play("spotify:track:ABC", {"title": "T", "duration_ms": 200000})
        uri, duration, on_end = spotify.play.call_args.args
        assert (uri, duration) == ("spotify:track:ABC", 200.0)
        on_end("eof")
        assert finished == ["eof"]                       # the app's end-of-track handler still runs

        assert player.get_progress() == (42.0, 200.0)
        player.toggle_pause()
        spotify.pause.assert_called_once()
        player.seek(5)
        spotify.seek.assert_called_once_with(47.0)
        player.set_volume(30)
        spotify.set_volume.assert_called_once_with(30)


def test_youtube_song_after_a_spotify_song_stops_librespot_first():
    player = MPVController(socket_path="/nonexistent.sock")
    spotify = MagicMock()
    spotify.play.return_value = True
    player.spotify = spotify
    with patch.object(player, "_send_command", return_value=True):
        player.load_and_play("spotify:track:ABC", {"title": "T"})
    with patch.object(player, "start_mpv", side_effect=RuntimeError("no mpv in tests")):
        player.load_and_play("https://www.youtube.com/watch?v=dQw4w9WgXcQ", {"title": "Y"})
    spotify.stop_playback.assert_called()
    assert player._on_spotify is False


def test_spotify_uri_without_spotify_audio_is_refused():
    player = MPVController(socket_path="/nonexistent.sock")
    assert player.load_and_play("spotify:track:ABC", {"title": "T"}) is False


def test_session_flags_never_reach_storage():
    from spoff.storage import normalize_track
    assert "_spotify_unavailable" not in normalize_track({"id": "a", "title": "T", "_spotify_unavailable": True})


def test_refused_audio_keys_fail_fast_and_turn_spotify_audio_off(tmp_path):
    audio = _audio(tmp_path)
    audio._librespot = MagicMock()
    audio._librespot.poll.return_value = None
    assert audio.running()
    audio.keys_refused = True                      # librespot logged "audio key error"
    assert not audio.running()                     # the app stops routing songs here
    with patch("spoff.spotify_audio.auth.spotify_api_request", return_value=(True, {}, "")):
        assert not audio.play("spotify:track:ABC", 100.0, lambda r: None, timeout=5)
