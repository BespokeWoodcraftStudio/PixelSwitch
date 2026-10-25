"""The updater's own transport (api.py): it follows at most 5 redirects, to GitHub release hosts over https
only, never sends a credential header, and keeps the size cut-off. The token transport stays redirect-free.

Nothing here touches the network: redirect targets are checked as URLs, and the transfers run against a
local server (test mode) or an in-process double."""
from __future__ import annotations

import os
import unittest
import urllib.request

from pixelswitch import api
from pixelswitch.api import (
    MAX_REDIRECTS, RELEASE_REDIRECT_HOSTS, RedirectRefused, Transport, TransportError, check_release_url,
)

from .lb_support import tmpdir
from .test_updater import ReleaseServer


class ReleaseUrlRuleTests(unittest.TestCase):
    def allowed(self, url):
        check_release_url(url, test_mode=False)

    def refused(self, url, **kw):
        with self.assertRaises(RedirectRefused, msg=url):
            check_release_url(url, test_mode=kw.get("test_mode", False))

    def test_the_hosts_github_uses_today_are_allowed(self):
        self.assertEqual(set(RELEASE_REDIRECT_HOSTS), {"github.com", "objects.githubusercontent.com",
                                                       "release-assets.githubusercontent.com"})
        for url in ("https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v1.9/appcast.xml",
                    "https://release-assets.githubusercontent.com/github-production-release-asset/1/x?sp=r&sig=abc",
                    "https://objects.githubusercontent.com/github-production-release-asset-2e65be/1/x",
                    "https://GitHub.com/x", "https://github.com:443/x", "https://github.com./x"):
            self.allowed(url)

    def test_everything_else_is_refused(self):
        for url in ("http://github.com/x", "ftp://github.com/x", "file:///etc/passwd", "https://evil.example/x",
                    "https://github.com.evil.example/x", "https://evilgithub.com/x", "https://githubusercontent.com/x",
                    "https://raw.githubusercontent.com/x", "https://api.github.com/x", "https://gist.github.com/x",
                    "https://api.anthropic.com/x", "https://console.anthropic.com/x", "https://claude.com/x",
                    "https://user:pass@github.com/x", "https://github.com@evil.example/x", "https://github.com:8443/x",
                    "https://127.0.0.1/x", "https://localhost/x", "https:///x", "//github.com/x", "https://[::1]/x"):
            self.refused(url)

    def test_test_mode_allows_a_local_http_server_but_never_a_blocked_host(self):
        check_release_url("http://127.0.0.1:5000/releases/x", test_mode=True)
        for url in ("http://api.anthropic.com/x", "https://claude.com/x", "ftp://127.0.0.1/x", "file:///etc/passwd"):
            self.refused(url, test_mode=True)


class RedirectHandlerTests(unittest.TestCase):
    def request(self, url="https://github.com/a", headers=None, method="GET", data=None):
        return urllib.request.Request(url, headers=headers or {}, method=method, data=data)

    def handler(self, test_mode=False):
        return api._FollowReleaseRedirects(test_mode)

    def test_a_good_hop_keeps_the_user_agent_and_drops_every_credential(self):
        old = self.request(headers={"User-Agent": "PixelSwitch/test", "Authorization": "Bearer secret",
                                    "Cookie": "a=b", "Proxy-Authorization": "x", "Accept": "*/*"})
        new = self.handler().redirect_request(old, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/z")
        self.assertEqual(new.full_url, "https://release-assets.githubusercontent.com/z")
        names = {key.lower() for key in new.headers}
        self.assertEqual(names, {"user-agent", "accept"})
        self.assertEqual(new.get_method(), "GET")

    def test_a_hop_to_a_bad_place_raises(self):
        for url in ("http://github.com/x", "https://evil.example/x", "https://api.anthropic.com/x"):
            with self.assertRaises(RedirectRefused):
                self.handler().redirect_request(self.request(), None, 302, "Found", {}, url)

    def test_only_five_hops_are_followed(self):
        handler = self.handler()
        request = self.request()
        for hop in range(MAX_REDIRECTS):
            request = handler.redirect_request(request, None, 302, "Found", {}, f"https://github.com/h{hop}")
            self.assertIsNotNone(request)
        with self.assertRaises(RedirectRefused):
            handler.redirect_request(request, None, 302, "Found", {}, "https://github.com/one-too-many")

    def test_a_post_is_never_redirected(self):
        old = self.request(method="POST", data=b"x")
        self.assertIsNone(self.handler().redirect_request(old, None, 307, "Temporary", {}, "https://github.com/x"))


class UpdaterTransportTests(unittest.TestCase):
    def setUp(self):
        self.server = ReleaseServer()
        self.addCleanup(self.server.stop)
        self.root = self.server.base.rsplit("/releases", 1)[0]
        self.work = tmpdir(self)

    def follower(self):
        return Transport(user_agent="PixelSwitch/test", test_mode=True, follow_redirects=True)

    def test_the_first_url_outside_the_rule_is_refused_before_any_connection(self):
        production = Transport(user_agent="PixelSwitch/test", follow_redirects=True)
        for url in ("https://api.anthropic.com/api/oauth/usage", "http://github.com/x", "https://evil.example/x",
                    "https://console.anthropic.com/v1/oauth/token"):
            with self.assertRaises(TransportError, msg=url):
                production.request("GET", url, timeout=1)
            with self.assertRaises(TransportError, msg=url):
                production.download(url, os.path.join(self.work, "never"), limit=10, timeout=1)
        self.assertFalse(os.path.exists(os.path.join(self.work, "never")))

    def test_a_caller_supplied_authorization_header_is_dropped_at_the_first_hop_too(self):
        self.server.routes["/a"] = (200, b"hello")
        reply = self.follower().request("GET", self.root + "/a", timeout=5,
                                        headers={"Authorization": "Bearer secret", "Cookie": "a=b", "X-Other": "kept"})
        self.assertEqual(reply.status, 200)
        sent = self.server.received_headers[-1]
        self.assertNotIn("authorization", sent)
        self.assertNotIn("cookie", sent)
        self.assertEqual(sent.get("x-other"), "kept")

    def test_a_download_through_two_redirects_is_written_0600(self):
        self.server.routes["/blob"] = (200, b"payload")
        self.server.routes["/a"] = (302, b"", {"Location": "/b"})
        self.server.routes["/b"] = (302, b"", {"Location": "/blob"})
        destination = os.path.join(self.work, "out")
        self.assertEqual(self.follower().download(self.root + "/a", destination, limit=100, timeout=5), (200, 7, False))
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"payload")
        self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)

    def test_the_limit_is_enforced_on_the_final_response_whether_or_not_it_declares_a_length(self):
        self.server.routes["/blob"] = (200, b"x" * 500)
        self.server.routes["/a"] = (302, b"", {"Location": "/blob"})
        destination = os.path.join(self.work, "out")
        status, written, exceeded = self.follower().download(self.root + "/a", destination, limit=100, timeout=5)
        self.assertEqual((status, exceeded), (200, True))
        self.assertFalse(os.path.exists(destination), "nothing is left on disk")

    def test_a_final_404_is_reported_as_404(self):
        self.server.routes["/a"] = (302, b"", {"Location": "/gone"})
        destination = os.path.join(self.work, "out")
        self.assertEqual(self.follower().download(self.root + "/a", destination, limit=100, timeout=5), (404, 0, False))
        self.assertEqual(self.follower().request("GET", self.root + "/a", timeout=5).status, 404)
        self.assertFalse(os.path.exists(destination))

    def test_should_stop_abandons_a_transfer(self):
        self.server.routes["/blob"] = (200, b"x" * 500)
        destination = os.path.join(self.work, "out")
        with self.assertRaises(TransportError):
            self.follower().download(self.root + "/blob", destination, limit=1000, timeout=5, should_stop=lambda: True)
        self.assertFalse(os.path.exists(destination))

    def test_the_default_transport_does_not_follow_and_sends_what_it_is_given(self):
        self.server.routes["/a"] = (302, b"", {"Location": "/b"})
        self.server.routes["/b"] = (200, b"x")
        plain = Transport(user_agent="PixelSwitch/test", test_mode=True)
        self.assertFalse(plain.follow_redirects)
        self.assertEqual(plain.request("GET", self.root + "/a", timeout=5, headers={"Authorization": "Bearer t"}).status, 302)
        self.assertEqual(self.server.received_headers[-1].get("authorization"), "Bearer t")
        self.assertEqual(plain.download(self.root + "/a", os.path.join(self.work, "o2"), limit=10, timeout=5), (302, 0, False))


if __name__ == "__main__":
    unittest.main()
