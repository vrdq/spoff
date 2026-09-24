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
                  search_results=[], current_playlist_tracks=[], current_liked_tracks=[],
                  query_one=Mock(return_value=pill), call_from_thread=lambda fn,*a:fn(*a),
                  notify_user=Mock(), render_tracks=Mock())
    values.update(kwargs)
    return SimpleNamespace(**values), pill


def test_searching_is_independent_of_notifications():
    fake,pill = fake_app(_search_loading_request_id=1, notifications_enabled=False)
    app.SpoffTUI._update_loading_status(fake)
    pill.update.assert_called_with('Searching…')
    fake._pending_track={'title':'Song'}
    app.SpoffTUI._update_loading_status(fake)
    pill.update.assert_called_with("Loading 'Song'…")
    fake._search_loading_request_id=None
    app.SpoffTUI._update_loading_status(fake)
    pill.update.assert_called_with("Loading 'Song'…")
    fake._pending_track=None
    app.SpoffTUI._update_loading_status(fake)
    pill.update.assert_called_with('')


def test_search_success_clears_activity():
    fake,pill=fake_app(_search_loading_request_id=1)
    with patch.object(app.SpoffTUI,'_run_search'):
        app.SpoffTUI._search_worker.__wrapped__(fake,'song',1,'ytmusic','YouTube Music')
    assert fake._search_loading_request_id is None
    pill.update.assert_called_with('')


def test_search_exception_clears_activity_and_reports_failure():
    fake,pill=fake_app(_search_loading_request_id=1)
    with patch.object(app.SpoffTUI,'_run_search',side_effect=RuntimeError('provider failed')):
        app.SpoffTUI._search_worker.__wrapped__(fake,'song',1,'ytmusic','YouTube Music')
    assert fake._search_loading_request_id is None
    pill.update.assert_called_with('')
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


@pytest.mark.parametrize('size', [(100, 30), (60, 20)])
def test_sidebar_browsing_preserves_status_and_marks_open_playlist(size):
    async def check():
        application = LoadingHarness(visualizer_enabled=False)
        async with application.run_test(size=size) as pilot:
            table = application.query_one('#side-table', app.DataTable)
            table.add_column('Playlist')
            application.playlists = [
                {'id': 'one', 'name': 'Evening', 'tracks': [{'id': 'song'}]},
                {'id': 'two', 'name': 'Weekend', 'tracks': []},
            ]
            application.current_playlist_id = 'one'
            application.advanced_mode = False
            application.refresh_side_table()
            application.set_download_status('Downloading 2/5', channel='bulk')
            table.focus()
            await pilot.press('down')
            await pilot.pause()
            assert application.current_playlist_id == 'one'
            assert table.get_cell_at(app.Coordinate(0, 0)).plain == 'Evening'
            assert table.get_cell_at(app.Coordinate(1, 0)).plain == 'Weekend'
            assert table.show_cursor
            selected_row = table.cursor_row
            application.query_one('#track-table', app.DataTable).focus()
            await pilot.pause()
            assert not table.show_cursor
            table.focus()
            await pilot.pause()
            assert table.show_cursor
            assert table.cursor_row == selected_row
            assert str(application.query_one('#notification-line', Static).content) == 'Downloading 2/5'
            hint = application.query_one('#sidebar-hint', Static)
            assert hint.region.height >= 1
            assert hint.region.bottom <= size[1]
    asyncio.run(check())


@pytest.mark.parametrize('size',[(100,30),(60,20)])
def test_loading_is_visible_above_seek_bar_in_real_layout(size):
    async def check_layout():
        application=LoadingHarness(visualizer_enabled=False,notifications_enabled=False)
        async with application.run_test(size=size) as pilot:
            application._search_loading_request_id=1
            application._update_loading_status()
            await pilot.pause()
            pill=application.query_one('#notification-line',Static)
            bar=application.query_one('#playback-bar')
            assert pill.display
            assert pill.region.width >= len('Searching…')
            assert pill.region.y < bar.region.y
            assert pill.region.right <= size[0]
            application._pending_track={'title':'Song'}
            application._update_loading_status()
            await pilot.pause()
            assert str(pill.content) == "Loading 'Song'…"
            application._search_loading_request_id=None
            application._pending_track=None
            application._update_loading_status()
            await pilot.pause()
            assert str(pill.content) == ""
            assert not list(application.query("#loading-pill, #download-pill"))
            application.set_download_status("Downloading 3/15 from Favorites: Song", channel="bulk")
            await pilot.pause()
            assert str(pill.content) == "Downloading 3/15 from Favorites: Song"
            assert pill.region.y < bar.region.y
            assert not list(application.query("Toast"))
    asyncio.run(check_layout())


@pytest.mark.parametrize('remaining', [[], [{'id': 'kept', 'name': 'Kept', 'tracks': []}]])
def test_playlist_removed_during_sync_cannot_leave_stale_tracks(remaining):
    async def check():
        application = LoadingHarness(visualizer_enabled=False)
        async with application.run_test(size=(100, 30)) as pilot:
            application.query_one('#side-table', app.DataTable).add_column('Playlist')
            table = application.query_one('#track-table', app.DataTable)
            table.add_columns('Source', 'Title', 'Artist', 'Duration')
            application.current_playlist_id = 'removed'
            application.current_playlist_tracks = [{'id': 'old', 'title': 'Old song'}]
            with patch.object(app, 'load_saved_playlists', return_value=remaining), \
                 patch.object(app, 'save_last_tab'):
                application.switch_view('playlist')
            await pilot.pause()
            assert application.current_playlist_id == ('kept' if remaining else None)
            assert application.current_playlist_tracks == []
            assert table.row_count == 0
            assert application.query_one('#side-table', app.DataTable).row_count == len(remaining)
    asyncio.run(check())


def test_download_channels_share_one_line_and_resume_after_completion():
    fake,bar=fake_app(_thread_id=threading.get_ident())
    timers=[]
    fake.set_timer=lambda delay,fn: timers.append(fn)
    app.SpoffTUI.set_download_status(fake,'Downloading 2/10 from Favorites',channel='bulk')
    app.SpoffTUI.set_download_status(fake,'Saved Song offline.',channel='single',clear_after=4)
    bar.update.assert_called_with('Saved Song offline.')
    timers[0]()
    bar.update.assert_called_with('Downloading 2/10 from Favorites')
    assert set(fake._download_statuses) == {'bulk'}


def test_foreground_loading_temporarily_replaces_download_progress():
    fake,bar=fake_app(_thread_id=threading.get_ident())
    app.SpoffTUI.set_download_status(fake,'Downloading 2/10 from Favorites',channel='bulk')
    fake._pending_track={'title':'Song'}
    app.SpoffTUI._render_status_line(fake)
    bar.update.assert_called_with("Loading 'Song'…")
    fake._pending_track=None
    app.SpoffTUI._render_status_line(fake)
    bar.update.assert_called_with('Downloading 2/10 from Favorites')


def test_brief_copy_confirmation_resumes_existing_download():
    fake,bar=fake_app(_thread_id=threading.get_ident())
    app.SpoffTUI.set_download_status(fake,'Downloading 2/10 from Favorites',channel='bulk')
    with patch.object(app.time,'monotonic',return_value=100):
        app.SpoffTUI.notify_user(fake,'Copied song link.',force=True)
    bar.update.assert_called_with('Copied song link.')
    with patch.object(app.time,'monotonic',return_value=104):
        app.SpoffTUI._render_status_line(fake)
    bar.update.assert_called_with('Downloading 2/10 from Favorites')


@pytest.mark.parametrize('tab', ['search', 'playlist', 'liked'])
def test_single_download_emits_no_duplicate_toast(tab):
    from types import MethodType
    fake,bar=fake_app(_thread_id=threading.get_ident(),_on_ui=lambda fn,*a,**kw:fn(*a,**kw),
                      active_tab=tab, search_results=[], current_playlist_tracks=[],
                      current_liked_tracks=[], notify=Mock(),set_timer=Mock())
    fake.set_download_status=MethodType(app.SpoffTUI.set_download_status,fake)
    with patch.object(app,'get_cached_track_path',return_value=None),patch.object(app,'download_track_to_cache') as download:
        app.SpoffTUI._download_single_track(fake,{'id':'song','title':'Song','artist':'Artist'})
        bar.update.assert_called_with("Downloading 'Song'…")
        download.call_args.kwargs['on_complete']('song.opus')
    bar.update.assert_called_with("Saved 'Song' offline.")
    fake.notify.assert_not_called()
    fake.render_tracks.assert_called_once_with([])
    fake.query_one.assert_called_with('#notification-line',Static)


@pytest.mark.parametrize('already_cached',[False,True])
@pytest.mark.parametrize('tab', ['search', 'playlist', 'liked'])
def test_bulk_download_runs_and_reports_only_one_status_surface(tmp_path,already_cached,tab):
    from types import MethodType
    fake,bar=fake_app(_thread_id=threading.get_ident(),notify=Mock(),set_timer=Mock(),active_tab=tab)
    fake.set_download_status=MethodType(app.SpoffTUI.set_download_status,fake)
    cached=tmp_path/'song.opus'
    cached.write_bytes(b'audio')
    tracks=[{'id':'song','title':'Song','artist':'Artist'}]
    def complete_download(*args,**kwargs):
        kwargs['on_complete'](cached)
    def inline_thread(*args,**kwargs):
        return SimpleNamespace(start=kwargs['target'])
    with patch.object(app.threading,'Thread',side_effect=inline_thread), \
         patch.object(app,'get_cached_track_path',return_value=cached if already_cached else None), \
         patch.object(app,'cached_audio_matches_duration',return_value=True), \
         patch.object(app,'load_offline_index',return_value={}), \
         patch.object(app,'save_offline_index'), \
         patch.object(app,'download_track_to_cache',side_effect=complete_download) as download:
        app.SpoffTUI._bulk_download_playlist(fake,{'name':'Favorites','tracks':tracks})
    assert not fake._bulk_download_in_progress
    fake.render_tracks.assert_called_with([])
    assert download.call_count == (0 if already_cached else 1)
    expected="'Favorites' is already available offline (1 song)." if already_cached else "Saved 1/1 song from 'Favorites' offline."
    bar.update.assert_called_with(expected)
    fake.notify.assert_not_called()
    assert all(call.args[0]=='#notification-line' for call in fake.query_one.call_args_list)


def test_bulk_metadata_repair_keeps_the_resolved_recording(tmp_path):
    from types import MethodType
    fake,bar=fake_app(_thread_id=threading.get_ident(),notify=Mock(),set_timer=Mock())
    fake.set_download_status=MethodType(app.SpoffTUI.set_download_status,fake)
    cached=tmp_path/'song.opus'; cached.write_bytes(b'audio')
    index={'song':{'id':'song','title':'Old title','artist':'Artist',
                   'url':'https://open.spotify.com/track/original',
                   'resolved_url':'https://youtube.com/watch?v=correct0001',
                   'resolved_title':'Correct recording','source':'spotify','album':'Album'}}
    with patch.object(app.threading,'Thread',side_effect=lambda *a,**kw:SimpleNamespace(start=kw['target'])), \
         patch.object(app,'get_cached_track_path',return_value=cached), \
         patch.object(app,'cached_audio_matches_duration',return_value=True), \
         patch.object(app,'load_offline_index',return_value=index), \
         patch.object(app,'save_offline_index') as save:
        app.SpoffTUI._bulk_download_playlist(fake,{'name':'Favorites','tracks':[{'id':'song','title':'New title','artist':'Artist'}]})
    save.assert_called_once()
    updated=save.call_args.args[0]['song']
    assert updated['title']=='New title'
    assert updated['resolved_url']=='https://youtube.com/watch?v=correct0001'
    assert updated['url']=='https://open.spotify.com/track/original'
    assert updated['resolved_title']=='Correct recording'
    assert updated['source']=='spotify'
    assert updated['album']=='Album'
