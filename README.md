# spoff

Terminal music player for Linux that streams audio from YouTube Music and Spotify metadata with automatic local caching, synchronized LRC lyrics, and a CAVA audio visualizer.

## Overview & Architecture

- **Playback & Caching**: Controlled via `mpv` through an IPC socket. Audio streams cache automatically to `~/.local/share/spoff/cache/` for offline listening without requiring Spotify Premium or API keys.
- **Spectrum Visualizer**: Connects to a `cava` FIFO pipe to render raw PCM audio spectrum data at 60 FPS in 5 selectable rendering modes.
- **Metadata & Dual Search**: Queries YouTube Music for audio streams and syncs with Spotify user libraries (`Ctrl+E` to toggle search backends).
- **Synchronized Lyrics**: Parses LRC timestamps for line-by-line synced lyrics with click-to-seek playback.
- **Desktop & D-Bus**: Exposes an MPRIS 2 interface for integration with `playerctl`, Waybar, Hyprland binds, and system lock screens.
- **Interface**: Built with Textual for terminal rendering with full vim navigation (`h`/`j`/`k`/`l`).


## Installation

### Prerequisites

- **Python 3.10+**
- **mpv** (required audio playback engine)
- **cava** (optional, for the audio visualizer)

On Arch Linux:
```bash
sudo pacman -S mpv cava python
```

On Debian / Ubuntu:
```bash
sudo apt install mpv cava python3 python3-pip
```

On Fedora:
```bash
sudo dnf install mpv cava python3
```

---

### Install via pipx (Recommended)

```bash
pipx install git+https://github.com/vrdq/spoff.git
```

To update anytime:
```bash
pipx upgrade spoff
# Or simply run:
spoff --update
```

---

### Install via uv

Run directly without installing:
```bash
uv run --with git+https://github.com/vrdq/spoff.git spoff
```

Or install as a persistent tool:
```bash
uv tool install git+https://github.com/vrdq/spoff.git
```

---

### Arch Linux (AUR / PKGBUILD)

Using `yay`:
```bash
yay -S spoff
# Or the development git version:
yay -S spoff-git
```

Or build locally with `makepkg`:
```bash
git clone https://github.com/vrdq/spoff.git
cd spoff
makepkg -si
```

---

### Manual / Virtualenv

```bash
git clone https://github.com/vrdq/spoff.git
cd spoff
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
spoff
```

---

## Quick Tour & Workflows

### 1. Search & Play
Press `1` to open Search. With **Instant Search** enabled (default), start typing immediately. Press `Enter` to play the highlighted track, or `a` to save it to a playlist. Toggle between YouTube Music and Spotify search anytime with `Ctrl+E`.

### 2. Import Any Music Link
Paste any Spotify URL (`https://open.spotify.com/playlist/...`, album, or track) or YouTube / YouTube Music URL directly into the sidebar input (`i`). Spoff parses and imports the tracklist immediately.

### 3. Browse Playlists with Vim Keys
Press `2` to focus the playlist sidebar. Use `j` and `k` to scroll through playlists—tracks update live in the main table as you move. Press `Enter` or `l` to jump into the tracklist, select a song, and press `Enter` to play. Press `h` to return to the sidebar.

### 4. Synced Lyrics
Press `4` while a song is playing to open live lyrics. Use `j` / `k` or mouse click, and press `Enter` on any line to seek playback directly to that lyric timestamp. Press `4` again to return to your previous view.

### 5. Reorder Songs & Playlists
In the playlist view, press `Shift+J` or `Shift+K` on any song to move it down or up. Reordering Spotify playlists syncs back to your Spotify account automatically. In the sidebar, `Shift+J` / `Shift+K` reorders your playlists.

---

## Keybindings Reference

### Navigation
| Key | Action |
| :--- | :--- |
| `1` | Search view |
| `2` | Playlists view (focuses sidebar) |
| `3` | Offline cached library |
| `4` | Synced lyrics view |
| `h` / `Left` | Focus playlists sidebar |
| `l` / `Right` | Focus tracks table |
| `j` / `Down` | Move cursor down |
| `k` / `Up` | Move cursor up |
| `Tab` | Cycle focus between Sidebar and Main views |
| `Escape` | Dismiss modal / unfocus input / return to table |

### Playback & Volume
| Key | Action |
| :--- | :--- |
| `Enter` | Play highlighted track |
| `Space` | Toggle Play / Pause |
| `p` / `Fn + F7` | Previous track in queue |
| `n` / `Fn + F9` | Next track in queue |
| `Left` / `Right` | Seek backward / forward 5 seconds |
| `b` | Focus progress scrub bar (use `h`/`l` for 5s, `H`/`L` for 15s, `0`-`9` for 0%-90%) |
| `s` | Toggle Shuffle mode |
| `r` | Toggle Repeat mode (`OFF` $\rightarrow$ `ALL` $\rightarrow$ `SINGLE`) |
| `c` | Copy track share link to clipboard |
| `F2` / `Down` | Lower volume (-5%) |
| `F3` / `Up` | Raise volume (+5%) |
| `F1` | Mute / Unmute audio |

### Library & Playlist Management
| Key | Action |
| :--- | :--- |
| `a` | Add highlighted track to a playlist |
| `y` | Copy playlist share link to clipboard |
| `i` | Focus playlist create / link import input |
| `Shift+J` / `Shift+Down` | Move track or playlist down |
| `Shift+K` / `Shift+Up` | Move track or playlist up |
| `d` / `Delete` | Remove song from playlist |
| `Shift+D` | Delete playlist (with confirmation modal) |

### System & Tools
| Key | Action |
| :--- | :--- |
| `Ctrl+E` | Switch search engine (**YTMusic** $\leftrightarrow$ **Spotify**) |
| `,` (or `Ctrl+,`) | Open Settings (Visualizer, Instant Search, Keybindings) |
| `L` (or `Shift+S`) | Connect / Sync Spotify account |
| `:` (or `?`) | Open Keybindings Guide modal |
| `u` | Check for updates and pull latest commit |
| `q` | Quit Spoff |

---

## Configuration & Storage

Spoff keeps configuration and data organized according to the XDG Base Directory specification:

- **Cached Songs**: `~/.local/share/spoff/cache/`
- **Playlists & Metadata**: `~/.local/share/spoff/playlists.json`
- **User Settings & Keybindings**: `~/.config/spoff/settings.json`
- **Spotify Auth Token**: `~/.config/spoff/spotify_auth.json`

To customize keybindings or defaults, you can edit `~/.config/spoff/settings.json` or adjust them directly in-app by pressing `,`.

---

## License

MIT © [vrdq](https://github.com/vrdq)
