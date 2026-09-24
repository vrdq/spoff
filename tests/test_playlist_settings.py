import asyncio
import os
from unittest.mock import patch

from spoff import auth, storage


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "PLAYLISTS_FILE", tmp_path / "playlists.json")
    monkeypatch.setattr(auth, "AUTH_FILE", tmp_path / "spotify_auth.json")


def test_shift_s_edits_name_description_and_visibility(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    storage.save_saved_playlists([{
        "id": "local_a", "name": "Late Night", "spotify_id": "1KUyRdfZw7qhwKG6xqyIE8",
        "public": False, "owner_id": "me", "tracks": [],
    }])
    auth.save_spotify_auth({"access_token": "t", "user": {"id": "me"}})
    from spoff.app import SpoffTUI, PlaylistSettingsModal
    pushed = []

    async def run():
        app = SpoffTUI(visualizer_enabled=False, notifications_enabled=False)
        app._submit_spotify_job = lambda fn, *a, **k: fn(*a, **k)
        with patch.object(app, "check_github_updates_bg"), \
             patch.object(app, "backfill_playlists_art_bg"), \
             patch.object(app, "_sync_liked_from_spotify_bg"), \
             patch.object(app.player, "start_mpv"), \
             patch("spoff.app.is_first_launch", return_value=False), \
             patch("spoff.app.update_spotify_playlist_details",
                   side_effect=lambda pid, **kw: pushed.append((pid, kw)) or (True, "ok")):
            async with app.run_test(size=(130, 42)) as pilot:
                await pilot.pause(0.8)
                app.query_one("#side-table").focus()
                await pilot.press("S")
                await pilot.pause(0.3)
                assert isinstance(app.screen, PlaylistSettingsModal)
                if os.environ.get("SPOFF_SHOT_DIR"):
                    app.save_screenshot(filename="plset.svg", path=os.environ["SPOFF_SHOT_DIR"])

                # Vim flow: j/k never type into a field unless it is being edited.
                await pilot.press("j", "k")
                assert app.screen.focused.id == "plset-name-field"
                await pilot.press("enter")                       # edit name
                app.screen.query_one("#plset-name").value = "Late Night Drive"
                await pilot.press("escape")                      # back to normal mode
                assert app.screen.query_one("#plset-name").value == "Late Night Drive"
                await pilot.press("j", "i", *"slow jk songs", "enter")   # j/k inside edit are text
                await pilot.press("j", "space")                  # visibility -> public
                await pilot.press("w")                           # save
                await pilot.pause(0.3)
                assert not isinstance(app.screen, PlaylistSettingsModal)

    asyncio.run(run())

    saved = storage.load_saved_playlists()[0]
    assert (saved["name"], saved["description"], saved["public"]) == ("Late Night Drive", "slow jk songs", True)
    assert pushed == [("local_a", {"remote_id": "1KUyRdfZw7qhwKG6xqyIE8", "name": "Late Night Drive",
                                   "description": "slow jk songs", "public": True})]


def test_local_playlist_settings_are_used_when_created_on_spotify(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    storage.save_saved_playlists([{"id": "local_b", "name": "Gym", "public": True,
                                   "description": "loud", "tracks": []}])
    bodies = []

    def fake_api(url, method="GET", body=None, token=None):
        if method == "POST" and url == "/me/playlists":
            bodies.append(body)
            return True, {"id": "n" * 22}, ""
        return True, {}, ""

    with patch.object(auth, "has_modify_scopes", return_value=True), \
         patch.object(auth, "spotify_api_request", side_effect=fake_api):
        ok, _ = auth.sync_playlist_tracks_to_spotify("local_b", token="t")
    assert ok
    assert bodies == [{"name": "Gym", "description": "loud", "public": True}]


def test_followed_playlist_edits_never_reach_spotify(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with patch.object(auth, "has_modify_scopes", return_value=True), \
         patch.object(auth, "spotify_api_request", return_value=(False, None, "HTTP 403: Forbidden")):
        ok, msg = auth.update_spotify_playlist_details("x", public=True, token="t", remote_id="r" * 22)
    assert not ok and "owner" in msg
