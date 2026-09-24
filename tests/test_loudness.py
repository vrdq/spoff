import json
from unittest.mock import MagicMock, patch

from spoff import storage
from spoff.player import MPVController


def test_filter_chain_is_eq_then_loudness_and_can_be_turned_off():
    eq = MagicMock()
    eq.to_ffmpeg_af.return_value = "equalizer=f=1000:t=q:w=1:g=2"
    player = MPVController(socket_path="/nonexistent.sock", eq_engine=eq)
    assert player._audio_filters() == "equalizer=f=1000:t=q:w=1:g=2," + MPVController.LOUDNORM
    with patch.object(player, "_send_command", return_value=True) as send:
        player.set_loudness_normalization(False)
    send.assert_called_once_with(["set_property", "af", "equalizer=f=1000:t=q:w=1:g=2"])
    eq.to_ffmpeg_af.return_value = ""          # EQ bypassed
    player.loudness_normalization = True
    assert player._audio_filters() == MPVController.LOUDNORM


def test_saved_eq_above_48k_moves_to_48k_once(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "config.json")
    storage.save_config({"eq": {"sample_rate": 96000.0, "preset_name": "Mine"}})

    assert storage.migrate_eq_to_native_rate() is True
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["eq"]["sample_rate"] == 48000.0 and cfg["eq"]["preset_name"] == "Mine"

    cfg["eq"]["sample_rate"] = 96000.0          # chosen again later on purpose
    storage.save_config(cfg)
    assert storage.migrate_eq_to_native_rate() is False
    assert json.loads((tmp_path / "config.json").read_text())["eq"]["sample_rate"] == 96000.0
