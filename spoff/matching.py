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


def _split_artists(value: str) -> List[str]:
    """Splits multi-artist strings while preserving primary artist names."""
    if not value or not isinstance(value, str):
        return []
    # Slashes, ampersands, and words such as "and" belong to real band names.
    # Treating their components as artists can accept unrelated recordings.
    parts = re.split(r"(?i)\s*(?:,|\b(?:featuring|feat|ft)\b\.?)\s*", value)
    results: List[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        results.append(part)
    return results


def _clean_artist_name(name: str) -> str:
    """Removes platform channel suffixes like '- Topic', 'VEVO', 'Official', 'Channel'."""
    if not name or not isinstance(name, str):
        return ""
    cleaned = str(name).strip()
    cleaned = re.sub(r"(?i)\s*(?:[-_–—:]\s*|\b)topic$", "", cleaned)
    cleaned = re.sub(r"(?i)(?<=[a-zA-Z0-9])vevo$", "", cleaned)
    cleaned = re.sub(r"(?i)\s*[-_–—:]?\s*official(?:\s+channel)?$", "", cleaned)
    cleaned = re.sub(r"(?i)\s*[-_–—:]?\s*channel$", "", cleaned)
    return cleaned.strip()


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


def _extract_featured_artists(text: str) -> List[str]:
    """Extracts artist names credited in (feat. ...), [ft. ...], featuring ... clauses."""
    found: List[str] = []
    if not text or not isinstance(text, str):
        return found
    for m in re.finditer(r"[\[(](?:feat\.?|ft\.?|featuring)\s+([^\[\]()]+)[\])]", text, flags=re.I):
        for name in re.split(r"(?i)\s*(?:,|&|\band\b|\bx\b)\s*", m.group(1)):
            n = name.strip()
            if n:
                found.append(n)
    for m in re.finditer(r"\b(?:feat\.?|ft\.?|featuring)\s+([^\[\]()\-–—:•·|]+)", text, flags=re.I):
        for name in re.split(r"(?i)\s*(?:,|&|\band\b|\bx\b)\s*", m.group(1)):
            n = name.strip()
            if n:
                found.append(n)
    return found


def _title_without_known_credits(value: str, credits: Any) -> str:
    """Removes featured performer clauses whose artists are in the known credits set."""
    if not value or not isinstance(value, str):
        return ""

    def replace(match):
        names = re.split(r"(?i)\s*(?:,|&|\band\b|\bx\b)\s*", match.group(1))
        valid_names = [n.strip() for n in names if n.strip()]
        if valid_names and all(_normalized_name(n) in credits for n in valid_names):
            return " "
        return match.group(0)

    cleaned = re.sub(
        r"[\[(](?:feat\.?|ft\.?|featuring)\s+([^\[\]()]+)[\])]",
        replace,
        value,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\b(?:feat\.?|ft\.?|featuring)\s+([^\[\]()\-–—:•·|]+)",
        replace,
        cleaned,
        flags=re.I,
    )
    return cleaned


def _matches_recording(item: Dict[str, Any], title: str, artist: str, duration: float) -> bool:
    """Reject substitutions; metadata similarity is not proof of audio identity."""
    candidate_title = str(item.get("track") or item.get("title") or "")
    artists = item.get("artists") or []
    candidate_artists = [a.get("name", "") if isinstance(a, dict) else str(a) for a in artists]
    candidate_artists.extend(str(item.get(k) or "") for k in ("artist", "uploader", "channel"))

    # Expand candidate artists with split artists and cleaned names
    expanded_candidate_artists: List[str] = []
    for ca in candidate_artists:
        if ca:
            expanded_candidate_artists.append(ca)
            cca = _clean_artist_name(ca)
            if cca and cca != ca:
                expanded_candidate_artists.append(cca)
            for sa in _split_artists(ca):
                expanded_candidate_artists.append(sa)
                csa = _clean_artist_name(sa)
                if csa and csa != sa:
                    expanded_candidate_artists.append(csa)
    candidate_artists = expanded_candidate_artists

    # Requested artists: include full artist string, split parts, and cleaned names
    requested_artist_parts = [artist] + _split_artists(artist)
    cra = _clean_artist_name(artist)
    if cra and cra != artist:
        requested_artist_parts.append(cra)
        requested_artist_parts.extend(_split_artists(cra))
    for a in list(requested_artist_parts):
        ca = _clean_artist_name(a)
        if ca and ca != a:
            requested_artist_parts.append(ca)
    # Extract any featured artists credited directly in the requested title
    for fa in _extract_featured_artists(title):
        requested_artist_parts.append(fa)
        requested_artist_parts.extend(_split_artists(fa))
        cfa = _clean_artist_name(fa)
        if cfa and cfa != fa:
            requested_artist_parts.append(cfa)
    requested_artists = {_normalized_name(a) for a in requested_artist_parts if a and _normalized_name(a)}

    def _artist_in_requested(name: str) -> bool:
        if not name:
            return False
        norm = _normalized_name(name)
        if norm and norm in requested_artists:
            return True
        cleaned = _normalized_name(_clean_artist_name(name))
        if cleaned and cleaned in requested_artists:
            return True
        for part in _split_artists(name):
            pnorm = _normalized_name(part)
            if pnorm and pnorm in requested_artists:
                return True
            pc = _normalized_name(_clean_artist_name(part))
            if pc and pc in requested_artists:
                return True
        return False

    # Video uploads often put the performer in the title rather than artist tags (e.g. "Artist - Title", "Title - Artist", "Artist • Title").
    sep_pattern = r"\s+[-–—:|]\s+|\s*[·•]\s*"
    all_seps = list(re.finditer(sep_pattern, candidate_title))
    if all_seps:
        first_sep = all_seps[0]
        prefix = candidate_title[:first_sep.start()].strip()
        rest = candidate_title[first_sep.end():].strip()
        if _artist_in_requested(prefix):
            candidate_artists.append(prefix)
            candidate_artists.extend(_split_artists(prefix))
            candidate_title = rest
        elif _artist_in_requested(rest):
            candidate_artists.append(rest)
            candidate_artists.extend(_split_artists(rest))
            candidate_title = prefix
        elif len(all_seps) > 1:
            last_sep = all_seps[-1]
            last_prefix = candidate_title[:last_sep.start()].strip()
            last_suffix = candidate_title[last_sep.end():].strip()
            if _artist_in_requested(last_suffix):
                candidate_artists.append(last_suffix)
                candidate_artists.extend(_split_artists(last_suffix))
                candidate_title = last_prefix
            elif _artist_in_requested(last_prefix):
                candidate_artists.append(last_prefix)
                candidate_artists.extend(_split_artists(last_prefix))
                candidate_title = last_suffix

    cand_credit_set = {
        _normalized_name(a) for a in candidate_artists if a and _normalized_name(a)
    }
    all_known_credits = requested_artists | cand_credit_set
    normalized_title = _title_without_known_credits(title, all_known_credits)
    candidate_title = _title_without_known_credits(candidate_title, all_known_credits)

    if _normalized_name(candidate_title) != _normalized_name(normalized_title):
        return False
    if not requested_artists or not any(
        _normalized_name(_clean_artist_name(a)) in requested_artists
        for a in candidate_artists if a
    ):
        return False
    duration_s = _seconds(duration)
    cand_dur = item.get("duration_seconds")
    if cand_dur is None and "duration_ms" in item:
        cand_dur = float(item["duration_ms"] or 0) / 1000.0
    elif cand_dur is None:
        cand_dur = item.get("duration")
    candidate_duration = _seconds(cand_dur)
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
    raw_artists1 = [str(t1.get("artist") or "")] + _split_artists(str(t1.get("artist") or ""))
    for a in list(raw_artists1):
        ca = _clean_artist_name(a)
        if ca and ca != a:
            raw_artists1.append(ca)
            raw_artists1.extend(_split_artists(ca))
    raw_artists2 = [str(t2.get("artist") or "")] + _split_artists(str(t2.get("artist") or ""))
    for a in list(raw_artists2):
        ca = _clean_artist_name(a)
        if ca and ca != a:
            raw_artists2.append(ca)
            raw_artists2.extend(_split_artists(ca))
    artists1 = {_normalized_name(a) for a in raw_artists1 if a and _normalized_name(a)}
    artists2 = {_normalized_name(a) for a in raw_artists2 if a and _normalized_name(a)}
    if t1.get("artists") and isinstance(t1["artists"], list):
        for a in t1["artists"]:
            name = a.get("name") if isinstance(a, dict) else str(a)
            if name:
                artists1.add(_normalized_name(name))
                cname = _clean_artist_name(name)
                if cname:
                    artists1.add(_normalized_name(cname))
                for sa in _split_artists(name):
                    artists1.add(_normalized_name(sa))
                    csa = _clean_artist_name(sa)
                    if csa:
                        artists1.add(_normalized_name(csa))
    if t2.get("artists") and isinstance(t2["artists"], list):
        for a in t2["artists"]:
            name = a.get("name") if isinstance(a, dict) else str(a)
            if name:
                artists2.add(_normalized_name(name))
                cname = _clean_artist_name(name)
                if cname:
                    artists2.add(_normalized_name(cname))
                for sa in _split_artists(name):
                    artists2.add(_normalized_name(sa))
                    csa = _clean_artist_name(sa)
                    if csa:
                        artists2.add(_normalized_name(csa))
    raw_title1 = str(t1.get("title") or "")
    raw_title2 = str(t2.get("title") or "")
    for fa in _extract_featured_artists(raw_title1):
        artists1.add(_normalized_name(fa))
        cfa = _clean_artist_name(fa)
        if cfa:
            artists1.add(_normalized_name(cfa))
        for sa in _split_artists(fa):
            artists1.add(_normalized_name(sa))
            csa = _clean_artist_name(sa)
            if csa:
                artists1.add(_normalized_name(csa))
    for fa in _extract_featured_artists(raw_title2):
        artists2.add(_normalized_name(fa))
        cfa = _clean_artist_name(fa)
        if cfa:
            artists2.add(_normalized_name(cfa))
        for sa in _split_artists(fa):
            artists2.add(_normalized_name(sa))
            csa = _clean_artist_name(sa)
            if csa:
                artists2.add(_normalized_name(csa))
    all_credits = artists1 | artists2
    clean_title1 = _title_without_known_credits(raw_title1, all_credits)
    clean_title2 = _title_without_known_credits(raw_title2, all_credits)
    title1 = _normalized_name(clean_title1)
    title2 = _normalized_name(clean_title2)
    return bool(title1 and title1 == title2 and (artists1 & artists2))
