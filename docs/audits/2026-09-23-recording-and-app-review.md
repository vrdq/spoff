# Recording identity and application review

Reviewed the existing application and the uncommitted feature changes on top of `767982b`. This report describes the final fixes, not a claim that every execution path is bug-free.

## Confirmed user-visible failure

Spotify track `3fuyYaLhZ2RoP9eWpvfP1H` is **A New Kind Of Love – Demo**, with stored duration 259.071 seconds. Its cached file measured **129.214626 seconds**. Spoff's original YouTube Music query returned **a new kind of love (gamvae remix)** first. The resolver accepted the artist match without checking the recording name, version, or duration.

The updated resolver was exercised against the live providers and selected **Frou Frou - A New Kind Of Love (Demo) (Official Audio)**, YouTube ID `I5VZyZaeQC0`, duration **259 seconds**. Audio was not fingerprinted or compared by listening. The originally observed bad cache file and its index entry were subsequently absent when the repair step ran, so that step did not alter the user's cache.

## Fixed defects

| Location | Root cause and failure | Fix and regression coverage |
|---|---|---|
| `spoff/matching.py:27`, `spoff/streamer.py:77` | Artist-only selection accepted remixes, live versions, covers, or different songs. | Match normalized title while retaining version tags, match artist names, and enforce duration tolerance when known. Reject insufficient metadata. Test demo versus remix, unrelated title/artist, and duration mismatch. |
| `spoff/streamer.py:77` | Failed extraction of a selected YouTube/YouTube Music URL silently switched to a title search. | Exact URLs are authoritative and have no substitute-search fallback. Test failed exact URLs and intentionally selected remixes. |
| `spoff/streamer.py:77` | Bare video IDs in `direct_url` were ignored; eleven-character song names could instead be mistaken for video IDs. | Interpret IDs only in the direct-source argument. Test both cases. |
| `spoff/streamer.py:60`, `spoff/app.py:8020` | Previously cached wrong audio bypassed corrected resolution; download fast paths could bypass verification. | Probe known durations in worker paths before reuse, propagate expected duration to resolution and download validation, and bypass mismatches. Test old 129-second audio against the requested 259 seconds. |
| `spoff/streamer.py:204` | A corrected file in another extension could remain shadowed by an older cache file preferred by extension ordering. | Archive superseded extensions after publishing the replacement. Test wrong `.m4a` replaced by correct `.webm`; original bytes remain in an archive. |
| `spoff/search.py:107`, `spoff/app.py:8909` | YouTube URLs with reordered query parameters missed the direct parser; failed links could become broad name searches. | Parse URL query parameters and keep failed direct-link searches explicit. Test reordered parameters and refusal to substitute a search. |
| `spoff/search.py:107` | Spotify oEmbed titles containing ` - ` were incorrectly split into artist/title, and API failures lost useful artist/duration information. | Try public embed metadata before oEmbed; retain oEmbed title literally. |
| `spoff/auth.py:618`, `spoff/auth.py:873` | Sync stripped parenthesized version tags and accepted the first Spotify result, allowing incorrect remote likes/removals or merges. | Preserve version tags and validate candidates; do not inherit a client playback URL merely from a metadata match. Tests cover demo/remix separation and rejecting the first wrong Spotify result. |
| `spoff/storage.py:1388` | Offline registration discarded the original source URL and resolved recording identity. | Preserve source/URI/artwork and resolved title/URL through re-registration. Regression test checks both first registration and later metadata-only registration. |
| `spoff/app.py:7111` | Liked-song refresh read its local baseline after the network fetch, allowing a remote snapshot to undo a local unlike. | Capture before fetch, compare and merge inside a storage transaction, and preserve intervening edits. Test local removal during fetch. Also transact the later Spotify-ID merge into liked storage. |
| `spoff/app.py:5767`, `spoff/app.py:8020` | Completion callbacks changed download state off the UI thread, and a previous completion timer could clear a newer progress indicator. | Dispatch completion/error callbacks onto the UI thread; invalidate old timers on every status update. Timer regression test included. |
| `spoff/player.py:262` | Failure to start mpv or create its IPC socket escaped the normal playback failure return path. | Log, clean up, and return failure so the application can recover. Test missing mpv. |
| `spoff/visualizer.py:94` | Failure to start the reader thread after spawning CAVA dropped the process reference and leaked its configuration/process. | Run existing cleanup on startup failure. Test process termination and config removal. |
| `spoff/lyrics.py:100` | Lyrics search accepted its first unrelated result; live/remaster version labels were stripped from queries. | Check fallback title, artist and duration, retain those version labels, and normalize duration input. Test unrelated first result. |
| `spoff/storage.py` cache reconciliation | An I/O or permission error was treated as proof that cached audio had been deleted. | Preserve the index entry on non-FileNotFound I/O failures. |

The commit also preserves the checkout's existing direct-link search, playlist duplicate controls, keyboard handling, download indicators, and Spotify liked-song integration changes.

## Coverage and validation

- Static syntax scan across **all 17 Python modules**, including Python 3.10 grammar compatibility. Actual isolated storage import checked on Python 3.12.
- Risk-focused source review across app/UI workers, auth/sync, player IPC and shutdown, streamer/download lifecycle, storage transactions and cache paths, search, Spotify/YT Music adapters, EQ, lyrics, artwork, MPRIS, visualizer, updater, and package entrypoints. Earlier audit coverage informed the review of unchanged code; the large application also contains CSS and modal presentation code. This is not exhaustive dynamic path coverage.
- Pyflakes across the whole package: no undefined-name findings; existing unused imports/locals remain.
- Isolated full suite: **340 passed, 1 skipped, 4 subtests passed**. The skip is the desktop-file installation check in the temporary home. The real mpv rapid-skip/EOF/cleanup regression ran successfully.
- New recording and recovery cases are in `tests/test_recording_identity.py`; tests mock remote writes and use temporary storage.
- Live resolution of the reported track succeeded without a Spotify credential exchange or remote account mutation.
- `git diff --check` passed.

## Practical limits

Metadata matching is conservative, not audio fingerprinting. Equal-title/equal-duration recordings can still be ambiguous across catalogs. Ambiguous or unavailable matches now fail rather than deliberately taking an unchecked substitute. This may reject some legitimate uploads with unusual metadata. Existing same-duration bad caches cannot be reliably identified by a duration probe alone; missing ffprobe also prevents that check. Exact selected YouTube URLs do not require a cross-catalog match.

Provider availability, geographic restrictions, authentication throttling, and yt-dlp extractor changes remain external failure sources. The suite does not simulate every network outage or every desktop/D-Bus environment. No authenticated remote library was modified as part of validation. Shutdown tests cover normal and rapid playback lifecycle; abrupt process termination cannot guarantee completion of outstanding network edits.

## Follow-up: download completion ownership

A further lifecycle review found that download futures invoked callbacks before the job released its semaphore slot and active-job registration. A callback chaining another download could receive a false capacity error. Jobs now retire their registration and release the slot before delivering success or failure. Tests use a one-slot semaphore and chain a second blocking download from both callback paths, verifying exactly one slot is returned.

Completion notification exceptions also previously called the download-error callback, incorrectly presenting successfully saved audio as failed. Callback exceptions are now logged separately from transfer errors. The same delivery function handles subscribers waiting on an existing download.

Validation after these fixes: **343 passed, 1 skipped, 4 subtests passed**; `git diff --check` clean. These are additional verified fixes; the broader reliability goal remains active.

## Visible search and playback activity

Added a dedicated status above the seek bar for `Searching…` and `Loading song…`. It remains visible with notifications disabled and can show both operations together. Search completion only clears the status belonging to that request, so stale results cannot hide a newer search. Playback activity follows the existing pending-track state and clears on commit, cancellation, or exhaustion of playable queue items.

Search and playback preparation now route unexpected exceptions through visible error handling and the existing playback recovery path. Tests cover success, exceptions, overlapping activity, stale completion, and actual Textual layout at 100×30 and 60×20 terminal sizes.

Validation: **350 passed, 1 skipped, 4 subtests passed**. The broader reliability goal remains active.

## Refinement: one grey status line

The separate loading and download badges have been removed following user feedback. The existing grey line above the seek bar is now the single status surface for search, song loading, download progress and results. It displays one message at a time: foreground loading/search takes priority, brief action confirmations can interrupt background progress, and the latest download status resumes afterward. Single and bulk download state remain independently owned so one completion timer cannot clear another operation.

Removed duplicate download and copied-link popups and the update modal's duplicate messages in the obscured playback deck. Download wording now consistently uses “Downloading” and “Saved offline” instead of mixing installing, caching, badges and notifications. The status text remains grey with increased contrast. Bulk cache checking has immediate feedback; an unexpected bulk failure replaces progress with an actionable error.

Validation: **356 passed, 1 skipped, 4 subtests passed**. Tests exercise real single/bulk download control flow with mocked transfers, already-cached playlists, status priority and timer ownership, disabled notifications, and the full Textual layout at 100×30 and 60×20. They verify the old badges are absent and progress occupies the existing line. Pyflakes reports no undefined names; only pre-existing unused imports/locals remain. `git diff --check` is clean.
