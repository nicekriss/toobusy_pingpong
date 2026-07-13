import json
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")
SOURCE_SUBDIR = "krea2_control_canny_showcase"
BUNDLE = "krea2_canny_nk2e_hangul_readability_retry_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

STAGES = [
    ("S01_OFF", 0.0),
    ("S02_100", 1.0),
    ("S03_160", 1.6),
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


def font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("NotoSansKR-VF.ttf", "malgunbd.ttf", "gulim.ttc"):
        path = Path(r"C:\Windows\Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_center(draw: ImageDraw.ImageDraw, text: str, size: int, y: int) -> None:
    fnt = font(size)
    bbox = draw.textbbox((0, 0), text, font=fnt, stroke_width=10)
    x = (2048 - (bbox[2] - bbox[0])) / 2
    draw.text((x, y), text, font=fnt, fill=(255, 255, 255, 255), stroke_fill=(255, 255, 255, 255), stroke_width=10)


def edge_from_text(base: Image.Image) -> Image.Image:
    gray = base.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges = edges.point(lambda p: 255 if p > 16 else 0)
    edges = edges.filter(ImageFilter.MaxFilter(5))
    out = Image.new("RGB", base.size, (0, 0, 0))
    out.paste((255, 255, 255), mask=edges)
    return out


def save_source(slug: str, lines: list[tuple[str, int, int]]) -> tuple[Path, Path, Path]:
    input_dir = INPUT_ROOT / SOURCE_SUBDIR
    hub_dir = HUB_ROOT / "control_sources"
    input_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGB", (2048, 1152), (0, 0, 0))
    d = ImageDraw.Draw(base, "RGBA")
    for text, size, y in lines:
        draw_center(d, text, size, y)
    base = base.filter(ImageFilter.GaussianBlur(0.25))
    edge = edge_from_text(base)
    base_path = input_dir / f"{slug}_base_2048x1152.png"
    edge_2k_path = input_dir / f"{slug}_edges_2k_2048x1152.png"
    edge_1mp_path = input_dir / f"{slug}_edges_1mp_1024x576.png"
    base.save(base_path)
    edge.save(edge_2k_path)
    edge.resize((1024, 576), Image.Resampling.LANCZOS).save(edge_1mp_path)
    for src in (base_path, edge_1mp_path, edge_2k_path):
        (hub_dir / src.name).write_bytes(src.read_bytes())
    return base_path, edge_1mp_path, edge_2k_path


def build_workflow(slug: str, prompt: str, edge_1mp: Path, edge_2k: Path, stage: str, strength: float, idx: int) -> dict:
    seed = 911000 + idx * 31
    prefix = f"{OUT_SUBDIR}/{slug}_{stage}"
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_1mp)}},
        "2": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_2k)}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["3", 0], "lora_name": r"KREA2_ControlNet\NK2E-canny-v0.1.safetensors", "strength_model": strength}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
        "8": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["6", 0]}},
        "9": {"class_type": "NK2EInContextModelNode", "inputs": {"model": ["4", 0]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["5", 0]}},
        "11": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["10", 0]}},
        "12": {"class_type": "NK2ESetReferenceNode", "inputs": {"conditioning": ["10", 0], "reference": ["8", 0]}},
        "13": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 8,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["9", 0],
                "positive": ["12", 0],
                "negative": ["11", 0],
                "latent_image": ["7", 0],
            },
        },
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["6", 0]}},
        "15": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xRealWebPhoto_v4_dat2.pth"}},
        "16": {
            "class_type": "ToobusyHiresUpscale",
            "inputs": {
                "image": ["14", 0],
                "vae": ["6", 0],
                "upscale_model_name": "4xRealWebPhoto_v4_dat2.pth",
                "downscale_method": "lanczos",
                "scale_by": 0.5,
                "upscale_model": ["15", 0],
            },
        },
        "17": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["6", 0]}},
        "18": {"class_type": "NK2ESetReferenceNode", "inputs": {"conditioning": ["10", 0], "reference": ["17", 0]}},
        "19": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed + 100,
                "steps": 4,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.12,
                "model": ["9", 0],
                "positive": ["18", 0],
                "negative": ["11", 0],
                "latent_image": ["16", 1],
            },
        },
        "20": {"class_type": "VAEDecode", "inputs": {"samples": ["19", 0], "vae": ["6", 0]}},
        "21": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["20", 0]}},
    }
    if strength <= 0:
        workflow["13"]["inputs"]["model"] = ["3", 0]
        workflow["13"]["inputs"]["positive"] = ["10", 0]
        workflow["19"]["inputs"]["model"] = ["3", 0]
        workflow["19"]["inputs"]["positive"] = ["10", 0]
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
            paths.append(OUTPUT_ROOT / (image.get("subfolder") or "") / image["filename"])
    return paths


def sidecar(path: Path, slug: str, edge_1mp: Path, edge_2k: Path, stage_idx: int, strength: float, elapsed: float) -> None:
    with Image.open(path) as im:
        width, height = im.size
    meta = {
        "request": "KREA2 Canny Hangul readability retry",
        "generated": f"{slug}: simplified Hangul Canny source, strength {strength:.2f}",
        "mode": "image",
        "note": "Simplified Canny source: black background and thick Hangul contours only. Left side is the exact 2K reference.",
        "orig": rel_input(edge_2k),
        "orig_1mp": rel_input(edge_1mp),
        "bundle": f"{BUNDLE}_{slug}",
        "stage": "ControlNet OFF" if strength == 0 else f"Canny strength {strength:.2f}",
        "stage_idx": stage_idx,
        "gen_secs": round(elapsed, 1),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    request_json("/system_stats", timeout=5)
    scenarios = [
        (
            "pure_hangul_subscribe",
            [("구독", 360, 150), ("좋아요", 300, 610)],
            "A minimal premium neon sign made only from huge readable Hangul tube letters, exact Korean words '구독 좋아요', black background, cyan-white neon glow, crisp edges, no person, no objects, no random letters.",
        ),
        (
            "pure_hangul_codex",
            [("코덱스", 360, 230), ("쩐다", 300, 660)],
            "A bold Korean neon title card made only from huge readable Hangul letters, exact Korean words '코덱스 쩐다', black background, hot-pink and cyan glass neon tubes, crisp edge-following typography, no person, no objects, no random letters.",
        ),
    ]
    results = []
    for sidx, (slug, lines, prompt) in enumerate(scenarios, 1):
        _base, edge_1mp, edge_2k = save_source(slug, lines)
        for stage_idx, (stage, strength) in enumerate(STAGES, 1):
            workflow = build_workflow(slug, prompt, edge_1mp, edge_2k, stage, strength, sidx * 100 + stage_idx)
            t0 = time.time()
            queued = request_json("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=30)
            prompt_id = queued["prompt_id"]
            print(f"[{slug} {stage_idx}/{len(STAGES)}] queued {stage} strength={strength} prompt_id={prompt_id}", flush=True)
            history = wait_for_prompt(prompt_id)
            elapsed = time.time() - t0
            paths = output_paths(history)
            if not paths:
                raise RuntimeError(f"No image output for {slug} {stage}")
            final = max(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0)
            sidecar(final, slug, edge_1mp, edge_2k, stage_idx, strength, elapsed)
            print(f"[{slug} {stage_idx}/{len(STAGES)}] done {final} ({elapsed:.1f}s)", flush=True)
            results.append(str(final))
    manifest = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"bundle": BUNDLE, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
