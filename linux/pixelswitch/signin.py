"""One run of ``claude auth login`` that shows its links instead of opening a browser.

Ports ``SignInSession.swift`` and ``SignInProcess.swift`` (recon ``signin.md`` sections 2.1-2.14)
on pipes (no terminal): ``BROWSER`` is pointed at a private capture helper, so the automatic
(localhost) link lands in a file and nothing opens by itself; the manual link is read from the
CLI's output; a pasted ``code#state`` goes to the CLI's stdin.

**No 10-second fallback** (spec 5.3). The Mac, when no link of either kind appeared within 10 s,
re-ran the CLI without ``BROWSER`` so Claude Code opened the default browser itself. On Linux that
would open a browser on a machine that may be headless or someone else's desktop, so it does not
exist: if no automatic link arrives the manual link and the code box are shown, and if neither
arrives a notice says so and the sign-in simply waits for Cancel or the 15-minute limit.

States move forward only: ``starting`` -> ``waitingForUser`` -> ``completing`` ->
``succeeded`` | ``failed`` | ``cancelled``. One controller thread owns the process; ``cancel``
and ``submit_code`` may be called from any thread. Limits (signin.md 2.11): 15 minutes overall,
SIGKILL 2 s after SIGTERM, give up 5 s after a stop request, capture file read every 0.25 s,
64 KiB of output kept, 2 s to drain the pipe after the exit. SIGTERM and SIGKILL go to the CLI's
whole process group (the CLI is started in its own session), so a wrapper cannot leave the
localhost listener behind.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

from . import signin_parse as parse

LINK_WAIT = 10.0
OVERALL_LIMIT = 15 * 60.0
KILL_AFTER = 2.0
GIVE_UP_AFTER = 5.0
CAPTURE_POLL_INTERVAL = 0.25
OUTPUT_LIMIT = 64 * 1024
OUTPUT_DRAIN_TIMEOUT = 2.0

FINISHED = ("succeeded", "failed", "cancelled")
NO_LINK_NOTICE = "Claude Code has not shown a sign-in link yet. If none appears, cancel and start again."


@dataclass(frozen=True)
class Purpose:
    """``newAccount`` or ``reauthenticate`` (with the account and its email)."""

    kind: str
    account_id: str | None = None
    email: str | None = None

    @classmethod
    def new_account(cls) -> "Purpose":
        return cls("newAccount")

    @classmethod
    def reauthenticate(cls, account_id: str, email: str) -> "Purpose":
        return cls("reauthenticate", account_id, email)

    def arguments(self) -> list[str]:
        """``claude`` arguments. Re-authentication pre-fills the email."""
        if self.kind == "reauthenticate" and self.email:
            return ["auth", "login", "--email", self.email]
        return ["auth", "login"]


@dataclass(frozen=True)
class Launch:
    """What a session runs: the claude binary and the environment every ``claude`` gets."""

    executable: str
    environment: Mapping[str, str]


class SignInSession:
    def __init__(
        self,
        purpose: Purpose,
        launch: Launch,
        capture_parent: str,
        *,
        log: Callable[[str], None] = lambda message: None,
        complete: Callable[["SignInSession"], tuple[str, str | None, str | None]],
        on_finish: Callable[["SignInSession"], None] = lambda session: None,
        on_change: Callable[["SignInSession"], None] = lambda session: None,
        clock: Callable[[], float] = time.time,
        session_id: str | None = None,
    ) -> None:
        self.id = session_id or str(uuid.uuid4()).upper()
        self.purpose = purpose
        self.started_at = clock()
        self._launch = launch
        self._capture_parent = capture_parent
        self._log = log
        self._complete = complete
        self._on_finish = on_finish
        self._on_change = on_change
        self._clock = clock
        self._lock = threading.RLock()
        self._state = "starting"
        self._message: str | None = None
        self._result_account_id: str | None = None
        self._automatic_link: str | None = None
        self._manual_link: str | None = None
        self._notice: str | None = None
        self._code_submitted = False
        self._did_start = False
        self._stop_reason: str | None = None  # "cancel" | "timeout"
        self._capture: parse.LinkCapture | None = None
        self._process: subprocess.Popen | None = None
        self._output = bytearray()
        self._thread: threading.Thread | None = None
        self._finished_event = threading.Event()
        self._finish_called = False

    # -- reading the state (any thread) ----------------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def is_finished(self) -> bool:
        return self.state in FINISHED

    @property
    def message(self) -> str | None:
        with self._lock:
            return self._message

    @property
    def result_account_id(self) -> str | None:
        with self._lock:
            return self._result_account_id

    @property
    def automatic_link(self) -> str | None:
        with self._lock:
            return self._automatic_link

    @property
    def manual_link(self) -> str | None:
        with self._lock:
            return self._manual_link

    @property
    def notice(self) -> str | None:
        with self._lock:
            return self._notice

    @property
    def code_submitted(self) -> bool:
        with self._lock:
            return self._code_submitted

    def wait_finished(self, timeout: float | None = None) -> bool:
        return self._finished_event.wait(timeout)

    # -- starting ------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Starts the CLI on its own thread. Does nothing if already started or finished (a sign-in
        cancelled during the backup launches nothing)."""
        with self._lock:
            if self._did_start or self._state in FINISHED:
                return
            self._did_start = True
        self._thread = threading.Thread(target=self._run, name=f"signin-{self.id[:8]}", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- the controller thread --------------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._run_inner()
        except Exception as error:  # a bug must not leave the sign-in busy forever
            self._log(f"[signIn] Unexpected error ({type(error).__name__})")
            self._terminate_process(kill=True)
            self._finish("failed", "Claude Code's sign-in stopped unexpectedly.")

    def _run_inner(self) -> None:
        try:
            capture = parse.LinkCapture.create(self._capture_parent, self.id, self._clock())
        except OSError as error:
            self._log(f"[signIn] Could not prepare the link capture: {type(error).__name__}")
            self._finish("failed", f"Couldn't prepare the sign-in: {error.strerror or type(error).__name__}")
            return
        self._capture = capture
        environment = dict(self._launch.environment)
        environment.update(capture.environment_overrides)
        try:
            process = subprocess.Popen(
                [self._launch.executable, *self.purpose.arguments()], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, start_new_session=True,
                cwd=environment.get("HOME") if environment.get("HOME") and os.path.isdir(environment["HOME"]) else None)
        except OSError as error:
            self._log(f"[signIn] Could not start Claude Code: {type(error).__name__}")
            self._finish("failed", f"Couldn't start Claude Code: {error.strerror or type(error).__name__}")
            return
        with self._lock:
            self._process = process
        self._log("[signIn] Started `claude auth login`")
        started = self._clock()
        link_deadline: float | None = started + LINK_WAIT
        overall_deadline = started + OVERALL_LIMIT
        next_poll = 0.0
        kill_at = give_up_at = None
        stdout_fd = process.stdout.fileno()  # type: ignore[union-attr]
        exit_status: int | None = None
        stopped_at: float | None = None

        while True:
            now = self._clock()
            # -- output
            ready, _, _ = select.select([stdout_fd], [], [], CAPTURE_POLL_INTERVAL)
            if ready:
                try:
                    chunk = os.read(stdout_fd, 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    self._received(chunk)
                elif process.poll() is None:
                    # EOF on stdout but the process lingers; wait for it below.
                    time.sleep(0.05)
            # -- the capture file
            now = self._clock()
            if self._automatic_link is None and now >= next_poll:
                next_poll = now + CAPTURE_POLL_INTERVAL
                self._poll_capture()
            # -- timers
            if link_deadline is not None and now >= link_deadline:
                link_deadline = None
                self._link_wait_elapsed()
            if self._stop_reason is None and now >= overall_deadline and self.state in ("starting", "waitingForUser"):
                self._log(f"[signIn] Timed out after {int(OVERALL_LIMIT // 60)} minutes")
                self._stop("timeout")
            with self._lock:
                reason = self._stop_reason
            if reason is not None and stopped_at is None:
                stopped_at = now
                kill_at, give_up_at = now + KILL_AFTER, now + GIVE_UP_AFTER
                self._signal_group(signal.SIGTERM)  # a cancel that raced the launch still reaches the CLI
            if kill_at is not None and now >= kill_at and process.poll() is None:
                kill_at = None
                self._log("[signIn] Claude Code did not stop after SIGTERM; sending SIGKILL")
                self._signal_group(signal.SIGKILL)
            if give_up_at is not None and now >= give_up_at and process.poll() is None:
                self._log("[signIn] No exit reported after stopping Claude Code; ending the sign-in anyway")
                exit_status = -1
                break
            code = process.poll()
            if code is not None:
                exit_status = code
                break
            if self.state in FINISHED:  # finished from outside (cancelled before the process mattered)
                break

        self._drain(stdout_fd)
        self._close_input()
        self._reap(process)
        if self.state in FINISHED:
            return
        with self._lock:
            reason, self._stop_reason = self._stop_reason, None
        if reason == "cancel":
            self._finish("cancelled")
        elif reason == "timeout":
            self._finish("failed", "Sign-in timed out after 15 minutes. Start it again when you're ready.")
        elif exit_status == 0:
            self._process_succeeded()
        else:
            self._log(f"[signIn] Claude Code exited with status {exit_status}")
            self._finish("failed", f"Claude Code's sign-in stopped (exit status {exit_status}).")

    # -- output and links ---------------------------------------------------------------------------------------

    def _received(self, data: bytes) -> None:
        with self._lock:
            if self._stop_reason is not None or self._state in FINISHED:
                return
            if len(self._output) < OUTPUT_LIMIT:
                self._output.extend(data[:OUTPUT_LIMIT - len(self._output)])
            if self._manual_link is not None:
                return
            link = parse.manual_link(self._output.decode("utf-8", "replace"))
            if link is None:
                return
            self._manual_link = link
        self._log(f"[signIn] Manual link read from the output: {parse.redacted(link)}")
        self._link_arrived()

    def _poll_capture(self) -> None:
        capture = self._capture
        if capture is None:
            return
        with self._lock:
            if self._stop_reason is not None or self._state in FINISHED or self._automatic_link is not None:
                return
        link = parse.automatic_link(capture.read())
        if link is None:
            return
        with self._lock:
            if self._automatic_link is not None:
                return
            self._automatic_link = link
        self._log(f"[signIn] Automatic link captured: {parse.redacted(link)}")
        self._link_arrived()

    def _link_arrived(self) -> None:
        with self._lock:
            if self._state == "starting":
                self._state = "waitingForUser"
        self._changed()

    def _link_wait_elapsed(self) -> None:
        with self._lock:
            if self._state != "starting" or self._stop_reason is not None or self._manual_link or self._automatic_link:
                return
            self._notice = NO_LINK_NOTICE
        self._log(f"[signIn] Neither link appeared within {int(LINK_WAIT)} s; waiting (no fallback on Linux)")
        self._changed()

    # -- the code from another device ---------------------------------------------------------------------------------

    def submit_code(self, code: str) -> bool:
        """Hands a pasted ``code#state`` to Claude Code. False unless the session is waiting for
        the user, no code was handed over yet, and the code has the ``code#state`` shape."""
        with self._lock:
            if self._state != "waitingForUser" or self._stop_reason is not None or self._code_submitted:
                return False
            line = parse.normalized_code(code)
            process = self._process
            if line is None or process is None or process.stdin is None:
                return False
            try:
                process.stdin.write((line + "\n").encode("utf-8"))
                process.stdin.flush()
            except (OSError, ValueError):
                self._log("[signIn] Could not hand the code to Claude Code")
                return False
            self._code_submitted = True
        self._log("[signIn] Code handed to Claude Code")
        self._changed()
        return True

    # -- stopping ------------------------------------------------------------------------------------------------------------

    def cancel(self) -> None:
        """Stops the sign-in: SIGTERM, then SIGKILL 2 s later if needed. Ignored once Claude Code
        has exited 0 (``completing``) or the session has finished, so closing a window never
        interrupts saving the account."""
        with self._lock:
            if self._state not in ("starting", "waitingForUser"):
                return
            if not self._did_start:
                started = False
            else:
                started = True
                if self._stop_reason in ("cancel", "timeout"):
                    return
        if not started:
            self._log("[signIn] Cancelled before Claude Code started")
            self._finish("cancelled")
            return
        self._log("[signIn] Cancelled")
        self._stop("cancel")

    def _stop(self, reason: str) -> None:
        with self._lock:
            if self._stop_reason is None:
                self._stop_reason = reason
        self._signal_group(signal.SIGTERM)

    def _signal_group(self, sig: int) -> None:
        process = self._process
        if process is None:
            return
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        except OSError:
            try:
                process.send_signal(sig)
            except OSError:
                pass

    def _terminate_process(self, kill: bool = False) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        self._signal_group(signal.SIGKILL if kill else signal.SIGTERM)

    def _close_input(self) -> None:
        process = self._process
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    def _drain(self, fd: int) -> None:
        """Reads what is left in the pipe, for up to 2 s (a grandchild holding it open must not hang us)."""
        deadline = self._clock() + OUTPUT_DRAIN_TIMEOUT
        while self._clock() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                break
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self._received(chunk)

    @staticmethod
    def _reap(process: subprocess.Popen) -> None:
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    # -- ending ----------------------------------------------------------------------------------------------------------------

    def _process_succeeded(self) -> None:
        self._log("[signIn] Claude Code finished the sign-in; saving it")
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.remove()
        with self._lock:
            self._state = "completing"
        self._changed()
        try:
            state, message, account_id = self._complete(self)
        except Exception as error:
            self._log(f"[signIn] Saving the account failed unexpectedly ({type(error).__name__})")
            state, message, account_id = "failed", "Login did not complete", None
        self._finish(state, message, account_id)

    def _finish(self, state: str, message: str | None = None, result_account_id: str | None = None) -> None:
        with self._lock:
            if self._state in FINISHED or self._finish_called:
                return
            self._finish_called = True
            self._state = state
            self._message = message
            self._result_account_id = result_account_id
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.remove()
        self._close_input()
        self._log(f"[signIn] Finished: {_summary(state)}")
        self._finished_event.set()
        try:
            self._on_finish(self)
        finally:
            self._changed()

    def _changed(self) -> None:
        try:
            self._on_change(self)
        except Exception:
            pass


def _summary(state: str) -> str:
    return {"waitingForUser": "waiting for the user"}.get(state, state)
