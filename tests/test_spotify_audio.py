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


def test_shutdown_stops_the_event_reader_and_removes_its_fifo(tmp_path):
    audio = _audio(tmp_path)
    audio.dir.mkdir(parents=True)
    fifo = audio._make_event_fifo()
    reader = threading.Thread(target=audio._read_events, args=(fifo,), daemon=True)
    reader.start()
    audio.shutdown()
    reader.join(timeout=3)
    assert not reader.is_alive()
    assert not fifo.exists()


def test_each_session_gets_its_own_event_fifo(tmp_path):
    first, second = _audio(tmp_path), _audio(tmp_path)
    first.dir.mkdir(parents=True)
    assert first._make_event_fifo() != second._make_event_fifo()


def test_reader_started_after_shutdown_exits_without_waiting(tmp_path):
    audio = _audio(tmp_path)
    audio.dir.mkdir(parents=True)
    fifo = audio._make_event_fifo()
    stop = audio._event_stop
    audio.shutdown()
    reader = threading.Thread(target=audio._read_events, args=(fifo, stop), daemon=True)
    reader.start()
    reader.join(timeout=1)
    assert not reader.is_alive()


def test_same_instance_restart_uses_new_fifo_and_stop_event(tmp_path):
    audio = _audio(tmp_path)
    audio.dir.mkdir(parents=True)
    old = audio._make_event_fifo()
    old_stop = audio._event_stop
    audio.shutdown()
    new = audio._make_event_fifo()
    assert old != new
    assert old_stop.is_set()
    assert not audio._event_stop.is_set()
    audio.shutdown()


def test_failed_play_request_cannot_trigger_late_queue_advance(tmp_path):
    audio = _audio(tmp_path)
    ended = []
    with patch.object(audio, '_api', return_value=False):
        assert not audio.play('spotify:track:ABC', 100, ended.append)
    audio._handle_event('end_of_track', 'ABC', '', '')
    assert ended == []


def test_stale_track_changed_cannot_replace_current_duration(tmp_path):
    audio = _audio(tmp_path)
    audio.track_id = 'NEW'
    audio.duration = 200
    audio._handle_event('track_changed', 'OLD', '', '400000')
    assert audio.duration == 200


def test_librespot_spawn_failure_cleans_started_sink(tmp_path):
    audio = _audio(tmp_path)
    sink = MagicMock()
    sink.poll.return_value = None
    def start_sink():
        audio._sink = sink
    with patch('spoff.spotify_audio.librespot_path', return_value='/fake/librespot'), \
         patch.object(audio, '_start_sink', side_effect=start_sink), \
         patch('spoff.spotify_audio.subprocess.Popen', side_effect=OSError('spawn failed')):
        assert not audio.start(timeout=0)
    sink.terminate.assert_called_once()
    assert audio._sink is None
    assert audio._events_fifo is None


def test_pause_and_resume_do_not_block_caller_and_keep_command_order(tmp_path):
    audio = _audio(tmp_path)
    audio.track_id = 'ABC'
    entered, release = threading.Event(), threading.Event()
    commands, callers = [], []
    def api(endpoint):
        commands.append(endpoint)
        callers.append(threading.get_ident())
        entered.set()
        assert release.wait(2)
        return True
    try:
        with patch.object(audio, '_api', side_effect=api):
            paused = audio.pause()
            assert entered.wait(1)
            resumed = audio.resume()
            assert not audio.is_paused
            release.set()
            assert paused.result(timeout=2)
            assert resumed.result(timeout=2)
        assert commands == ['/me/player/pause', '/me/player/play']
        assert all(tid != threading.get_ident() for tid in callers)
    finally:
        release.set()
        audio.shutdown()


def test_failed_pause_restores_transport_state(tmp_path):
    audio = _audio(tmp_path)
    audio.track_id = 'ABC'
    try:
        with patch.object(audio, '_api', return_value=False):
            assert not audio.pause().result(timeout=2)
        assert not audio.is_paused
    finally:
        audio.shutdown()


def test_queued_seek_cannot_affect_the_next_song(tmp_path):
    audio = _audio(tmp_path)
    audio.track_id = 'ABC'
    entered, release = threading.Event(), threading.Event()
    def api(endpoint):
        entered.set()
        assert release.wait(2)
        return True
    try:
        with patch.object(audio, '_api', side_effect=api) as request:
            paused = audio.pause()
            assert entered.wait(1)
            seek = audio.seek(20)
            with audio._events:
                audio.track_id = 'NEW'
            release.set()
            paused.result(timeout=2)
            assert not seek.result(timeout=2)
            assert request.call_count == 1
    finally:
        release.set()
        audio.shutdown()
