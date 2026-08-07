# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import onboarding


class OnboardingTests(unittest.TestCase):
    def test_default_config_is_browser_first_and_telegram_optional(self):
        cfg = onboarding.default_config()
        self.assertFalse(cfg["onboarding_complete"])
        self.assertFalse(cfg["telegram_enabled"])
        self.assertTrue(cfg["agent_default"])
        self.assertGreaterEqual(len(cfg["custom_workflows"]), 1)

    def test_missing_config_is_created_atomically_enough_for_first_run(self):
        with tempfile.TemporaryDirectory() as work:
            target = Path(work) / "config.json"
            cfg, created = onboarding.ensure_config(target)
            self.assertTrue(created)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), cfg)
            same, created_again = onboarding.ensure_config(target)
            self.assertFalse(created_again)
            self.assertEqual(same, cfg)

    @patch.object(onboarding, "_probe_json")
    def test_status_separates_core_from_optional_channels(self, probe):
        probe.side_effect = lambda url, timeout=2.0: "8188" in url or "1234" in url
        cfg = onboarding.default_config()
        cfg["llm_model"] = "small-planner"
        status = onboarding.first_run_status(cfg, {"image": {"ready": True}})
        self.assertTrue(status["core_ready"])
        self.assertTrue(status["agent_ready"])
        self.assertFalse(status["checks"]["telegram"]["ready"])
        self.assertTrue(status["checks"]["telegram"]["optional"])

    @patch.object(onboarding, "_probe_json", return_value=False)
    def test_status_gives_one_clear_next_action(self, _probe):
        status = onboarding.first_run_status(onboarding.default_config(), {})
        self.assertFalse(status["core_ready"])
        self.assertIn("ComfyUI", status["next_action"])


if __name__ == "__main__":
    unittest.main()
