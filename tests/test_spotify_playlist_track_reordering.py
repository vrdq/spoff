import pytest
from unittest.mock import MagicMock, patch
from spoff.storage import move_playlist_track, move_liked_track
from spoff.auth import (
    extract_spotify_playlist_id,
    reorder_spotify_playlist_track,
    sync_playlist_tracks_to_spotify,
)


def test_move_playlist_track_with_duplicates(tmp_path):
    """Verify move_playlist_track targets the exact track at `index` when duplicates exist."""
    t1 = {"id": "track_dup", "title": "Song A", "artist": "Artist 1"}
    t2 = {"id": "track_mid", "title": "Song B", "artist": "Artist 2"}
    t3 = {"id": "track_dup", "title": "Song A", "artist": "Artist 1"}  # Duplicate

    mock_playlists = [{
        "id": "pl_1",
        "name": "Test PL",
        "tracks": [dict(t1), dict(t2), dict(t3)]
    }]

    with patch("spoff.storage.load_saved_playlists", return_value=mock_playlists), \
         patch("spoff.storage.save_saved_playlists") as mock_save:
        
        # Move the second duplicate (index 2) UP
        ok = move_playlist_track("pl_1", t3, -1, index=2)
        assert ok is True
        saved = mock_save.call_args[0][0]
        tracks = saved[0]["tracks"]
        # Expected order: [t1, t3, t2] (t3 moved from index 2 to index 1)
        assert tracks[0]["id"] == "track_dup"
        assert tracks[1]["id"] == "track_dup"
        assert tracks[2]["id"] == "track_mid"


def test_move_liked_track_with_duplicates():
    """Verify move_liked_track targets the exact track at `index` when duplicates exist."""
    t1 = {"id": "track_dup", "title": "Song A", "artist": "Artist 1"}
    t2 = {"id": "track_mid", "title": "Song B", "artist": "Artist 2"}
    t3 = {"id": "track_dup", "title": "Song A", "artist": "Artist 1"}

    with patch("spoff.storage.load_liked_songs", return_value=[dict(t1), dict(t2), dict(t3)]), \
         patch("spoff.storage.save_liked_songs") as mock_save:
        
        ok = move_liked_track(t3, -1, index=2)
        assert ok is True
        saved = mock_save.call_args[0][0]
        assert saved[0]["id"] == "track_dup"
        assert saved[1]["id"] == "track_dup"
        assert saved[2]["id"] == "track_mid"


def test_reorder_spotify_playlist_track_id_resolution_and_payload():
    """Verify reorder_spotify_playlist_track resolves various ID formats and builds Spotify payload."""
    spotify_id = "37i9dQZF1DXcBWIGoYBM5M"
    local_pl_id = "local_pl_123"

    mock_saved = [{
        "id": local_pl_id,
        "name": "Local Link",
        "url": f"https://open.spotify.com/playlist/{spotify_id}"
    }]

    with patch("spoff.auth.get_valid_token", return_value="fake_token"), \
         patch("spoff.auth.has_modify_scopes", return_value=True), \
         patch("spoff.auth.load_saved_playlists", return_value=mock_saved), \
         patch("spoff.auth.spotify_api_request", return_value=(True, {"snapshot_id": "snap1"}, "")) as mock_req:

        # 1. Moving item UP: old=2, new=1 -> insert_before = 1
        ok, msg = reorder_spotify_playlist_track(local_pl_id, old_index=2, new_index=1, token="fake_token")
        assert ok is True
        assert "Reordered" in msg
        mock_req.assert_called_with(
            f"/playlists/{spotify_id}/tracks",
            method="PUT",
            body={"range_start": 2, "insert_before": 1, "range_length": 1},
            token="fake_token"
        )

        # 2. Moving item DOWN: old=0, new=1 -> insert_before = 2
        ok, msg = reorder_spotify_playlist_track(spotify_id, old_index=0, new_index=1, token="fake_token")
        assert ok is True
        mock_req.assert_called_with(
            f"/playlists/{spotify_id}/tracks",
            method="PUT",
            body={"range_start": 0, "insert_before": 2, "range_length": 1},
            token="fake_token"
        )


def test_reorder_spotify_playlist_track_modify_scope_check():
    """Verify missing modify scopes prevent unauthorized reorder requests."""
    with patch("spoff.auth.get_valid_token", return_value="fake_token"), \
         patch("spoff.auth.has_modify_scopes", return_value=False):

        ok, msg = reorder_spotify_playlist_track("37i9dQZF1DXcBWIGoYBM5M", old_index=1, new_index=0)
        assert ok is False
        assert "permission required" in msg


def test_sync_playlist_tracks_to_spotify():
    """Verify sync_playlist_tracks_to_spotify replaces remote track list in bulk."""
    spotify_id = "37i9dQZF1DXcBWIGoYBM5M"
    tracks = [
        {"id": "4iV5W9uYEdYUVa79Axb7Rh", "title": "Track 1"},
        {"id": "yt_123", "source": "ytmusic", "title": "Client YT Song"},  # Should be skipped
        {"uri": "spotify:track:1301WleyT98MSxVHPZCA6M", "title": "Track 2"},
    ]

    with patch("spoff.auth.get_valid_token", return_value="fake_token"), \
         patch("spoff.auth.has_modify_scopes", return_value=True), \
         patch("spoff.auth.spotify_api_request", return_value=(True, {}, "")) as mock_req:

        ok, msg = sync_playlist_tracks_to_spotify(spotify_id, tracks=tracks, token="fake_token")
        assert ok is True
        assert "synced to Spotify" in msg

        expected_uris = [
            "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "spotify:track:1301WleyT98MSxVHPZCA6M"
        ]
        mock_req.assert_called_once_with(
            f"/playlists/{spotify_id}/tracks",
            method="PUT",
            body={"uris": expected_uris},
            token="fake_token"
        )


def test_app_reorder_index_calculation_with_interspersed_client_tracks():
    """Verify SpoffTUI calculates Spotify remote indices correctly when client tracks are mixed in."""
    from spoff.app import SpoffTUI

    with patch("spoff.app.MPVController"), \
         patch("spoff.app.MPRISService"), \
         patch("spoff.app.CavaVisualizer"):

        app = SpoffTUI()
        spotify_id = "37i9dQZF1DXcBWIGoYBM5M"
        app.active_tab = "playlist"
        app.current_playlist_id = spotify_id

        # S0 (remote 0), C1 (client), S2 (remote 1), S3 (remote 2)
        s0 = {"id": "37i9dQZF1DXcBWIGoYBM50", "title": "Spotify 0"}
        c1 = {"id": "yt_abc", "source": "ytmusic", "title": "YT Music"}
        s2 = {"id": "37i9dQZF1DXcBWIGoYBM52", "title": "Spotify 2"}
        s3 = {"id": "37i9dQZF1DXcBWIGoYBM53", "title": "Spotify 3"}

        app.current_playlist_tracks = [s0, c1, s2, s3]
        app.playlists = [{
            "id": spotify_id,
            "spotify_id": spotify_id,
            "tracks": app.current_playlist_tracks
        }]

        # Mock table cursor on s2 (index 2 in table)
        mock_table = MagicMock()
        mock_table.cursor_row = 2
        app.query_one = MagicMock(return_value=mock_table)

        with patch("spoff.app.move_playlist_track", return_value=True), \
             patch("spoff.app.load_saved_playlists", return_value=[{
                 "id": spotify_id,
                 "tracks": [s0, s2, c1, s3]  # After moving s2 from 2 to 1 (past c1)
             }]), \
             patch.object(app, "_submit_spotify_job") as mock_job, \
             patch.object(app, "render_tracks"):

            with patch.object(SpoffTUI, "focused", None):
                app.action_move_item_up()
                mock_job.assert_not_called()

        # Now test moving s2 (at index 1) UP past s0 (at index 0)
        app.current_playlist_tracks = [s0, s2, c1, s3]
        mock_table.cursor_row = 1
        with patch("spoff.app.move_playlist_track", return_value=True), \
             patch("spoff.app.load_saved_playlists", return_value=[{
                 "id": spotify_id,
                 "tracks": [s2, s0, c1, s3]  # After moving s2 from 1 to 0
             }]), \
             patch.object(app, "_submit_spotify_job") as mock_job, \
             patch.object(app, "render_tracks"):

            with patch.object(SpoffTUI, "focused", None):
                app.action_move_item_up()
                # s2 was remote index 1, moves to remote index 0
                mock_job.assert_called_once_with(
                    reorder_spotify_playlist_track,
                    spotify_id,
                    1,
                    0
                )
