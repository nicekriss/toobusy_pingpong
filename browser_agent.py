# -*- coding: utf-8 -*-
"""Small file-backed session store shared by the dashboard and PingPong worker."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SESSION_DIR = HERE / "browser_agent_sessions"
SESSION_ID = re.compile(r"^[a-f0-9]{24}$")


def _path(session_id: str) -> Path:
    session_id = str(session_id or "").lower()
    if not SESSION_ID.fullmatch(session_id):
        raise ValueError("invalid browser agent session")
    return SESSION_DIR / (session_id + ".json")


def create(request: str, **payload: Any) -> dict[str, Any]:
    session_id = uuid.uuid4().hex[:24]
    data = {
        "id": session_id,
        "state": "planning",
        "request": str(request or "").strip(),
        "created_at": time.time(),
        "updated_at": time.time(),
        **payload,
    }
    write(data)
    return data


def read(session_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_path(session_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write(data: dict[str, Any]) -> dict[str, Any]:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(data or {})
    data["updated_at"] = time.time()
    target = _path(data.get("id", ""))
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    return data


def update(session_id: str, **changes: Any) -> dict[str, Any]:
    data = read(session_id)
    if not data:
        raise FileNotFoundError("browser agent session not found")
    data.update(changes)
    return write(data)


def public(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"ok": False, "error": "세션을 찾지 못했어요."}
    allowed = {
        "id", "state", "request", "plan", "settings", "changes", "error",
        "results", "created_at", "updated_at", "queued_at", "started_at", "completed_at",
    }
    return {"ok": True, **{key: value for key, value in data.items() if key in allowed}}
