from unittest.mock import patch

from spoff import storage


def _file(tmp_path, name="a.opus"):
    path = tmp_path / name
    path.write_bytes(b"x" * 100)
    return path


def test_low_bitrate_opus_is_upgraded_only_while_signed_in(tmp_path):
    path = _file(tmp_path)
    with patch.object(storage, "cache_bitrate_kbps", return_value=130), \
         patch.object(storage, "HQ_CHECKED_FILE", tmp_path / "checked.json"):
        storage.set_hq_upgrades(False)
        assert not storage.is_low_quality_cache(path)
        storage.set_hq_upgrades(True)
        try:
            assert storage.is_low_quality_cache(path)
            storage.mark_hq_checked(path)            # a signed-in download made it: best available
            assert not storage.is_low_quality_cache(path)
        finally:
            storage.set_hq_upgrades(False)


def test_premium_files_and_unknown_bitrate_are_kept(tmp_path):
    path = _file(tmp_path)
    with patch.object(storage, "HQ_CHECKED_FILE", tmp_path / "checked.json"):
        storage.set_hq_upgrades(True)
        try:
            with patch.object(storage, "cache_bitrate_kbps", return_value=257):
                assert not storage.is_low_quality_cache(path)
            with patch.object(storage, "cache_bitrate_kbps", return_value=0):
                assert not storage.is_low_quality_cache(path)
        finally:
            storage.set_hq_upgrades(False)


def test_signing_in_turns_upgrades_on():
    from spoff import streamer
    streamer.set_youtube_login(("brave", None, None))
    try:
        assert storage._hq_upgrades
    finally:
        streamer.set_youtube_login(None)
    assert not storage._hq_upgrades
