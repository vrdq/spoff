# SPOFF

A fast, minimalist, dark-monochrome Spotify and YouTube music TUI with transparent offline caching. Built for Linux terminals, dotfiles enthusiasts, and distraction-free listening.

---

## Features

- **No Credentials Required (Default)**: Works out of the box with no Spotify Premium, API tokens, or web cookies required. Parses public Spotify playlist/album links directly.
- **Seamless Spotify Account Sync**: Optional one-click Spotify login (`L` or `S`) via browser OAuth (PKCE, zero developer portal setup) to sync private playlists and Liked Songs into Spoff.
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

### Arch Linux (Local Build)

If AUR registration is unavailable, build and install locally with pacman:

```bash
git clone https://github.com/vrdq/spoff.git
cd spoff
makepkg -si
```

### Manual / From Source

Prerequisites: `mpv`, `python>=3.10`

Using standard Python virtual environment:
```bash
git clone https://github.com/vrdq/spoff.git
cd spoff
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
spoff
```

Using `pipx`:
```bash
pipx install .
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
| `Space` / `F8` | Toggle Play / Pause |
| `F1` | Mute / Unmute audio |
| `F2` | Lower volume (-5%) |
| `F3` | Raise volume (+5%) |
| `F7` / `p` | Previous track in queue |
| `F9` / `n` | Next track in queue |
| `b` | Focus song scrub / seek bar |
| `h` / `l` | Seek -/+ 5s on scrub bar (or switch sidebar/tracks) |
| `H` / `L` | Fast seek -/+ 15s on scrub bar |
| `0` – `9` | Jump directly to 0% – 90% of song on scrub bar |
| `/` | Focus live YouTube search bar |
| `:` (or `Shift`+`;`) | Open Keybindings & Usage Guide cheat sheet |
| `L` / `S` | Spotify account login & library sync dialog |
| `i` | Focus playlist import / create input |
| `a` / `+` | Add highlighted or playing track to playlist |
| `Tab` | Cycle active focus (Sidebar $\rightarrow$ Tracks $\rightarrow$ Scrub Bar) |
| `j` / `k` (or `Down` / `Up`) | Navigate table rows (scrolling past last row focuses Scrub Bar) |
| `Left` / `Right` | Seek backward / forward 5 seconds |
| `Up` / `Down` | Adjust volume |
| `Delete` / `d` / `x` | Remove playlist (with confirmation) or delete track |
| `1` | View 1: Search |
| `2` | View 2: Playlist |
| `3` | View 3: Offline Library |
| `Escape` | Unfocus input / return from scrub bar to tracks / dismiss modal |
| `q` | Quit |

---

## License

MIT © [vrdq](https://github.com/vrdq)
