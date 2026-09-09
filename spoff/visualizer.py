import os
import math
import time
import shutil
import tempfile
import logging
import threading
import subprocess
from typing import Optional, List, Dict
from textual.widgets import Static

logger = logging.getLogger("visualizer")

BAR_GLYPHS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
DOT_GLYPHS = [" ", "·", "∶", "⁝", "⁞", "█"]

# Braille 2x4 dot masks
# Left column (dots 1, 2, 3, 7 from top to bottom)
LEFT_DOTS = [0, 0x40, 0x40 | 0x4, 0x40 | 0x4 | 0x2, 0x40 | 0x4 | 0x2 | 0x1]
# Right column (dots 4, 5, 6, 8 from top to bottom)
RIGHT_DOTS = [0, 0x80, 0x80 | 0x20, 0x80 | 0x20 | 0x10, 0x80 | 0x20 | 0x10 | 0x8]
# Peak dots for top row
LEFT_PEAK = [0, 0x40, 0x4, 0x2, 0x1]
RIGHT_PEAK = [0, 0x80, 0x20, 0x10, 0x8]

STYLE_NAMES: Dict[str, str] = {
    "bars": "Studio Spectrum (Peaks)",
    "braille": "High-Res Braille EQ",
    "stereo": "Mirrored Stereo Pulse",
    "wave": "Liquid Oscilloscope",
    "dots": "Minimal Matrix",
}

COLOR_NAMES: Dict[str, str] = {
    "green": "Spotify Emerald",
    "cyan": "Cyberpunk Ice",
    "amber": "Warm Vintage Tube",
    "mono": "Studio Greyscale",
}

PALETTES: Dict[str, List[str]] = {
    "green": ["#2d663b", "#3b874e", "#569f68", "#6cc483", "#8deda4", "#ffffff"],
    "cyan":  ["#184f60", "#20728c", "#2bb8d6", "#4cd4f0", "#7cedff", "#ffffff"],
    "amber": ["#5e3f17", "#8c5e22", "#c4a768", "#e2c27c", "#fae09c", "#ffffff"],
    "mono":  ["#383838", "#585858", "#787878", "#aaaaaa", "#cccccc", "#ffffff"],
}


class CavaVisualizer:
    """
    High-performance, 60+ FPS audio spectrum analyzer powered by CAVA in PipeWire
    binary stream mode, featuring:
    - 5 selectable visualization styles (Studio Bars with Peak Caps, High-Res Braille EQ,
      Mirrored Stereo Pulse, Liquid Oscilloscope, Minimal Matrix)
    - 4 curated studio color themes (Emerald, Cyberpunk Ice, Warm Tube, Studio Greyscale)
    - Sub-millisecond peak cap detection with realistic gravitational drop-off
    - CAVA Monstercat integral smoothing with 40-14,000 Hz acoustic tuning
    - Multi-harmonic organic waveform fallback for buffering and offline tracks.
    """

    def __init__(
        self,
        bars: int = 24,
        fps: Optional[int] = None,
        style: str = "bars",
        color: str = "green"
    ):
        if fps is None:
            fps = max(60, int(os.environ.get("SPOFF_VISUALIZER_FPS", os.environ.get("TEXTUAL_FPS", "60"))))
        self.bars_count = bars
        self.fps = fps
        self.style = style if style in STYLE_NAMES else "bars"
        self.color = color if color in PALETTES else "green"

        self.proc: Optional[subprocess.Popen] = None
        self.conf_path: Optional[str] = None
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None

        self._raw_values: List[float] = [0.0] * bars
        self._smooth_values: List[float] = [0.0] * bars
        self._peaks: List[float] = [0.0] * bars
        self._peak_hold: List[int] = [0] * bars

        self._last_cava_time: float = 0.0
        self._sim_phase: float = 0.0
        self._has_cava = shutil.which("cava") is not None

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        if not self._has_cava:
            logger.debug("cava binary not found on system; visualizer using synthesized mode")
            return

        try:
            with tempfile.NamedTemporaryFile("w", delete=False, prefix="spoff_cava_") as f:
                f.write(f"""
[general]
bars = {self.bars_count}
framerate = {self.fps}
autosens = 1
lower_cutoff_freq = 40
higher_cutoff_freq = 14000

[smoothing]
monstercat = 1
noise_reduction = 55

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
                    vals = [b / 255.0 for b in data]
                    self._raw_values = vals
                    if any(b > 0 for b in data):
                        self._last_cava_time = time.monotonic()
            except Exception:
                break

    def cycle_style(self) -> str:
        styles = list(STYLE_NAMES.keys())
        idx = (styles.index(self.style) + 1) % len(styles)
        self.style = styles[idx]
        return self.style

    def cycle_color(self) -> str:
        colors = list(PALETTES.keys())
        idx = (colors.index(self.color) + 1) % len(colors)
        self.color = colors[idx]
        return self.color

    def get_style_name(self) -> str:
        return STYLE_NAMES.get(self.style, "Studio Spectrum")

    def get_color_name(self) -> str:
        return COLOR_NAMES.get(self.color, "Emerald")

    def set_style(self, style: str) -> None:
        if style in STYLE_NAMES:
            self.style = style

    def set_color(self, color: str) -> None:
        if color in PALETTES:
            self.color = color

    def get_markup(self, is_playing: bool, is_paused: bool) -> str:
        """
        Returns a 24-character Rich markup string representing the current
        audio spectrum at 60+ FPS.
        """
        if not is_playing:
            return ""

        if is_paused:
            if self.style in ("braille", "wave"):
                flat = "⠤" * self.bars_count
            elif self.style == "dots":
                flat = "·" * self.bars_count
            else:
                flat = "─" * self.bars_count
            return f"[dim #3a3a3a]{flat}[/]"

        now = time.monotonic()
        falloff = 0.84
        cava_active = (now - self._last_cava_time < 0.5) and any(v > 0.01 for v in self._raw_values)

        if not cava_active:
            # Multi-harmonic organic audio fallback synthesis
            self._sim_phase += 0.09
            for i in range(self.bars_count):
                norm_i = (i - self.bars_count / 2) / (self.bars_count / 2)
                envelope = math.exp(-norm_i * norm_i * 1.8)
                wave1 = math.sin(self._sim_phase * 1.2 + i * 0.5)
                wave2 = math.cos(self._sim_phase * 0.7 - i * 0.35)
                wave3 = math.sin(self._sim_phase * 2.1 + i * 0.9) * 0.25
                bass_pulse = 0.35 if (math.sin(self._sim_phase * 1.8) > 0.6) and i < 8 else 0.0
                val = envelope * (0.38 + 0.32 * wave1 + 0.18 * wave2 + wave3) + bass_pulse
                self._raw_values[i] = max(0.0, min(1.0, val))

        # Update smoothed values and peak caps
        for i in range(self.bars_count):
            v = self._raw_values[i]
            if v >= self._smooth_values[i]:
                self._smooth_values[i] = v
            else:
                self._smooth_values[i] = max(0.0, self._smooth_values[i] * falloff)

            if self._smooth_values[i] >= self._peaks[i]:
                self._peaks[i] = self._smooth_values[i]
                self._peak_hold[i] = 10  # hold peak for ~160ms at 60 FPS
            else:
                if self._peak_hold[i] > 0:
                    self._peak_hold[i] -= 1
                else:
                    self._peaks[i] = max(0.0, self._peaks[i] - 0.035)

        palette = PALETTES.get(self.color, PALETTES["green"])
        peak_color = palette[-1]

        # 1. STYLE: Studio Bars (with Peak Caps)
        if self.style == "bars":
            chars = []
            for i in range(self.bars_count):
                val = self._smooth_values[i]
                peak = self._peaks[i]
                if val <= 0.01:
                    if peak >= 0.7:
                        chars.append(f"[{peak_color}]▔[/]")
                    else:
                        chars.append("[dim #222222] [/]")
                else:
                    idx = max(1, min(8, int(val * 8.99)))
                    if peak >= 0.88 and val < 0.6:
                        chars.append(f"[{peak_color}]▔[/]")
                    else:
                        c_idx = min(len(palette) - 1, int(val * (len(palette) - 1)))
                        chars.append(f"[{palette[c_idx]}]{BAR_GLYPHS[idx]}[/]")
            return "".join(chars)

        # 2. STYLE: High-Res Braille EQ (Dual-band per cell + peak dots)
        elif self.style == "braille":
            # Linear interpolate 24 bars to 48 columns (2 columns per character)
            interp_vals = []
            interp_peaks = []
            n = self.bars_count
            for i in range(n):
                interp_vals.append(self._smooth_values[i])
                nxt_v = self._smooth_values[min(n - 1, i + 1)]
                interp_vals.append((self._smooth_values[i] + nxt_v) / 2.0)

                interp_peaks.append(self._peaks[i])
                nxt_p = self._peaks[min(n - 1, i + 1)]
                interp_peaks.append((self._peaks[i] + nxt_p) / 2.0)

            chars = []
            for c in range(24):
                i1 = 2 * c
                i2 = 2 * c + 1
                lv = min(4, max(0, int(interp_vals[i1] * 4.4)))
                rv = min(4, max(0, int(interp_vals[i2] * 4.4)))
                lp = min(4, max(0, int(interp_peaks[i1] * 4.4)))
                rp = min(4, max(0, int(interp_peaks[i2] * 4.4)))

                mask = LEFT_DOTS[lv] | RIGHT_DOTS[rv]
                if lp > lv:
                    mask |= LEFT_PEAK[lp]
                if rp > rv:
                    mask |= RIGHT_PEAK[rp]

                char = chr(0x2800 | mask) if mask else " "
                avg_v = (interp_vals[i1] + interp_vals[i2]) / 2.0
                c_idx = min(len(palette) - 1, int(avg_v * (len(palette) - 1)))
                chars.append(f"[{palette[c_idx]}]{char}[/]")
            return "".join(chars)

        # 3. STYLE: Mirrored Stereo Pulse (Bass in center)
        elif self.style == "stereo":
            half = self.bars_count // 2
            left_v = self._smooth_values[:half][::-1]
            right_v = self._smooth_values[:half]
            stereo_v = left_v + right_v

            left_p = self._peaks[:half][::-1]
            right_p = self._peaks[:half]
            stereo_p = left_p + right_p

            chars = []
            for i in range(self.bars_count):
                val = stereo_v[i]
                peak = stereo_p[i]
                if val <= 0.01:
                    if peak >= 0.7:
                        chars.append(f"[{peak_color}]▔[/]")
                    else:
                        chars.append("[dim #222222] [/]")
                else:
                    idx = max(1, min(8, int(val * 8.99)))
                    if peak >= 0.88 and val < 0.6:
                        chars.append(f"[{peak_color}]▔[/]")
                    else:
                        c_idx = min(len(palette) - 1, int(val * (len(palette) - 1)))
                        chars.append(f"[{palette[c_idx]}]{BAR_GLYPHS[idx]}[/]")
            return "".join(chars)

        # 4. STYLE: Liquid Oscilloscope (Analog Wave)
        elif self.style == "wave":
            L_ROWS = [0x1, 0x2, 0x4, 0x40]
            R_ROWS = [0x8, 0x10, 0x20, 0x80]
            chars = []
            for c in range(24):
                i1 = 2 * c
                i2 = 2 * c + 1
                e1 = self._smooth_values[min(len(self._smooth_values) - 1, i1 // 2)]
                e2 = self._smooth_values[min(len(self._smooth_values) - 1, i2 // 2)]

                y1 = 1.5 + math.sin(self._sim_phase + i1 * 0.38) * (0.35 + 1.15 * e1)
                y2 = 1.5 + math.sin(self._sim_phase + i2 * 0.38) * (0.35 + 1.15 * e2)

                r1 = max(0, min(3, int(round(y1))))
                r2 = max(0, min(3, int(round(y2))))

                mask = L_ROWS[r1] | R_ROWS[r2]
                char = chr(0x2800 | mask)
                avg_e = (e1 + e2) / 2.0
                c_idx = min(len(palette) - 1, int(avg_e * (len(palette) - 1)))
                chars.append(f"[{palette[c_idx]}]{char}[/]")
            return "".join(chars)

        # 5. STYLE: Minimal Matrix (Discrete LED Pips)
        elif self.style == "dots":
            chars = []
            for i in range(self.bars_count):
                val = self._smooth_values[i]
                if val <= 0.01:
                    chars.append("[dim #222222]·[/]")
                else:
                    d_idx = min(5, int(val * 5.99))
                    c_idx = min(len(palette) - 1, int(val * (len(palette) - 1)))
                    chars.append(f"[{palette[c_idx]}]{DOT_GLYPHS[d_idx]}[/]")
            return "".join(chars)

        return ""

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
    with zero UI layout overhead and interactive style cycling.
    """

    DEFAULT_CSS = """
    VisualizerWidget {
        width: 24;
        height: 1;
        margin-right: 2;
    }
    """

    def __init__(self, visualizer: CavaVisualizer, **kwargs):
        super().__init__("", **kwargs)
        self.visualizer = visualizer
        self.fps = visualizer.fps
        self._idle_counter = 0
        self.tooltip = "Click to cycle visualizer mode (or press V)"

    def on_mount(self) -> None:
        self.set_interval(1.0 / self.fps, self._tick)

    def on_click(self, event) -> None:
        self.visualizer.cycle_style()
        app = getattr(self, "app", None)
        if app:
            if hasattr(app, "save_visualizer_preferences"):
                app.save_visualizer_preferences()
            if hasattr(app, "notify_user"):
                app.notify_user(f"Visualizer: {self.visualizer.get_style_name()}")

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
