import unittest
from unittest.mock import Mock, patch
from textual.widgets import Static
from spoff.app import (
    canonicalize_key,
    format_key_display,
    normalize_captured_key,
    key_matches,
    SpoffTUI,
    DEFAULT_KEYBINDINGS,
)


class TestDownloadStatusAndDelKey(unittest.TestCase):
    def test_canonicalize_del_and_delete(self):
        self.assertEqual(canonicalize_key("del"), "delete")
        self.assertEqual(canonicalize_key("DEL"), "delete")
        self.assertEqual(canonicalize_key("delete"), "delete")
        self.assertEqual(canonicalize_key("Delete"), "delete")
        self.assertEqual(canonicalize_key("shift+del"), "shift+delete")
        self.assertEqual(canonicalize_key("ctrl+del"), "ctrl+delete")

    def test_format_key_display_del(self):
        self.assertEqual(format_key_display("delete"), "Del")
        self.assertEqual(format_key_display("del"), "Del")
        self.assertEqual(format_key_display("shift+delete"), "Shift+Del")
        self.assertEqual(format_key_display("d"), "d")
        self.assertEqual(format_key_display("D"), "D")

    def test_normalize_captured_key_del(self):
        self.assertEqual(normalize_captured_key("del", None), "delete")
        self.assertEqual(normalize_captured_key("delete", None), "delete")
        self.assertEqual(normalize_captured_key("d", "d"), "d")
        self.assertEqual(normalize_captured_key("D", "D"), "D")

    def test_del_key_standalone_not_confused_with_d_or_shift_d(self):
        # When bound to delete, only del/delete matches, NOT d or D
        self.assertTrue(key_matches("delete", None, "delete"))
        self.assertTrue(key_matches("del", None, "delete"))
        self.assertFalse(key_matches("d", "d", "delete"))
        self.assertFalse(key_matches("D", "D", "delete"))

        # When bound to d, only d matches, NOT del or delete
        self.assertTrue(key_matches("d", "d", "d"))
        self.assertFalse(key_matches("delete", None, "d"))
        self.assertFalse(key_matches("del", None, "d"))
        self.assertFalse(key_matches("D", "D", "d"))

        # When bound to D, only D matches, NOT del or delete or shift+delete
        self.assertTrue(key_matches("D", "D", "D"))
        self.assertFalse(key_matches("delete", None, "D"))
        self.assertFalse(key_matches("del", None, "D"))
        self.assertFalse(key_matches("shift+delete", None, "D"))

    def test_download_progress_uses_only_existing_status_line(self):
        from types import SimpleNamespace
        status = Mock()
        app = SimpleNamespace(_thread_id=12345, notifications_enabled=False,
                              query_one=Mock(return_value=status))
        with patch("threading.get_ident", return_value=12345):
            SpoffTUI.set_download_status(app, "Downloading 3/15 from 'Favorites': Song")
        app.query_one.assert_called_once_with("#notification-line", Static)
        status.update.assert_called_once_with("Downloading 3/15 from 'Favorites': Song")


if __name__ == "__main__":
    unittest.main()
