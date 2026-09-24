import unittest
from unittest.mock import patch, Mock
from typing import Dict, Any, List
import json

from spoff import auth, storage
from spoff.matching import _matches_recording, _tracks_match, _clean_artist_name, _split_artists


class TestSpotifySyncHardening(unittest.TestCase):
    def setUp(self):
        storage.save_saved_playlists([])
        storage.save_liked_songs([])

    def test_clean_artist_name_channel_suffixes(self):
        """Channel suffixes like - Topic, VEVO, Official are cleaned while preserving band names."""
        self.assertEqual(_clean_artist_name("Queen - Topic"), "Queen")
        self.assertEqual(_clean_artist_name("QueenVEVO"), "Queen")
        self.assertEqual(_clean_artist_name("Coldplay Official"), "Coldplay")
        self.assertEqual(_clean_artist_name("Coldplay - Official Channel"), "Coldplay")
        self.assertEqual(_clean_artist_name("EminemVEVO"), "Eminem")
        self.assertEqual(_clean_artist_name("The Beatles - Topic"), "The Beatles")

        # Invariant: band names are strictly preserved
        self.assertEqual(_clean_artist_name("AC/DC"), "AC/DC")
        self.assertEqual(_clean_artist_name("Florence and the Machine"), "Florence and the Machine")
        self.assertEqual(_clean_artist_name("Earth, Wind & Fire"), "Earth, Wind & Fire")

    def test_matching_with_channel_suffixes(self):
        """_matches_recording and _tracks_match match candidate Spotify tracks against YouTube channel metadata."""
        spotify_cand = {
            "id": "spot123456789012345678",
            "title": "Bohemian Rhapsody",
            "artists": [{"name": "Queen"}],
            "duration_ms": 354000,
        }

        # Candidate Queen matches requested Queen - Topic
        self.assertTrue(_matches_recording(spotify_cand, "Bohemian Rhapsody", "Queen - Topic", 354))
        # Candidate Queen matches requested QueenVEVO
        self.assertTrue(_matches_recording(spotify_cand, "Bohemian Rhapsody", "QueenVEVO", 354))
        # Candidate Queen matches requested Queen Official
        self.assertTrue(_matches_recording(spotify_cand, "Bohemian Rhapsody", "Queen Official", 354))

        # Reverse: Candidate with - Topic matches requested pure artist
        yt_cand = {
            "id": "dQw4w9WgXcQ",
            "title": "Bohemian Rhapsody",
            "artist": "Queen - Topic",
            "duration_seconds": 354,
        }
        self.assertTrue(_matches_recording(yt_cand, "Bohemian Rhapsody", "Queen", 354))

        # _tracks_match handles cross-platform artist names
        t_sp = {"title": "Radio Ga Ga", "artist": "Queen"}
        t_yt = {"title": "Radio Ga Ga", "artist": "Queen - Topic"}
        self.assertTrue(_tracks_match(t_sp, t_yt))

        t_vevo = {"title": "Radio Ga Ga", "artist": "QueenVEVO"}
        self.assertTrue(_tracks_match(t_sp, t_vevo))

        # Unrelated artist never matches
        self.assertFalse(_matches_recording(spotify_cand, "Bohemian Rhapsody", "Prince", 354))
        self.assertFalse(_tracks_match(t_sp, {"title": "Radio Ga Ga", "artist": "Prince"}))

    def test_candidate_duration_ms_checking(self):
        """_matches_recording checks candidate duration_ms against requested seconds without magnitude guessing."""
        cand = {
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "duration_ms": 200000,  # 200 seconds
        }
        # Matching duration (within tolerance)
        self.assertTrue(_matches_recording(cand, "Song", "Artist", 200))
        # Mismatched duration (>8s different) rejects
        self.assertFalse(_matches_recording(cand, "Song", "Artist", 250))

    def test_search_spotify_track_queries_cleaned_and_primary_artist(self):
        """search_spotify_track cleans channel suffixes and queries primary artist for multi-artist credits."""
        recorded_queries = []

        def fake_request(url, method="GET", token=None, body=None):
            recorded_queries.append(url)
            # Return item when queried with clean artist
            if "Queen" in url and "Topic" not in url:
                return True, {
                    "tracks": {
                        "items": [
                            {
                                "id": "q" * 22,
                                "uri": f"spotify:track:{'q' * 22}",
                                "name": "Under Pressure",
                                "artists": [{"name": "Queen"}, {"name": "David Bowie"}],
                                "duration_ms": 240000,
                            }
                        ]
                    }
                }, ""
            return True, {"tracks": {"items": []}}, ""

        with patch.object(auth, "spotify_api_request", side_effect=fake_request):
            found = auth.search_spotify_track("Under Pressure", "Queen - Topic", token="fake_token")
            self.assertIsNotNone(found)
            self.assertEqual(found["id"], "q" * 22)
            # Verify queries attempted cleaned artist "Queen" rather than "Queen - Topic"
            self.assertTrue(any("Queen" in q for q in recorded_queries))

    def test_sync_playlist_tracks_to_spotify_includes_resolved_ytmusic_tracks(self):
        """sync_playlist_tracks_to_spotify keeps tracks that have spotify_id or spotify_uri, even if source is ytmusic."""
        pl = {
            "id": "test_pl",
            "name": "My Mix",
            "spotify_id": "37i9dQZF1DXcBWIGoYBM5M",
            "tracks": [
                # Pure Spotify track
                {"id": "sp1" + "a" * 19, "title": "Song 1", "artist": "A", "source": "spotify"},
                # YouTube Music track that has been matched/resolved to Spotify
                {
                    "id": "yt_video_11",
                    "title": "Song 2",
                    "artist": "B",
                    "source": "ytmusic",
                    "spotify_id": "sp2" + "b" * 19,
                    "spotify_uri": f"spotify:track:sp2{'b' * 19}",
                },
                # Pure local track with no Spotify mapping
                {"id": "local_audio_1", "title": "Song 3", "artist": "C", "source": "local"},
            ],
        }
        storage.save_saved_playlists([pl])

        recorded_put_uris = []

        def fake_api(url, method="GET", body=None, token=None):
            if method == "PUT" and "/tracks" in url:
                recorded_put_uris.extend(body.get("uris", []))
                return True, {}, ""
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            ok, msg = auth.sync_playlist_tracks_to_spotify("test_pl", token="fake_tok")
            self.assertTrue(ok)
            # Must include both Song 1 AND Song 2's Spotify URI, but not Song 3
            self.assertEqual(len(recorded_put_uris), 2)
            self.assertIn(f"spotify:track:sp1{'a' * 19}", recorded_put_uris)
            self.assertIn(f"spotify:track:sp2{'b' * 19}", recorded_put_uris)

    def test_add_track_to_spotify_account_persists_spotify_ids_locally(self):
        """add_track_to_spotify_account writes resolved spotify_id and spotify_uri back to local storage."""
        # Test Liked Songs
        liked_track = {
            "id": "yt_like_123",
            "title": "Liked Melody",
            "artist": "Indie Band",
            "source": "ytmusic",
        }
        storage.save_liked_songs([liked_track])

        found_sp = {
            "id": "sp_liked_" + "2" * 13,
            "uri": "spotify:track:sp_liked_" + "2" * 13,
            "title": "Liked Melody",
            "artist": "Indie Band",
        }

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "search_spotify_track", return_value=found_sp), \
             patch.object(auth, "spotify_api_request", return_value=(True, {}, "")):
            ok, msg = auth.add_track_to_spotify_account("liked", "Liked Songs", liked_track, token="tok")
            self.assertTrue(ok)

        # Verify storage was updated with spotify_id and spotify_uri
        saved_liked = storage.load_liked_songs()
        self.assertEqual(len(saved_liked), 1)
        self.assertEqual(saved_liked[0].get("spotify_id"), found_sp["id"])
        self.assertEqual(saved_liked[0].get("spotify_uri"), found_sp["uri"])

        # Test Playlist
        pl_track = {
            "id": "yt_pl_456",
            "title": "Playlist Track",
            "artist": "Acoustic Band",
            "source": "ytmusic",
        }
        pl = {
            "id": "local_pl_1",
            "name": "Favorites",
            "spotify_id": "37i9dQZF1DXcBWIGoYBM5M",
            "tracks": [pl_track],
        }
        storage.save_saved_playlists([pl])

        found_pl_sp = {
            "id": "sp_track_" + "4" * 13,
            "uri": "spotify:track:sp_track_" + "4" * 13,
            "title": "Playlist Track",
            "artist": "Acoustic Band",
        }

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "search_spotify_track", return_value=found_pl_sp), \
             patch.object(auth, "spotify_api_request", return_value=(True, {}, "")):
            ok, msg = auth.add_track_to_spotify_account("local_pl_1", "Favorites", pl_track, token="tok")
            self.assertTrue(ok)

        saved_pls = storage.load_saved_playlists()
        self.assertEqual(saved_pls[0]["tracks"][0].get("spotify_id"), found_pl_sp["id"])
        self.assertEqual(saved_pls[0]["tracks"][0].get("spotify_uri"), found_pl_sp["uri"])

    def test_sync_spotify_library_not_canceled_by_concurrent_artwork_updates(self):
        """sync_spotify_library does not drop/abort sync if artwork is merged into local storage during fetch."""
        local_track = {
            "id": "t" * 22,
            "title": "Existing Song",
            "artist": "Existing Artist",
            "source": "spotify",
        }
        storage.save_liked_songs([local_track])

        remote_new_track = {
            "id": "r" * 22,
            "title": "Remote Song",
            "artist": "Remote Artist",
            "source": "spotify",
            "uri": f"spotify:track:{'r' * 22}",
        }

        def fake_user_playlists(_):
            # Concurrent background artwork worker updates local_track with art_url while playlists are fetching
            storage.merge_track_artwork("liked", "t" * 22, {"art_url": "https://artwork.com/img.jpg"})
            return []

        with patch.object(auth, "fetch_liked_songs", return_value=[local_track, remote_new_track]), \
             patch.object(auth, "fetch_user_playlists", side_effect=fake_user_playlists):
            synced = auth.sync_spotify_library("fake_tok")
            self.assertGreaterEqual(synced, 1)

        # Liked songs must contain BOTH existing (with its artwork!) and remote new track
        final_liked = storage.load_liked_songs()
        self.assertEqual(len(final_liked), 2)
        final_ids = {t["id"] for t in final_liked}
        self.assertIn("t" * 22, final_ids)
        self.assertIn("r" * 22, final_ids)

        existing_in_final = next(t for t in final_liked if t["id"] == "t" * 22)
        self.assertEqual(existing_in_final.get("art_url"), "https://artwork.com/img.jpg")

    def test_sync_spotify_library_preserves_concurrent_local_additions(self):
        """sync_spotify_library preserves tracks added locally while remote playlists were fetching."""
        local_initial = {
            "id": "init_" + "1" * 17,
            "title": "Song 1",
            "artist": "A",
            "source": "spotify",
        }
        storage.save_liked_songs([local_initial])

        remote_new = {
            "id": "rem_" + "2" * 18,
            "title": "Remote 2",
            "artist": "B",
            "source": "spotify",
        }

        local_added_during_fetch = {
            "id": "added_" + "3" * 16,
            "title": "Local 3",
            "artist": "C",
            "source": "ytmusic",
        }

        def fake_user_playlists(_):
            # User likes a song locally while fetching
            storage.add_track_to_liked_songs(local_added_during_fetch)
            return []

        with patch.object(auth, "fetch_liked_songs", return_value=[local_initial, remote_new]), \
             patch.object(auth, "fetch_user_playlists", side_effect=fake_user_playlists):
            synced = auth.sync_spotify_library("fake_tok")
            self.assertGreaterEqual(synced, 1)

        final_liked = storage.load_liked_songs()
        final_ids = {t["id"] for t in final_liked}
        # Must have initial, remote new, AND local addition
        self.assertIn(local_initial["id"], final_ids)
        self.assertIn(remote_new["id"], final_ids)
        self.assertIn(local_added_during_fetch["id"], final_ids)


if __name__ == "__main__":
    unittest.main()
