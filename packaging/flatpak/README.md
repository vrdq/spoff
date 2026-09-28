# Flatpak Packaging for Spoff

This directory contains the Flathub-ready Flatpak packaging files for `spoff` (`dev.vrdq.spoff`).

## Files
- `dev.vrdq.spoff.yml`: The Flatpak manifest: runtime, permissions, and build steps (mpv, ffmpeg, cava and deno are built or bundled in).
- `python3-modules.yaml`: Every Python dependency, pinned with checksums. Regenerate it after changing dependencies in `pyproject.toml` (see below).
- `dev.vrdq.spoff.desktop`: Desktop launcher entry with `Terminal=true` so it opens in the user's default terminal.
- `dev.vrdq.spoff.metainfo.xml`: AppStream metadata file with descriptions, features, and screenshots for app stores.
- `dev.vrdq.spoff.svg`: Scalable vector icon.

---

## Testing Locally

Make sure you have `flatpak` and `flatpak-builder` installed:

```bash
# On Arch / CachyOS:
sudo pacman -S flatpak flatpak-builder

# Install the GNOME runtime and SDK (GNOME ships PyGObject, which MPRIS needs)
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50
```

Build and test the Flatpak bundle:

```bash
cd packaging/flatpak
flatpak-builder --user --install --force-clean build-dir dev.vrdq.spoff.yml
```

The first build compiles ffmpeg, mpv and their libraries, which takes a while; later builds reuse them.

Regenerate `python3-modules.yaml` after changing dependencies:

```bash
curl -LO https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py
uv run --with requirements-parser --with pyyaml --with pip python flatpak-pip-generator.py \
  --runtime org.gnome.Sdk//50 --prefer-wheels cryptography,cffi --yaml -o python3-modules \
  "pydbus>=0.6.0" "rich>=13.0.0" "secretstorage>=3.3" "textual>=8.0.0" \
  "yt-dlp-ejs>=0.8.0" "yt-dlp>=2024.0.0" "ytmusicapi>=1.12.0" hatchling
```

Run the installed Flatpak:

```bash
flatpak run dev.vrdq.spoff
```

---

## Submitting to Flathub

1. Fork the [flathub/flathub](https://github.com/flathub/flathub) repository on GitHub.
2. Clone your fork locally and create a new branch named `dev.vrdq.spoff`:
   ```bash
   git checkout -b dev.vrdq.spoff
   ```
3. Copy `dev.vrdq.spoff.yml`, `python3-modules.yaml`, `dev.vrdq.spoff.desktop`, `dev.vrdq.spoff.svg`, and `dev.vrdq.spoff.metainfo.xml` into the root of your branch.
4. Commit and push the branch to your fork:
   ```bash
   git add .
   git commit -m "Add dev.vrdq.spoff"
   git push origin dev.vrdq.spoff
   ```
5. Open a Pull Request from your `dev.vrdq.spoff` branch to `flathub/flathub:new-pr`.
6. The Flathub bot will run automated verification and provide a test build link. Once reviewed, your app will be live on Flathub!
