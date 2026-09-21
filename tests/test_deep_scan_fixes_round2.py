import os
import time
import json
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from spoff import art, auth, streamer, ytmusic, storage

def test_art_cache_pruning(tmp_path, monkeypatch):
    """Verifies that disk and memory art caches prune oldest entries beyond limits."""
    cache_file = tmp_path / "art_cache.json"
    monkeypatch.setattr(art, "_get_art_cache_file", lambda: cache_file)
    with art._art_cache_lock:
        art._memory_art_cache.clear()

    # Preload 2050 entries
    for i in range(2050):
        art._memory_art_cache[f"track_{i}"] = {"art_url": f"https://example.com/art_{i}.jpg"}

    # Trigger disk save
    art._save_disk_cache()

    assert cache_file.exists()
    with open(cache_file, "r", encoding="utf-8") as f:
        disk_data = json.load(f)

    # Disk cache should be capped at 2000
    assert len(disk_data) == 2000
    # Oldest entries (0 to 49) should have been pruned
    assert "track_0" not in disk_data
    assert "track_2049" in disk_data

    # Now verify _save_to_cache prunes memory cache
    art._save_to_cache({"id": "track_new", "title": "New", "artist": "Artist"}, {"art_url": "https://new"})
    assert len(art._memory_art_cache) <= 2000


def test_callback_http_server_reuse_address():
    """Verifies that CallbackHTTPServer has allow_reuse_address enabled."""
    assert getattr(auth.CallbackHTTPServer, "allow_reuse_address", False) is True


def test_token_refresh_concurrency(tmp_path, monkeypatch):
    """Verifies that simultaneous calls to get_valid_token do not cause double refreshes."""
    auth_file = tmp_path / "spotify_auth.json"
    monkeypatch.setattr(auth, "AUTH_FILE", auth_file)

    initial_auth = {
        "access_token": "expired_token",
        "refresh_token": "valid_refresh",
        "expires_at": time.time() - 100,  # Expired
    }
    with open(auth_file, "w", encoding="utf-8") as f:
        json.dump(initial_auth, f)

    refresh_count = 0
    refresh_lock = threading.Lock()

    def mock_refresh(ref_token):
        nonlocal refresh_count
        with refresh_lock:
            refresh_count += 1
        time.sleep(0.05)
        return {
            "access_token": "new_refreshed_token",
            "refresh_token": "valid_refresh",
            "expires_at": time.time() + 3600,
        }

    monkeypatch.setattr(auth, "refresh_spotify_token", mock_refresh)

    results = []
    def worker():
        tok = auth.get_valid_token()
        results.append(tok)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads should have received the refreshed token
    assert len(results) == 5
    assert all(r == "new_refreshed_token" for r in results)
    # The network refresh endpoint must have been called exactly once
    assert refresh_count == 1


def test_streamer_sanitization(monkeypatch):
    """Verifies that invalidate_stream_cache and search_and_resolve_stream safely handle None/non-strings."""
    # Should not raise AttributeError: 'NoneType' object has no attribute 'lower'
    streamer.invalidate_stream_cache(None, None)
    streamer.invalidate_stream_cache("Some Track", None)
    streamer.invalidate_stream_cache(None, "Some Artist")

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"url": "https://stream.audio/sample"}
        mock_ydl.return_value.__enter__.return_value = mock_instance

        res = streamer.search_and_resolve_stream(None, None)
        assert res is not None
        assert res.get("stream_url") == "https://stream.audio/sample"


def test_ytmusic_safe_duration_ms():
    """Verifies that _safe_duration_ms handles numeric, float strings, MM:SS, and malformed inputs safely."""
    safe = ytmusic._safe_duration_ms
    assert safe(0) == 0
    assert safe(None) == 0
    assert safe("") == 0
    assert safe(180) == 180000
    assert safe(215.5) == 215500
    assert safe("215.5") == 215500
    assert safe("180") == 180000
    assert safe("03:30") == 210000
    assert safe("01:02:03") == 3723000
    assert safe("invalid") == 0


def test_storage_merge_track_artwork_liked_songs(tmp_path, monkeypatch):
    """Verifies that merge_track_artwork properly enriches liked tracks in liked_songs.json."""
    data_dir = tmp_path / "spoff"
    data_dir.mkdir()
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "LIKED_SONGS_FILE", data_dir / "liked_songs.json")

    liked = [
        {"id": "track_1", "title": "Liked Track 1", "artist": "Artist 1"},
        {"id": "track_2", "title": "Liked Track 2", "artist": "Artist 2", "art_url": "https://existing.art"},
    ]
    storage.save_liked_songs(liked)

    artwork = {
        "art_url": "https://resolved.art/1.jpg",
        "artist_art_url": "https://resolved.art/artist1.jpg",
    }
    merged = storage.merge_track_artwork("liked", "track_1", artwork)
    assert merged is True

    reloaded = storage.load_liked_songs()
    assert reloaded[0]["art_url"] == "https://resolved.art/1.jpg"
    assert reloaded[0]["artist_art_url"] == "https://resolved.art/artist1.jpg"
    # Should not overwrite existing artwork
    assert reloaded[1]["art_url"] == "https://existing.art"
