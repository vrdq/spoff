# Spotify Synchronization Hardening Audit (2026-09-24)

## Overview
Investigated reported library and playlist synchronization issues in Spoff across [`spoff/matching.py`](file:///home/vrdq/spot-tui/spoff/matching.py), [`spoff/auth.py`](file:///home/vrdq/spot-tui/spoff/auth.py), and [`spoff/app.py`](file:///home/vrdq/spot-tui/spoff/app.py). Four root causes were identified, establishing concrete failure scenarios that caused songs not to sync, sync tasks to silently drop remote updates, and YouTube Music tracks to be excluded or lost on reordering.

---

## Findings and Repaired Bugs

1. **Platform Channel Suffixes Blocked Spotify Matching & Search Resolution**
   - **File / Lines**: [`spoff/matching.py:62-174`](file:///home/vrdq/spot-tui/spoff/matching.py#L62-L174), [`spoff/auth.py:855-908`](file:///home/vrdq/spot-tui/spoff/auth.py#L855-L908)
   - **Root Cause**: YouTube/YouTube Music channel artist names often append `- Topic`, `VEVO`, or `Official` (e.g. `Queen - Topic`, `QueenVEVO`, `Queen Official`). In `_matches_recording` and `_tracks_match`, `- Topic` was only stripped on candidate strings, not on requested artists. Furthermore, Spotify search queried `artist:"Queen - Topic"`, returning 0 results because Spotify catalogue artists do not contain channel suffixes.
   - **Fix**: Added `_clean_artist_name` to clean channel suffixes (`- Topic`, `VEVO`, `Official`, `Channel`) while preserving band names (e.g. `AC/DC`, `Florence and the Machine`, `Earth, Wind & Fire`). Expanded both requested and candidate artists with cleaned names in `_matches_recording` and `_tracks_match`. In `search_spotify_track`, structured queries to search using cleaned primary artist and fallback queries.

2. **Strict Snapshot Equality Canceled Background and Library Sync During Concurrent Metadata Writes**
   - **File / Lines**: [`spoff/app.py:7290-7296`](file:///home/vrdq/spot-tui/spoff/app.py#L7290-L7296), [`spoff/auth.py:748-774`](file:///home/vrdq/spot-tui/spoff/auth.py#L748-L774)
   - **Root Cause**: While network requests were in flight to fetch Spotify Liked Songs or playlists, local background workers (such as `merge_track_artwork`) updated `art_url` in `liked_songs.json` or `playlists.json`. Strict list equality checks (`load_liked_songs() != initial_liked` and `original.get("tracks") != p.get("tracks")`) evaluated to `True`, causing Spoff to log an edit notice and abort/break sync, dropping incoming tracks from Spotify.
   - **Fix**: Under `storage_transaction()`, compute the set of track keys removed locally during the fetch window (`initial_keys - current_keys`). Filter out only those specifically deleted tracks from the remote fetch before merging into `current_liked` / `existing_tracks`. Local additions, artwork updates, and remote tracks are now preserved without aborting sync.

3. **Playlist Track Reorder and Sync Omitted Resolved YouTube Music Tracks**
   - **File / Lines**: [`spoff/auth.py:1159`](file:///home/vrdq/spot-tui/spoff/auth.py#L1159)
   - **Root Cause**: `sync_playlist_tracks_to_spotify` unconditionally skipped tracks where `is_client_side_track(t)` was True (`continue`), even if that track had already been matched and given a valid `spotify_id` or `spotify_uri`. When replacing tracks on Spotify with `PUT /playlists/{id}/tracks`, all synced YouTube Music tracks were wiped from the Spotify playlist.
   - **Fix**: Prioritize resolving `spotify_uri` and `spotify_id` on each track before falling back to `uri` / `id`. Only tracks lacking any Spotify identity are excluded from the remote Spotify playlist payload.

4. **Added Track Spotify IDs Not Persisted Directly to Storage**
   - **File / Lines**: [`spoff/auth.py:978-1020`](file:///home/vrdq/spot-tui/spoff/auth.py#L978-L1020)
   - **Root Cause**: When adding a track to Spotify via `add_track_to_spotify_account`, `resolve_spotify_track_info` populated `spotify_id` and `spotify_uri` on the in-memory dict, but did not guarantee immediate persistence to `liked_songs.json` or `playlists.json` for all caller code paths.
   - **Fix**: On successful API response in `add_track_to_spotify_account`, mutate the track in `liked_songs.json` or `playlists.json` under `storage_transaction()` to persist `spotify_id` and `spotify_uri`.

5. **Unlinked and 404 Spotify Playlists Blocked Track Addition, Rename, and Sync**
   - **File / Lines**: [`spoff/auth.py:1006-1215`](file:///home/vrdq/spot-tui/spoff/auth.py#L1006-L1215), [`spoff/auth.py:1330-1410`](file:///home/vrdq/spot-tui/spoff/auth.py#L1330-L1410)
   - **Root Cause**: When a playlist was local or had an invalid/deleted Spotify playlist ID, requests to Spotify returned `404: Resource not found`. Spoff aborted immediately without clearing the stale ID or creating the playlist on Spotify. Furthermore, `add_track_to_spotify_account` previously only pushed the single newly added track on playlist creation rather than syncing the existing tracks, and `sync_playlist_tracks_to_spotify` refused to sync unlinked playlists.
   - **Fix**: Added `create_spotify_playlist` and `_collect_playlist_spotify_uris`. In `add_track_to_spotify_account`, `sync_playlist_tracks_to_spotify`, and `rename_spotify_playlist`, automatically auto-heal on 404: create the playlist on Spotify, update `spotify_id` on the local playlist, and batch-upload all playlist tracks. In `sync_spotify_library`, automatically push any unlinked local playlists to Spotify so all library playlists remain synchronized.

6. **Track Removal Incomplete For Client-Side Only Tracks and Unlinked Playlists**
   - **File / Lines**: [`spoff/auth.py:1215-1265`](file:///home/vrdq/spot-tui/spoff/auth.py#L1215-L1265), [`spoff/app.py:8697-8705`](file:///home/vrdq/spot-tui/spoff/app.py#L8697-L8705)
   - **Root Cause**: `remove_track_from_spotify_account` returned `False` if a track was client-side only (not in the Spotify catalogue) or if the remote playlist returned 404. This caused Spoff background tasks to treat local removals as failures.
   - **Fix**: Handled non-Spotify and 404 remote playlist responses gracefully in `remove_track_from_spotify_account`, returning clean success since the track is already absent from Spotify. Added proper status logging in `_sync_remove_bg`.

7. **Track Deletion Confirmation Dialog UX Friction**
   - **File / Lines**: [`spoff/app.py:1657-1695`](file:///home/vrdq/spot-tui/spoff/app.py#L1657-L1695)
   - **Root Cause**: `ConfirmModal` only handled `y` and `Enter` keyboard events. Users pressing `d` (the delete key) or `Delete` a second time to confirm removal were ignored, leaving the track in place. Mouse clicks on the modal were also not bound.
   - **Fix**: Bound `d` and `delete` to `action_confirm` in `ConfirmModal`, and added mouse click detection on the confirmation hint.

---

## Validation
- Ran full test suite in an isolated temporary directory:
  `HOME=$(mktemp -d) uv run pytest`
  **Result**: 424 passed, 1 skipped in 9.99s.
- Created regression test suite [`tests/test_spotify_sync_hardening.py`](file:///home/vrdq/spot-tui/tests/test_spotify_sync_hardening.py) covering channel suffix matching, duration checks, multi-artist querying, resolved client-side playlist track sync, local ID persistence, concurrent artwork updates, 404 auto-healing on add/sync/rename, safe client-side track removal, and `ConfirmModal` bindings. All 14 tests pass.
- Invariants preserved:
  - Band names (`AC/DC`, `Florence and the Machine`, `Earth, Wind & Fire`) are preserved.
  - Duration units are respected explicitly without magnitude guessing.
  - Conservative recording identity is maintained.
  - YouTube Music tracks without Spotify counterparts remain safely client-side.

