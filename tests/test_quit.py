import os
import pty
import select
import subprocess
import sys
import time


def _read_until(fd, deadline, want=None):
    out = b""
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # child closed the terminal
                break
            if not chunk:
                break
            out += chunk
            if want is not None and want in out:
                break
    return out


def test_quit_key_ends_spoff_and_restores_the_terminal(tmp_path):
    """Pressing q in a real terminal used to freeze the screen half-drawn and never
    exit. It now has to end the process fast, leave the alt screen and take mpv with it."""
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / ".config"),
               XDG_DATA_HOME=str(tmp_path / ".local/share"), XDG_CACHE_HOME=str(tmp_path / ".cache"),
               TERM="xterm-256color", COLUMNS="160", LINES="45")
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-c", "from spoff.app import main; main()",
                                   "--no-visualizer"], env)
    try:
        # The status line's "q: quit" hint means the app is up and taking keys.
        assert b"quit" in _read_until(fd, time.monotonic() + 20, want=b"quit"), "spoff never started"
        time.sleep(1.0)
        children = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()

        os.write(fd, b"\x1b")
        time.sleep(0.3)
        started = time.monotonic()
        os.write(fd, b"q")
        tail = _read_until(fd, started + 5)
        deadline = started + 5
        while time.monotonic() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("pressing q left spoff running")

        assert os.waitstatus_to_exitcode(status) == 0
        assert time.monotonic() - started < 3
        assert b"\x1b[?1049l" in tail, "the terminal was left in spoff's alt screen"
        for child in children:
            assert not os.path.exists(f"/proc/{child}") or \
                open(f"/proc/{child}/stat").read().split()[2] == "Z", f"child {child} outlived spoff"
    finally:
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass
        os.close(fd)
