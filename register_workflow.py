# -*- coding: utf-8 -*-
r"""Register a ComfyUI API-format workflow into config.json.

Usage:
  python register_workflow.py C:\path\workflow_API.json
"""
import json
import re
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:  # 콘솔에서 한글이 깨지지 않도록
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


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
        print("  (후보가 없어요)")
        return
    for i, row in enumerate(rows[:limit], 1):
        preview = re.sub(r"\s+", " ", str(row["value"]))[:90]
        print(f"  [{i}] {preview}")


def choose_nodes(title, rows, default="1", limit=10):
    display_candidates(title, rows, limit)
    if not rows:
        return []
    raw = ask("👉 몇 번을 쓸까요? (그냥 엔터=1번 추천, 여러 개면 쉼표로 예: 1,2)", default)
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


def _comfy_api():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("comfy_api", "http://127.0.0.1:8188").rstrip("/")
    except Exception:
        return "http://127.0.0.1:8188"


def comfy_combo_options(class_type, field):
    """컴피 object_info에서 해당 노드 필드의 실제 콤보 옵션 목록을 가져옴(없으면 None)."""
    try:
        url = _comfy_api() + "/object_info/" + urllib.parse.quote(str(class_type), safe="")
        data = json.loads(urllib.request.urlopen(url, timeout=5).read().decode("utf-8"))
        info = data.get(class_type, {})
        for sect in ("required", "optional"):
            f = info.get("input", {}).get(sect, {}).get(field)
            if not isinstance(f, list) or not f:
                continue
            if isinstance(f[0], list):
                return f[0]
            if len(f) > 1 and isinstance(f[1], dict) and isinstance(f[1].get("options"), list):
                return f[1]["options"]
    except Exception:
        return None
    return None


def build_ratio_map(options):
    """노드의 실제 옵션 문자열에서 표준 비율('3:4') → 옵션('3:4 (Portrait Standard)') 매핑 생성."""
    if not options:
        return None
    out = {}
    for key in ("1:1", "3:4", "4:3", "2:3", "3:2", "9:16", "16:9", "21:9"):
        for opt in options:
            s = str(opt).strip()
            if s == key or s.startswith(key + " ") or s.startswith(key + "("):
                out[key] = s
                break
    return out or None


def find_ratio_node(wf):
    for nid, node in wf.items():
        inputs = node_inputs(node)
        if "ratio_preset" in inputs:
            return [str(nid), "ratio_preset"], None
        if "aspect_ratio" in inputs:
            # 컴피에서 실제 옵션을 읽어 매핑 생성(라벨 변경에도 안전), 실패 시 기본표로 폴백
            live = build_ratio_map(comfy_combo_options(class_type(wf, nid), "aspect_ratio"))
            return [str(nid), "aspect_ratio"], (live or RATIO_MAP)
    return None, None


def find_megapixels_nodes(wf):
    out = []
    for nid, node in wf.items():
        for field, value in node_inputs(node).items():
            fl = field.lower()
            if (fl == "megapixels" or fl.endswith(".megapixels")) and isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append([str(nid), field])
    return out


def find_dimension_nodes(wf):
    """width/height 정수 입력을 동시에 가진 노드(EmptyLatentImage 등)를 점수순으로 반환."""
    rows = []
    for nid, node in wf.items():
        inputs = node_inputs(node)
        w, h = inputs.get("width"), inputs.get("height")
        if isinstance(w, bool) or isinstance(h, bool):
            continue
        if not (isinstance(w, (int, float)) and isinstance(h, (int, float))):
            continue
        ct = class_type(wf, nid).lower()
        score = 20
        if "emptylatent" in ct or "emptysd3" in ct or "latentimage" in ct:
            score += 60
        elif "resolution" in ct or "imagesize" in ct or "emptyimage" in ct:
            score += 40
        elif "latent" in ct:
            score += 30
        if "upscale" in ct or "scale" in ct:
            score -= 25
        rows.append({"score": score, "node": str(nid), "class": class_type(wf, nid),
                     "width": int(w), "height": int(h)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def choose_dimension_nodes(rows, default="1"):
    print("\n크기(가로·세로)를 정하는 칸 후보:")
    if not rows:
        print("  (후보가 없어요)")
        return [], []
    for i, r in enumerate(rows[:10], 1):
        print(f"  [{i}] {r['class']}  ({r['width']}x{r['height']})")
    raw = ask("👉 어떤 걸 쓸까요? (그냥 엔터=1번 추천, 안 쓰려면 - 입력)", default)
    if raw.strip() == "-":
        return [], []
    if not raw.strip():
        raw = default
    wn, hn = [], []
    for part in re.split(r"\s*,\s*", raw):
        try:
            idx = int(part) - 1
        except ValueError:
            continue
        if 0 <= idx < min(10, len(rows)):
            r = rows[idx]
            wn.append([r["node"], "width"])
            hn.append([r["node"], "height"])
    return wn, hn


ENHANCER_WORDS = (
    "textgenerate", "llm", "ollama", "groq", "openai", "gpt", "deepseek", "qwen", "gemini",
    "florence", "joycaption", "vlm", "enhanc", "chatglm", "promptgen", "describe", "caption",
)

def _branch_has_enhancer(wf, start_id, depth=0, seen=None):
    """스위치 한쪽 가지가 LLM/증강기 노드를 거쳐가는지 (위로 추적)."""
    if seen is None:
        seen = set()
    sid = str(start_id)
    if depth > 5 or sid in seen:
        return False
    seen.add(sid)
    if any(w in class_type(wf, sid).lower() for w in ENHANCER_WORDS):
        return True
    for _f, v in node_inputs(wf.get(sid, {})).items():
        if is_link(v) and _branch_has_enhancer(wf, source_id(v), depth + 1, seen):
            return True
    return False

def find_prompt_switches(wf, prompt_node_ids):
    """프롬프트 '원본 vs 자동증강' 을 고르는 불리언 스위치를 찾아 바이패스 값 결정.
    반환: [{'bool_node','value','found_enhancer'}]  (value = 증강기 끄는 불리언 값)"""
    pset = {str(x) for x in prompt_node_ids}
    out = []
    for nid, node in wf.items():
        inp = node_inputs(node)
        if "switch" not in class_type(wf, nid).lower():
            continue
        if not all(k in inp for k in ("switch", "on_true", "on_false")):
            continue
        if not is_link(inp["switch"]):
            continue
        bool_id = source_id(inp["switch"])
        if "boolean" not in class_type(wf, bool_id).lower():
            continue
        if "value" not in node_inputs(wf.get(bool_id, {})):
            continue
        t_src = source_id(inp["on_true"]) if is_link(inp["on_true"]) else ""
        f_src = source_id(inp["on_false"]) if is_link(inp["on_false"]) else ""
        t_enh, f_enh = _branch_has_enhancer(wf, t_src), _branch_has_enhancer(wf, f_src)
        bypass = None
        # 원본 프롬프트가 직접 연결된 가지를 고르도록 불리언 값 결정
        if f_src in pset and t_src not in pset:
            bypass = False
        elif t_src in pset and f_src not in pset:
            bypass = True
        elif f_enh and not t_enh:
            bypass = False  # on_true 가 원본
        elif t_enh and not f_enh:
            bypass = True   # on_false 가 원본
        if bypass is not None and (t_enh or f_enh):
            out.append({"bool_node": str(bool_id), "value": bypass})
    return out

def main():
    print("=" * 58)
    print(" 🦗 핑퐁 워크플로 등록기")
    print(" ComfyUI 워크플로를 모델비교/봇에서 쓸 수 있게 등록해요.")
    print(" 잘 모르겠으면 그냥 [엔터]만 치면 추천값으로 진행돼요.")
    print("=" * 58)

    src = sys.argv[1] if len(sys.argv) > 1 else ask("📂 등록할 워크플로 JSON 경로 (ComfyUI에서 'API 형식으로 저장'한 파일)")
    src_path = Path(src.strip().strip('"')).expanduser().resolve()
    if not src_path.exists():
        raise SystemExit(f"❌ 파일을 못 찾았어요: {src_path}")
    try:
        wf = json.loads(src_path.read_text(encoding="utf-8"))
        assert isinstance(wf, dict)
    except Exception:
        raise SystemExit("❌ ComfyUI 'API 형식' JSON이 아닌 것 같아요.\n"
                         "   ComfyUI에서 워크플로를 'API 형식으로 저장(Save API Format)'으로 다시 저장해서 넣어주세요.")

    WORKFLOWS.mkdir(exist_ok=True)
    dest = WORKFLOWS / src_path.name
    if src_path.resolve() != dest.resolve():
        if dest.exists() and not yes_no(f"이미 같은 이름의 파일이 있어요 ({dest.name}). 덮어쓸까요?", False):
            raise SystemExit("취소했어요.")
        shutil.copy2(src_path, dest)

    default_name = safe_key(src_path.stem.replace("_API", "").replace("image_", ""))
    print("\n[1/3] 기본 정보  (모르면 엔터)")
    name = ask("  이 워크플로를 부를 이름 (예: 크레아2)", default_name)
    trigger = ask("  텔레그램에서 부를 명령어", "/" + name)
    wtype = ask("  만드는 종류 — image=이미지 / video=영상 / audio=음악", infer_type(wf)).lower()
    if wtype not in ("image", "video", "audio"):
        wtype = "image"
    llm = ask("  프롬프트를 AI가 자동으로 다듬을까요 — image / video / none(안 다듬음)",
              "video" if wtype == "video" else "image").lower()
    if llm not in ("image", "video", "none"):
        llm = "image"

    print("\n[2/3] '그림 설명(프롬프트)'이 들어갈 칸 고르기")
    print("  아래 후보 중 '실제 그림 묘사'처럼 보이는 번호를 고르세요.")
    print("  예) 영어/한글 장면 설명 = 맞음   /   'You are...'·중국어 지시문 = 보통 아님")
    positives, negatives, all_rows = find_prompt_candidates(wf)
    if not positives and all_rows:
        positives = all_rows
    selected_prompt_nodes = choose_nodes("프롬프트 칸 후보:", positives, "1")
    prompt_nodes = [x for x in selected_prompt_nodes if x[1] != "timeline_data"]
    timeline_prompt_nodes = [x for x in selected_prompt_nodes if x[1] == "timeline_data"]
    if not prompt_nodes and not timeline_prompt_nodes:
        raise SystemExit("❌ 프롬프트 칸을 안 골라서 등록을 취소했어요.")
    negative_nodes = choose_nodes("빼고 싶은 것(네거티브) 칸 후보 — 없으면 그냥 엔터:", negatives, "") if negatives else []

    seed_nodes = find_seed_nodes(wf)
    image_nodes = find_image_nodes(wf)
    prefix_node = find_prefix_node(wf)
    output_node = find_output_node(wf)
    ratio_node, ratio_map = find_ratio_node(wf)
    megapixels_nodes = find_megapixels_nodes(wf)
    dim_rows = find_dimension_nodes(wf)

    def _yn(x):
        return "✓ 찾음" if x else "✗ 없음"
    print("\n🔎 자동으로 찾은 것:")
    print("   시드(랜덤 숫자) 칸 :", _yn(seed_nodes))
    print("   결과 저장 칸       :", _yn(output_node))
    if ratio_node:
        note = "(컴피 실제 옵션 기반)" if ratio_map is not RATIO_MAP else "(컴피가 꺼져있어 라벨이 다를 수 있어요 ⚠)"
        print("   비율 선택 칸       : ✓ 있음", note)
    elif dim_rows:
        print("   해상도 조절        : ✓ 가로·세로 칸 있음")
    else:
        print("   해상도/비율 제어   : ✗ 없음 (크기 고정)")
    if image_nodes:
        print("   사진 입력 칸       : ✓ 있음 (사진 넣는 워크플로 같아요)")

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
    if negative_nodes and yes_no("네거티브(빼고 싶은 것) 칸도 등록할까요?", True):
        spec["negative_nodes"] = negative_nodes
        spec["negative_prompt"] = ask("기본으로 넣을 네거티브 문구", "low quality, blurry, distorted, watermark")
    if image_nodes and yes_no("사진을 넣어서 쓰는 워크플로인가요? (인물합성·편집 등)", False):
        spec["image_nodes"] = image_nodes
    if ratio_node and yes_no("대시보드에 '비율 선택' 칸을 보여줄까요?", True):
        spec["ratio_node"] = ratio_node
        if ratio_map:
            spec["ratio_map"] = ratio_map

    if megapixels_nodes:
        spec["megapixels_nodes"] = megapixels_nodes

    if wtype == "image":
        print("\n[3/3] 해상도 맞추기")
        if not spec.get("ratio_node"):
            if dim_rows:
                print("  '비율 선택' 칸은 없지만 '가로·세로' 칸이 있어요.")
                print("  등록해두면 대시보드에서 고른 비율로 크기를 자동으로 맞춰줘서,")
                print("  모델비교 때 양쪽 그림 크기가 똑같아져요. (강력 추천)")
                if yes_no("가로·세로로 크기를 제어할까요?", True):
                    wn, hn = choose_dimension_nodes(dim_rows, "1")
                    if wn:
                        spec["width_nodes"] = wn
                    if hn:
                        spec["height_nodes"] = hn
            else:
                print("  조절 가능한 크기 칸을 못 찾았어요.")
        else:
            print("  비율 선택 칸으로 크기가 맞춰져요. ✓")
        if not spec.get("ratio_node") and not spec.get("width_nodes"):
            print("\n  ⚠ 주의: 이 워크플로는 크기/비율을 바꿀 수 없어요(고정).")
            print("     모델비교에서 상대 워크플로와 그림 크기가 다를 수 있어요.")

    switches = find_prompt_switches(wf, [n for n, _ in prompt_nodes])
    if switches:
        print("\n💡 이 워크플로에 '프롬프트 자동 증강기'(내장 AI가 프롬프트를 다시 써주는 스위치)가 있어요.")
        print("   핑퐁이 프롬프트를 직접 만들어 주니까, 끄는 걸 추천해요 (공정 비교 + 내 프롬프트 그대로).")
        if yes_no("자동 증강기를 끌까요?", True):
            sets = spec.get("set_nodes", [])
            for s in switches:
                sets.append([s["bool_node"], "value", s["value"]])
            spec["set_nodes"] = sets
            print("   → 껐어요. 내가 넣는 프롬프트가 그대로 들어가요. ✓")

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("custom_workflows", {})[name] = spec
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n" + "=" * 58)
    print("✅ 등록 완료!")
    print(f"   이름   : {name}")
    print(f"   명령어 : {trigger}")
    print("👉 대시보드를 새로고침(Ctrl+Shift+R)하면")
    print("   '⚔️ 모델비교' 목록과 봇 메뉴에 바로 떠요.")
    print("=" * 58)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n취소했어요.")
