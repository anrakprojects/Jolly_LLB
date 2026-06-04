"""PTY bridge for `hermes dashboard` chat tab.

Wraps a child process behind a pseudo-terminal so its ANSI output can be
streamed to a browser-side terminal emulator (xterm.js) and typed
keystrokes can be fed back in.  The only caller today is the
``/api/pty`` WebSocket endpoint in ``hermes_cli.web_server``.

Design constraints:

* **Cross-platform.**  Two backends share one byte-oriented facade:

  - *POSIX* (Linux / macOS / WSL) uses :mod:`ptyprocess`, a pure-Python
    wrapper around ``forkpty`` + the ``fcntl``/``termios`` ioctls.
  - *Windows* uses :mod:`pywinpty` (the ``winpty`` package), which drives
    the native **ConPTY** API (Windows 10 build 17763+).  pywinpty runs a
    reader thread that forwards PTY output over a loopback socket, so its
    ``PtyProcess.fileobj`` is selectable just like a POSIX master fd —
    which lets the Windows backend mirror the POSIX read loop almost
    exactly.

  :meth:`PtyBridge.spawn` picks the right backend for the host OS.  If the
  platform's backend package is missing, :meth:`spawn` raises
  :class:`PtyUnavailableError` and the dashboard's ``/chat`` tab surfaces
  the message as a banner instead of crashing.  Every other dashboard
  feature (sessions, jobs, metrics, config editor) works regardless.
* **Zero Node dependency on the server side.**  Both backends are
  pure-Python wrappers around the OS calls.  The browser talks to the same
  ``hermes --tui`` binary it would launch from the CLI, so every TUI
  feature (slash popover, model picker, tool rows, markdown, skin engine,
  clarify/sudo/approval prompts) ships automatically.
* **Byte-safe I/O.**  The POSIX backend reads/writes the master fd
  directly because streaming ANSI is inherently byte-oriented and UTF-8
  boundaries may land mid-read.  The Windows backend gets the same
  guarantee from pywinpty's reader thread, which only ever forwards
  complete UTF-8 over its socket; we re-encode to UTF-8 for the WebSocket.
"""

from __future__ import annotations

import errno
import os
import select
import signal
import struct
import sys
import time
from typing import Optional, Sequence

_IS_WINDOWS = sys.platform.startswith("win")

# Backend selection.  Exactly one backend's dependencies import per platform;
# the other module names stay ``None`` so this module imports cleanly on every
# OS (importing ``fcntl``/``termios`` on native Windows would raise, which is
# why those imports must never be unconditional).
if _IS_WINDOWS:
    fcntl = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    ptyprocess = None  # type: ignore[assignment]
    try:
        import winpty  # type: ignore
        _PTY_AVAILABLE = True
    except ImportError:  # pragma: no cover - Windows without pywinpty
        winpty = None  # type: ignore[assignment]
        _PTY_AVAILABLE = False
else:
    winpty = None  # type: ignore[assignment]
    try:
        import fcntl
        import termios

        import ptyprocess  # type: ignore
        _PTY_AVAILABLE = True
    except ImportError:  # pragma: no cover - dev env without ptyprocess
        fcntl = None  # type: ignore[assignment]
        termios = None  # type: ignore[assignment]
        ptyprocess = None  # type: ignore[assignment]
        _PTY_AVAILABLE = False


__all__ = ["PtyBridge", "PtyUnavailableError"]


class PtyUnavailableError(RuntimeError):
    """Raised when a PTY cannot be created on this platform.

    Today this means a host whose backend package is missing — native
    Windows without ``pywinpty``, or a POSIX dev environment without
    ``ptyprocess``.  The dashboard surfaces the message to the user as a
    chat-tab banner.
    """


class PtyBridge:
    """Platform-agnostic facade + factory for a PTY-hosted child process.

    Construct one with :meth:`spawn`, which returns the backend matching the
    host OS (:class:`_PosixPtyBridge` or :class:`_WindowsPtyBridge`).  All
    backends expose the same byte-oriented :meth:`read` / :meth:`write` /
    :meth:`resize` / :meth:`close` surface the ``/api/pty`` WebSocket handler
    drives.

    Not thread-safe.  A single bridge is owned by the WebSocket handler that
    spawned it; the reader runs in an executor thread while writes happen on
    the event-loop thread.  Both sides are OK because the OS PTY (POSIX) or
    pywinpty's loopback socket (Windows) is the actual synchronization point.
    """

    # -- lifecycle --------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """True if a PTY can be spawned on this platform."""
        return bool(_PTY_AVAILABLE)

    @classmethod
    def spawn(
        cls,
        argv: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: int = 80,
        rows: int = 24,
    ) -> "PtyBridge":
        """Spawn ``argv`` behind a new PTY and return a bridge.

        Raises :class:`PtyUnavailableError` if the platform can't host a
        PTY (backend package missing).  Raises :class:`FileNotFoundError`
        or :class:`OSError` for ordinary exec failures (missing binary,
        bad cwd, etc.).
        """
        if not _PTY_AVAILABLE:
            if _IS_WINDOWS:
                raise PtyUnavailableError(
                    "The `pywinpty` package is missing. "
                    "Install it with: pip install pywinpty "
                    "(or reinstall Hermes with `pip install -e '.[pty]'`)."
                )
            if ptyprocess is None:
                raise PtyUnavailableError(
                    "The `ptyprocess` package is missing. "
                    "Install with: pip install ptyprocess "
                    "(or pip install -e '.[pty]')."
                )
            raise PtyUnavailableError("Pseudo-terminals are unavailable.")

        # PTY-hosted programs expect TERM to describe the terminal type.
        # CI often runs without TERM in the parent process, which makes
        # simple terminal probes like `tput cols` fail before winsize reads.
        # Preserve explicit caller overrides, but backfill a sensible default
        # when TERM is missing or blank.
        spawn_env = (os.environ.copy() if env is None else env.copy())
        if not spawn_env.get("TERM"):
            spawn_env["TERM"] = "xterm-256color"

        backend = _WindowsPtyBridge if _IS_WINDOWS else _PosixPtyBridge
        return backend._spawn(
            list(argv), cwd=cwd, env=spawn_env, cols=cols, rows=rows
        )

    # -- instance API (implemented by backends) ---------------------------

    @property
    def pid(self) -> int:
        raise NotImplementedError

    def is_alive(self) -> bool:
        raise NotImplementedError

    def read(self, timeout: float = 0.2) -> Optional[bytes]:
        """Read up to 64 KiB of raw bytes from the PTY master.

        Returns:
            * bytes — zero or more bytes of child output
            * empty bytes (``b""``) — no data available within ``timeout``
            * None — child has exited and the master is at EOF

        Never blocks longer than ``timeout`` seconds.  Safe to call after
        :meth:`close`; returns ``None`` in that case.
        """
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        """Write raw bytes to the PTY master (i.e. the child's stdin)."""
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:
        """Forward a terminal resize to the child."""
        raise NotImplementedError

    def close(self) -> None:
        """Terminate the child and release fds.  Idempotent."""
        raise NotImplementedError

    # Context-manager sugar — handy in tests and ad-hoc scripts.
    def __enter__(self) -> "PtyBridge":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class _PosixPtyBridge(PtyBridge):
    """POSIX backend: thin wrapper around ``ptyprocess.PtyProcess``.

    Reads and writes go through the PTY master fd directly — we avoid
    :class:`ptyprocess.PtyProcessUnicode` because streaming ANSI is
    inherently byte-oriented and UTF-8 boundaries may land mid-read.
    """

    def __init__(self, proc: "ptyprocess.PtyProcess"):  # type: ignore[name-defined]
        self._proc = proc
        self._fd: int = proc.fd
        self._closed = False

    @classmethod
    def _spawn(cls, argv, *, cwd, env, cols, rows) -> "_PosixPtyBridge":
        proc = ptyprocess.PtyProcess.spawn(  # type: ignore[union-attr]
            list(argv),
            cwd=cwd,
            env=env,
            dimensions=(rows, cols),
        )
        return cls(proc)

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def read(self, timeout: float = 0.2) -> Optional[bytes]:
        if self._closed:
            return None
        try:
            readable, _, _ = select.select([self._fd], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not readable:
            return b""
        try:
            data = os.read(self._fd, 65536)
        except OSError as exc:
            # EIO on Linux = slave side closed.  EBADF = already closed.
            if exc.errno in {errno.EIO, errno.EBADF}:
                return None
            raise
        if not data:
            return None
        return data

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        # os.write can return a short write under load; loop until drained.
        view = memoryview(data)
        while view:
            try:
                n = os.write(self._fd, view)
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF, errno.EPIPE}:
                    return
                raise
            if n <= 0:
                return
            view = view[n:]

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        # struct winsize: rows, cols, xpixel, ypixel (all unsigned short)
        winsize = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        try:
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        # SIGHUP is the conventional "your terminal went away" signal.
        # We escalate if the child ignores it.
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):  # windows-footgun: ok — POSIX-only backend
            if not self._proc.isalive():
                break
            try:
                self._proc.kill(sig)
            except Exception:
                pass
            deadline = time.monotonic() + 0.5
            while self._proc.isalive() and time.monotonic() < deadline:
                time.sleep(0.02)

        try:
            self._proc.close(force=True)
        except Exception:
            pass


class _WindowsPtyBridge(PtyBridge):
    """Windows backend: ConPTY via ``pywinpty`` (the ``winpty`` package).

    pywinpty's :class:`winpty.PtyProcess` spawns a reader thread that pumps
    ConPTY output over a loopback TCP socket, exposed as ``proc.fileobj``.
    Because that socket is selectable on Windows, this backend can use the
    same ``select`` + read loop as the POSIX backend: ``select`` reports the
    socket readable when real data (or EOF) is pending, so the subsequent
    ``proc.read`` — which does a blocking ``recv`` under the hood — never
    actually blocks past the ``select`` gate.

    pywinpty works in decoded :class:`str`, having reassembled complete
    UTF-8 in its reader thread; we re-encode to UTF-8 bytes for the
    WebSocket.  Inbound keystrokes are decoded UTF-8 → str for ``write``.
    """

    def __init__(self, proc: "winpty.PtyProcess"):  # type: ignore[name-defined]
        self._proc = proc
        # The loopback socket pywinpty's reader thread writes child output to.
        # Selectable on Windows; this is the synchronization point.
        self._sock = proc.fileobj
        self._closed = False

    @classmethod
    def _spawn(cls, argv, *, cwd, env, cols, rows) -> "_WindowsPtyBridge":
        # winpty.PtyProcess.spawn takes dimensions as (rows, cols), resolves
        # argv[0] on PATH itself, and raises FileNotFoundError for a missing
        # binary — matching the POSIX backend's contract.
        proc = winpty.PtyProcess.spawn(  # type: ignore[union-attr]
            list(argv),
            cwd=cwd,
            env=env,
            dimensions=(rows, cols),
        )
        return cls(proc)

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def read(self, timeout: float = 0.2) -> Optional[bytes]:
        if self._closed:
            return None
        try:
            readable, _, _ = select.select([self._sock], [], [], timeout)
        except (OSError, ValueError):
            # Socket closed underneath us (race with close()) → treat as EOF.
            return None
        if not readable:
            return b""
        try:
            chunk = self._proc.read(65536)
        except EOFError:
            return None
        except (OSError, ValueError):
            return None
        except Exception:
            # pywinpty's read() has a fragile keepalive-sentinel branch that
            # can raise on rare empty frames; treat anything unexpected as
            # "no data this tick" rather than killing the stream.
            return b""
        if not chunk:
            return b""
        # pywinpty returns decoded str (complete UTF-8); re-encode for the WS.
        return chunk.encode("utf-8", "replace")

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        try:
            self._proc.write(data.decode("utf-8", "replace"))
        except (EOFError, OSError, ValueError):
            return

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        # pywinpty's setwinsize takes (rows, cols) — opposite of our arg order.
        try:
            self._proc.setwinsize(max(1, rows), max(1, cols))
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            pid = int(self._proc.pid)
        except Exception:
            pid = None

        # pywinpty's close(force=True) shuts the loopback socket (unblocking
        # the reader thread), then SIGINT → SIGTERM the child with grace
        # sleeps.  It can raise IOError if a stubborn child survives; the
        # ConPTY teardown still tears the child's console down, so swallow it.
        try:
            self._proc.close(force=True)
        except Exception:
            pass

        # Backstop: pywinpty's terminate has been observed to occasionally
        # leave the child (the `node ui-tui` TUI) alive — and it never reaps
        # grandchildren.  Guarantee no orphaned process tree by force-killing
        # it if the handle-backed isalive() still reports it running.  We gate
        # on isalive() (which checks the OS process *handle* pywinpty holds,
        # not a bare pid) so there's no risk of killing a recycled pid.
        if pid is not None:
            try:
                still_alive = bool(self._proc.isalive())
            except Exception:
                still_alive = False
            if still_alive:
                _force_kill_tree(pid)


def _force_kill_tree(pid: int) -> None:
    """Force-kill a Windows process and all its descendants.

    Uses ``taskkill /F /T`` (always present in System32).  Best-effort: a
    failure here only means the OS will reap the process slightly later.
    """
    import subprocess

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass
