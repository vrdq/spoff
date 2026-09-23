"""Activity above the seek bar belongs to the request that is still active."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from textual.widgets import Static
from spoff import app


def fake_app(**kwargs):
    pill = Mock()
    values = dict(_is_mounted=True, _pending_track=None, _search_loading_request_id=None,
                  _search_request_id=1, _closing=False, active_tab='search', advanced_mode=False,
                  query_one=Mock(return_value=pill), call_from_thread=lambda fn,*a:fn(*a),
                  notify_user=Mock(), render_tracks=Mock())
    values.update(kwargs)
    return SimpleNamespace(**values), pill


def test_searching_is_independent_of_notifications():
    fake,pill = fake_app(_search_loading_request_id=1, notifications_enabled=False)
    app.SpoffTUI._update_loading_status(fake)
    pill.update.assert_called_with('Searching…')
    assert pill.display is True
    fake._pending_track={'title':'Song'}
    app.SpoffTUI._update_loading_status(fake)
    pill.update.assert_called_with('Loading song… · Searching…')
    fake._search_loading_request_id=None
    app.SpoffTUI._update_loading_status(fake)
    pill.update.assert_called_with('Loading song…')
    fake._pending_track=None
    app.SpoffTUI._update_loading_status(fake)
    assert pill.display is False


def test_search_success_clears_activity():
    fake,pill=fake_app(_search_loading_request_id=1)
    with patch.object(app.SpoffTUI,'_run_search'):
        app.SpoffTUI._search_worker.__wrapped__(fake,'song',1,'ytmusic','YouTube Music')
    assert fake._search_loading_request_id is None
    assert pill.display is False


def test_search_exception_clears_activity_and_reports_failure():
    fake,pill=fake_app(_search_loading_request_id=1)
    with patch.object(app.SpoffTUI,'_run_search',side_effect=RuntimeError('provider failed')):
        app.SpoffTUI._search_worker.__wrapped__(fake,'song',1,'ytmusic','YouTube Music')
    assert fake._search_loading_request_id is None
    assert pill.display is False
    fake.notify_user.assert_called_once_with('Search failed. Please try again.',force=True)


def test_old_search_cannot_clear_new_activity():
    fake,pill=fake_app(_search_loading_request_id=1)
    def newer_search(*args):
        fake._search_request_id=2
        fake._search_loading_request_id=2
    with patch.object(app.SpoffTUI,'_run_search',side_effect=newer_search):
        app.SpoffTUI._search_worker.__wrapped__(fake,'song',1,'ytmusic','YouTube Music')
    assert fake._search_loading_request_id == 2
    pill.update.assert_not_called()


def test_playback_preparation_exception_uses_normal_recovery():
    track={'id':'song','title':'Song'}
    fake,_=fake_app(_playback_failed=Mock())
    with patch.object(app.SpoffTUI,'_resolve_playback',side_effect=OSError('cache unavailable')):
        app.SpoffTUI.start_playback.__wrapped__(fake,track,7)
    fake._playback_failed.assert_called_once_with(7,track)


class LoadingHarness(app.SpoffTUI):
    def on_mount(self, event):
        event.prevent_default()
        # Exercise the real layout without starting network/background integrations.
        self._thread_id=threading.get_ident()
        self._is_ready=True
        self.player=Mock()
        self.player.get_progress.return_value=(0,0)
        self.player.current_track=None
        self.player.is_paused=False
        self.mpris=None


@pytest.mark.parametrize('size',[(100,30),(60,20)])
def test_loading_is_visible_above_seek_bar_in_real_layout(size):
    async def check_layout():
        application=LoadingHarness(visualizer_enabled=False,notifications_enabled=False)
        async with application.run_test(size=size) as pilot:
            application._search_loading_request_id=1
            application._update_loading_status()
            await pilot.pause()
            pill=application.query_one('#loading-pill',Static)
            bar=application.query_one('#playback-bar')
            assert pill.display
            assert pill.region.width >= len('Searching…')
            assert pill.region.y < bar.region.y
            assert pill.region.right <= size[0]
            application._pending_track={'title':'Song'}
            application._update_loading_status()
            await pilot.pause()
            assert pill.region.width >= len('Loading song… · Searching…')
            application._search_loading_request_id=None
            application._pending_track=None
            application._update_loading_status()
            await pilot.pause()
            assert not pill.display
    asyncio.run(check_layout())
