import os
import logging
import threading
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger("mpris")

try:
    from pydbus import SessionBus
    from pydbus.generic import signal
    from gi.repository import GLib
    HAS_DBUS = True
except Exception as e:
    logger.debug(f"pydbus or GLib not available: {e}")
    HAS_DBUS = False

class SpoffMPRISDbus:
    """
    D-Bus object implementing the org.mpris.MediaPlayer2 and
    org.mpris.MediaPlayer2.Player specifications.
    """
    dbus = """
    <node>
        <interface name='org.mpris.MediaPlayer2'>
            <method name='Raise'/>
            <method name='Quit'/>
            <property name='CanQuit' type='b' access='read'/>
            <property name='CanRaise' type='b' access='read'/>
            <property name='HasTrackList' type='b' access='read'/>
            <property name='Identity' type='s' access='read'/>
            <property name='SupportedUriSchemes' type='as' access='read'/>
            <property name='SupportedMimeTypes' type='as' access='read'/>
        </interface>
        <interface name='org.mpris.MediaPlayer2.Player'>
            <method name='Next'/>
            <method name='Previous'/>
            <method name='Pause'/>
            <method name='PlayPause'/>
            <method name='Stop'/>
            <method name='Play'/>
            <method name='Seek'>
                <arg direction='in' name='Offset' type='x'/>
            </method>
            <method name='SetPosition'>
                <arg direction='in' name='TrackId' type='o'/>
                <arg direction='in' name='Position' type='x'/>
            </method>
            <method name='OpenUri'>
                <arg direction='in' name='Uri' type='s'/>
            </method>
            <property name='PlaybackStatus' type='s' access='read'/>
            <property name='LoopStatus' type='s' access='readwrite'/>
            <property name='Rate' type='d' access='readwrite'/>
            <property name='Shuffle' type='b' access='readwrite'/>
            <property name='Metadata' type='a{sv}' access='read'/>
            <property name='Volume' type='d' access='readwrite'/>
            <property name='Position' type='x' access='read'/>
            <property name='CanControl' type='b' access='read'/>
            <property name='CanPlay' type='b' access='read'/>
            <property name='CanPause' type='b' access='read'/>
            <property name='CanSeek' type='b' access='read'/>
            <property name='CanGoNext' type='b' access='read'/>
            <property name='CanGoPrevious' type='b' access='read'/>
        </interface>
    </node>
    """
    if HAS_DBUS:
        PropertiesChanged = signal()

    # Root Interface
    CanQuit = True
    CanRaise = False
    HasTrackList = False
    Identity = "Spoff"
    SupportedUriSchemes = ["file", "http", "https"]
    SupportedMimeTypes = ["audio/mpeg", "audio/ogg", "audio/flac", "audio/webm"]

    def __init__(self, callbacks: Dict[str, Callable]):
        self.callbacks = callbacks
        self.PlaybackStatus = "Stopped"
        self.LoopStatus = "None"
        self.Rate = 1.0
        self.Shuffle = False
        self._volume = 0.8
        self.Position = 0
        self.CanControl = True
        self.CanPlay = True
        self.CanPause = True
        self.CanSeek = True
        self.CanGoNext = True
        self.CanGoPrevious = True

    @property
    def Volume(self) -> float:
        return self._volume

    @Volume.setter
    def Volume(self, val: float) -> None:
        self._volume = max(0.0, min(1.0, float(val)))
        fn = self.callbacks.get("set_volume")
        if fn:
            fn(int(round(self._volume * 100)))

    # Root Methods
    def Raise(self) -> None:
        fn = self.callbacks.get("raise")
        if fn:
            fn()

    def Quit(self) -> None:
        fn = self.callbacks.get("quit")
        if fn:
            fn()

    # Player Methods
    def Next(self) -> None:
        fn = self.callbacks.get("next")
        if fn:
            fn()

    def Previous(self) -> None:
        fn = self.callbacks.get("prev")
        if fn:
            fn()

    def Pause(self) -> None:
        fn = self.callbacks.get("pause")
        if fn:
            fn()

    def PlayPause(self) -> None:
        fn = self.callbacks.get("play_pause")
        if fn:
            fn()

    def Stop(self) -> None:
        fn = self.callbacks.get("stop")
        if fn:
            fn()

    def Play(self) -> None:
        fn = self.callbacks.get("play")
        if fn:
            fn()

    def Seek(self, offset_us: int) -> None:
        fn = self.callbacks.get("seek")
        if fn:
            fn(float(offset_us) / 1_000_000.0)

    def SetPosition(self, track_id: str, pos_us: int) -> None:
        fn = self.callbacks.get("set_position")
        if fn:
            fn(float(pos_us) / 1_000_000.0)

    def OpenUri(self, uri: str) -> None:
        fn = self.callbacks.get("open_uri")
        if fn:
            fn(uri)


class MPRISService:
    """
    Manager for the Spoff MPRIS D-Bus lifecycle and synchronization.
    """
    def __init__(self, callbacks: Dict[str, Callable]):
        self.callbacks = callbacks
        self.dbus_obj: Optional[SpoffMPRISDbus] = None
        self.publication = None
        self.loop: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._started = False

    def start(self) -> bool:
        if not HAS_DBUS or self._started:
            return False

        try:
            self.loop = GLib.MainLoop()
            self._thread = threading.Thread(target=self.loop.run, daemon=True)
            self._thread.start()

            bus = SessionBus()
            self.dbus_obj = SpoffMPRISDbus(self.callbacks)
            try:
                self.publication = bus.publish(
                    "org.mpris.MediaPlayer2.spoff",
                    ("/org/mpris/MediaPlayer2", self.dbus_obj)
                )
                logger.info("MPRIS service registered at org.mpris.MediaPlayer2.spoff")
            except Exception as e:
                logger.debug(f"Primary MPRIS bus name busy: {e}, falling back to instance name")
                self.publication = bus.publish(
                    f"org.mpris.MediaPlayer2.spoff.instance{os.getpid()}",
                    ("/org/mpris/MediaPlayer2", self.dbus_obj)
                )
                logger.info(f"MPRIS service registered at org.mpris.MediaPlayer2.spoff.instance{os.getpid()}")
            self._started = True
            return True
        except Exception as e:
            logger.warning(f"Failed to publish MPRIS service: {e}")
            self.dbus_obj = None
            return False

    def update_track(self, track: Optional[Dict[str, Any]], duration_sec: float = 0.0) -> None:
        if not self.dbus_obj:
            return

        if not track:
            self.dbus_obj.Metadata = {}
            self.dbus_obj.PlaybackStatus = "Stopped"
            try:
                self.dbus_obj.PropertiesChanged(
                    "org.mpris.MediaPlayer2.Player",
                    {
                        "Metadata": GLib.Variant("a{sv}", {}),
                        "PlaybackStatus": GLib.Variant("s", "Stopped"),
                    },
                    []
                )
            except Exception:
                pass
            return

        raw_id = str(track.get("id") or hash(track.get("title", "") + track.get("artist", "")))
        clean_id = "".join(c for c in raw_id if c.isalnum() or c == "_") or "track"
        track_obj_path = f"/org/spoff/track/t_{clean_id}"

        title = track.get("title", "Unknown Title")
        artist = track.get("artist", "Unknown Artist")
        artists_list = [a.strip() for a in artist.split(",") if a.strip()] or [artist]
        dur_us = int(duration_sec * 1_000_000)
        if dur_us <= 0:
            dur_ms = track.get("duration_ms") or 0
            dur_us = int(dur_ms * 1000)

        meta: Dict[str, Any] = {
            "mpris:trackid": GLib.Variant("o", track_obj_path),
            "xesam:title": GLib.Variant("s", str(title)),
            "xesam:artist": GLib.Variant("as", artists_list),
            "mpris:length": GLib.Variant("x", max(0, dur_us))
        }

        art_url = track.get("art_url") or track.get("thumbnail")
        if art_url:
            meta["mpris:artUrl"] = GLib.Variant("s", str(art_url))

        album = track.get("album")
        if album:
            meta["xesam:album"] = GLib.Variant("s", str(album))

        self.dbus_obj.Metadata = meta
        self.dbus_obj.PlaybackStatus = "Playing"

        try:
            self.dbus_obj.PropertiesChanged(
                "org.mpris.MediaPlayer2.Player",
                {
                    "Metadata": GLib.Variant("a{sv}", meta),
                    "PlaybackStatus": GLib.Variant("s", "Playing"),
                },
                []
            )
        except Exception as e:
            logger.debug(f"Error emitting MPRIS PropertiesChanged: {e}")

    def update_status(self, is_playing: bool, is_paused: bool) -> None:
        if not self.dbus_obj:
            return

        status = "Playing" if is_playing and not is_paused else ("Paused" if is_playing and is_paused else "Stopped")
        if self.dbus_obj.PlaybackStatus == status:
            return

        self.dbus_obj.PlaybackStatus = status
        try:
            self.dbus_obj.PropertiesChanged(
                "org.mpris.MediaPlayer2.Player",
                {"PlaybackStatus": GLib.Variant("s", status)},
                []
            )
        except Exception:
            pass

    def update_position(self, pos_sec: float) -> None:
        if not self.dbus_obj:
            return
        self.dbus_obj.Position = int(pos_sec * 1_000_000)

    def update_volume(self, vol_int: int) -> None:
        if not self.dbus_obj:
            return
        vol_float = max(0.0, min(1.0, float(vol_int) / 100.0))
        self.dbus_obj._volume = vol_float
        try:
            self.dbus_obj.PropertiesChanged(
                "org.mpris.MediaPlayer2.Player",
                {"Volume": GLib.Variant("d", vol_float)},
                []
            )
        except Exception:
            pass

    def stop(self) -> None:
        if self.publication:
            try:
                self.publication.unpublish()
            except Exception:
                pass
            self.publication = None
        if self.loop:
            try:
                self.loop.quit()
            except Exception:
                pass
            self.loop = None
        self._started = False
        self.dbus_obj = None
