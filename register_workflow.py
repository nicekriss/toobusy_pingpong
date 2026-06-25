# -*- coding: utf-8 -*-
r"""
Register a ComfyUI API-format workflow into config.json.

Usage:
  python register_workflow.py C:\path\workflow_API.json
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
WORKFLOWS = HERE / "workflows"
CONFIG = HERE / "config.json"

RATIO_MAP = {
    "1:1": "1:1 (Square)",
    "3:4": "3:4 (Portrait)",
    "4:3": "4:3 (Landscape)",
    "2:3": "2:3 (Portrait)",
    "3:2": "3:2 (Landscape)",
    "9:16": "9:16 (Portrait)",
    "16:9": "16:9 (Landscape)",
    "21:9": "21:9 (Cinematic)",
}


def prompt(label, default=""):
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def yn(label, default=True):
    d = "Y/n" if default else "y/N"
    ans = input(f"{label} [{d}]: ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes", "1", "true", "ㅇ", "예")


def safe_key(text):
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    return text.strip("._-") or "workflow"


def node_inputs(node):
    inputs = node.get("inputs") if isinstance(node, dict) else {}
    return inputs if isinstance(inputs, dict) else {}


def score_prompt(node_id, node, field, value):
    ct = str(node.get("class_type", "")).lower()
    key = field.lower()
    val = str(value).strip()
    bad = ("you are ", "system prompt", "task is", "follow these rules", "negative")
    if any(x in val.lower() for x in bad):
        return -100
    score = 0
    if key in ("prompt", "positive", "text"):
        score += 30
    if key == "value":
        score += 18
    if "textencode" in ct or "prompt" in ct:
        score += 12
    if "primitive" in ct or "multiline" in ct:
        score += 8
    if len(val) > 20:
        score += 6
    if len(val) > 1200:
        score -= 8
    if "negative" in ct or "negative" in key:
        score -= 50
    return score


def find_prompt_nodes(wf):
    candidates = []
    for nid, node in wf.items():
        for field, value in node_inputs(node).items():
            if not isinstance(value, str):
                continue
            if field not in ("prompt", "positive", "text", "value", "string"):
                continue
            score = score_prompt(nid, node, field, value)
            if score > 0:
                candidates.append((score, str(nid), field, node.get("class_type", ""), value))
    candidates.sort(reverse=True)
    return candidates


def find_seed_nodes(wf):
    out = []
    for nid, node in wf.items():
        for field, value in node_inputs(node).items():
            fl = field.lower()
            if "seed" in fl and isinstance(value, (int, float)):
                out.append((str(nid), field))
    return out


def find_image_nodes(wf):
    out = []
    for nid, node in wf.items():
        ct = str(node.get("class_type", "")).lower()
        for field, value in node_inputs(node).items():
            if field == "image" and ("loadimage" in ct or isinstance(value, str)):
                out.append((str(nid), field))
    return out


def find_prefix_node(wf):
    for nid, node in wf.items():
        if "filename_prefix" in node_inputs(node):
            return [str(nid), "filename_prefix"]
    return None


def find_output_node(wf):
    best = []
    for nid, node in wf.items():
        ct = str(node.get("class_type", "")).lower()
        score = 0
        if "saveimage" in ct:
            score = 50
        elif "savevideo" in ct or "videocombine" in ct:
            score = 45
        elif "saveaudio" in ct or "audio" in ct and "save" in ct:
            score = 40
        elif "filename_prefix" in node_inputs(node):
            score = 20
        if score:
            best.append((score, str(nid), ct))
    best.sort(reverse=True)
    return best[0][1] if best else None


def infer_type(wf):
    text = " ".join(str(node.get("class_type", "")).lower() for node in wf.values())
    if "saveaudio" in text or "audio" in text and "save" in text:
        return "audio"
    if "savevideo" in text or "videocombine" in text or "animated" in text:
        return "video"
    return "image"


def find_ratio_node(wf):
    for nid, node in wf.items():
        inputs = node_inputs(node)
        if "ratio_preset" in inputs:
            return [str(nid), "ratio_preset"], None
        if "aspect_ratio" in inputs:
            return [str(nid), "aspect_ratio"], RATIO_MAP
    return None, None


def choose_prompt_nodes(candidates):
    print("\n프롬프트 후보:")
    for i, (_, nid, field, ct, value) in enumerate(candidates[:8], 1):
        preview = re.sub(r"\s+", " ", str(value))[:90]
        print(f"  {i}. node {nid}.{field}  {ct}  :: {preview}")
    default = "1" if candidates else ""
    raw = prompt("사용할 후보 번호(여러 개는 쉼표, 비우면 1번)", default)
    nodes = []
    for part in re.split(r"\s*,\s*", raw):
        if not part:
            continue
        try:
            idx = int(part) - 1
        except ValueError:
            continue
        if 0 <= idx < len(candidates[:8]):
            _, nid, field, _ct, _value = candidates[idx]
            nodes.append([nid, field])
    return nodes


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else prompt("워크플로우 API JSON 경로")
    src_path = Path(src.strip('"')).expanduser().resolve()
    if not src_path.exists():
        raise SystemExit(f"파일을 찾을 수 없어요: {src_path}")

    wf = json.loads(src_path.read_text(encoding="utf-8"))
    if not isinstance(wf, dict):
        raise SystemExit("ComfyUI API Format JSON 객체가 아니에요.")

    WORKFLOWS.mkdir(exist_ok=True)
    dest = WORKFLOWS / src_path.name
    if src_path.resolve() != dest.resolve():
        if dest.exists() and not yn(f"{dest.name} 파일이 이미 있어요. 덮어쓸까요?", False):
            raise SystemExit("취소됨")
        shutil.copy2(src_path, dest)

    default_name = safe_key(src_path.stem.replace("_API", "").replace("image_", ""))
    name = prompt("대시보드에 표시할 이름", default_name)
    trigger = prompt("텔레그램 명령어", "/" + name)
    wtype = prompt("결과 타입(image/video/audio)", infer_type(wf)).lower()
    if wtype not in ("image", "video", "audio"):
        wtype = "image"
    llm = prompt("LLM 프롬프트 생성(image/video/none)", "video" if wtype == "video" else "image").lower()
    if llm not in ("image", "video", "none"):
        llm = "image"

    prompt_candidates = find_prompt_nodes(wf)
    prompt_nodes = choose_prompt_nodes(prompt_candidates)
    if not prompt_nodes:
        raise SystemExit("프롬프트 노드를 고르지 않아 등록을 중단했어요.")

    seed_nodes = find_seed_nodes(wf)
    image_nodes = find_image_nodes(wf)
    prefix_node = find_prefix_node(wf)
    output_node = find_output_node(wf)
    ratio_node, ratio_map = find_ratio_node(wf)

    print("\n자동 감지:")
    print("  seed_nodes:", seed_nodes or "-")
    print("  image_nodes:", image_nodes or "-")
    print("  prefix_node:", prefix_node or "-")
    print("  output_node:", output_node or "-")
    print("  ratio_node:", ratio_node or "-")

    spec = {
        "file": "workflows/" + dest.name,
        "trigger": trigger,
        "type": wtype,
        "llm": llm,
        "prompt_nodes": prompt_nodes,
        "seed_nodes": seed_nodes,
        "prefix_node": prefix_node,
        "prefix": "pingpong/" + safe_key(name).lower() + "_",
        "output_node": output_node or (prefix_node[0] if prefix_node else ""),
    }
    if image_nodes and yn("이미지 입력 노드도 등록할까요?", False):
        spec["image_nodes"] = image_nodes
    if ratio_node and yn("비율 옵션도 대시보드에 표시할까요?", True):
        spec["ratio_node"] = ratio_node
        if ratio_map:
            spec["ratio_map"] = ratio_map

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("custom_workflows", {})[name] = spec
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n등록 완료!")
    print(f"  이름: {name}")
    print(f"  파일: workflows/{dest.name}")
    print(f"  명령어: {trigger}")
    print("대시보드/봇을 새로고침 또는 재시작하면 보입니다.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n취소됨")
