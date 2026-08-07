# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pingpong


class GenerationControlInjectionTests(unittest.TestCase):
    def tearDown(self):
        pingpong.reset_state()

    def test_settings_only_followup_reuses_plan_without_planner(self):
        session = {
            "plan": {
                "tool": "image", "intent": "portrait",
                "normalized_request": "cinematic portrait", "confidence": 0.9,
            },
            "photos": [], "audio": None,
            "settings": {"steps": 8, "cfg": 1, "denoise": 0.5, "seed": 123},
        }
        with patch.object(pingpong, "make_agent_plan") as planner, \
             patch.object(pingpong, "_agent_offer_confirmation") as confirm, \
             patch.object(pingpong, "installed_loras", return_value=[]):
            pingpong.start_agent_request("스탭수 조금만 올려줘", previous_session=session)
        planner.assert_not_called()
        confirm.assert_called_once()
        self.assertEqual(pingpong.STATE["agent_plan"]["tool"], "image")
        self.assertEqual(pingpong.STATE["agent_settings"]["steps"], 10)
        self.assertTrue(pingpong.STATE["agent_changes"])

    def test_image_controls_reach_workflow_json(self):
        settings = pingpong.prepare_agent_settings("image", {
            "steps": 10,
            "cfg": 1.5,
            "denoise": 0.35,
            "seed": 123,
            "image_ratio": "16:9",
            "loras": [{"name": r"ZIT\demo.safetensors", "strength": 0.7}],
        })
        workflow, _ = pingpong.inject_zit("test", settings)
        self.assertEqual(workflow["1"]["inputs"]["steps"], 10)
        self.assertEqual(workflow["1"]["inputs"]["cfg"], 1.5)
        self.assertEqual(workflow["4"]["inputs"]["denoise"], 0.35)
        self.assertEqual(workflow["1"]["inputs"]["seed"], 123)
        self.assertEqual(workflow["4"]["inputs"]["seed"], 123)
        self.assertEqual(workflow["1"]["inputs"]["ratio_preset"], "16:9")
        self.assertEqual(workflow["1"]["inputs"]["lora_1_name"], r"ZIT\demo.safetensors")

    def test_video_controls_reach_workflow_json(self):
        settings = pingpong.prepare_agent_settings("video", {
            "steps": 10, "cfg": 2, "denoise": 0.8, "seed": 456,
        })
        workflow, _ = pingpong.inject_ltx("test", settings=settings)
        self.assertEqual(workflow["33"]["inputs"]["steps"], 10)
        self.assertEqual(workflow["17"]["inputs"]["cfg"], 2)
        self.assertEqual(workflow["33"]["inputs"]["denoise"], 0.8)
        self.assertEqual(workflow["30"]["inputs"]["noise_seed"], 456)

    def test_custom_video_ratio_megapixels_and_seconds_reach_workflow_json(self):
        source = {
            "size": {"inputs": {"aspect_ratio": "16:9", "megapixels": 0.5}},
            "duration": {"inputs": {"value": 7.0}},
            "prompt": {"inputs": {"value": ""}},
            "image": {"inputs": {"image": "default.png"}},
            "save": {"inputs": {"filename_prefix": "test"}},
        }
        spec = {
            "file": "workflows/test.json", "type": "video", "output_node": "save",
            "prompt_nodes": [["prompt", "value"]], "image_nodes": [["image", "image"]],
            "ratio_node": ["size", "aspect_ratio"],
            "ratio_map": {"9:16": "9:16 (Portrait Widescreen)"},
            "megapixels_nodes": [["size", "megapixels"]], "default_megapixels": 0.5,
            "duration_node": ["duration", "value"], "default_seconds": 7,
        }
        with tempfile.TemporaryDirectory() as td:
            workflows = Path(td, "workflows")
            workflows.mkdir()
            Path(workflows, "test.json").write_text(json.dumps(source), encoding="utf-8")
            with patch.object(pingpong, "HERE", td), patch.object(pingpong, "apply_model_overrides"):
                workflow, _ = pingpong.inject_custom(spec, "test", ["ref.png"], {
                    "image_ratio": "9:16", "video_megapixels": 0.8, "video_seconds": 9.5,
                })
        self.assertEqual(workflow["size"]["inputs"]["aspect_ratio"], "9:16 (Portrait Widescreen)")
        self.assertEqual(workflow["size"]["inputs"]["megapixels"], 0.8)
        self.assertEqual(workflow["duration"]["inputs"]["value"], 9.5)


if __name__ == "__main__":
    unittest.main()
