from pathlib import Path
from unittest.mock import MagicMock, patch

from spoff import storage, streamer


def test_opus_is_requested_for_streaming_and_downloads():
    assert streamer.get_base_ydl_opts()["format"].startswith("bestaudio[acodec=opus]")
    assert storage.CACHE_EXTENSIONS.index(".opus") < storage.CACHE_EXTENSIONS.index(".m4a")
    assert storage.CACHE_EXTENSIONS.index(".webm") < storage.CACHE_EXTENSIONS.index(".m4a")


def test_old_aac_files_count_as_low_quality():
    assert storage.is_low_quality_cache(Path("x.m4a"))
    assert storage.is_low_quality_cache("x.mp3")
    assert not storage.is_low_quality_cache("x.webm")
    assert not storage.is_low_quality_cache("x.opus")


def test_an_old_aac_file_is_redownloaded_instead_of_reused(tmp_path):
    old = tmp_path / "song.m4a"
    old.write_bytes(b"aac")
    with patch.object(streamer, "get_cached_track_path", return_value=old), \
         patch.object(streamer, "search_and_resolve_stream", return_value=None) as resolve:
        try:
            streamer._run_download_process("song", "T", "A", track_meta={"duration_ms": 1000})
        except RuntimeError:
            pass  # the resolver is stubbed out; what matters is that it was asked
    resolve.assert_called_once()


def test_playing_an_old_aac_file_starts_a_background_upgrade():
    from spoff.app import SpoffTUI
    app = SpoffTUI.__new__(SpoffTUI)
    app._closing = False
    app._play_request_id = 3
    app.call_from_thread = lambda fn, *a: fn(*a)
    app._on_ui = lambda fn, *a, **k: None
    app._commit_playback = MagicMock()
    track = {"id": "dQw4w9WgXcQ", "title": "T", "artist": "A", "duration_ms": 1000}
    with patch("spoff.app.get_cached_track_path", return_value=Path("/c/dQw4w9WgXcQ.m4a")), \
         patch("spoff.app.cached_audio_matches_duration", return_value=True), \
         patch("spoff.app.register_cached_track"), \
         patch("spoff.app.get_cached_artwork", return_value={}), \
         patch("spoff.app.fetch_lyrics", return_value=None), \
         patch("spoff.app.resolve_track_artwork", return_value={}), \
         patch("spoff.app.download_track_to_cache") as download:
        SpoffTUI._resolve_playback(app, track, 3)
    app._commit_playback.assert_called_once_with(3, "/c/dQw4w9WgXcQ.m4a", track)   # still plays right away
    download.assert_called_once()                                                    # and upgrades quietly
