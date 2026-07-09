import json
import math
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")

SOURCE_SUBDIR = "krea2_control_top7_landscape"
BASE_NAME = "control_canny_04_neon_portal_base_16x9.png"
EDGE_NAME = "control_canny_04_neon_portal_edges_16x9.png"
EDGE_1MP_NAME = "control_canny_04_neon_portal_edges_1mp_1024x576.png"
EDGE_2K_NAME = "control_canny_04_neon_portal_edges_2k_2048x1152.png"
BUNDLE = "krea2_canny_nk2e_matched_edge_strength_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

STAGES = [
    ("S01_OFF", 0.0),
    ("S02_025", 0.25),
    ("S03_050", 0.50),
    ("S04_075", 0.75),
    ("S05_100", 1.00),
    ("S06_125", 1.25),
]


def request_json(path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(COMFY + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        text = response.read().decode("utf-8")
    return json.loads(text) if text else {}


def rel_input(path: Path) -> str:
    return path.relative_to(INPUT_ROOT).as_posix()


def make_base_and_edge() -> tuple[Path, Path, Path]:
    input_dir = INPUT_ROOT / SOURCE_SUBDIR
    hub_dir = HUB_ROOT / "control_sources"
    input_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)

    base = Image.new("RGB", (2048, 1152), (9, 8, 18))
    d = ImageDraw.Draw(base, "RGBA")

    # Neon portal rings and runway perspective.
    cx, cy = 1180, 520
    for i in range(17):
        r = 130 + i * 43
        color = (50, 220, 255, 45) if i % 2 == 0 else (255, 74, 190, 38)
        d.ellipse((cx - r * 1.55, cy - r, cx + r * 1.55, cy + r), outline=color, width=5 + i % 3)
    for i in range(14):
        x = 250 + i * 130
        d.line((1024, 770, x, 1140), fill=(0, 230, 255, 90), width=3)
    for y in range(780, 1120, 70):
        d.line((340, y, 1760, y - 20), fill=(255, 220, 80, 65), width=3)

    # One dynamic performer silhouette leaning into camera.
    body = [(860, 420), (1010, 425), (1115, 760), (1020, 1005), (770, 1015), (700, 770)]
    d.polygon(body, fill=(25, 35, 46, 255), outline=(0, 250, 190, 180))
    d.ellipse((790, 230, 1040, 485), fill=(23, 21, 30, 255), outline=(255, 88, 160, 220), width=8)
    d.line((800, 520, 520, 350), fill=(0, 245, 255, 220), width=34)
    d.line((1000, 530, 1320, 410), fill=(255, 80, 190, 220), width=30)
    d.line((805, 980, 650, 1130), fill=(0, 245, 255, 220), width=36)
    d.line((980, 985, 1180, 1130), fill=(255, 80, 190, 220), width=36)
    d.polygon([(720, 610), (1160, 640), (1110, 735), (670, 705)], fill=(44, 255, 155, 190))

    # Graphic city panels and speed slashes for canny-rich structure.
    for i in range(24):
        x = 80 + (i * 83) % 1840
        y = 80 + (i * 137) % 580
        w = 45 + (i * 17) % 120
        h = 80 + (i * 31) % 260
        d.rectangle((x, y, x + w, y + h), outline=(255, 228, 80, 65), width=3)
    for i in range(38):
        a = i / 38 * math.tau
        x1 = cx + math.cos(a) * 120
        y1 = cy + math.sin(a) * 80
        x2 = cx + math.cos(a) * 840
        y2 = cy + math.sin(a) * 520
        d.line((x1, y1, x2, y2), fill=(255, 255, 255, 24), width=2)

    base = base.filter(ImageFilter.GaussianBlur(0.35))
    base_path = input_dir / BASE_NAME
    base.save(base_path)

    # Canny-like edge material: black background, bright edges.
    gray = base.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges = edges.point(lambda p: 255 if p > 28 else 0)
    edges = edges.filter(ImageFilter.MaxFilter(3))
    edge_rgb = Image.new("RGB", edges.size, (0, 0, 0))
    edge_rgb.paste((245, 252, 255), mask=edges)
    edge_path = input_dir / EDGE_NAME
    edge_2k_path = input_dir / EDGE_2K_NAME
    edge_1mp_path = input_dir / EDGE_1MP_NAME
    edge_rgb.save(edge_path)
    edge_rgb.save(edge_2k_path)
    edge_rgb.resize((1024, 576), Image.Resampling.LANCZOS).save(edge_1mp_path)

    for src in (base_path, edge_path, edge_1mp_path, edge_2k_path):
        dst = hub_dir / src.name
        dst.write_bytes(src.read_bytes())
    return base_path, edge_1mp_path, edge_2k_path


def build_workflow(edge_1mp_path: Path, edge_2k_path: Path, stage: str, strength: float, idx: int) -> dict:
    prompt = (
        "A single handsome Korean male cyberpunk idol performer lunges toward the camera through a huge neon portal tunnel, "
        "low wide-angle music-video shot, glossy black jacket with electric cyan and hot-pink graphic panels, reflective glass runway, "
        "fresh futuristic Seoul night energy, crisp contour-driven composition, pop comic color impact, dramatic rim lights, clean face, "
        "one person only, no background characters, no insects, no text, no logo. "
        f"ControlNet canny edge strength demonstration, stage {idx}: keep the edge silhouette and portal structure increasingly faithful."
    )
    seed = 701000 + idx
    prefix = f"{OUT_SUBDIR}/{BUNDLE}_{stage}"
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_1mp_path)}},
        "2": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_2k_path)}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "4": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["3", 0], "lora_name": r"KREA2_TOP7\krea2_neondrip.safetensors", "strength_model": 0.32},
        },
        "5": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["4", 0], "lora_name": r"KREA2\KREA2_TOP7\06_illustria_anime01_krea2.safetensors", "strength_model": 0.28},
        },
        "6": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["5", 0], "lora_name": r"KREA2_ControlNet\NK2E-canny-v0.1.safetensors", "strength_model": strength},
        },
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "9": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
        "10": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["1", 0], "vae": ["8", 0]},
        },
        "11": {"class_type": "NK2EInContextModelNode", "inputs": {"model": ["6", 0]}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["7", 0]}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["12", 0]}},
        "23": {"class_type": "NK2ESetReferenceNode", "inputs": {"conditioning": ["12", 0], "reference": ["10", 0]}},
        "14": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 8,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["11", 0],
                "positive": ["23", 0],
                "negative": ["13", 0],
                "latent_image": ["9", 0],
            },
        },
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["8", 0]}},
        "16": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xRealWebPhoto_v4_dat2.pth"}},
        "17": {
            "class_type": "ToobusyHiresUpscale",
            "inputs": {
                "image": ["15", 0],
                "vae": ["8", 0],
                "upscale_model_name": "4xRealWebPhoto_v4_dat2.pth",
                "downscale_method": "lanczos",
                "scale_by": 0.5,
                "upscale_model": ["16", 0],
            },
        },
        "18": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["2", 0], "vae": ["8", 0]},
        },
        "19": {"class_type": "NK2ESetReferenceNode", "inputs": {"conditioning": ["12", 0], "reference": ["18", 0]}},
        "20": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed + 100,
                "steps": 4,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.24,
                "model": ["11", 0],
                "positive": ["19", 0],
                "negative": ["13", 0],
                "latent_image": ["17", 1],
            },
        },
        "21": {"class_type": "VAEDecode", "inputs": {"samples": ["20", 0], "vae": ["8", 0]}},
        "22": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["21", 0]}},
    }
    if strength <= 0:
        # OFF means no NK2E in-context path at all.
        workflow["14"]["inputs"]["model"] = ["5", 0]
        workflow["14"]["inputs"]["positive"] = ["12", 0]
        workflow["20"]["inputs"]["model"] = ["5", 0]
        workflow["20"]["inputs"]["positive"] = ["12", 0]
    return workflow


def wait_for_prompt(prompt_id: str, timeout: int = 900) -> dict:
    started = time.time()
    while time.time() - started < timeout:
        hist = request_json(f"/history/{prompt_id}", timeout=30)
        if prompt_id in hist:
            return hist[prompt_id]
        time.sleep(2)
    raise TimeoutError(prompt_id)


def output_paths(history: dict) -> list[Path]:
    paths = []
    for node in (history.get("outputs") or {}).values():
        for image in node.get("images") or []:
            filename = image["filename"]
            subfolder = image.get("subfolder") or ""
            paths.append(OUTPUT_ROOT / subfolder / filename)
    return paths


def write_sidecar(path: Path, edge_1mp_path: Path, edge_2k_path: Path, stage: str, strength: float, idx: int, elapsed: float) -> None:
    with Image.open(path) as im:
        width, height = im.size
    meta = {
        "request": "KREA2 Canny ControlNet strength demo",
        "generated": f"Canny edge ControlNet strength {strength:.2f}: neon portal cyberpunk idol performer, single subject, pop comic music-video style.",
        "mode": "image",
        "note": f"Canny/Edge control source fixed on left; right image uses strength {strength:.2f}. The displayed 2K edge source is the same file used in the hires reference pass.",
        "orig": rel_input(edge_2k_path),
        "orig_1mp": rel_input(edge_1mp_path),
        "bundle": BUNDLE,
        "stage": "ControlNet OFF" if strength == 0 else f"Canny strength {strength:.2f}",
        "stage_idx": idx,
        "gen_secs": round(elapsed, 1),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    base_path, edge_1mp_path, edge_2k_path = make_base_and_edge()
    print(f"base={base_path}")
    print(f"edge_1mp={edge_1mp_path}")
    print(f"edge_2k={edge_2k_path}")
    request_json("/system_stats", timeout=5)

    results = []
    for idx, (stage, strength) in enumerate(STAGES, 1):
        workflow = build_workflow(edge_1mp_path, edge_2k_path, stage, strength, idx)
        client_id = str(uuid.uuid4())
        t0 = time.time()
        queued = request_json("/prompt", {"prompt": workflow, "client_id": client_id}, timeout=30)
        prompt_id = queued["prompt_id"]
        print(f"[{idx}/{len(STAGES)}] queued {stage} strength={strength} prompt_id={prompt_id}", flush=True)
        history = wait_for_prompt(prompt_id)
        elapsed = time.time() - t0
        paths = output_paths(history)
        if not paths:
            raise RuntimeError(f"No image output for {stage}")
        final = max(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        write_sidecar(final, edge_1mp_path, edge_2k_path, stage, strength, idx, elapsed)
        print(f"[{idx}/{len(STAGES)}] done {final} ({elapsed:.1f}s)", flush=True)
        results.append(str(final))

    manifest = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"bundle": BUNDLE, "base": str(base_path), "edge_1mp": str(edge_1mp_path), "edge_2k": str(edge_2k_path), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
