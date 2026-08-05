# Learnings: full-screen config selector TUI

## Task 3 - PTY harness (2026-08-05, macOS, Python 3.11.15)

Platform-specific pty behavior discovered while building `tests/pty_harness.py`:

1. **macOS discards pending slave->master output when the last slave fd closes.**
   A child that writes its final line and immediately exits can have that line
   dropped entirely: the master becomes readable but the first read raises
   `OSError` (EIO) with zero bytes delivered. Linux delivers pending data
   before EIO. Contract for all future TUI tests: always `wait_for(...)` final
   markers *while the child is still alive*, then assert the exit status.

2. **`termios.tcdrain()` on a macOS pty slave blocks until the master reads.**
   It is not a safe "flush before exit" on macOS: a child calling `tcdrain`
   while the harness is blocked in `proc.wait()` deadlocks. Do not use
   `tcdrain` in probes/fixtures on this platform.

3. **Slave pty defaults to canonical mode + echo.** Single-keystroke and
   escape-sequence delivery requires the child to clear `ICANON|ECHO` and set
   `VMIN=1, VTIME=0`; harness-side sending of a trailing newline is not a
   substitute because arrow keys contain no newline.

4. **ONLCR is on by default on the slave**: child `\n` arrives at the master
   as `\r\n`. Byte markers must never span a line ending.

5. `termios.tcgetwinsize`/`tcsetwinsize` (new in 3.11) work on macOS
   3.11.15; the harness keeps `TIOCSWINSZ`/`TIOCGWINSZ` ioctl fallbacks for
   older interpreters.

6. `LC_ALL=C.UTF-8` + `TERM=xterm-256color` are accepted silently by Python
   children on macOS (no locale warnings observed on stderr).

7. `start_new_session=True` + inherited-slave-fd means the child has no
   controlling tty, which is fine here: resize notification is explicit
   (`SIGWINCH` to the child pid), never via process groups.

8. The probe consumes exactly one key then exits - scenario scripts must
   resize/report *before* sending the final key (ordering bug bit the
   evidence generator once).

Evidence: `.omo/evidence/task-3-pty-resize.bin`,
`.omo/evidence/task-3-pty-timeout.txt`. Suite: 8 tests, ~3.6s, green twice.
