# -*- coding: utf-8 -*-
"""In-app patch discovery, backups, and idempotent config migrations."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "patches.json"
CONFIG = HERE / "config.json"
STATE = HERE / "update_state.json"
BACKUPS = HERE / "backups"
LOCK = threading.Lock()
HIDDEN_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=HIDDEN_FLAGS,
    )


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(HERE), *args], timeout=timeout)


def _json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def local_manifest() -> dict[str, Any]:
    data = _json_file(MANIFEST, {})
    return data if isinstance(data, dict) else {}


def remote_manifest(branch: str) -> dict[str, Any]:
    result = _git("show", f"origin/{branch}:patches.json", timeout=30)
    if result.returncode:
        return {}
    try:
        data = json.loads(result.stdout)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _count_revision(spec: str) -> int:
    result = _git("rev-list", "--count", spec, timeout=30)
    try:
        return int(result.stdout.strip()) if result.returncode == 0 else 0
    except ValueError:
        return 0


def update_status(refresh: bool = False) -> dict[str, Any]:
    if not (HERE / ".git").is_dir() or not shutil.which("git"):
        return {"ok": False, "available": False, "can_apply": False,
                "error": "Git으로 설치된 핑퐁에서만 자동 패치를 사용할 수 있어요."}
    if refresh:
        fetched = _git("fetch", "--prune", "origin", timeout=180)
        if fetched.returncode:
            return {"ok": False, "available": False, "can_apply": False,
                    "error": (fetched.stderr or fetched.stdout or "업데이트 확인 실패")[-500:]}
    branch_result = _git("branch", "--show-current", timeout=20)
    branch = branch_result.stdout.strip() or "main"
    remote_ref = _git("show-ref", "--verify", f"refs/remotes/origin/{branch}", timeout=20)
    if remote_ref.returncode:
        return {"ok": False, "available": False, "can_apply": False,
                "error": f"원격 {branch} 브랜치를 찾지 못했어요."}
    behind = _count_revision(f"HEAD..origin/{branch}")
    ahead = _count_revision(f"origin/{branch}..HEAD")
    dirty_result = _git("status", "--porcelain", "--untracked-files=no", timeout=20)
    tracked_dirty = bool(dirty_result.stdout.strip())
    local = local_manifest()
    remote = remote_manifest(branch) if behind else local
    local_ids = {str(row.get("id")) for row in (local.get("patches") or []) if isinstance(row, dict) and row.get("id")}
    remote_patches = remote.get("patches") if isinstance(remote.get("patches"), list) else []
    patches = [row for row in remote_patches if isinstance(row, dict) and str(row.get("id") or "") not in local_ids] if behind else []
    if behind and not patches:
        log_result = _git("log", "--format=%s", "--max-count=8", f"HEAD..origin/{branch}", timeout=30)
        patches = [{"id": f"commit-{i}", "kind": "core", "title": title, "summary": "프로그램 개선"}
                   for i, title in enumerate(log_result.stdout.splitlines(), 1) if title.strip()]
    can_apply = behind > 0 and ahead == 0 and not tracked_dirty
    reason = ""
    if ahead:
        reason = f"배포본에 없는 로컬 커밋이 {ahead}개 있어 자동 병합을 멈췄어요."
    elif tracked_dirty:
        reason = "프로그램 파일에 직접 수정한 내용이 있어요. 먼저 커밋하거나 제작자 모드에서 정리해주세요."
    return {
        "ok": True,
        "available": behind > 0,
        "can_apply": can_apply,
        "behind": behind,
        "ahead": ahead,
        "tracked_dirty": tracked_dirty,
        "branch": branch,
        "current_release": str(local.get("release") or "개발 버전"),
        "target_release": str(remote.get("release") or f"새 커밋 {behind}개"),
        "published_at": remote.get("published_at") or "",
        "patches": patches,
        "reason": reason,
    }


def automatic_update_status(max_age: int = 6 * 3600) -> dict[str, Any]:
    state = read_apply_state()
    last = float(state.get("last_check_epoch") or 0)
    should_refresh = time.time() - last >= max_age
    status = update_status(refresh=should_refresh)
    if should_refresh and status.get("ok"):
        state = read_apply_state()
        state.update({"last_check_epoch": time.time(), "last_check": time.strftime("%Y-%m-%d %H:%M:%S")})
        _write_json(STATE, state)
    return status


def backup_user_data() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = BACKUPS / ("before-patch-" + stamp)
    target.mkdir(parents=True, exist_ok=False)
    files = [
        "config.json", "agent_preferences.json", "agent_session.json",
        "dashboard_hidden.json", "dashboard_reference_presets.json",
    ]
    for name in files:
        source = HERE / name
        if source.is_file():
            shutil.copy2(source, target / name)
    workflows = HERE / "workflows"
    if workflows.is_dir():
        shutil.copytree(workflows, target / "workflows")
    _write_json(target / "backup.json", {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files})
    return target


def _merge_missing(target: dict[str, Any], defaults: dict[str, Any]) -> bool:
    changed = False
    for key, value in defaults.items():
        if key not in target:
            target[key] = value
            changed = True
        elif isinstance(target.get(key), dict) and isinstance(value, dict):
            changed = _merge_missing(target[key], value) or changed
    return changed


def apply_migrations(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = manifest or local_manifest()
    state = _json_file(STATE, {})
    applied = set(state.get("applied_patches") or [])
    config = _json_file(CONFIG, {})
    if not isinstance(config, dict):
        raise RuntimeError("config.json을 읽을 수 없어요.")
    changed = False
    newly_applied: list[str] = []
    for patch in manifest.get("patches") or []:
        if not isinstance(patch, dict) or not patch.get("id") or patch["id"] in applied:
            continue
        defaults = patch.get("config_defaults") or {}
        if isinstance(defaults, dict):
            changed = _merge_missing(config, defaults) or changed
        applied.add(str(patch["id"]))
        newly_applied.append(str(patch["id"]))
    if changed:
        _write_json(CONFIG, config)
    state.update({
        "state": "idle",
        "applied_patches": sorted(applied),
        "last_migration_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_release": manifest.get("release") or state.get("last_release") or "",
        "restart_required": bool(newly_applied),
    })
    _write_json(STATE, state)
    return {"changed": changed, "applied": newly_applied}


def read_apply_state() -> dict[str, Any]:
    state = _json_file(STATE, {})
    return state if isinstance(state, dict) else {}


def _apply_worker(expected_release: str) -> None:
    backup = None
    try:
        _write_json(STATE, {**read_apply_state(), "state": "backing_up", "progress": 10, "error": ""})
        backup = backup_user_data()
        _write_json(STATE, {**read_apply_state(), "state": "installing", "progress": 30,
                            "backup": str(backup), "target_release": expected_release})
        command = [
            "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(HERE / "update_pingpong.ps1"), "-AcceptedFromApp",
        ]
        result = _run(command, timeout=1800)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "패치 설치 실패")[-1600:])
        _write_json(STATE, {**read_apply_state(), "state": "migrating", "progress": 80})
        migrated = apply_migrations(local_manifest())
        state = read_apply_state()
        state.update({"state": "complete", "progress": 100, "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "restart_required": True, "migration": migrated, "error": ""})
        _write_json(STATE, state)
    except Exception as exc:
        state = read_apply_state()
        state.update({"state": "error", "progress": 0, "error": str(exc), "backup": str(backup or "")})
        _write_json(STATE, state)


def start_apply(expected_release: str = "") -> dict[str, Any]:
    with LOCK:
        state = read_apply_state()
        if state.get("state") in {"backing_up", "installing", "migrating"}:
            return {"ok": False, "error": "이미 패치를 적용하고 있어요."}
        status = update_status(refresh=False)
        if not status.get("available") or not status.get("can_apply"):
            return {"ok": False, "error": status.get("reason") or "지금 적용할 수 있는 패치가 없어요."}
        target = expected_release or str(status.get("target_release") or "")
        _write_json(STATE, {**state, "state": "queued", "progress": 1, "target_release": target, "error": ""})
        threading.Thread(target=_apply_worker, args=(target,), daemon=True).start()
        return {"ok": True, "target_release": target}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "migrate"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(update_status(args.refresh), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(apply_migrations(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
