import asyncio
from unittest.mock import patch

from spoff import streamer


def _track(i, source="spotify"):
    return {"id": f"{i:022d}", "title": f"Song {i}", "artist": "A", "duration_ms": 200000, "source": source}


def test_songs_that_cannot_play_never_appear_and_order_is_kept():
    from spoff.app import SpoffTUI
    results = [_track(0), _track(1), _track(2, "ytmusic"), _track(3), _track(4)]
    missing = {"Song 0", "Song 3"}          # not on YouTube
    checked, shown = [], []

    def fake_check(title, artist, duration_ms):
        checked.append(title)
        return title not in missing

    async def run():
        app = SpoffTUI(visualizer_enabled=False, notifications_enabled=False)
        real_publish = SpoffTUI._publish_search_results

        def spy(self, req_id, tracks, summary):
            shown.append([t["title"] for t in tracks])
            real_publish(self, req_id, tracks, summary)
        with patch.object(app, "check_github_updates_bg"), \
             patch.object(app, "backfill_playlists_art_bg"), \
             patch.object(app.player, "start_mpv"), \
             patch("spoff.app.is_first_launch", return_value=False), \
             patch("spoff.app.is_on_youtube", side_effect=fake_check), \
             patch.object(SpoffTUI, "_publish_search_results", spy):
            async with app.run_test() as pilot:
                await pilot.pause(0.8)
                app._search_request_id = 5
                app.switch_view("search")
                await asyncio.to_thread(app._reveal_playable_results, 5, list(results), "song", "Spotify", None)
                await pilot.pause(0.2)
                return [t["title"] for t in app.search_results]

    final = asyncio.run(run())
    assert final == ["Song 1", "Song 2", "Song 4"]                 # original order, dead songs gone
    assert shown[0] == ["Song 2"]                                  # YouTube result shows at once
    assert all(not missing & set(snapshot) for snapshot in shown)  # a dead song is never shown
    assert "Song 2" not in checked                                 # YouTube results aren't checked


def test_a_network_error_never_hides_a_song():
    streamer._playable_cache.clear()

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k): raise OSError("offline")

    with patch.object(streamer, "_ytmusic_match_queries", return_value=[]), \
         patch.object(streamer.yt_dlp, "YoutubeDL", Boom):
        assert streamer.is_on_youtube("Song", "Artist", 200000) is True


def test_youtube_match_is_found_and_cached():
    streamer._playable_cache.clear()
    with patch.object(streamer, "_ytmusic_match_queries",
                      return_value=[("https://www.youtube.com/watch?v=aaaaaaaaaaa", True)]) as lookup:
        assert streamer.is_on_youtube("Song", "Artist", 200000)
        assert streamer.is_on_youtube("Song", "Artist", 200000)
    assert lookup.call_count == 1
