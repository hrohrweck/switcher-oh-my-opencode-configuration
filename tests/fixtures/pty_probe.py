"""Minimal probe for PTY harness self-tests.

Reports initial dimensions, reads one key and echoes it,
optionally sleeps forever for timeout tests.
"""

import os
import sys
import time
import signal
try:
    import termios
    import tty
    _HAVE_TERMIOS = True
except ImportError:
    _HAVE_TERMIOS = False


def _disable_echo(fd):
    """Disable terminal echo and canonical mode temporarily."""
    if not _HAVE_TERMIOS:
        return None
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[3] = new[3] & ~termios.ECHO   # disable echo
    new[3] = new[3] & ~termios.ICANON # disable canonical mode
    new[6][termios.VMIN] = 1
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSADRAIN, new)
    return old


def _restore_echo(fd, old):
    if old is not None:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _get_winsize():
    if not _HAVE_TERMIOS:
        return (24, 80)
    try:
        return termios.tcgetwinsize(sys.stdout.fileno())
    except Exception:
        return (24, 80)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "echo"

    if mode == "dimensions":
        rows, cols = _get_winsize()
        sys.stdout.write(f"INIT {rows}x{cols}\r\n")
        sys.stdout.flush()
        signal.pause()

    elif mode == "echo":
        rows, cols = _get_winsize()
        sys.stdout.write(f"READY {rows}x{cols}\r\n")
        sys.stdout.flush()
        old = _disable_echo(sys.stdin.fileno())
        ch = sys.stdin.read(1)
        _restore_echo(sys.stdin.fileno(), old)
        sys.stdout.write(f"KEY {ch!r}\r\n")
        sys.stdout.flush()

    elif mode == "resize":
        rows, cols = _get_winsize()
        sys.stdout.write(f"INIT {rows}x{cols}\r\n")
        sys.stdout.flush()
        received = False
        def handler(signum, frame):
            nonlocal received
            received = True
        old_sig = signal.signal(signal.SIGWINCH, handler)
        while not received:
            signal.pause()
        signal.signal(signal.SIGWINCH, old_sig)
        rows2, cols2 = _get_winsize()
        sys.stdout.write(f"RESIZED {rows2}x{cols2}\r\n")
        sys.stdout.flush()

    elif mode == "sleep":
        sys.stdout.write("SLEEPING\r\n")
        sys.stdout.flush()
        time.sleep(3600)

    elif mode == "signal_die":
        sys.stdout.write("READY\r\n")
        sys.stdout.flush()
        signal.pause()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
