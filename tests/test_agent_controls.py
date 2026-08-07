# -*- coding: utf-8 -*-
import unittest

from agent_controls import is_control_request, is_settings_only_request, parse_control_request


CAPS = {
    "controls": {
        "steps": {"default": 8, "min": 1, "max": 50},
        "cfg": {"default": 1, "min": 0, "max": 20},
        "denoise": {"default": 0.5, "min": 0, "max": 1},
        "seed": {"default": None, "min": 0, "max": 2**63 - 1},
        "image_ratio": {"default": "3:4"},
    },
    "supports_lora": True,
}


class AgentControlTests(unittest.TestCase):
    def test_relative_steps_uses_previous_value(self):
        result = parse_control_request("스탭수 조금만 올려줘", {"steps": 8}, CAPS)
        self.assertTrue(result.ok)
        self.assertEqual(result.settings["steps"], 10)

    def test_exact_denoise_and_same_seed(self):
        result = parse_control_request(
            "디노이즈 0.35로 하고 같은 시드 유지해줘",
            {"denoise": 0.5, "seed": 12345}, CAPS,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.settings["denoise"], 0.35)
        self.assertEqual(result.settings["seed"], 12345)

    def test_ratio_is_normalized(self):
        result = parse_control_request("16 : 9 비율로 바꿔줘", {"image_ratio": "3:4"}, CAPS)
        self.assertTrue(result.ok)
        self.assertEqual(result.settings["image_ratio"], "16:9")

    def test_lora_must_match_installed_name(self):
        names = [r"ZIT\ZIT_normal_girl01.safetensors", r"styles\film_grain.safetensors"]
        result = parse_control_request("film_grain 로라 강도 0.7 적용해줘", {}, CAPS, names)
        self.assertTrue(result.ok)
        self.assertEqual(result.settings["loras"][0]["name"], names[1])
        self.assertEqual(result.settings["loras"][0]["strength"], 0.7)

    def test_unknown_lora_is_rejected(self):
        result = parse_control_request("없는로라 LoRA 적용해줘", {}, CAPS, ["real.safetensors"])
        self.assertFalse(result.ok)
        self.assertTrue(any("찾지 못했어요" in x for x in result.errors))

    def test_unsupported_control_is_rejected(self):
        result = parse_control_request("CFG 4로 바꿔줘", {}, {"controls": {}, "supports_lora": False})
        self.assertFalse(result.ok)
        self.assertTrue(any("지원하지 않아요" in x for x in result.errors))

    def test_detection(self):
        self.assertTrue(is_control_request("같은 시드로 다시"))
        self.assertFalse(is_control_request("조명을 조금 따뜻하게 해줘"))
        self.assertTrue(is_settings_only_request("스탭 두 개 올려서 다시 만들어줘"))
        self.assertFalse(is_settings_only_request("16:9로 바닷가 배경에서 만들어줘"))


if __name__ == "__main__":
    unittest.main()
