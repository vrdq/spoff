import os
import math
import time
import shutil
import tempfile
import logging
import threading
import subprocess
from typing import Optional, List
from textual.widgets import Static

logger = logging.getLogger("visualizer")

GLYPHS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]


class CavaVisualizer:
    """
    Real-time PipeWire audio spectrum analyzer powered by CAVA in 60+ FPS binary stream mode,
    with sub-millisecond peak detection, smooth exponential gravity falloff, and
    an organic waveform fallback for buffering / offline tracks.
    """

    def __init__(self, bars: int = 14, fps: Optional[int] = None):
        if fps is None:
            fps = max(60, int(os.environ.get("SPOFF_VISUALIZER_FPS", os.environ.get("TEXTUAL_FPS", "60"))))
        self.bars_count = bars
        self.fps = fps
        self.proc: Optional[subprocess.Popen] = None
        self.conf_path: Optional[str] = None
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._raw_values: List[float] = [0.0] * bars
        self._smooth_values: List[float] = [0.0] * bars
        self._last_cava_time: float = 0.0
        self._sim_phase: float = 0.0
        self._has_cava = shutil.which("cava") is not None

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        if not self._has_cava:
            logger.debug("cava binary not found on system; visualizer will use 60+ FPS synthesized waveform mode")
            return

        try:
            with tempfile.NamedTemporaryFile("w", delete=False, prefix="spoff_cava_") as f:
                f.write(f"""
[general]
bars = {self.bars_count}
framerate = {self.fps}
autosens = 1

[input]
method = pipewire
source = auto

[output]
method = raw
raw_target = /dev/stdout
data_format = binary
bit_format = 8bit
""")
                self.conf_path = f.name

            self.proc = subprocess.Popen(
                ["cava", "-p", self.conf_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            self._thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._thread.start()
            logger.info(f"CAVA 60+ FPS audio visualizer active ({self.bars_count} bars @ {self.fps} FPS).")
        except Exception as e:
            logger.debug(f"Could not start CAVA: {e}")
            self.proc = None

    def _reader_loop(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        fd = self.proc.stdout.fileno()
        bars_count = self.bars_count
        while self.running:
            try:
                data = os.read(fd, bars_count)
                if not data:
                    break
                if len(data) == bars_count:
                    # 0..255 binary stream to normalized 0.0..1.0
                    vals = [b / 255.0 for b in data]
                    self._raw_values = vals
                    if any(b > 0 for b in data):
                        self._last_cava_time = time.monotonic()
            except Exception:
                break

    def get_markup(self, is_playing: bool, is_paused: bool) -> str:
        """
        Returns Rich markup string representing the current spectrum at 60+ FPS.
        """
        if not is_playing:
            return ""

        if is_paused:
            flat = "─" * self.bars_count
            return f"[dim #444444]{flat}[/]"

        now = time.monotonic()
        # Smooth exponential decay factor per 60 FPS frame (~16.6ms)
        falloff = 0.82

        # Check if CAVA is feeding active audio (within last 0.5s)
        cava_active = (now - self._last_cava_time < 0.5) and any(v > 0.01 for v in self._raw_values)

        if cava_active:
            for i in range(self.bars_count):
                v = self._raw_values[i]
                if v >= self._smooth_values[i]:
                    self._smooth_values[i] = v
                else:
                    self._smooth_values[i] = max(0.0, self._smooth_values[i] * falloff)

            chars = []
            for i in range(self.bars_count):
                val = self._smooth_values[i]
                if val <= 0.005:
                    chars.append(GLYPHS[0])
                else:
                    idx = max(1, min(8, int(val * 8.99)))
                    chars.append(GLYPHS[idx])

            bars_str = "".join(chars)
            return f"[bold #569f68]{bars_str}[/]"

        # Organic audio fallback synthesis (pulsing wave based on playback)
        self._sim_phase += 0.08
        chars = []
        for i in range(self.bars_count):
            norm_i = (i - self.bars_count / 2) / (self.bars_count / 2)
            envelope = math.exp(-norm_i * norm_i * 1.5)
            wave1 = math.sin(self._sim_phase + i * 0.45)
            wave2 = math.cos(self._sim_phase * 0.7 - i * 0.3)
            val = envelope * (0.45 + 0.35 * wave1 + 0.15 * wave2)
            val = max(0.0, min(1.0, val))
            if val <= 0.01:
                chars.append(GLYPHS[0])
            else:
                idx = max(1, min(8, int(val * 8.99)))
                chars.append(GLYPHS[idx])

        bars_str = "".join(chars)
        return f"[#569f68]{bars_str}[/]"

    def stop(self) -> None:
        self.running = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.2)
            except Exception:
                pass
            self.proc = None

        if self.conf_path and os.path.exists(self.conf_path):
            try:
                os.unlink(self.conf_path)
            except Exception:
                pass
            self.conf_path = None


class VisualizerWidget(Static):
    """
    Textual widget dedicated to rendering the audio spectrum at 60+ FPS
    with zero UI layout overhead and low CPU consumption during standby.
    """

    DEFAULT_CSS = """
    VisualizerWidget {
        width: 14;
        height: 1;
        margin-right: 2;
    }
    """

    def __init__(self, visualizer: CavaVisualizer, **kwargs):
        super().__init__("", **kwargs)
        self.visualizer = visualizer
        self.fps = visualizer.fps
        self._idle_counter = 0

    def on_mount(self) -> None:
        self.set_interval(1.0 / self.fps, self._tick)

    def _tick(self) -> None:
        app = getattr(self, "app", None)
        if not app or not hasattr(app, "player"):
            return
        curr = app.player.current_track
        is_playing = curr is not None
        is_paused = app.player.is_paused if is_playing else False

        if is_playing and not is_paused:
            markup = self.visualizer.get_markup(is_playing, is_paused)
            self.update(markup, layout=False)
        else:
            self._idle_counter = (self._idle_counter + 1) % 12
            if self._idle_counter == 0:
                markup = self.visualizer.get_markup(is_playing, is_paused)
                self.update(markup, layout=False)
