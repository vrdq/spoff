import sys
import subprocess
import json
import socket
import os
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger("player")


def get_direct_hardware_audio_device() -> Optional[str]:
    """
    Detects physical ALSA or Bluetooth hardware output to bypass virtual filter-chain sinks
    (such as PipeWire samsung_akg_eq or easyeffects), avoiding double-equalization.
    Preserves Bluetooth headsets (bluez_output) over internal ALSA speakers.
    """
    try:
        res = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True,
            text=True,
            timeout=0.5
        )
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            has_filter_sink = any(
                not (parts[1].startswith("alsa_output.") or parts[1].startswith("bluez_output.") or parts[1].startswith("bluez_sink."))
                for parts in [line.split() for line in lines] if len(parts) >= 2
            )
            if has_filter_sink:
                # Prefer the user's default output when it is real hardware;
                # the first listed ALSA sink may be the wrong device (HDMI vs DAC).
                try:
                    default = subprocess.run(
                        ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=0.5
                    ).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    default = ""
                if default.startswith(("alsa_output.", "bluez_output.", "bluez_sink.")):
                    return f"pulse/{default}"
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2 and (parts[1].startswith("bluez_output.") or parts[1].startswith("bluez_sink.")):
                        return f"pulse/{parts[1]}"
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith("alsa_output."):
                        return f"pulse/{parts[1]}"
    except Exception:
        pass
    return None


class MPVController:
    def __init__(self, socket_path: Optional[str] = None, initial_volume: int = 80, eq_engine: Optional[Any] = None):
        if socket_path is None:
            self.socket_path = f"/tmp/spoff_mpv_{os.getpid()}.sock"
        else:
            self.socket_path = socket_path
        self.process: Optional[subprocess.Popen] = None
        self.current_track: Optional[Dict[str, Any]] = None
        self.is_paused: bool = False
        self._playback_finished_callback: Optional[Callable] = None
        self._next_callback: Optional[Callable] = None
        self._load_lock = threading.Lock()
        self._playback_socket: Optional[socket.socket] = None
        self._playback_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._listener_stop_event: Optional[threading.Event] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._last_pos = 0.0
        self._duration = 0.0
        self._volume = max(0, min(100, int(initial_volume)))
        self.eq_engine: Optional[Any] = eq_engine

    @property
    def playback_finished_callback(self) -> Optional[Callable]:
        with self._lock:
            return self._playback_finished_callback

    @playback_finished_callback.setter
    def playback_finished_callback(self, cb: Optional[Callable]):
        with self._lock:
            self._playback_finished_callback = cb
            self._next_callback = cb

    def register_pending_callback(self, cb: Callable, request_id: Optional[Any] = None):
        with self._lock:
            self._playback_finished_callback = cb
            self._next_callback = cb

    def start_mpv(self):
        with self._lock:
            if self.process and self.process.poll() is None:
                if self._listener_thread is None or not self._listener_thread.is_alive():
                    if self._listener_stop_event is not None:
                        self._listener_stop_event.set()
                    stop_ev = threading.Event()
                    self._listener_stop_event = stop_ev
                    self._listener_thread = threading.Thread(target=self._ipc_listener, args=(stop_ev, self.process), daemon=True)
                    self._listener_thread.start()
                return

            if self._listener_stop_event is not None:
                self._listener_stop_event.set()

            if os.path.exists(self.socket_path):
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass

            env = os.environ.copy()
            venv_bin = str(Path(sys.executable).parent)
            if venv_bin not in env.get("PATH", ""):
                env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

            cmd = [
                "mpv",
                "--idle=yes",
                f"--input-ipc-server={self.socket_path}",
                "--no-video",
                f"--volume={self._volume}",
                "--really-quiet",
                "--gapless-audio=yes",
                "--audio-client-name=spoff",
                "--title=spoff",
                "--force-media-title=spoff",
            ]
            if self.eq_engine:
                af_str = self.eq_engine.to_ffmpeg_af()
                if af_str:
                    cmd.append(f"--af={af_str}")
                direct_dev = get_direct_hardware_audio_device()
                if direct_dev:
                    cmd.append(f"--audio-device={direct_dev}")

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )

            # Wait for socket to appear
            for _ in range(30):
                if os.path.exists(self.socket_path):
                    break
                time.sleep(0.05)

            stop_ev = threading.Event()
            self._listener_stop_event = stop_ev
            self._listener_thread = threading.Thread(target=self._ipc_listener, args=(stop_ev, self.process), daemon=True)
            self._listener_thread.start()

            if self.eq_engine:
                self.apply_eq()

    def _send_command(self, cmd: list, timeout: float = 1.0) -> bool:
        if not os.path.exists(self.socket_path):
            return False
        request_id = 1
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(self.socket_path)
                msg = json.dumps({"command": cmd, "request_id": request_id}) + "\n"
                sock.sendall(msg.encode("utf-8"))
                if cmd and cmd[0] == "quit":
                    return True
                with sock.makefile("rb") as stream:
                    while True:
                        line = stream.readline(1024 * 1024)
                        if not line:
                            return False
                        try:
                            reply = json.loads(line.decode("utf-8", errors="ignore"))
                        except Exception:
                            continue
                        if reply.get("request_id") == request_id:
                            if reply.get("error") != "success":
                                logger.error("mpv command %r failed: %r", cmd[0] if cmd else "", reply)
                                return False
                            return True
        except (OSError, ValueError):
            if cmd and cmd[0] == "quit":
                return True
            return False

    def _ipc_listener(self, stop_event: threading.Event, proc: Optional[subprocess.Popen] = None):
        """Listens for MPV IPC events and property updates in the background."""
        sock_path = self.socket_path
        while not stop_event.is_set():
            if not os.path.exists(sock_path):
                time.sleep(0.05)
                continue
            if proc is not None and (proc is not self.process or proc.poll() is not None):
                return
            if self.process and self.process.poll() is not None:
                return
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(sock_path)
                    s.sendall(json.dumps({"command": ["observe_property", 1, "time-pos"]}).encode("utf-8") + b"\n")
                    s.sendall(json.dumps({"command": ["observe_property", 2, "duration"]}).encode("utf-8") + b"\n")
                    s.sendall(json.dumps({"command": ["observe_property", 3, "pause"]}).encode("utf-8") + b"\n")

                    s.settimeout(0.5)
                    buffer = ""
                    while not stop_event.is_set():
                        try:
                            data = s.recv(1024).decode("utf-8", errors="ignore")
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        buffer += data
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if not line.strip():
                                continue
                            try:
                                event = json.loads(line)
                                ev_type = event.get("event")
                                if ev_type == "property-change":
                                    name = event.get("name")
                                    val = event.get("data")
                                    if name == "time-pos" and val is not None:
                                        self._last_pos = float(val)
                                    elif name == "duration" and val is not None:
                                        self._duration = float(val)
                                    elif name == "pause" and val is not None:
                                        self.is_paused = bool(val)
                            except Exception:
                                pass
            except Exception:
                time.sleep(0.1)

    def apply_eq(self) -> bool:
        """Applies active Parametric EQ filter graph to running MPV stream in real-time."""
        if not self.eq_engine:
            return False
        af_str = self.eq_engine.to_ffmpeg_af()
        return self._send_command(["set_property", "af", af_str])

    def toggle_eq_bypass(self) -> bool:
        """Seamlessly toggles EQ bypass without audio interruption (A-B testing)."""
        if not self.eq_engine:
            return False
        bypassed = self.eq_engine.toggle_bypass()
        self.apply_eq()
        return bypassed

    def set_eq_bypassed(self, bypassed: bool) -> bool:
        """Sets EQ bypass state and updates MPV immediately."""
        if not self.eq_engine:
            return False
        self.eq_engine.set_bypassed(bypassed)
        self.apply_eq()
        return self.eq_engine.bypassed

    def set_eq_engine(self, engine: Any) -> None:
        """Sets active EQ engine and updates MPV."""
        self.eq_engine = engine
        self.apply_eq()

    def load_and_play(self, source_path_or_url: str, track_meta: Dict[str, Any]) -> bool:
        # A load owns its IPC connection from command submission through EOF.
        # This subscribes before loading and avoids guessing ownership from a
        # different listener's delayed/missed start-file events.
        with self._load_lock:
            with self._lock:
                callback = self._next_callback
                self._next_callback = None
            try:
                self.start_mpv()
                if self.eq_engine:
                    self.apply_eq()
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            except (OSError, RuntimeError):
                logger.exception("Could not start mpv or open its IPC socket")
                self.stop()
                return False
            stream = None
            with self._lock:
                self._close_playback_socket()
                self._playback_socket = sock
            try:
                sock.settimeout(2.0)
                sock.connect(self.socket_path)
                stream = sock.makefile("rb")
                sock.sendall((json.dumps({
                    "command": ["loadfile", source_path_or_url, "replace"],
                    "request_id": 1,
                }) + "\n").encode())
                acknowledged = False
                entry_id = None
                deferred = []
                deadline = time.monotonic() + 2.0
                while not acknowledged or entry_id is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("mpv load ownership timed out")
                    sock.settimeout(remaining)
                    line = stream.readline(1024 * 1024)
                    if not line:
                        raise OSError("mpv disconnected during load")
                    event = json.loads(line)
                    if event.get("request_id") == 1:
                        if event.get("error") != "success":
                            raise OSError(f"mpv rejected load: {event.get('error')}")
                        acknowledged = True
                    elif event.get("event") == "start-file":
                        entry_id = event.get("playlist_entry_id")
                    else:
                        deferred.append(event)
                sock.settimeout(None)
                with self._lock:
                    if self._playback_socket is not sock:
                        raise OSError("playback canceled during load")
                    self.current_track = track_meta
                    self.is_paused = False
                    self._last_pos = 0.0
                    try:
                        self._duration = float(track_meta.get("duration_ms") or 0) / 1000.0
                    except (ValueError, TypeError):
                        self._duration = 0.0
                    playback_thread = threading.Thread(
                        target=self._watch_playback,
                        args=(sock, stream, entry_id, callback, deferred), daemon=True,
                    )
                    playback_thread.start()
                    self._playback_thread = playback_thread
                self._send_command(["set_property", "pause", False])
                return True
            except (OSError, ValueError, RuntimeError) as exc:
                logger.warning("Could not establish mpv playback ownership: %s", exc)
                # An unacknowledged command might still execute. Retire the
                # process so it cannot produce a late start for the next load.
                self.stop()
                if stream is not None:
                    stream.close()
                sock.close()
                return False

    def _close_playback_socket(self):
        sock, self._playback_socket = self._playback_socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _watch_playback(self, sock, stream, entry_id, callback, deferred):
        reason = "error"
        owned = False
        try:
            while True:
                if deferred:
                    event = deferred.pop(0)
                else:
                    line = stream.readline(1024 * 1024)
                    if not line:
                        break
                    event = json.loads(line)
                if event.get("event") == "end-file" and event.get("playlist_entry_id") == entry_id:
                    reason = event.get("reason")
                    break
        except (OSError, ValueError):
            logger.warning("mpv playback event connection lost")
        finally:
            try:
                stream.close()
            except Exception:
                pass
            with self._lock:
                owned = self._playback_socket is sock
                if owned:
                    self._close_playback_socket()
            try:
                sock.close()
            except Exception:
                pass
        if owned and callback is not None and reason in ("eof", "error"):
            try:
                callback(reason)
            except Exception:
                logger.exception("Playback completion callback failed")

    def pause(self):
        with self._lock:
            self.is_paused = True
            self._send_command(["set_property", "pause", True])

    def resume(self):
        with self._lock:
            self.is_paused = False
            self._send_command(["set_property", "pause", False])

    def toggle_pause(self):
        with self._lock:
            self.is_paused = not self.is_paused
            self._send_command(["set_property", "pause", self.is_paused])

    def seek(self, seconds_relative: float):
        with self._lock:
            self._send_command(["seek", seconds_relative, "relative"])
            self._last_pos = max(0.0, self._last_pos + seconds_relative)

    def seek_absolute(self, seconds_absolute: float):
        with self._lock:
            seconds_absolute = max(0.0, float(seconds_absolute))
            self._send_command(["seek", seconds_absolute, "absolute"])
            self._last_pos = seconds_absolute

    def set_volume(self, volume: int):
        with self._lock:
            self._volume = max(0, min(100, volume))
            self._send_command(["set_property", "volume", self._volume])

    def get_volume(self) -> int:
        with self._lock:
            return int(self._volume)

    def get_progress(self) -> tuple[float, float]:
        if not self.current_track:
            return 0.0, 0.0
        return self._last_pos, self._duration

    def get_position(self) -> float:
        """Returns the current playback position in seconds."""
        return float(self._last_pos)

    def get_duration(self) -> float:
        """Returns the current track duration in seconds."""
        return float(self._duration)

    def stop(self):
        listener_to_join = None
        playback_to_join = None
        with self._lock:
            if self._listener_stop_event is not None:
                self._listener_stop_event.set()
                self._listener_stop_event = None
            self._next_callback = None
            self._playback_finished_callback = None
            self._close_playback_socket()
            playback_to_join = self._playback_thread
            self._playback_thread = None
            self.current_track = None
            self._last_pos = 0.0
            self._duration = 0.0
            self.is_paused = False
            proc = self.process
            if proc is not None:
                self.process = None
                try:
                    self._send_command(["quit"])
                    proc.terminate()
                    try:
                        proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1.0)
                except Exception:
                    pass
            listener_to_join = self._listener_thread
            self._listener_thread = None
            if os.path.exists(self.socket_path):
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass
        if listener_to_join and listener_to_join != threading.current_thread() and listener_to_join.is_alive():
            try:
                listener_to_join.join(timeout=0.5)
            except Exception:
                pass
        if playback_to_join and playback_to_join != threading.current_thread() and playback_to_join.is_alive():
            try:
                playback_to_join.join(timeout=0.5)
            except Exception:
                pass

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
