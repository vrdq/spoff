import unittest
from unittest.mock import patch, MagicMock
import tempfile
from pathlib import Path
from textual.app import App, ComposeResult

from spoff import auth, storage
from spoff.app import SpotifyAuthModal


class DummyTestApp(App):
    def compose(self) -> ComposeResult:
        yield from ()


class TestSpotifyAuthModal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_data_dir = storage.DATA_DIR
        self.orig_auth_file = auth.AUTH_FILE
        self.orig_config_file = storage.CONFIG_FILE

        storage.DATA_DIR = Path(self.temp_dir.name)
        auth.AUTH_FILE = storage.DATA_DIR / "spotify_auth.json"
        storage.CONFIG_FILE = storage.DATA_DIR / "config.json"

    async def asyncTearDown(self):
        storage.DATA_DIR = self.orig_data_dir
        auth.AUTH_FILE = self.orig_auth_file
        storage.CONFIG_FILE = self.orig_config_file
        self.temp_dir.cleanup()

    async def test_modal_composes_input_and_uses_client_id(self):
        app = DummyTestApp()
        async with app.run_test() as pilot:
            modal = SpotifyAuthModal(first_run=True)
            await app.push_screen(modal)
            await pilot.pause()

            # Verify input and instructions are mounted
            inp = modal.query_one("#spotify-client-id-input")
            self.assertIsNotNone(inp)
            help_txt = modal.query_one("#spotify-client-id-help")
            self.assertIn("127.0.0.1:8989/login", str(help_txt.render()))

            # Type a custom client ID
            inp.value = "my_custom_client_id_999"

            with patch("spoff.app.OAuthCallbackServer") as mock_srv, \
                 patch("spoff.app.open_in_browser"), \
                 patch("spoff.app.build_auth_url", wraps=auth.build_auth_url) as spy_build_url:
                mock_inst = MagicMock()
                mock_srv.return_value = mock_inst

                modal.start_browser_login()
                await pilot.pause()

                self.assertEqual(modal.effective_client_id, "my_custom_client_id_999")
                self.assertEqual(auth.get_spotify_client_id(), "my_custom_client_id_999")
                spy_build_url.assert_called_once()
                _, kwargs = spy_build_url.call_args
                self.assertEqual(kwargs.get("client_id"), "my_custom_client_id_999")


if __name__ == "__main__":
    unittest.main()
