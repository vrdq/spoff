"""Exercise real socket ordering, failed commands, and playback cancellation."""
import json
import shutil
import socket
import subprocess
import threading
import wave
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from spoff.player import MPVController

SOCKET_CLASS = socket.socket


@contextmanager
def peer(controller, entry, *, reject=False, early_eof=False, missing_start=False):
    with patch("socket.socket", SOCKET_CLASS):
        client, server = socket.socketpair()
    connected = Mock(wraps=client)
    connected.connect = Mock()  # socketpair is already connected.
    def send(event):
        server.sendall((json.dumps(event) + "\n").encode())

    def answer():
        with server.makefile("rb") as incoming:
            command = json.loads(incoming.readline())
            assert command["command"][0] == "loadfile"
            if reject:
                send({"request_id": 1, "error": "invalid parameter"})
                return
            send({"event": "end-file", "playlist_entry_id": entry - 1, "reason": "eof"})
            if not missing_start:
                send({"event": "start-file", "playlist_entry_id": entry})
            if early_eof:
                send({"event": "end-file", "playlist_entry_id": entry, "reason": "eof"})
            send({"request_id": 1, "error": "success"})
            if missing_start:
                server.shutdown(socket.SHUT_RDWR)

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        with patch("spoff.player.socket.socket", return_value=connected), \
             patch.object(controller, "start_mpv"), \
             patch.object(controller, "_send_command", return_value=True):
            yield send, server
    finally:
        thread.join(timeout=2)
        server.close()


def test_rapid_loads_ignore_old_and_unidentified_eof():
    controller = MPVController()
    first, second = Mock(), Mock()
    finished = threading.Event()
    try:
        controller.register_pending_callback(first, 1)
        with peer(controller, 10):
            assert controller.load_and_play("first", {"id": "a"})
            old_thread = controller._playback_thread
            controller.register_pending_callback(lambda reason: (second(reason), finished.set()), 2)
            with peer(controller, 11) as (send, _):
                assert controller.load_and_play("second", {"id": "b"})
                send({"event": "end-file", "playlist_entry_id": 10, "reason": "eof"})
                send({"event": "end-file", "reason": "eof"})
                send({"event": "end-file", "playlist_entry_id": 11, "reason": "eof"})
                assert finished.wait(2)
                second.assert_called_once_with("eof")
            old_thread.join(2)
            assert not old_thread.is_alive()
        first.assert_not_called()
    finally:
        controller.stop()


@pytest.mark.parametrize("failure", ["reject", "missing_start"])
def test_failed_load_does_not_steal_next_callback(failure):
    controller = MPVController()
    failed, success = Mock(), Mock()
    finished = threading.Event()
    try:
        controller.register_pending_callback(failed, 1)
        with peer(controller, 10, **{failure: True}):
            assert not controller.load_and_play("broken", {"id": "a"})
        controller.register_pending_callback(lambda reason: (success(reason), finished.set()), 2)
        with peer(controller, 11, early_eof=True):
            assert controller.load_and_play("good", {"id": "b"})
            assert finished.wait(2)
        failed.assert_not_called()
        success.assert_called_once_with("eof")
    finally:
        controller.stop()


def test_disconnected_playback_reports_error_once():
    controller = MPVController()
    callback = Mock()
    finished = threading.Event()
    try:
        controller.register_pending_callback(lambda reason: (callback(reason), finished.set()))
        with peer(controller, 10) as (_, server):
            assert controller.load_and_play("source", {"id": "a"})
            server.shutdown(socket.SHUT_RDWR)
            assert finished.wait(2)
        callback.assert_called_once_with("error")
    finally:
        controller.stop()


def test_stop_closes_event_reader_without_advancing_queue():
    controller = MPVController()
    callback = Mock()
    controller.register_pending_callback(callback)
    with peer(controller, 10):
        assert controller.load_and_play("source", {"id": "a"})
        reader = controller._playback_thread
        controller.stop()
        assert not reader.is_alive()
        assert controller._playback_socket is None
    callback.assert_not_called()


@pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv is not installed")
def test_real_mpv_rapid_skips_eof_and_cleanup(tmp_path):
    """Use silent local WAV files and a null audio sink; no network or speakers."""
    paths = []
    for name, frames in (("long", 24000), ("short", 400)):
        path = tmp_path / f"{name}.wav"
        with wave.open(str(path), "wb") as output:
            output.setparams((1, 2, 8000, frames, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * frames)
        paths.append(path)
    controller = MPVController(socket_path=str(tmp_path / "mpv.sock"))
    popen = subprocess.Popen
    def silent_mpv(command, **kwargs):
        return popen(command + ["--no-config", "--ao=null"], **kwargs)
    callbacks = []
    finished = threading.Event()
    try:
        with patch("spoff.player.subprocess.Popen", side_effect=silent_mpv):
            errors = []
            controller.register_pending_callback(lambda reason: (errors.append(reason), finished.set()))
            assert controller.load_and_play(str(tmp_path / "missing.wav"), {"id": "missing"})
            assert finished.wait(5)
            assert errors == ["error"]
            finished.clear()
            for request in range(10):
                controller.register_pending_callback(lambda reason, i=request: callbacks.append((i, reason)), request)
                assert controller.load_and_play(str(paths[0]), {"id": str(request)})
            controller.register_pending_callback(lambda reason: (callbacks.append((10, reason)), finished.set()), 10)
            assert controller.load_and_play(str(paths[1]), {"id": "last"})
            assert finished.wait(5)
            assert callbacks == [(10, "eof")]
            process = controller.process
            listener = controller._listener_thread
    finally:
        controller.stop()
    assert process.poll() is not None
    assert not listener.is_alive()
    assert not (tmp_path / "mpv.sock").exists()
