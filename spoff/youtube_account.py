"""YouTube (Music) sign-in by way of the user's browser.

yt-dlp can no longer sign in to YouTube on its own; it can only use the login
a browser already holds. So "Log in with Google" opens Google's normal sign-in
page for YouTube Music in the default browser and waits until that browser has
a YouTube login, then every yt-dlp request uses it. Nothing is copied out of
the browser: yt-dlp reads its cookie store each time.

With a YouTube Premium account, YouTube also serves a ~256 kbps stream, which
the existing "prefer Opus" format choice then picks up.
"""
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("youtube_account")

LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube"
             "&continue=https%3A%2F%2Fmusic.youtube.com%2F")
LOGIN_COOKIES = ("SAPISID", "__Secure-3PAPISID", "LOGIN_INFO")
# A song that has YouTube Music's Premium-only high-quality stream.
PROBE_URL = "https://music.youtube.com/watch?v=dQw4w9WgXcQ"

# (yt-dlp browser name, profile path or None, keyring or None)
BrowserSpec = Tuple[str, Optional[str], Optional[str]]
# yt-dlp can't detect the keyring on some desktops (e.g. Hyprland) and then
# can't decrypt Chromium cookies, so each one is tried explicitly.
KEYRINGS = (None, "GNOMEKEYRING", "KWALLET6", "KWALLET5", "BASICTEXT")

_HOME = Path.home()
# Desktop file of the default browser -> where yt-dlp finds its cookies.
_KNOWN_BROWSERS = {
    "brave-origin": ("brave", str(_HOME / ".config/BraveSoftware/Brave-Origin/Default"), None),
    "brave-browser": ("brave", None, None),
    "brave": ("brave", None, None),
    "chromium": ("chromium", None, None),
    "google-chrome": ("chrome", None, None),
    "vivaldi-stable": ("vivaldi", None, None),
    "microsoft-edge": ("edge", None, None),
    "opera": ("opera", None, None),
    "firefox": ("firefox", None, None),
}


def default_browser() -> Optional[BrowserSpec]:
    try:
        desktop = subprocess.run(["xdg-settings", "get", "default-web-browser"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    name = desktop.removesuffix(".desktop").lower()
    for key, spec in _KNOWN_BROWSERS.items():
        if name == key or name.startswith(key):
            return spec
    return None


def candidate_browsers() -> List[BrowserSpec]:
    """The default browser first, then every other known one."""
    seen: List[BrowserSpec] = []
    first = default_browser()
    for spec in ([first] if first else []) + list(_KNOWN_BROWSERS.values()):
        if spec not in seen:
            seen.append(spec)
    return seen


def logged_in_spec(spec: BrowserSpec) -> Optional[BrowserSpec]:
    """The spec, with a working keyring, if this browser holds a YouTube login.

    Cookie values are only checked for presence, never logged or stored.
    """
    browser, profile, _ = spec
    if profile and not os.path.isdir(profile):
        return None
    from yt_dlp.cookies import extract_cookies_from_browser
    keyrings = KEYRINGS if browser != "firefox" else (None,)  # Firefox needs no keyring
    for keyring in keyrings:
        try:
            jar = extract_cookies_from_browser(browser, profile, logger=_QuietLogger(), keyring=keyring)
        except Exception:
            continue
        if any(c.domain.endswith("youtube.com") and c.name in LOGIN_COOKIES and c.value for c in jar):
            return (browser, profile, keyring)
    return None


def find_logged_in_browser() -> Optional[BrowserSpec]:
    for spec in candidate_browsers():
        found = logged_in_spec(spec)
        if found:
            return found
    return None


def wait_for_login(timeout: float = 300.0, interval: float = 3.0,
                   cancelled: Callable[[], bool] = lambda: False) -> Optional[BrowserSpec]:
    """Polls until some browser has a YouTube login, or the timeout passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not cancelled():
        spec = find_logged_in_browser()
        if spec:
            return spec
        time.sleep(interval)
    return None


def has_premium_audio(spec: BrowserSpec) -> bool:
    """Whether this login gets YouTube Music's high-quality (~256 kbps) stream."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
            "cookiesfrombrowser": (spec[0], spec[1], spec[2], None), "logger": _QuietLogger()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(PROBE_URL, download=False) or {}
    except Exception:
        logger.debug("Premium audio probe failed", exc_info=True)
        return False
    return any(f.get("vcodec") == "none" and (f.get("abr") or 0) >= 200 for f in info.get("formats") or [])


def browser_label(spec: Optional[BrowserSpec]) -> str:
    if not spec:
        return ""
    browser, profile = spec[0], spec[1]
    if profile and "Brave-Origin" in profile:
        return "Brave Origin"
    return {"chrome": "Chrome", "edge": "Edge"}.get(browser, browser.capitalize())


class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg, *a, **k): logger.debug("yt-dlp cookies: %s", msg)
    def error(self, msg, *a, **k): logger.debug("yt-dlp cookies: %s", msg)
