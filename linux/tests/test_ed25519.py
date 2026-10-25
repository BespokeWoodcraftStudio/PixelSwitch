"""Ed25519 verification: the RFC 8032 section 7.1 test vectors 1 to 3, a throwaway key generated
with openssl (when available) to sign and verify, a tampered byte that must fail, and the
fail-closed behaviour."""
from __future__ import annotations

import base64
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from pixelswitch import ed25519

# RFC 8032 section 7.1. The public keys and signatures are the RFC's; the seeds are used to derive
# the public keys again and to sign again, so a typo in any hex string here fails a test.
RFC_VECTORS = [
    {   # TEST 1 (empty message)
        "seed": "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "public": "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "message": "",
        "signature": "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    },
    {   # TEST 2 (one byte)
        "seed": "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "public": "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "message": "72",
        "signature": "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    },
    {   # TEST 3 (two bytes)
        "seed": "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "public": "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "message": "af82",
        "signature": "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    },
]


def b64(hex_text: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_text)).decode()


def openssl_ed25519_available() -> bool:
    exe = ed25519.find_openssl()
    if not exe:
        return False
    with tempfile.TemporaryDirectory() as work:
        key = os.path.join(work, "k.pem")
        data = os.path.join(work, "d")
        sig = os.path.join(work, "s")
        with open(data, "wb") as handle:
            handle.write(b"x")
        try:
            subprocess.run([exe, "genpkey", "-algorithm", "ed25519", "-out", key], check=True, capture_output=True, timeout=60)
            subprocess.run([exe, "pkeyutl", "-sign", "-rawin", "-inkey", key, "-in", data, "-out", sig],
                           check=True, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return False
    return True


HAVE_OPENSSL = openssl_ed25519_available()


class Rfc8032Tests(unittest.TestCase):
    def test_public_keys_derive_from_the_seeds(self):
        for vector in RFC_VECTORS:
            self.assertEqual(ed25519.public_key_from_seed(bytes.fromhex(vector["seed"])).hex(), vector["public"])

    def test_pure_verifier_accepts_the_rfc_vectors(self):
        for vector in RFC_VECTORS:
            self.assertTrue(ed25519.pure_verify(bytes.fromhex(vector["public"]), bytes.fromhex(vector["message"]),
                                                bytes.fromhex(vector["signature"])), vector["seed"][:8])

    def test_signing_reproduces_the_rfc_signatures(self):
        for vector in RFC_VECTORS:
            self.assertEqual(ed25519.sign(bytes.fromhex(vector["seed"]), bytes.fromhex(vector["message"])).hex(),
                             vector["signature"])

    def test_verify_with_the_pure_path(self):
        for vector in RFC_VECTORS:
            self.assertTrue(ed25519.verify(bytes.fromhex(vector["message"]), bytes.fromhex(vector["signature"]),
                                           b64(vector["public"]), use_openssl=False))

    def test_verify_takes_a_base64_signature_too(self):
        v = RFC_VECTORS[1]
        self.assertTrue(ed25519.verify(bytes.fromhex(v["message"]), b64(v["signature"]), b64(v["public"]), use_openssl=False))

    @unittest.skipUnless(HAVE_OPENSSL, "openssl with Ed25519 is not available")
    def test_verify_with_openssl_first(self):
        for vector in RFC_VECTORS:
            self.assertTrue(ed25519.verify(bytes.fromhex(vector["message"]), bytes.fromhex(vector["signature"]),
                                           b64(vector["public"])))

    def test_a_tampered_message_signature_or_key_fails(self):
        v = RFC_VECTORS[2]
        message, signature, public = bytes.fromhex(v["message"]), bytes.fromhex(v["signature"]), b64(v["public"])
        self.assertFalse(ed25519.verify(message + b"\x00", signature, public, use_openssl=False))
        self.assertFalse(ed25519.verify(b"\x00" + message[1:], signature, public, use_openssl=False))
        for index in (0, 31, 32, 63):
            tampered = bytearray(signature)
            tampered[index] ^= 1
            self.assertFalse(ed25519.verify(message, bytes(tampered), public, use_openssl=False), index)
        other = b64(RFC_VECTORS[0]["public"])
        self.assertFalse(ed25519.verify(message, signature, other, use_openssl=False))

    def test_a_signature_with_s_at_or_above_the_group_order_is_refused(self):
        v = RFC_VECTORS[0]
        signature = bytes.fromhex(v["signature"])
        s = int.from_bytes(signature[32:], "little") + ed25519.Q
        malleable = signature[:32] + s.to_bytes(32, "little")
        self.assertFalse(ed25519.pure_verify(bytes.fromhex(v["public"]), b"", malleable))

    def test_points_that_do_not_decompress_are_refused(self):
        v = RFC_VECTORS[0]
        bad_point = (ed25519.P).to_bytes(32, "little")  # y = p is not a field element
        self.assertFalse(ed25519.pure_verify(bad_point, b"", bytes.fromhex(v["signature"])))
        self.assertFalse(ed25519.pure_verify(bytes.fromhex(v["public"]), b"", bad_point + bytes.fromhex(v["signature"])[32:]))


class FailClosedTests(unittest.TestCase):
    V = RFC_VECTORS[1]

    def good(self, **overrides):
        args = {"data": bytes.fromhex(self.V["message"]), "signature": bytes.fromhex(self.V["signature"]),
                "public_key_b64": b64(self.V["public"]), "use_openssl": False}
        args.update(overrides)
        return ed25519.verify(**args)

    def test_baseline(self):
        self.assertTrue(self.good())

    def test_wrong_lengths_and_encodings_return_false_and_never_raise(self):
        self.assertFalse(self.good(signature=b"\x00" * 63))
        self.assertFalse(self.good(signature=b"\x00" * 65))
        self.assertFalse(self.good(signature=""))
        self.assertFalse(self.good(signature="not base64!!"))
        self.assertFalse(self.good(signature=base64.b64encode(b"\x01" * 63).decode()))
        self.assertFalse(self.good(public_key_b64=""))
        self.assertFalse(self.good(public_key_b64="@@@@"))
        self.assertFalse(self.good(public_key_b64=base64.b64encode(b"\x01" * 31).decode()))
        self.assertFalse(self.good(public_key_b64=base64.b64encode(b"\x01" * 33).decode()))
        self.assertFalse(self.good(public_key_b64=None))
        self.assertFalse(self.good(signature=None))
        self.assertFalse(self.good(data=None))
        self.assertFalse(self.good(signature=12345))

    def test_a_small_order_key_cannot_sign_anything(self):
        """y = 0 is a point of order 4: with an all-zero signature a naive verifier accepts about one message in four."""
        key = base64.b64encode(b"\x00" * 32).decode()
        accepted = sum(ed25519.verify(bytes([n]), b"\x00" * 64, key, use_openssl=False) for n in range(64))
        self.assertEqual(accepted, 0)
        identity = (1).to_bytes(32, "little")             # y = 1 is the identity (order 1)
        self.assertFalse(ed25519.verify(b"x", identity + bytes(32), base64.b64encode(identity).decode(), use_openssl=False))

    def test_a_missing_file_fails(self):
        self.assertFalse(ed25519.verify_file("/nonexistent/path", b"\x00" * 64, b64(self.V["public"]), use_openssl=False))

    def test_a_broken_openssl_falls_back_to_the_pure_verifier(self):
        work = tempfile.mkdtemp(prefix="psfake")
        try:
            fake = os.path.join(work, "openssl")
            with open(fake, "w") as handle:
                handle.write("#!/bin/sh\necho 'pkeyutl: Unknown option: -rawin' >&2\nexit 1\n")
            os.chmod(fake, stat.S_IRWXU)
            data_path = os.path.join(work, "data")
            with open(data_path, "wb") as handle:
                handle.write(bytes.fromhex(self.V["message"]))
            self.assertIsNone(ed25519.openssl_verify_file(data_path, bytes.fromhex(self.V["signature"]),
                                                          bytes.fromhex(self.V["public"]), openssl=fake))
            # verify_file ends up on the pure path and still says yes to a good signature
            original = ed25519.find_openssl
            ed25519.find_openssl = lambda: fake
            try:
                self.assertTrue(ed25519.verify_file(data_path, b64(self.V["signature"]), b64(self.V["public"])))
                self.assertFalse(ed25519.verify_file(data_path, b64(RFC_VECTORS[0]["signature"]), b64(self.V["public"])))
            finally:
                ed25519.find_openssl = original
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_no_openssl_at_all_uses_the_pure_verifier(self):
        original = ed25519.find_openssl
        ed25519.find_openssl = lambda: None
        try:
            self.assertTrue(ed25519.verify(bytes.fromhex(self.V["message"]), bytes.fromhex(self.V["signature"]),
                                           b64(self.V["public"])))
            self.assertFalse(ed25519.verify(b"tampered", bytes.fromhex(self.V["signature"]), b64(self.V["public"])))
        finally:
            ed25519.find_openssl = original

    def test_an_openssl_that_hangs_is_not_a_yes(self):
        work = tempfile.mkdtemp(prefix="psfake")
        original_timeout = ed25519.OPENSSL_TIMEOUT_SECONDS
        try:
            fake = os.path.join(work, "openssl")
            with open(fake, "w") as handle:
                handle.write("#!/bin/sh\nsleep 30\n")
            os.chmod(fake, stat.S_IRWXU)
            data_path = os.path.join(work, "data")
            with open(data_path, "wb") as handle:
                handle.write(b"")
            ed25519.OPENSSL_TIMEOUT_SECONDS = 1
            self.assertIsNone(ed25519.openssl_verify_file(data_path, bytes.fromhex(RFC_VECTORS[0]["signature"]),
                                                          bytes.fromhex(RFC_VECTORS[0]["public"]), openssl=fake))
        finally:
            ed25519.OPENSSL_TIMEOUT_SECONDS = original_timeout
            shutil.rmtree(work, ignore_errors=True)

    def test_an_openssl_that_says_yes_without_the_phrase_is_not_a_yes(self):
        work = tempfile.mkdtemp(prefix="psfake")
        try:
            fake = os.path.join(work, "openssl")
            with open(fake, "w") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(fake, stat.S_IRWXU)
            data_path = os.path.join(work, "data")
            with open(data_path, "wb") as handle:
                handle.write(b"")
            self.assertIsNone(ed25519.openssl_verify_file(data_path, bytes(64), bytes(32), openssl=fake))
        finally:
            shutil.rmtree(work, ignore_errors=True)


@unittest.skipUnless(HAVE_OPENSSL, "openssl with Ed25519 is not available")
class ThrowawayKeyTests(unittest.TestCase):
    """A key generated in the test with openssl signs a file; the module verifies it."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="psed")
        exe = ed25519.find_openssl()
        cls.key = os.path.join(cls.work, "key.pem")
        subprocess.run([exe, "genpkey", "-algorithm", "ed25519", "-out", cls.key], check=True, capture_output=True)
        der = subprocess.run([exe, "pkey", "-in", cls.key, "-pubout", "-outform", "DER"], check=True,
                             capture_output=True).stdout
        cls.public_b64 = base64.b64encode(der[-32:]).decode()
        cls.data_path = os.path.join(cls.work, "payload.bin")
        cls.payload = os.urandom(200_000) + b"PixelSwitch throwaway payload"
        with open(cls.data_path, "wb") as handle:
            handle.write(cls.payload)
        sig_path = os.path.join(cls.work, "payload.sig")
        subprocess.run([exe, "pkeyutl", "-sign", "-rawin", "-inkey", cls.key, "-in", cls.data_path, "-out", sig_path],
                       check=True, capture_output=True)
        with open(sig_path, "rb") as handle:
            cls.signature = handle.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_the_signature_is_64_bytes_and_the_key_32(self):
        self.assertEqual(len(self.signature), 64)
        self.assertEqual(len(base64.b64decode(self.public_b64)), 32)

    def test_verify_through_openssl(self):
        self.assertTrue(ed25519.verify(self.payload, self.signature, self.public_b64))
        self.assertTrue(ed25519.verify_file(self.data_path, base64.b64encode(self.signature).decode(), self.public_b64))
        self.assertTrue(ed25519.openssl_verify_file(self.data_path, self.signature, base64.b64decode(self.public_b64)))

    def test_verify_through_the_pure_verifier_agrees(self):
        self.assertTrue(ed25519.verify(self.payload, self.signature, self.public_b64, use_openssl=False))

    def test_a_tampered_byte_fails_on_both_paths(self):
        for index in (0, len(self.payload) // 2, len(self.payload) - 1):
            tampered = bytearray(self.payload)
            tampered[index] ^= 0x01
            self.assertFalse(ed25519.verify(bytes(tampered), self.signature, self.public_b64), index)
            self.assertFalse(ed25519.verify(bytes(tampered), self.signature, self.public_b64, use_openssl=False), index)

    def test_a_tampered_file_fails(self):
        path = os.path.join(self.work, "tampered.bin")
        data = bytearray(self.payload)
        data[10] ^= 0xFF
        with open(path, "wb") as handle:
            handle.write(data)
        self.assertFalse(ed25519.verify_file(path, self.signature, self.public_b64))
        self.assertFalse(ed25519.verify_file(path, self.signature, self.public_b64, use_openssl=False))
        self.assertIs(ed25519.openssl_verify_file(path, self.signature, base64.b64decode(self.public_b64)), False)

    def test_a_tampered_signature_fails(self):
        tampered = bytearray(self.signature)
        tampered[5] ^= 0x80
        self.assertFalse(ed25519.verify(self.payload, bytes(tampered), self.public_b64))

    def test_another_key_fails(self):
        other = b64(RFC_VECTORS[0]["public"])
        self.assertFalse(ed25519.verify(self.payload, self.signature, other))

    def test_the_pure_signer_agrees_with_openssl_on_the_same_seed(self):
        exe = ed25519.find_openssl()
        seed = bytes.fromhex(RFC_VECTORS[1]["seed"])
        pem = os.path.join(self.work, "seed.pem")
        der = bytes.fromhex("302e020100300506032b657004220420") + seed  # PKCS8 prefix from release.md D16
        with open(pem, "w") as handle:
            handle.write("-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PRIVATE KEY-----\n")
        message_path = os.path.join(self.work, "m")
        with open(message_path, "wb") as handle:
            handle.write(bytes.fromhex(RFC_VECTORS[1]["message"]))
        out = subprocess.run([exe, "pkeyutl", "-sign", "-rawin", "-inkey", pem, "-in", message_path], check=True,
                             capture_output=True).stdout
        self.assertEqual(out.hex(), RFC_VECTORS[1]["signature"])


if __name__ == "__main__":
    unittest.main()
