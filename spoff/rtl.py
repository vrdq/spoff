"""Arabic, Persian and Hebrew text for terminals that don't lay it out themselves.

Most terminals (kitty, alacritty, foot, wezterm) print characters left to right in
the order they're stored, each letter in its isolated form. So right-to-left text
comes out backwards with no joined letters. This joins the letters (Arabic
presentation forms) and reorders the line into display order, the way the text
would look on paper.

Shape whole strings, after any truncation: the result is in visual order, so
cutting it afterwards would remove the start of the sentence, not the end.
"""
import logging
import unicodedata
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    from arabic_reshaper import reshape as _reshape
    from bidi import get_display as _get_display
except ImportError:  # optional on distro packages that don't ship them
    _reshape = _get_display = None


def _is_rtl_char(ch: str) -> bool:
    return unicodedata.bidirectional(ch) in ("R", "AL")


def has_rtl(text: str) -> bool:
    return any(_is_rtl_char(ch) for ch in text)


def is_rtl_line(text: str) -> bool:
    """True when the first letter with a direction is right-to-left, which is
    how the Unicode bidi algorithm picks a paragraph's direction."""
    for ch in text:
        direction = unicodedata.bidirectional(ch)
        if direction in ("R", "AL"):
            return True
        if direction == "L":
            return False
    return False


@lru_cache(maxsize=2048)
def for_terminal(text: str) -> str:
    """The text as a left-to-right terminal should print it. Unchanged when it has
    no right-to-left letters or the shaping libraries aren't installed."""
    if not text or _reshape is None or not has_rtl(text):
        return text
    try:
        return _get_display(_reshape(text))
    except Exception:
        logger.warning("Could not lay out right-to-left text; showing it as stored", exc_info=True)
        return text
