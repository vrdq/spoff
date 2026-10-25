from spoff import rtl
from spoff.app import _lyric_cell


def test_arabic_is_joined_and_in_display_order():
    # "يا ليل" -> joined forms, last word first, so a left-to-right terminal shows it right.
    shown = rtl.for_terminal("يا ليل")
    assert shown == "ﻞﻴﻟ ﺎﻳ"
    assert all(not ("؀" <= ch <= "ۿ") for ch in shown if ch != " "), "letters weren't joined"


def test_numbers_inside_arabic_keep_their_order():
    assert "2024" in rtl.for_terminal("أنا 2024 هنا")


def test_text_without_rtl_letters_is_untouched():
    for text in ("", "Boys Don't Cry", "Базовый минимум", "♪"):
        assert rtl.for_terminal(text) == text


def test_line_direction_follows_its_first_letter():
    assert rtl.is_rtl_line("يا ليل")
    assert rtl.is_rtl_line("2024 يا ليل")  # digits have no direction of their own
    assert not rtl.is_rtl_line("Habibi يا ليل")
    assert not rtl.is_rtl_line("♪")


def test_lyric_cells_align_by_direction():
    assert _lyric_cell("يا ليل", "#888888").justify == "right"
    assert _lyric_cell("Hello", "#888888").justify == "left"


def test_missing_libraries_fall_back_to_the_stored_text(monkeypatch):
    rtl.for_terminal.cache_clear()
    monkeypatch.setattr(rtl, "_reshape", None)
    try:
        assert rtl.for_terminal("يا ليل") == "يا ليل"
    finally:
        rtl.for_terminal.cache_clear()
