"""Real Spotify audio for Premium accounts, through librespot.

librespot runs as a private Spotify Connect device ("Spoff on <host>") and
writes raw PCM to stdout (its `pipe` backend). A relay thread feeds that into
a second mpv that only plays this stream, so the EQ and loudness filters still
apply. Spoff drives playback through the Spotify Web API (play, pause, seek)
and learns what librespot is doing from its `--onevent` hook.

Nothing here runs unless the user turns Spotify audio on, and every failure
leaves the caller free to fall back to YouTube.
"""
import json
import logging
import os
import shutil
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from . import auth
    from .storage import DATA_DIR
except ImportError:
    import auth  # type: ignore
    from storage import DATA_DIR  # type: ignore

logger = logging.getLogger("spotify_audio")

SAMPLE_RATE = 44100  # librespot always decodes to 44.1 kHz stereo
EVENT_SCRIPT = """#!/bin/sh
# Written by Spoff: forwards librespot player events to Spoff.
printf '%s\\t%s\\t%s\\t%s\\n' "$PLAYER_EVENT" "$TRACK_ID" "$POSITION_MS" "$DURATION_MS" > "$SPOFF_LIBRESPOT_EVENTS"
"""


def librespot_path() -> Optional[str]:
    return shutil.which("librespot")


class SpotifyAudio:
    """Owns librespot, the PCM relay, and the mpv that plays Spotify audio."""

    def __init__(self, filters: Callable[[], str], volume: int = 80, data_dir: Optional[Path] = None):
        self._filters = filters
        self._volume = max(0, min(100, int(volume)))
        self.dir = Path(data_dir or DATA_DIR) / "librespot"
        self.device_name = f"Spoff on {socket.gethostname()}"
        self.device_id: Optional[str] = None
        self.login_url: Optional[str] = None
        self.last_error = ""
        # Spotify refused decryption keys ("audio key error"): nothing will play
        # this session, so callers should stop routing songs here.
        self.keys_refused = False

        self._librespot: Optional[subprocess.Popen] = None
        self._sink: Optional[subprocess.Popen] = None
        self._sink_socket = str(self.dir / "sink.sock")
        self._lock = threading.RLock()
        self._events = threading.Condition()
        self._last_event: Dict[str, Any] = {}

        # Playback state read by the player facade.
        self.track_id: Optional[str] = None
        self.is_paused = False
        self.duration = 0.0
        self._anchor_pos = 0.0
        self._anchor_time = time.monotonic()
        self._on_end: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------ setup

    @property
    def credentials_file(self) -> Path:
        return self.dir / "credentials.json"

    def is_logged_in(self) -> bool:
        return self.credentials_file.is_file()

    def running(self) -> bool:
        return bool(self._librespot and self._librespot.poll() is None and self.device_id
                    and not self.keys_refused)

    def start(self, on_login_url: Optional[Callable[[str], None]] = None, timeout: float = 180.0) -> bool:
        """Starts everything and waits until the device is visible to the Web API.

        If librespot has no saved login, it runs its browser sign-in first and
        on_login_url receives the URL to open.
        """
        with self._lock:
            if self.running():
                return True
            binary = librespot_path()
            if not binary:
                self.last_error = "librespot isn't installed"
                return False
            self.dir.mkdir(parents=True, exist_ok=True)
            events = self._make_event_fifo()
            script = self._write_event_script()
            self._start_sink()

            cmd = [
                binary,
                "--name", self.device_name,
                "--device-type", "computer",
                "--backend", "pipe",           # raw PCM on stdout, relayed to mpv
                "--format", "S16",
                "--bitrate", "320",
                "--system-cache", str(self.dir),
                "--disable-audio-cache",
                "--disable-discovery",
                "--autoplay", "off",           # Spoff owns the queue
                "--volume-ctrl", "fixed",      # volume is applied in mpv
                "--initial-volume", "100",
                "--onevent", str(script),
                "--oauth-port", "5588",
            ]
            if not self.is_logged_in():
                cmd.append("--enable-oauth")
            env = dict(os.environ, SPOFF_LIBRESPOT_EVENTS=str(events))
            self._librespot = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
            )
            threading.Thread(target=self._relay_audio, args=(self._librespot,), daemon=True).start()
            threading.Thread(target=self._read_log, args=(self._librespot, on_login_url), daemon=True).start()
            threading.Thread(target=self._read_events, args=(events,), daemon=True).start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._librespot is None or self._librespot.poll() is not None:
                self.last_error = self.last_error or "librespot stopped while starting"
                return False
            device = self._find_device()
            if device:
                self.device_id = device
                return True
            time.sleep(1.0)
        self.last_error = "Spotify never saw the Spoff device"
        self.shutdown()
        return False

    def _make_event_fifo(self) -> Path:
        path = self.dir / "events.fifo"
        try:
            if path.exists() and not stat.S_ISFIFO(path.stat().st_mode):
                path.unlink()
            if not path.exists():
                os.mkfifo(path, 0o600)
        except OSError:
            logger.exception("Could not create librespot event FIFO")
        return path

    def _write_event_script(self) -> Path:
        path = self.dir / "onevent.sh"
        path.write_text(EVENT_SCRIPT)
        path.chmod(0o700)
        return path

    def _start_sink(self) -> None:
        if self._sink and self._sink.poll() is None:
            return
        try:
            os.unlink(self._sink_socket)
        except OSError:
            pass
        cmd = [
            "mpv", "--no-video", "--really-quiet", "--idle=no",
            f"--input-ipc-server={self._sink_socket}",
            "--demuxer=rawaudio",
            f"--demuxer-rawaudio-rate={SAMPLE_RATE}",
            "--demuxer-rawaudio-channels=2",
            "--demuxer-rawaudio-format=s16le",
            "--cache=no",                     # keep pause/seek latency low
            "--audio-client-name=spoff",
            f"--volume={self._volume}",
        ]
        filters = self._filters()
        if filters:
            cmd.append(f"--af={filters}")
        cmd.append("-")
        self._sink = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # --------------------------------------------------------------- threads

    def _relay_audio(self, proc: subprocess.Popen) -> None:
        """librespot stdout -> sink mpv stdin. The sink never sees EOF between tracks."""
        src = proc.stdout
        while src is not None:
            chunk = src.read1(16384) if hasattr(src, "read1") else src.read(16384)
            if not chunk:
                return
            sink = self._sink
            if sink is None or sink.stdin is None:
                continue
            try:
                sink.stdin.write(chunk)
                sink.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                logger.warning("Spotify audio sink stopped; restarting it")
                with self._lock:
                    self._start_sink()

    def _read_log(self, proc: subprocess.Popen, on_login_url: Optional[Callable[[str], None]]) -> None:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if "https://accounts.spotify.com/" in line and not self.login_url:
                self.login_url = line[line.index("https://accounts.spotify.com/"):].split()[0]
                if on_login_url:
                    on_login_url(self.login_url)
            if "audio key error" in line:
                with self._events:
                    self.keys_refused = True
                    self.last_error = "Spotify refused the audio for this account"
                    self._events.notify_all()
            elif " ERROR " in line or "Premium" in line:
                self.last_error = line.split("] ", 1)[-1]
                logger.warning("librespot: %s", line)

    def _read_events(self, fifo: Path) -> None:
        # O_RDWR keeps the FIFO open with a writer, so reads never hit EOF
        # between the short-lived event script runs.
        try:
            fd = os.open(fifo, os.O_RDWR)
        except OSError:
            logger.exception("Could not open librespot event FIFO")
            return
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                parts = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
                self._handle_event(*parts)

    def _handle_event(self, name: str, track_id: str, position_ms: str, duration_ms: str) -> None:
        pos = _ms_to_s(position_ms)
        callback = None
        with self._events:
            if track_id and self.track_id and track_id != self.track_id and name != "track_changed":
                return  # stale event from the previous song
            if name == "track_changed" and _ms_to_s(duration_ms):
                self.duration = _ms_to_s(duration_ms)
            elif name in ("playing", "position_correction", "seeked"):
                self.is_paused = False if name == "playing" else self.is_paused
                self._set_position(pos)
            elif name == "paused":
                self.is_paused = True
                self._set_position(pos)
            elif name in ("end_of_track", "unavailable"):
                callback, self._on_end = self._on_end, None
            self._last_event = {"name": name, "track_id": track_id, "at": time.monotonic()}
            self._events.notify_all()
        if callback is not None:
            try:
                callback("eof" if name == "end_of_track" else "error")
            except Exception:
                logger.exception("Spotify end-of-track callback failed")

    def _set_position(self, seconds: Optional[float]) -> None:
        if seconds is not None:
            self._anchor_pos = seconds
            self._anchor_time = time.monotonic()

    # -------------------------------------------------------------- controls

    def _find_device(self) -> Optional[str]:
        ok, data, _ = auth.spotify_api_request("/me/player/devices", method="GET")
        for device in (data or {}).get("devices", []) if ok else []:
            if device.get("name") == self.device_name and device.get("id"):
                return device["id"]
        return None

    def _api(self, endpoint: str, method: str = "PUT", body: Optional[Dict[str, Any]] = None) -> bool:
        if not self.device_id:
            return False
        sep = "&" if "?" in endpoint else "?"
        ok, _, err = auth.spotify_api_request(f"{endpoint}{sep}device_id={self.device_id}", method=method, body=body)
        if not ok:
            self.last_error = err
        return ok

    def play(self, uri: str, duration_s: float, on_end: Callable[[str], None], timeout: float = 12.0) -> bool:
        """Starts a track and waits until librespot confirms it is playing."""
        track_id = uri.rsplit(":", 1)[-1]
        with self._events:
            self.track_id = track_id
            self.duration = duration_s
            self.is_paused = False
            self._set_position(0.0)
            self._on_end = on_end
            started = time.monotonic()
        if not self._api("/me/player/play", body={"uris": [uri], "position_ms": 0}):
            return False
        with self._events:
            while True:
                if self.keys_refused:
                    self._on_end = None
                    return False
                event = self._last_event
                if event.get("at", 0) >= started and event.get("track_id") == track_id:
                    if event.get("name") == "playing":
                        return True
                    if event.get("name") == "unavailable":
                        self._on_end = None
                        self.last_error = "Spotify can't play this song"
                        return False
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    self._on_end = None
                    self.last_error = "Spotify didn't start the song in time"
                    return False
                self._events.wait(remaining)

    def pause(self) -> None:
        if self._api("/me/player/pause"):
            with self._events:
                self._set_position(self.position())
                self.is_paused = True

    def resume(self) -> None:
        if self._api("/me/player/play"):
            with self._events:
                self._anchor_time = time.monotonic()
                self.is_paused = False

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, min(float(seconds), self.duration or float(seconds)))
        if self._api(f"/me/player/seek?position_ms={int(seconds * 1000)}"):
            with self._events:
                self._set_position(seconds)

    def stop_playback(self) -> None:
        """Stops the current song without firing its end callback."""
        with self._events:
            self._on_end = None
            was_playing = self.track_id is not None and not self.is_paused
            self.track_id = None
        if was_playing:
            self._api("/me/player/pause")

    def position(self) -> float:
        with self._events:
            if self.is_paused:
                return self._anchor_pos
            pos = self._anchor_pos + (time.monotonic() - self._anchor_time)
            return min(pos, self.duration) if self.duration else pos

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        self._sink_command(["set_property", "volume", self._volume])

    def apply_filters(self) -> None:
        self._sink_command(["set_property", "af", self._filters()])

    def _sink_command(self, command: list) -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect(self._sink_socket)
                sock.sendall((json.dumps({"command": command}) + "\n").encode())
        except OSError:
            pass  # sink not running; the setting is applied when it starts

    def shutdown(self) -> None:
        with self._lock:
            self._on_end = None
            self.device_id = None
            for proc in (self._librespot, self._sink):
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            self._librespot = None
            self._sink = None


def _ms_to_s(value: str) -> Optional[float]:
    try:
        return int(value) / 1000.0
    except (TypeError, ValueError):
        return None
