import unittest
from unittest.mock import Mock, patch
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

    def test_set_download_status_updates_pill_and_notif(self):
        app = Mock(spec=SpoffTUI)
        app._thread_id = 12345
        app.notifications_enabled = True
        app._download_pill_text = ""
        mock_pill = Mock()
        mock_notif = Mock()
        app.query_one = Mock(side_effect=lambda sel, *args: mock_pill if sel == "#download-pill" else mock_notif)

        # Call SpoffTUI.set_download_status on our mocked app instance
        with patch("threading.get_ident", return_value=12345):
            SpoffTUI.set_download_status(app, "[bold #569f68]⬇ INSTALLING (3/15)[/]", "Bulk installing track 3...")
            self.assertEqual(app._download_pill_text, "[bold #569f68]⬇ INSTALLING (3/15)[/]")
            mock_pill.update.assert_called_with("[bold #569f68]⬇ INSTALLING (3/15)[/]")
            app.notify_user.assert_called_with("Bulk installing track 3...", force=True)


if __name__ == "__main__":
    unittest.main()
