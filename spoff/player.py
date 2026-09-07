import sys
import subprocess
import json
import socket
import os
import time
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any

class MPVController:
    def __init__(self, socket_path: Optional[str] = None):
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
        self._volume = 80

    def start_mpv(self):
        if self.process and self.process.poll() is None:
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
            "--volume=80",
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

    def _send_command(self, cmd: list) -> Optional[Any]:
        if not os.path.exists(self.socket_path):
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(self.socket_path)
                msg = json.dumps({"command": cmd}) + "\n"
                s.sendall(msg.encode("utf-8"))
                resp_bytes = s.recv(4096)
                if resp_bytes:
                    res = json.loads(resp_bytes.decode("utf-8").split("\n")[0])
                    return res.get("data")
        except Exception:
            return None

    def _ipc_listener(self):
        """Listens for MPV IPC events like playback finish or position change."""
        while not self._stop_listener:
            if not os.path.exists(self.socket_path):
                time.sleep(0.1)
                continue
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(self.socket_path)
                    buffer = ""
                    while not self._stop_listener:
                        data = s.recv(1024).decode("utf-8")
                        if not data:
                            break
                        buffer += data
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if not line.strip():
                                continue
                            try:
                                event = json.loads(line)
                                if event.get("event") == "end-file":
                                    reason = event.get("reason")
                                    if reason == "eof" and self.playback_finished_callback:
                                        self.playback_finished_callback()
                            except Exception:
                                pass
            except Exception:
                time.sleep(0.2)

    def load_and_play(self, source_path_or_url: str, track_meta: Dict[str, Any]):
        self.start_mpv()
        self.current_track = track_meta
        self.is_paused = False
        self._last_pos = 0.0
        self._duration = float(track_meta.get("duration_ms", 0)) / 1000.0
        self._send_command(["loadfile", source_path_or_url, "replace"])
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
        pos = self._send_command(["get_property", "time-pos"])
        dur = self._send_command(["get_property", "duration"])
        if pos is not None:
            try:
                self._last_pos = float(pos)
            except (ValueError, TypeError):
                pass
        if dur is not None:
            try:
                self._duration = float(dur)
            except (ValueError, TypeError):
                pass
        return self._last_pos, self._duration

    def stop(self):
        self._stop_listener = True
        self.current_track = None
        self._last_pos = 0.0
        self._duration = 0.0
        self.is_paused = False
        if self.process:
            try:
                self._send_command(["quit"])
                self.process.terminate()
            except Exception:
                pass
            self.process = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
