from unittest.mock import patch

from spoff import ytmusic_sync


class FakeYTM:
    """Remembers playlists like YouTube Music does, with setVideoIds for removal."""

    def __init__(self):
        self.playlists = {}
        self.edits = []
        self._n = 0

    def _track(self, vid):
        self._n += 1
        return {"videoId": vid, "setVideoId": f"s{self._n}"}

    def create_playlist(self, title, description, privacy_status="PRIVATE", video_ids=None):
        pid = f"PL{len(self.playlists) + 1}"
        self.playlists[pid] = {"title": title, "privacy": privacy_status,
                               "tracks": [self._track(v) for v in video_ids or []]}
        return pid

    def get_playlist(self, pid, limit=None):
        if pid not in self.playlists:
            raise Exception("not found")
        return {"tracks": list(self.playlists[pid]["tracks"])}

    def add_playlist_items(self, pid, video_ids, duplicates=False):
        self.playlists[pid]["tracks"] += [self._track(v) for v in video_ids]
        return "STATUS_SUCCEEDED"

    def remove_playlist_items(self, pid, videos):
        gone = {v["setVideoId"] for v in videos}
        self.playlists[pid]["tracks"] = [t for t in self.playlists[pid]["tracks"] if t["setVideoId"] not in gone]
        return "STATUS_SUCCEEDED"

    def edit_playlist(self, pid, **changes):
        self.edits.append(changes)
        return "STATUS_SUCCEEDED"

    def delete_playlist(self, pid):
        del self.playlists[pid]
        return "STATUS_SUCCEEDED"

    def videos(self, pid):
        return [t["videoId"] for t in self.playlists[pid]["tracks"]]


def _yt_track(tid, vid):
    return {"id": tid, "title": tid, "artist": "A", "url": f"https://www.youtube.com/watch?v={vid}"}


def _run(fake, playlists, tmp_path):
    with patch.object(ytmusic_sync, "client", return_value=fake), \
         patch.object(ytmusic_sync, "STATE_FILE", tmp_path / "state.json"), \
         patch.object(ytmusic_sync, "load_offline_index", return_value={}):
        return ytmusic_sync.sync_playlists(playlists)


def test_copies_then_follows_adds_and_removes_but_keeps_songs_added_in_ytm(tmp_path):
    fake = FakeYTM()
    pl = {"id": "p1", "name": "Gym", "public": False,
          "tracks": [_yt_track("a", "AAAAAAAAAAA"), _yt_track("b", "BBBBBBBBBBB")]}
    counts = _run(fake, [pl], tmp_path)
    assert counts["playlists"] == 1
    assert fake.videos("PL1") == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert fake.playlists["PL1"]["privacy"] == "PRIVATE"

    fake.add_playlist_items("PL1", ["USERADDED01"])            # added in the YouTube Music app
    pl["tracks"] = [_yt_track("b", "BBBBBBBBBBB"), _yt_track("c", "CCCCCCCCCCC")]
    _run(fake, [pl], tmp_path)
    assert sorted(fake.videos("PL1")) == ["BBBBBBBBBBB", "CCCCCCCCCCC", "USERADDED01"]


def test_rename_and_privacy_change_are_copied(tmp_path):
    fake = FakeYTM()
    pl = {"id": "p1", "name": "Old", "public": False, "tracks": []}
    _run(fake, [pl], tmp_path)
    pl.update(name="New", public=True)
    _run(fake, [pl], tmp_path)
    assert fake.edits[-1] == {"title": "New", "description": "", "privacyStatus": "PUBLIC"}


def test_deleting_in_spoff_deletes_the_copy_and_a_deleted_copy_is_remade(tmp_path):
    fake = FakeYTM()
    keep = {"id": "k", "name": "Keep", "tracks": []}
    drop = {"id": "d", "name": "Drop", "tracks": []}
    _run(fake, [keep, drop], tmp_path)
    assert len(fake.playlists) == 2
    counts = _run(fake, [keep], tmp_path)
    assert counts["deleted"] == 1 and len(fake.playlists) == 1

    fake.playlists.clear()                                     # user deleted the copy on YouTube Music
    _run(fake, [keep], tmp_path)
    assert len(fake.playlists) == 1


def test_spotify_tracks_are_matched_once_and_misses_are_counted(tmp_path):
    fake = FakeYTM()
    pl = {"id": "p", "name": "S", "tracks": [
        {"id": "sp1", "title": "Song", "artist": "Band", "duration_ms": 200000},
        {"id": "sp2", "title": "Nowhere", "artist": "Band", "duration_ms": 200000},
    ]}
    def match(title, artist, duration):
        return [("https://www.youtube.com/watch?v=SSSSSSSSSSS", True)] if title == "Song" else []
    with patch.object(ytmusic_sync.streamer, "_ytmusic_match_queries", side_effect=match) as search:
        counts = _run(fake, [pl], tmp_path)
        _run(fake, [pl], tmp_path)
    assert fake.videos("PL1") == ["SSSSSSSSSSS"]
    assert counts["missing"] == 1
    assert search.call_count == 2                              # cached: no second lookup for either


def test_signed_out_does_nothing(tmp_path):
    with patch.object(ytmusic_sync, "client", return_value=None):
        assert ytmusic_sync.sync_playlists([{"id": "p", "name": "x", "tracks": []}])["playlists"] == 0


def test_songs_youtube_hasnt_listed_yet_stay_recorded_as_spoffs(tmp_path):
    fake = FakeYTM()
    pl = {"id": "p", "name": "Lag", "tracks": [_yt_track("a", "AAAAAAAAAAA")]}
    _run(fake, [pl], tmp_path)
    listed = fake.playlists["PL1"]["tracks"]
    fake.playlists["PL1"]["tracks"] = []                       # YouTube Music hasn't caught up
    fake.add_playlist_items = lambda *a, **k: {"status": "STATUS_FAILED"}   # "already in the playlist"
    _run(fake, [pl], tmp_path)
    fake.playlists["PL1"]["tracks"] = listed
    del fake.add_playlist_items
    pl["tracks"] = []
    _run(fake, [pl], tmp_path)
    assert fake.videos("PL1") == []                            # still removed once dropped in Spoff
