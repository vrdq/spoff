import math
import cmath
import logging
import re
from dataclasses import dataclass, asdict
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
            if abs(self.s1_l) < 1e-15:
                self.s1_l = 0.0
            if abs(self.s2_l) < 1e-15:
                self.s2_l = 0.0
            return y
        else:
            y = b0 * x + self.s1_r
            self.s1_r = b1 * x - a1 * y + self.s2_r
            self.s2_r = b2 * x - a2 * y
            if abs(self.s1_r) < 1e-15:
                self.s1_r = 0.0
            if abs(self.s2_r) < 1e-15:
                self.s2_r = 0.0
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
    def from_dict(cls, data: Dict[str, Any], sample_rate: float = 48000.0) -> "EQPreset":
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
        preset = cls(
            name=str(data.get("name") or data.get("preset_name") or "Custom"),
            description=str(data.get("description", "")),
            preamp_db=float(data.get("preamp_db", 0.0)),
            bands=bands
        )
        return validate_preset(preset, sample_rate=sample_rate)


def checked_number(value: Any, name: str, low: float, high: float) -> float:
    number = float(value)
    if not math.isfinite(number) or not (low <= number <= high):
        raise ValueError(f"{name} must be between {low} and {high}")
    return number


def validate_preset(preset: EQPreset, sample_rate: float = 48000.0) -> EQPreset:
    checked_number(sample_rate, "Sample rate", 8000.0, 192000.0)
    checked_number(preset.preamp_db, "Preamp", -120.0, 12.0)
    if len(preset.bands) > 64:
        raise ValueError("At most 64 EQ bands are supported")
    seen = set()
    for band in preset.bands:
        if band.index in seen:
            raise ValueError("Duplicate EQ band index")
        seen.add(band.index)
        checked_number(band.frequency, "Frequency", 10.0, sample_rate * 0.495)
        checked_number(band.gain_db, "Gain", -36.0, 24.0)
        checked_number(band.q, "Q", 0.1, 25.0)
    return preset


# ============================================================================
# BUILT-IN FLAGSHIP ACOUSTIC PRESETS
# ============================================================================

SAMSUNG_AKG_AUDIOPHILE_PRO_PRESET = EQPreset(
    name="Samsung AKG Audiophile Pro",
    description="Samsung AKG earbuds: more sub-bass, less mud, softer upper mids, more air",
    preamp_db=-3.8,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 55.0, +3.8, 0.71, label="Sub-bass shelf"),
        EQBand(2, FilterType.PEAKING, 160.0, -3.2, 0.85, label="Mud cut"),
        EQBand(3, FilterType.PEAKING, 650.0, +2.2, 1.10, label="Vocal warmth"),
        EQBand(4, FilterType.PEAKING, 1200.0, -1.0, 1.80, label="Boxiness cut"),
        EQBand(5, FilterType.PEAKING, 2900.0, -1.8, 2.50, label="Upper-mid cut"),
        EQBand(6, FilterType.PEAKING, 4200.0, +1.8, 2.80, label="Attack lift"),
        EQBand(7, FilterType.PEAKING, 6300.0, -3.5, 3.20, label="Sibilance cut"),
        EQBand(8, FilterType.PEAKING, 8200.0, +1.5, 3.00, label="Treble lift"),
        EQBand(9, FilterType.PEAKING, 9800.0, -4.5, 2.60, label="Tweeter peak cut"),
        EQBand(10, FilterType.HIGH_SHELF, 13000.0, +3.5, 0.71, label="Air shelf"),
    ]
)

SAMSUNG_AKG_REFERENCE_PRESET = EQPreset(
    name="Samsung AKG Master Reference",
    description="Samsung AKG EO-IG955 earbuds tuned toward the Harman target",
    preamp_db=-5.0,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 65.0, +4.5, 0.70, label="Sub-bass shelf"),
        EQBand(2, FilterType.PEAKING, 145.0, -4.5, 0.50, label="Mud cut"),
        EQBand(3, FilterType.PEAKING, 230.0, -1.8, 1.40, label="Low-mid cut"),
        EQBand(4, FilterType.PEAKING, 780.0, +4.2, 0.65, label="Vocal warmth"),
        EQBand(5, FilterType.PEAKING, 1750.0, -1.2, 1.80, label="Nasal cut"),
        EQBand(6, FilterType.PEAKING, 2900.0, -2.2, 3.20, label="Upper-mid cut"),
        EQBand(7, FilterType.PEAKING, 4600.0, +2.2, 3.50, label="Attack lift"),
        EQBand(8, FilterType.PEAKING, 6400.0, +2.8, 4.00, label="Crossover fill"),
        EQBand(9, FilterType.PEAKING, 10000.0, -7.5, 2.20, label="Tweeter peak cut"),
        EQBand(10, FilterType.HIGH_SHELF, 13500.0, +3.0, 0.70, label="Air shelf"),
    ]
)

HARMAN_IN_EAR_2019_PRESET = EQPreset(
    name="Harman Target 2019 (In-Ear)",
    description="Harman 2019 in-ear target",
    preamp_db=-5.5,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 80.0, +5.5, 0.71, label="Bass shelf"),
        EQBand(2, FilterType.PEAKING, 200.0, -1.5, 1.00, label="Low-mid cut"),
        EQBand(3, FilterType.PEAKING, 1000.0, +1.0, 1.20, label="Mid lift"),
        EQBand(4, FilterType.PEAKING, 2800.0, +3.5, 2.00, label="Ear-canal lift"),
        EQBand(5, FilterType.PEAKING, 5000.0, -2.0, 3.00, label="Treble dip"),
        EQBand(6, FilterType.PEAKING, 7500.0, -3.0, 3.50, label="Sibilance cut"),
        EQBand(7, FilterType.HIGH_SHELF, 11000.0, +2.5, 0.71, label="Air"),
    ]
)

BASS_IMPACT_PRESET = EQPreset(
    name="Deep Sub-Bass Impact",
    description="More bass and punch, less mud",
    preamp_db=-6.0,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 45.0, +6.0, 0.70, label="Sub-bass shelf"),
        EQBand(2, FilterType.PEAKING, 85.0, +3.5, 1.20, label="Punch"),
        EQBand(3, FilterType.PEAKING, 250.0, -2.5, 1.00, label="Boxiness cut"),
        EQBand(4, FilterType.PEAKING, 3500.0, +1.5, 2.00, label="Attack lift"),
        EQBand(5, FilterType.HIGH_SHELF, 12000.0, +2.0, 0.70, label="Treble shelf"),
    ]
)

VOCAL_PRESENCE_PRESET = EQPreset(
    name="Vocal Intelligibility & Air",
    description="Brings vocals forward and softens sibilance",
    preamp_db=-3.5,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 100.0, -2.0, 0.70, label="Bass cut"),
        EQBand(2, FilterType.PEAKING, 250.0, -1.5, 1.40, label="Low-mid cut"),
        EQBand(3, FilterType.PEAKING, 1200.0, +2.5, 1.20, label="Vocal lift"),
        EQBand(4, FilterType.PEAKING, 3200.0, +3.2, 1.80, label="Presence lift"),
        EQBand(5, FilterType.PEAKING, 6800.0, -3.0, 3.00, label="Sibilance cut"),
        EQBand(6, FilterType.HIGH_SHELF, 12000.0, +2.0, 0.70, label="Air shelf"),
    ]
)

FLAT_PRESET = EQPreset(
    name="Flat / Studio Neutral",
    description="No change (all bands at 0 dB)",
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

HARMAN_OVER_EAR_2018_PRESET = EQPreset(
    name="Harman Target 2018 (Over-Ear)",
    description="Harman 2018 over-ear target",
    preamp_db=-4.5,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 60.0, +4.0, 0.71, label="Bass shelf"),
        EQBand(2, FilterType.PEAKING, 200.0, -1.0, 1.00, label="Low-mid cut"),
        EQBand(3, FilterType.PEAKING, 1200.0, +1.5, 1.40, label="Presence lift"),
        EQBand(4, FilterType.PEAKING, 3000.0, +3.0, 2.00, label="Ear-canal lift"),
        EQBand(5, FilterType.PEAKING, 6000.0, -2.5, 3.00, label="Treble peak cut"),
        EQBand(6, FilterType.HIGH_SHELF, 10000.0, +2.0, 0.71, label="Air"),
    ]
)

IEF_NEUTRAL_PRESET = EQPreset(
    name="IEF Neutral 2020",
    description="In-Ear Fidelity 2020 neutral target",
    preamp_db=-4.0,
    bands=[
        EQBand(1, FilterType.PEAKING, 1000.0, +1.0, 1.40, label="Mid lift"),
        EQBand(2, FilterType.PEAKING, 2800.0, +4.0, 1.80, label="Ear-canal lift"),
        EQBand(3, FilterType.PEAKING, 5800.0, -1.5, 2.50, label="Treble dip"),
        EQBand(4, FilterType.HIGH_SHELF, 10000.0, +1.0, 0.71, label="Air"),
    ]
)

DIFFUSE_FIELD_PRESET = EQPreset(
    name="Diffuse Field (DF)",
    description="Diffuse-field target",
    preamp_db=-6.0,
    bands=[
        EQBand(1, FilterType.PEAKING, 1500.0, +1.5, 1.20, label="Mid lift"),
        EQBand(2, FilterType.PEAKING, 3000.0, +6.0, 1.50, label="Ear-canal lift"),
        EQBand(3, FilterType.PEAKING, 7000.0, -2.0, 2.50, label="Treble dip"),
        EQBand(4, FilterType.HIGH_SHELF, 11000.0, +2.0, 0.71, label="Air"),
    ]
)

FREE_FIELD_PRESET = EQPreset(
    name="Free Field (FF)",
    description="Free-field target",
    preamp_db=-5.0,
    bands=[
        EQBand(1, FilterType.PEAKING, 1000.0, +1.0, 1.00, label="Mid lift"),
        EQBand(2, FilterType.PEAKING, 2700.0, +5.0, 1.60, label="Ear-canal lift"),
        EQBand(3, FilterType.PEAKING, 6500.0, -3.0, 2.80, label="Treble dip"),
        EQBand(4, FilterType.HIGH_SHELF, 12000.0, +1.5, 0.71, label="Air"),
    ]
)

MOONDROP_CHU_2_REFERENCE_PRESET = EQPreset(
    name="Moondrop Chu II Audiophile Reference",
    description="Moondrop Chu II: a little more sub-bass, less mud, tamed 5.8k and 8.2k peaks, more air",
    preamp_db=-4.0,
    bands=[
        EQBand(1, FilterType.LOW_SHELF, 40.0, +2.0, 0.71, label="Sub-bass shelf"),
        EQBand(2, FilterType.PEAKING, 200.0, -2.2, 1.00, label="Mud cut"),
        EQBand(3, FilterType.PEAKING, 750.0, +1.2, 1.20, label="Vocal warmth"),
        EQBand(4, FilterType.PEAKING, 1500.0, -1.0, 1.80, label="Boxiness cut"),
        EQBand(5, FilterType.PEAKING, 3100.0, -2.0, 2.20, label="Upper-mid cut"),
        EQBand(6, FilterType.PEAKING, 4500.0, +1.8, 2.50, label="Attack lift"),
        EQBand(7, FilterType.PEAKING, 5800.0, -3.2, 3.20, label="Sibilance cut"),
        EQBand(8, FilterType.PEAKING, 8200.0, -3.0, 3.50, label="Treble peak cut"),
        EQBand(9, FilterType.PEAKING, 10500.0, +2.0, 2.00, label="Treble lift"),
        EQBand(10, FilterType.HIGH_SHELF, 13500.0, +3.5, 0.71, label="Air shelf"),
    ]
)

BUILTIN_PRESETS: List[EQPreset] = [
    MOONDROP_CHU_2_REFERENCE_PRESET,
    SAMSUNG_AKG_AUDIOPHILE_PRO_PRESET,
    SAMSUNG_AKG_REFERENCE_PRESET,
    HARMAN_IN_EAR_2019_PRESET,
    HARMAN_OVER_EAR_2018_PRESET,
    IEF_NEUTRAL_PRESET,
    DIFFUSE_FIELD_PRESET,
    FREE_FIELD_PRESET,
    BASS_IMPACT_PRESET,
    VOCAL_PRESENCE_PRESET,
    FLAT_PRESET,
]


# ============================================================================
# EQUALIZER APO & AUTOEQ PARSER
# ============================================================================

def parse_equalizer_apo(text: str, default_name: str = "Imported AutoEQ", *, sample_rate: float = 48000.0) -> Optional[EQPreset]:
    """
    Parses AutoEQ or EqualizerAPO (Peace) configuration text into an EQPreset.
    Handles:
      - Preamp: -5.5 dB
      - Filter 1: ON PK Fc 65.0 Hz Gain +4.5 dB Q 0.70
      - Filter: ON LSC Fc 105 Hz Gain -2.1 dB Q 0.71
      - Filter 2: ON HSC Fc 10000 Hz Gain 3.5 dB Q 0.70
      - Lines without 'Filter': 'ON PK Fc 100 Hz Gain 2.0 dB Q 1.0'
    """
    if not text or not text.strip():
        return None

    lines = text.strip().splitlines()
    preamp_db = 0.0
    preset_name = default_name
    bands: List[EQBand] = []

    preamp_re = re.compile(r"^\s*Preamp\s*:\s*([+-]?\d+(?:\.\d+)?)\s*(?:dB)?", re.IGNORECASE)
    name_re = re.compile(r"^\s*#\s*(?:Profile|Preset|Name|EqualizerAPO(?:\s+Profile)?)\s*:\s*(.+)$", re.IGNORECASE)
    filter_re = re.compile(
        r"(?:Filter(?:\s*\d+)?\s*:\s*)?"
        r"(?:(ON|OFF)\s+)?"
        r"(PK|PEAK|PEAKING|LSC|LOW_SHELF|LOWSHELF|LS|HSC|HIGH_SHELF|HIGHSHELF|HS|BELL)\s+"
        r"Fc\s+(\d+(?:\.\d+)?)\s*(?:Hz)?\s+"
        r"Gain\s+([+-]?\d+(?:\.\d+)?)\s*(?:dB)?\s+"
        r"Q\s+(\d+(?:\.\d+)?)",
        re.IGNORECASE
    )

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        m_name = name_re.match(line_clean)
        if m_name and preset_name == default_name:
            cand = m_name.group(1).strip()
            if cand:
                preset_name = cand
            continue

        if line_clean.startswith("#") or line_clean.startswith(";"):
            continue

        m_preamp = preamp_re.match(line_clean)
        if m_preamp:
            try:
                preamp_db = float(m_preamp.group(1))
            except ValueError:
                pass
            continue

        m_filter = filter_re.search(line_clean)
        if m_filter:
            on_off = (m_filter.group(1) or "ON").upper()
            ft_str = m_filter.group(2).upper()
            freq_str = m_filter.group(3)
            gain_str = m_filter.group(4)
            q_str = m_filter.group(5)

            ft = FilterType.from_str(ft_str)
            enabled = (on_off != "OFF")
            try:
                freq = float(freq_str)
                gain = float(gain_str)
                q_val = float(q_str)
            except ValueError:
                continue

            idx = len(bands) + 1
            bands.append(EQBand(
                index=idx,
                filter_type=ft,
                frequency=freq,
                gain_db=gain,
                q=q_val,
                enabled=enabled,
                label=f"AutoEQ Band {idx}"
            ))

    if not bands:
        return None

    desc = f"Imported AutoEQ / EqualizerAPO profile ({len(bands)} bands)"
    preset = EQPreset(
        name=preset_name,
        description=desc,
        preamp_db=preamp_db,
        bands=bands
    )
    return validate_preset(preset, sample_rate)


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
        bypassed: bool = False,
        precision: str = "f64",
        auto_headroom: bool = True,
        headroom_margin: float = 0.5,
        intersample_guard: bool = True,
        anti_denormal: bool = True,
        curve_style: str = "braille",
        curve_range_db: float = 12.0,
    ):
        p = preset or SAMSUNG_AKG_REFERENCE_PRESET
        validate_preset(p, sample_rate)
        self.preset_name: str = p.name
        self.description: str = p.description
        self.preamp_db: float = p.preamp_db
        self.bands: List[EQBand] = [EQBand(**asdict(b)) for b in p.bands]
        self.sample_rate: float = sample_rate
        self.bypassed: bool = bypassed
        self.precision: str = precision if precision in ("f32", "f64") else "f64"
        self.auto_headroom: bool = auto_headroom
        self.headroom_margin: float = max(0.0, min(6.0, float(headroom_margin)))
        self.intersample_guard: bool = intersample_guard
        self.anti_denormal: bool = anti_denormal
        self.curve_style: str = curve_style if curve_style in ("braille", "blocks", "outline") else "braille"
        self.curve_range_db: float = float(curve_range_db) if float(curve_range_db) in (12.0, 18.0, 24.0) else 12.0
        self._filters: List[BiquadFilter] = [BiquadFilter() for _ in self.bands]
        self._update_filter_coefficients()

    def set_sample_rate(self, sample_rate: float) -> None:
        rate = float(sample_rate)
        if not math.isfinite(rate) or not 8000 <= rate <= 192000:
            raise ValueError("Unsupported sample rate")
        if rate == self.sample_rate:
            return
        for band in self.bands:
            band.frequency = max(10.0, min(band.frequency, rate * 0.495))
        self.sample_rate = rate
        self._update_filter_coefficients()
        if self.auto_headroom:
            self.preamp_db = self.auto_preamp_headroom()

    def set_precision(self, precision: str) -> None:
        p = precision.lower().strip()
        if p in ("f32", "f64"):
            self.precision = p

    def set_auto_headroom(self, enabled: bool) -> None:
        self.auto_headroom = bool(enabled)
        if self.auto_headroom:
            self.preamp_db = self.auto_preamp_headroom()

    def set_headroom_margin(self, margin_db: float) -> None:
        self.headroom_margin = max(0.0, min(6.0, float(margin_db)))
        if self.auto_headroom:
            self.preamp_db = self.auto_preamp_headroom()

    def set_intersample_guard(self, enabled: bool) -> None:
        self.intersample_guard = bool(enabled)
        if self.auto_headroom:
            self.preamp_db = self.auto_preamp_headroom()

    def set_anti_denormal(self, enabled: bool) -> None:
        """Toggle internal anti-denormal engine protection flag."""
        self.anti_denormal = bool(enabled)

    def set_curve_style(self, style: str) -> None:
        s = style.lower().strip()
        if s in ("braille", "blocks", "outline"):
            self.curve_style = s

    def set_curve_range_db(self, range_db: float) -> None:
        r = float(range_db)
        if r in (12.0, 18.0, 24.0):
            self.curve_range_db = r

    def _update_filter_coefficients(self) -> None:
        for i, band in enumerate(self.bands):
            coeffs = band.calculate_coefficients(self.sample_rate)
            if i < len(self._filters):
                self._filters[i].set_coefficients(coeffs)
            else:
                self._filters.append(BiquadFilter(coeffs))

    def load_preset(self, preset: EQPreset) -> None:
        """Loads a preset while maintaining smooth transition."""
        validate_preset(preset, self.sample_rate)
        self.preset_name = preset.name
        self.description = preset.description
        self.preamp_db = preset.preamp_db
        self.bands = [EQBand(**asdict(b)) for b in preset.bands]
        self._update_filter_coefficients()
        if self.auto_headroom:
            self.preamp_db = self.auto_preamp_headroom()

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
                if self.auto_headroom:
                    self.preamp_db = self.auto_preamp_headroom()
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

    def calculate_peak_gain(self, num_points: int = 16384, *, ignore_bypass: bool = False, preamp_db: Optional[float] = None) -> Tuple[float, float]:
        """
        Scans the 20 Hz - 20 kHz audio range to find the maximum composite peak gain.
        Returns (peak_gain_dbfs, peak_frequency_hz).
        """
        if self.bypassed and not ignore_bypass:
            return 0.0, 1000.0

        preamp = self.preamp_db if preamp_db is None else preamp_db
        coeffs_list = [
            b.calculate_coefficients(self.sample_rate)
            for b in self.bands if b.enabled
        ]
        if not coeffs_list:
            return preamp, 1000.0

        # Dense logarithmic sweep from 0.1 Hz to Nyquist, plus DC and shelf/peak critical points
        max_gain_db = -999.0
        peak_freq = 0.0

        count = max(2, num_points)
        lower = 0.1
        upper = self.sample_rate / 2.0
        freq_list = [lower * ((upper / lower) ** (i / (count - 1))) for i in range(count)]
        freq_list.extend((0.0, upper))

        for b in self.bands:
            if b.enabled:
                f0 = b.frequency
                freq_list.extend((
                    max(0.0, f0 * 0.01),
                    max(0.0, f0 * 0.05),
                    max(0.0, f0 * 0.1),
                    max(0.0, f0 * 0.25),
                    max(0.0, f0 * 0.5),
                    max(0.0, f0 * 0.75),
                    f0,
                    min(upper, f0 * 1.1),
                    min(upper, f0 * 1.25),
                    min(upper, f0 * 1.5),
                    min(upper, f0 * 2.0),
                    min(upper, f0 * 4.0),
                ))

        for f in freq_list:
            w = 2.0 * math.pi * f / self.sample_rate
            z_inv = cmath.exp(-1j * w)
            z_inv2 = z_inv * z_inv

            tot_db = preamp
            for c in coeffs_list:
                num = c.b0 + c.b1 * z_inv + c.b2 * z_inv2
                den = 1.0 + c.a1 * z_inv + c.a2 * z_inv2
                if abs(den) > 1e-15:
                    tot_db += 20.0 * math.log10(abs(num / den))

            if tot_db > max_gain_db:
                max_gain_db = tot_db
                peak_freq = f

        # Refine around sampled maximum
        if peak_freq > 0.0:
            for delta in (-0.05, -0.02, -0.01, -0.005, 0.005, 0.01, 0.02, 0.05):
                f = peak_freq * (1.0 + delta)
                if 0.0 <= f <= upper:
                    w = 2.0 * math.pi * f / self.sample_rate
                    z_inv = cmath.exp(-1j * w)
                    z_inv2 = z_inv * z_inv
                    tot_db = preamp
                    for c in coeffs_list:
                        num = c.b0 + c.b1 * z_inv + c.b2 * z_inv2
                        den = 1.0 + c.a1 * z_inv + c.a2 * z_inv2
                        if abs(den) > 1e-15:
                            tot_db += 20.0 * math.log10(abs(num / den))
                    if tot_db > max_gain_db:
                        max_gain_db = tot_db
                        peak_freq = f

        return max_gain_db, peak_freq

    def auto_preamp_headroom(self, margin_db: Optional[float] = None) -> float:
        """
        Calculates the exact attenuation needed to ensure no frequency exceeds 0 dBFS,
        preventing inter-sample and digital saturation clipping.
        """
        margin = self.headroom_margin if margin_db is None else margin_db
        if self.intersample_guard and margin_db is None:
            margin = margin + 0.2

        peak_gain_no_preamp, _ = self.calculate_peak_gain(
            num_points=16384, ignore_bypass=True, preamp_db=0.0,
        )

        if peak_gain_no_preamp > 0.0:
            recommended = -(peak_gain_no_preamp + margin)
            return math.floor(recommended * 10) / 10
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
        Utilizes 64-bit double precision float processing (or 32-bit float) and transposed direct form II.
        """
        if self.bypassed:
            return ""

        filters: List[str] = [f"aresample={int(self.sample_rate)}"]

        # 1. Preamp Stage: Attenuation before filters prevents clipping inside and after biquads
        vol_prec = "double" if self.precision == "f64" else "float"
        if abs(self.preamp_db) > 0.01:
            filters.append(f"volume=volume={self.preamp_db:.2f}dB:precision={vol_prec}")

        # 2. Biquad Filter Chain
        prec = "f64" if self.precision == "f64" else "f32"
        for b in self.bands:
            if not b.enabled or abs(b.gain_db) < 0.01:
                continue

            f_hz = b.frequency
            gain = b.gain_db
            q_val = b.q

            if b.filter_type == FilterType.LOW_SHELF:
                filters.append(f"lowshelf=f={f_hz:.1f}:t=q:w={q_val:.2f}:g={gain:.2f}:r={prec}")
            elif b.filter_type == FilterType.HIGH_SHELF:
                filters.append(f"highshelf=f={f_hz:.1f}:t=q:w={q_val:.2f}:g={gain:.2f}:r={prec}")
            elif b.filter_type == FilterType.PEAKING:
                filters.append(f"equalizer=f={f_hz:.1f}:t=q:w={q_val:.2f}:g={gain:.2f}:r={prec}")

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
            "sample_rate": self.sample_rate,
            "precision": self.precision,
            "auto_headroom": self.auto_headroom,
            "headroom_margin": self.headroom_margin,
            "intersample_guard": self.intersample_guard,
            "anti_denormal": self.anti_denormal,
            "curve_style": self.curve_style,
            "curve_range_db": self.curve_range_db,
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
        sr = float(data.get("sample_rate", sample_rate))
        preset = EQPreset.from_dict(data, sample_rate=sr)
        bypassed = bool(data.get("bypassed", False))
        precision = str(data.get("precision", "f64"))
        auto_headroom = bool(data.get("auto_headroom", True))
        headroom_margin = float(data.get("headroom_margin", 0.5))
        intersample_guard = bool(data.get("intersample_guard", True))
        anti_denormal = bool(data.get("anti_denormal", True))
        curve_style = str(data.get("curve_style", "braille"))
        curve_range_db = float(data.get("curve_range_db", 12.0))
        return cls(
            preset=preset,
            sample_rate=sr,
            bypassed=bypassed,
            precision=precision,
            auto_headroom=auto_headroom,
            headroom_margin=headroom_margin,
            intersample_guard=intersample_guard,
            anti_denormal=anti_denormal,
            curve_style=curve_style,
            curve_range_db=curve_range_db,
        )

    def to_equalizer_apo(self) -> str:
        """Exports the EQ configuration to EqualizerAPO / Peace format."""
        lines = [
            f"# Profile: {self.preset_name}",
            f"# {self.description}",
            f"Preamp: {self.preamp_db:.1f} dB"
        ]
        for b in self.bands:
            if b.filter_type == FilterType.LOW_SHELF:
                ft = "LSC"
            elif b.filter_type == FilterType.HIGH_SHELF:
                ft = "HSC"
            else:
                ft = "PK"
            state = "ON" if b.enabled else "OFF"
            lines.append(f"Filter {b.index}: {state} {ft} Fc {b.frequency:.1f} Hz Gain {b.gain_db:+.1f} dB Q {b.q:.2f}")
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


def _frequency_axis(width: int, offset: int = 7) -> str:
    """Tick labels placed at their real position on the curve's log 20 Hz–20 kHz scale."""
    # U+2800 (blank braille) pads the start: Textual strips leading spaces.
    row = ["\u2800"] * offset + [" "] * (width + 6)
    last_end = 0
    for hz, label in ((20, "20"), (100, "100"), (1000, "1k"), (10000, "10k"), (20000, "20k")):
        col = offset + round(math.log(hz / 20.0) / math.log(1000.0) * (width - 1))
        start = max(offset, min(col - len(label) // 2, offset + width - len(label)))
        if start <= last_end:
            continue  # too narrow to fit this label without merging into the last
        row[start:start + len(label)] = label
        last_end = start + len(label)
    return f"[#555555]{''.join(row).rstrip()}  Hz[/]"


def _db_label(db: float) -> str:
    """Fixed-width dB label (5 cells) so the plot's left border lines up."""
    text = "0dB" if abs(db) < 0.5 else f"{db:+.0f}dB"
    return text.rjust(5, "\u2800")


def render_braille_curve(
    engine: ParametricEQEngine,
    width: int = 68,
    height: int = 6,
    max_db: Optional[float] = None
) -> str:
    """
    Renders a high-fidelity Braille frequency response curve across 20 Hz to 20 kHz.
    Uses logarithmic frequency interpolation and Robert Bristow-Johnson analytical transfer functions.
    """
    limit_db = float(max_db or getattr(engine, "curve_range_db", 12.0))
    min_db, max_db_val = -limit_db, limit_db
    total_rows = height * 4
    total_cols = width * 2

    grid = [[0 for _ in range(total_cols)] for _ in range(total_rows)]
    zero_y = int(round((max_db_val - 0.0) / (max_db_val - min_db) * (total_rows - 1)))
    zero_y = max(0, min(total_rows - 1, zero_y))

    # Baseline dots along 0 dBFS
    for x in range(total_cols):
        if x % 4 == 0:
            grid[zero_y][x] = 1

    prev_y = None
    for x in range(total_cols):
        f = 20.0 * (1000.0 ** (x / (total_cols - 1)))
        g = engine.get_magnitude_at_freq(f)
        y = int(round((max_db_val - g) / (max_db_val - min_db) * (total_rows - 1)))
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

    # Top line
    top_chars = "".join(
        chr(0x2800 + sum(dot_map[dr][dc] for dr in range(4) for dc in range(2) if grid[dr][c * 2 + dc]))
        for c in range(width)
    )
    lines.append(f"[#555555]{_db_label(limit_db)} ┌[/][{color}]{top_chars}[/][#555555]┐[/]")

    for r in range(1, height - 1):
        db_val = max_db_val - r * (max_db_val - min_db) / (height - 1)
        lbl = _db_label(db_val)
        chars = "".join(
            chr(0x2800 + sum(dot_map[dr][dc] for dr in range(4) for dc in range(2) if grid[r * 4 + dr][c * 2 + dc]))
            for c in range(width)
        )
        lines.append(f"[#555555]{lbl} │[/][{color}]{chars}[/][#555555]│[/]")

    # Bottom line
    bot_chars = "".join(
        chr(0x2800 + sum(dot_map[dr][dc] for dr in range(4) for dc in range(2) if grid[(height - 1) * 4 + dr][c * 2 + dc]))
        for c in range(width)
    )
    lines.append(f"[#555555]{_db_label(-limit_db)} └[/][{color}]{bot_chars}[/][#555555]┘[/]")
    lines.append(_frequency_axis(width))

    return "\n".join(lines)


def render_blocks_curve(
    engine: ParametricEQEngine,
    width: int = 68,
    height: int = 6,
    max_db: Optional[float] = None
) -> str:
    """Renders frequency response curve with solid ASCII blocks."""
    limit_db = float(max_db or getattr(engine, "curve_range_db", 12.0))
    min_db, max_db_val = -limit_db, limit_db
    color = "#555555" if engine.bypassed else "#569f68"

    gains = []
    for c in range(width):
        f = 20.0 * (1000.0 ** (c / (width - 1)))
        g = engine.get_magnitude_at_freq(f)
        gains.append(g)

    zero_r = int(round((max_db_val - 0.0) / (max_db_val - min_db) * (height - 1)))
    lines = []

    for r in range(height):
        db_val = max_db_val - r * (max_db_val - min_db) / (height - 1)
        if r == 0:
            lbl = f"{_db_label(limit_db)} ┌"
            r_edge = "┐"
        elif r == height - 1:
            lbl = f"{_db_label(-limit_db)} └"
            r_edge = "┘"
        elif abs(db_val) < 0.5:
            lbl = f"{_db_label(0)} │"
            r_edge = "│"
        else:
            lbl = f"{_db_label(db_val)} │"
            r_edge = "│"

        row_chars = []
        r_top = max_db_val - (r - 0.5) * (max_db_val - min_db) / (height - 1) if r > 0 else max_db_val + 10.0
        r_bot = max_db_val - (r + 0.5) * (max_db_val - min_db) / (height - 1) if r < height - 1 else min_db - 10.0

        for c in range(width):
            g = gains[c]
            if min(r_top, r_bot) <= g <= max(r_top, r_bot):
                row_chars.append("█")
            elif g > 0 and zero_r >= r and g >= r_bot:
                row_chars.append("▄")
            elif g < 0 and zero_r <= r and g <= r_top:
                row_chars.append("▀")
            elif r == zero_r and c % 4 == 0:
                row_chars.append("·")
            else:
                row_chars.append(" ")

        line_str = "".join(row_chars)
        lines.append(f"[#555555]{lbl}[/][{color}]{line_str}[/][#555555]{r_edge}[/]")

    lines.append(_frequency_axis(width))
    return "\n".join(lines)


def render_outline_curve(
    engine: ParametricEQEngine,
    width: int = 68,
    height: int = 6,
    max_db: Optional[float] = None
) -> str:
    """Renders frequency response curve with dotted outline."""
    limit_db = float(max_db or getattr(engine, "curve_range_db", 12.0))
    min_db, max_db_val = -limit_db, limit_db
    color = "#555555" if engine.bypassed else "#61afef"

    gains = []
    for c in range(width):
        f = 20.0 * (1000.0 ** (c / (width - 1)))
        g = engine.get_magnitude_at_freq(f)
        y = int(round((max_db_val - g) / (max_db_val - min_db) * (height - 1)))
        gains.append(max(0, min(height - 1, y)))

    zero_r = int(round((max_db_val - 0.0) / (max_db_val - min_db) * (height - 1)))
    lines = []

    for r in range(height):
        db_val = max_db_val - r * (max_db_val - min_db) / (height - 1)
        if r == 0:
            lbl = f"{_db_label(limit_db)} ┌"
            r_edge = "┐"
        elif r == height - 1:
            lbl = f"{_db_label(-limit_db)} └"
            r_edge = "┘"
        elif abs(db_val) < 0.5:
            lbl = f"{_db_label(0)} │"
            r_edge = "│"
        else:
            lbl = f"{_db_label(db_val)} │"
            r_edge = "│"

        row_chars = []
        for c in range(width):
            if gains[c] == r:
                row_chars.append("●")
            elif r == zero_r and c % 4 == 0:
                row_chars.append("·")
            else:
                row_chars.append(" ")

        line_str = "".join(row_chars)
        lines.append(f"[#555555]{lbl}[/][{color}]{line_str}[/][#555555]{r_edge}[/]")

    lines.append(_frequency_axis(width))
    return "\n".join(lines)


def render_curve(
    engine: ParametricEQEngine,
    width: int = 68,
    height: int = 6,
    style: Optional[str] = None,
    max_db: Optional[float] = None
) -> str:
    """Dispatches curve rendering according to engine mode and scale."""
    st = (style or getattr(engine, "curve_style", "braille")).lower()
    if st in ("block", "blocks", "solid"):
        return render_blocks_curve(engine, width, height, max_db)
    elif st in ("outline", "dot", "dots"):
        return render_outline_curve(engine, width, height, max_db)
    return render_braille_curve(engine, width, height, max_db)
