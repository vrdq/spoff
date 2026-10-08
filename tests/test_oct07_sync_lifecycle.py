from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from spoff import app, storage, ytmusic_sync


@pytest.mark.parametrize('closing,enabled', [(True, True), (False, False)])
def test_delayed_sync_does_not_write_after_exit_or_disable(closing, enabled):
    fake = SimpleNamespace(_shutting_down=closing, youtube_signed_in=lambda: True)
    with patch.object(app, 'get_saved_ytmusic_playlist_sync', return_value=enabled), \
         patch.object(ytmusic_sync, 'sync_playlists') as sync:
        app.SpoffTUI._run_ytmusic_sync(fake)
    sync.assert_not_called()


def test_busy_sync_is_rescheduled_with_fresh_snapshot():
    fake = SimpleNamespace(_shutting_down=False, youtube_signed_in=lambda: True,
                           schedule_ytmusic_sync=Mock())
    with patch.object(app, 'get_saved_ytmusic_playlist_sync', return_value=True), \
         patch.object(app, 'load_saved_playlists', return_value=[]), \
         patch.object(ytmusic_sync, 'sync_playlists', return_value={'busy': 1}):
        app.SpoffTUI._run_ytmusic_sync(fake, announce=True)
    fake.schedule_ytmusic_sync.assert_called_once_with(announce=True)


def test_busy_sync_is_not_reported_as_success():
    with ytmusic_sync._sync_lock:
        assert ytmusic_sync.sync_playlists([]).get('busy') == 1


def test_playlist_listener_can_be_removed_before_app_exits():
    listener = Mock()
    with patch.object(storage, '_playlist_listeners', []), \
         patch.object(storage, '_atomic_json_dump'):
        storage.on_playlists_saved(listener)
        storage.remove_playlists_saved_listener(listener)
        storage.save_saved_playlists([])
    listener.assert_not_called()
