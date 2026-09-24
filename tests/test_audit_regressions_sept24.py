"""Regression checks for the follow-up audit's matching and playback changes."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from spoff import streamer
from spoff.app import SpoffTUI
from spoff.matching import _matches_recording, _tracks_match


def test_long_recording_accepts_formatted_provider_duration():
    assert _matches_recording(
        {'title': 'Long Song', 'artist': 'Artist', 'duration': '20:00'},
        'Long Song', 'Artist', 1200,
    )


def test_long_recording_rejects_short_clip():
    assert not _matches_recording(
        {'title': 'Long Song', 'artist': 'Artist', 'duration': 1.2},
        'Long Song', 'Artist', 1200,
    )


def test_long_cached_recording_duration_uses_explicit_units():
    probe = Mock(stdout=json.dumps({'format': {'duration': '1200'}}))
    with patch.object(streamer.subprocess, 'run', return_value=probe):
        assert streamer.cached_audio_matches_duration(Path('audio'), 1200000)


@pytest.mark.parametrize('band,unrelated', [
    ('AC/DC', 'AC'), ('Florence and the Machine', 'the Machine'),
    ('Earth, Wind & Fire', 'Fire'),
])
def test_band_name_components_are_not_recording_identity(band, unrelated):
    candidate = {'title': 'Song', 'artist': unrelated, 'duration': 200}
    assert not _matches_recording(candidate, 'Song', band, 200)
    assert not _tracks_match(candidate, {'title': 'Song', 'artist': band})


@pytest.mark.parametrize('has_previous_track', [False, True])
def test_play_during_cached_preparation_does_not_restart_or_toggle_old_track(has_previous_track):
    pending = {'id': 'new', 'title': 'New', 'is_offline': True}
    fake = SimpleNamespace(
        focused=None, _pending_track=pending,
        player=Mock(current_track={'id': 'old'} if has_previous_track else None),
        notify_user=Mock(), update_player_hud=Mock(), _start_or_resume_playback=Mock(),
    )
    SpoffTUI.action_toggle_play(fake)
    fake.player.toggle_pause.assert_not_called()
    fake._start_or_resume_playback.assert_not_called()


def test_select_pending_cached_row_does_not_restart_request():
    pending = {'id': 'new', 'title': 'New', 'is_offline': True}
    fake = SimpleNamespace(
        _pending_track=pending, _get_current_view_tracks=lambda: [pending],
        _is_same_track=lambda a, b: SpoffTUI._is_same_track(None, a, b),
        player=Mock(current_track={'id': 'old'}), notify_user=Mock(),
        update_player_hud=Mock(), play_index=Mock(),
    )
    SpoffTUI.play_current_table_row(fake, 0)
    fake.player.toggle_pause.assert_not_called()
    fake.play_index.assert_not_called()


def test_matches_recording_featured_artist_asymmetry():
    # Spotify search returns title with feat and artists containing both
    req_title = "Are You Bored Yet? (feat. Clairo)"
    req_artist = "Wallows, Clairo"
    # YouTube Music returns official track with feat in title, but only primary artist in metadata
    candidate = {
        "title": "Are You Bored Yet? (feat. Clairo)",
        "artists": [{"name": "Wallows"}],
        "duration": 178,
    }
    assert _matches_recording(candidate, req_title, req_artist, 178)


def test_matches_recording_featured_artist_in_requested_title_only():
    req_title = "Are You Bored Yet? (feat. Clairo)"
    req_artist = "Wallows"
    candidate = {
        "title": "Are You Bored Yet? (feat. Clairo)",
        "artists": [{"name": "Wallows"}],
        "duration": 178,
    }
    assert _matches_recording(candidate, req_title, req_artist, 178)


def test_matches_recording_rejects_unrequested_feature_substitution():
    req_title = "Levitating"
    req_artist = "Dua Lipa"
    candidate = {
        "title": "Levitating (feat. DaBaby)",
        "artists": [{"name": "Dua Lipa"}],
        "duration": 203,
    }
    assert not _matches_recording(candidate, req_title, req_artist, 203)


def test_matches_recording_multi_separator_and_dot():
    # Middle dot separator
    cand_dot = {
        "title": "Song Title · Famous Artist",
        "duration": 210,
    }
    assert _matches_recording(cand_dot, "Song Title", "Famous Artist", 210)

    # Bullet separator
    cand_bullet = {
        "title": "Famous Artist • Song Title",
        "duration": 210,
    }
    assert _matches_recording(cand_bullet, "Song Title", "Famous Artist", 210)

    # Multi-dash separator
    cand_multi = {
        "title": "Song - Subtitle - Famous Artist",
        "duration": 210,
    }
    assert _matches_recording(cand_multi, "Song - Subtitle", "Famous Artist", 210)


def test_tracks_match_featured_artist_normalization():
    t1 = {"title": "Somebody That I Used to Know (feat. Kimbra)", "artist": "Gotye"}
    t2 = {"title": "Somebody That I Used to Know", "artist": "Gotye, Kimbra"}
    assert _tracks_match(t1, t2)


def test_playback_failed_search_origin_halts_without_cascading():
    fake = SimpleNamespace(
        _closing=False,
        _play_request_id=1,
        _pending_track="track1",
        _failed_indices=set(),
        current_index=0,
        queue=[{"title": "Track 1"}, {"title": "Track 2"}, {"title": "Track 3"}],
        _queue_origin={"tab": "search", "playlist_id": None},
        player=Mock(),
        mpris=Mock(),
        update_player_hud=Mock(),
        notify_user=Mock(),
        play_index=Mock(),
    )
    SpoffTUI._playback_failed(fake, 1, {"title": "Track 1", "artist": "Artist 1"})
    # Should stop player and reset state, not call play_index on Track 2
    fake.player.stop.assert_called_once()
    assert fake.current_index == -1
    fake.play_index.assert_not_called()


def test_search_and_resolve_stream_video_fallback(monkeypatch):
    mock_ytm = Mock()
    # First search (filter="songs") returns empty
    # Second search (filter="videos") returns verified video
    def fake_search(query, filter, limit):
        if filter == "songs":
            return []
        if filter == "videos":
            return [{"videoId": "vid123", "title": "Slowed Song", "artists": [{"name": "Artist"}], "duration": 200}]
        return []

    mock_ytm.search = fake_search
    from spoff import ytmusic
    monkeypatch.setattr(ytmusic, "get_ytmusic_client", lambda: mock_ytm)

    # Mock YoutubeDL extraction for vid123
    class FakeYDL:
        def __init__(self, opts):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, query, download=False):
            if "vid123" in query:
                return {"url": "https://stream.url/audio.m4a", "duration": 200}
            return None

    monkeypatch.setattr(streamer.yt_dlp, "YoutubeDL", FakeYDL)

    res = streamer.search_and_resolve_stream("Slowed Song", "Artist", expected_duration_ms=200000)
    assert res is not None
    assert res.get("stream_url") == "https://stream.url/audio.m4a"


def test_fetch_spotify_recommendations(monkeypatch):
    from spoff import auth

    def mock_request(url, method="GET", token=None):
        assert "/recommendations?seed_tracks=7gSWo5ym0jyT0tIQOpBDEK" in url
        data = {
            "tracks": [
                {
                    "id": "rec1",
                    "name": "Satisfaction - Push Push Push",
                    "artists": [{"name": "Eibell"}],
                    "duration_ms": 162000,
                    "uri": "spotify:track:rec1",
                    "album": {"name": "Remix EP", "images": [{"url": "https://img.url"}]},
                    "external_urls": {"spotify": "https://open.spotify.com/track/rec1"},
                }
            ]
        }
        return True, data, ""

    monkeypatch.setattr(auth, "spotify_api_request", mock_request)
    ok, recs, err = auth.fetch_spotify_recommendations("7gSWo5ym0jyT0tIQOpBDEK", token="fake_token")
    assert ok
    assert len(recs) == 1
    assert recs[0]["id"] == "rec1"
    assert recs[0]["title"] == "Satisfaction - Push Push Push"
    assert recs[0]["artist"] == "Eibell"
    assert recs[0]["source"] == "spotify"


def test_fetch_ytmusic_radio(monkeypatch):
    from spoff import ytmusic
    mock_ytm = Mock()
    mock_ytm.get_watch_playlist.return_value = {
        "tracks": [
            {
                "videoId": "vid_rad_1",
                "title": "Radio Track 1",
                "artists": [{"name": "Radio Artist"}],
                "length": "3:45",
                "thumbnail": [{"url": "https://img.yt/1.jpg"}],
            }
        ]
    }
    monkeypatch.setattr(ytmusic, "get_ytmusic_client", lambda: mock_ytm)
    recs = ytmusic.fetch_ytmusic_radio("vid_seed", limit=10)
    assert len(recs) == 1
    assert recs[0]["id"] == "vid_rad_1"
    assert recs[0]["title"] == "Radio Track 1"
    assert recs[0]["artist"] == "Radio Artist"
    assert recs[0]["duration_ms"] == (3 * 60 + 45) * 1000


def test_action_song_radio_tunes_and_loads(monkeypatch):
    seed_track = {"id": "seed123456789012345678", "title": "Seed Song", "artist": "Seed Artist", "source": "spotify"}
    fake = SimpleNamespace(
        _is_ready=True,
        focused=None,
        active_tab="search",
        _get_current_view_tracks=lambda: [seed_track],
        player=Mock(current_track=None),
        notify_user=Mock(),
        _start_song_radio=lambda track: None,
    )
    # Mock table query
    fake_tt = Mock(cursor_row=0, id="track-table")
    fake.query_one = lambda sel, cls=None: fake_tt

    # Test that action_song_radio locates the track and starts song radio
    with patch.object(fake, "_start_song_radio") as mock_start:
        SpoffTUI.action_song_radio(fake)
        mock_start.assert_called_once_with(seed_track)


