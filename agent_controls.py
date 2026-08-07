# -*- coding: utf-8 -*-
"""Deterministic parsing for technical follow-ups to a generation request."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Any, Iterable


@dataclass
class ControlResult:
    matched: bool
    settings: dict[str, Any]
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.matched and not self.errors


CONTROL_WORDS = re.compile(
    r"(?:스텝|스탭|steps?|cfg|씨에프지|디노이즈|denoise|시드|seed|"
    r"로라|lora|비율|화면비|종횡비|\d+\s*:\s*\d+)",
    re.IGNORECASE,
)


def is_control_request(text: str) -> bool:
    return bool(CONTROL_WORDS.search(str(text or "")))


def is_settings_only_request(text: str) -> bool:
    """Conservative gate for skipping the creative planner on a follow-up."""
    text = str(text or "").strip()
    if not is_control_request(text):
        return False
    content_cues = re.compile(
        r"(?:배경|조명|색감|구도|카메라|의상|옷|표정|포즈|얼굴|머리|인물|사람|"
        r"풍경|장면|분위기|스타일|에서|고양이|강아지|자동차|건물)"
    )
    if content_cues.search(text):
        return False
    if re.search(r"(?:만들|그려|편집)", text) and "다시" not in text:
        return False
    return len(text) <= 120


def _number_near(text: str, names: str) -> float | None:
    patterns = (
        rf"(?:{names})(?:\s*(?:수|값|강도))?\s*(?:을|를|은|는|이|가|:|=)?\s*(-?\d+(?:\.\d+)?)",
        rf"(-?\d+(?:\.\d+)?)\s*(?:으로|로)?\s*(?:{names})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _direction(text: str, names: str) -> int:
    if not re.search(names, text, re.IGNORECASE):
        return 0
    if re.search(r"(?:올려|높여|늘려|증가|up|increase)", text, re.IGNORECASE):
        return 1
    if re.search(r"(?:내려|낮춰|줄여|감소|down|decrease)", text, re.IGNORECASE):
        return -1
    return 0


def _limits(capabilities: dict[str, Any], key: str, default: tuple[float, float]) -> tuple[float, float]:
    raw = (capabilities.get("controls") or {}).get(key) or {}
    return float(raw.get("min", default[0])), float(raw.get("max", default[1]))


def _supports(capabilities: dict[str, Any], key: str) -> bool:
    return key in (capabilities.get("controls") or {})


def _set_number(
    result: ControlResult,
    capabilities: dict[str, Any],
    key: str,
    label: str,
    value: float,
    integer: bool = False,
) -> None:
    if not _supports(capabilities, key):
        result.errors.append(f"현재 워크플로는 {label} 조절을 지원하지 않아요.")
        return
    lo, hi = _limits(capabilities, key, (1, 100) if integer else (0, 30))
    value = max(lo, min(hi, value))
    value = int(round(value)) if integer else round(float(value), 4)
    old = result.settings.get(key)
    result.settings[key] = value
    touched = set(result.settings.get("_touched_controls") or [])
    touched.add(key)
    result.settings["_touched_controls"] = sorted(touched)
    result.changes.append(f"{label}: {old if old is not None else '기본값'} → {value}")


def _norm_lora(value: str) -> str:
    stem = os.path.splitext(os.path.basename(str(value).replace("\\", "/")))[0]
    return re.sub(r"[^0-9a-z가-힣]+", "", stem.lower())


def _match_lora(text: str, available: Iterable[str]) -> tuple[str | None, list[str]]:
    names = [str(x) for x in available if str(x).strip()]
    normalized_text = re.sub(r"[^0-9a-z가-힣]+", "", text.lower())
    direct = [name for name in names if _norm_lora(name) and _norm_lora(name) in normalized_text]
    if direct:
        return max(direct, key=lambda x: len(_norm_lora(x))), []

    query_match = re.search(
        r"(?:로라|lora)\s+(.+?)(?:\s+(?:적용|넣어|추가|써|사용|강도)|$)", text, re.IGNORECASE
    )
    if not query_match:
        query_match = re.search(
            r"([0-9a-z가-힣_.\\/ -]+?)\s+(?:로라|lora)(?:\s|$)", text, re.IGNORECASE
        )
    query = _norm_lora(query_match.group(1)) if query_match else ""
    if not query:
        return None, names[:5]
    hits = [name for name in names if query in _norm_lora(name) or _norm_lora(name) in query]
    if len(hits) == 1:
        return hits[0], []
    return None, hits[:5]


def parse_control_request(
    text: str,
    current_settings: dict[str, Any] | None,
    capabilities: dict[str, Any] | None,
    available_loras: Iterable[str] = (),
) -> ControlResult:
    text = str(text or "").strip()
    capabilities = capabilities or {}
    result = ControlResult(is_control_request(text), dict(current_settings or {}))
    if not result.matched:
        return result

    specs = (
        ("steps", r"스텝|스탭|steps?", "스텝", 2.0, True),
        ("cfg", r"cfg|씨에프지", "CFG", 0.5, False),
        ("denoise", r"디노이즈|denoise", "디노이즈", 0.05, False),
    )
    for key, names, label, delta, integer in specs:
        if not re.search(names, text, re.IGNORECASE):
            continue
        explicit = _number_near(text, names)
        direction = _direction(text, names)
        if explicit is not None:
            value = explicit
        elif direction:
            default = ((capabilities.get("controls") or {}).get(key) or {}).get("default")
            current = result.settings.get(key, default)
            if current is None:
                result.errors.append(f"현재 {label} 값을 읽지 못해 상대 변경을 할 수 없어요.")
                continue
            value = float(current) + direction * delta
        else:
            result.errors.append(f"{label} 값을 얼마로 바꿀지 알려주세요.")
            continue
        _set_number(result, capabilities, key, label, value, integer)
        if direction and explicit is None:
            deltas = dict(result.settings.get("_control_deltas") or {})
            deltas[key] = direction * delta
            result.settings["_control_deltas"] = deltas

    ratio_match = re.search(r"\b(1\s*:\s*1|3\s*:\s*4|4\s*:\s*3|2\s*:\s*3|3\s*:\s*2|9\s*:\s*16|16\s*:\s*9|21\s*:\s*9)\b", text)
    ratio = ratio_match.group(1).replace(" ", "") if ratio_match else None
    if not ratio:
        if "정사각" in text:
            ratio = "1:1"
        elif re.search(r"(?:세로|portrait)", text, re.IGNORECASE):
            ratio = "9:16"
        elif re.search(r"(?:가로|landscape)", text, re.IGNORECASE):
            ratio = "16:9"
    if ratio:
        if not _supports(capabilities, "image_ratio"):
            result.errors.append("현재 워크플로는 화면 비율 변경을 지원하지 않아요.")
        else:
            old = result.settings.get("image_ratio")
            result.settings["image_ratio"] = ratio
            touched = set(result.settings.get("_touched_controls") or [])
            touched.add("image_ratio")
            result.settings["_touched_controls"] = sorted(touched)
            result.changes.append(f"화면 비율: {old or '기본값'} → {ratio}")

    if re.search(r"시드|seed", text, re.IGNORECASE):
        seed_value = _number_near(text, r"시드|seed")
        if not _supports(capabilities, "seed"):
            result.errors.append("현재 워크플로는 시드 지정을 지원하지 않아요.")
        elif seed_value is not None:
            seed = max(0, min(2**63 - 1, int(seed_value)))
            old = result.settings.get("seed")
            result.settings["seed"] = seed
            touched = set(result.settings.get("_touched_controls") or [])
            touched.add("seed")
            result.settings["_touched_controls"] = sorted(touched)
            result.changes.append(f"시드: {old if old is not None else '자동'} → {seed}")
        elif re.search(r"(?:같은|그대로|유지|고정|same|keep)", text, re.IGNORECASE):
            if result.settings.get("seed") is None:
                result.errors.append("직전 시드가 저장되어 있지 않아 같은 시드를 쓸 수 없어요.")
            else:
                result.changes.append(f"시드 유지: {result.settings['seed']}")
        elif re.search(r"(?:새|다른|랜덤|random|new)", text, re.IGNORECASE):
            result.settings.pop("seed", None)
            result.settings["random_seed"] = True
            touched = set(result.settings.get("_touched_controls") or [])
            touched.add("seed")
            result.settings["_touched_controls"] = sorted(touched)
            result.changes.append("시드: 새 랜덤 시드")
        else:
            result.errors.append("같은 시드인지, 새 시드인지, 숫자 시드인지 알려주세요.")

    if re.search(r"로라|lora", text, re.IGNORECASE):
        if not capabilities.get("supports_lora"):
            result.errors.append("현재 워크플로에는 LoRA 적용 슬롯이 없어요.")
        elif re.search(r"(?:빼|제거|해제|끄|remove|without)", text, re.IGNORECASE):
            old = list(result.settings.get("loras") or [])
            result.settings["loras"] = []
            touched = set(result.settings.get("_touched_controls") or [])
            touched.add("loras")
            result.settings["_touched_controls"] = sorted(touched)
            result.changes.append(f"LoRA 제거: {len(old)}개 → 0개")
        else:
            match, suggestions = _match_lora(text, available_loras)
            if not match:
                suffix = (" 후보: " + ", ".join(suggestions)) if suggestions else ""
                result.errors.append("설치된 LoRA에서 정확히 찾지 못했어요." + suffix)
            else:
                strength = _number_near(text, r"강도|strength")
                strength = max(0.0, min(2.0, float(strength if strength is not None else 1.0)))
                loras = [x for x in (result.settings.get("loras") or []) if x.get("name") != match]
                loras.append({"name": match, "strength": round(strength, 3)})
                result.settings["loras"] = loras
                touched = set(result.settings.get("_touched_controls") or [])
                touched.add("loras")
                result.settings["_touched_controls"] = sorted(touched)
                result.changes.append(f"LoRA 적용: {match} (강도 {strength:g})")

    if result.matched and not result.changes and not result.errors:
        result.errors.append("바꿀 생성 설정을 구체적으로 알려주세요.")
    return result


def format_control_changes(changes: Iterable[str]) -> str:
    rows = [str(x) for x in changes if str(x).strip()]
    return "\n".join("• " + row for row in rows)
