import asyncio
from unittest.mock import patch

from spoff import auth, storage


def test_pending_like_survives_sync_that_predates_it():
    """A Spotify track liked locally but not yet on Spotify is kept by the merge."""
    remote = [{"id": "A" * 22, "title": "Remote", "artist": "R",
               "uri": "spotify:track:" + "A" * 22, "source": "spotify"}]
    pending = {"id": "B" * 22, "title": "Just liked", "artist": "S",
               "uri": "spotify:track:" + "B" * 22, "source": "spotify",
               "spotify_sync_pending": True}
    unliked_on_phone = {"id": "C" * 22, "title": "Gone", "artist": "T",
                        "uri": "spotify:track:" + "C" * 22, "source": "spotify"}

    merged = auth.merge_spotify_and_client_tracks(list(remote), [pending, unliked_on_phone] + remote)

    titles = [t["title"] for t in merged]
    assert "Just liked" in titles
    assert "Gone" not in titles


def test_right_arrow_in_sidebar_opens_playlist_instead_of_seeking(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "PLAYLISTS_FILE", tmp_path / "playlists.json")
    storage.save_saved_playlists([
        {"id": "local_a", "name": "A", "tracks": [{"id": "dQw4w9WgXcQ", "title": "T", "artist": "X"}]},
    ])
    from spoff.app import SpoffTUI

    async def run():
        app = SpoffTUI(visualizer_enabled=False, notifications_enabled=False)
        with patch.object(app, "check_github_updates_bg"), \
             patch.object(app, "backfill_playlists_art_bg"), \
             patch.object(app.player, "start_mpv"), \
             patch("spoff.app.is_first_launch", return_value=False):
            async with app.run_test() as pilot:
                await pilot.pause(0.8)
                seeks = []
                app.player.seek = seeks.append
                app.query_one("#side-table").focus()
                await pilot.pause(0.1)
                await pilot.press("right")
                await pilot.pause(0.2)
                assert seeks == []
                assert app.focused.id == "track-table"

    asyncio.run(run())


def _isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "LIKED_SONGS_FILE", tmp_path / "liked_songs.json")
    monkeypatch.setattr(auth, "AUTH_FILE", tmp_path / "spotify_auth.json")


def test_expired_token_is_refreshed_once_and_request_retried(tmp_path, monkeypatch):
    import io
    import urllib.error
    _isolate_storage(tmp_path, monkeypatch)
    auth.save_spotify_auth({"access_token": "old", "refresh_token": "r", "expires_at": 9e12})
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen.append(req.headers["Authorization"])
        if req.headers["Authorization"] == "Bearer old":
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        return Resp(b'{"ok": true}')

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch.object(auth, "refresh_spotify_token", return_value={"access_token": "new", "refresh_token": "r"}):
        ok, data, _ = auth.spotify_api_request("/me", token="old")

    assert ok and data == {"ok": True}
    assert seen == ["Bearer old", "Bearer new"]
    assert auth.load_spotify_auth()["access_token"] == "new"


def test_unlike_that_failed_to_reach_spotify_is_not_resurrected(tmp_path, monkeypatch):
    _isolate_storage(tmp_path, monkeypatch)
    sp_id = "C" * 22
    track = {"id": sp_id, "title": "Gone", "artist": "T", "uri": "spotify:track:" + sp_id, "source": "spotify"}
    storage.save_liked_songs([track])
    assert storage.remove_liked_track(track)
    assert storage.get_pending_spotify_unlikes() == {sp_id}

    remote = [dict(track), {"id": "D" * 22, "title": "Kept", "artist": "U", "source": "spotify"}]
    with patch.object(auth, "has_modify_scopes", return_value=True), \
         patch.object(auth, "spotify_api_request", return_value=(False, None, "offline")):
        filtered = auth.apply_pending_unlikes(remote, "tok")
    assert [t["title"] for t in filtered] == ["Kept"]
    assert storage.get_pending_spotify_unlikes() == {sp_id}  # still pending after a failed retry

    with patch.object(auth, "has_modify_scopes", return_value=True), \
         patch.object(auth, "spotify_api_request", return_value=(True, {}, "")):
        auth.apply_pending_unlikes(remote, "tok")
    assert storage.get_pending_spotify_unlikes() == set()


def test_artwork_from_a_different_artist_is_rejected():
    import io
    import json
    from spoff import art

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    wrong = {"results": [{"artistName": "Someone Else", "artworkUrl100": "https://x/100x100bb.jpg"}]}
    with patch("urllib.request.urlopen", return_value=Resp(json.dumps(wrong).encode())):
        assert art.fetch_itunes_art("Song", "Real Artist - Topic") is None
    right = {"results": [{"artistName": "Real Artist", "artworkUrl100": "https://x/100x100bb.jpg"}]}
    with patch("urllib.request.urlopen", return_value=Resp(json.dumps(right).encode())):
        assert art.fetch_itunes_art("Song", "Real Artist - Topic") == "https://x/600x600bb.jpg"
