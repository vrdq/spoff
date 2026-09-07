# SPOFF

A fast, minimalist, dark-monochrome Spotify and YouTube music TUI with transparent offline caching. Built for Linux terminals, dotfiles enthusiasts, and distraction-free listening.

---

## Features

- **No Credentials or Cookies**: No Spotify Premium, API tokens, or web cookies required. Parses public Spotify playlist/album links directly.
- **Instant Streaming + Transparent Caching**: Tracks start streaming in seconds and simultaneously save to local storage (`~/.local/share/spoff/cache/`) for offline playback.
- **Offline Library**: Browse, search, and play cached tracks with zero internet connection in `[3] Offline Library`.
- **Local Playlist Management**: Create local playlists, add tracks with `a` or `+`, and manage playlists safely with confirmation dialogs.
- **Monochrome Terminal Aesthetic**: Designed to match dark terminal themes (`#131313`) with subtle sage green indicators, single-line borders, and zero emojis.
- **Vim Navigation**: Full support for `h`, `j`, `k`, `l`, `Tab`, and arrow keys.

---

## Installation

### Arch Linux (AUR)

Using `yay`:
```bash
yay -S spoff
# Or the development git version:
yay -S spoff-git
```

Using `paru`:
```bash
paru -S spoff
# Or:
paru -S spoff-git
```

### Manual / From Source

Prerequisites: `mpv`, `python>=3.10`

```bash
git clone https://github.com/yassinMMK/spoff.git
cd spoff
pip install .
```

Or run directly with `uv`:
```bash
uv run python3 -m spoff
```

---

## Keybindings

| Key | Action |
| :--- | :--- |
| `Enter` | Play highlighted track / Open highlighted playlist |
| `Space` | Toggle Play / Pause |
| `/` | Focus live YouTube search bar |
| `i` | Focus playlist import / create input |
| `a` / `+` | Add highlighted or playing track to playlist |
| `h` / `l` | Switch pane (Left: Playlists, Right: Tracks) |
| `Tab` | Toggle active pane focus |
| `j` / `k` (or `Down` / `Up`) | Navigate table rows |
| `Left` / `Right` | Seek backward / forward 5 seconds |
| `Up` / `Down` | Adjust volume |
| `n` / `p` | Next / Previous track in queue |
| `Delete` / `d` / `x` | Remove playlist (with confirmation) or delete track |
| `1` | View 1: Search |
| `2` | View 2: Current Playlist |
| `3` | View 3: Offline Library |
| `Escape` | Unfocus input / dismiss modal |
| `q` | Quit |

---

## License

MIT © [yassinMMK](https://github.com/yassinMMK)
