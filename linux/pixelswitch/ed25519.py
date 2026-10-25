"""Ed25519 signature verification with the standard library only.

``verify`` tries ``openssl pkeyutl -verify -pubin -rawin`` first (OpenSSL 3, present on
Ubuntu 24.04 and 26.04) and falls back to the pure-Python RFC 8032 section 6 verifier
below (docs/investigations/2026-10-24-linux-and-split-thresholds/recon/release.md,
appendix A). It FAILS CLOSED: any error, a wrong length, a missing tool that also cannot
be replaced, or a signature that does not verify returns False. It never raises.

The signature is plain Ed25519 over the raw bytes of the file (pure Ed25519, not
prehashed), the same contract ``scripts/verify-release.sh`` uses. The public key is the
base64 of the 32 raw bytes (``SUPublicEDKey`` in ``project.yml``). The key is wrapped in
the 12-byte SPKI prefix ``302a300506032b6570032100`` for openssl; as that prefix is a
multiple of 3 bytes its base64 (``MCowBQYDK2VwAyEA``) simply precedes the key's base64.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import shutil
import subprocess
import tempfile
from typing import Optional

_PEM_PREFIX_B64 = "MCowBQYDK2VwAyEA"
OPENSSL_TIMEOUT_SECONDS = 120

# --- RFC 8032 section 6 reference verifier (release.md appendix A) --------------------------

P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _add(a: tuple, b: tuple) -> tuple:
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * D % P
    Dd = 2 * a[2] * b[2] % P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)


def _mul(s: int, pt: tuple) -> tuple:
    r = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        s >>= 1
    return r


def _equal(a: tuple, b: tuple) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * _inv(5) % P
_GX = _recover_x(_GY, 0)
BASE = (_GX, _GY, 1, _GX * _GY % P)


def _decompress(s: bytes) -> Optional[tuple]:
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _compress(pt: tuple) -> bytes:
    zinv = _inv(pt[2])
    x, y = pt[0] * zinv % P, pt[1] * zinv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def public_key_from_seed(seed: bytes) -> bytes:
    """The public key for a 32-byte seed (RFC 8032 section 5.1.5). For tests and tools."""
    h = bytearray(hashlib.sha512(seed).digest()[:32])
    h[0] &= 248
    h[31] &= 127
    h[31] |= 64
    return _compress(_mul(int.from_bytes(h, "little"), BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    """RFC 8032 section 5.1.6 signing. Pure Python and slow-ish: for tests only; PixelSwitch
    never signs anything at run time (the release job signs with openssl)."""
    digest = hashlib.sha512(seed).digest()
    a = bytearray(digest[:32])
    a[0] &= 248
    a[31] &= 127
    a[31] |= 64
    scalar = int.from_bytes(a, "little")
    public = _compress(_mul(scalar, BASE))
    r = int.from_bytes(hashlib.sha512(digest[32:] + message).digest(), "little") % Q
    big_r = _compress(_mul(r, BASE))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % Q
    s = (r + k * scalar) % Q
    return big_r + s.to_bytes(32, "little")


def _small_order(point: tuple) -> bool:
    """Whether ``[8]point`` is the identity (a point of order 1, 2, 4 or 8)."""
    eight = _mul(8, point)
    return eight[0] % P == 0 and (eight[1] - eight[2]) % P == 0


def pure_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """The RFC 8032 verifier. Standard (non-cofactored) equation ``[s]B == R + [h]A``;
    rejects ``s >= L``, points that do not decompress, and (stricter than the RFC, like
    libsodium) a public key or ``R`` of small order, which would let a forger sign anything."""
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None or _small_order(A) or _small_order(R):
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % Q
    return _equal(_mul(s, BASE), _add(R, _mul(h, A)))


# --- openssl path ---------------------------------------------------------------------------


def _decode_public(public_key_b64: "str | bytes") -> Optional[bytes]:
    try:
        if isinstance(public_key_b64, (bytes, bytearray)) and len(public_key_b64) == 32:
            return bytes(public_key_b64)
        text = public_key_b64.decode("ascii") if isinstance(public_key_b64, (bytes, bytearray)) else public_key_b64
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError, UnicodeDecodeError, AttributeError):
        return None
    return raw if len(raw) == 32 else None


def _decode_signature(signature: "str | bytes") -> Optional[bytes]:
    try:
        if isinstance(signature, (bytes, bytearray)):
            raw = bytes(signature)
            if len(raw) == 64:
                return raw
            text = raw.decode("ascii")
        else:
            text = signature
        decoded = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError, UnicodeDecodeError, AttributeError):
        return None
    return decoded if len(decoded) == 64 else None


def find_openssl() -> Optional[str]:
    return shutil.which("openssl")


def openssl_verify_file(path: str, signature: bytes, public: bytes, openssl: Optional[str] = None) -> Optional[bool]:
    """True or False when openssl gave a definite answer, None when it could not (not
    installed, no ``-rawin`` / Ed25519 support, a timeout): the caller then uses the pure
    verifier. A definite "Signature Verification Failure" is False."""
    exe = openssl or find_openssl()
    if not exe:
        return None
    workdir = tempfile.mkdtemp(prefix="psverify-")
    try:
        os.chmod(workdir, 0o700)
        pem_path = os.path.join(workdir, "pub.pem")
        sig_path = os.path.join(workdir, "sig.bin")
        with open(pem_path, "w", encoding="ascii") as handle:
            handle.write("-----BEGIN PUBLIC KEY-----\n%s%s\n-----END PUBLIC KEY-----\n"
                         % (_PEM_PREFIX_B64, base64.b64encode(public).decode("ascii")))
        with open(sig_path, "wb") as handle:
            handle.write(signature)
        try:
            result = subprocess.run(
                [exe, "pkeyutl", "-verify", "-pubin", "-inkey", pem_path, "-rawin", "-in", path, "-sigfile", sig_path],
                capture_output=True, timeout=OPENSSL_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        output = (result.stdout + result.stderr).decode("utf-8", "replace")
        if result.returncode == 0 and "Signature Verified Successfully" in output:
            return True
        if "Signature Verification Failure" in output:
            return False
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def verify_file(path: str, signature: "str | bytes", public_key_b64: "str | bytes", *, use_openssl: bool = True) -> bool:
    """True only if ``signature`` is a valid Ed25519 signature of the file's bytes under the
    public key. Fails closed."""
    try:
        public = _decode_public(public_key_b64)
        raw_signature = _decode_signature(signature)
        if public is None or raw_signature is None:
            return False
        if use_openssl:
            answer = openssl_verify_file(path, raw_signature, public)
            if answer is not None:
                return answer
        with open(path, "rb") as handle:
            data = handle.read()
        return pure_verify(public, data, raw_signature)
    except Exception:  # noqa: BLE001 - fail closed on anything at all
        return False


def verify(data: bytes, signature: "str | bytes", public_key_b64: "str | bytes", *, use_openssl: bool = True) -> bool:
    """True only if ``signature`` (64 raw bytes, or its base64) is a valid Ed25519 signature
    of ``data`` under the public key (the base64 of its 32 raw bytes). Fails closed."""
    try:
        public = _decode_public(public_key_b64)
        raw_signature = _decode_signature(signature)
        if public is None or raw_signature is None:
            return False
        if use_openssl and find_openssl():
            workdir = tempfile.mkdtemp(prefix="psverify-")
            try:
                os.chmod(workdir, 0o700)
                path = os.path.join(workdir, "data.bin")
                with open(path, "wb") as handle:
                    handle.write(data)
                answer = openssl_verify_file(path, raw_signature, public)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
            if answer is not None:
                return answer
        return pure_verify(public, bytes(data), raw_signature)
    except Exception:  # noqa: BLE001 - fail closed on anything at all
        return False
