import asyncio
import threading
from unittest.mock import Mock

from test_loading_status import LoadingHarness


def test_slow_backend_keeps_ui_responsive_and_rejects_stale_load():
    async def check():
        app = LoadingHarness(visualizer_enabled=False)
        async with app.run_test() as pilot:
            entered, release = threading.Event(), threading.Event()
            thread_ids = []
            def slow_load(*args):
                thread_ids.append(threading.get_ident())
                entered.set()
                assert release.wait(3)
                return True
            app._play_request_id = 1
            app.player.load_and_play.side_effect = slow_load
            app._commit_playback = Mock(return_value=True)
            task = asyncio.create_task(asyncio.to_thread(
                app._load_and_commit_playback, 1, 'spotify:track:ABC', {'id': 'ABC'}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                heartbeat = []
                app.set_timer(0.01, lambda: heartbeat.append(True))
                await pilot.pause(0.05)
                assert heartbeat
                assert len(thread_ids) == 1
                assert thread_ids[0] != threading.get_ident()
                app._play_request_id = 2
            finally:
                release.set()
                result = await task
            assert not result
            app._commit_playback.assert_not_called()
            app.player.stop.assert_called_once()
    asyncio.run(check())


def test_end_event_is_delivered_only_after_ui_commit():
    async def check():
        app = LoadingHarness(visualizer_enabled=False)
        async with app.run_test():
            app._play_request_id = 1
            order = []
            def load(*args):
                app.player.register_pending_callback.call_args.args[0]('eof')
                order.append('loaded')
                return True
            app.player.load_and_play.side_effect = load
            app._commit_playback = Mock(side_effect=lambda *args: order.append('committed') or True)
            app._handle_track_end = Mock(side_effect=lambda *args: order.append('ended'))
            assert await asyncio.to_thread(app._load_and_commit_playback, 1, 'audio', {'id': 'song'})
            assert order == ['loaded', 'committed', 'ended']
    asyncio.run(check())
