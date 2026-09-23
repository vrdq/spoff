import unittest
from unittest.mock import Mock, patch
from spoff.search import resolve_direct_track_url
from spoff.storage import is_track_in_playlist
from spoff.streamer import search_and_resolve_stream


class TestDirectTrackUrlAndMatching(unittest.TestCase):
    def test_resolve_spotify_track_url(self):
        with patch("spoff.auth.get_valid_token", return_value="fake_token"), \
             patch("spoff.auth.spotify_api_request", return_value=(True, {
                 "id": "4cOdK2wGLETKBW3PvgPWqT",
                 "name": "Never Gonna Give You Up",
                 "artists": [{"name": "Rick Astley"}],
                 "album": {"name": "Whenever You Need Somebody", "images": [{"url": "https://img.spotify.com/art.jpg"}]},
                 "duration_ms": 213000,
                 "uri": "spotify:track:4cOdK2wGLETKBW3PvgPWqT",
                 "external_urls": {"spotify": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"}
             }, "")):
            t = resolve_direct_track_url("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=abc123")
            self.assertIsNotNone(t)
            self.assertEqual(t["id"], "4cOdK2wGLETKBW3PvgPWqT")
            self.assertEqual(t["title"], "Never Gonna Give You Up")
            self.assertEqual(t["artist"], "Rick Astley")
            self.assertEqual(t["source"], "spotify")

    def test_resolve_youtube_music_url(self):
        mock_ytm = Mock()
        mock_ytm.get_song.return_value = {
            "videoDetails": {
                "videoId": "dQw4w9WgXcQ",
                "title": "Never Gonna Give You Up",
                "author": "Rick Astley",
                "lengthSeconds": "213",
                "thumbnail": {"thumbnails": [{"url": "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"}]}
            }
        }
        with patch("spoff.ytmusic.get_ytmusic_client", return_value=mock_ytm):
            t = resolve_direct_track_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ&feature=share")
            self.assertIsNotNone(t)
            self.assertEqual(t["id"], "dQw4w9WgXcQ")
            self.assertEqual(t["title"], "Never Gonna Give You Up")
            self.assertEqual(t["artist"], "Rick Astley")
            self.assertEqual(t["duration_ms"], 213000)
            self.assertEqual(t["source"], "ytmusic")

    def test_is_track_in_playlist_by_id_and_title(self):
        from spoff.storage import get_track_index_in_playlist, add_track_to_playlist
        pl = {
            "id": "pl_1",
            "name": "My Favorites",
            "tracks": [
                {"id": "sp_1", "title": "Prom Queen", "artist": "Beach Bunny"},
                {"id": "sp_2", "title": "Sports", "artist": "Beach Bunny"}
            ]
        }
        # Exact ID match
        self.assertTrue(is_track_in_playlist(pl, {"id": "sp_1", "title": "Different Title"}))
        self.assertEqual(get_track_index_in_playlist(pl, {"id": "sp_1"}), 0)
        # Title & artist match with different or missing ID
        self.assertTrue(is_track_in_playlist(pl, {"id": "other_id", "title": "sports", "artist": "beach bunny"}))
        self.assertEqual(get_track_index_in_playlist(pl, {"title": "sports", "artist": "beach bunny"}), 1)
        # Non-present track
        self.assertFalse(is_track_in_playlist(pl, {"id": "sp_3", "title": "Cloud 9", "artist": "Beach Bunny"}))
        self.assertIsNone(get_track_index_in_playlist(pl, {"title": "Cloud 9"}))

    def test_add_track_to_playlist_allow_duplicate(self):
        from spoff.storage import add_track_to_playlist
        playlists = [
            {
                "id": "pl_test",
                "name": "Test PL",
                "tracks": [{"id": "t1", "title": "Song A", "artist": "Artist A"}]
            }
        ]
        with patch("spoff.storage.load_saved_playlists", return_value=playlists), \
             patch("spoff.storage.save_saved_playlists") as mock_save:
            # Duplicate blocked when allow_duplicate=False
            res_blocked = add_track_to_playlist("pl_test", {"id": "t1", "title": "Song A", "artist": "Artist A"}, allow_duplicate=False)
            self.assertFalse(res_blocked)
            mock_save.assert_not_called()

            # Duplicate allowed when allow_duplicate=True
            res_allowed = add_track_to_playlist("pl_test", {"id": "t1", "title": "Song A", "artist": "Artist A"}, allow_duplicate=True)
            self.assertTrue(res_allowed)
            mock_save.assert_called_once()
            self.assertEqual(len(playlists[0]["tracks"]), 2)

    def test_streamer_prioritizes_ytm_studio_match(self):
        mock_ytm = Mock()
        mock_ytm.search.return_value = [
            {"title": "Prom Queen", "artists": [{"name": "Beach Bunny"}], "videoId": "4aez6rfdhfQ", "duration": "2:17"},
            {"title": "Prom Queen (slowed + reverb)", "artists": [{"name": "Slawd"}], "videoId": "LMvSVACO01g", "duration": "2:58"}
        ]
        with patch("spoff.ytmusic.get_ytmusic_client", return_value=mock_ytm), \
             patch("spoff.streamer.get_cached_track_path", return_value=None), \
             patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = Mock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = {
                "url": "https://stream.audio/prom_queen.webm",
                "thumbnail": "https://img.youtube.com/thumb.jpg",
                "duration": 137
            }
            res = search_and_resolve_stream("Prom Queen", "Beach Bunny")
            self.assertIsNotNone(res)
            # Verify that the extracted URL was the official studio videoId
            first_call_query = mock_ydl.extract_info.call_args_list[0][0][0]
            self.assertEqual(first_call_query, "https://www.youtube.com/watch?v=4aez6rfdhfQ")


if __name__ == "__main__":
    unittest.main()
