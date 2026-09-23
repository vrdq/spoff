"""Completed jobs must release their admission slot before notifying callers."""
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from spoff import streamer


@pytest.mark.parametrize('first_fails', [False, True])
def test_completion_can_start_next_download_with_last_slot(first_fails):
    slots = threading.BoundedSemaphore(1)
    calls = []
    errors = []
    completed = []

    def download(identifier, *args, **kwargs):
        calls.append(identifier)
        if first_fails and identifier == 'first':
            raise RuntimeError('network failed')
        return Path(identifier)

    def next_download(_):
        assert 'first' not in streamer._active_downloads
        assert 'first' not in streamer._active_download_futures
        streamer.download_track_to_cache('second', 'Second', 'Artist', blocking=True,
                                         on_complete=completed.append, on_error=errors.append)

    with patch.object(streamer, '_download_slots', slots), \
         patch.object(streamer, '_active_downloads', set()), \
         patch.object(streamer, '_active_download_futures', {}), \
         patch.object(streamer, 'get_cached_track_path', return_value=None), \
         patch.object(streamer, '_run_download_process', side_effect=download):
        streamer.download_track_to_cache('first', 'First', 'Artist', blocking=True,
                                         on_complete=next_download, on_error=next_download)
        assert calls == ['first', 'second']
        assert completed == [Path('second')]
        assert not errors
        assert not streamer._active_download_futures
        assert not streamer._active_downloads
        assert slots.acquire(blocking=False)
        assert not slots.acquire(blocking=False)
        slots.release()


def test_notification_failure_does_not_report_successful_download_as_failed():
    errors = []

    def broken_notification(path):
        raise RuntimeError('UI was unmounted')

    with patch.object(streamer, 'get_cached_track_path', return_value=None), \
         patch.object(streamer, '_run_download_process', return_value=Path('audio')):
        streamer.download_track_to_cache('notification_test', 'Song', 'Artist', blocking=True,
                                         on_complete=broken_notification, on_error=errors.append)
    assert errors == []


def test_worker_start_failure_reports_once_without_escaping_to_ui():
    errors=[]
    slots=threading.BoundedSemaphore(1)
    with patch.object(streamer,'get_cached_track_path',return_value=None), \
         patch.object(streamer,'_active_downloads',set()), \
         patch.object(streamer,'_active_download_futures',{}), \
         patch.object(streamer,'_download_slots',slots), \
         patch.object(streamer.threading,'Thread') as thread:
        thread.return_value.start.side_effect=RuntimeError('cannot start new thread')
        assert streamer.download_track_to_cache('song','Song','Artist',on_error=errors.append) is None
        assert len(errors)==1
        assert str(errors[0])=='cannot start new thread'
        assert not streamer._active_downloads
        assert not streamer._active_download_futures
        assert slots.acquire(blocking=False)
        assert not slots.acquire(blocking=False)
        slots.release()


def test_cached_registration_failure_reports_error_not_success(tmp_path):
    cached = tmp_path / 'song.m4a'
    cached.write_bytes(b'audio')
    completed, errors = [], []
    with patch.object(streamer, 'get_cached_track_path', return_value=cached), \
         patch.object(streamer, 'register_cached_track', side_effect=OSError('disk full')):
        streamer.download_track_to_cache('song', 'Song', 'Artist', blocking=True,
                                         on_complete=completed.append, on_error=errors.append)
    assert completed == []
    assert len(errors) == 1
    assert str(errors[0]) == 'disk full'


def test_cached_success_callback_failure_is_contained(tmp_path):
    cached = tmp_path / 'song.m4a'
    cached.write_bytes(b'audio')
    errors = []
    def broken_callback(_):
        raise RuntimeError('unmounted')
    with patch.object(streamer, 'get_cached_track_path', return_value=cached), \
         patch.object(streamer, 'register_cached_track'):
        streamer.download_track_to_cache('song', 'Song', 'Artist', blocking=True,
                                         on_complete=broken_callback, on_error=errors.append)
    assert not errors


def test_invalid_id_error_callback_failure_is_contained():
    def broken_callback(_):
        raise RuntimeError('unmounted')
    assert streamer.download_track_to_cache('../escape', 'Song', 'Artist',
                                            on_error=broken_callback) is None
