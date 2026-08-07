# -*- coding: utf-8 -*-
"""First-run defaults and diagnostics that work before PingPong is configured."""
from __future__ import annotations

import json
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
EXAMPLE_PATH = HERE / "config.example.json"


def _desktop_dirs() -> tuple[str, str]:
    settings = Path(os.environ.get("APPDATA", "")) / "Comfy Desktop" / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
        if data.get("outputDir") and data.get("inputDir"):
            return str(data["outputDir"]), str(data["inputDir"])
    except Exception:
        pass
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Shared"
    return str(base / "output"), str(base / "input")


def bundled_workflows() -> dict[str, Any]:
    try:
        raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")).get("custom_workflows") or {}
    except Exception:
        return {}
    return {
        name: spec for name, spec in raw.items()
        if isinstance(spec, dict) and spec.get("file") and (HERE / str(spec["file"])).is_file()
    }


def default_config() -> dict[str, Any]:
    output_dir, input_dir = _desktop_dirs()
    return {
        "onboarding_complete": False,
        "telegram_enabled": False,
        "telegram_token": "",
        "telegram_chat_id": 0,
        "telegram_poll_timeout": 5,
        "llm_model": "",
        "agent_default": True,
        "agent_confirmation": "always",
        "agent_auto_confidence": 0.9,
        "agent_whisper_exe": "",
        "agent_whisper_model": "medium",
        "agent_whisper_timeout": 240,
        "comfy_api": "http://127.0.0.1:8188",
        "lmstudio_api": "http://127.0.0.1:1234",
        "comfy_output_dir": output_dir,
        "comfy_input_dir": input_dir,
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 8910,
        "send_upscaled": True,
        "zit_lora": False,
        "video_width": 0,
        "models": {
            "zit": "ZIT\\zImageTurbo_turbo.safetensors",
            "ltx_gguf": "LTX23\\ltx23DEVGGUFUnsloth_q4km.gguf",
            "klein": "FLUX2\\flux-2-klein-9b-kv-fp8.safetensors",
            "ace": "aceStepAudioGen_v15XLTurbo.safetensors",
        },
        "custom_workflows": bundled_workflows(),
    }


def ensure_config(path: str | os.PathLike[str] = CONFIG_PATH) -> tuple[dict[str, Any], bool]:
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data, False
    except Exception:
        pass
    data = default_config()
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data, True


def _probe_json(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            json.load(response)
        return True
    except Exception:
        return False


def first_run_status(config: dict[str, Any], mode_status: dict[str, Any] | None = None) -> dict[str, Any]:
    comfy = str(config.get("comfy_api") or "http://127.0.0.1:8188").rstrip("/")
    lmstudio = str(config.get("lmstudio_api") or "http://127.0.0.1:1234").rstrip("/")
    whisper = str(config.get("agent_whisper_exe") or "").strip()
    if not whisper:
        whisper = str(HERE.parent / "Faster-Whisper-XXL" / "faster-whisper-xxl.exe")
    modes = mode_status or {}
    ready_modes = sorted(name for name, row in modes.items() if isinstance(row, dict) and row.get("ready"))
    telegram = bool(config.get("telegram_enabled") and config.get("telegram_token") and config.get("telegram_chat_id"))
    checks = {
        "comfy": {"ready": _probe_json(comfy + "/system_stats"), "label": "ComfyUI"},
        "llm": {
            "ready": bool(config.get("llm_model")) and (_probe_json(lmstudio + "/v1/models") or bool(shutil.which("lms"))),
            "label": "요청 정리 AI",
        },
        "workflow": {"ready": bool(ready_modes), "label": "생성 기능", "count": len(ready_modes)},
        "telegram": {"ready": telegram, "label": "텔레그램", "optional": True},
        "voice": {"ready": os.path.isfile(whisper), "label": "음성 입력", "optional": True},
    }
    if not checks["comfy"]["ready"]:
        next_action = "ComfyUI를 실행하거나 연결 주소를 확인하세요."
    elif not checks["workflow"]["ready"]:
        next_action = "이미지 스타터에 필요한 노드와 모델을 준비하세요."
    elif not checks["llm"]["ready"]:
        next_action = "LM Studio 모델을 선택하세요. 생성 화면의 직접 모드는 먼저 사용할 수 있어요."
    else:
        next_action = "준비됐어요. 첫 요청을 보내보세요."
    return {
        "onboarding_complete": bool(config.get("onboarding_complete")),
        "checks": checks,
        "ready_modes": ready_modes,
        "core_ready": checks["comfy"]["ready"] and checks["workflow"]["ready"],
        "agent_ready": checks["comfy"]["ready"] and checks["workflow"]["ready"] and checks["llm"]["ready"],
        "next_action": next_action,
        "config": {
            "comfy_api": comfy,
            "lmstudio_api": lmstudio,
            "llm_model": str(config.get("llm_model") or ""),
            "telegram_enabled": bool(config.get("telegram_enabled")),
            "telegram_token_set": bool(config.get("telegram_token")),
            "telegram_chat_id": config.get("telegram_chat_id") or "",
        },
    }
