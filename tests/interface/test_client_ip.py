"""
The per-IP rate limiter keys on get_client_ip, so a spoofable IP
means a bypassable limit. These pin down the trust rules.
"""
import unittest
from types import SimpleNamespace

from app.interface.client_ip import get_client_ip


def fake_request(headers=None, host="10.0.0.7"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


class ClientIpTest(unittest.TestCase):
    def test_header_ignored_unless_proxy_trusted(self):
        req = fake_request({"x-forwarded-for": "1.2.3.4"})
        self.assertEqual(get_client_ip(req, trust_proxy_headers=False), "10.0.0.7")

    def test_trusted_proxy_uses_rightmost_entry(self):
        # Client forged "6.6.6.6"; our proxy appended the real 203.0.113.9.
        req = fake_request({"x-forwarded-for": "6.6.6.6, 203.0.113.9"})
        self.assertEqual(get_client_ip(req, trust_proxy_headers=True), "203.0.113.9")

    def test_trusted_proxy_without_header_falls_back_to_socket(self):
        self.assertEqual(get_client_ip(fake_request(), trust_proxy_headers=True), "10.0.0.7")

    def test_no_client_at_all(self):
        req = SimpleNamespace(headers={}, client=None)
        self.assertEqual(get_client_ip(req, trust_proxy_headers=False), "unknown")


if __name__ == "__main__":
    unittest.main()
