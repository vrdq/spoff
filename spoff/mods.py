"""Song mods: slowed + reverb, sped up, nightcore, bass boost, 8D.

A mod is four settings: speed (tape-style, so pitch follows it), reverb
(convolution with one of the bundled impulse responses), bass boost and 8D
panning. While the mod menu is open the player applies them live; saving
renders the modded song with ffmpeg into the cache as a local-only track.
"""
import hashlib
import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .storage import DATA_DIR, CACHE_DIR, _atomic_json_dump, register_cached_track
except ImportError:
    from storage import DATA_DIR, CACHE_DIR, _atomic_json_dump, register_cached_track  # type: ignore

logger = logging.getLogger("mods")

IR_DIR = Path(__file__).resolve().parent / "assets" / "ir"
MODDED_FILE = DATA_DIR / "modded_tracks.json"
RATE = 48000

# Speed and bass steps the sliders move through.
SPEEDS = (0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4)
BASS_STEPS = (0, 2, 4, 6, 8, 10, 12)
REVERBS = ("off", "small", "big", "hall")
REVERB_LABELS = {"off": "off", "small": "small room", "big": "big room", "hall": "hall"}
# afir outputs only the reverberated signal, so it's mixed back under the dry
# song. These weights put the reverb about 12 / 9 / 6 dB below the dry signal
# (measured with the bundled impulse responses, whose loudness differs).
REVERB_WET = {"small": 0.19, "big": 0.42, "hall": 0.81}


def _reverb_graph(source: str, ir: str, out: str, wet: float) -> str:
    """Filter graph text that adds reverb under the dry signal."""
    return (f"{source}asplit[dry][send];[send]{ir}afir=dry=10:wet=10[wet];"
            f"[dry][wet]amix=inputs=2:weights=1 {wet}:normalize=0:duration=first{out}")


@dataclass(frozen=True)
class Mod:
    speed: float = 1.0
    reverb: str = "off"
    bass: int = 0
    eight_d: bool = False

    @property
    def is_original(self) -> bool:
        return self == Mod()


PRESETS: List[Tuple[str, Optional[Mod]]] = [
    ("Original", Mod()),
    ("Slowed + reverb", Mod(speed=0.85, reverb="hall", bass=2)),
    ("Sped up", Mod(speed=1.2)),
    ("Nightcore", Mod(speed=1.3, bass=2)),
    ("Bass boosted", Mod(bass=8)),
    ("8D", Mod(reverb="small", eight_d=True)),
    ("Custom", None),  # whatever the sliders say
]


def describe(mod: Mod) -> str:
    """Short lowercase label for titles, e.g. 'slowed + reverb' or 'custom'."""
    for name, preset in PRESETS:
        if preset is not None and preset == mod and name != "Original":
            return name.lower()
    parts = []
    if mod.speed < 1.0:
        parts.append("slowed")
    elif mod.speed > 1.0:
        parts.append("sped up")
    if mod.reverb != "off":
        parts.append("reverb")
    if mod.bass:
        parts.append("bass boosted")
    if mod.eight_d:
        parts.append("8D")
    return " + ".join(parts) or "original"


def step(values: tuple, current: Any, direction: int) -> Any:
    """The next value along a slider, clamped at both ends."""
    try:
        idx = values.index(current)
    except ValueError:
        idx = min(range(len(values)), key=lambda i: abs(values[i] - current)) if isinstance(current, (int, float)) else 0
    return values[max(0, min(len(values) - 1, idx + direction))]


def _escape_graph_path(path: Path) -> str:
    # Inside an ffmpeg filter graph these characters end an option value.
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,") \
        .replace("[", "\\[").replace("]", "\\]").replace(";", "\\;")


def _tone_filters(mod: Mod) -> List[str]:
    """Bass and 8D, identical live and when rendering."""
    parts = []
    if mod.bass:
        parts.append(f"bass=g={mod.bass}:f=100:w=0.7")
    if mod.eight_d:
        parts.append("apulsator=hz=0.12:amount=0.85:level_out=1.5")  # panning dips the level ~3 dB
    return parts


def live_filters(mod: Mod) -> List[str]:
    """mpv audio filters for the mod (speed is handled by mpv's speed property)."""
    parts = _tone_filters(mod)
    if mod.reverb in REVERB_WET:
        ir = _escape_graph_path(IR_DIR / f"{mod.reverb}.wav")
        graph = _reverb_graph(f"aresample={RATE},", "[ir]", "", REVERB_WET[mod.reverb])
        parts.append(f"lavfi=[amovie={ir}[ir];{graph}]")
    if parts:
        parts.append("alimiter=limit=0.95")  # boosts and reverb tails can clip
    return parts


def _encoder() -> Tuple[List[str], str]:
    """Opus when ffmpeg has libopus; FLAC otherwise (lossless, just bigger)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    if "libopus" in out:
        return ["-c:a", "libopus", "-b:a", "256k"], ".opus"
    return ["-c:a", "flac"], ".flac"


def render_command(src: Path, dst: Path, mod: Mod, codec: List[str]) -> List[str]:
    """The ffmpeg command that renders the modded song."""
    chain = [f"aresample={RATE}"]
    if mod.speed != 1.0:
        chain += [f"asetrate={RATE}*{mod.speed}", f"aresample={RATE}"]
    chain += _tone_filters(mod)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
    if mod.reverb in REVERB_WET:
        cmd += ["-i", str(IR_DIR / f"{mod.reverb}.wav")]
        graph = _reverb_graph(f"[0:a]{','.join(chain)},", "[1:a]", ",alimiter=limit=0.95[out]",
                              REVERB_WET[mod.reverb])
    else:
        tail = ",alimiter=limit=0.95" if len(chain) > 1 else ""
        graph = f"[0:a]{','.join(chain)}{tail}[out]"
    return cmd + ["-filter_complex", graph, "-map", "[out]", "-vn", *codec, str(dst)]


def mod_id(original_id: str, mod: Mod) -> str:
    key = json.dumps({"of": original_id, **asdict(mod)}, sort_keys=True)
    return "mod_" + hashlib.sha1(key.encode()).hexdigest()[:16]


def load_modded() -> List[Dict[str, Any]]:
    try:
        data = json.loads(MODDED_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("id")] if isinstance(data, list) else []


def _save_modded(tracks: List[Dict[str, Any]]) -> None:
    _atomic_json_dump(MODDED_FILE, tracks)


def render(original: Dict[str, Any], src: Path, mod: Mod) -> Dict[str, Any]:
    """Renders the modded song into the cache and records it. Returns the new track.

    Raises RuntimeError with a message fit for the user when ffmpeg fails.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Saving a mod needs ffmpeg installed")
    original_id = str(original.get("id") or "")
    new_id = mod_id(original_id, mod)
    codec, ext = _encoder()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dst = CACHE_DIR / f"{new_id}{ext}"
    part = dst.with_name(dst.stem + ".part" + ext)
    try:
        result = subprocess.run(render_command(src, part, mod, codec), capture_output=True, text=True,
                                timeout=900)
    except subprocess.TimeoutExpired as exc:
        part.unlink(missing_ok=True)
        raise RuntimeError("Rendering took too long") from exc
    if result.returncode != 0 or not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        logger.warning("ffmpeg mod render failed: %s", result.stderr.strip()[-500:])
        raise RuntimeError("ffmpeg couldn't render this song")
    part.replace(dst)

    try:
        duration = int(float(original.get("duration_ms") or 0) / mod.speed)
    except (TypeError, ValueError, ZeroDivisionError):
        duration = 0
    label = describe(mod)
    track = {
        "id": new_id,
        "title": f"{original.get('title') or 'Unknown'} ({label})",
        "artist": original.get("artist") or "Unknown",
        "album": original.get("album") or "",
        "duration_ms": duration,
        # Local-only: "local" tracks never sync to Spotify or YouTube Music.
        "source": "local",
        "mod_of": original_id,
        "mod": asdict(mod),
    }
    for key in ("art_url", "album_art_url", "thumbnail"):
        if original.get(key):
            track[key] = original[key]
    register_cached_track(new_id, track, dst)
    tracks = [t for t in load_modded() if t.get("id") != new_id]
    tracks.insert(0, track)
    _save_modded(tracks)
    return track


def delete_modded(track_id: str) -> None:
    _save_modded([t for t in load_modded() if t.get("id") != track_id])


def with_speed(mod: Mod, direction: int) -> Mod:
    return replace(mod, speed=step(SPEEDS, mod.speed, direction))


def with_reverb(mod: Mod, direction: int) -> Mod:
    return replace(mod, reverb=step(REVERBS, mod.reverb, direction))


def with_bass(mod: Mod, direction: int) -> Mod:
    return replace(mod, bass=step(BASS_STEPS, mod.bass, direction))


def with_eight_d(mod: Mod, direction: int) -> Mod:
    return replace(mod, eight_d=direction > 0)
