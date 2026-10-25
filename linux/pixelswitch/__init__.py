"""PixelSwitch for Linux: the pure core.

Standard library only (Python 3.12 or newer). Nothing in this package imports a
third-party module. The modules here hold no I/O of their own except where a
module says so (client, ed25519's openssl call, update_check's archive helper):
they port the Swift logic of PixelSwitch 1.9 and the 2.0 spec so the daemon and
the faces can build on one tested core.
"""
