# -*- coding: utf-8 -*-
"""Ping-Pong install/runtime health check.

This script is intentionally read-only. It helps a first-time user see what is
ready, what is missing, and what can be fixed next.
"""
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
EXAMPLE = HERE / "config.example.json"
WORKFLOWS = HERE / "workflows"
SNAPSHOT = HERE / "snapshots" / "snapshot-comfy-post-update-20260627.json"


def auto_comfy_dirs():
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Shared"
    return str(base / "output"), str(base / "input")


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def normalize_model(name):
    return str(name or "").replace("\\", "/").lower()


class Report:
    def __init__(self):
        self.rows = []
        self.counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0}

    def add(self, level, text):
        self.counts[level] = self.counts.get(level, 0) + 1
        self.rows.append((level, text))

    def ok(self, text):
        self.add("ok", text)

    def warn(self, text):
        self.add("warn", text)

    def fail(self, text):
        self.add("fail", text)

    def info(self, text):
        self.add("info", text)


def icon(level):
    return {"ok": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️"}.get(level, "-")


def node_info(comfy, cls):
    try:
        r = urllib.request.urlopen(f"{comfy}/object_info/{cls}", timeout=5)
        if getattr(r, "status", 200) == 200:
            return json.load(r).get(cls)
    except Exception:
        return None
    return None


def model_options(info, field):
    if not info:
        return None
    for section in ("required", "optional"):
        try:
            opts = info["input"][section][field][0]
            if isinstance(opts, list):
                return opts
        except Exception:
            pass
    return None


def has_model(expected, options):
    if not expected or options is None:
        return True
    exp = normalize_model(expected)
    return exp in [normalize_model(o) for o in options]


FEATURE_CHECKS = [
    ("이미지 ZIT", ["ToobusyZImageTurbo", "ToobusyHiresUpscale"], "ToobusyZImageTurbo", "model_name", "zit"),
    ("영상 LTX Director", ["LTXDirector", "UnetLoaderGGUF", "VAEDecodeTiled"], "UnetLoaderGGUF", "unet_name", "ltx_gguf"),
    ("음악 ACE", ["TextEncodeAceStepAudio1.5"], "UNETLoader", "unet_name", "ace"),
    ("Flux2 Klein 보드", ["ToobusyFlux2Klein", "ToobusyReferenceBoard"], "ToobusyFlux2Klein", "model_name", "klein"),
]


def workflow_class_types(path):
    data = load_json(path, {})
    out = set()
    if isinstance(data, dict):
        for node in data.values():
            if isinstance(node, dict) and node.get("class_type"):
                out.add(str(node["class_type"]))
    return sorted(out)


def check_workflow_files(rep, cfg, example):
    expected = set()
    for spec in ((cfg or {}).get("custom_workflows") or {}).values():
        if isinstance(spec, dict) and spec.get("file"):
            expected.add(spec["file"])
    if not expected:
        for spec in ((example or {}).get("custom_workflows") or {}).values():
            if isinstance(spec, dict) and spec.get("file") and (HERE / spec["file"]).is_file():
                expected.add(spec["file"])
    for wf in sorted(expected):
        path = HERE / wf
        if path.is_file():
            rep.ok(f"워크플로 파일 있음: {wf}")
        else:
            rep.fail(f"워크플로 파일 없음: {wf}")
    if SNAPSHOT.is_file():
        rep.ok(f"Comfy 스냅샷 있음: {SNAPSHOT.relative_to(HERE)}")
    else:
        rep.warn("Comfy 스냅샷 없음: snapshots/snapshot-comfy-post-update-20260627.json")


def check_config(rep):
    example = load_json(EXAMPLE, {})
    cfg = load_json(CONFIG)
    if cfg is None:
        rep.fail("config.json 없음 → 설치.bat을 먼저 실행하세요.")
        cfg = {}
    else:
        rep.ok("config.json 읽기 성공")
    for key in ("telegram_token", "telegram_chat_id", "llm_model"):
        if cfg.get(key):
            rep.ok(f"config.{key} 설정됨")
        else:
            rep.warn(f"config.{key} 비어 있음")
    return cfg, example


def check_paths(rep, cfg):
    out_auto, in_auto = auto_comfy_dirs()
    out_dir = cfg.get("comfy_output_dir") or out_auto
    in_dir = cfg.get("comfy_input_dir") or in_auto
    rep.ok(f"Comfy output 폴더 확인: {out_dir}") if os.path.isdir(out_dir) else rep.warn(f"Comfy output 폴더 없음: {out_dir}")
    rep.ok(f"Comfy input 폴더 확인: {in_dir}") if os.path.isdir(in_dir) else rep.warn(f"Comfy input 폴더 없음: {in_dir}")


def check_lmstudio(rep, cfg):
    if shutil.which("lms"):
        rep.ok("LM Studio CLI(lms) 발견")
    else:
        rep.warn("LM Studio CLI(lms) 없음 → LM Studio 설치/CLI 활성화 확인")
    api = (cfg.get("lmstudio_api") or "http://127.0.0.1:1234").rstrip("/")
    try:
        data = get_json(api + "/v1/models", timeout=3)
        ids = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
        if ids:
            rep.ok("LM Studio API 응답: " + ", ".join(ids[:3]))
        else:
            rep.warn("LM Studio API는 응답하지만 로드된 모델이 없음")
    except Exception:
        rep.warn(f"LM Studio API 응답 없음: {api}")
    if any((s.get("llm") == "refsheet_video") for s in (cfg.get("custom_workflows") or {}).values() if isinstance(s, dict)):
        rep.info("LTX레퍼시트는 비전 가능한 LLM이 필요합니다. 텍스트 전용 모델이면 레퍼런스 시트를 못 봅니다.")


def check_comfy(rep, cfg):
    comfy = (cfg.get("comfy_api") or "http://127.0.0.1:8188").rstrip("/")
    try:
        get_json(comfy + "/system_stats", timeout=5)
        rep.ok(f"ComfyUI 연결됨: {comfy}")
    except Exception:
        rep.fail(f"ComfyUI 연결 안 됨: {comfy} → ComfyUI Desktop을 먼저 켜세요.")
        return False

    models = cfg.get("models") or {}
    for label, nodes, loader, field, model_key in FEATURE_CHECKS:
        missing = [n for n in nodes if not node_info(comfy, n)]
        if missing:
            rep.warn(f"{label}: 노드 없음 → {', '.join(missing)}")
            continue
        expected = models.get(model_key, "")
        opts = model_options(node_info(comfy, loader), field)
        if not has_model(expected, opts):
            rep.warn(f"{label}: 모델 없음/이름 다름 → {expected}")
        else:
            rep.ok(f"{label}: 노드/모델 확인")

    # Workflow-level node smoke check: catch missing custom nodes introduced later.
    checked = 0
    for path in sorted(WORKFLOWS.glob("*.json")):
        missing = []
        for cls in workflow_class_types(path):
            if not node_info(comfy, cls):
                missing.append(cls)
            if len(missing) >= 6:
                break
        checked += 1
        if missing:
            rep.warn(f"{path.name}: 누락 노드 가능 → {', '.join(missing)}")
    if checked:
        rep.info(f"워크플로 노드 스캔 완료: {checked}개")
    return True


def run_checks():
    rep = Report()
    cfg, example = check_config(rep)
    check_paths(rep, cfg)
    check_workflow_files(rep, cfg, example)
    check_lmstudio(rep, cfg)
    check_comfy(rep, cfg)
    return rep


def main():
    rep = run_checks()
    print()
    print("=" * 58)
    print("  Ping-Pong 설치/실행 점검")
    print("=" * 58)
    for level, text in rep.rows:
        print(f"{icon(level)} {text}")
    print("-" * 58)
    print(f"OK {rep.counts['ok']} / WARN {rep.counts['warn']} / FAIL {rep.counts['fail']}")
    if rep.counts["fail"]:
        print("먼저 ❌ 항목을 해결하세요. 대부분 ComfyUI 실행 또는 config.json 생성 문제입니다.")
    elif rep.counts["warn"]:
        print("실행은 가능할 수 있지만 ⚠️ 항목 기능은 실패할 수 있어요.")
    else:
        print("기본 준비 상태가 좋아요. run_bot.bat으로 시작하면 됩니다.")
    return 1 if rep.counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
