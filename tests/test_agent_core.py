# -*- coding: utf-8 -*-
import json
import unittest

from agent_core import (
    build_tool_catalog,
    format_plan,
    is_likely_followup,
    parse_agent_plan,
    prepare_song_request,
    planner_user_prompt,
    planner_system_prompt,
)


class AgentCoreTests(unittest.TestCase):
    def test_custom_workflow_becomes_agent_tool(self):
        tools = build_tool_catalog({
            "내 편집기": {
                "type": "image",
                "image_nodes": [["1", "image"], ["2", "image"]],
                "agent_description": "두 장을 섞어 광고 이미지를 만든다.",
            }
        })
        tool = next(t for t in tools if t.id == "custom:내 편집기")
        self.assertEqual(tool.min_images, 2)
        self.assertEqual(tool.max_images, 2)
        self.assertIn("광고", tool.description)

    def test_valid_llm_plan_is_normalized_and_checked(self):
        tools = build_tool_catalog({})
        raw = json.dumps({
            "intent": "비 오는 밤 영상",
            "tool": "video",
            "normalized_request": "비 오는 서울의 밤거리를 달리는 6초 영화 영상",
            "reason": "움직임과 길이를 요청했기 때문",
            "confidence": 1.7,
        }, ensure_ascii=False)
        plan = parse_agent_plan(raw, tools, "비오는 밤 영상", image_count=0)
        self.assertEqual(plan.tool, "video")
        self.assertEqual(plan.confidence, 1.0)
        self.assertEqual(plan.missing_inputs, ())

    def test_invalid_tool_falls_back_without_trusting_llm(self):
        tools = build_tool_catalog({})
        raw = '{"tool":"delete_everything","confidence":0.9}'
        plan = parse_agent_plan(raw, tools, "신나는 음악을 만들어줘")
        self.assertEqual(plan.tool, "song")

    def test_required_inputs_are_reported(self):
        tools = build_tool_catalog({})
        raw = '{"tool":"faceswap","intent":"얼굴 교체","normalized_request":"얼굴을 바꿔줘","reason":"두 인물의 정체성을 합성","confidence":0.8}'
        plan = parse_agent_plan(raw, tools, "두 사람 얼굴 바꿔줘", image_count=1)
        self.assertEqual(plan.missing_inputs, ("image:1",))
        rendered = format_plan(plan, tools)
        self.assertIn("사진 1개", rendered)

    def test_custom_description_is_inferred_from_workflow_name(self):
        tools = build_tool_catalog({
            "크레아 캐니 2K": {
                "type": "image",
                "file": "workflows/krea_canny_hires.json",
                "image_nodes": [["1", "image"]],
            }
        })
        tool = next(t for t in tools if t.id.startswith("custom:"))
        self.assertIn("스케치", tool.description)
        self.assertIn("고해상도", tool.description)

    def test_followup_detection_is_conservative(self):
        self.assertTrue(is_likely_followup("좀 더 영화처럼 해줘"))
        self.assertTrue(is_likely_followup("얼굴은 그대로 유지해줘"))
        self.assertFalse(is_likely_followup("비 오는 서울에서 달리는 고양이를 만들어줘"))

    def test_previous_plan_and_preferences_are_given_to_planner(self):
        prompt = planner_user_prompt(
            "카메라만 느리게",
            previous_plan={"tool": "video", "normalized_request": "빠른 카메라"},
            preference_context="video=3",
        )
        self.assertIn("PREVIOUS PLAN", prompt)
        self.assertIn("video=3", prompt)

    def test_planner_is_told_not_to_invent_unspecified_details(self):
        prompt = planner_system_prompt(build_tool_catalog({}))
        self.assertIn("did not state", prompt)
        self.assertIn("leave it unspecified", prompt)

    def test_song_request_defaults_to_user_language_and_does_not_literalize_ost(self):
        brief = prepare_song_request("가슴 뛰는 일본 애니메이션 OST 같은 남자 보컬 록")
        self.assertIn("가사는 한국어로", brief)
        self.assertIn("작품 내용을 가사의 소재로 직접 쓰지 않는다", brief)
        self.assertEqual(brief, prepare_song_request(brief))

    def test_explicit_song_language_wins(self):
        brief = prepare_song_request("영어 가사로 신나는 록 노래를 만들어줘")
        self.assertIn("가사는 영어로", brief)

    def test_song_plan_exposes_music_interpretation_before_execution(self):
        tools = build_tool_catalog({})
        raw = json.dumps({
            "intent": "애니메이션 록 음악",
            "tool": "song",
            "normalized_request": "90년대 일본 애니메이션 OST 같은 남성 보컬 록",
            "reason": "음악 생성 요청",
            "confidence": 0.9,
        }, ensure_ascii=False)
        plan = parse_agent_plan(raw, tools, "애니메이션 록 노래를 만들어줘")
        self.assertIn("가사는 한국어로", plan.normalized_request)
        self.assertIn("고유명사", plan.normalized_request)


if __name__ == "__main__":
    unittest.main()
