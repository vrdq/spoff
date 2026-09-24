import http.cookiejar
from unittest.mock import patch

from spoff import storage, streamer, youtube_account


def _cookie(domain, name, value="v"):
    return http.cookiejar.Cookie(0, name, value, None, False, domain, True, domain.startswith("."), "/", True,
                                 True, 2000000000, False, None, None, {})


def test_login_goes_with_requests_and_only_youtube_google_cookies_are_kept():
    jar = [_cookie(".youtube.com", "SAPISID"), _cookie(".google.com", "SID"), _cookie(".bank.example", "session")]
    streamer.set_youtube_login(("brave", "/profile", "KWALLET6"))
    try:
        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=jar) as extract:
            first = streamer.get_base_ydl_opts()["cookiefile"].read()
            second = streamer.get_base_ydl_opts()["cookiefile"].read()
        extract.assert_called_once_with("brave", "/profile", keyring="KWALLET6")   # decrypted once, then cached
        assert first == second
        assert "SAPISID" in first and "SID" in first and "bank.example" not in first
    finally:
        streamer.set_youtube_login(None)
    assert "cookiefile" not in streamer.get_base_ydl_opts()                         # signed out: no login sent


def test_login_is_found_through_whichever_keyring_can_decrypt_it(tmp_path):
    def fake_extract(browser, profile, logger=None, keyring=None):
        return [_cookie(".youtube.com", "SAPISID", "x" if keyring == "KWALLET6" else "")]

    with patch("yt_dlp.cookies.extract_cookies_from_browser", side_effect=fake_extract):
        assert youtube_account.logged_in_spec(("brave", str(tmp_path), None)) == ("brave", str(tmp_path), "KWALLET6")


def test_saved_login_round_trips_and_accepts_the_old_two_part_form(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "config.json")
    storage.save_config({"youtube_login": ["brave", "/p"]})
    assert storage.get_saved_youtube_login() == ("brave", "/p", None)
    storage.save_youtube_login(("brave", "/p", "KWALLET6"))
    assert storage.get_saved_youtube_login() == ("brave", "/p", "KWALLET6")
    storage.save_youtube_login(None)
    assert storage.get_saved_youtube_login() is None


def test_signed_in_requests_use_music_links_and_a_js_runtime():
    streamer.set_youtube_login(("brave", "/p", "GNOMEKEYRING"))
    try:
        assert streamer._for_login("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        assert streamer._for_login("ytsearch5:x") == "ytsearch5:x"
        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=[]), \
             patch("shutil.which", side_effect=lambda n: "/usr/bin/node" if n == "node" else None):
            assert streamer.get_base_ydl_opts()["js_runtimes"] == {"node": {}}
            assert "js_runtimes" not in streamer.get_base_ydl_opts(use_login=False)
    finally:
        streamer.set_youtube_login(None)
    assert streamer._for_login("https://www.youtube.com/watch?v=dQw4w9WgXcQ").startswith("https://www.")


def test_songs_blocked_by_restricted_mode_play_signed_out():
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            self.signed_in = "cookiefile" in opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            calls.append(self.signed_in)
            if self.signed_in:
                raise Exception("ERROR: [youtube] nt4_p9Pz0RI: Video unavailable")
            return {"url": "https://audio.example/stream", "title": "Are You Bored Yet?", "duration": 178}

    streamer.set_youtube_login(("brave", "/p", None))
    try:
        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=[_cookie(".youtube.com", "SAPISID")]), \
             patch.object(streamer.yt_dlp, "YoutubeDL", FakeYDL):
            res = streamer.search_and_resolve_stream("Are You Bored Yet?", "Wallows",
                                                     direct_url="https://www.youtube.com/watch?v=nt4_p9Pz0RI")
    finally:
        streamer.set_youtube_login(None)
    assert res and res["stream_url"] == "https://audio.example/stream"
    assert calls == [True, False]


def test_the_account_picked_in_the_chooser_wins_over_the_old_login(tmp_path):
    spec = ("brave", str(tmp_path), "GNOMEKEYRING")
    (tmp_path / "Cookies").write_text("")
    old = (tmp_path / "Cookies").stat().st_mtime
    with patch.object(youtube_account, "find_logged_in_browser", return_value=spec):
        assert youtube_account.wait_for_login(timeout=0.3, interval=0.05, since=old + 60) is None   # nothing picked yet
        assert youtube_account.wait_for_login(timeout=0.3, interval=0.05, since=old - 1) == spec
