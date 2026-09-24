# Review of changes after 47896b5

Reviewed the changes through 9b9874f against the preceding recording-identity,
cache-refresh, and status-line fixes. Three regressions were found and repaired.

1. **Duration unit guessing** (`spoff/streamer.py:62`, `:70`, `:88`,
   `spoff/matching.py`, `spoff/lyrics.py`). The new conversion divided any numeric
   value above 1000 by 1000, including provider durations already in seconds.
   A 20-minute recording became 1.2 seconds; a correct `20:00` candidate failed
   matching while a 1.2-second clip passed. Cache checks also rejected legitimate
   long audio. Restored explicit millisecond conversion at the named input
   boundaries and seconds parsing for provider/ffprobe outputs. Reproduced both
   matcher failures directly against the committed 9b9874f source.

2. **Band names split into unrelated artist identities**
   (`spoff/matching.py:21`). Splitting slashes, ampersands, and words such as
   `and` allowed `AC` to match `AC/DC`, affecting playback selection and library
   matching. Kept comma and explicit featured-credit splitting; preserved the
   other characters as part of artist names. Reproduced the AC/DC false match
   against 9b9874f. Tests cover AC/DC, Florence and the Machine, and Earth,
   Wind & Fire. Ambiguous collaborator strings are deliberately not guessed.

3. **Pending cached playback bypassed request guards**
   (`spoff/app.py:7099`, `:7572`). Suppressing a loading notice also bypassed the
   early return. Repeated play/row selection could restart preparation or toggle
   the previous track before the new cached track committed. Restored the pending
   request guard independently of whether a notice is displayed. Tests cover
   startup, a previous track, and selecting the pending row.

Validation: 391 tests passed, 1 skipped, 4 subtests passed in an isolated home;
`git diff --check` passed. Existing recording-identity tests (including Crave
You and remix rejection) and headless single-status-line tests remain green.
The playlist/search/offline cache completion refresh remains in the source.
Useful MPRIS metadata, playlist normalization, and deletion-confirmation changes
were retained. No live Spotify write or new listening comparison was performed.
These checks establish regression coverage, not a guarantee of no remaining bugs.
