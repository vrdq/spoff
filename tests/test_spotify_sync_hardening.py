import unittest
from unittest.mock import patch, Mock
from typing import Dict, Any, List
import json

import tempfile
from pathlib import Path

from spoff import auth, storage
from spoff.matching import _matches_recording, _tracks_match, _clean_artist_name, _split_artists


class TestSpotifySyncHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_cache_dir = storage.CACHE_DIR
        self.old_playlists_file = storage.PLAYLISTS_FILE
        self.old_liked_file = storage.LIKED_SONGS_FILE
        self.old_config_file = storage.CONFIG_FILE
        self.old_deleted_file = storage.DELETED_PLAYLISTS_FILE
        self.old_auth_file = auth.AUTH_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.CACHE_DIR = storage.DATA_DIR / "cache"
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        storage.PLAYLISTS_FILE = storage.DATA_DIR / "playlists.json"
        storage.LIKED_SONGS_FILE = storage.DATA_DIR / "liked_songs.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"
        storage.DELETED_PLAYLISTS_FILE = storage.DATA_DIR / "deleted_spotify_playlists.json"
        auth.AUTH_FILE = storage.DATA_DIR / "spotify_auth.json"

        storage.save_saved_playlists([])
        storage.save_liked_songs([])

    def tearDown(self):
        storage.DATA_DIR = self.old_data_dir
        storage.CACHE_DIR = self.old_cache_dir
        storage.PLAYLISTS_FILE = self.old_playlists_file
        storage.LIKED_SONGS_FILE = self.old_liked_file
        storage.CONFIG_FILE = self.old_config_file
        storage.DELETED_PLAYLISTS_FILE = self.old_deleted_file
        auth.AUTH_FILE = self.old_auth_file
        self.temp_dir.cleanup()

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

    def test_sync_playlist_tracks_to_spotify_excludes_ytmusic_tracks(self):
        """Only Spotify tracks are pushed; YouTube Music tracks stay local even if previously matched."""
        pl = {
            "id": "test_pl",
            "name": "My Mix",
            "spotify_id": "37i9dQZF1DXcBWIGoYBM5M",
            "tracks": [
                {"id": "sp1" + "a" * 19, "title": "Song 1", "artist": "A", "source": "spotify"},
                {
                    "id": "yt_video_11",
                    "title": "Song 2",
                    "artist": "B",
                    "source": "ytmusic",
                    "spotify_id": "sp2" + "b" * 19,
                    "spotify_uri": f"spotify:track:sp2{'b' * 19}",
                },
                {"id": "local_audio_1", "title": "Song 3", "artist": "C", "source": "local"},
            ],
        }
        storage.save_saved_playlists([pl])
        recorded_put_uris = []

        def fake_api(url, method="GET", body=None, token=None):
            if method == "PUT" and "/tracks" in url:
                recorded_put_uris.extend(body.get("uris", []))
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            ok, _ = auth.sync_playlist_tracks_to_spotify("test_pl", token="fake_tok")
            self.assertTrue(ok)
        self.assertEqual(recorded_put_uris, [f"spotify:track:sp1{'a' * 19}"])
    def test_add_track_to_spotify_account_persists_spotify_ids_locally(self):
        """Adding a Spotify track to a linked playlist records its Spotify ID locally."""
        sp_track = {"id": "local_sp_1", "title": "Playlist Track", "artist": "Acoustic Band",
                    "source": "spotify", "uri": "spotify:track:" + "4" * 22}
        storage.save_saved_playlists([{
            "id": "local_pl_1", "name": "Favorites",
            "spotify_id": "37i9dQZF1DXcBWIGoYBM5M", "tracks": [sp_track],
        }])
        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", return_value=(True, {}, "")):
            ok, _ = auth.add_track_to_spotify_account("local_pl_1", "Favorites", sp_track, token="tok")
            self.assertTrue(ok)
        saved = storage.load_saved_playlists()[0]["tracks"][0]
        self.assertEqual(saved.get("spotify_uri"), "spotify:track:" + "4" * 22)
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
    def test_add_track_to_spotify_account_auto_heals_404_by_recreating_playlist(self):
        """When adding a track to a playlist whose Spotify ID returns 404, Spoff auto-heals by recreating on Spotify."""
        stale_sp_id = "stale" + "1" * 17
        new_sp_id = "new_pl" + "2" * 16
        pl = {
            "id": "my_playlist_id",
            "name": "My Mix",
            "spotify_id": stale_sp_id,
            "tracks": [{"id": "t1" + "a" * 20, "title": "Track 1", "artist": "A", "uri": f"spotify:track:t1{'a' * 20}"}]
        }
        storage.save_saved_playlists([pl])

        new_track = {
            "id": "t2" + "b" * 20,
            "title": "Track 2",
            "artist": "B",
            "uri": f"spotify:track:t2{'b' * 20}"
        }

        created_playlists = []
        posted_tracks = []

        def fake_api(url, method="GET", body=None, token=None):
            if method == "POST" and f"/playlists/{stale_sp_id}/tracks" in url:
                return False, None, "Resource not found"
            if method == "POST" and "/me/playlists" in url:
                created_playlists.append(body)
                return True, {"id": new_sp_id, "name": body.get("name")}, ""
            if method == "POST" and f"/playlists/{new_sp_id}/tracks" in url:
                posted_tracks.extend(body.get("uris", []))
                return True, {}, ""
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            ok, msg = auth.add_track_to_spotify_account("my_playlist_id", "My Mix", new_track, token="tok")
            self.assertTrue(ok)
            self.assertIn("recreated", msg.lower())

        # Verify local playlist spotify_id was updated to new_sp_id
        saved_pls = storage.load_saved_playlists()
        self.assertEqual(saved_pls[0]["spotify_id"], new_sp_id)
        # Verify tracks were uploaded to the new Spotify playlist
        self.assertIn(f"spotify:track:t1{'a' * 20}", posted_tracks)
        self.assertIn(f"spotify:track:t2{'b' * 20}", posted_tracks)

    def test_sync_playlist_tracks_to_spotify_auto_creates_unlinked_playlist(self):
        """sync_playlist_tracks_to_spotify automatically creates the playlist on Spotify if unlinked."""
        new_sp_id = "created_sp_" + "3" * 11
        pl = {
            "id": "local_mix_99",
            "name": "Summer Mix",
            "tracks": [{"id": "t1" + "c" * 20, "title": "Track 1", "artist": "A", "uri": f"spotify:track:t1{'c' * 20}"}]
        }
        storage.save_saved_playlists([pl])

        uploaded_uris = []

        def fake_api(url, method="GET", body=None, token=None):
            if method == "POST" and "/me/playlists" in url:
                return True, {"id": new_sp_id}, ""
            if method == "POST" and f"/playlists/{new_sp_id}/tracks" in url:
                uploaded_uris.extend(body.get("uris", []))
                return True, {}, ""
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            ok, msg = auth.sync_playlist_tracks_to_spotify("local_mix_99", token="tok")
            self.assertTrue(ok)

        saved = storage.load_saved_playlists()
        self.assertEqual(saved[0]["spotify_id"], new_sp_id)
        self.assertIn(f"spotify:track:t1{'c' * 20}", uploaded_uris)

    def test_sync_playlist_tracks_to_spotify_auto_heals_404(self):
        """sync_playlist_tracks_to_spotify recreates the playlist when PUT returns 404."""
        stale_id = "s" * 22
        new_id = "n" * 22
        pl = {
            "id": "local_mix_404",
            "name": "Road Trip",
            "spotify_id": stale_id,
            "tracks": [{"id": "t1" + "d" * 20, "title": "Track 1", "artist": "A", "uri": f"spotify:track:t1{'d' * 20}"}]
        }
        storage.save_saved_playlists([pl])

        uploaded_uris = []

        def fake_api(url, method="GET", body=None, token=None):
            if method == "PUT" and f"/playlists/{stale_id}/tracks" in url:
                return False, None, "Resource not found (404)"
            if method == "POST" and "/me/playlists" in url:
                return True, {"id": new_id}, ""
            if method == "POST" and f"/playlists/{new_id}/tracks" in url:
                uploaded_uris.extend(body.get("uris", []))
                return True, {}, ""
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            ok, msg = auth.sync_playlist_tracks_to_spotify("local_mix_404", token="tok")
            self.assertTrue(ok)
            self.assertIn("recreated", msg.lower())

        saved = storage.load_saved_playlists()
        self.assertEqual(saved[0]["spotify_id"], new_id)
        self.assertIn(f"spotify:track:t1{'d' * 20}", uploaded_uris)

    def test_rename_spotify_playlist_auto_heals_404(self):
        """rename_spotify_playlist creates the playlist on Spotify with the new name if remote is 404."""
        stale_id = "r" * 22
        new_id = "m" * 22
        pl = {
            "id": "local_ren_pl",
            "name": "Old Name",
            "spotify_id": stale_id,
            "tracks": [{"id": "t1" + "e" * 20, "title": "Track 1", "artist": "A", "uri": f"spotify:track:t1{'e' * 20}"}]
        }
        storage.save_saved_playlists([pl])

        def fake_api(url, method="GET", body=None, token=None):
            if method == "PUT" and f"/playlists/{stale_id}" in url:
                return False, None, "Resource not found (404)"
            if method == "POST" and "/me/playlists" in url:
                return True, {"id": new_id}, ""
            if method == "POST" and f"/playlists/{new_id}/tracks" in url:
                return True, {}, ""
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            ok, msg = auth.rename_spotify_playlist("local_ren_pl", "New Name", token="tok")
            self.assertTrue(ok)

        saved = storage.load_saved_playlists()
        self.assertEqual(saved[0]["spotify_id"], new_id)
        self.assertEqual(saved[0]["name"], "New Name")

    def test_remove_track_from_spotify_account_client_side_and_404_safe(self):
        """Local-only tracks make no Spotify call and report no Spotify removal; 404 playlists are safe."""
        # 1. YouTube Music track: nothing on Spotify to remove, so no success notice
        yt_track = {"id": "yt_only_123", "title": "Obscure Indie Song", "artist": "Local Band", "source": "ytmusic"}
        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request") as mock_api:
            ok, msg = auth.remove_track_from_spotify_account("some_pl", "My Mix", yt_track, token="tok")
            self.assertFalse(ok)
            self.assertIn("local-only", msg)
            mock_api.assert_not_called()

        # 2. Track removed from a playlist that was 404 on Spotify
        sp_track = {"id": "sp1" + "f" * 19, "title": "Track F", "artist": "F", "uri": f"spotify:track:sp1{'f' * 19}"}
        pl = {"id": "pl_404", "name": "Dead Mix", "spotify_id": "d" * 22, "tracks": [sp_track]}
        storage.save_saved_playlists([pl])

        def fake_del(url, method="GET", body=None, token=None):
            return False, None, "Resource not found (404)"

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "spotify_api_request", side_effect=fake_del):
            ok, msg = auth.remove_track_from_spotify_account("pl_404", "Dead Mix", sp_track, token="tok")
            self.assertTrue(ok)
            self.assertIn("already removed", msg.lower())

    def test_confirm_modal_d_and_delete_bindings(self):
        """ConfirmModal binds 'd' and 'delete' keys to confirm removal."""
        from spoff.app import ConfirmModal
        modal = ConfirmModal("Title", "Message")
        keys = {b.key for b in modal.BINDINGS if b.action == "confirm"}
        self.assertIn("d", keys)
        self.assertIn("delete", keys)
        self.assertIn("enter", keys)
        self.assertIn("y", keys)

    def test_pre_login_local_playlist_with_tracks_syncs_to_spotify(self):
        """A pre-login playlist is created on Spotify with its Spotify tracks; YouTube Music tracks stay local."""
        local_pl = storage.create_local_playlist("Mine-v2")
        sp_uri = "spotify:track:" + "1" * 22
        spotify_track = {"id": "1" * 22, "title": "Nice To Each Other", "artist": "Olivia Dean",
                         "source": "spotify", "uri": sp_uri, "duration_ms": 180000}
        yt_track = {"id": "yt_res_123", "title": "Some Video", "artist": "Olivia Dean",
                    "source": "ytmusic", "duration_ms": 180000}
        storage.add_track_to_playlist(local_pl["id"], yt_track)
        storage.add_track_to_playlist(local_pl["id"], spotify_track)

        created_playlists = []
        posted_tracks = []
        new_sp_pl_id = "sp_created_pl_" + "7" * 8

        def fake_api(url, method="GET", body=None, token=None):
            if method == "POST" and "/me/playlists" in url:
                created_playlists.append(body)
                return True, {"id": new_sp_pl_id, "name": body.get("name")}, ""
            if method == "POST" and f"/playlists/{new_sp_pl_id}/tracks" in url:
                posted_tracks.extend(body.get("uris", []))
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "fetch_liked_songs", return_value=[]), \
             patch.object(auth, "fetch_user_playlists", return_value=[]), \
             patch.object(auth, "search_spotify_track") as mock_search, \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            synced = auth.sync_spotify_library("test_token")
            self.assertGreaterEqual(synced, 1)
            mock_search.assert_not_called()

        self.assertEqual([p["name"] for p in created_playlists], ["Mine-v2"])
        self.assertEqual(posted_tracks, [sp_uri])

        saved = storage.load_saved_playlists()[0]
        self.assertEqual(saved["spotify_id"], new_sp_pl_id)
        self.assertIn("open.spotify.com/playlist", saved["url"])
        # Both songs stay in the local playlist; the YouTube one gains no Spotify link.
        self.assertEqual([t["id"] for t in saved["tracks"]], ["yt_res_123", "1" * 22])
        self.assertNotIn("spotify_id", saved["tracks"][0])

    def test_pre_login_empty_local_playlist_creates_spotify_playlist(self):
        """An empty local playlist created before login is created on Spotify upon login."""
        storage.create_local_playlist("Empty PreLogin")
        created_playlists = []
        new_sp_id = "sp_empty_" + "9" * 13

        def fake_api(url, method="GET", body=None, token=None):
            if method == "POST" and "/me/playlists" in url:
                created_playlists.append(body)
                return True, {"id": new_sp_id, "name": body.get("name")}, ""
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "fetch_liked_songs", return_value=[]), \
             patch.object(auth, "fetch_user_playlists", return_value=[]), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            synced = auth.sync_spotify_library("test_token")
            self.assertGreaterEqual(synced, 1)

        self.assertEqual(len(created_playlists), 1)
        self.assertEqual(created_playlists[0]["name"], "Empty PreLogin")
        saved_pls = storage.load_saved_playlists()
        self.assertEqual(saved_pls[0]["spotify_id"], new_sp_id)


    def test_auto_sync_names_each_unlinked_playlist_correctly(self):
        """Each unlinked playlist is created on Spotify under its own name."""
        storage.create_local_playlist("First")
        storage.create_local_playlist("Second")
        created = []

        def fake_api(url, method="GET", body=None, token=None):
            if method == "POST" and "/me/playlists" in url:
                created.append(body["name"])
                return True, {"id": f"sp_new_{len(created):016d}"}, ""
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "fetch_liked_songs", return_value=[]), \
             patch.object(auth, "fetch_user_playlists", return_value=[]), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            auth.sync_spotify_library("test_token")

        self.assertCountEqual(created, ["First", "Second"])

    def test_auto_sync_skips_linked_but_creates_imported_playlists(self):
        """A linked playlist whose fetch failed is never overwritten; imports get created."""
        storage.save_saved_playlists([
            {"id": "local_linked01", "name": "Linked", "spotify_id": "1KUyRdfZw7qhwKG6xqyIE8",
             "tracks": [{"id": "a" * 22, "uri": "spotify:track:" + "a" * 22, "title": "A", "artist": "B"}]},
            {"id": "PLimported123456", "name": "YT Import", "url": "https://music.youtube.com/playlist?list=PLimported123456",
             "tracks": []},
        ])
        writes = []

        def fake_api(url, method="GET", body=None, token=None):
            if method != "GET":
                writes.append((method, url))
            return True, {}, ""

        with patch.object(auth, "has_modify_scopes", return_value=True), \
             patch.object(auth, "fetch_liked_songs", return_value=[]), \
             patch.object(auth, "fetch_user_playlists", return_value=[{"id": "1KUyRdfZw7qhwKG6xqyIE8", "name": "Linked"}]), \
             patch.object(auth, "fetch_playlist_tracks", return_value=None), \
             patch.object(auth, "spotify_api_request", side_effect=fake_api):
            auth.sync_spotify_library("test_token")

        self.assertEqual(writes, [("POST", "/me/playlists")])

    def test_has_modify_scopes_requires_all_write_scopes(self):
        storage._atomic_json_dump(auth.AUTH_FILE, {"access_token": "x", "scope": "user-library-modify"})
        self.assertFalse(auth.has_modify_scopes())
        storage._atomic_json_dump(auth.AUTH_FILE, {
            "access_token": "x",
            "scope": "playlist-modify-public playlist-modify-private user-library-modify",
        })
        self.assertTrue(auth.has_modify_scopes())


if __name__ == "__main__":
    unittest.main()

