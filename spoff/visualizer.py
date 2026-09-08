import os
import sys
import math
import time
import shutil
import tempfile
import logging
import threading
import subprocess
from typing import Optional, List

logger = logging.getLogger("visualizer")

GLYPHS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇"]

class CavaVisualizer:
    """
    Real-time audio spectrum visualizer powered by CAVA (with PipeWire backend)
    and smooth fallback synthesis when CAVA is idle or unavailable.
    """
    def __init__(self, bars: int = 14):
        self.bars_count = bars
        self.proc: Optional[subprocess.Popen] = None
        self.conf_path: Optional[str] = None
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._raw_values: List[int] = [0] * bars
        self._last_cava_time: float = 0.0
        self._sim_phase: float = 0.0
        self._has_cava = shutil.which("cava") is not None

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        if not self._has_cava:
            logger.debug("cava binary not found on system; visualizer will use fallback mode")
            return

        try:
            with tempfile.NamedTemporaryFile("w", delete=False, prefix="spoff_cava_") as f:
                f.write(f"""
[general]
bars = {self.bars_count}
framerate = 25
autosens = 1

[input]
method = pipewire
active = 1

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 7
bar_delimiter = 59
frame_delimiter = 10
""")
                self.conf_path = f.name

            self.proc = subprocess.Popen(
                ["cava", "-p", self.conf_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True
            )
            self._thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._thread.start()
            logger.info("CAVA audio visualizer background process started.")
        except Exception as e:
            logger.debug(f"Could not start CAVA: {e}")
            self.proc = None

    def _reader_loop(self) -> None:
        while self.running and self.proc and self.proc.stdout:
            try:
                line = self.proc.stdout.readline()
                if not line:
                    break
                parts = line.strip().split(";")
                vals = []
                for p in parts:
                    if p.isdigit():
                        vals.append(min(7, max(0, int(p))))
                if vals:
                    while len(vals) < self.bars_count:
                        vals.append(0)
                    vals = vals[:self.bars_count]
                    self._raw_values = vals
                    if any(v > 0 for v in vals):
                        self._last_cava_time = time.time()
            except Exception:
                break

    def get_bars_markup(self, is_playing: bool, is_paused: bool) -> str:
        """
        Returns Rich markup string representing the current spectrum.
        """
        if not is_playing:
            return ""

        if is_paused:
            flat = "─" * self.bars_count
            return f"[dim #444444]{flat}[/]"

        # Check if CAVA is feeding active audio (within last 1.0s)
        now = time.time()
        cava_active = (now - self._last_cava_time < 1.0) and any(v > 0 for v in self._raw_values)

        if cava_active:
            bars_str = "".join(GLYPHS[v] for v in self._raw_values)
            return f"[bold #569f68]{bars_str}[/]"

        # Organic audio fallback synthesis (pulsing wave based on playback)
        self._sim_phase += 0.25
        sim_bars = []
        for i in range(self.bars_count):
            norm_i = (i - self.bars_count / 2) / (self.bars_count / 2)
            envelope = math.exp(-norm_i * norm_i * 1.5)
            wave1 = math.sin(self._sim_phase + i * 0.45)
            wave2 = math.cos(self._sim_phase * 0.7 - i * 0.3)
            val = int(envelope * (3.5 + 2.5 * wave1 + 1.0 * wave2))
            clamped = max(0, min(7, val))
            sim_bars.append(GLYPHS[clamped])

        bars_str = "".join(sim_bars)
        return f"[#569f68]{bars_str}[/]"

    def stop(self) -> None:
        self.running = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.3)
            except Exception:
                pass
            self.proc = None

        if self.conf_path and os.path.exists(self.conf_path):
            try:
                os.unlink(self.conf_path)
            except Exception:
                pass
            self.conf_path = None
