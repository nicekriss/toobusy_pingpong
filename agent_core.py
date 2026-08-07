# -*- coding: utf-8 -*-
"""Intent-first planning primitives for PingPong.

This module deliberately has no Telegram, ComfyUI, or LM Studio dependency.
It turns the workflows currently available to PingPong into a small tool
catalog and validates an LLM-produced execution plan against that catalog.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AgentTool:
    id: str
    label: str
    media_type: str
    description: str
    min_images: int = 0
    max_images: int = 0
    needs_audio: bool = False


@dataclass(frozen=True)
class AgentPlan:
    intent: str
    tool: str
    normalized_request: str
    reason: str
    confidence: float
    missing_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["missing_inputs"] = list(self.missing_inputs)
        return data


BUILTIN_TOOLS = (
    AgentTool(
        "image", "새 이미지 만들기", "image",
        "참조 이미지 없이 설명만으로 새로운 이미지를 만든다.",
    ),
    AgentTool(
        "video", "영상 만들기", "video",
        "설명만으로 영상을 만들거나, 참조 사진 한 장을 짧은 영상으로 움직인다.",
        max_images=1,
    ),
    AgentTool(
        "song", "음악 만들기", "audio",
        "주제, 분위기, 장르 설명으로 새로운 음악과 가사를 만든다.",
    ),
    AgentTool(
        "song_reference", "참고곡으로 음악 만들기", "audio",
        "첨부한 음원의 질감과 분위기를 참고해 새로운 음악을 만든다.",
        needs_audio=True,
    ),
    AgentTool(
        "klein", "사진 편집·인물 합성", "image",
        "참조 사진 한 장의 인물이나 구도를 유지하면서 장면, 의상, 배경, 스타일을 바꾼다.",
        min_images=1, max_images=1,
    ),
    AgentTool(
        "faceswap", "얼굴 바꾸기", "image",
        "첫 사진의 몸·포즈·장면에 두 번째 사진의 얼굴 정체성을 적용한다.",
        min_images=2, max_images=2,
    ),
)


def _infer_custom_description(name: str, spec: dict[str, Any], media_type: str, image_count: int) -> str:
    filename = str(spec.get("file") or "")
    low = f"{name} {filename}".lower()
    traits: list[str] = []
    if any(k in low for k in ("canny", "스케치", "lineart", "control")):
        traits.append("스케치·라인·구도를 강하게 유지해 완성 이미지로 만든다")
    if any(k in low for k in ("edit", "편집", "identity")):
        traits.append("참조 사진을 자연어 지시대로 편집하며 인물 정체성 보존에 적합하다")
    if any(k in low for k in ("refsheet", "레퍼시트", "reference")) and media_type == "video":
        traits.append("레퍼런스 시트의 캐릭터와 스타일을 읽어 영상으로 만든다")
    if any(k in low for k in ("hires", "하이레즈", "2k")):
        traits.append("고해상도 마무리 단계를 포함한다")
    if any(k in low for k in ("turbo", "터보")):
        traits.append("빠른 생성용이다")
    if "3ref" in low or image_count >= 3:
        traits.append("참조 이미지 여러 장을 함께 사용한다")
    if not traits:
        action = {"image": "이미지를 생성한다", "video": "영상을 생성한다", "audio": "음악을 생성한다"}[media_type]
        traits.append(action)
    trigger = str(spec.get("trigger") or "").strip()
    details = f"사용자 등록 워크플로 '{name}'"
    if trigger:
        details += f"(기존 명령 {trigger})"
    return details + ". " + " ".join(traits)


def build_tool_catalog(custom_workflows: dict[str, Any] | None) -> list[AgentTool]:
    """Return built-ins plus user workflows that opted into agent routing."""
    tools = list(BUILTIN_TOOLS)
    for name, raw in (custom_workflows or {}).items():
        spec = raw if isinstance(raw, dict) else {}
        if spec.get("agent_enabled", True) is False:
            continue
        media_type = str(spec.get("type") or "image").strip().lower()
        if media_type not in {"image", "video", "audio"}:
            continue
        image_count = len(spec.get("image_nodes") or [])
        description = str(spec.get("agent_description") or "").strip()
        if not description:
            description = _infer_custom_description(name, spec, media_type, image_count)
        tools.append(AgentTool(
            id=f"custom:{name}",
            label=str(spec.get("label") or name),
            media_type=media_type,
            description=description,
            min_images=image_count,
            max_images=image_count,
            needs_audio=bool(spec.get("audio_nodes")),
        ))
    return tools


def planner_system_prompt(tools: Iterable[AgentTool]) -> str:
    rows = [
        {
            "id": t.id,
            "label": t.label,
            "media_type": t.media_type,
            "description": t.description,
            "min_images": t.min_images,
            "max_images": t.max_images,
            "needs_audio": t.needs_audio,
        }
        for t in tools
    ]
    return (
        "You are PingPong's production planner. Convert a rough Korean or English request into one "
        "immediately executable plan. Remove filler and repetition, preserve the user's creative intent, "
        "Never add a subject attribute, identity, breed, age, clothing, location detail, or story fact that "
        "the user did not state. If a detail is unspecified, leave it unspecified instead of guessing. "
        "and select exactly one tool from the supplied catalog. Prefer a user custom workflow only when "
        "its name or description clearly fits better than a built-in. Do not invent tool ids. "
        "Return ONLY one JSON object with these keys: intent, tool, normalized_request, reason, confidence. "
        "intent and reason must be concise Korean. normalized_request must be a polished Korean production "
        "brief that retains quoted dialogue and exact on-screen text. confidence is 0.0 to 1.0.\n"
        "TOOL CATALOG:\n" + json.dumps(rows, ensure_ascii=False)
    )


def planner_user_prompt(
    request: str,
    image_count: int = 0,
    has_audio: bool = False,
    previous_plan: dict[str, Any] | None = None,
    preference_context: str = "",
) -> str:
    prompt = (
        f"USER REQUEST:\n{(request or '').strip()}\n\n"
        f"ATTACHMENTS: images={max(0, int(image_count or 0))}, audio={bool(has_audio)}"
    )
    if previous_plan:
        prompt += (
            "\n\nPREVIOUS PLAN (the new request is a revision or continuation):\n"
            + json.dumps(previous_plan, ensure_ascii=False)
            + "\nPreserve everything the user did not ask to change. You may keep or change the tool as needed."
        )
    if preference_context:
        prompt += "\n\nUSER PREFERENCES:\n" + str(preference_context).strip()
    return prompt


def prepare_song_request(request: str) -> str:
    """Add safe, low-friction music assumptions without changing the user's idea.

    A title, artist, or OST mention normally describes the desired sound. It must
    not silently become the literal subject of the lyrics. When the user does not
    name a lyrics language, matching the language they used is the least
    surprising default.
    """
    text = str(request or "").strip()
    if not text or "[핑퐁 음악 해석]" in text:
        return text
    low = text.lower()
    explicit_languages = (
        (("한국어 가사", "한글 가사", "가사는 한국어", "가사를 한국어"), "한국어"),
        (("영어 가사", "english lyrics", "가사는 영어", "가사를 영어"), "영어"),
        (("일본어 가사", "일어 가사", "japanese lyrics", "가사는 일본어", "가사를 일본어"), "일본어"),
    )
    lyrics_language = ""
    for phrases, language in explicit_languages:
        if any(phrase in low for phrase in phrases):
            lyrics_language = language
            break
    if not lyrics_language:
        lyrics_language = "한국어" if re.search(r"[가-힣]", text) else "사용자 요청과 같은 언어"
    instrumental = any(term in low for term in ("연주곡", "인스트루멘탈", "instrumental", "보컬 없이", "가사 없이"))
    language_rule = "보컬과 가사 없이 연주곡으로 만든다." if instrumental else f"가사는 {lyrics_language}로 쓴다."
    return (
        text
        + "\n\n[핑퐁 음악 해석]\n"
        + f"- {language_rule}\n"
        + "- 언급된 작품명·아티스트·OST는 음악의 시대감, 정서, 템포, 악기, 편곡과 보컬 에너지를 이해하기 위한 참고다.\n"
        + "- 사용자가 명시하지 않았다면 그 고유명사나 작품 내용을 가사의 소재로 직접 쓰지 않는다.\n"
        + "- 막연한 장르명만 쓰지 말고 도입, 벌스, 프리코러스, 후렴의 에너지 변화와 핵심 악기를 구체화한다."
    )


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = re.sub(r"```(?:json)?|```", "", str(raw or ""), flags=re.I).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def heuristic_tool(request: str, image_count: int = 0, has_audio: bool = False) -> str:
    low = str(request or "").lower()
    if has_audio or any(k in low for k in ("참고곡", "이 음악", "이 노래", "음원 참고")):
        return "song_reference"
    if any(k in low for k in ("페이스스왑", "얼굴 바꿔", "얼굴 교체", "face swap")):
        return "faceswap"
    if any(k in low for k in ("노래", "음악", "브금", "bgm", "작곡")):
        return "song"
    if any(k in low for k in ("영상", "움직여", "애니메이션", "카메라 무빙", "video")):
        return "video"
    if image_count and any(k in low for k in ("바꿔", "입혀", "유지", "편집", "합성", "배경", "스타일")):
        return "klein"
    return "image"


def is_likely_followup(request: str) -> bool:
    """Conservative signal for short Korean revision requests."""
    text = str(request or "").strip()
    if not text or len(text) > 140:
        return False
    low = text.lower()
    starters = (
        "좀 더", "조금 더", "조금만", "아까", "이전", "그거", "그걸", "이걸",
        "다시", "이번엔", "이번에는", "카메라만", "배경만", "색감만", "얼굴은",
        "얼굴만", "움직임만", "더 ", "덜 ", "말고 ", "그대로 ",
    )
    if low.startswith(starters):
        return True
    revision_terms = ("유지해", "바꿔줘", "수정해", "강하게", "약하게", "느리게", "빠르게")
    return len(text) <= 60 and any(term in low for term in revision_terms)


def parse_agent_plan(
    raw: str,
    tools: Iterable[AgentTool],
    request: str,
    image_count: int = 0,
    has_audio: bool = False,
) -> AgentPlan:
    catalog = {tool.id: tool for tool in tools}
    data = _extract_json_object(raw)
    tool_id = str(data.get("tool") or "").strip()
    if tool_id not in catalog:
        tool_id = heuristic_tool(request, image_count, has_audio)
    if tool_id not in catalog:
        tool_id = "image"
    tool = catalog[tool_id]

    try:
        confidence = float(data.get("confidence", 0.55))
    except (TypeError, ValueError):
        confidence = 0.55
    confidence = max(0.0, min(1.0, confidence))

    missing: list[str] = []
    if image_count < tool.min_images:
        missing.append(f"image:{tool.min_images - image_count}")
    if tool.needs_audio and not has_audio:
        missing.append("audio:1")

    normalized = str(data.get("normalized_request") or request or "").strip()
    if tool_id in {"song", "song_reference"}:
        normalized = prepare_song_request(normalized)
    intent = str(data.get("intent") or normalized or request or "작업 요청").strip()
    reason = str(data.get("reason") or f"{tool.label} 도구가 요청에 가장 가깝습니다.").strip()
    return AgentPlan(
        intent=intent[:240],
        tool=tool_id,
        normalized_request=normalized[:3000],
        reason=reason[:300],
        confidence=confidence,
        missing_inputs=tuple(missing),
    )


def format_plan(plan: AgentPlan | dict[str, Any], tools: Iterable[AgentTool]) -> str:
    data = plan.to_dict() if isinstance(plan, AgentPlan) else dict(plan)
    catalog = {tool.id: tool for tool in tools}
    tool = catalog.get(str(data.get("tool") or ""))
    label = tool.label if tool else str(data.get("tool") or "알 수 없는 도구")
    confidence = int(round(float(data.get("confidence") or 0) * 100))
    lines = [
        "🧭 핑퐁이 이렇게 이해했어요",
        f"• 의도: {data.get('intent') or data.get('normalized_request')}",
        f"• 방법: {label}",
        f"• 판단: {data.get('reason')}",
        f"• 확신: {confidence}%",
    ]
    missing = list(data.get("missing_inputs") or [])
    if missing:
        labels = []
        for item in missing:
            kind, _, count = str(item).partition(":")
            labels.append(("사진" if kind == "image" else "음원") + f" {count or '1'}개")
        lines.append("• 더 필요함: " + ", ".join(labels))
    return "\n".join(lines)
