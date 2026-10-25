"""A guard that fails the whole test run if any test opens a socket to anthropic.com, claude.com or
github.com (or a subdomain of them, githubusercontent.com included).

No test may talk to a production host: the tokens in the tests are fabricated, but the requests would
still reach Anthropic or GitHub (review LB, finding "tests P1 test_daemon.py:195"). ``tests/__init__.py``
installs it, so it is active for every way of running the suite.

How it works, in this process:

* ``socket.getaddrinfo``, ``gethostbyname``, ``gethostbyname_ex`` and ``socket.socket.connect`` /
  ``connect_ex`` look at the host name; a blocked host is recorded and refused with an ``OSError``.
* Because a test (or the code under test) may swallow that ``OSError``, the record is what fails the
  suite: an ``atexit`` handler prints every violation and ends the process with exit code 1, whatever
  unittest decided.

In a child process a test starts (the daemon), :func:`child_environment` returns an environment that makes
Python load the same guard through ``sitecustomize``; the child appends violations to a log file, and
the test's cleanup fails if that file is not empty.

This module imports nothing from the package under test and nothing outside the standard library, so
``sitecustomize`` can load it by path.
"""
from __future__ import annotations

import atexit
import errno
import os
import socket
import sys
import tempfile

BLOCKED_DOMAINS = ("anthropic.com", "claude.com", "github.com", "githubusercontent.com")
ENV_LOG = "PIXELSWITCH_NETGUARD_LOG"

violations: list[str] = []
_installed = False
_log_path: str | None = None


def blocked(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str) or not host:
        return False
    name = host.strip().rstrip(".").lower()
    return any(name == domain or name.endswith("." + domain) for domain in BLOCKED_DOMAINS)


def _record(where: str, host) -> None:
    line = f"{where}({host!r})"
    violations.append(line)
    if _log_path:
        try:
            with open(_log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def _refuse(where: str, host) -> None:
    _record(where, host)
    raise OSError(errno.ENETUNREACH, f"the test network guard refuses {host!r}")


def _host_of(address):
    if isinstance(address, tuple) and address:
        return address[0]
    return None


def install(log_path: str | None = None) -> None:
    """Idempotent. ``log_path`` is where a child process appends violations."""
    global _installed, _log_path
    if log_path:
        _log_path = log_path
    if _installed:
        return
    _installed = True

    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        if blocked(host):
            _refuse("getaddrinfo", host)
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host):
        if blocked(host):
            _refuse("gethostbyname", host)
        return real_gethostbyname(host)

    def gethostbyname_ex(host):
        if blocked(host):
            _refuse("gethostbyname_ex", host)
        return real_gethostbyname_ex(host)

    def connect(self, address):
        if blocked(_host_of(address)):
            _refuse("connect", _host_of(address))
        return real_connect(self, address)

    def connect_ex(self, address):
        if blocked(_host_of(address)):
            _refuse("connect_ex", _host_of(address))
        return real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    atexit.register(_fail_if_violated)


def _fail_if_violated() -> None:
    if not violations:
        return
    sys.stderr.write("\nTEST NETWORK GUARD: a test tried to reach a production host (anthropic.com, claude.com, "
                     "github.com):\n" + "".join(f"  {line}\n" for line in violations))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(1)


def clear() -> None:
    """For the guard's own test only."""
    del violations[:]


def child_environment(directory: str, log_path: str | None = None) -> dict[str, str]:
    """Environment entries that make a child Python process install this guard at start-up. ``directory``
    must be an empty folder the caller owns (a ``sitecustomize.py`` is written into it)."""
    log_path = log_path or os.path.join(directory, "netguard.log")
    with open(os.path.join(directory, "sitecustomize.py"), "w", encoding="utf-8") as handle:
        handle.write(
            "import importlib.util, os\n"
            f"spec = importlib.util.spec_from_file_location('ps_network_guard', {os.path.abspath(__file__)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            f"module.install(os.environ.get({ENV_LOG!r}))\n")
    return {"PYTHONPATH": directory, ENV_LOG: log_path}


def read_child_log(log_path: str) -> list[str]:
    try:
        with open(log_path, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]
    except OSError:
        return []


def make_child_dir() -> str:
    return tempfile.mkdtemp(prefix="pswitch-netguard-")
