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
