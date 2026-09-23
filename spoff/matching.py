"""Conservative recording metadata checks shared by service integrations."""
import math
import re
import unicodedata
from typing import Any, Dict

def _seconds(value: Any) -> float:
    try:
        if isinstance(value, str) and ":" in value:
            result = 0.0
            for part in value.split(":"):
                result = result * 60 + float(part)
        else:
            result = float(value or 0)
        return result if math.isfinite(result) and result > 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"[\[(](?:official(?: music)? (?:video|audio)|lyrics?|visualizer|audio)[\])]", " ", value)
    value = re.sub(r"\s+official(?: music)? (?:video|audio)$", " ", value)
    return " ".join(re.findall(r"[^\W_]+", value))


def _matches_recording(item: Dict[str, Any], title: str, artist: str, duration: float) -> bool:
    """Reject substitutions; metadata similarity is not proof of audio identity."""
    candidate_title = str(item.get("track") or item.get("title") or "")
    artists = item.get("artists") or []
    candidate_artists = [a.get("name", "") if isinstance(a, dict) else str(a) for a in artists]
    candidate_artists.extend(str(item.get(k) or "") for k in ("artist", "uploader", "channel"))
    requested_artists = [_normalized_name(a) for a in artist.split(",") if a.strip()]
    # Video uploads often put the performer in the title rather than artist tags.
    if " - " in candidate_title:
        prefix, rest = candidate_title.split(" - ", 1)
        if _normalized_name(prefix) in requested_artists:
            candidate_artists.append(prefix)
            candidate_title = rest
    def title_without_known_credits(value, credits):
        # Providers move featured performers between title and artist fields.
        # Remove only explicitly credited names; keep every version qualifier.
        def replace(match):
            names = re.split(r"\s*(?:,|&|\band\b)\s*", match.group(1), flags=re.I)
            if names and all(_normalized_name(name) in credits for name in names):
                return " "
            return match.group(0)

        return re.sub(r"[\[(](?:feat\.?|ft\.?|featuring)\s+([^\[\]()]+)[\])]",
                      replace, value, flags=re.I)

    normalized_title = title_without_known_credits(title, {
        _normalized_name(a) for a in candidate_artists if a
    })
    candidate_title = title_without_known_credits(candidate_title, set(requested_artists))
    if _normalized_name(candidate_title) != _normalized_name(normalized_title):
        return False
    if not requested_artists or not any(
        _normalized_name(re.sub(r"(?i)\s*-\s*topic$", "", a)) in requested_artists
        for a in candidate_artists if a
    ):
        return False
    candidate_duration = _seconds(item.get("duration_seconds") or item.get("duration"))
    if duration and (not candidate_duration or abs(candidate_duration - duration) > max(8.0, duration * 0.04)):
        return False
    return True

