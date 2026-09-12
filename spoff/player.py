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

class MPVController:
    def __init__(self, socket_path: Optional[str] = None, initial_volume: int = 80):
        if socket_path is None:
            self.socket_path = f"/tmp/spoff_mpv_{os.getpid()}.sock"
        else:
            self.socket_path = socket_path
        self.process: Optional[subprocess.Popen] = None
        self.current_track: Optional[Dict[str, Any]] = None
        self.is_paused: bool = False
        self.playback_finished_callback: Optional[Callable[[], None]] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_listener = False
        self._last_pos = 0.0
        self._duration = 0.0
        self._volume = max(0, min(100, int(initial_volume)))

    def start_mpv(self):
        if self.process and self.process.poll() is None:
            if self._listener_thread is None or not self._listener_thread.is_alive():
                self._stop_listener = False
                self._listener_thread = threading.Thread(target=self._ipc_listener, daemon=True)
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
            "--gapless-audio=yes"
        ]
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env
        )

        # Wait for socket to appear
        for _ in range(30):
            if os.path.exists(self.socket_path):
                break
            time.sleep(0.05)

        self._stop_listener = False
        self._listener_thread = threading.Thread(target=self._ipc_listener, daemon=True)
        self._listener_thread.start()

    def _send_command(self, cmd: list) -> bool:
        if not os.path.exists(self.socket_path):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(self.socket_path)
                msg = json.dumps({"command": cmd}) + "\n"
                s.sendall(msg.encode("utf-8"))
                return True
        except Exception:
            return False

    def _ipc_listener(self):
        """Listens for MPV IPC events and property updates in the background."""
        while not self._stop_listener:
            if not os.path.exists(self.socket_path):
                time.sleep(0.05)
                continue
            if self.process and self.process.poll() is not None:
                time.sleep(0.2)
                continue
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(self.socket_path)
                    s.sendall(json.dumps({"command": ["observe_property", 1, "time-pos"]}).encode("utf-8") + b"\n")
                    s.sendall(json.dumps({"command": ["observe_property", 2, "duration"]}).encode("utf-8") + b"\n")
                    s.sendall(json.dumps({"command": ["observe_property", 3, "pause"]}).encode("utf-8") + b"\n")

                    buffer = ""
                    while not self._stop_listener:
                        data = s.recv(1024).decode("utf-8", errors="ignore")
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

    def load_and_play(self, source_path_or_url: str, track_meta: Dict[str, Any]) -> bool:
        self.start_mpv()
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

    def stop(self):
        self._stop_listener = True
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
        if self._listener_thread and self._listener_thread != threading.current_thread() and self._listener_thread.is_alive():
            try:
                self._listener_thread.join(timeout=0.5)
            except Exception:
                pass
            self._listener_thread = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
