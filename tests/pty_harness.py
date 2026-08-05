"""Standard-library PTY test harness for curses and plain-mode integration tests.

Usage (spawn a child):
    h = PtyHarness(["python3.11", "probe.py", "echo"], rows=24, cols=80)
    h.wait_for(b"GOT")
    assert h.exit_status == 0
"""

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time


class PtyHarness:
    """Spawn and control a child process through a pseudo-terminal."""

    def __init__(self, argv: list[str], *,
                 rows: int = 24, cols: int = 80,
                 cwd: str | None = None,
                 env: dict[str, str] | None = None,
                 term: str = "xterm-256color"):
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._child_pid: int | None = None
        self._output = bytearray()
        self._exit_status: int | None = None

        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        self._slave_fd = slave_fd

        # Set initial window size
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        child_env.setdefault("TERM", term)

        self._child_pid = os.fork()
        if self._child_pid == 0:
            # Child
            os.close(master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.execvpe(argv[0], argv, child_env)
            os._exit(127)

        os.close(slave_fd)
        self._slave_fd = None

    # ── I/O ─────────────────────────────────────────────────────────

    def send(self, data: bytes) -> None:
        """Write *data* to the child's stdin."""
        assert self._master_fd is not None
        os.write(self._master_fd, data)

    def read_available(self, timeout: float = 0.1) -> bytes:
        """Read whatever is currently available without blocking long."""
        assert self._master_fd is not None
        r, _, _ = select.select([self._master_fd], [], [], timeout)
        if r:
            try:
                chunk = os.read(self._master_fd, 65536)
            except OSError:
                return b""
            self._output.extend(chunk)
            return chunk
        return b""

    def collect_until(self, marker: bytes, timeout: float = 10.0) -> int:
        """Read output until *marker* appears, or *timeout* expires.

        Returns the byte offset where *marker* was first found,
        or -1 if timeout elapsed.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            self.read_available(timeout=min(remaining, 0.5))
            idx = bytes(self._output).find(marker)
            if idx >= 0:
                return idx
        return -1

    def wait_for(self, marker: bytes, timeout: float = 10.0) -> None:
        """Block until *marker* appears in output; raise on timeout."""
        idx = self.collect_until(marker, timeout)
        if idx < 0:
            tail = bytes(self._output[-200:])
            raise TimeoutError(
                f"Marker {marker!r} not found within {timeout}s. "
                f"Output tail: {tail!r}")

    # ── window resize ───────────────────────────────────────────────

    def resize(self, rows: int, cols: int) -> None:
        """Resize the PTY and notify the child via SIGWINCH."""
        assert self._master_fd is not None
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        if self._child_pid is not None:
            os.kill(self._child_pid, signal.SIGWINCH)

    # ── output / state ──────────────────────────────────────────────

    @property
    def output(self) -> bytes:
        return bytes(self._output)

    @property
    def exit_status(self) -> int | None:
        if self._exit_status is not None:
            return self._exit_status
        if self._child_pid is None:
            return None
        pid, status = os.waitpid(self._child_pid, os.WNOHANG)
        if pid == self._child_pid:
            self._child_pid = None
            if os.WIFEXITED(status):
                self._exit_status = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                self._exit_status = -os.WTERMSIG(status)
            return self._exit_status
        return None

    def wait_exit(self, timeout: float = 10.0) -> int:
        """Wait for child to exit and return exit status (or negative signal)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            self.read_available(timeout=min(remaining, 0.5))
            status = self.exit_status
            if status is not None:
                return status
        raise TimeoutError(f"Child did not exit within {timeout}s")

    # ── cleanup ─────────────────────────────────────────────────────

    def terminate(self) -> None:
        """Kill the child if still alive."""
        if self._child_pid is not None:
            try:
                os.kill(self._child_pid, signal.SIGKILL)
                os.waitpid(self._child_pid, 0)
            except OSError:
                pass
            self._child_pid = None

    def close(self) -> None:
        """Read remaining output, clean up master, and ensure child is dead."""
        try:
            self.read_available(timeout=0.3)
        except Exception:
            pass
        self.terminate()
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def __del__(self) -> None:
        self.close()
