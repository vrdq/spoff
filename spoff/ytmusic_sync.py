"""Copies Spoff's playlists to YouTube Music.

One-way: Spoff is the source. Each playlist gets a YouTube Music copy with
the same name, description and privacy, and songs added or removed in Spoff
(or synced from Spotify) are added or removed there. Songs the user adds to a
copy in the YouTube Music app are left alone, because only songs Spoff put
there are ever removed. Deleting a playlist in Spoff deletes its copy.

It uses the YouTube login Spoff already reads from the browser, so it only
runs while signed in.
"""
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

try:
    from . import streamer
    from .storage import DATA_DIR, _atomic_json_dump, load_offline_index
except ImportError:
    import streamer  # type: ignore
    from storage import DATA_DIR, _atomic_json_dump, load_offline_index  # type: ignore

logger = logging.getLogger("ytmusic_sync")

STATE_FILE = DATA_DIR / "ytmusic_sync.json"
ORIGIN = "https://music.youtube.com"
# Songs that couldn't be found on YouTube are looked up again after this long.
MISS_RETRY_SECONDS = 3 * 24 * 3600
_VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")

_sync_lock = threading.Lock()


def client() -> Optional[Any]:
    """A YouTube Music client signed in with the browser login, or None."""
    cookies = streamer.youtube_login_cookies()
    sapisid = cookies.get("__Secure-3PAPISID") or cookies.get("SAPISID")
    if not sapisid:
        return None
    from ytmusicapi import YTMusic
    from ytmusicapi.helpers import get_authorization
    headers = {
        "cookie": "; ".join(f"{name}={value}" for name, value in cookies.items()),
        # Only marks this as browser auth; ytmusicapi signs each request itself.
        "authorization": get_authorization(f"{sapisid} {ORIGIN}"),
        "x-goog-authuser": "0",
        "origin": ORIGIN,
    }
    return YTMusic(auth=headers)


def _load_state() -> Dict[str, Any]:
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("playlists", {})
    state.setdefault("videos", {})
    return state


def _video_id_from_url(url: Any) -> Optional[str]:
    match = _VIDEO_ID.search(url) if isinstance(url, str) else None
    return match.group(1) if match else None


def video_id_for(track: Dict[str, Any], state: Dict[str, Any], offline: Dict[str, Any]) -> Optional[str]:
    """The YouTube video for a track: its own link, the one its download came
    from, an earlier lookup, or a fresh YouTube Music search."""
    track_id = str(track.get("id") or "")
    for url in (track.get("url"), track.get("resolved_url"), (offline.get(track_id) or {}).get("resolved_url")):
        found = _video_id_from_url(url)
        if found:
            return found
    known = state["videos"].get(track_id)
    if isinstance(known, dict):
        if known.get("v"):
            return known["v"]
        if time.time() - float(known.get("at") or 0) < MISS_RETRY_SECONDS:
            return None
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    if not title or not artist:
        return None
    try:
        duration = float(track.get("duration_ms") or 0) / 1000
    except (TypeError, ValueError):
        duration = 0.0
    matches = streamer._ytmusic_match_queries(title, artist, duration)
    found = _video_id_from_url(matches[0][0]) if matches else None
    if track_id:
        state["videos"][track_id] = {"v": found or "", "at": time.time()}
    return found


def _clean_description(text: Any) -> str:
    # YouTube rejects descriptions containing angle brackets.
    return re.sub(r"[<>]", "", str(text or ""))[:5000]


def _privacy(playlist: Dict[str, Any]) -> str:
    return "PUBLIC" if playlist.get("public") else "PRIVATE"


def _ok(result: Any) -> bool:
    """ytmusicapi reports success as a status string, or (for adds) a dict with a status."""
    status = result.get("status") if isinstance(result, dict) else result
    return isinstance(status, str) and "SUCCEEDED" in status


def _sync_one(yt: Any, playlist: Dict[str, Any], entry: Optional[Dict[str, Any]],
              wanted: List[str]) -> Optional[Dict[str, Any]]:
    """Brings one copy up to date and returns its new record (None on failure)."""
    # YouTube Music rejects titles containing angle brackets.
    name = re.sub(r"[<>]", "", str(playlist.get("name") or ""))[:150].strip() or "Untitled playlist"
    description = _clean_description(playlist.get("description"))
    privacy = _privacy(playlist)

    remote = None
    if entry and entry.get("ytm_id"):
        try:
            remote = yt.get_playlist(entry["ytm_id"], limit=None)
        except Exception:
            logger.info("YouTube Music copy of %s is gone; making a new one", name)
            remote = None

    if remote is None:
        created = yt.create_playlist(name, description, privacy_status=privacy, video_ids=wanted or None)
        if not isinstance(created, str):
            logger.warning("Could not create YouTube Music playlist %s: %s", name, created)
            return None
        return {"ytm_id": created, "name": name, "description": description,
                "privacy": privacy, "videos": list(wanted)}

    ytm_id = entry["ytm_id"]
    present = {t.get("videoId") for t in remote.get("tracks") or [] if t.get("videoId")}
    pushed_before = set(entry.get("videos") or [])
    wanted_set = set(wanted)

    to_add = [v for v in wanted if v not in present]
    if to_add and not _ok(yt.add_playlist_items(ytm_id, to_add, duplicates=False)):
        logger.warning("Could not add %d songs to YouTube Music playlist %s", len(to_add), name)
        to_add = []
    # Only remove songs Spoff put there, never ones added in the YouTube Music app.
    to_remove = [t for t in remote.get("tracks") or []
                 if t.get("videoId") in pushed_before and t.get("videoId") not in wanted_set
                 and t.get("setVideoId")]
    if to_remove and not _ok(yt.remove_playlist_items(ytm_id, to_remove)):
        logger.warning("Could not remove %d songs from YouTube Music playlist %s", len(to_remove), name)

    if (name, description, privacy) != (entry.get("name"), entry.get("description"), entry.get("privacy")):
        yt.edit_playlist(ytm_id, title=name, description=description, privacyStatus=privacy)

    # YouTube Music takes a few seconds to list new songs, so a song pushed
    # earlier may be missing from `present` (and re-adding it fails as a
    # duplicate). It still counts as Spoff's.
    videos = [v for v in wanted if v in present or v in to_add or v in pushed_before]
    return {"ytm_id": ytm_id, "name": name, "description": description, "privacy": privacy, "videos": videos}


def sync_playlists(playlists: List[Dict[str, Any]],
                   progress: Optional[Callable[[str], None]] = None) -> Dict[str, int]:
    """Copies every playlist to YouTube Music. Returns counts for a status message."""
    counts = {"playlists": 0, "songs": 0, "missing": 0, "failed": 0, "deleted": 0}
    if not _sync_lock.acquire(blocking=False):
        return counts  # a sync is already running; it will see the latest playlists
    try:
        yt = client()
        if yt is None:
            return counts
        state = _load_state()
        offline = load_offline_index()
        current_ids = set()
        for playlist in playlists:
            pid = str(playlist.get("id") or "")
            if not pid:
                continue
            current_ids.add(pid)
            if progress:
                progress(f"Copying '{playlist.get('name') or 'playlist'}' to YouTube Music…")
            wanted: List[str] = []
            for track in playlist.get("tracks") or []:
                vid = video_id_for(track, state, offline)
                if vid is None:
                    counts["missing"] += 1
                elif vid not in wanted:
                    wanted.append(vid)
            try:
                record = _sync_one(yt, playlist, state["playlists"].get(pid), wanted)
            except Exception:
                logger.exception("YouTube Music sync failed for %s", playlist.get("name"))
                record = None
            if record is None:
                counts["failed"] += 1
            else:
                state["playlists"][pid] = record
                counts["playlists"] += 1
                counts["songs"] += len(record["videos"])
            _atomic_json_dump(STATE_FILE, state)

        # Playlists deleted in Spoff: delete the copies Spoff made.
        for pid in [p for p in state["playlists"] if p not in current_ids]:
            ytm_id = state["playlists"][pid].get("ytm_id")
            try:
                if ytm_id:
                    yt.delete_playlist(ytm_id)
                counts["deleted"] += 1
            except Exception:
                logger.warning("Could not delete YouTube Music copy %s", ytm_id, exc_info=True)
                continue
            del state["playlists"][pid]
        _atomic_json_dump(STATE_FILE, state)
        return counts
    finally:
        _sync_lock.release()
