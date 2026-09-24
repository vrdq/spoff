# Playback, cache, and playlist reliability follow-up

## Fixed failures

- `spoff/app.py:6358`: entering Playlists after the selected playlist disappeared
  kept its old ID and tracks. Resolve the selection against the latest saved
  library, fall back to the first remaining playlist, or clear it when none
  remain. Refresh the sidebar from the same library snapshot. Headless tests
  cover both disappearance cases and check the actual track/sidebar row counts.
- `spoff/app.py:8195`, `:8287`, `:8327`, `:8344`, `:9465`: cache completion
  did not update Liked Songs; bulk downloads also missed Search and other
  playlists containing the same songs. Refresh the currently displayed track
  collection on completion. Tests exercise manual and bulk completion in Search,
  Playlists, and Liked Songs, including an already-cached bulk operation.
- `spoff/streamer.py:306`: a second unguarded filesystem stat after cache lookup
  could raise when the file disappeared. Let guarded cache registration validate
  the file and deliver the failure once through the error callback. Removed the
  duplicate manual-download shortcut in app.py that swallowed registration
  failures and repeated the same filesystem race.
- `spoff/player.py:275`, `:323`: thread-start failure could escape playback setup
  and leave MPV running with an open playback socket. Handle RuntimeError in
  startup cleanup and publish the playback thread only after it starts, avoiding
  a join on an unstarted thread. A fault-injection test verifies process
  termination, stream/socket closure, and cleared playback state.

## Verification

403 tests passed, 1 skipped, 4 subtests passed using an isolated home directory.
The suite includes actual MPV rapid-switch/EOF/cleanup coverage, recording
identity checks, and headless sidebar/status tests. `git diff --check` passed.
The skip is the desktop-file installation check in the isolated home.

The user's sidebar preferences remain: playlist names without counts or arrows,
and a grey selection highlight only while the sidebar has focus. Network service
availability and every possible playback combination are not proven by this run;
this is a verified reliability improvement, not a claim that all bugs are gone.
