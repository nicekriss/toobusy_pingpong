# -*- coding: utf-8 -*-
r"""Register a ComfyUI API-format workflow into config.json.

Usage:
  python register_workflow.py C:\path\workflow_API.json
"""
import json
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

PROMPT_FIELDS = {
    "prompt", "positive", "negative", "text", "value", "string",
    "caption", "global_prompt", "positive_prompt", "negative_prompt", "timeline_data",
}
NEGATIVE_WORDS = (
    "negative", "bad anatomy", "bad hands", "blurry", "low quality",
    "worst quality", "deformed", "watermark", "jpeg artifacts",
)
META_WORDS = (
    "you are ", "system prompt", "follow these rules", "instruction",
    "assistant", "respond with", "output only",
)


def ask(label, default=""):
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def yes_no(label, default=True):
    d = "Y/n" if default else "y/N"
    ans = input(f"{label} [{d}]: ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes", "1", "true", "on", "ㅇ", "예", "네")


def safe_key(text):
    text = re.sub(r"\s+", "_", str(text or "").strip())
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    return text.strip("._-") or "workflow"


def node_inputs(node):
    inputs = node.get("inputs") if isinstance(node, dict) else {}
    return inputs if isinstance(inputs, dict) else {}


def class_type(wf, node_id):
    node = wf.get(str(node_id), {})
    return str(node.get("class_type", ""))


def is_link(value):
    return isinstance(value, list) and len(value) >= 1 and isinstance(value[0], (str, int))


def source_id(value):
    return str(value[0]) if is_link(value) else ""


def build_consumers(wf):
    consumers = {}
    for nid, node in wf.items():
        for field, value in node_inputs(node).items():
            if is_link(value):
                consumers.setdefault(source_id(value), []).append((str(nid), field, class_type(wf, nid)))
    return consumers


def downstream_text(wf, consumers, start_id, depth=0, seen=None):
    if seen is None:
        seen = set()
    if depth > 5 or start_id in seen:
        return ""
    seen.add(start_id)
    parts = []
    for dst, field, ct in consumers.get(str(start_id), []):
        parts.append(" ".join([dst, field, ct]).lower())
        parts.append(downstream_text(wf, consumers, dst, depth + 1, seen))
    return " ".join(parts)


def prompt_kind_score(wf, consumers, nid, field, value):
    ct = class_type(wf, nid).lower()
    key = field.lower()
    val = str(value or "").strip()
    json_prompt = ""
    if key == "timeline_data":
        try:
            data = json.loads(val)
            json_prompt = str(data.get("global_prompt") or "")
        except Exception:
            json_prompt = ""
    low = " ".join([ct, key, val[:500].lower(), json_prompt[:500].lower(), downstream_text(wf, consumers, nid)])
    if any(w in low for w in META_WORDS):
        return "meta", -80

    pos = 0
    neg = 0
    if key in ("positive", "positive_prompt"):
        pos += 80
    if key in ("negative", "negative_prompt"):
        neg += 90
    if "positive" in low:
        pos += 30
    if "negative" in low:
        neg += 45
    if "cliptextencode" in ct or "textencode" in ct or "prompt" in ct:
        pos += 18
        neg += 12
    if "ksampler" in low and "positive" in low:
        pos += 45
    if "ksampler" in low and "negative" in low:
        neg += 45
    if any(w in val.lower() for w in NEGATIVE_WORDS):
        neg += 70
    if key in ("prompt", "text", "value", "string", "caption", "global_prompt", "timeline_data"):
        pos += 25
    if json_prompt:
        pos += 60
    if len(val) > 20:
        pos += 8
        neg += 4
    if len(val) > 1200:
        pos -= 10
        neg -= 10
    if neg > pos + 10:
        return "negative", neg
    return "positive", pos


def find_prompt_candidates(wf):
    consumers = build_consumers(wf)
    positive = []
    negative = []
    all_rows = []
    for nid, node in wf.items():
        for field, value in node_inputs(node).items():
            if not isinstance(value, str):
                continue
            if field.lower() not in PROMPT_FIELDS:
                continue
            kind, score = prompt_kind_score(wf, consumers, str(nid), field, value)
            if score <= 0 or kind == "meta":
                continue
            row = {
                "score": score,
                "node": str(nid),
                "field": field,
                "class": class_type(wf, nid),
                "value": value,
                "kind": kind,
            }
            all_rows.append(row)
            (negative if kind == "negative" else positive).append(row)
    positive.sort(key=lambda r: r["score"], reverse=True)
    negative.sort(key=lambda r: r["score"], reverse=True)
    all_rows.sort(key=lambda r: r["score"], reverse=True)
    return positive, negative, all_rows


def display_candidates(title, rows, limit=10):
    print("\n" + title)
    if not rows:
        print("  - none")
        return
    for i, row in enumerate(rows[:limit], 1):
        preview = re.sub(r"\s+", " ", str(row["value"]))[:100]
        print(
            f"  {i}. node {row['node']}.{row['field']}  "
            f"{row['class']}  score={row['score']}  :: {preview}"
        )


def choose_nodes(title, rows, default="1", limit=10):
    display_candidates(title, rows, limit)
    if not rows:
        return []
    raw = ask("Use candidate numbers, comma separated. Empty keeps default", default)
    chosen = []
    for part in re.split(r"\s*,\s*", raw):
        if not part:
            continue
        try:
            idx = int(part) - 1
        except ValueError:
            continue
        if 0 <= idx < min(limit, len(rows)):
            row = rows[idx]
            chosen.append([row["node"], row["field"]])
    return chosen


def find_seed_nodes(wf):
    out = []
    for nid, node in wf.items():
        for field, value in node_inputs(node).items():
            fl = field.lower()
            if "seed" in fl and isinstance(value, (int, float)):
                out.append([str(nid), field])
    return out


def find_image_nodes(wf):
    out = []
    for nid, node in wf.items():
        ct = class_type(wf, nid).lower()
        for field, value in node_inputs(node).items():
            fl = field.lower()
            if fl == "image" and ("loadimage" in ct or isinstance(value, str)):
                out.append([str(nid), field])
    return out


def find_prefix_node(wf):
    best = []
    for nid, node in wf.items():
        inputs = node_inputs(node)
        if "filename_prefix" not in inputs:
            continue
        ct = class_type(wf, nid).lower()
        score = 30
        if "save" in ct:
            score += 40
        best.append((score, [str(nid), "filename_prefix"]))
    best.sort(reverse=True)
    return best[0][1] if best else None


def find_output_candidates(wf):
    best = []
    for nid, node in wf.items():
        ct = class_type(wf, nid).lower()
        inputs = node_inputs(node)
        score = 0
        if "saveimage" in ct:
            score = 80
        elif "savevideo" in ct or "videocombine" in ct:
            score = 75
        elif "saveaudio" in ct or ("audio" in ct and "save" in ct):
            score = 70
        elif "previewimage" in ct:
            score = 35
        elif "filename_prefix" in inputs:
            score = 30
        if score:
            best.append((score, str(nid), ct))
    best.sort(reverse=True)
    return best


def find_output_node(wf):
    best = find_output_candidates(wf)
    return best[0][1] if best else None


def infer_type(wf):
    best = find_output_candidates(wf)
    ct = best[0][2] if best else ""
    if "saveaudio" in ct or ("audio" in ct and "save" in ct):
        return "audio"
    if "savevideo" in ct or "videocombine" in ct or "animated" in ct:
        return "video"
    text = " ".join(class_type(wf, nid).lower() for nid in wf)
    if "savevideo" in text or "videocombine" in text or "animated" in text:
        return "video"
    if "saveaudio" in text:
        return "audio"
    return "image"


def find_ratio_node(wf):
    for nid, node in wf.items():
        inputs = node_inputs(node)
        if "ratio_preset" in inputs:
            return [str(nid), "ratio_preset"], None
        if "aspect_ratio" in inputs:
            return [str(nid), "aspect_ratio"], RATIO_MAP
    return None, None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else ask("Workflow API JSON path")
    src_path = Path(src.strip('"')).expanduser().resolve()
    if not src_path.exists():
        raise SystemExit(f"File not found: {src_path}")

    wf = json.loads(src_path.read_text(encoding="utf-8"))
    if not isinstance(wf, dict):
        raise SystemExit("This does not look like a ComfyUI API-format JSON object.")

    WORKFLOWS.mkdir(exist_ok=True)
    dest = WORKFLOWS / src_path.name
    if src_path.resolve() != dest.resolve():
        if dest.exists() and not yes_no(f"{dest.name} already exists. Overwrite?", False):
            raise SystemExit("Cancelled.")
        shutil.copy2(src_path, dest)

    default_name = safe_key(src_path.stem.replace("_API", "").replace("image_", ""))
    name = ask("Dashboard/custom workflow name", default_name)
    trigger = ask("Telegram command trigger", "/" + name)
    wtype = ask("Output type (image/video/audio)", infer_type(wf)).lower()
    if wtype not in ("image", "video", "audio"):
        wtype = "image"
    llm = ask("Prompt generation (image/video/none)", "video" if wtype == "video" else "image").lower()
    if llm not in ("image", "video", "none"):
        llm = "image"

    positives, negatives, all_rows = find_prompt_candidates(wf)
    if not positives and all_rows:
        positives = all_rows
    selected_prompt_nodes = choose_nodes("Positive prompt candidates", positives, "1")
    prompt_nodes = [x for x in selected_prompt_nodes if x[1] != "timeline_data"]
    timeline_prompt_nodes = [x for x in selected_prompt_nodes if x[1] == "timeline_data"]
    if not prompt_nodes and not timeline_prompt_nodes:
        raise SystemExit("No positive prompt node selected; registration cancelled.")
    negative_nodes = choose_nodes("Negative prompt candidates (optional)", negatives, "") if negatives else []

    seed_nodes = find_seed_nodes(wf)
    image_nodes = find_image_nodes(wf)
    prefix_node = find_prefix_node(wf)
    output_node = find_output_node(wf)
    ratio_node, ratio_map = find_ratio_node(wf)

    print("\nAuto-detected:")
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
    if timeline_prompt_nodes:
        spec["timeline_prompt_nodes"] = timeline_prompt_nodes
    if negative_nodes and yes_no("Register negative prompt nodes too?", True):
        spec["negative_nodes"] = negative_nodes
        spec["negative_prompt"] = ask("Negative prompt text", "low quality, blurry, distorted, watermark")
    if image_nodes and yes_no("Register image input nodes?", False):
        spec["image_nodes"] = image_nodes
    if ratio_node and yes_no("Expose ratio option on dashboard?", True):
        spec["ratio_node"] = ratio_node
        if ratio_map:
            spec["ratio_map"] = ratio_map

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("custom_workflows", {})[name] = spec
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\nRegistered.")
    print(f"  name: {name}")
    print(f"  file: workflows/{dest.name}")
    print(f"  trigger: {trigger}")
    print("Restart or refresh the dashboard to use it.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
