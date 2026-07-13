import json
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")

SOURCE_SUBDIR = "krea2_control_canny_showcase"
BUNDLE = "krea2_canny_creative_showcase_fixed_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

BASE_IMAGE = (
    OUTPUT_ROOT
    / "pingpong"
    / "krea2_cn_strength_manga_pop_0709"
    / "krea2_cn_strength_manga_pop_0709_S04_075_00001_.png"
)

SAMPLES = [
    {
        "slug": "subscribe_neon_cover",
        "label": "\uad6c\ub3c5\n\uc88b\uc544\uc694",
        "strength": 0.70,
        "seed": 1207111,
        "prompt": "Korean pop manhwa YouTube thumbnail, heroic close-up male performer, huge readable Hangul title in lower-left, glossy neon ink, cyan magenta yellow lighting, premium comic cover, exact Hangul words '구독 좋아요', no extra letters, no watermark",
    },
    {
        "slug": "ai_breaking_news",
        "label": "AI\n\ub300\ud3ed\ubc1c",
        "strength": 0.62,
        "seed": 1207222,
        "prompt": "explosive Korean tech news thumbnail, futuristic AI reactor behind a dramatic hero portrait, sharp white Hangul title block, electric blue and hazard yellow accents, cinematic smoke, exact Hangul words 'AI 대폭발', no extra letters, no watermark",
    },
    {
        "slug": "today_reveal",
        "label": "\uc624\ub298\n\uacf5\uac1c",
        "strength": 0.78,
        "seed": 1207333,
        "prompt": "premium K-pop teaser poster, idol silhouette stepping through a glowing portal, bold readable Korean text, glossy black chrome and pink rim light, suspenseful reveal composition, exact Hangul words '오늘 공개', no extra letters, no watermark",
    },
    {
        "slug": "crazy_quality",
        "label": "\ubbf8\uce5c\n\ud004\ub9ac\ud2f0",
        "strength": 0.86,
        "seed": 1207444,
        "prompt": "high-impact Korean YouTube thumbnail, luxury anime action portrait, intense speed lines, expensive fabric detail, luminous white title strokes, saturated comic lighting, exact Hangul words '미친 퀄리티', no extra letters, no watermark",
    },
    {
        "slug": "one_click",
        "label": "\ud55c\ubc88\uc5d0\n\ub05d",
        "strength": 0.55,
        "seed": 1207555,
        "prompt": "clean tutorial thumbnail, confident creator pointing at a holographic workflow board, readable Korean title block, polished neon studio, green and white UI glow, exact Hangul words '한번에 끝', no extra letters, no watermark",
    },
    {
        "slug": "secret_recipe",
        "label": "\ube44\ubc00\n\ub808\uc2dc\ud53c",
        "strength": 0.72,
        "seed": 1207666,
        "prompt": "mysterious Korean creator thumbnail, hidden AI recipe book glowing on desk, dramatic face lighting, cinematic shadows, bold white Hangul text at lower-left, exact Hangul words '비밀 레시피', no extra letters, no watermark",
    },
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


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("malgunbd.ttf", "malgun.ttf", "gulim.ttc"):
        path = Path(r"C:\Windows\Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_label_mask(size: tuple[int, int], label: str) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    lines = label.splitlines()
    fonts = [get_font(190), get_font(150), get_font(132)]
    x = 140
    y = 700 if len(lines) <= 2 else 650
    for i, text in enumerate(lines):
        font = fonts[min(i, len(fonts) - 1)]
        draw.text((x, y), text, font=font, fill=255, stroke_fill=255, stroke_width=9)
        bbox = draw.textbbox((x, y), text, font=font, stroke_width=9)
        y = bbox[3] + 26
    return mask.filter(ImageFilter.MaxFilter(3))


def make_source(sample: dict) -> tuple[Path, Path, Path]:
    input_dir = INPUT_ROOT / SOURCE_SUBDIR
    hub_dir = HUB_ROOT / "control_sources"
    input_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)

    base = Image.open(BASE_IMAGE).convert("RGB").resize((2048, 1152), Image.Resampling.LANCZOS)
    label_mask = draw_label_mask(base.size, sample["label"])

    gray = ImageOps.autocontrast(base.convert("L"))
    img_edges = gray.filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > 42 else 0)
    img_edges = img_edges.filter(ImageFilter.MaxFilter(3))

    combined = Image.new("L", base.size, 0)
    combined = Image.composite(Image.new("L", base.size, 190), combined, img_edges)
    combined = Image.composite(Image.new("L", base.size, 255), combined, label_mask)
    combined = combined.filter(ImageFilter.MaxFilter(3))

    edge_rgb = Image.new("RGB", base.size, (0, 0, 0))
    edge_rgb.paste((246, 252, 255), mask=combined)

    preview = base.copy()
    preview_overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    preview_overlay.paste((255, 70, 170, 220), mask=label_mask)
    preview = Image.alpha_composite(preview.convert("RGBA"), preview_overlay).convert("RGB")

    stem = f"creative_{sample['slug']}"
    preview_path = input_dir / f"{stem}_preview_2048x1152.png"
    edge_2k_path = input_dir / f"{stem}_edges_2k_2048x1152.png"
    edge_1mp_path = input_dir / f"{stem}_edges_1mp_1024x576.png"

    preview.save(preview_path)
    edge_rgb.save(edge_2k_path)
    edge_rgb.resize((1024, 576), Image.Resampling.LANCZOS).save(edge_1mp_path)

    for src in (preview_path, edge_2k_path, edge_1mp_path):
        (hub_dir / src.name).write_bytes(src.read_bytes())
    return preview_path, edge_1mp_path, edge_2k_path


def build_workflow(sample: dict, edge_1mp: Path) -> dict:
    prefix = f"{OUT_SUBDIR}/{sample['slug']}_S{sample['strength']:.2f}".replace(".", "")
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_1mp)}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["4", 0]}},
        "6": {"class_type": "NK2EInContextEditNode", "inputs": {"model": ["2", 0], "reference": ["5", 0]}},
        "7": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["6", 0], "lora_name": r"KREA2_ControlNet\NK2E-canny-v0.1.safetensors", "strength_model": sample["strength"]}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": sample["prompt"], "clip": ["3", 0]}},
        "9": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["8", 0]}},
        "10": {
            "class_type": "KSampler",
            "inputs": {
                "seed": sample["seed"],
                "steps": 6,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["7", 0],
                "positive": ["8", 0],
                "negative": ["9", 0],
                "latent_image": ["5", 0],
            },
        },
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["11", 0]}},
    }


def wait_for_prompt(prompt_id: str, timeout: int = 900) -> dict:
    start = time.time()
    while time.time() - start < timeout:
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


def write_sidecar(path: Path, sample: dict, preview: Path, edge_1mp: Path, edge_2k: Path, idx: int, elapsed: float) -> None:
    with Image.open(path) as im:
        width, height = im.size
    meta = {
        "request": "Creative KREA2 Canny thumbnail showcase",
        "generated": sample["prompt"],
        "mode": "image",
        "note": "Left comparison image is the exact 2K Canny guide used for this sample. The KSampler starts from the 1MP encoded guide latent.",
        "orig": rel_input(edge_2k),
        "orig_1mp": rel_input(edge_1mp),
        "source_preview": rel_input(preview),
        "bundle": BUNDLE,
        "stage": f"{idx}. {sample['slug']} / NK2E Canny strength {sample['strength']:.2f}",
        "stage_idx": idx,
        "gen_secs": round(elapsed, 1),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    request_json("/system_stats", timeout=5)
    results = []
    for idx, sample in enumerate(SAMPLES, 1):
        preview, edge_1mp, edge_2k = make_source(sample)
        workflow = build_workflow(sample, edge_1mp)
        start = time.time()
        queued = request_json("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=30)
        prompt_id = queued["prompt_id"]
        print(f"[{idx}/{len(SAMPLES)}] queued {sample['slug']} strength={sample['strength']} prompt_id={prompt_id}", flush=True)
        history = wait_for_prompt(prompt_id)
        elapsed = time.time() - start
        if history.get("status", {}).get("status_str") == "error":
            raise RuntimeError(json.dumps(history.get("status"), ensure_ascii=False))
        paths = output_paths(history)
        if not paths:
            raise RuntimeError(f"No output for {sample['slug']}")
        final = max(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        write_sidecar(final, sample, preview, edge_1mp, edge_2k, idx, elapsed)
        print(f"[{idx}/{len(SAMPLES)}] done {final} ({elapsed:.1f}s)", flush=True)
        results.append(str(final))

    manifest = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"bundle": BUNDLE, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
