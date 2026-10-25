"""The test package. Importing it installs the network guard (tests/network_guard.py): a test that opens a
socket to anthropic.com, claude.com or github.com fails the whole run.

test_network_guard.py proves the guard is installed in a real run. The import is tolerant only because
test_lb_la_fixes.py copies this file alone, with test_conformance.py, into a scratch checkout."""
try:
    from . import network_guard
except ImportError:  # pragma: no cover - that scratch copy has no network_guard.py
    network_guard = None
else:
    network_guard.install()
