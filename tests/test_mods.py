import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from spoff import mods
from spoff.mods import Mod


def test_describe_names_presets_and_combinations():
    assert mods.describe(Mod(speed=0.85, reverb="hall", bass=2)) == "slowed + reverb"
    assert mods.describe(Mod(speed=1.3, bass=2)) == "nightcore"
    assert mods.describe(Mod(speed=0.8, reverb="small")) == "slowed + reverb"
    assert mods.describe(Mod(bass=4, eight_d=True)) == "bass boosted + 8D"


def test_sliders_clamp_at_both_ends():
    m = Mod(speed=mods.SPEEDS[0])
    assert mods.with_speed(m, -1).speed == mods.SPEEDS[0]
    assert mods.with_reverb(Mod(reverb="hall"), 1).reverb == "hall"
    assert mods.with_bass(Mod(bass=0), -1).bass == 0
    assert mods.with_bass(Mod(bass=0), 1).bass == 2


def test_live_filters_mix_reverb_under_the_dry_signal():
    assert mods.live_filters(Mod()) == []
    graph = ",".join(mods.live_filters(Mod(reverb="big")))
    assert "afir" in graph and "amix" in graph and "asplit" in graph   # dry + wet, not wet only
    assert graph.endswith("alimiter=limit=0.95")


def test_render_command_speeds_up_tape_style_and_adds_the_impulse_response(tmp_path):
    cmd = mods.render_command(tmp_path / "in.opus", tmp_path / "out.opus",
                              Mod(speed=0.85, reverb="hall"), ["-c:a", "libopus"])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "asetrate=48000*0.85" in graph                      # pitch follows speed
    assert str(mods.IR_DIR / "hall.wav") in cmd
    assert all((mods.IR_DIR / f"{room}.wav").exists() for room in ("small", "big", "hall"))


def test_render_records_a_local_only_track(tmp_path):
    src = tmp_path / "song.opus"
    src.write_bytes(b"x")

    def fake_run(cmd, **kwargs):
        if "-encoders" in cmd:
            return MagicMock(stdout="libopus", returncode=0)
        Path(cmd[-1]).write_bytes(b"rendered")
        return MagicMock(returncode=0, stderr="")

    original = {"id": "abc", "title": "Song", "artist": "Band", "duration_ms": 170000, "source": "spotify"}
    with patch.object(mods, "CACHE_DIR", tmp_path), patch.object(mods, "MODDED_FILE", tmp_path / "m.json"), \
         patch.object(mods, "register_cached_track") as register, \
         patch.object(mods.shutil, "which", return_value="/usr/bin/ffmpeg"), \
         patch.object(mods.subprocess, "run", side_effect=fake_run):
        track = mods.render(original, src, Mod(speed=0.85, reverb="hall", bass=2))
        assert mods.load_modded() == [track]

    from spoff.auth import is_client_side_track
    assert track["title"] == "Song (slowed + reverb)"
    assert track["duration_ms"] == 200000
    assert is_client_side_track(track)                           # never synced to Spotify
    assert register.call_args.args[2].name.endswith(".opus")


def test_render_failure_leaves_no_partial_file(tmp_path):
    def fake_run(cmd, **kwargs):
        if "-encoders" in cmd:
            return MagicMock(stdout="", returncode=0)
        Path(cmd[-1]).write_bytes(b"half")
        return MagicMock(returncode=1, stderr="boom")

    with patch.object(mods, "CACHE_DIR", tmp_path), patch.object(mods, "MODDED_FILE", tmp_path / "m.json"), \
         patch.object(mods.shutil, "which", return_value="/usr/bin/ffmpeg"), \
         patch.object(mods.subprocess, "run", side_effect=fake_run):
        try:
            mods.render({"id": "a", "title": "S"}, tmp_path / "s.opus", Mod(bass=4))
            raise AssertionError("expected a failure")
        except RuntimeError as exc:
            assert "ffmpeg" in str(exc)
    assert not list(tmp_path.glob("mod_*"))


def test_mod_files_are_never_upgraded_or_copied_to_youtube_music(tmp_path):
    from spoff import storage, ytmusic_sync
    f = tmp_path / "mod_1234.m4a"
    f.write_bytes(b"x")
    assert not storage.is_low_quality_cache(f)
    assert ytmusic_sync.video_id_for({"id": "mod_1", "mod_of": "a", "title": "S (sped up)"},
                                     {"videos": {}}, {}) is None


def test_mod_menu_end_to_end(tmp_path, monkeypatch):
    """Press m on a downloaded song, move through presets, adjust a slider, save."""
    import asyncio
    monkeypatch.setenv("HOME", str(tmp_path))
    song = tmp_path / "sine.opus"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=3", "-c:a", "libopus", str(song)],
                   check=True)

    from spoff import app as app_mod

    async def run():
        app = app_mod.SpoffTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            await pilot.press("escape")
            track = {"id": "t1", "title": "Test song", "artist": "Band", "duration_ms": 3000, "source": "local"}
            app.search_results = [track]
            app.switch_view("search")
            await pilot.pause(0.2)
            app.query_one("#track-table").focus()
            set_mod = MagicMock()
            monkeypatch.setattr(app.player, "set_mod", set_mod)
            monkeypatch.setattr(app.player, "clear_mod", MagicMock())
            monkeypatch.setattr(app, "play_current_table_row", MagicMock())
            rendered = []
            monkeypatch.setattr(app_mod.mods, "render", lambda t, src, mod: rendered.append((t["id"], src, mod)) or
                                {**t, "id": "mod_x", "title": "Test song (slowed + reverb)"})

            monkeypatch.setattr(app_mod, "get_cached_track_path", lambda tid: None)
            await pilot.press("m")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, app_mod.ModModal)   # not downloaded: says so instead

            monkeypatch.setattr(app_mod, "get_cached_track_path", lambda tid: song)
            await pilot.press("m")
            await pilot.pause(0.2)
            assert isinstance(app.screen, app_mod.ModModal)
            await pilot.press("j")                                  # Slowed + reverb, applied live
            await pilot.pause(0.1)
            filters, speed = set_mod.call_args.args
            assert speed == 0.85 and any("afir" in f for f in filters)
            await pilot.press("l", "j", "l")                        # sliders: reverb up (already hall: stays)
            await pilot.press("j", "l")                             # bass +2 -> +4 = custom
            await pilot.pause(0.1)
            assert app.screen.mod.bass == 4
            await pilot.press("escape")                             # save
            await pilot.pause(0.5)
            assert rendered and rendered[0][0] == "t1" and rendered[0][2].bass == 4
            app.player.clear_mod.assert_called()

    asyncio.run(run())
