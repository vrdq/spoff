from unittest.mock import MagicMock, patch

from spoff import streamer


def test_same_recording_under_another_uploader_is_accepted():
    """Same title and length within 2 s matches even when the uploader differs."""
    ytm = MagicMock()
    ytm.search.return_value = [
        {"videoId": "aaaaaaaaaaa", "title": "Satisfaction - Instrumental", "duration_seconds": 150},  # too long
        {"videoId": "bbbbbbbbbbb", "title": "Satisfaction (Remix)", "duration_seconds": 139},         # other title
        {"videoId": "ccccccccccc", "title": "Satisfaction - Instrumental", "duration_seconds": 140},  # same recording
    ]
    found = streamer._same_recording_other_uploader(ytm, "Satisfaction - Instrumental", 138.7)
    assert found == [("https://www.youtube.com/watch?v=ccccccccccc", True)]


def test_spotify_only_track_plays_the_preview_instead_of_failing():
    from spoff.app import SpoffTUI
    app = SpoffTUI.__new__(SpoffTUI)
    app._closing = False
    app._play_request_id = 7
    committed, failed = [], []
    app.call_from_thread = lambda fn, *a: fn(*a)
    app._on_ui = lambda fn, *a, **k: None  # background lyrics/art threads touch no UI in this stub
    app._commit_playback = lambda req, src, track: committed.append(src)
    app._playback_failed = lambda req, track: failed.append(track)
    app.notify_user = MagicMock()
    track = {"id": "7gSWo5ym0jyT0tIQOpBDEK", "title": "Satisfaction - Instrumental", "artist": "P4nnel!",
             "source": "spotify", "duration_ms": 138736}

    with patch("spoff.app.get_cached_track_path", return_value=None), \
         patch("spoff.app.get_cached_artwork", return_value={}), \
         patch("spoff.app.fetch_lyrics", return_value=None), \
         patch("spoff.app.resolve_track_artwork", return_value={}), \
         patch("spoff.app.search_and_resolve_stream", return_value=None), \
         patch("spoff.app.fetch_spotify_preview_url", return_value="https://p.scdn.co/mp3-preview/x") as preview:
        SpoffTUI._resolve_playback(app, track, 7)

    preview.assert_called_once_with("7gSWo5ym0jyT0tIQOpBDEK")
    assert committed == ["https://p.scdn.co/mp3-preview/x"]
    assert failed == []


def test_youtube_tracks_never_fall_back_to_a_spotify_preview():
    from spoff.app import SpoffTUI
    app = SpoffTUI.__new__(SpoffTUI)
    app._closing = False
    app._play_request_id = 1
    failed = []
    app.call_from_thread = lambda fn, *a: fn(*a)
    app._on_ui = lambda fn, *a, **k: None  # background lyrics/art threads touch no UI in this stub
    app._commit_playback = MagicMock()
    app._playback_failed = lambda req, track: failed.append(track)
    app.notify_user = MagicMock()
    track = {"id": "dQw4w9WgXcQ", "title": "T", "artist": "X", "source": "ytmusic"}

    with patch("spoff.app.get_cached_track_path", return_value=None), \
         patch("spoff.app.get_cached_artwork", return_value={}), \
         patch("spoff.app.fetch_lyrics", return_value=None), \
         patch("spoff.app.resolve_track_artwork", return_value={}), \
         patch("spoff.app.search_and_resolve_stream", return_value=None), \
         patch("spoff.app.fetch_spotify_preview_url") as preview:
        SpoffTUI._resolve_playback(app, track, 1)

    preview.assert_not_called()
    app._commit_playback.assert_not_called()
    assert failed == [track]
