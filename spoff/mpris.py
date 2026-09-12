import os
import re
import logging
import threading
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger("mpris")

try:
    from pydbus import SessionBus
    from pydbus.generic import signal
    from gi.repository import GLib  # type: ignore
    HAS_DBUS = True
except Exception as e:
    logger.debug(f"pydbus or GLib not available: {e}")
    SessionBus = None  # type: ignore
    signal = None  # type: ignore
    GLib = None  # type: ignore
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
            <property name='DesktopEntry' type='s' access='read'/>
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
            <signal name='Seeked'>
                <arg direction='out' name='Position' type='x'/>
            </signal>
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
    if HAS_DBUS and signal:
        PropertiesChanged = signal()
        Seeked = signal()

    # Root Interface
    CanQuit = True
    CanRaise = False
    HasTrackList = False
    Identity = "Spoff"
    DesktopEntry = "spoff"
    SupportedUriSchemes = ["file", "http", "https"]
    SupportedMimeTypes = ["audio/mpeg", "audio/ogg", "audio/flac", "audio/webm"]

    def __init__(self, callbacks: Dict[str, Callable]):
        self.callbacks = callbacks
        self.PlaybackStatus = "Stopped"
        self._loop_status = "None"
        self.Rate = 1.0
        self._shuffle = False
        self._volume = 0.8
        self.Position = 0
        self.CanControl = True
        self.CanPlay = True
        self.CanPause = True
        self.CanSeek = True
        self.CanGoNext = True
        self.CanGoPrevious = True
        self.Metadata: Dict[str, Any] = {}

    @property
    def LoopStatus(self) -> str:
        return self._loop_status

    @LoopStatus.setter
    def LoopStatus(self, val: str) -> None:
        val_str = str(val)
        if val_str in ("None", "Track", "Playlist"):
            self._loop_status = val_str
            fn = self.callbacks.get("set_loop_status")
            if fn:
                fn(val_str)

    @property
    def Shuffle(self) -> bool:
        return self._shuffle

    @Shuffle.setter
    def Shuffle(self, val: bool) -> None:
        self._shuffle = bool(val)
        fn = self.callbacks.get("set_shuffle")
        if fn:
            fn(self._shuffle)

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
        if not HAS_DBUS or self._started or GLib is None or SessionBus is None:
            return False

        try:
            loop = GLib.MainLoop()
            self.loop = loop
            self._thread = threading.Thread(target=loop.run, daemon=True)
            self._thread.start()

            bus = SessionBus()
            self.dbus_obj = SpoffMPRISDbus(self.callbacks)

            primary_name = "org.mpris.MediaPlayer2.spoff"
            has_owner = False
            try:
                ret = bus.con.call_sync(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "NameHasOwner",
                    GLib.Variant("(s)", (primary_name,)),
                    None,
                    0,
                    -1,
                    None
                )
                has_owner = bool(ret[0])
            except Exception:
                has_owner = False

            bus_name = primary_name if not has_owner else f"{primary_name}.instance{os.getpid()}"
            self.publication = bus.publish(
                bus_name,
                ("/org/mpris/MediaPlayer2", self.dbus_obj)
            )
            logger.info(f"MPRIS service registered at {bus_name}")
            self._started = True
            return True
        except Exception as e:
            logger.warning(f"Failed to publish MPRIS service: {e}")
            self.dbus_obj = None
            return False

    def _emit_changed(self, props: Dict[str, Any]) -> None:
        if not self.dbus_obj:
            return
        prop_sig = getattr(self.dbus_obj, "PropertiesChanged", None)
        if callable(prop_sig):
            try:
                # pydbus automatically wraps values based on introspected types.
                # Passing raw python types (str, float, bool, dict) avoids double-wrapping TypeErrors.
                prop_sig("org.mpris.MediaPlayer2.Player", props, [])
            except Exception as e:
                logger.debug(f"Error emitting MPRIS PropertiesChanged: {e}")

    def update_track(self, track: Optional[Dict[str, Any]], duration_sec: float = 0.0) -> None:
        if not HAS_DBUS or not self.dbus_obj or GLib is None:
            return

        if not track:
            self.dbus_obj.Metadata = {}
            self.dbus_obj.PlaybackStatus = "Stopped"
            self._emit_changed({
                "Metadata": {},
                "PlaybackStatus": "Stopped",
            })
            return

        raw_id = str(track.get("id") or hash(track.get("title", "") + track.get("artist", "")))
        clean_id = "".join(c for c in raw_id if c.isalnum() or c == "_") or "track"
        track_obj_path = f"/org/mpris/MediaPlayer2/track/t_{clean_id}"

        title = track.get("title", "Unknown Title")
        artist = track.get("artist", "Unknown Artist")
        artists_list = [a.strip() for a in artist.split(",") if a.strip()] or [artist]
        dur_us = int(duration_sec * 1_000_000)
        if dur_us <= 0:
            try:
                dur_ms = float(track.get("duration_ms") or 0)
            except (ValueError, TypeError):
                dur_ms = 0.0
            dur_us = int(dur_ms * 1000)

        meta: Dict[str, Any] = {
            "mpris:trackid": GLib.Variant("o", track_obj_path),
            "xesam:title": GLib.Variant("s", str(title)),
            "xesam:artist": GLib.Variant("as", artists_list),
            "mpris:length": GLib.Variant("x", max(0, dur_us))
        }

        art_url = track.get("art_url") or track.get("thumbnail") or track.get("cover_url") or track.get("artist_art_url")
        artist_art_url = track.get("artist_art_url")
        album_art_url = track.get("album_art_url")

        if not art_url or not artist_art_url:
            try:
                from .art import get_cached_artwork
            except ImportError:
                try:
                    from art import get_cached_artwork
                except ImportError:
                    get_cached_artwork = None
            if get_cached_artwork:
                cached = get_cached_artwork(track)
                if cached:
                    art_url = art_url or cached.get("art_url") or cached.get("artist_art_url")
                    artist_art_url = artist_art_url or cached.get("artist_art_url")
                    album_art_url = album_art_url or cached.get("album_art_url")

        t_id = str(track.get("id") or "")
        if not art_url and t_id and len(t_id) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', t_id):
            art_url = f"https://img.youtube.com/vi/{t_id}/hqdefault.jpg"

        if art_url:
            meta["mpris:artUrl"] = GLib.Variant("s", str(art_url))
        if artist_art_url:
            meta["xesam:artistArtUrl"] = GLib.Variant("s", str(artist_art_url))
            meta["spoff:artistPicture"] = GLib.Variant("s", str(artist_art_url))
        if album_art_url:
            meta["xesam:albumArtUrl"] = GLib.Variant("s", str(album_art_url))

        album = track.get("album")
        if album:
            meta["xesam:album"] = GLib.Variant("s", str(album))

        track_url = track.get("url")
        if not track_url:
            if t_id and len(t_id) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', t_id):
                track_url = f"https://www.youtube.com/watch?v={t_id}"
            elif track.get("uri", "").startswith("spotify:track:"):
                s_id = track.get("uri").split(":")[-1]
                track_url = f"https://open.spotify.com/track/{s_id}"
        if track_url:
            meta["xesam:url"] = GLib.Variant("s", str(track_url))

        self.dbus_obj.Metadata = meta
        self.dbus_obj.PlaybackStatus = "Playing"

        self._emit_changed({
            "Metadata": meta,
            "PlaybackStatus": "Playing",
        })

    def update_status(self, is_playing: bool, is_paused: bool) -> None:
        if not HAS_DBUS or not self.dbus_obj or GLib is None:
            return

        status = "Playing" if is_playing and not is_paused else ("Paused" if is_playing and is_paused else "Stopped")
        if self.dbus_obj.PlaybackStatus == status:
            return

        self.dbus_obj.PlaybackStatus = status
        self._emit_changed({"PlaybackStatus": status})

    def update_position(self, pos_sec: float) -> None:
        if not self.dbus_obj:
            return
        self.dbus_obj.Position = int(pos_sec * 1_000_000)

    def update_volume(self, vol_int: int) -> None:
        if not HAS_DBUS or not self.dbus_obj or GLib is None:
            return
        vol_float = max(0.0, min(1.0, float(vol_int) / 100.0))
        if abs(self.dbus_obj._volume - vol_float) < 0.005:
            return
        self.dbus_obj._volume = vol_float
        self._emit_changed({"Volume": vol_float})

    def update_loop_status(self, repeat_mode: str) -> None:
        if not HAS_DBUS or not self.dbus_obj or GLib is None:
            return
        mapping = {"off": "None", "one": "Track", "all": "Playlist"}
        val = mapping.get(repeat_mode, "None")
        if self.dbus_obj._loop_status == val:
            return
        self.dbus_obj._loop_status = val
        self._emit_changed({"LoopStatus": val})

    def update_shuffle(self, shuffle_on: bool) -> None:
        if not HAS_DBUS or not self.dbus_obj or GLib is None:
            return
        val = bool(shuffle_on)
        if self.dbus_obj._shuffle == val:
            return
        self.dbus_obj._shuffle = val
        self._emit_changed({"Shuffle": val})

    def emit_seeked(self, pos_sec: float) -> None:
        if not HAS_DBUS or not self.dbus_obj:
            return
        pos_us = int(pos_sec * 1_000_000)
        self.dbus_obj.Position = pos_us
        seek_sig = getattr(self.dbus_obj, "Seeked", None)
        if callable(seek_sig):
            try:
                seek_sig(pos_us)
            except Exception as e:
                logger.debug(f"Error emitting MPRIS Seeked: {e}")

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
