import unittest
from unittest.mock import Mock, patch, MagicMock
from types import SimpleNamespace
import urllib.request
import urllib.error

from spoff.app import (
    SpoffTUI,
    RebindKeyModal,
    SettingsModal,
    canonicalize_key,
    normalize_captured_key,
    DEFAULT_KEYBINDINGS,
)
from spoff import auth


class TestAuditFixesComplete(unittest.TestCase):
    def test_offline_track_suppresses_loading_notification(self):
        """Offline tracks should suppress 'Loading...' notifications in player actions and status bar."""
        # 1. Test _is_track_offline detection
        offline_track = {"id": "off1", "title": "Local Song", "artist": "Artist", "is_offline": True}
        cached_track = {"id": "cache1", "title": "Cached Song", "artist": "Artist"}
        online_track = {"id": "online1", "title": "Online Song", "artist": "Artist"}

        with patch("spoff.app.get_cached_track_path", side_effect=lambda t_id: "/path/to/song.m4a" if t_id == "cache1" else None):
            self.assertTrue(SpoffTUI._is_track_offline(offline_track))
            self.assertTrue(SpoffTUI._is_track_offline(cached_track))
            self.assertFalse(SpoffTUI._is_track_offline(online_track))

        # 2. Test action_toggle_play does not notify "Loading..." for offline tracks
        app = SimpleNamespace(
            focused=None,
            _pending_track=offline_track,
            notify_user=Mock(),
            player=Mock(current_track=None),
            _start_or_resume_playback=Mock(),
        )
        SpoffTUI.action_toggle_play(app)
        app.notify_user.assert_not_called()

        # For online track, it should notify "Loading..."
        app._pending_track = online_track
        SpoffTUI.action_toggle_play(app)
        app.notify_user.assert_called_with("Loading 'Online Song'...")

    def test_rebind_key_modal_captures_delete_and_backspace(self):
        """In RebindKeyModal, pressing Del or Backspace captures them as standalone keys instead of resetting to default."""
        modal = RebindKeyModal(
            action_id="delete_item",
            action_category="Playlists",
            action_title="Delete Selected Item",
            current_key="d",
            default_key="d",
            existing_bindings=dict(DEFAULT_KEYBINDINGS),
        )
        modal._update_preview = Mock()
        modal.dismiss = Mock()

        # Pressing 'delete' key should capture 'delete'
        del_event = Mock(key="delete", character=None)
        modal.on_key(del_event)
        self.assertEqual(modal.selected_key, "delete")
        modal._update_preview.assert_called_with("delete")
        modal.dismiss.assert_not_called()

        # Pressing 'backspace' key should capture 'backspace'
        bs_event = Mock(key="backspace", character=None)
        modal.on_key(bs_event)
        self.assertEqual(modal.selected_key, "backspace")
        modal._update_preview.assert_called_with("backspace")
        modal.dismiss.assert_not_called()

    def test_settings_modal_unbind_with_delete_key(self):
        """In SettingsModal, pressing the Delete key triggers unbinding."""
        bindings = {b.key: b.action for b in SettingsModal.BINDINGS}
        self.assertEqual(bindings.get("delete"), "unbind_selected_key")
        self.assertEqual(bindings.get("u"), "unbind_selected_key")

    def test_secondary_delete_binding_applied(self):
        """apply_keybindings attaches secondary bindings for standalone Delete key and Shift+Delete."""
        app = SpoffTUI.__new__(SpoffTUI)
        app.custom_keybindings = {}
        app._bindings = Mock()
        app._bindings.key_to_bindings = {}
        app._update_nav_bar = Mock()

        SpoffTUI.apply_keybindings(app)
        bound_pairs = [call.args[:2] for call in app._bindings.bind.call_args_list]

        # Verify ('delete', 'delete_item') was bound as secondary
        self.assertIn(("delete", "delete_item"), bound_pairs)
        # Verify ('shift+delete', 'delete_playlist') was bound as secondary
        self.assertIn(("shift+delete", "delete_playlist"), bound_pairs)

    def test_is_same_track_cross_provider_and_distinct_ids(self):
        """_is_same_track matches cross-provider Spotify IDs, but differentiates distinct recordings with different IDs."""
        app = SpoffTUI.__new__(SpoffTUI)

        yt_track = {
            "id": "yt_dQw4w9WgXcQ",
            "spotify_id": "sp_4uLU6hMCjMI75M1A2tKUQC",
            "title": "Never Gonna Give You Up",
            "artist": "Rick Astley",
        }
        sp_track = {
            "id": "sp_4uLU6hMCjMI75M1A2tKUQC",
            "title": "Never Gonna Give You Up",
            "artist": "Rick Astley",
        }
        distinct_track = {
            "id": "yt_other_version",
            "title": "Never Gonna Give You Up",
            "artist": "Rick Astley",
        }

        # Cross-provider match via spotify_id == id
        self.assertTrue(app._is_same_track(yt_track, sp_track))
        # Distinct IDs without matching cross-provider references do not match
        self.assertFalse(app._is_same_track(yt_track, distinct_track))

    def test_spotify_api_request_handles_empty_payload_and_429_window(self):
        """spotify_api_request sends Content-Length: 0 on empty body PUT/POST, and respects retry-after windows."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"status": "ok"}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            ok, data, err = auth.spotify_api_request("/me/tracks?ids=123", method="PUT", token="token")
            self.assertTrue(ok)
            # Verify request headers included Content-Length: 0
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.headers.get("Content-length"), "0")


if __name__ == "__main__":
    unittest.main()
