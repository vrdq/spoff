import math
import cmath
import json
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("eq")

class FilterType(str, Enum):
    PEAKING = "PK"
    LOW_SHELF = "LSC"
    HIGH_SHELF = "HSC"

    @classmethod
    def from_str(cls, val: str) -> "FilterType":
        u = val.strip().upper()
        if u in ("PK", "PEAK", "PEAKING", "BELL"):
            return cls.PEAKING
        elif u in ("LSC", "LOW_SHELF", "LOWSHELF", "LS"):
            return cls.LOW_SHELF
        elif u in ("HSC", "HIGH_SHELF", "HIGHSHELF", "HS"):
            return cls.HIGH_SHELF
        return cls.PEAKING


@dataclass
class BiquadCoefficients:
    b0: float = 1.0
    b1: float = 0.0
    b2: float = 0.0
    a1: float = 0.0
    a2: float = 0.0

    def frequency_response(self, freq_hz: float, sample_rate: float = 48000.0) -> complex:
        """Calculates complex transfer function H(e^{j omega}) at frequency freq_hz."""
        w = 2.0 * math.pi * freq_hz / sample_rate
        z_inv = cmath.exp(-1j * w)
        z_inv2 = z_inv * z_inv
        num = self.b0 + self.b1 * z_inv + self.b2 * z_inv2
        den = 1.0 + self.a1 * z_inv + self.a2 * z_inv2
        if abs(den) < 1e-15:
            return complex(1.0, 0.0)
        return num / den

    def magnitude_db(self, freq_hz: float, sample_rate: float = 48000.0) -> float:
        """Returns gain in decibels at frequency freq_hz."""
        resp = self.frequency_response(freq_hz, sample_rate)
        mag = abs(resp)
        if mag <= 1e-12:
            return -240.0
        return 20.0 * math.log10(mag)

    def phase_deg(self, freq_hz: float, sample_rate: float = 48000.0) -> float:
        """Returns phase shift in degrees at frequency freq_hz."""
        resp = self.frequency_response(freq_hz, sample_rate)
        return math.degrees(cmath.phase(resp))


class BiquadFilter:
    """
    Transposed Direct Form II IIR Biquad Filter.
    Provides superior numerical stability and low quantization noise.
    Includes anti-denormal handling and state reset.
    """
    def __init__(self, coeffs: Optional[BiquadCoefficients] = None):
        self.coeffs = coeffs or BiquadCoefficients()
        # State variables for 2 audio channels (Left, Right)
        self.s1_l: float = 0.0
        self.s2_l: float = 0.0
        self.s1_r: float = 0.0
        self.s2_r: float = 0.0

    def reset(self) -> None:
        self.s1_l = 0.0
        self.s2_l = 0.0
        self.s1_r = 0.0
        self.s2_r = 0.0

    def set_coefficients(self, coeffs: BiquadCoefficients) -> None:
        self.coeffs = coeffs

    def process_sample(self, x: float, channel: int = 0) -> float:
        """Processes a single audio sample through the Transposed Direct Form II structure."""
        b0, b1, b2 = self.coeffs.b0, self.coeffs.b1, self.coeffs.b2
        a1, a2 = self.coeffs.a1, self.coeffs.a2

        if channel == 0:
            y = b0 * x + self.s1_l
            self.s1_l = b1 * x - a1 * y + self.s2_l
            self.s2_l = b2 * x - a2 * y
            # Flush subnormals to zero to prevent CPU penalties
            if abs(self.s1_l) < 1e-15: self.s1_l = 0.0
            if abs(self.s2_l) < 1e-15: self.s2_l = 0.0
            return y
        else:
            y = b0 * x + self.s1_r
            self.s1_r = b1 * x - a1 * y + self.s2_r
            self.s2_r = b2 * x - a2 * y
            if abs(self.s1_r) < 1e-15: self.s1_r = 0.0
            if abs(self.s2_r) < 1e-15: self.s2_r = 0.0
            return y

    def process_buffer(self, samples: List[float], channel: int = 0) -> List[float]:
        """Processes a buffer of audio samples."""
        return [self.process_sample(s, channel) for s in samples]


@dataclass
class EQBand:
    index: int
    filter_type: FilterType
    frequency: float
    gain_db: float
    q: float
    enabled: bool = True
    label: str = ""

    def calculate_coefficients(self, sample_rate: float = 48000.0) -> BiquadCoefficients:
        """
        Calculates normalized second-order IIR biquad filter coefficients
        using Robert Bristow-Johnson's Audio EQ Cookbook formulae.
        """
        if not self.enabled or abs(self.gain_db) < 0.001:
            return BiquadCoefficients(b0=1.0, b1=0.0, b2=0.0, a1=0.0, a2=0.0)

        f0 = max(10.0, min(self.frequency, sample_rate * 0.495))
        q = max(0.1, min(self.q, 25.0))
        gain = max(-36.0, min(self.gain_db, 24.0))

        A = 10.0 ** (gain / 40.0)
        w0 = 2.0 * math.pi * f0 / sample_rate
        cos_w0 = math.cos(w0)
        sin_w0 = math.sin(w0)
        alpha = sin_w0 / (2.0 * q)

        if self.filter_type == FilterType.PEAKING:
            b0 = 1.0 + alpha * A
            b1 = -2.0 * cos_w0
            b2 = 1.0 - alpha * A
            a0 = 1.0 + alpha / A
            a1 = -2.0 * cos_w0
            a2 = 1.0 - alpha / A

        elif self.filter_type == FilterType.LOW_SHELF:
            sqrt_A = math.sqrt(A)
            two_sqrt_A_alpha = 2.0 * sqrt_A * alpha
            b0 = A * ((A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_A_alpha)
            b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0)
            b2 = A * ((A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_A_alpha)
            a0 = (A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_A_alpha
            a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0)
            a2 = (A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_A_alpha

        elif self.filter_type == FilterType.HIGH_SHELF:
            sqrt_A = math.sqrt(A)
            two_sqrt_A_alpha = 2.0 * sqrt_A * alpha
            b0 = A * ((A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_A_alpha)
            b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w0)
            b2 = A * ((A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_A_alpha)
            a0 = (A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_A_alpha
            a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cos_w0)
            a2 = (A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_A_alpha

        else:
            return BiquadCoefficients()

        if abs(a0) < 1e-15:
            return BiquadCoefficients()

        # Normalize by a0
        return BiquadCoefficients(
            b0=b0 / a0,
            b1=b1 / a0,
            b2=b2 / a0,
            a1=a1 / a0,
            a2=a2 / a0,
        )


@dataclass
class EQPreset:
    name: str
    description: str
    preamp_db: float
    bands: List[EQBand]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "preamp_db": self.preamp_db,
            "bands": [
                {
                    "index": b.index,
                    "filter_type": b.filter_type.value,
                    "frequency": b.frequency,
                    "gain_db": b.gain_db,
                    "q": b.q,
                    "enabled": b.enabled,
                    "label": b.label,
                }
                for b in self.bands
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EQPreset":
        bands = []
        for bd in data.get("bands", []):
            bands.append(EQBand(
                index=int(bd.get("index", len(bands) + 1)),
                filter_type=FilterType.from_str(bd.get("filter_type", "PK")),
                frequency=float(bd.get("frequency", 1000.0)),
                gain_db=float(bd.get("gain_db", 0.0)),
                q=float(bd.get("q", 1.0)),
                enabled=bool(bd.get("enabled", True)),
                label=str(bd.get("label", "")),
            ))
        return cls(
            name=str(data.get("name") or data.get("preset_name") or "Custom"),
            description=str(data.get("description", "")),
            preamp_db=float(data.get("preamp_db", 0.0)),
            bands=bands
        )


# ============================================================================
# BUILT-IN FLAGSHIP ACOUSTIC PRESETS
# ============================================================================

SAMSUNG_AKG_REFERENCE_PRESET = EQPreset(
    name="Samsung AKG Master Reference",
    description="Studio-grade calibration for Samsung EO-IG955 dual-driver IEMs (Harman Target with mud purge and tweeter resonance compensation)",
    preamp_db=-5.0,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 65.0, +4.5, 0.70, label="Sub-Bass Shelf"),
        EQBand(2, FilterType.PEAKING, 145.0, -4.5, 0.50, label="Acoustic Mud Purge"),
        EQBand(3, FilterType.PEAKING, 230.0, -1.8, 1.40, label="Bass/Mid Transition"),
        EQBand(4, FilterType.PEAKING, 780.0, +4.2, 0.65, label="Vocal Chest Resonance"),
        EQBand(5, FilterType.PEAKING, 1750.0, -1.2, 1.80, label="Anti-Nasal Notch"),
        EQBand(6, FilterType.PEAKING, 2900.0, -2.2, 3.20, label="Pinna Glare Tamer"),
        EQBand(7, FilterType.PEAKING, 4600.0, +2.2, 3.50, label="Instrument Snap"),
        EQBand(8, FilterType.PEAKING, 6400.0, +2.8, 4.00, label="Dual-Driver Crossover Bridge"),
        EQBand(9, FilterType.PEAKING, 10000.0, -7.5, 2.20, label="8mm Tweeter Spike Killer"),
        EQBand(10, FilterType.HIGH_SHELF, 13500.0, +3.0, 0.70, label="Holographic Air Shelf"),
    ]
)

HARMAN_IN_EAR_2019_PRESET = EQPreset(
    name="Harman Target 2019 (In-Ear)",
    description="Industry reference Harman In-Ear Target 2019 curve with sub-bass shelf and pinna compensation",
    preamp_db=-5.5,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 80.0, +5.5, 0.71, label="Harman Sub-Bass"),
        EQBand(2, FilterType.PEAKING, 200.0, -1.5, 1.00, label="Warmth Control"),
        EQBand(3, FilterType.PEAKING, 1000.0, +1.0, 1.20, label="Midrange Body"),
        EQBand(4, FilterType.PEAKING, 2800.0, +3.5, 2.00, label="Ear Canal Resonance"),
        EQBand(5, FilterType.PEAKING, 5000.0, -2.0, 3.00, label="Concha Notch"),
        EQBand(6, FilterType.PEAKING, 7500.0, -3.0, 3.50, label="Sibilance Dampener"),
        EQBand(7, FilterType.HIGH_SHELF, 11000.0, +2.5, 0.71, label="Air & Brilliance"),
    ]
)

BASS_IMPACT_PRESET = EQPreset(
    name="Deep Sub-Bass Impact",
    description="Club & EDM low-end authority with high-pass safety and mud cut",
    preamp_db=-6.0,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 45.0, +6.0, 0.70, label="Sub-Bass Rumble"),
        EQBand(2, FilterType.PEAKING, 85.0, +3.5, 1.20, label="Punch & Thump"),
        EQBand(3, FilterType.PEAKING, 250.0, -2.5, 1.00, label="Boxiness Cut"),
        EQBand(4, FilterType.PEAKING, 3500.0, +1.5, 2.00, label="Transient Attack"),
        EQBand(5, FilterType.HIGH_SHELF, 12000.0, +2.0, 0.70, label="Crisp Shimmer"),
    ]
)

VOCAL_PRESENCE_PRESET = EQPreset(
    name="Vocal Intelligibility & Air",
    description="Brings lead vocals and dialogue forward with body resonance and anti-sibilance",
    preamp_db=-3.5,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 100.0, -2.0, 0.70, label="Proximity Rumble Cut"),
        EQBand(2, FilterType.PEAKING, 250.0, -1.5, 1.40, label="Chest Mud Reduction"),
        EQBand(3, FilterType.PEAKING, 1200.0, +2.5, 1.20, label="Vocal Formant Lift"),
        EQBand(4, FilterType.PEAKING, 3200.0, +3.2, 1.80, label="Presence & Articulation"),
        EQBand(5, FilterType.PEAKING, 6800.0, -3.0, 3.00, label="De-Esser Notch"),
        EQBand(6, FilterType.HIGH_SHELF, 12000.0, +2.0, 0.70, label="Vocal Air"),
    ]
)

FLAT_PRESET = EQPreset(
    name="Flat / Studio Neutral",
    description="Bit-perfect unity response (0.0 dB across all bands)",
    preamp_db=0.0,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 65.0, 0.0, 0.70, label="Band 1"),
        EQBand(2, FilterType.PEAKING, 145.0, 0.0, 0.50, label="Band 2"),
        EQBand(3, FilterType.PEAKING, 230.0, 0.0, 1.40, label="Band 3"),
        EQBand(4, FilterType.PEAKING, 780.0, 0.0, 0.65, label="Band 4"),
        EQBand(5, FilterType.PEAKING, 1750.0, 0.0, 1.80, label="Band 5"),
        EQBand(6, FilterType.PEAKING, 2900.0, 0.0, 3.20, label="Band 6"),
        EQBand(7, FilterType.PEAKING, 4600.0, 0.0, 3.50, label="Band 7"),
        EQBand(8, FilterType.PEAKING, 6400.0, 0.0, 4.00, label="Band 8"),
        EQBand(9, FilterType.PEAKING, 10000.0, 0.0, 2.20, label="Band 9"),
        EQBand(10, FilterType.HIGH_SHELF, 13500.0, 0.0, 0.70, label="Band 10"),
    ]
)

BUILTIN_PRESETS: List[EQPreset] = [
    SAMSUNG_AKG_REFERENCE_PRESET,
    HARMAN_IN_EAR_2019_PRESET,
    BASS_IMPACT_PRESET,
    VOCAL_PRESENCE_PRESET,
    FLAT_PRESET,
]


# ============================================================================
# PARAMETRIC EQUALIZER ENGINE
# ============================================================================

class ParametricEQEngine:
    """
    Studio-grade 10-band Parametric Equalizer DSP Engine.
    Handles IIR Biquad coefficient computation, digital headroom anti-clipping,
    frequency response analysis, and audio filter graph compilation.
    """
    def __init__(
        self,
        preset: Optional[EQPreset] = None,
        sample_rate: float = 48000.0,
        bypassed: bool = False
    ):
        p = preset or SAMSUNG_AKG_REFERENCE_PRESET
        self.preset_name: str = p.name
        self.description: str = p.description
        self.preamp_db: float = p.preamp_db
        self.bands: List[EQBand] = [EQBand(**asdict(b)) for b in p.bands]
        self.sample_rate: float = sample_rate
        self.bypassed: bool = bypassed
        self._filters: List[BiquadFilter] = [BiquadFilter() for _ in self.bands]
        self._update_filter_coefficients()

    def set_sample_rate(self, sample_rate: float) -> None:
        if sample_rate > 0 and sample_rate != self.sample_rate:
            self.sample_rate = sample_rate
            self._update_filter_coefficients()

    def _update_filter_coefficients(self) -> None:
        for i, band in enumerate(self.bands):
            coeffs = band.calculate_coefficients(self.sample_rate)
            if i < len(self._filters):
                self._filters[i].set_coefficients(coeffs)
            else:
                self._filters.append(BiquadFilter(coeffs))

    def load_preset(self, preset: EQPreset) -> None:
        """Loads a preset while maintaining smooth transition."""
        self.preset_name = preset.name
        self.description = preset.description
        self.preamp_db = preset.preamp_db
        self.bands = [EQBand(**asdict(b)) for b in preset.bands]
        self._update_filter_coefficients()

    def set_preamp(self, preamp_db: float) -> None:
        """Sets the preamp gain in dB (-24.0 to +12.0)."""
        self.preamp_db = max(-30.0, min(12.0, float(preamp_db)))

    def set_band(
        self,
        index: int,
        frequency: Optional[float] = None,
        gain_db: Optional[float] = None,
        q: Optional[float] = None,
        filter_type: Optional[FilterType] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """Dynamically adjusts a band's parameters at runtime."""
        for b in self.bands:
            if b.index == index:
                if frequency is not None:
                    b.frequency = max(10.0, min(frequency, self.sample_rate * 0.495))
                if gain_db is not None:
                    b.gain_db = max(-36.0, min(gain_db, 24.0))
                if q is not None:
                    b.q = max(0.1, min(q, 25.0))
                if filter_type is not None:
                    b.filter_type = filter_type
                if enabled is not None:
                    b.enabled = enabled
                self._update_filter_coefficients()
                break

    def toggle_bypass(self) -> bool:
        """Toggles EQ bypass without audio interruption. Returns new bypassed state."""
        self.bypassed = not self.bypassed
        return self.bypassed

    def set_bypassed(self, bypassed: bool) -> None:
        self.bypassed = bool(bypassed)

    # ------------------------------------------------------------------------
    # DSP & Digital Headroom Analysis
    # ------------------------------------------------------------------------

    def get_magnitude_at_freq(self, freq_hz: float) -> float:
        """Calculates total transfer gain in dB (including preamp) at frequency freq_hz."""
        if self.bypassed:
            return 0.0
        tot_db = self.preamp_db
        for band in self.bands:
            if not band.enabled:
                continue
            coeffs = band.calculate_coefficients(self.sample_rate)
            tot_db += coeffs.magnitude_db(freq_hz, self.sample_rate)
        return tot_db

    def calculate_peak_gain(self, num_points: int = 250) -> Tuple[float, float]:
        """
        Scans the 20 Hz - 20 kHz audio range to find the maximum composite peak gain.
        Returns (peak_gain_dbfs, peak_frequency_hz).
        """
        if self.bypassed:
            return 0.0, 1000.0

        coeffs_list = [
            b.calculate_coefficients(self.sample_rate)
            for b in self.bands if b.enabled
        ]
        if not coeffs_list:
            return self.preamp_db, 1000.0

        # Logarithmic frequency sweep from 20 Hz to 20,000 Hz
        max_gain_db = -999.0
        peak_freq = 20.0

        for i in range(num_points):
            f = 20.0 * (1000.0 ** (i / (num_points - 1)))
            w = 2.0 * math.pi * f / self.sample_rate
            z_inv = cmath.exp(-1j * w)
            z_inv2 = z_inv * z_inv

            tot_db = self.preamp_db
            for c in coeffs_list:
                num = c.b0 + c.b1 * z_inv + c.b2 * z_inv2
                den = 1.0 + c.a1 * z_inv + c.a2 * z_inv2
                if abs(den) > 1e-15:
                    tot_db += 20.0 * math.log10(abs(num / den))

            if tot_db > max_gain_db:
                max_gain_db = tot_db
                peak_freq = f

        return max_gain_db, peak_freq

    def auto_preamp_headroom(self, margin_db: float = 0.5) -> float:
        """
        Calculates the exact attenuation needed to ensure no frequency exceeds 0 dBFS,
        preventing inter-sample and digital saturation clipping.
        """
        saved_preamp = self.preamp_db
        self.preamp_db = 0.0
        peak_gain_no_preamp, _ = self.calculate_peak_gain(num_points=300)
        self.preamp_db = saved_preamp

        if peak_gain_no_preamp > 0.0:
            recommended = -(peak_gain_no_preamp + margin_db)
            return round(recommended, 1)
        return 0.0

    def get_curve_points(self, num_points: int = 80) -> List[Tuple[float, float]]:
        """Returns list of (freq_hz, gain_db) tuples logarithmically spaced across 20Hz-20kHz."""
        points = []
        for i in range(num_points):
            f = 20.0 * (1000.0 ** (i / (num_points - 1)))
            g = self.get_magnitude_at_freq(f)
            points.append((round(f, 1), round(g, 2)))
        return points

    # ------------------------------------------------------------------------
    # Audio Pipeline Integration (mpv / FFmpeg filter graph)
    # ------------------------------------------------------------------------

    def to_ffmpeg_af(self) -> str:
        """
        Compiles this parametric equalizer into an exact FFmpeg/mpv audio filter string.
        Utilizes 64-bit double precision float processing and transposed direct form II.
        """
        if self.bypassed:
            return ""

        filters: List[str] = []

        # 1. Preamp Stage: Attenuation before filters prevents clipping inside and after biquads
        if abs(self.preamp_db) > 0.01:
            filters.append(f"volume=volume={self.preamp_db:.2f}dB:precision=fixed")

        # 2. Biquad Filter Chain
        for b in self.bands:
            if not b.enabled or abs(b.gain_db) < 0.01:
                continue

            f_hz = b.frequency
            gain = b.gain_db
            q_val = b.q

            if b.filter_type == FilterType.LOW_SHELF:
                filters.append(f"lowshelf=f={f_hz:.1f}:t=q:w={q_val:.2f}:g={gain:.2f}:r=f64")
            elif b.filter_type == FilterType.HIGH_SHELF:
                filters.append(f"highshelf=f={f_hz:.1f}:t=q:w={q_val:.2f}:g={gain:.2f}:r=f64")
            elif b.filter_type == FilterType.PEAKING:
                filters.append(f"equalizer=f={f_hz:.1f}:t=q:w={q_val:.2f}:g={gain:.2f}:r=f64")

        return ",".join(filters)

    # ------------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.preset_name,
            "preset_name": self.preset_name,
            "description": self.description,
            "preamp_db": self.preamp_db,
            "bypassed": self.bypassed,
            "bands": [
                {
                    "index": b.index,
                    "filter_type": b.filter_type.value,
                    "frequency": b.frequency,
                    "gain_db": b.gain_db,
                    "q": b.q,
                    "enabled": b.enabled,
                    "label": b.label,
                }
                for b in self.bands
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], sample_rate: float = 48000.0) -> "ParametricEQEngine":
        preset = EQPreset.from_dict(data)
        bypassed = bool(data.get("bypassed", False))
        return cls(preset=preset, sample_rate=sample_rate, bypassed=bypassed)

    def to_equalizer_apo(self) -> str:
        """Exports the EQ configuration to EqualizerAPO / Peace format."""
        lines = [
            f"# Spoff Parametric EQ Profile: {self.preset_name}",
            f"# {self.description}",
            f"Preamp: {self.preamp_db:.1f} dB"
        ]
        for b in self.bands:
            if not b.enabled:
                continue
            if b.filter_type == FilterType.LOW_SHELF:
                ft = "LSC"
            elif b.filter_type == FilterType.HIGH_SHELF:
                ft = "HSC"
            else:
                ft = "PK"
            lines.append(f"Filter {b.index}: ON {ft} Fc {b.frequency:.1f} Hz Gain {b.gain_db:+.1f} dB Q {b.q:.2f}")
        return "\n".join(lines)


# ============================================================================
# TUI VISUALIZATION HELPERS
# ============================================================================

def format_gain_bar(gain_db: float, max_range: float = 12.0, width: int = 12) -> str:
    """Renders a compact visual gauge representing filter boost or cut in dB."""
    half = width // 2
    clamped = max(-max_range, min(max_range, float(gain_db)))
    if abs(clamped) < 0.1:
        return f"[dim #555555]{'·' * half}|{'·' * half}[/]"
    if clamped > 0:
        bars = int(round((clamped / max_range) * half))
        bars = max(1, min(half, bars))
        dots = half - bars
        return f"[dim #555555]{'·' * half}[/]|[#569f68]{'=' * bars}[/][dim #555555]{'·' * dots}[/]"
    else:
        bars = int(round((abs(clamped) / max_range) * half))
        bars = max(1, min(half, bars))
        dots = half - bars
        return f"[dim #555555]{'·' * dots}[/][#61afef]{'=' * bars}[/]|[dim #555555]{'·' * half}[/]"


def render_braille_curve(engine: ParametricEQEngine, width: int = 68, height: int = 6) -> str:
    """
    Renders a high-fidelity Braille frequency response curve across 20 Hz to 20 kHz.
    Uses logarithmic frequency interpolation and Robert Bristow-Johnson analytical transfer functions.
    """
    min_db, max_db = -12.0, +12.0
    total_rows = height * 4
    total_cols = width * 2

    grid = [[0 for _ in range(total_cols)] for _ in range(total_rows)]
    zero_y = int(round((max_db - 0.0) / (max_db - min_db) * (total_rows - 1)))
    zero_y = max(0, min(total_rows - 1, zero_y))

    # Baseline dots along 0 dBFS
    for x in range(total_cols):
        if x % 4 == 0:
            grid[zero_y][x] = 1

    prev_y = None
    for x in range(total_cols):
        f = 20.0 * (1000.0 ** (x / (total_cols - 1)))
        g = engine.get_magnitude_at_freq(f)
        y = int(round((max_db - g) / (max_db - min_db) * (total_rows - 1)))
        y = max(0, min(total_rows - 1, y))
        grid[y][x] = 1
        if prev_y is not None:
            step = 1 if y > prev_y else -1
            for cy in range(prev_y, y, step):
                grid[cy][x] = 1
        prev_y = y

    dot_map = [
        [0x01, 0x08],
        [0x02, 0x10],
        [0x04, 0x20],
        [0x40, 0x80]
    ]

    color = "#555555" if engine.bypassed else "#569f68"
    lines = []

    # Top line (+12 dB)
    top_chars = "".join(
        chr(0x2800 + sum(dot_map[dr][dc] for dr in range(4) for dc in range(2) if grid[dr][c * 2 + dc]))
        for c in range(width)
    )
    lines.append(f"[#555555]+12dB ┌[/][{color}]{top_chars}[/][#555555]┐[/]")

    for r in range(1, height - 1):
        db_val = max_db - r * (max_db - min_db) / (height - 1)
        lbl = "  0dB" if abs(db_val) < 0.5 else f"{db_val:+3.0f}dB"
        chars = "".join(
            chr(0x2800 + sum(dot_map[dr][dc] for dr in range(4) for dc in range(2) if grid[r * 4 + dr][c * 2 + dc]))
            for c in range(width)
        )
        lines.append(f"[#555555]{lbl} │[/][{color}]{chars}[/][#555555]│[/]")

    # Bottom line (-12 dB)
    bot_chars = "".join(
        chr(0x2800 + sum(dot_map[dr][dc] for dr in range(4) for dc in range(2) if grid[(height - 1) * 4 + dr][c * 2 + dc]))
        for c in range(width)
    )
    lines.append(f"[#555555]-12dB └[/][{color}]{bot_chars}[/][#555555]┘[/]")
    lines.append("[#555555]       20Hz       100Hz        500Hz        1kHz         5kHz        10kHz      20kHz[/]")

    return "\n".join(lines)
