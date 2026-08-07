# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import pingpong
from agent_core import AgentTool


class AgentRoutingTests(unittest.TestCase):
    def setUp(self):
        pingpong.reset_state()
        pingpong.AGENT_TOOL_STATUS_CACHE.update({"time": 0.0, "tools": []})

    def test_natural_media_sentence_is_not_mistaken_for_menu_button(self):
        self.assertIsNone(pingpong.menu_pick("비 오는 서울 영상을 만들어줘"))
        self.assertEqual(pingpong.menu_pick("2️⃣ 영상"), "video")

    @patch.object(pingpong, "agent_default_enabled", return_value=True)
    @patch.object(pingpong, "start_agent_request")
    def test_idle_natural_text_enters_agent(self, start_agent, _enabled):
        pingpong.handle_message({"text": "비 오는 서울 영상을 만들어줘"})
        start_agent.assert_called_once_with(
            "비 오는 서울 영상을 만들어줘", None, None, previous_session=None
        )

    @patch.object(pingpong, "agent_default_enabled", return_value=True)
    @patch.object(pingpong, "start_agent_request")
    def test_audio_attachment_also_enters_agent(self, start_agent, _enabled):
        audio = {"file_id": "audio-1", "mime_type": "audio/mpeg"}
        pingpong.handle_message({"audio": audio})
        start_agent.assert_called_once_with("", None, audio, previous_session=None)

    @patch.object(pingpong, "agent_default_enabled", return_value=True)
    @patch.object(pingpong, "recent_agent_session", return_value={"plan": {"tool": "video"}})
    @patch.object(pingpong, "start_agent_request")
    def test_short_revision_reuses_recent_plan(self, start_agent, recent, _enabled):
        pingpong.handle_message({"text": "좀 더 영화처럼"})
        start_agent.assert_called_once_with(
            "좀 더 영화처럼", None, None, previous_session=recent.return_value
        )

    @patch.object(pingpong.requests, "get")
    @patch.object(pingpong, "agent_tools")
    def test_planner_refuses_to_offer_only_unavailable_tools(self, agent_tools, get):
        agent_tools.return_value = [AgentTool("image", "이미지", "image", "이미지 생성")]
        get.return_value.json.return_value = {"status": {"image": {"ready": False}}}
        with self.assertRaisesRegex(RuntimeError, "실행 가능한 생성 기능"):
            pingpong.agent_planning_tools()

    def test_telegram_can_be_disabled_without_removing_credentials(self):
        old_token, old_chat = pingpong.TOKEN, pingpong.CHAT
        try:
            pingpong.TOKEN, pingpong.CHAT = "saved-token", 123
            with patch.object(pingpong, "reload_runtime_config", return_value={"telegram_enabled": False}):
                self.assertFalse(pingpong.telegram_enabled())
        finally:
            pingpong.TOKEN, pingpong.CHAT = old_token, old_chat


if __name__ == "__main__":
    unittest.main()
