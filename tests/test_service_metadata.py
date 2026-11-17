"""Every registered action is described for Home Assistant's action editor.

Home Assistant shows action names, descriptions and fields from services.yaml
and the services section of strings.json / translations/en.json. An action
missing from either shows up with no description or field labels.

Run from the repository root: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

import yaml

from test_service_authorization import load_exact_nodes

PKG = Path(__file__).resolve().parents[1] / "custom_components/skylight"


class ServiceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.yaml = yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8"))
        scope = load_exact_nodes()
        self.registered = {name for name, *_ in scope["_WRITE_SERVICES"]} | {scope["SERVICE_UPLOAD_MEDIA"]}

    def test_every_registered_action_is_in_services_yaml(self):
        self.assertEqual(self.registered, set(self.yaml))

    def test_translations_match_services_yaml(self):
        for name in ("strings.json", "translations/en.json"):
            services = json.loads((PKG / name).read_text(encoding="utf-8"))["services"]
            with self.subTest(file=name):
                self.assertEqual(set(services), set(self.yaml))
                for action, spec in self.yaml.items():
                    self.assertEqual(
                        set(services[action].get("fields", {})),
                        set(spec.get("fields", {})),
                        f"{name}: fields of {action}",
                    )


if __name__ == "__main__":
    unittest.main()
