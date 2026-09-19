import sys
import subprocess
import json
import socket
import os
import time
import logging
import threading
import signal
from pathlib import Path
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger("player")


def _preexec_deathsig():
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        # PR_SET_PDEATHSIG = 1, SIGKILL = 9
        libc.prctl(1, signal.SIGKILL, 0, 0, 0)
    except Exception:
        pass


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
        self.playback_finished_callback: Optional[Callable[[], None]] = None
        self._lock = threading.RLock()
        self._listener_stop_event: Optional[threading.Event] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._last_pos = 0.0
        self._duration = 0.0
        self._volume = max(0, min(100, int(initial_volume)))
        self.eq_engine: Optional[Any] = eq_engine

    def start_mpv(self):
        with self._lock:
            if self.process and self.process.poll() is None:
                if self._listener_thread is None or not self._listener_thread.is_alive():
                    stop_ev = threading.Event()
                    self._listener_stop_event = stop_ev
                    self._listener_thread = threading.Thread(target=self._ipc_listener, args=(stop_ev,), daemon=True)
                    self._listener_thread.start()
                return

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

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                preexec_fn=_preexec_deathsig
            )

            # Wait for socket to appear
            for _ in range(30):
                if os.path.exists(self.socket_path):
                    break
                time.sleep(0.05)

            stop_ev = threading.Event()
            self._listener_stop_event = stop_ev
            self._listener_thread = threading.Thread(target=self._ipc_listener, args=(stop_ev,), daemon=True)
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

    def _ipc_listener(self, stop_event: threading.Event):
        """Listens for MPV IPC events and property updates in the background."""
        sock_path = self.socket_path
        while not stop_event.is_set():
            if not os.path.exists(sock_path):
                time.sleep(0.05)
                continue
            proc = self.process
            if proc and proc.poll() is not None:
                time.sleep(0.2)
                continue
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
                                elif ev_type == "end-file":
                                    reason = event.get("reason")
                                    if reason == "error":
                                        logger.warning("MPV reported playback end due to stream error")
                                    if reason in ("eof", "error") and self.playback_finished_callback:
                                        self.playback_finished_callback()
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
        with self._lock:
            self.start_mpv()
            if self.eq_engine:
                self.apply_eq()
            ok = self._send_command(["loadfile", source_path_or_url, "replace"])
            if not ok:
                self.current_track = None
                self.is_paused = False
                return False
            self.current_track = track_meta
            self.is_paused = False
            self._last_pos = 0.0
            try:
                self._duration = float(track_meta.get("duration_ms") or 0) / 1000.0
            except (ValueError, TypeError):
                self._duration = 0.0
            self._send_command(["set_property", "pause", False])
            return True

    def pause(self):
        with self._lock:
            self.is_paused = True
            self._send_command(["set_property", "pause", True])

    def resume(self):
        with self._lock:
            self.is_paused = False
            self._send_command(["set_property", "pause", False])

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self._send_command(["set_property", "pause", self.is_paused])

    def seek(self, seconds_relative: float):
        self._send_command(["seek", seconds_relative, "relative"])
        self._last_pos = max(0.0, self._last_pos + seconds_relative)

    def seek_absolute(self, seconds_absolute: float):
        seconds_absolute = max(0.0, float(seconds_absolute))
        self._send_command(["seek", seconds_absolute, "absolute"])
        self._last_pos = seconds_absolute

    def set_volume(self, volume: int):
        self._volume = max(0, min(100, volume))
        self._send_command(["set_property", "volume", self._volume])

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
        with self._lock:
            if self._listener_stop_event is not None:
                self._listener_stop_event.set()
                self._listener_stop_event = None
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

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
