"""Conservative recording metadata checks shared by service integrations."""
import math
import re
import unicodedata
from typing import Any, Dict, List


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


def _duration_seconds(value: Any) -> float:
    """Safely converts duration to seconds.
    Handles None, numeric seconds (e.g. 214.5), numeric ms (e.g. 214000),
    and strings formatted as 'MM:SS' or 'HH:MM:SS'.
    """
    if value is None:
        return 0.0
    if isinstance(value, str) and ":" in value:
        return _seconds(value)
    try:
        val = float(value)
        if not math.isfinite(val) or val <= 0:
            return 0.0
        if val > 1000:
            return val / 1000.0
        return val
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _split_artists(value: str) -> List[str]:
    """Splits multi-artist strings while preserving primary artist names."""
    if not value or not isinstance(value, str):
        return []
    parts = re.split(r"(?i)\s*(?:[,;/]|\b(?:featuring|with|feat|ft)\b\.?)\s*", value)
    results: List[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        results.append(part)
        subparts = re.split(r"(?i)\s*(?:[&]|\b(?:and|x|vs)\b\.?)\s*", part)
        if len(subparts) > 1:
            for sp in subparts:
                sp = sp.strip()
                if sp and sp not in results:
                    results.append(sp)
    return results


def _normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # Strip video/audio descriptors in brackets or parentheses
    value = re.sub(
        r"[\[(]\s*(?:official\s+)?(?:music\s+video|music\s+audio|lyric\s+video|lyrics?|visualizer|audio|video|track|stream|hd|4k|hq|full\s+audio|official\s+track)\s*[\])]",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"[\[(]\s*official\s*[\])]", " ", value, flags=re.I)
    value = re.sub(
        r"\s*-\s*(?:official\s+)?(?:music\s+video|lyric\s+video|visualizer|audio|video)$",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s+(?:official\s+)?(?:music\s+video|lyric\s+video|official\s+video|official\s+audio)$",
        " ",
        value,
        flags=re.I,
    )
    return " ".join(re.findall(r"[^\W_]+", value))


def _matches_recording(item: Dict[str, Any], title: str, artist: str, duration: float) -> bool:
    """Reject substitutions; metadata similarity is not proof of audio identity."""
    candidate_title = str(item.get("track") or item.get("title") or "")
    artists = item.get("artists") or []
    candidate_artists = [a.get("name", "") if isinstance(a, dict) else str(a) for a in artists]
    candidate_artists.extend(str(item.get(k) or "") for k in ("artist", "uploader", "channel"))

    # Expand candidate artists with split artists
    expanded_candidate_artists: List[str] = []
    for ca in candidate_artists:
        if ca:
            expanded_candidate_artists.append(ca)
            expanded_candidate_artists.extend(_split_artists(ca))
    candidate_artists = expanded_candidate_artists

    # Requested artists: include both full artist string and split parts
    requested_artist_parts = [artist] + _split_artists(artist)
    requested_artists = {_normalized_name(a) for a in requested_artist_parts if a and _normalized_name(a)}

    # Video uploads often put the performer in the title rather than artist tags (e.g. "Artist - Title" or "Title - Artist").
    dash_match = re.search(r"\s+[-–—:]\s+", candidate_title)
    if dash_match:
        prefix = candidate_title[:dash_match.start()]
        rest = candidate_title[dash_match.end():]
        if _normalized_name(prefix) in requested_artists:
            candidate_artists.append(prefix)
            candidate_title = rest
        elif _normalized_name(rest) in requested_artists:
            candidate_artists.append(rest)
            candidate_title = prefix

    def title_without_known_credits(value, credits):
        # Providers move featured performers between title and artist fields.
        # Remove only explicitly credited names; keep every version qualifier.
        def replace(match):
            names = re.split(r"(?i)\s*(?:,|&|\band\b|\bx\b)\s*", match.group(1))
            if names and all(_normalized_name(name) in credits for name in names if name.strip()):
                return " "
            return match.group(0)

        cleaned = re.sub(
            r"[\[(](?:feat\.?|ft\.?|featuring)\s+([^\[\]()]+)[\])]",
            replace,
            value,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\b(?:feat\.?|ft\.?|featuring)\s+([^\[\]()\-–—:]+)",
            replace,
            cleaned,
            flags=re.I,
        )
        return cleaned

    cand_credit_set = {
        _normalized_name(a) for a in candidate_artists if a and _normalized_name(a)
    }
    normalized_title = title_without_known_credits(title, cand_credit_set)
    candidate_title = title_without_known_credits(candidate_title, requested_artists)

    if _normalized_name(candidate_title) != _normalized_name(normalized_title):
        return False
    if not requested_artists or not any(
        _normalized_name(re.sub(r"(?i)\s*-\s*topic$", "", a)) in requested_artists
        for a in candidate_artists if a
    ):
        return False
    duration_s = _duration_seconds(duration)
    candidate_duration = _duration_seconds(item.get("duration_seconds") or item.get("duration"))
    if duration_s and (not candidate_duration or abs(candidate_duration - duration_s) > max(8.0, duration_s * 0.04)):
        return False
    return True


def _tracks_match(t1: Dict[str, Any], t2: Dict[str, Any]) -> bool:
    """Checks whether two track dicts represent the same underlying track."""
    if not isinstance(t1, dict) or not isinstance(t2, dict):
        return False
    id1, id2 = str(t1.get("id") or "").strip(), str(t2.get("id") or "").strip()
    sp1 = str(t1.get("spotify_id") or "").strip()
    sp2 = str(t2.get("spotify_id") or "").strip()
    if id1 and id2 and id1 == id2:
        return True
    if sp1 and (sp1 == id2 or sp1 == sp2):
        return True
    if sp2 and (sp2 == id1 or sp2 == sp1):
        return True
    u1, u2 = str(t1.get("uri") or "").strip(), str(t2.get("uri") or "").strip()
    su1, su2 = str(t1.get("spotify_uri") or "").strip(), str(t2.get("spotify_uri") or "").strip()
    uris1 = {u for u in (u1, su1) if u.startswith("spotify:track:")}
    uris2 = {u for u in (u2, su2) if u.startswith("spotify:track:")}
    if uris1 and uris2 and (uris1 & uris2):
        return True
    title1 = _normalized_name(t1.get("title"))
    title2 = _normalized_name(t2.get("title"))
    artists1 = {_normalized_name(a) for a in [str(t1.get("artist") or "")] + _split_artists(str(t1.get("artist") or "")) if a and _normalized_name(a)}
    artists2 = {_normalized_name(a) for a in [str(t2.get("artist") or "")] + _split_artists(str(t2.get("artist") or "")) if a and _normalized_name(a)}
    if t1.get("artists") and isinstance(t1["artists"], list):
        for a in t1["artists"]:
            name = a.get("name") if isinstance(a, dict) else str(a)
            if name:
                artists1.add(_normalized_name(name))
                for sa in _split_artists(name):
                    artists1.add(_normalized_name(sa))
    if t2.get("artists") and isinstance(t2["artists"], list):
        for a in t2["artists"]:
            name = a.get("name") if isinstance(a, dict) else str(a)
            if name:
                artists2.add(_normalized_name(name))
                for sa in _split_artists(name):
                    artists2.add(_normalized_name(sa))
    return bool(title1 and title1 == title2 and (artists1 & artists2))

