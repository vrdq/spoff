import unittest
import os
import inspect
from pathlib import Path
from spoff.mpris import SpoffMPRISDbus, MPRISService
from spoff.player import MPVController

class TestMPRISAndAudio(unittest.TestCase):
    def test_mpris_dbus_interface_compliance(self):
        self.assertEqual(SpoffMPRISDbus.DesktopEntry, "spoff")
        self.assertIn("<property name='DesktopEntry' type='s' access='read'/>", SpoffMPRISDbus.dbus)
        self.assertIn("<signal name='Seeked'>", SpoffMPRISDbus.dbus)
        self.assertIn("<arg direction='out' name='Position' type='x'/>", SpoffMPRISDbus.dbus)

    def test_mpris_properties_changed_emission(self):
        callbacks = {
            "set_volume": lambda v: None,
            "set_loop_status": lambda s: None,
            "set_shuffle": lambda b: None,
        }
        service = MPRISService(callbacks)
        ok = service.start()
        self.assertTrue(ok)
        try:
            track = {
                "id": "dQw4w9WgXcQ",
                "title": "Never Gonna Give You Up",
                "artist": "Rick Astley",
                "duration_ms": 213000,
                "album": "Whenever You Need Somebody"
            }
            # Test update_track does not raise TypeError
            service.update_track(track, 213.0)
            self.assertEqual(service.dbus_obj.PlaybackStatus, "Playing")
            self.assertIn("xesam:title", service.dbus_obj.Metadata)
            self.assertIn("mpris:artUrl", service.dbus_obj.Metadata)
            self.assertIn("xesam:url", service.dbus_obj.Metadata)
            self.assertEqual(str(service.dbus_obj.Metadata["xesam:url"]), "'https://www.youtube.com/watch?v=dQw4w9WgXcQ'")

            # Test update_status
            service.update_status(True, False)
            self.assertEqual(service.dbus_obj.PlaybackStatus, "Playing")
            service.update_status(True, True)
            self.assertEqual(service.dbus_obj.PlaybackStatus, "Paused")

            # Test update_volume
            service.update_volume(75)
            self.assertAlmostEqual(service.dbus_obj._volume, 0.75, places=2)

            # Test update_loop_status and update_shuffle
            service.update_loop_status("one")
            self.assertEqual(service.dbus_obj._loop_status, "Track")
            service.update_shuffle(True)
            self.assertTrue(service.dbus_obj._shuffle)

            # Test emit_seeked
            service.emit_seeked(30.0)
            self.assertEqual(service.dbus_obj.Position, 30000000)

            # Test stop / track None
            service.update_track(None)
            self.assertEqual(service.dbus_obj.PlaybackStatus, "Stopped")
            self.assertEqual(service.dbus_obj.Metadata, {})
        finally:
            service.stop()

    def test_mpris_youtube_art_url_derivation(self):
        callbacks = {}
        service = MPRISService(callbacks)
        ok = service.start()
        self.assertTrue(ok)
        try:
            yt_track = {
                "id": "Vjo7pCekqSQ",
                "title": "as the world caves in",
                "artist": "angélina",
                "duration_ms": 247000
            }
            service.update_track(yt_track, 247.0)
            self.assertIn("mpris:artUrl", service.dbus_obj.Metadata)
            self.assertEqual(str(service.dbus_obj.Metadata["mpris:artUrl"]), "'https://img.youtube.com/vi/Vjo7pCekqSQ/hqdefault.jpg'")
        finally:
            service.stop()

    def test_mpv_audio_client_name_flags(self):
        controller = MPVController(socket_path="/tmp/test_audit_flags.sock")
        src = inspect.getsource(controller.start_mpv)
        self.assertIn("--audio-client-name=spoff", src)
        self.assertIn("--title=spoff", src)
        self.assertIn("--force-media-title=spoff", src)

    def test_spoff_desktop_file_and_icon(self):
        desktop_path = Path.home() / ".local/share/applications/spoff.desktop"
        self.assertTrue(desktop_path.exists())
        content = desktop_path.read_text()
        self.assertIn("Name=Spoff", content)
        self.assertIn("Icon=spoff", content)
        self.assertIn("Exec=", content)

if __name__ == "__main__":
    unittest.main()
