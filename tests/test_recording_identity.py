"""Playback must not silently replace the selected recording."""
import json
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from spoff import streamer


@pytest.fixture(autouse=True)
def fresh_stream_cache():
    with patch.object(streamer, '_stream_cache', {}):
        yield


def song(title, identifier='correct0001', artist='Frou Frou', duration='4:19'):
    return dict(title=title, videoId=identifier, artists=[{'name': artist}], duration=duration)


def test_spotify_rejects_remix_and_uses_matching_demo():
    ytm = Mock()
    ytm.search.return_value = [song('a new kind of love (gamvae remix)', 'KyeGAfiQWkQ', duration='2:10'),
                               song('A New Kind Of Love (Demo)')]
    with patch('spoff.ytmusic.get_ytmusic_client', return_value=ytm), patch.object(streamer.yt_dlp, 'YoutubeDL') as cls:
        ydl = cls.return_value.__enter__.return_value
        ydl.extract_info.return_value = {'url': 'https://audio.invalid/correct', 'duration': 259}
        result = streamer.search_and_resolve_stream('A New Kind Of Love - Demo', 'Frou Frou, Imogen Heap, Guy Sigsworth',
                    'https://open.spotify.com/track/3fuyYaLhZ2RoP9eWpvfP1H', expected_duration_ms=259071)
        assert result['stream_url'].endswith('/correct')
        assert ydl.extract_info.call_args.args[0].endswith('correct0001')


@pytest.mark.parametrize('url', ['https://music.youtube.com/watch?v=correct0001',
                                 'https://www.youtube.com/watch?v=correct0001'])
def test_exact_youtube_failure_does_not_search_for_substitute(url):
    with patch('spoff.ytmusic.get_ytmusic_client') as client, patch.object(streamer.yt_dlp, 'YoutubeDL') as cls:
        ydl = cls.return_value.__enter__.return_value
        ydl.extract_info.side_effect = RuntimeError('unavailable')
        assert streamer.search_and_resolve_stream('Song', 'Artist', url) is None
        ydl.extract_info.assert_called_once_with(url, download=False)
        client.assert_not_called()


def test_explicit_remix_is_preserved():
    url = 'https://music.youtube.com/watch?v=KyeGAfiQWkQ'
    with patch('spoff.ytmusic.get_ytmusic_client') as client, patch.object(streamer.yt_dlp, 'YoutubeDL') as cls:
        cls.return_value.__enter__.return_value.extract_info.return_value = {'url': 'https://audio.invalid/remix', 'duration': 130}
        result = streamer.search_and_resolve_stream('a new kind of love (gamvae remix)', 'Frou Frou', url)
        assert result['stream_url'].endswith('/remix')
        client.assert_not_called()


@pytest.mark.parametrize('candidate', [song('Different Song'), song('A New Kind Of Love - Demo', duration='2:10'),
                                      song('A New Kind Of Love - Demo', artist='Cover Band'),
                                      song('A New Kind Of Love (Live)'), song('A New Kind Of Love (Demo)', artist='')])
def test_rejects_wrong_recordings(candidate):
    assert not streamer._matches_recording(candidate, 'A New Kind Of Love - Demo', 'Frou Frou', 259.071)


def test_youtube_search_fallback_checks_all_results():
    with patch('spoff.ytmusic.get_ytmusic_client', return_value=None), patch.object(streamer.yt_dlp, 'YoutubeDL') as cls:
        cls.return_value.__enter__.return_value.extract_info.return_value = {'entries': [
            {'title': 'Frou Frou - A New Kind Of Love (Remix)', 'duration': 259, 'url': 'wrong'},
            {'title': 'Frou Frou - A New Kind Of Love (Demo) [Official Audio]', 'duration': 259, 'url': 'right'}]}
        result = streamer.search_and_resolve_stream('A New Kind Of Love - Demo', 'Frou Frou', expected_duration_ms=259071)
        assert result['stream_url'] == 'right'


def test_unverified_fallback_is_rejected():
    with patch('spoff.ytmusic.get_ytmusic_client', return_value=None), patch.object(streamer.yt_dlp, 'YoutubeDL') as cls:
        cls.return_value.__enter__.return_value.extract_info.return_value = {'entries': [{'url': 'wrong'}]}
        assert streamer.search_and_resolve_stream('Song', 'Artist') is None


def test_existing_bad_cache_duration_is_rejected():
    with patch.object(streamer.subprocess, 'run', return_value=Mock(stdout=json.dumps({'format': {'duration': '129.214626'}}))):
        assert not streamer.cached_audio_matches_duration(Path('song.m4a'), 259071)
        assert streamer.cached_audio_matches_duration(Path('song.m4a'), 130000)


def test_download_resolves_again_when_cached_duration_is_wrong(tmp_path):
    cached = tmp_path / 'song.m4a'
    cached.write_bytes(b'existing')
    with patch.object(streamer, 'get_cached_track_path', return_value=cached), \
         patch.object(streamer, 'cached_audio_matches_duration', return_value=False), \
         patch.object(streamer, '_run_download_process', return_value=cached) as download:
        streamer.download_track_to_cache('song', 'Title', 'Artist', blocking=True, track_meta={'duration_ms':259071})
        download.assert_called_once()


def test_bare_youtube_id_keeps_selected_recording():
    with patch('spoff.ytmusic.get_ytmusic_client') as client, patch.object(streamer.yt_dlp, 'YoutubeDL') as cls:
        ydl = cls.return_value.__enter__.return_value
        ydl.extract_info.return_value = {'url': 'audio'}
        assert streamer.search_and_resolve_stream('Song', 'Artist', 'correct0001')
        ydl.extract_info.assert_called_once_with('https://www.youtube.com/watch?v=correct0001', download=False)
        client.assert_not_called()


def test_youtube_url_with_reordered_query_parameters():
    from spoff.search import resolve_direct_track_url
    ytm = Mock()
    ytm.get_song.return_value = {'videoDetails': {'videoId':'correct0001','title':'Selected recording','author':'Artist','lengthSeconds':'259'}}
    with patch('spoff.ytmusic.get_ytmusic_client', return_value=ytm):
        result = resolve_direct_track_url('https://music.youtube.com/watch?si=share&v=correct0001&list=PLother')
        assert result['id'] == 'correct0001'
        ytm.get_song.assert_called_once_with('correct0001')


def test_sync_keeps_demo_and_remix_separate():
    from spoff import auth
    assert not auth._tracks_match({'id':'a', 'title':'Song (Demo)', 'artist':'Artist'},
                                  {'id':'b', 'title':'Song (Remix)', 'artist':'Artist'})
    assert not auth._tracks_match({'id':'a', 'title':'Song', 'artist':'Ann'},
                                  {'id':'b', 'title':'Song', 'artist':'Anne'})


def test_spotify_mapping_does_not_take_first_wrong_recording():
    from spoff import auth
    items = [{'id':'wrong','uri':'spotify:track:wrong','name':'Song (Remix)','artists':[{'name':'Artist'}]},
             {'id':'right','uri':'spotify:track:right','name':'Song (Demo)','artists':[{'name':'Artist'}]}]
    with patch.object(auth,'spotify_api_request',return_value=(True,{'tracks':{'items':items}},'')):
        result = auth.search_spotify_track('Song (Demo)','Artist',token='test')
        assert result['id'] == 'right'


def test_missing_mpv_is_reported_as_playback_failure():
    from spoff.player import MPVController
    player = MPVController()
    with patch.object(player,'start_mpv',side_effect=FileNotFoundError('mpv')), patch.object(player,'stop') as stop:
        assert player.load_and_play('audio', {'id':'test'}) is False
        stop.assert_called_once()


def test_old_download_timer_does_not_clear_new_progress():
    import threading
    from types import SimpleNamespace
    from spoff.app import SpoffTUI
    timers = []
    fake = SimpleNamespace(_thread_id=threading.get_ident(), query_one=Mock(), notify_user=Mock(),
                           set_timer=lambda delay, fn: timers.append(fn))
    SpoffTUI.set_download_status(fake,'Done',clear_after=2)
    SpoffTUI.set_download_status(fake,'Downloading')
    timers[0]()
    assert fake._download_statuses['download'][0] == 'Downloading'


def test_local_unlike_during_remote_fetch_is_preserved():
    from types import SimpleNamespace
    from spoff import app
    old = [{'id':'song','title':'Song','artist':'Artist','source':'spotify'}]
    fake = SimpleNamespace(_submit_spotify_job=lambda fn:fn())
    with patch.object(app,'get_valid_token',return_value='test'), \
         patch.object(app,'load_liked_songs',side_effect=[old,[]]), \
         patch.object(app,'fetch_liked_songs',return_value=old), \
         patch.object(app,'save_liked_songs') as save:
        app.SpoffTUI._sync_liked_from_spotify_bg(fake,force=True)
        save.assert_not_called()


def test_failed_direct_link_does_not_become_name_search():
    from types import SimpleNamespace
    from spoff import app
    fake=SimpleNamespace(_search_request_id=1, active_tab='search', advanced_mode=False,
                         call_from_thread=lambda fn:fn(), render_tracks=Mock(), query_one=Mock(), notify_user=Mock())
    with patch.object(app,'resolve_direct_track_url',return_value=None), \
         patch.object(app,'live_search_tracks') as ytm, patch.object(app,'search_spotify_tracks') as spotify:
        app.SpoffTUI._search_worker.__wrapped__(fake,'https://music.youtube.com/watch?v=correct0001',1,'spotify','Spotify')
        ytm.assert_not_called()
        spotify.assert_not_called()
        assert fake.search_results == []


def test_offline_registration_preserves_source_and_resolved_identity(tmp_path):
    from spoff import storage
    media=tmp_path/'song.m4a'; media.write_bytes(b'audio')
    index={}
    with patch.object(storage,'load_offline_index',return_value=index), patch.object(storage,'save_offline_index'):
        storage.register_cached_track('song',{'title':'Song','url':'https://example.invalid/song',
                        'resolved_url':'https://youtube.com/watch?v=correct0001','source':'spotify'},media)
        storage.register_cached_track('song',{'title':'Song'},media)
    assert index['song']['url'] == 'https://example.invalid/song'
    assert index['song']['resolved_url'].endswith('correct0001')


def test_replacement_in_another_format_does_not_leave_bad_cache_preferred(tmp_path):
    from spoff import storage
    old = tmp_path/'song.m4a'; old.write_bytes(b'wrong')
    with patch.object(storage,'_get_cache_dir',return_value=tmp_path), \
         patch.object(streamer,'get_cached_track_path',return_value=old), \
         patch.object(streamer,'cached_audio_matches_duration',side_effect=lambda p,d:p != old), \
         patch.object(streamer,'search_and_resolve_stream',return_value={'stream_url':'audio','webpage_url':'https://youtube.com/watch?v=correct0001'}), \
         patch.object(streamer,'register_cached_track'), patch.object(streamer.subprocess,'run'), \
         patch.object(streamer.yt_dlp,'YoutubeDL') as cls:
        def make_downloader(opts):
            downloader=Mock()
            def download(urls):
                Path(opts['outtmpl'].replace('%(ext)s','webm')).write_bytes(b'correct')
                return 0
            downloader.download.side_effect=download
            return Mock(__enter__=Mock(return_value=downloader),__exit__=Mock(return_value=False))
        cls.side_effect=make_downloader
        result=streamer._run_download_process('song','Song','Artist',track_meta={'duration_ms':259000})
        assert result.read_bytes() == b'correct'
        assert storage.get_cached_track_path('song') == result
        assert not (tmp_path / 'song.m4a').exists()
        assert not list(tmp_path.glob('.replaced-*'))


def test_lyrics_search_rejects_unrelated_first_result(tmp_path):
    from spoff import lyrics
    responses=[{},[{'trackName':'Wrong Song','artistName':'Artist','duration':259,'plainLyrics':'wrong'},
                  {'trackName':'Song (Demo)','artistName':'Artist','duration':259,'plainLyrics':'correct'}]]
    def response(*args,**kwargs):
        obj=Mock(status=200)
        obj.read.return_value=json.dumps(responses.pop(0)).encode()
        return Mock(__enter__=Mock(return_value=obj),__exit__=Mock(return_value=False))
    with patch.object(lyrics,'LYRICS_DIR',tmp_path),patch.object(lyrics.urllib.request,'urlopen',side_effect=response):
        assert lyrics.fetch_lyrics('Song (Demo)','Artist',259000)['plain']=='correct'


def test_cava_thread_start_failure_cleans_process_and_config():
    from spoff.visualizer import CavaVisualizer
    vis=CavaVisualizer()
    vis._has_cava=True
    vis.style='bars'
    with patch('spoff.visualizer.subprocess.Popen') as popen,patch('spoff.visualizer.threading.Thread') as thread:
        thread.return_value.start.side_effect=RuntimeError('cannot start thread')
        vis.start()
        popen.return_value.terminate.assert_called_once()
        assert vis.proc is None
        assert vis.conf_path is None


def test_eleven_character_title_is_not_treated_as_a_video_id():
    ytm=Mock()
    ytm.search.return_value=[song('SummertimeX')]
    with patch('spoff.ytmusic.get_ytmusic_client',return_value=ytm),patch.object(streamer.yt_dlp,'YoutubeDL') as cls:
        ydl=cls.return_value.__enter__.return_value
        ydl.extract_info.return_value={'url':'audio'}
        assert streamer.search_and_resolve_stream('SummertimeX','Frou Frou')
        assert ydl.extract_info.call_args.args[0].endswith('correct0001')


def test_malformed_direct_url_fails_without_crashing():
    assert streamer.search_and_resolve_stream('Song','Artist','https://[') is None


def test_mpv_watcher_start_failure_closes_audio_process_and_socket(tmp_path):
    from spoff import player as player_module
    player = player_module.MPVController(socket_path=str(tmp_path / 'mpv.sock'))
    proc = Mock()
    player.process = proc
    sock = Mock()
    stream = sock.makefile.return_value
    stream.readline.side_effect = [
        b'{"request_id":1,"error":"success"}\n',
        b'{"event":"start-file","playlist_entry_id":1}\n',
    ]
    with patch.object(player, 'start_mpv'), \
         patch.object(player, '_send_command', return_value=True), \
         patch.object(player_module.socket, 'socket', return_value=sock), \
         patch.object(player_module.threading, 'Thread') as thread:
        thread.return_value.start.side_effect = RuntimeError('cannot start thread')
        assert not player.load_and_play('audio', {'id': 'song'})
    assert player.current_track is None
    assert player.process is None
    assert player._playback_socket is None
    proc.terminate.assert_called_once()
    stream.close.assert_called()
    sock.close.assert_called()
    thread.return_value.join.assert_not_called()


@pytest.mark.parametrize('title,duration,accepted', [
    ('Crave You (feat. Giselle)', '3:55', True),
    ('Crave You (feat. Giselle)', '4:19', False),
    ('Crave You (Adventure Club Remix)', '3:57', False),
    ('Crave You (Live) (feat. Giselle)', '3:55', False),
    ('Crave You (feat. Someone Else)', '3:55', False),
])
def test_crave_you_feature_credit_keeps_recording_checks(title, duration, accepted):
    candidate = song(title, artist='Flight Facilities', duration=duration)
    assert streamer._matches_recording(candidate, 'Crave You',
                                      'Flight Facilities, Giselle', 234.776) is accepted


def test_feature_credit_can_move_from_requested_title_to_candidate_artists():
    candidate = {'title': 'Crave You', 'artists': [{'name': 'Flight Facilities'},
                 {'name': 'Giselle'}], 'duration': 235}
    assert streamer._matches_recording(candidate, 'Crave You (feat. Giselle)',
                                      'Flight Facilities', 234.776)
