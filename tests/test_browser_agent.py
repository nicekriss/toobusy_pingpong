# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import browser_agent
import dashboard
import pingpong


class BrowserAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_dir = browser_agent.SESSION_DIR
        browser_agent.SESSION_DIR = Path(self.temp.name)

    def tearDown(self):
        browser_agent.SESSION_DIR = self.old_dir
        self.temp.cleanup()

    def test_file_backed_session_is_publicly_filtered(self):
        session = browser_agent.create("고양이 이미지를 만들어줘", reference_assets=[{"rel": "secret.jpg"}])
        loaded = browser_agent.read(session["id"])
        self.assertEqual(loaded["request"], "고양이 이미지를 만들어줘")
        public = browser_agent.public(loaded)
        self.assertNotIn("reference_assets", public)
        self.assertEqual(public["state"], "planning")

    def test_worker_plans_then_executes_browser_request(self):
        session = browser_agent.create("비 오는 서울의 고양이")
        plan = {
            "tool": "image", "intent": "고양이 이미지",
            "normalized_request": "비 오는 서울의 고양이", "reason": "이미지 요청",
            "confidence": 0.95, "missing_inputs": [],
        }
        fake_plan = SimpleNamespace(tool="image", to_dict=lambda: dict(plan))
        with patch.object(pingpong, "make_agent_plan", return_value=fake_plan):
            pingpong.run_browser_agent_plan(session["id"])
        planned = browser_agent.read(session["id"])
        self.assertEqual(planned["state"], "planned")
        self.assertEqual(planned["plan"]["tool"], "image")
        self.assertIn("seed", planned["settings"])

        with patch.object(pingpong, "do_text") as generate, \
             patch.object(pingpong, "_browser_results_since", return_value=["img_test.png"]):
            pingpong.run_browser_agent_execute(session["id"])
        generate.assert_called_once()
        completed = browser_agent.read(session["id"])
        self.assertEqual(completed["state"], "complete")
        self.assertEqual(completed["results"], ["img_test.png"])

    def test_dashboard_contains_agent_controls_and_endpoints(self):
        self.assertIn('id="agentpanel"', dashboard.PAGE)
        self.assertIn("function agentPlan()", dashboard.PAGE)
        self.assertIn("function toggleDirectMode()", dashboard.PAGE)
        self.assertIn('class="msel manualonly"', dashboard.PAGE)
        self.assertIn("🤖 핑퐁에게 맡기기", dashboard.PAGE)
        self.assertIn('id="agentdrop"', dashboard.PAGE)
        self.assertIn("function agentDropTypes(e)", dashboard.PAGE)
        self.assertIn("function addPickedFiles(files, role, forceAppend)", dashboard.PAGE)
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertIn('p == "/api/agent/plan"', source)
        self.assertIn('p == "/api/agent/confirm"', source)


if __name__ == "__main__":
    unittest.main()
