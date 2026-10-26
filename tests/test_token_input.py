"""Offline tests for the "use a refresh token" sign-in input parser.

Run from the repository root:
    python3 -m unittest discover -s tests -p 'test_token_input.py' -v

All tokens here are made up.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/skylight/token_input.py"
spec = importlib.util.spec_from_file_location("skylight_token_input", SOURCE)
token_input = importlib.util.module_from_spec(spec)
spec.loader.exec_module(token_input)
parse = token_input.parse_token_input

TOKEN = "dGhpcy1pcy1hLWZha2UtcmVmcmVzaC10b2tlbi0xMjM0NTY"  # not a real token
ACCESS = "ZmFrZS1hY2Nlc3MtdG9rZW4tZm9yLXRlc3RzLW9ubHktMDAwMQ"  # not a real token
DEVICE = "00000000-1111-2222-3333-444444444444"


def web_auth_state(**state):
    """The shape Skylight's web/mobile client persists, as copied from developer tools."""
    return json.dumps({"state": {
        "userId": "1", "authToken": None, "accessToken": ACCESS, "refreshToken": TOKEN,
        "accessTokenLifeSpan": 86400000, "accessTokenExpiry": 1793031668756,
        "uniqueId": DEVICE, **state}, "version": 1})


class BareAndSimpleJson(unittest.TestCase):
    def test_bare_token(self):
        self.assertEqual(parse(TOKEN), (TOKEN, None))

    def test_whitespace_and_quotes_are_trimmed(self):
        self.assertEqual(parse(f'  "{TOKEN}"\n'), (TOKEN, None))

    def test_json_with_fingerprint(self):
        raw = json.dumps({"refresh_token": TOKEN, "device_fingerprint": "abc123"})
        self.assertEqual(parse(raw), (TOKEN, "abc123"))

    def test_json_accepts_unique_id_as_the_fingerprint(self):
        self.assertEqual(parse(json.dumps({"refresh_token": TOKEN, "unique_id": "uuid-1"})), (TOKEN, "uuid-1"))

    def test_json_without_fingerprint(self):
        self.assertEqual(parse(json.dumps({"refresh_token": TOKEN})), (TOKEN, None))


class SkylightClientStorage(unittest.TestCase):
    def test_pasted_web_auth_state(self):
        self.assertEqual(parse(web_auth_state()), (TOKEN, DEVICE))

    def test_storage_key_in_front_of_the_json(self):
        for prefix in ("mmkv.default\\auth-storage\t", "auth-storage ", "mmkv.default\\auth-storage\n"):
            with self.subTest(prefix=prefix):
                self.assertEqual(parse(prefix + web_auth_state()), (TOKEN, DEVICE))

    def test_camel_case_keys_at_the_top_level(self):
        raw = json.dumps({"refreshToken": TOKEN, "uniqueId": DEVICE})
        self.assertEqual(parse(raw), (TOKEN, DEVICE))

    def test_access_token_is_never_used_as_the_refresh_token(self):
        raw = json.dumps({"state": {"accessToken": ACCESS, "uniqueId": DEVICE}})
        with self.assertRaises(ValueError):
            parse(raw)

    def test_state_without_a_device_id(self):
        raw = json.dumps({"state": {"refreshToken": TOKEN}})
        self.assertEqual(parse(raw), (TOKEN, None))

    def test_top_level_values_win_over_state(self):
        raw = json.dumps({"refresh_token": TOKEN, "state": {"refreshToken": "x" * 30, "uniqueId": DEVICE}})
        self.assertEqual(parse(raw), (TOKEN, DEVICE))

    def test_multiline_pretty_printed_json(self):
        self.assertEqual(parse(json.dumps(json.loads(web_auth_state()), indent=2)), (TOKEN, DEVICE))


class Rejected(unittest.TestCase):
    def test_unusable_input(self):
        for bad in ("", "   ", None, "short", "has a space inside the token value here",
                    "{not json", '{"refresh_token": ""}', "[1, 2]", '{"access_token": "x"}',
                    "{}", '{"state": {}}', '{"state": "nope"}', "}{"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse(bad)

    def test_a_pasted_redirect_url_is_not_a_token(self):
        with self.assertRaises(ValueError):
            parse("https://ourskylight.com/welcome?code=abc def")


if __name__ == "__main__":
    unittest.main()
