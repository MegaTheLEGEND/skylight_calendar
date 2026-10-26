"""Offline test: config-flow strings must not contain stray braces.

Run from the repository root:
    python3 -m unittest discover -s tests -p 'test_strings.py' -v

Home Assistant treats ``{name}`` in a translation as a placeholder. A literal brace (for
example a JSON example) makes the whole step fail with "Translation error: MALFORMED_ARGUMENT",
so the only braces allowed are the placeholders the flow actually supplies.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/skylight"
ALLOWED_PLACEHOLDERS = {"auth_url"}


def strings(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, str):
        yield path, node


class ConfigStrings(unittest.TestCase):
    def test_only_known_placeholders(self):
        for name in ("strings.json", "translations/en.json"):
            data = json.loads((ROOT / name).read_text())["config"]
            for path, text in strings(data):
                with self.subTest(file=name, key=path):
                    self.assertEqual(text.count("{"), text.count("}"), "unbalanced braces")
                    found = set(re.findall(r"\{([^{}]*)\}", text))
                    self.assertLessEqual(found, ALLOWED_PLACEHOLDERS)
                    self.assertNotIn("{", re.sub(r"\{[^{}]*\}", "", text), "stray brace")

    def test_both_files_match(self):
        self.assertEqual(json.loads((ROOT / "strings.json").read_text()),
                         json.loads((ROOT / "translations/en.json").read_text()))


if __name__ == "__main__":
    unittest.main()
