"""Regression checks for the follow-up audit's matching and playback changes."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from spoff import streamer
from spoff.app import SpoffTUI
from spoff.matching import _matches_recording, _tracks_match


def test_long_recording_accepts_formatted_provider_duration():
    assert _matches_recording(
        {'title': 'Long Song', 'artist': 'Artist', 'duration': '20:00'},
        'Long Song', 'Artist', 1200,
    )


def test_long_recording_rejects_short_clip():
    assert not _matches_recording(
        {'title': 'Long Song', 'artist': 'Artist', 'duration': 1.2},
        'Long Song', 'Artist', 1200,
    )


def test_long_cached_recording_duration_uses_explicit_units():
    probe = Mock(stdout=json.dumps({'format': {'duration': '1200'}}))
    with patch.object(streamer.subprocess, 'run', return_value=probe):
        assert streamer.cached_audio_matches_duration(Path('audio'), 1200000)


@pytest.mark.parametrize('band,unrelated', [
    ('AC/DC', 'AC'), ('Florence and the Machine', 'the Machine'),
    ('Earth, Wind & Fire', 'Fire'),
])
def test_band_name_components_are_not_recording_identity(band, unrelated):
    candidate = {'title': 'Song', 'artist': unrelated, 'duration': 200}
    assert not _matches_recording(candidate, 'Song', band, 200)
    assert not _tracks_match(candidate, {'title': 'Song', 'artist': band})


@pytest.mark.parametrize('has_previous_track', [False, True])
def test_play_during_cached_preparation_does_not_restart_or_toggle_old_track(has_previous_track):
    pending = {'id': 'new', 'title': 'New', 'is_offline': True}
    fake = SimpleNamespace(
        focused=None, _pending_track=pending,
        player=Mock(current_track={'id': 'old'} if has_previous_track else None),
        notify_user=Mock(), update_player_hud=Mock(), _start_or_resume_playback=Mock(),
    )
    SpoffTUI.action_toggle_play(fake)
    fake.player.toggle_pause.assert_not_called()
    fake._start_or_resume_playback.assert_not_called()


def test_select_pending_cached_row_does_not_restart_request():
    pending = {'id': 'new', 'title': 'New', 'is_offline': True}
    fake = SimpleNamespace(
        _pending_track=pending, _get_current_view_tracks=lambda: [pending],
        _is_same_track=lambda a, b: SpoffTUI._is_same_track(None, a, b),
        player=Mock(current_track={'id': 'old'}), notify_user=Mock(),
        update_player_hud=Mock(), play_index=Mock(),
    )
    SpoffTUI.play_current_table_row(fake, 0)
    fake.player.toggle_pause.assert_not_called()
    fake.play_index.assert_not_called()
