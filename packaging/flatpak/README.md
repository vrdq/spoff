# Flatpak Packaging for Spoff

This directory contains the Flathub-ready Flatpak packaging files for `spoff` (`dev.vrdq.spoff`).

## Files
- `dev.vrdq.spoff.yml`: The Flatpak manifest specifying runtime, permissions, and build steps.
- `dev.vrdq.spoff.desktop`: Desktop launcher entry with `Terminal=true` so it opens in the user's default terminal.
- `dev.vrdq.spoff.metainfo.xml`: AppStream metadata file with descriptions, features, and screenshots for app stores.
- `dev.vrdq.spoff.svg`: Scalable vector icon.

---

## Testing Locally

Make sure you have `flatpak` and `flatpak-builder` installed:

```bash
# On Arch / CachyOS:
sudo pacman -S flatpak flatpak-builder

# Install the Freedesktop runtime and SDK
flatpak install flathub org.freedesktop.Platform//24.08 org.freedesktop.Sdk//24.08
```

Build and test the Flatpak bundle:

```bash
cd packaging/flatpak
flatpak-builder --user --install --force-clean build-dir dev.vrdq.spoff.yml
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
3. Copy `dev.vrdq.spoff.yml`, `dev.vrdq.spoff.desktop`, `dev.vrdq.spoff.svg`, and `dev.vrdq.spoff.metainfo.xml` into the root of your branch.
4. Commit and push the branch to your fork:
   ```bash
   git add .
   git commit -m "Add dev.vrdq.spoff"
   git push origin dev.vrdq.spoff
   ```
5. Open a Pull Request from your `dev.vrdq.spoff` branch to `flathub/flathub:new-pr`.
6. The Flathub bot will run automated verification and provide a test build link. Once reviewed, your app will be live on Flathub!
